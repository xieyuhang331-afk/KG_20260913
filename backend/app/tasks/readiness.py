from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from celery.worker.control import inspect_command
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import (
    dispose_slice1_runtime,
    dispose_slice2_runtime,
    dispose_slice4_runtime,
    dispose_slice5_runtime,
    dispose_slice6_runtime,
    dispose_slice7_runtime,
    get_slice1_session_factory,
    get_slice2_session_factory,
    get_slice4_session_factory,
    get_slice5_session_factory,
    get_slice6_session_factory,
    get_slice7_session_factory,
)

_NONCE = re.compile(r"^[0-9a-f]{32}$")
_WORKER_PROBE_TIMEOUT_SECONDS = 0.8


@dataclass(frozen=True, slots=True)
class WorkerSpec:
    queue: str
    required_tasks: frozenset[str]


WORKER_SPECS = {
    "registration": WorkerSpec(
        "registration",
        frozenset(
            {
                "identity.registration.dispatch_outbox",
                "identity.registration.reconcile_outbox",
            }
        ),
    ),
    "private_file": WorkerSpec(
        "private-file",
        frozenset(
            {
                "phase1.private_file.scan",
                "phase1.private_file.recover_pending",
                "phase1.private_file.cleanup_orphans",
            }
        ),
    ),
    "therapist": WorkerSpec(
        "therapist-workflow",
        frozenset(
            {
                "phase1.therapist.dispatch_outbox",
                "phase1.therapist.consume_outbox",
                "phase1.therapist.recover_workflow",
                "phase1.therapist.expire_qualifications",
                "phase1.therapist.expire_invitations",
                "phase1.therapist.recompute_readiness",
                "phase1.therapist.sweep_readiness",
            }
        ),
    ),
    "slice4": WorkerSpec(
        "slice4-health-workflow",
        frozenset(
            {
                "phase1.slice4.dispatch_outbox",
                "phase1.slice4.consume_outbox",
                "phase1.slice4.recover_outbox",
                "phase1.slice4.recompute_readiness",
                "phase1.slice4.sweep_readiness",
                "phase1.slice4.build_projection_v2",
            }
        ),
    ),
    "slice5": WorkerSpec(
        "slice5-assessment-workflow",
        frozenset(
            {
                "phase1.slice5.run_assessment",
                "phase1.slice5.dispatch_outbox",
                "phase1.slice5.consume_outbox",
                "phase1.slice5.recover_outbox",
            }
        ),
    ),
    "slice6": WorkerSpec(
        "slice6-health-plan-workflow",
        frozenset(
            {
                "phase1.slice6.generate_plan",
                "phase1.slice6.dispatch_outbox",
                "phase1.slice6.consume_outbox",
                "phase1.slice6.recover_outbox",
            }
        ),
    ),
    "slice7": WorkerSpec(
        "slice7-service-fulfillment-workflow",
        frozenset(
            {
                "phase1.slice7.mark_overdue",
                "phase1.slice7.generate_export",
                "phase1.slice7.dispatch_outbox",
                "phase1.slice7.consume_outbox",
                "phase1.slice7.recover_outbox",
            }
        ),
    ),
}


def _validate_request(worker_kind: object, nonce: object) -> tuple[str, str]:
    if type(worker_kind) is not str or worker_kind not in WORKER_SPECS:
        raise ValueError("WORKER_READINESS_REQUEST_INVALID")
    if type(nonce) is not str or _NONCE.fullmatch(nonce) is None:
        raise ValueError("WORKER_READINESS_REQUEST_INVALID")
    return worker_kind, nonce


def _queue_names(state) -> set[str]:
    queues = getattr(getattr(state.consumer, "task_consumer", None), "queues", {})
    if isinstance(queues, dict):
        return set(queues)
    return {str(getattr(queue, "name", "")) for queue in queues}


async def _database_ready(factory, expected_role: str | None) -> bool:
    if not expected_role:
        return False
    try:
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        """
                        SELECT current_user AS role,
                               COALESCE((SELECT rolsuper OR rolcreatedb OR rolcreaterole
                                                 OR rolreplication OR rolbypassrls
                                         FROM pg_catalog.pg_roles
                                         WHERE rolname=current_user), TRUE) AS high_privilege
                        """
                    )
                )
            ).mappings().one()
        return row["role"] == expected_role and row["high_privilege"] is False
    except asyncio.CancelledError:
        raise
    except Exception:
        return False


async def _with_dispose(check, dispose) -> bool:
    primary: BaseException | None = None
    try:
        return await check()
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            await dispose()
        except asyncio.CancelledError:
            if not isinstance(primary, asyncio.CancelledError):
                raise
        except Exception:
            if primary is None:
                raise


async def _run_worker_check(worker_kind: str) -> bool:
    settings = get_settings()
    if worker_kind == "registration":
        from app.tasks.registration_outbox_tasks import _environment_bootstrap

        try:
            async with _environment_bootstrap():
                return True
        except asyncio.CancelledError:
            raise
        except Exception:
            return False

    if worker_kind == "private_file":
        from app.tasks.institution_onboarding_tasks import (
            check_private_file_worker_readiness,
        )

        async def check() -> bool:
            factory = get_slice1_session_factory("file_writer")
            database_ok = await _database_ready(factory, settings.private_file_writer_role)
            return database_ok and await check_private_file_worker_readiness()

        return await _with_dispose(
            check, lambda: dispose_slice1_runtime("file_writer")
        )

    mappings = {
        "therapist": (
            lambda: get_slice2_session_factory("readiness_worker"),
            settings.therapist_readiness_worker_role,
            lambda: dispose_slice2_runtime("readiness_worker"),
        ),
        "slice4": (
            lambda: get_slice4_session_factory("workflow_worker"),
            settings.slice4_workflow_worker_role,
            lambda: dispose_slice4_runtime("workflow_worker"),
        ),
        "slice5": (
            lambda: get_slice5_session_factory("workflow_worker"),
            settings.slice5_workflow_worker_role,
            lambda: dispose_slice5_runtime("workflow_worker"),
        ),
        "slice6": (
            lambda: get_slice6_session_factory("workflow_worker"),
            settings.slice6_workflow_worker_role,
            lambda: dispose_slice6_runtime("workflow_worker"),
        ),
        "slice7": (
            lambda: get_slice7_session_factory("export_worker"),
            settings.slice7_export_worker_role,
            lambda: dispose_slice7_runtime("export_worker"),
        ),
    }
    factory_builder, expected_role, dispose = mappings[worker_kind]
    async def check() -> bool:
        factory = await factory_builder() if worker_kind not in {"therapist"} else factory_builder()
        return await _database_ready(factory, expected_role)

    return await _with_dispose(check, dispose)


@inspect_command()
def worker_readiness(state, worker_kind=None, nonce=None):
    try:
        kind, checked_nonce = _validate_request(worker_kind, nonce)
        spec = WORKER_SPECS[kind]
        registered = set(getattr(state.app, "tasks", {}))
        hostname = str(getattr(state.consumer, "hostname", ""))
        boundary_ok = (
            _queue_names(state) == {spec.queue}
            and spec.required_tasks.issubset(registered)
            and bool(hostname)
        )
        ready = boundary_ok and asyncio.run(
            asyncio.wait_for(
                _run_worker_check(kind), timeout=_WORKER_PROBE_TIMEOUT_SECONDS
            )
        )
    except Exception:
        return {"status": "NOT_READY", "code": "WORKER_NOT_READY"}
    return {
        "status": "READY" if ready else "NOT_READY",
        "code": "READY" if ready else "WORKER_NOT_READY",
        "worker_kind": kind,
        "hostname": hostname,
        "nonce": checked_nonce,
    }
