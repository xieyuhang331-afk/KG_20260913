from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, TypeVar
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.uuid_generator import Uuid7Generator
from app.modules.private_file.domain import (
    PrivateFile,
    PrivateFileConflict,
    PrivateFileStatus,
    PRIVATE_FILE_SCAN_LEASE,
    PRIVATE_FILE_SCAN_MAX_ATTEMPTS,
    PrivateFileScanFailure,
    private_file_scan_retry_delay,
    validate_generated_export_archive,
)
from app.modules.private_file.models import PrivateFileModel
from app.modules.private_file.ports import PrivateFileScanner, PrivateFileScannerUnavailable
from app.modules.private_file.repository import PrivateFileRepository
from app.modules.private_file.schemas import UploadCompleteRequest, UploadInitiateRequest


PRIVATE_FILE_PERSISTENCE_UNAVAILABLE = "PRIVATE_FILE_PERSISTENCE_UNAVAILABLE"
PRIVATE_FILE_SCANNER_UNAVAILABLE = "PRIVATE_FILE_SCANNER_UNAVAILABLE"
PRIVATE_FILE_COMMIT_OUTCOME_UNKNOWN = "PRIVATE_FILE_COMMIT_OUTCOME_UNKNOWN"
PRIVATE_FILE_COMMIT_ROLLED_BACK = "PRIVATE_FILE_COMMIT_ROLLED_BACK"

_T = TypeVar("_T")


class PrivateFileScanUnavailable(RuntimeError):
    pass


_SCAN_SNAPSHOT_FIELDS = (
    "file_id",
    "status",
    "actual_size",
    "actual_mime_type",
    "actual_sha256",
    "scanned_at",
    "deleted_at",
    "scan_attempt_count",
    "scan_last_error_code",
    "scan_next_retry_at",
    "scan_lease_token",
    "scan_lease_until",
    "scan_operation_ref_digest",
    "scan_version",
)


async def _safe_rollback(session) -> None:
    cancellation: asyncio.CancelledError | None = None
    rollback_task = asyncio.create_task(session.rollback())
    try:
        await asyncio.shield(rollback_task)
    except asyncio.CancelledError as exc:
        cancellation = exc
        with suppress(BaseException):
            await rollback_task
    except Exception:
        pass
    close_task = asyncio.create_task(session.close())
    try:
        await asyncio.shield(close_task)
    except asyncio.CancelledError as exc:
        cancellation = cancellation or exc
        with suppress(BaseException):
            await close_task
    except Exception:
        pass
    if cancellation is not None:
        raise cancellation


async def _finish_scan_safety_step(
    awaitable: Awaitable[_T],
) -> tuple[_T | None, asyncio.CancelledError | None, bool]:
    """Finish an independent safety step before restoring caller cancellation."""
    task = asyncio.create_task(awaitable)
    cancellation: asyncio.CancelledError | None = None
    while True:
        try:
            return await asyncio.shield(task), cancellation, True
        except asyncio.CancelledError as exc:
            cancellation = cancellation or exc
            if not task.done():
                continue
            if task.cancelled():
                return None, cancellation, False
            return task.result(), cancellation, True


async def _commit_private_file(session, file_id: str, expected: dict) -> None:
    try:
        await session.commit()
        return
    except asyncio.CancelledError:
        await _safe_rollback(session)
        raise
    except Exception:
        await _safe_rollback(session)
    bind = session.bind
    if bind is None:
        raise HTTPException(503, PRIVATE_FILE_COMMIT_OUTCOME_UNKNOWN) from None
    try:
        async with AsyncSession(bind=bind, expire_on_commit=False) as confirmation:
            persisted = await PrivateFileRepository(confirmation).persistence_snapshot(file_id)
            if persisted is None:
                raise HTTPException(503, PRIVATE_FILE_COMMIT_ROLLED_BACK)
            if any(persisted[key] != value for key, value in expected.items()):
                raise HTTPException(503, PRIVATE_FILE_COMMIT_OUTCOME_UNKNOWN)
    except HTTPException:
        raise
    except asyncio.CancelledError:
        raise
    except Exception:
        raise HTTPException(503, PRIVATE_FILE_COMMIT_OUTCOME_UNKNOWN) from None


def _exact_scan_snapshot(value) -> dict[str, object] | None:
    if value is None:
        return None
    return {name: value[name] for name in _SCAN_SNAPSHOT_FIELDS}


async def _confirm_scan_commit_outcome(
    bind,
    file_id: str,
    *,
    preimage: dict[str, object],
    postimage: dict[str, object],
) -> str:
    try:
        async with AsyncSession(bind=bind, expire_on_commit=False) as confirmation:
            persisted = _exact_scan_snapshot(
                await PrivateFileRepository(confirmation).persistence_snapshot(file_id)
            )
    except asyncio.CancelledError:
        raise
    except Exception:
        return "UNKNOWN"
    if persisted == postimage:
        return "COMMITTED"
    if persisted == preimage:
        return "NOT_COMMITTED"
    return "UNKNOWN"


async def _isolate_scan_commit_unknown(bind, file_id: str) -> bool:
    isolation = AsyncSession(bind=bind, expire_on_commit=False)
    postimage: dict[str, object] | None = None
    try:
        row = await PrivateFileRepository(isolation).isolate_scan_commit_unknown(
            file_id, now=datetime.now(UTC)
        )
        if row is None:
            await _safe_rollback(isolation)
            return False
        postimage = _exact_scan_snapshot(
            await PrivateFileRepository(isolation).persistence_snapshot(file_id)
        )
        assert postimage is not None
        try:
            await isolation.commit()
            await isolation.close()
            return True
        except asyncio.CancelledError:
            await _safe_rollback(isolation)
            raise
        except Exception:
            await _safe_rollback(isolation)
    except asyncio.CancelledError:
        raise
    except Exception:
        with suppress(BaseException):
            await _safe_rollback(isolation)
        return False
    try:
        async with AsyncSession(bind=bind, expire_on_commit=False) as confirmation:
            persisted = _exact_scan_snapshot(
                await PrivateFileRepository(confirmation).persistence_snapshot(file_id)
            )
    except asyncio.CancelledError:
        raise
    except Exception:
        return False
    return persisted == postimage


async def _commit_scan_state(
    session,
    file_id: str,
    *,
    preimage: dict[str, object],
    postimage: dict[str, object],
) -> str:
    bind = session.bind
    cancellation: asyncio.CancelledError | None = None
    try:
        await session.commit()
        return "COMMITTED"
    except asyncio.CancelledError as exc:
        cancellation = exc
        _, cleanup_cancellation, _ = await _finish_scan_safety_step(
            _safe_rollback(session)
        )
        cancellation = cancellation or cleanup_cancellation
    except Exception:
        _, cleanup_cancellation, _ = await _finish_scan_safety_step(
            _safe_rollback(session)
        )
        cancellation = cancellation or cleanup_cancellation
    if bind is None:
        outcome = "UNKNOWN"
    else:
        confirmed, confirmation_cancellation, confirmation_completed = (
            await _finish_scan_safety_step(
                _confirm_scan_commit_outcome(
                    bind, file_id, preimage=preimage, postimage=postimage
                )
            )
        )
        cancellation = cancellation or confirmation_cancellation
        outcome = confirmed if confirmation_completed else "UNKNOWN"
        if outcome == "UNKNOWN":
            try:
                _, isolation_cancellation, _ = await _finish_scan_safety_step(
                    _isolate_scan_commit_unknown(bind, file_id)
                )
                cancellation = cancellation or isolation_cancellation
            except Exception:
                pass
    if cancellation is not None:
        raise cancellation
    return outcome


async def _acknowledge_scan_claim(
    session, claim: dict[str, object]
) -> int | None:
    for _ in range(2):
        repo = PrivateFileRepository(session)
        persisted = await repo.persistence_snapshot(str(claim["file_id"]))
        if persisted is None:
            return None
        preimage = _exact_scan_snapshot(persisted)
        assert preimage is not None
        row = await repo.get_claimed_scan(
            str(claim["file_id"]),
            lease_token=str(claim["lease_token"]),
            expected_version=int(claim["scan_version"]),
        )
        if (
            row is None
            or row.scan_last_error_code != "COMMIT_OUTCOME_UNKNOWN"
        ):
            await session.rollback()
            return None
        row.scan_last_error_code = None
        row.scan_version += 1
        await session.flush()
        postimage = _exact_scan_snapshot(
            await repo.persistence_snapshot(str(claim["file_id"]))
        )
        assert postimage is not None
        outcome = await _commit_scan_state(
            session,
            str(claim["file_id"]),
            preimage=preimage,
            postimage=postimage,
        )
        if outcome == "COMMITTED":
            return int(postimage["scan_version"])
        if outcome == "UNKNOWN":
            return None
    return None


async def _guard_scan_finalize(
    session, claim: dict[str, object]
) -> int | None:
    for _ in range(2):
        repo = PrivateFileRepository(session)
        persisted = await repo.persistence_snapshot(str(claim["file_id"]))
        if persisted is None:
            return None
        preimage = _exact_scan_snapshot(persisted)
        assert preimage is not None
        row = await repo.get_claimed_scan(
            str(claim["file_id"]),
            lease_token=str(claim["lease_token"]),
            expected_version=int(claim["scan_version"]),
        )
        if row is None or row.scan_last_error_code is not None:
            await session.rollback()
            return None
        row.scan_last_error_code = "COMMIT_OUTCOME_UNKNOWN"
        row.scan_version += 1
        await session.flush()
        postimage = _exact_scan_snapshot(
            await repo.persistence_snapshot(str(claim["file_id"]))
        )
        assert postimage is not None
        outcome = await _commit_scan_state(
            session,
            str(claim["file_id"]),
            preimage=preimage,
            postimage=postimage,
        )
        if outcome == "COMMITTED":
            return int(postimage["scan_version"])
        if outcome == "UNKNOWN":
            return None
    return None


def _storage_root() -> Path:
    raw = os.getenv("KG_PRIVATE_FILE_STORAGE_ROOT")
    if not raw:
        raise RuntimeError("Private file storage is unavailable")
    root = Path(raw).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _path(object_key: str) -> Path:
    root = _storage_root()
    value = (root / object_key).resolve()
    if root not in value.parents:
        raise RuntimeError("Private file storage is unavailable")
    return value


def _domain(row: PrivateFileModel) -> PrivateFile:
    return PrivateFile(
        file_id=row.file_id, purpose=row.purpose, owner_user_id=row.owner_user_id,
        declared_size=row.declared_size, declared_mime_type=row.declared_mime_type,
        declared_sha256=row.declared_sha256, object_key=row.object_key,
        expires_at=row.expires_at, created_at=row.created_at, status=PrivateFileStatus(row.status),
        bound_application_id=row.bound_application_id, bound_at=row.bound_at,
    )


def _detected_mime(data: bytes) -> str | None:
    if data.startswith(b"%PDF-"): return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"): return "image/png"
    if data.startswith(b"\xff\xd8\xff"): return "image/jpeg"
    return None


async def register_generated_export_archive(
    session,
    *,
    export_id: UUID,
    file_id: UUID,
    worker_id: str,
    data: bytes,
    created_at: datetime,
    expires_at: datetime,
) -> dict[str, object]:
    evidence = validate_generated_export_archive(
        purpose="PERSONAL_DATA_EXPORT",
        data=data,
    )
    if (
        type(file_id) is not UUID
        or file_id.version != 7
        or type(export_id) is not UUID
        or export_id.version != 7
        or not isinstance(worker_id, str)
        or not 1 <= len(worker_id) <= 128
        or created_at.tzinfo is None
        or expires_at.tzinfo is None
        or expires_at <= created_at
    ):
        raise PrivateFileConflict("PRIVATE_FILE_EXPORT_ARCHIVE_INVALID")
    object_key = f"slice1/{created_at:%Y/%m}/{file_id}"
    path = _path(object_key)
    repository = PrivateFileRepository(session)
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.slice7.tmp")
    await asyncio.to_thread(temporary.write_bytes, data)
    await asyncio.to_thread(temporary.replace, path)
    expected = {
        "file_id": str(file_id),
        "size": evidence.size,
        "sha256": evidence.sha256,
        "mime_type": evidence.mime_type,
        "status": "CLEAN",
    }
    try:
        persisted = await repository.register_generated_export_archive(
            {
                **expected,
                "export_id": str(export_id),
                "worker_id": worker_id,
                "created_at": created_at,
                "expires_at": expires_at,
            }
        )
        await session.commit()
    except asyncio.CancelledError:
        await _safe_rollback(session)
        raise
    except Exception:
        await _safe_rollback(session)
        bind = session.bind
        if bind is None:
            raise HTTPException(503, PRIVATE_FILE_COMMIT_OUTCOME_UNKNOWN) from None
        try:
            async with AsyncSession(bind=bind, expire_on_commit=False) as confirmation:
                persisted = await PrivateFileRepository(
                    confirmation
                ).generated_export_archive_snapshot(
                    export_id=str(export_id), file_id=str(file_id)
                )
        except Exception:
            raise HTTPException(503, PRIVATE_FILE_COMMIT_OUTCOME_UNKNOWN) from None
        if persisted is None:
            raise HTTPException(503, PRIVATE_FILE_COMMIT_OUTCOME_UNKNOWN) from None
    if any(persisted.get(key) != value for key, value in expected.items()):
        raise PrivateFileConflict("PRIVATE_FILE_EXPORT_ARCHIVE_CONFLICT")
    return expected


async def read_export_source_content(metadata: dict[str, object]) -> bytes:
    try:
        file_id = UUID(str(metadata["file_id"]))
        created_at = metadata["created_at"]
        size = int(metadata["size"])
        digest = str(metadata["sha256"])
        mime_type = str(metadata["mime_type"])
    except (KeyError, TypeError, ValueError):
        raise PrivateFileConflict("PRIVATE_FILE_EXPORT_SOURCE_INVALID") from None
    if (
        file_id.version != 7
        or not isinstance(created_at, datetime)
        or created_at.tzinfo is None
        or not 1 <= size <= 10 * 1024 * 1024
        or len(digest) != 64
        or mime_type not in {"application/pdf", "image/jpeg", "image/png"}
    ):
        raise PrivateFileConflict("PRIVATE_FILE_EXPORT_SOURCE_INVALID")
    path = _path(f"slice1/{created_at.astimezone(timezone.utc):%Y/%m}/{file_id}")
    try:
        data = await asyncio.to_thread(path.read_bytes)
    except FileNotFoundError:
        raise PrivateFileConflict("PRIVATE_FILE_EXPORT_SOURCE_MISSING") from None
    if len(data) != size or not hmac.compare_digest(
        hashlib.sha256(data).hexdigest(), digest
    ):
        raise PrivateFileConflict("PRIVATE_FILE_EXPORT_SOURCE_MISMATCH")
    return data


async def cleanup_generated_export_archive(
    export_id: UUID, created_at: datetime | None
) -> int:
    if type(export_id) is not UUID or export_id.version != 7:
        raise PrivateFileConflict("PRIVATE_FILE_EXPORT_ARCHIVE_INVALID")
    root = _storage_root()
    candidates: set[Path] = set()
    if isinstance(created_at, datetime) and created_at.tzinfo is not None:
        candidates.add(
            _path(
                f"slice1/{created_at.astimezone(timezone.utc):%Y/%m}/{export_id}"
            )
        )
    candidates.update(root.rglob(str(export_id)))
    removed = 0
    for candidate in candidates:
        resolved = candidate.resolve()
        if (
            resolved.name != str(export_id)
            or root not in resolved.parents
            or not resolved.is_file()
        ):
            continue
        await asyncio.to_thread(resolved.unlink)
        removed += 1
    return removed


async def initiate_upload(session, user_id: int, payload: UploadInitiateRequest) -> dict:
    now = datetime.now(timezone.utc); file_id = Uuid7Generator().generate(); object_key = f"slice1/{now:%Y/%m}/{file_id}"
    value = PrivateFile.initiate(file_id=file_id, purpose=payload.purpose, owner_user_id=user_id, declared_size=payload.size, declared_mime_type=payload.mime_type, declared_sha256=payload.sha256, object_key=object_key, expires_at=now + timedelta(minutes=15), now=now)
    row = PrivateFileModel(file_id=str(file_id), purpose=value.purpose, owner_user_id=user_id, declared_size=value.declared_size, declared_mime_type=value.declared_mime_type, declared_sha256=value.declared_sha256, object_key=object_key, status=value.status.value, created_at=now, expires_at=value.expires_at)
    await PrivateFileRepository(session).add(row)
    await _commit_private_file(session, str(file_id), {"status": row.status})
    return {"file_id": str(file_id), "status": row.status, "upload_path": f"/api/v1/private-files/uploads/{file_id}/content", "expires_at": row.expires_at}


async def upload_content(session, user_id: int, file_id: str, data: bytes) -> None:
    row = await PrivateFileRepository(session).get(file_id)
    if row is None or row.owner_user_id != user_id: raise HTTPException(404, "PRIVATE_FILE_NOT_FOUND")
    if row.status != "UPLOAD_INITIATED" or len(data) > 10 * 1024 * 1024: raise HTTPException(409, "PRIVATE_FILE_STATE_CONFLICT")
    path = _path(row.object_key); await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True); await asyncio.to_thread(path.write_bytes, data)


async def complete_upload(session, user_id: int, file_id: str, payload: UploadCompleteRequest) -> dict:
    repo = PrivateFileRepository(session); row = await repo.get(file_id, for_update=True)
    if row is None or row.owner_user_id != user_id: raise HTTPException(404, "PRIVATE_FILE_NOT_FOUND")
    path = _path(row.object_key)
    try: data = await asyncio.to_thread(path.read_bytes)
    except FileNotFoundError: raise HTTPException(409, "PRIVATE_FILE_UPLOAD_MISSING") from None
    digest = hashlib.sha256(data).hexdigest(); detected_mime = _detected_mime(data)
    if detected_mime != payload.mime_type or detected_mime != row.declared_mime_type:
        raise HTTPException(409, "PRIVATE_FILE_EVIDENCE_MISMATCH")
    if row.status == "PENDING_SCAN" and row.actual_size == len(data) and row.actual_mime_type == payload.mime_type and hmac.compare_digest(row.actual_sha256 or "", digest):
        return {"file_id": row.file_id, "status": row.status}
    value = _domain(row)
    try: value.complete_upload(actual_size=len(data), actual_mime_type=payload.mime_type, actual_sha256=digest, now=datetime.now(timezone.utc))
    except PrivateFileConflict as exc:
        raise HTTPException(409, str(exc)) from None
    if payload.size != len(data) or not hmac.compare_digest(payload.sha256, digest): raise HTTPException(409, "PRIVATE_FILE_EVIDENCE_MISMATCH")
    row.actual_size = len(data); row.actual_mime_type = payload.mime_type; row.actual_sha256 = digest; row.status = "PENDING_SCAN"
    await _commit_private_file(session, row.file_id, {
        "status": row.status, "actual_size": row.actual_size,
        "actual_mime_type": row.actual_mime_type, "actual_sha256": row.actual_sha256,
    })
    return {"file_id": row.file_id, "status": row.status}


async def record_scan(
    session, file_id: str, *, scanner: PrivateFileScanner
) -> dict:
    now = datetime.now(UTC)
    lease_token = str(uuid4())
    operation_ref_digest = hashlib.sha256(
        f"A3_SCAN_CLAIM_V1\x1f{file_id}\x1f{lease_token}".encode()
    ).hexdigest()
    claim: dict[str, object] | None = None
    for _ in range(2):
        repo = PrivateFileRepository(session)
        persisted = await repo.persistence_snapshot(file_id)
        if persisted is None:
            raise HTTPException(404, "PRIVATE_FILE_NOT_FOUND")
        preimage = _exact_scan_snapshot(persisted)
        assert preimage is not None
        row = await repo.claim_scan_attempt(
            file_id,
            lease_token=lease_token,
            operation_ref_digest=operation_ref_digest,
            now=now,
            lease_until=now + PRIVATE_FILE_SCAN_LEASE,
        )
        if row is None:
            await session.rollback()
            return {"file_id": file_id, "status": str(persisted["status"]), "claimed": False}
        frozen_claim = {
            "file_id": file_id,
            "object_key": row.object_key,
            "actual_size": row.actual_size,
            "actual_mime_type": row.actual_mime_type,
            "actual_sha256": row.actual_sha256,
            "attempt_count": row.scan_attempt_count,
            "lease_token": lease_token,
            "scan_version": row.scan_version,
        }
        postimage = _exact_scan_snapshot(await repo.persistence_snapshot(file_id))
        assert postimage is not None
        outcome = await _commit_scan_state(
            session, file_id, preimage=preimage, postimage=postimage
        )
        if outcome == "COMMITTED":
            acknowledged_version = await _acknowledge_scan_claim(
                session, frozen_claim
            )
            if acknowledged_version is None:
                raise HTTPException(503, "PRIVATE_FILE_SCAN_STATE_UNKNOWN")
            frozen_claim["scan_version"] = acknowledged_version
            claim = frozen_claim
            break
        if outcome == "UNKNOWN":
            raise HTTPException(503, "PRIVATE_FILE_SCAN_STATE_UNKNOWN")
    if claim is None:
        raise HTTPException(503, "PRIVATE_FILE_SCAN_STATE_UNKNOWN")

    path = _path(str(claim["object_key"]))
    try:
        data = await asyncio.to_thread(path.read_bytes)
    except FileNotFoundError:
        return await _finalize_scan_attempt(
            session,
            claim,
            status="SCAN_FAILED",
            failure=PrivateFileScanFailure.OBJECT_MISSING,
        )
    if claim["actual_size"] != len(data) or not hmac.compare_digest(
        str(claim["actual_sha256"] or ""), hashlib.sha256(data).hexdigest()
    ):
        return await _finalize_scan_attempt(
            session,
            claim,
            status="SCAN_FAILED",
            failure=PrivateFileScanFailure.EVIDENCE_MISMATCH,
        )
    try:
        result = await scanner.scan(path, mime_type=str(claim["actual_mime_type"]))
    except asyncio.CancelledError:
        raise
    except (PrivateFileScannerUnavailable, TimeoutError):
        return await _finalize_scan_attempt(
            session,
            claim,
            status="PENDING_SCAN",
            failure=PrivateFileScanFailure.SCAN_SERVICE_UNAVAILABLE,
        )
    except Exception:
        return await _finalize_scan_attempt(
            session,
            claim,
            status="PENDING_SCAN",
            failure=PrivateFileScanFailure.SCAN_SERVICE_UNAVAILABLE,
        )
    if result not in {"CLEAN", "REJECTED"}:
        return await _finalize_scan_attempt(
            session,
            claim,
            status="PENDING_SCAN",
            failure=PrivateFileScanFailure.SCAN_SERVICE_UNAVAILABLE,
        )
    return await _finalize_scan_attempt(session, claim, status=result)


async def _finalize_scan_attempt(
    session,
    claim: dict[str, object],
    *,
    status: str,
    failure: PrivateFileScanFailure | None = None,
) -> dict:
    attempt_count = int(claim["attempt_count"])
    retry_after = (
        private_file_scan_retry_delay(attempt_count)
        if status == "PENDING_SCAN"
        else None
    )
    if status == "PENDING_SCAN" and retry_after is None:
        status = "SCAN_FAILED"
    now = datetime.now(UTC)
    persisted = await PrivateFileRepository(session).persistence_snapshot(
        str(claim["file_id"])
    )
    if persisted is None:
        raise HTTPException(404, "PRIVATE_FILE_NOT_FOUND")
    if (
        persisted["status"] != "PENDING_SCAN"
        or persisted["scan_lease_token"] != claim["lease_token"]
        or persisted["scan_version"] != claim["scan_version"]
    ):
        await session.rollback()
        return {
            "file_id": str(claim["file_id"]),
            "status": str(persisted["status"]),
            "claimed": False,
        }
    guarded_version = await _guard_scan_finalize(session, claim)
    if guarded_version is None:
        raise HTTPException(503, "PRIVATE_FILE_SCAN_STATE_UNKNOWN")
    claim["scan_version"] = guarded_version
    for _ in range(2):
        repo = PrivateFileRepository(session)
        persisted = await repo.persistence_snapshot(str(claim["file_id"]))
        if persisted is None:
            raise HTTPException(404, "PRIVATE_FILE_NOT_FOUND")
        preimage = _exact_scan_snapshot(persisted)
        assert preimage is not None
        row = await repo.get_claimed_scan(
            str(claim["file_id"]),
            lease_token=str(claim["lease_token"]),
            expected_version=int(claim["scan_version"]),
        )
        if row is None:
            await session.rollback()
            return {
                "file_id": str(claim["file_id"]),
                "status": str(persisted["status"]),
                "claimed": False,
            }
        row.status = status
        row.scan_attempt_count = attempt_count
        row.scan_last_error_code = failure.value if failure is not None else None
        row.scan_next_retry_at = (
            now + timedelta(seconds=retry_after)
            if status == "PENDING_SCAN" and retry_after is not None
            else None
        )
        row.scan_lease_token = None
        row.scan_lease_until = None
        row.scan_version += 1
        if status in {"CLEAN", "REJECTED", "SCAN_FAILED"}:
            row.scanned_at = now
        await session.flush()
        postimage = _exact_scan_snapshot(await repo.persistence_snapshot(row.file_id))
        assert postimage is not None
        outcome = await _commit_scan_state(
            session, row.file_id, preimage=preimage, postimage=postimage
        )
        if outcome == "COMMITTED":
            result = {"file_id": row.file_id, "status": status, "claimed": True}
            if status == "PENDING_SCAN" and retry_after is not None:
                result["retry_after"] = retry_after
            return result
        if outcome == "UNKNOWN":
            raise HTTPException(503, "PRIVATE_FILE_SCAN_STATE_UNKNOWN")
    raise HTTPException(503, "PRIVATE_FILE_SCAN_STATE_UNKNOWN")


async def mark_scan_failed(session, file_id: str) -> dict:
    row = await PrivateFileRepository(session).get(file_id, for_update=True)
    if row is None:
        raise HTTPException(404, "PRIVATE_FILE_NOT_FOUND")
    if row.status == "SCAN_FAILED":
        return {"file_id": row.file_id, "status": row.status}
    if row.status != "PENDING_SCAN":
        raise HTTPException(409, "PRIVATE_FILE_STATE_CONFLICT")
    row.status = "SCAN_FAILED"
    row.scan_last_error_code = PrivateFileScanFailure.SCAN_SERVICE_UNAVAILABLE.value
    row.scan_next_retry_at = None
    row.scan_lease_token = None
    row.scan_lease_until = None
    row.scan_version += 1
    row.scanned_at = datetime.now(timezone.utc)
    await _commit_private_file(session, row.file_id, {"status": row.status, "scanned_at": row.scanned_at})
    return {"file_id": row.file_id, "status": row.status}


async def metadata(session, user_id: int, file_id: str) -> dict:
    row = await PrivateFileRepository(session).metadata(file_id)
    if row is None or row["owner_user_id"] != user_id: raise HTTPException(404, "PRIVATE_FILE_NOT_FOUND")
    now = datetime.now(UTC)
    next_retry_at = row["scan_next_retry_at"]
    next_poll = None
    if row["status"] == "PENDING_SCAN" and next_retry_at is not None:
        next_poll = max(0, int((next_retry_at - now).total_seconds() + 0.999))
    return {
        "file_id": row["file_id"],
        "purpose": row["purpose"],
        "size": row["declared_size"],
        "mime_type": row["declared_mime_type"],
        "status": row["status"],
        "bound": row["bound_application_id"] is not None,
        "failure_code": row["scan_last_error_code"],
        "retryable": (
            row["status"] == "PENDING_SCAN"
            and row["scan_attempt_count"] < PRIVATE_FILE_SCAN_MAX_ATTEMPTS
            and row["scan_last_error_code"] != "COMMIT_OUTCOME_UNKNOWN"
        ),
        "next_poll_after_seconds": next_poll,
    }


def _access_secret() -> bytes:
    secret = os.getenv("KG_PRIVATE_FILE_ACCESS_SIGNING_KEY")
    if not secret:
        raise RuntimeError("Private file access is unavailable")
    return secret.encode()


def issue_access_token(
    *, file_id: str, user_id: int, reason_code: str, expires_at: int,
    evidence_digest: str, access_scope: str = "OWNER", token_id: str | None = None,
) -> str:
    values = {
        "expires_at": expires_at,
        "access_scope": access_scope,
        "evidence_digest": evidence_digest,
        "file_id": file_id,
        "reason_code": reason_code,
        "user_id": user_id,
        "version": 1,
    }
    if token_id is not None:
        values["token_id"] = token_id
    payload = json.dumps(
        values,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(
        _access_secret(), b"private-file-access-v1\x00" + payload, hashlib.sha256
    ).digest()
    encoded_payload = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    encoded_signature = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{encoded_payload}.{encoded_signature}"


def verify_access_token(
    *, token: str, file_id: str, user_id: int, access_scope: str = "OWNER"
) -> dict:
    try:
        encoded_payload, encoded_signature = token.split(".")
        payload = base64.urlsafe_b64decode(
            encoded_payload + "=" * (-len(encoded_payload) % 4)
        )
        signature = base64.urlsafe_b64decode(
            encoded_signature + "=" * (-len(encoded_signature) % 4)
        )
        expected = hmac.new(
            _access_secret(), b"private-file-access-v1\x00" + payload, hashlib.sha256
        ).digest()
        values = json.loads(payload)
    except Exception:
        raise HTTPException(403, "PRIVATE_FILE_ACCESS_INVALID") from None
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(403, "PRIVATE_FILE_ACCESS_INVALID")
    if (
        values.get("version") != 1
        or values.get("access_scope") != access_scope
        or values.get("file_id") != file_id
        or type(values.get("user_id")) is not int
        or values["user_id"] != user_id
        or type(values.get("expires_at")) is not int
        or values["expires_at"] < int(time.time())
        or not isinstance(values.get("evidence_digest"), str)
        or len(values["evidence_digest"]) != 64
        or (
            access_scope == "EXPORT"
            and (
                not isinstance(values.get("token_id"), str)
                or len(values["token_id"]) != 36
            )
        )
    ):
        raise HTTPException(403, "PRIVATE_FILE_ACCESS_INVALID")
    return values


def _content_evidence(*, file_id: str, user_id: int, data: bytes) -> str:
    payload = f"{file_id}\x1f{user_id}\x1f{len(data)}\x1f{hashlib.sha256(data).hexdigest()}".encode()
    return hmac.new(_access_secret(), b"private-file-content-v1\x00" + payload, hashlib.sha256).hexdigest()


async def authorize_file_access(
    session, user_id: int, file_id: str, reason_code: str, expires_at: int,
    *, reviewer: bool = False, report_authority_session=None,
    report_access_context: str | None = None,
) -> str:
    row = await PrivateFileRepository(session).access_snapshot(file_id)
    report_access = row is not None and row["purpose"] == "DETECTION_REPORT"
    if report_access:
        if report_authority_session is None or report_access_context is None:
            raise HTTPException(404, "PRIVATE_FILE_NOT_FOUND")
        permitted = await PrivateFileRepository(
            report_authority_session
        ).report_file_authority(
            file_id=file_id,
            actor_user_id=user_id,
            context=f"{report_access_context}_AUTHORIZE",
        )
    else:
        permitted = row is not None and (
            row["owner_user_id"] == user_id
            or (reviewer and (row["bound_application_id"] is not None or row["reviewer_access"]))
        )
    if not permitted or row["status"] != "CLEAN":
        raise HTTPException(404, "PRIVATE_FILE_NOT_FOUND")
    object_key = f"slice1/{row['created_at']:%Y/%m}/{file_id}"
    try:
        data = await asyncio.to_thread(_path(object_key).read_bytes)
    except FileNotFoundError:
        raise HTTPException(404, "PRIVATE_FILE_NOT_FOUND") from None
    if row["actual_size"] != len(data) or not hmac.compare_digest(
        row["actual_sha256"] or "", hashlib.sha256(data).hexdigest()
    ):
        raise HTTPException(409, "PRIVATE_FILE_EVIDENCE_MISMATCH")
    return issue_access_token(
        file_id=file_id, user_id=user_id, reason_code=reason_code,
        expires_at=expires_at,
        evidence_digest=_content_evidence(file_id=file_id, user_id=user_id, data=data),
        access_scope="REPORT" if report_access else ("REVIEWER" if reviewer else "OWNER"),
    )


async def authorize_generated_export_access(
    session,
    *,
    user_id: int,
    file_id: str,
    reason_code: str,
    expires_at: int,
    token_id: str,
) -> str:
    row = await PrivateFileRepository(session).access_snapshot(file_id)
    if (
        row is None
        or row["purpose"] != "PERSONAL_DATA_EXPORT"
        or row["owner_user_id"] != user_id
        or row["status"] != "CLEAN"
    ):
        raise HTTPException(404, "PRIVATE_FILE_NOT_FOUND")
    object_key = f"slice1/{row['created_at']:%Y/%m}/{file_id}"
    try:
        data = await asyncio.to_thread(_path(object_key).read_bytes)
    except FileNotFoundError:
        raise HTTPException(404, "PRIVATE_FILE_NOT_FOUND") from None
    if (
        row["actual_size"] != len(data)
        or not hmac.compare_digest(
            row["actual_sha256"] or "", hashlib.sha256(data).hexdigest()
        )
    ):
        raise HTTPException(409, "PRIVATE_FILE_EVIDENCE_MISMATCH")
    return issue_access_token(
        file_id=file_id,
        user_id=user_id,
        reason_code=reason_code,
        expires_at=expires_at,
        evidence_digest=_content_evidence(file_id=file_id, user_id=user_id, data=data),
        access_scope="EXPORT",
        token_id=token_id,
    )


async def read_authorized_content(
    session, user_id: int, file_id: str, token: str, *, reviewer: bool = False,
    report_authority_session=None, report_access_context: str | None = None,
    export_access_consumer: Callable[[dict], Awaitable[bool]] | None = None,
) -> tuple[bytes, str]:
    row = await PrivateFileRepository(session).metadata(file_id)
    report_access = row is not None and row["purpose"] == "DETECTION_REPORT"
    export_access = row is not None and row["purpose"] == "PERSONAL_DATA_EXPORT"
    token_values = verify_access_token(
        token=token, file_id=file_id, user_id=user_id,
        access_scope=(
            "REPORT"
            if report_access
            else "EXPORT"
            if export_access
            else "REVIEWER"
            if reviewer
            else "OWNER"
        ),
    )
    if report_access:
        if report_authority_session is None or report_access_context is None:
            raise HTTPException(404, "PRIVATE_FILE_NOT_FOUND")
        permitted = await PrivateFileRepository(
            report_authority_session
        ).report_file_authority(
            file_id=file_id,
            actor_user_id=user_id,
            context=f"{report_access_context}_CONTENT",
        )
    elif export_access:
        permitted = row["owner_user_id"] == user_id and export_access_consumer is not None
    else:
        permitted = row is not None and (
            row["owner_user_id"] == user_id
            or (reviewer and (row["bound_application_id"] is not None or row["reviewer_access"]))
        )
    if not permitted or row["status"] != "CLEAN":
        raise HTTPException(404, "PRIVATE_FILE_NOT_FOUND")
    object_key = f"slice1/{row['created_at']:%Y/%m}/{file_id}"
    try:
        data = await asyncio.to_thread(_path(object_key).read_bytes)
    except FileNotFoundError:
        raise HTTPException(404, "PRIVATE_FILE_NOT_FOUND") from None
    if row["declared_size"] != len(data) or not hmac.compare_digest(
        token_values["evidence_digest"],
        _content_evidence(file_id=file_id, user_id=user_id, data=data),
    ):
        raise HTTPException(409, "PRIVATE_FILE_EVIDENCE_MISMATCH")
    if export_access and not await export_access_consumer(token_values):
        raise HTTPException(403, "PRIVATE_FILE_ACCESS_INVALID")
    if report_access:
        # REPORT_ORIGINAL_ACCESSED is appended by the bounded database authority.
        await report_authority_session.commit()
    return data, row["declared_mime_type"]


async def delete_temporary(session, user_id: int, file_id: str) -> None:
    row = await PrivateFileRepository(session).get(file_id, for_update=True)
    if row is None or row.owner_user_id != user_id: raise HTTPException(404, "PRIVATE_FILE_NOT_FOUND")
    snapshot = await PrivateFileRepository(session).access_snapshot(file_id)
    if row.bound_application_id is not None or (snapshot and snapshot["qualification_bound"]):
        raise HTTPException(409, "PRIVATE_FILE_BOUND")
    path = _path(row.object_key)
    if row.status == "DELETED":
        if path.exists():
            await asyncio.to_thread(path.unlink)
        return
    row.status = "DELETED"
    row.deleted_at = datetime.now(UTC)
    row.scan_next_retry_at = None
    row.scan_lease_token = None
    row.scan_lease_until = None
    row.scan_operation_ref_digest = None
    row.scan_version += 1
    await _commit_private_file(session, row.file_id, {
        "status": row.status,
        "deleted_at": row.deleted_at,
        "scan_next_retry_at": None,
        "scan_lease_token": None,
        "scan_lease_until": None,
        "scan_operation_ref_digest": None,
        "scan_version": row.scan_version,
    })
    if path.exists():
        await asyncio.to_thread(path.unlink)


async def cleanup_orphan_private_file(session, file_id: str) -> bool:
    row = await PrivateFileRepository(session).get(file_id, for_update=True)
    if row is None:
        return False
    path = _path(row.object_key)
    if row.status == "DELETED":
        if path.exists():
            await asyncio.to_thread(path.unlink)
            return True
        return False
    if row.status == "PENDING_SCAN":
        return False
    if await PrivateFileRepository(session).private_file_referenced(file_id):
        return False
    snapshot = await PrivateFileRepository(session).access_snapshot(file_id)
    if snapshot is not None and snapshot["qualification_bound"]:
        return False
    value = _domain(row)
    if not value.is_orphan_expired(datetime.now(timezone.utc)):
        return False
    row.status = "DELETED"
    row.deleted_at = datetime.now(timezone.utc)
    row.scan_next_retry_at = None
    row.scan_lease_token = None
    row.scan_lease_until = None
    row.scan_operation_ref_digest = None
    row.scan_version += 1
    await _commit_private_file(session, row.file_id, {
        "status": row.status,
        "deleted_at": row.deleted_at,
        "scan_next_retry_at": None,
        "scan_lease_token": None,
        "scan_lease_until": None,
        "scan_operation_ref_digest": None,
        "scan_version": row.scan_version,
    })
    if path.exists():
        await asyncio.to_thread(path.unlink)
    return True
