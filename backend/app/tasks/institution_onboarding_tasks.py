from __future__ import annotations

import asyncio
import hashlib
import importlib
import inspect
import json
import os
from datetime import UTC, datetime, timedelta, timezone

from fastapi import HTTPException

from app.core.database import dispose_slice1_runtime, get_slice1_session_factory
from app.core.readiness import probe_private_object_store
from app.core.sqlalchemy_mapping import build_sqlalchemy_table
from app.modules.auth.models import USER_TABLE
from app.modules.institution_onboarding.ports import InstitutionApprovalDeliveryPort
from app.modules.institution_onboarding.repository import (
    InstitutionOnboardingRepository,
)
from app.modules.private_file.ports import PrivateFileScannerUnavailable
from app.modules.private_file.repository import PrivateFileRepository
from app.modules.private_file.service import cleanup_orphan_private_file, record_scan
from app.modules.private_file.storage import build_private_object_store
from app.modules.system.models import PLATFORM_ORG_TABLE
from app.modules.tenant.models import TENANT_TABLE
from app.tasks.celery_app import (
    PRIVATE_FILE_CLEANUP_TASK_NAME,
    PRIVATE_FILE_QUEUE,
    PRIVATE_FILE_RECOVER_TASK_NAME,
    PRIVATE_FILE_SCAN_TASK_NAME,
    celery_app,
)

for _table_spec in (PLATFORM_ORG_TABLE, TENANT_TABLE, USER_TABLE):
    build_sqlalchemy_table(_table_spec)


class _UnavailableScanner:
    async def scan(self, path, *, mime_type: str) -> str:
        del path, mime_type
        raise PrivateFileScannerUnavailable("PRIVATE_FILE_SCANNER_UNAVAILABLE")


class PrivateFileWorkerRejected(RuntimeError):
    pass


_TEST_SCANNER = None


def _run_worker_uow(kind: str, operation):
    async def execute():
        try:
            return await operation()
        finally:
            cleanup = asyncio.create_task(dispose_slice1_runtime(kind))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise

    return asyncio.run(execute())


def _scanner_for_worker():
    if _TEST_SCANNER is not None:
        return _TEST_SCANNER
    factory_path = os.getenv("KG_PRIVATE_FILE_SCANNER_FACTORY", "").strip()
    if not factory_path or ":" not in factory_path:
        return _UnavailableScanner()
    module_name, factory_name = factory_path.split(":", 1)
    try:
        factory = getattr(importlib.import_module(module_name), factory_name)
        scanner = factory()
    except Exception:
        return _UnavailableScanner()
    return scanner if callable(getattr(scanner, "scan", None)) else _UnavailableScanner()


def _object_store_for_worker():
    from app.core.config import get_settings

    settings = get_settings()
    return build_private_object_store(
        backend=settings.file_storage_backend,
        root=settings.private_file_storage_root,
    )


async def check_private_file_worker_readiness() -> bool:
    scanner = _scanner_for_worker()
    health = getattr(scanner, "health", None)
    if not callable(health):
        return False
    try:
        scanner_result = health()
        if not inspect.isawaitable(scanner_result) or await scanner_result is not True:
            return False
        return await probe_private_object_store(_object_store_for_worker())
    except asyncio.CancelledError:
        raise
    except Exception:
        return False


def configure_private_file_scanner_for_test(scanner) -> None:
    if os.getenv("KG_TEST_ENVIRONMENT") != "ci_ephemeral":
        raise RuntimeError("PRIVATE_FILE_SCANNER_CONFIGURATION_FORBIDDEN")
    global _TEST_SCANNER
    _TEST_SCANNER = scanner


def reset_private_file_scanner_for_test() -> None:
    global _TEST_SCANNER
    _TEST_SCANNER = None


async def scan_private_file(session, file_id: str, *, scanner) -> dict:
    """Queue-safe entrypoint; the Celery adapter supplies a fresh short UoW."""
    return await record_scan(
        session,
        file_id,
        scanner=scanner,
        object_store=_object_store_for_worker(),
    )


@celery_app.task(
    bind=True, name=PRIVATE_FILE_SCAN_TASK_NAME, acks_late=True,
    reject_on_worker_lost=True, queue=PRIVATE_FILE_QUEUE,
)
def scan_private_file_task(self, file_id: str):
    try:
        result = _run_worker_uow("file_writer", lambda: _run_scan(file_id))
    except HTTPException as exc:
        code = exc.detail if exc.detail in {
            "PRIVATE_FILE_NOT_FOUND", "PRIVATE_FILE_STATE_CONFLICT",
            "PRIVATE_FILE_EVIDENCE_MISMATCH",
            "PRIVATE_FILE_COMMIT_OUTCOME_UNKNOWN",
            "PRIVATE_FILE_COMMIT_ROLLED_BACK",
        } else "PRIVATE_FILE_WORKER_REJECTED"
        raise PrivateFileWorkerRejected(code) from None
    except Exception:
        raise PrivateFileWorkerRejected("PRIVATE_FILE_WORKER_UNAVAILABLE") from None
    retry_after = result.get("retry_after")
    if type(retry_after) is int:
        scan_private_file_task.apply_async(
            args=(file_id,), countdown=retry_after, queue=PRIVATE_FILE_QUEUE
        )
    return result


async def _run_scan(file_id: str) -> dict:
    factory = get_slice1_session_factory("file_writer")
    async with factory() as session:
        scanner = _scanner_for_worker()
        return await scan_private_file(session, file_id, scanner=scanner)


@celery_app.task(
    name=PRIVATE_FILE_RECOVER_TASK_NAME,
    acks_late=True,
    queue=PRIVATE_FILE_QUEUE,
)
def recover_pending_scan_tasks(limit: int = 100) -> int:
    try:
        return _run_worker_uow("file_writer", lambda: _recover_pending(limit))
    except Exception:
        raise PrivateFileWorkerRejected("PRIVATE_FILE_WORKER_UNAVAILABLE") from None


async def _recover_pending(limit: int) -> int:
    factory = get_slice1_session_factory("file_writer")
    async with factory() as session:
        file_ids = await PrivateFileRepository(session).recover_pending_scan_ids(
            now=datetime.now(UTC), limit=limit
        )
        await session.commit()
    for file_id in file_ids:
        scan_private_file_task.apply_async(args=(file_id,), queue=PRIVATE_FILE_QUEUE)
    return len(file_ids)


@celery_app.task(
    name=PRIVATE_FILE_CLEANUP_TASK_NAME,
    acks_late=True,
    queue=PRIVATE_FILE_QUEUE,
)
def cleanup_orphan_private_files(limit: int = 100) -> int:
    try:
        return _run_worker_uow("file_writer", lambda: _cleanup_orphans(limit))
    except Exception:
        raise PrivateFileWorkerRejected("PRIVATE_FILE_WORKER_UNAVAILABLE") from None


async def _cleanup_orphans(limit: int) -> int:
    factory = get_slice1_session_factory("file_writer")
    object_store = _object_store_for_worker()
    async with factory() as session:
        active_leases = await PrivateFileRepository(session).active_upload_leases(
            now=datetime.now(UTC)
        )
        await object_store.cleanup_temporary(
            active_leases=active_leases,
            older_than=datetime.now(UTC) - timedelta(minutes=10),
        )
        file_ids = await PrivateFileRepository(session).expired_orphan_ids(
            now=datetime.now(timezone.utc),
            limit=limit,
        )
    cleaned = 0
    for file_id in file_ids:
        async with factory() as session:
            cleaned += int(await cleanup_orphan_private_file(
                session, file_id, object_store=object_store
            ))
    return cleaned


class InstitutionOutboxDispatchUnavailable(RuntimeError):
    pass


async def _dispatch_one_outbox() -> dict:
    factory = get_slice1_session_factory("review_writer")
    async with factory() as session:
        now = datetime.now(timezone.utc)
        row = await InstitutionOnboardingRepository(session).claim_dispatchable_outbox(
            stale_before=now - timedelta(minutes=5)
        )
        if row is None:
            return {"status": "EMPTY"}
        if row.attempts >= 3:
            row.status = "FAILED"
            row.processing_at = None
            await session.commit()
            return {"event_id": row.event_id, "status": row.status}
        row.attempts += 1
        try:
            await asyncio.to_thread(
                celery_app.send_task,
                "phase1.institution.approved",
                kwargs={"event_id": row.event_id, "payload": dict(row.payload)},
                queue="notification",
            )
        except asyncio.CancelledError:
            await session.rollback()
            raise
        except Exception:
            row.status = "FAILED" if row.attempts >= 3 else "PENDING"
            row.processing_at = None
            await session.commit()
            raise InstitutionOutboxDispatchUnavailable(
                "ONBOARDING_OUTBOX_DELIVERY_UNAVAILABLE"
            ) from None
        row.status = "PROCESSING"
        row.processing_at = now
        event_id = row.event_id
        attempts = row.attempts
        try:
            await session.commit()
        except asyncio.CancelledError:
            await session.rollback()
            raise
        except Exception:
            try:
                await session.rollback()
            except Exception:
                await session.close()
            confirmed = await confirm_outbox_dispatch(
                factory,
                event_id=event_id,
                attempts=attempts,
                processing_at=now,
            )
            if confirmed is not None:
                return confirmed
            raise InstitutionOutboxDispatchUnavailable(
                "ONBOARDING_OUTBOX_DISPATCH_ROLLED_BACK"
            ) from None
        return {"event_id": row.event_id, "status": row.status}


async def confirm_outbox_dispatch(
    factory,
    *,
    event_id: str,
    attempts: int,
    processing_at: datetime,
) -> dict | None:
    async with factory() as confirmation:
        row = await InstitutionOnboardingRepository(confirmation).get_outbox(event_id)
        if (
            row is not None
            and row.attempts == attempts
            and row.processing_at == processing_at
            and row.status in {"PROCESSING", "DELIVERED"}
        ):
            return {"event_id": row.event_id, "status": row.status}
        if (
            row is not None
            and row.status == "PENDING"
            and row.attempts == attempts - 1
            and row.processing_at is None
            and row.delivered_at is None
        ):
            return None
    raise InstitutionOutboxDispatchUnavailable(
        "ONBOARDING_OUTBOX_DISPATCH_OUTCOME_UNKNOWN"
    )


def _delivery_digest(event_id: str, recipient_user_id: int, payload: dict) -> str:
    canonical = json.dumps(
        {
            "event_id": event_id,
            "event_type": "INSTITUTION_APPROVED",
            "payload": payload,
            "recipient_scope": "INSTITUTION_ADMIN",
            "recipient_user_id": recipient_user_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def _delivery_matches(delivery, *, recipient_user_id: int, payload: dict, digest: str) -> bool:
    return (
        delivery is not None
        and delivery.event_type == "INSTITUTION_APPROVED"
        and delivery.recipient_user_id == recipient_user_id
        and delivery.recipient_scope == "INSTITUTION_ADMIN"
        and dict(delivery.payload) == payload
        and delivery.payload_digest == digest
    )


async def confirm_outbox_delivery(
    factory, *, event_id: str, recipient_user_id: int, payload: dict, digest: str
) -> dict | None:
    async with factory() as confirmation:
        repo = InstitutionOnboardingRepository(confirmation)
        outbox = await repo.get_outbox(event_id)
        delivery = await repo.get_delivery(event_id)
        if (
            outbox is not None
            and outbox.status == "DELIVERED"
            and outbox.delivered_at is not None
            and dict(outbox.payload) == payload
            and _delivery_matches(
                delivery,
                recipient_user_id=recipient_user_id,
                payload=payload,
                digest=digest,
            )
        ):
            return {"event_id": event_id, "status": "DELIVERED"}
        if outbox is not None and outbox.status == "PROCESSING" and delivery is None:
            return None
    raise InstitutionOutboxDispatchUnavailable(
        "ONBOARDING_OUTBOX_CONSUMER_OUTCOME_UNKNOWN"
    )


@celery_app.task(
    bind=True, name="phase1.institution.dispatch_outbox", acks_late=True,
    reject_on_worker_lost=True, max_retries=3, queue="notification",
)
def dispatch_institution_outbox_task(self):
    try:
        return _run_worker_uow("review_writer", _dispatch_one_outbox)
    except InstitutionOutboxDispatchUnavailable as exc:
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)
    except Exception:
        safe_error = InstitutionOutboxDispatchUnavailable(
            "ONBOARDING_OUTBOX_DISPATCH_UNAVAILABLE"
        )
        raise self.retry(
            exc=safe_error,
            countdown=2 ** self.request.retries,
        ) from None


async def _consume_institution_approved_event(event_id: str, payload: dict) -> dict:
    factory = get_slice1_session_factory("review_writer")
    async with factory() as session:
        repo = InstitutionOnboardingRepository(session)
        row = await repo.get_outbox(
            event_id, for_update=True
        )
        if row is None or row.event_type != "INSTITUTION_APPROVED" or dict(row.payload) != payload:
            raise InstitutionOutboxDispatchUnavailable(
                "ONBOARDING_OUTBOX_CONSUMER_REJECTED"
            )
        application = await repo.get_application_for_review(row.aggregate_id)
        if application is None or application.status != "APPROVED":
            raise InstitutionOutboxDispatchUnavailable(
                "ONBOARDING_OUTBOX_CONSUMER_REJECTED"
            )
        recipient_user_id = application.applicant_user_id
        digest = _delivery_digest(event_id, recipient_user_id, payload)
        if row.status == "DELIVERED":
            delivery = await repo.get_delivery(event_id)
            if not _delivery_matches(
                delivery,
                recipient_user_id=recipient_user_id,
                payload=payload,
                digest=digest,
            ):
                raise InstitutionOutboxDispatchUnavailable(
                    "ONBOARDING_OUTBOX_CONSUMER_OUTCOME_UNKNOWN"
                )
            return {"event_id": row.event_id, "status": row.status}
        if row.status != "PROCESSING":
            raise InstitutionOutboxDispatchUnavailable(
                "ONBOARDING_OUTBOX_CONSUMER_NOT_READY"
            )
        now = datetime.now(timezone.utc)
        delivery_port: InstitutionApprovalDeliveryPort = repo
        delivery = await delivery_port.deliver(
            event_id=event_id,
            recipient_user_id=recipient_user_id,
            payload=payload,
            payload_digest=digest,
            now=now,
        )
        if not _delivery_matches(
            delivery,
            recipient_user_id=recipient_user_id,
            payload=payload,
            digest=digest,
        ):
            raise InstitutionOutboxDispatchUnavailable(
                "ONBOARDING_OUTBOX_CONSUMER_OUTCOME_UNKNOWN"
            )
        row.status = "DELIVERED"
        row.delivered_at = now
        try:
            await session.commit()
        except asyncio.CancelledError:
            await session.rollback()
            raise
        except Exception:
            try:
                await session.rollback()
            except Exception:
                await session.close()
            confirmed = await confirm_outbox_delivery(
                factory,
                event_id=event_id,
                recipient_user_id=recipient_user_id,
                payload=payload,
                digest=digest,
            )
            if confirmed is not None:
                return confirmed
            raise InstitutionOutboxDispatchUnavailable(
                "ONBOARDING_OUTBOX_CONSUMER_ROLLED_BACK"
            ) from None
        return {"event_id": row.event_id, "status": row.status}


async def _mark_outbox_failed(event_id: str) -> dict:
    factory = get_slice1_session_factory("review_writer")
    async with factory() as session:
        row = await InstitutionOnboardingRepository(session).get_outbox(
            event_id, for_update=True
        )
        if row is None:
            return {"status": "ABSENT"}
        if row.status == "PROCESSING" and row.attempts >= 3:
            row.attempts = 3
            row.status = "FAILED"
            row.processing_at = None
            await session.commit()
        return {"event_id": row.event_id, "status": row.status}


@celery_app.task(
    bind=True, name="phase1.institution.approved", acks_late=True,
    reject_on_worker_lost=True, max_retries=3, queue="notification",
)
def consume_institution_approved_event_task(self, event_id: str, payload: dict):
    try:
        return _run_worker_uow(
            "review_writer",
            lambda: _consume_institution_approved_event(event_id, payload),
        )
    except InstitutionOutboxDispatchUnavailable as exc:
        safe_error = exc
    except Exception:
        safe_error = InstitutionOutboxDispatchUnavailable(
            "ONBOARDING_OUTBOX_CONSUMER_UNAVAILABLE"
        )
    if self.request.retries >= self.max_retries:
        try:
            return _run_worker_uow(
                "review_writer", lambda: _mark_outbox_failed(event_id)
            )
        except Exception:
            raise InstitutionOutboxDispatchUnavailable(
                "ONBOARDING_OUTBOX_FAILURE_RECORD_UNAVAILABLE"
            ) from None
    raise self.retry(
        exc=safe_error,
        countdown=2 ** self.request.retries,
    ) from None


@celery_app.on_after_finalize.connect
def _schedule_slice1_recovery(sender, **kwargs):
    del kwargs
    sender.add_periodic_task(
        30.0, dispatch_institution_outbox_task.s(),
        name="phase1-institution-outbox-recovery",
    )
