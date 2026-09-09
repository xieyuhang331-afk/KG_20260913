from __future__ import annotations

import asyncio
import inspect
import re
from dataclasses import dataclass

from celery.worker.control import inspect_command

from app.core.config import get_settings
from app.core.database import (
    dispose_projection_runtime,
    dispose_slice1_runtime,
    dispose_slice2_runtime,
    dispose_slice3_runtime,
    dispose_slice4_runtime,
    dispose_slice5_runtime,
    dispose_slice6_runtime,
    dispose_slice7_runtime,
    get_projection_session_factory,
    get_slice1_session_factory,
    get_slice2_session_factory,
    get_slice3_session_factory,
    get_slice4_session_factory,
    get_slice5_session_factory,
    get_slice6_session_factory,
    get_slice7_session_factory,
)
from app.core.readiness import probe_database_role

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
    "member": WorkerSpec(
        "member-enrollment-workflow",
        frozenset(
            {
                "phase1.member_enrollment.dispatch_outbox",
                "phase1.member_enrollment.consume_outbox",
                "phase1.member_enrollment.recover_outbox",
                "phase1.member_enrollment.expire_invitations",
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
    return await probe_database_role(factory, expected_role)


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


async def _factory(builder):
    value = builder()
    return await value if inspect.isawaitable(value) else value


async def _dispose_all(disposes) -> None:
    primary: BaseException | None = None
    for dispose in disposes:
        try:
            await dispose()
        except asyncio.CancelledError as error:
            if not isinstance(primary, asyncio.CancelledError):
                primary = error
        except Exception as error:
            if primary is None:
                primary = error
    if primary is not None:
        raise primary


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
        "therapist": lambda: (
            (
                (
                    lambda: get_slice2_session_factory("readiness_worker"),
                    settings.therapist_readiness_worker_role,
                ),
                (
                    lambda: get_slice2_session_factory("reader"),
                    settings.therapist_reader_role,
                ),
            ),
            (
                lambda: dispose_slice2_runtime("readiness_worker"),
                lambda: dispose_slice2_runtime("reader"),
            ),
        ),
        "slice5": lambda: (
            (
                (
                    lambda: get_slice5_session_factory("workflow_worker"),
                    settings.slice5_workflow_worker_role,
                ),
                (
                    lambda: get_slice4_session_factory("identity_authority"),
                    settings.slice4_identity_authority_role,
                ),
            ),
            (
                lambda: dispose_slice5_runtime("workflow_worker"),
                lambda: dispose_slice4_runtime("identity_authority"),
            ),
        ),
        "slice4": lambda: (
            (
                (
                    lambda: get_slice4_session_factory("workflow_worker"),
                    settings.slice4_workflow_worker_role,
                ),
                (
                    lambda: get_slice4_session_factory(
                        "assessment_readiness_writer"
                    ),
                    settings.assessment_readiness_writer_role,
                ),
                (
                    lambda: get_projection_session_factory("health"),
                    settings.health_projection_builder_role,
                ),
                (
                    lambda: get_projection_session_factory("confirmation"),
                    settings.projection_confirmation_role,
                ),
                (
                    lambda: get_projection_session_factory("health_shadow"),
                    settings.health_projection_shadow_role,
                ),
                (
                    lambda: get_projection_session_factory("ready_gate"),
                    settings.projection_ready_gate_role,
                ),
                (
                    lambda: get_projection_session_factory(
                        "shadow_confirmation"
                    ),
                    settings.projection_shadow_confirmation_role,
                ),
            ),
            (
                lambda: dispose_slice4_runtime("workflow_worker"),
                lambda: dispose_slice4_runtime("assessment_readiness_writer"),
                lambda: dispose_projection_runtime("health"),
                lambda: dispose_projection_runtime("confirmation"),
                lambda: dispose_projection_runtime("health_shadow"),
                lambda: dispose_projection_runtime("ready_gate"),
                lambda: dispose_projection_runtime("shadow_confirmation"),
            ),
        ),
        "member": lambda: (
            (
                (
                    lambda: get_slice3_session_factory("workflow_worker"),
                    settings.member_workflow_worker_role,
                ),
                (
                    lambda: get_slice3_session_factory("enrollment_writer"),
                    settings.member_enrollment_writer_role,
                ),
            ),
            (
                lambda: dispose_slice3_runtime("workflow_worker"),
                lambda: dispose_slice3_runtime("enrollment_writer"),
            ),
        ),
        "slice6": lambda: (
            (
                (
                    lambda: get_slice6_session_factory("workflow_worker"),
                    settings.slice6_workflow_worker_role,
                ),
            ),
            (lambda: dispose_slice6_runtime("workflow_worker"),),
        ),
        "slice7": lambda: (
            (
                (
                    lambda: get_slice7_session_factory("export_worker"),
                    settings.slice7_export_worker_role,
                ),
            ),
            (lambda: dispose_slice7_runtime("export_worker"),),
        ),
    }
    dependencies, disposes = mappings[worker_kind]()

    async def check() -> bool:
        for factory_builder, expected_role in dependencies:
            factory = await _factory(factory_builder)
            if not await _database_ready(factory, expected_role):
                return False
        return True

    async def dispose_all() -> None:
        await _dispose_all(disposes)

    return await _with_dispose(check, dispose_all)


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
