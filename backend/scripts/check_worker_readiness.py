from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import re
import sys
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout, suppress
from pathlib import Path
from time import monotonic
from uuid import uuid4

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_HOSTNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}$")
_WORKER_KINDS = (
    "registration",
    "private_file",
    "therapist",
    "member",
    "slice4",
    "slice5",
    "slice6",
    "slice7",
)
_CLI_PROBE_TIMEOUT_SECONDS = 1.8
_PROCESS_CLEANUP_GRACE_SECONDS = 0.15
_PROCESS_TERMINATE_GRACE_SECONDS = 0.1


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


def _runtime_dependencies():
    from app.tasks.celery_app import celery_app
    from app.tasks.readiness import WORKER_SPECS

    return celery_app, WORKER_SPECS


def probe_worker(*, worker_kind: str, hostname: str) -> dict[str, str]:
    celery_app, worker_specs = _runtime_dependencies()
    if worker_kind not in worker_specs or _HOSTNAME.fullmatch(hostname) is None:
        raise RuntimeError("WORKER_NOT_READY")
    spec = worker_specs[worker_kind]
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


def _probe_process_entry(
    connection,
    worker_kind: str,
    hostname: str,
    probe: Callable[..., dict[str, str]],
) -> None:
    result: object = None
    try:
        with (
            open(os.devnull, "w", encoding="utf-8") as sink,
            redirect_stdout(sink),
            redirect_stderr(sink),
        ):
            try:
                result = probe(worker_kind=worker_kind, hostname=hostname)
                if result != {"status": "READY", "worker_kind": worker_kind}:
                    raise RuntimeError("WORKER_NOT_READY")
            except BaseException:
                result = None
        with suppress(BaseException):
            connection.send(result)
    finally:
        connection.close()


def _run_bounded_probe(
    *,
    worker_kind: str,
    hostname: str,
    probe: Callable[..., dict[str, str]] = probe_worker,
    timeout_seconds: float = _CLI_PROBE_TIMEOUT_SECONDS,
) -> dict[str, str]:
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_probe_process_entry,
        args=(sender, worker_kind, hostname, probe),
        name="c23-worker-readiness-probe",
        daemon=True,
    )
    deadline = monotonic() + timeout_seconds
    cleanup_deadline = deadline + _PROCESS_CLEANUP_GRACE_SECONDS
    payload: object = None
    started = False
    alive = False
    exitcode: int | None = None
    control_error: BaseException | None = None
    try:
        process.start()
        started = True
        sender.close()
        remaining = max(0.0, deadline - monotonic())
        if receiver.poll(remaining):
            payload = receiver.recv()
        remaining = max(0.0, deadline - monotonic())
        process.join(remaining)
    except BaseException as error:
        payload = None
        if not isinstance(error, Exception):
            control_error = error
    finally:
        with suppress(Exception):
            sender.close()
        with suppress(Exception):
            receiver.close()
        if started and process.is_alive():
            process.terminate()
            process.join(
                min(
                    _PROCESS_TERMINATE_GRACE_SECONDS,
                    max(0.0, cleanup_deadline - monotonic()),
                )
            )
        if started and process.is_alive():
            process.kill()
            while process.is_alive():
                remaining = max(0.0, cleanup_deadline - monotonic())
                if remaining <= 0:
                    break
                process.join(remaining)
        alive = started and process.is_alive()
        if started and not alive:
            exitcode = process.exitcode
            process.close()
    if control_error is not None:
        raise control_error
    if (
        not started
        or alive
        or exitcode != 0
        or payload != {"status": "READY", "worker_kind": worker_kind}
        or monotonic() > cleanup_deadline
    ):
        raise RuntimeError("WORKER_NOT_READY")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = _SafeParser(add_help=True)
    parser.add_argument("--worker-kind", required=True, choices=_WORKER_KINDS)
    parser.add_argument("--hostname", required=True)
    try:
        arguments = parser.parse_args(argv)
    except _ArgumentInvalid:
        result = {"status": "NOT_READY", "code": "WORKER_READINESS_ARGUMENT_INVALID"}
        print(json.dumps(result, separators=(",", ":")))
        return 2
    try:
        result = _run_bounded_probe(
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
