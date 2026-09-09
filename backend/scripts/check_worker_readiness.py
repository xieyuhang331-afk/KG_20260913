from __future__ import annotations

import argparse
import json
import re
import sys
from time import monotonic
from uuid import uuid4

from app.tasks.celery_app import celery_app
from app.tasks.readiness import WORKER_SPECS

_HOSTNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}$")


class _ArgumentInvalid(Exception):
    pass


class _SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise _ArgumentInvalid


def _single_response(value: object, hostname: str) -> object:
    if type(value) is not dict or set(value) != {hostname}:
        raise RuntimeError("WORKER_NOT_READY")
    return value[hostname]


def _registered_names(value: object) -> set[str]:
    if type(value) is not list or any(type(item) is not str for item in value):
        raise RuntimeError("WORKER_NOT_READY")
    return {item.split(" ", 1)[0] for item in value}


def probe_worker(*, worker_kind: str, hostname: str) -> dict[str, str]:
    if worker_kind not in WORKER_SPECS or _HOSTNAME.fullmatch(hostname) is None:
        raise RuntimeError("WORKER_NOT_READY")
    spec = WORKER_SPECS[worker_kind]
    started = monotonic()
    inspector = celery_app.control.inspect(destination=[hostname], timeout=0.35)
    queues = _single_response(inspector.active_queues(), hostname)
    registered = _single_response(inspector.registered(), hostname)
    queue_names = {
        item.get("name") for item in queues if type(item) is dict and type(item.get("name")) is str
    }
    if spec.queue not in queue_names or not spec.required_tasks.issubset(
        _registered_names(registered)
    ):
        raise RuntimeError("WORKER_NOT_READY")
    remaining = 2.0 - (monotonic() - started)
    if remaining <= 0:
        raise RuntimeError("WORKER_NOT_READY")
    nonce = uuid4().hex
    replies = celery_app.control.broadcast(
        "worker_readiness",
        arguments={"worker_kind": worker_kind, "nonce": nonce},
        destination=[hostname],
        reply=True,
        timeout=min(1.0, remaining),
    )
    if type(replies) is not list or len(replies) != 1:
        raise RuntimeError("WORKER_NOT_READY")
    result = _single_response(replies[0], hostname)
    if (
        type(result) is not dict
        or result.get("status") != "READY"
        or result.get("worker_kind") != worker_kind
        or result.get("hostname") != hostname
        or result.get("nonce") != nonce
        or monotonic() - started > 2.0
    ):
        raise RuntimeError("WORKER_NOT_READY")
    return {"status": "READY", "worker_kind": worker_kind}


def main(argv: list[str] | None = None) -> int:
    parser = _SafeParser(add_help=True)
    parser.add_argument("--worker-kind", required=True, choices=tuple(WORKER_SPECS))
    parser.add_argument("--hostname", required=True)
    try:
        arguments = parser.parse_args(argv)
    except _ArgumentInvalid:
        result = {"status": "NOT_READY", "code": "WORKER_READINESS_ARGUMENT_INVALID"}
        print(json.dumps(result, separators=(",", ":")))
        return 2
    try:
        result = probe_worker(
            worker_kind=arguments.worker_kind, hostname=arguments.hostname
        )
    except Exception:
        result = {"status": "NOT_READY", "code": "WORKER_NOT_READY"}
        print(json.dumps(result, separators=(",", ":")))
        return 1
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
