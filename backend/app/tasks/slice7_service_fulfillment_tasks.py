from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import UUID

from app.core.database import dispose_slice7_runtime, get_slice7_session_factory
from app.core.uuid_generator import Uuid7Generator
from app.modules.private_file.domain import PrivateFileConflict
from app.modules.private_file.service import (
    cleanup_generated_export_archive,
    read_export_source_content,
    register_generated_export_archive,
)
from app.modules.service_fulfillment.domain import (
    SystemBusinessClock,
    build_personal_data_export_archive,
)
from app.modules.service_fulfillment.repository import ServiceFulfillmentRepository
from app.modules.service_fulfillment.service import ServiceFulfillmentService
from app.modules.user_health.service import Slice4Secrets
from app.tasks.celery_app import SLICE7_SERVICE_FULFILLMENT_QUEUE, celery_app


MARK_OVERDUE_TASK = "phase1.slice7.mark_overdue"
GENERATE_EXPORT_TASK = "phase1.slice7.generate_export"
DISPATCH_TASK = "phase1.slice7.dispatch_outbox"
CONSUME_TASK = "phase1.slice7.consume_outbox"
RECOVER_TASK = "phase1.slice7.recover_outbox"
TASK_NAMES = {MARK_OVERDUE_TASK, GENERATE_EXPORT_TASK, DISPATCH_TASK, CONSUME_TASK, RECOVER_TASK}


def _delivery_id(event_id: UUID) -> UUID:
    raw = bytearray(sha256(b"slice7-delivery-v1:" + event_id.bytes).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x70
    raw[8] = (raw[8] & 0x3F) | 0x80
    return UUID(bytes=bytes(raw))


def _run(operation):
    async def execute():
        try:
            return await operation()
        finally:
            try:
                await dispose_slice7_runtime("export_worker")
            except Exception:
                pass

    return asyncio.run(execute())


async def _mark_overdue() -> int:
    factory = await get_slice7_session_factory("export_worker")
    async with factory() as session:
        service = ServiceFulfillmentService(
            ServiceFulfillmentRepository(session), SystemBusinessClock(), Uuid7Generator().generate
        )
        result = await service.mark_overdue(str(Uuid7Generator().generate()))
        await session.commit()
        return len(result)


async def _generate_export(export_id: UUID) -> str:
    factory = await get_slice7_session_factory("export_worker")
    async with factory() as session:
        repository = ServiceFulfillmentRepository(session)
        worker_id = str(Uuid7Generator().generate())
        claimed = await repository.claim_export(export_id, worker_id)
        if claimed is None:
            await session.rollback()
            return "NOT_CLAIMED"
        await session.commit()
        try:
            snapshot = await repository.export_snapshot(export_id)
            profile_internal = snapshot.pop("profile_internal", None)
            if "PROFILE" in snapshot["requested_scope"]:
                if not isinstance(profile_internal, dict):
                    raise RuntimeError("EXPORT_PROFILE_UNAVAILABLE")
                snapshot["data"]["PROFILE"] = Slice4Secrets().decrypt_profile(
                    bytes.fromhex(str(profile_internal["snapshot_ciphertext_hex"])),
                    str(profile_internal["snapshot_key_id"]),
                    tenant_public_id=UUID(str(profile_internal["tenant_public_id"])),
                    subject_member_id=UUID(str(snapshot["subject_member_id"])),
                    revision_id=UUID(str(profile_internal["profile_revision_id"])),
                    identity_source_version=int(
                        profile_internal["identity_source_version"]
                    ),
                )
            attachments: dict[str, bytes] = {}
            for report in snapshot["data"].get("REPORT", []):
                for attachment in report.get("attachments", []):
                    attachment_id = UUID(str(attachment["file_id"]))
                    metadata = await repository.export_source_file(
                        export_id, attachment_id
                    )
                    content = await read_export_source_content(metadata)
                    if sum(map(len, attachments.values())) + len(content) > 10 * 1024 * 1024:
                        raise RuntimeError("EXPORT_ARCHIVE_TOO_LARGE")
                    suffix = {
                        "application/pdf": ".pdf",
                        "image/jpeg": ".jpg",
                        "image/png": ".png",
                    }[str(metadata["mime_type"])]
                    attachments[
                        f"attachments/{report['report_id']}/{attachment_id}{suffix}"
                    ] = content
            archive = build_personal_data_export_archive(
                snapshot, attachments=attachments
            )
        except (PrivateFileConflict, RuntimeError, ValueError) as exc:
            failure_code = _export_failure_code(exc)
            if failure_code is None:
                raise
            now = datetime.now(timezone.utc)
            await repository.fail_export(
                {
                    "export_id": export_id,
                    "worker_id": worker_id,
                    "failure_code": failure_code,
                    "evidence_digest": sha256(failure_code.encode("ascii")).hexdigest(),
                    "audit_id": Uuid7Generator().generate(),
                    "event_id": Uuid7Generator().generate(),
                    "occurred_at": now,
                }
            )
            await session.commit()
            return "FAILED"
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=24)
        await register_generated_export_archive(
            session,
            export_id=export_id,
            file_id=export_id,
            worker_id=worker_id,
            data=archive.data,
            created_at=now,
            expires_at=expires_at,
        )
        repository = ServiceFulfillmentRepository(session)
        await repository.bind_export_artifact(
            {
                "artifact_id": Uuid7Generator().generate(),
                "export_id": export_id,
                "private_file_id": export_id,
                "manifest_digest": archive.manifest_digest,
                "artifact_digest": archive.artifact_digest,
                "artifact_size": len(archive.data),
                "worker_id": worker_id,
                "audit_id": Uuid7Generator().generate(),
                "event_id": Uuid7Generator().generate(),
                "created_at": now,
                "expires_at": expires_at,
            }
        )
        await session.commit()
        return "READY"


def _export_failure_code(exc: Exception) -> str | None:
    code = str(exc)
    if code in {"EXPORT_ARCHIVE_TOO_LARGE", "EXPORT_PROFILE_UNAVAILABLE"}:
        return code
    if code in {
        "PRIVATE_FILE_EXPORT_SOURCE_INVALID",
        "PRIVATE_FILE_EXPORT_SOURCE_MISSING",
        "PRIVATE_FILE_EXPORT_SOURCE_MISMATCH",
    }:
        return "EXPORT_SOURCE_INVALID"
    if code in {
        "EXPORT_SNAPSHOT_FORBIDDEN_FIELD",
        "EXPORT_SNAPSHOT_SCOPE_MISMATCH",
        "EXPORT_ATTACHMENT_INVALID",
        "EXPORT_SNAPSHOT_INVALID",
    }:
        return "EXPORT_SNAPSHOT_INVALID"
    return None


async def _claim_one() -> str | None:
    factory = await get_slice7_session_factory("export_worker")
    async with factory() as session:
        row = await ServiceFulfillmentRepository(session).outbox_claim(None, 60)
        if row is None:
            await session.rollback()
            return None
        await session.commit()
        return str(row["event_id"])


async def _consume(event_id: UUID) -> str:
    factory = await get_slice7_session_factory("export_worker")
    async with factory() as session:
        now = datetime.now(timezone.utc)
        row = await ServiceFulfillmentRepository(session).outbox_consume(
            {
                "event_id": event_id,
                "delivery_id": _delivery_id(event_id),
                "target_type": "INTERNAL_EVENT",
                "target_ref": event_id,
                "target_digest": sha256(event_id.bytes).hexdigest(),
                "delivered_at": now,
            }
        )
        if row.get("event_type") in {"CREATE_EXPORT", "EXPORT_RETRY_REQUESTED"}:
            generate_export.apply_async(
                args=(str(row["aggregate_ref"]),),
                queue=SLICE7_SERVICE_FULFILLMENT_QUEUE,
            )
        await session.commit()
        return str(row.get("status", "DELIVERED"))


def _cleanup_created_at(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else None
    return None


async def _recover(cutoff: datetime | None = None) -> int:
    factory = await get_slice7_session_factory("export_worker")
    async with factory() as session:
        repository = ServiceFulfillmentRepository(session)
        now = cutoff or datetime.now(timezone.utc)
        row = await repository.outbox_recover(now)
        recovered_exports = 0
        for _ in range(100):
            evidence_digest = sha256(
                f"EXPORT_RECOVERY_REQUESTED:{now.isoformat()}".encode("ascii")
            ).hexdigest()
            recovered = await repository.recover_export(
                {
                    "cutoff": now,
                    "audit_id": Uuid7Generator().generate(),
                    "event_id": Uuid7Generator().generate(),
                    "evidence_digest": evidence_digest,
                    "occurred_at": now,
                }
            )
            if recovered is None:
                break
            recovered_exports += 1
        candidates = await repository.claim_export_cleanup(now, 100)
        cleaned = 0
        for candidate in candidates:
            export_id = UUID(str(candidate["export_id"]))
            await cleanup_generated_export_archive(
                export_id, _cleanup_created_at(candidate.get("created_at"))
            )
            if await repository.complete_export_cleanup(
                {
                    "export_id": export_id,
                    "deleted_at": now,
                    "evidence_digest": sha256(
                        f"EXPORT_FILE_CLEANED:{export_id}".encode("ascii")
                    ).hexdigest(),
                    "audit_id": Uuid7Generator().generate(),
                    "event_id": Uuid7Generator().generate(),
                }
            ):
                cleaned += 1
        await session.commit()
        return int(row["recovered"]) + recovered_exports + cleaned


@celery_app.task(name=MARK_OVERDUE_TASK, queue=SLICE7_SERVICE_FULFILLMENT_QUEUE)
def mark_overdue():
    return _run(_mark_overdue)


@celery_app.task(name=GENERATE_EXPORT_TASK, queue=SLICE7_SERVICE_FULFILLMENT_QUEUE)
def generate_export(export_id: str):
    return _run(lambda: _generate_export(UUID(export_id)))


@celery_app.task(name=DISPATCH_TASK, queue=SLICE7_SERVICE_FULFILLMENT_QUEUE)
def dispatch_outbox():
    event_id = _run(_claim_one)
    if event_id is not None:
        consume_outbox.apply_async(args=(event_id,), queue=SLICE7_SERVICE_FULFILLMENT_QUEUE)
    return event_id


@celery_app.task(name=CONSUME_TASK, queue=SLICE7_SERVICE_FULFILLMENT_QUEUE)
def consume_outbox(event_id: str):
    return _run(lambda: _consume(UUID(event_id)))


@celery_app.task(name=RECOVER_TASK, queue=SLICE7_SERVICE_FULFILLMENT_QUEUE)
def recover_outbox():
    return _run(_recover)
