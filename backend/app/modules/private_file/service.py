from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.uuid_generator import Uuid7Generator
from app.modules.private_file.domain import (
    PrivateFile,
    PrivateFileConflict,
    PrivateFileStatus,
)
from app.modules.private_file.models import PrivateFileModel
from app.modules.private_file.ports import PrivateFileScanner, PrivateFileScannerUnavailable
from app.modules.private_file.repository import PrivateFileRepository
from app.modules.private_file.schemas import UploadCompleteRequest, UploadInitiateRequest


PRIVATE_FILE_PERSISTENCE_UNAVAILABLE = "PRIVATE_FILE_PERSISTENCE_UNAVAILABLE"
PRIVATE_FILE_SCANNER_UNAVAILABLE = "PRIVATE_FILE_SCANNER_UNAVAILABLE"
PRIVATE_FILE_COMMIT_OUTCOME_UNKNOWN = "PRIVATE_FILE_COMMIT_OUTCOME_UNKNOWN"
PRIVATE_FILE_COMMIT_ROLLED_BACK = "PRIVATE_FILE_COMMIT_ROLLED_BACK"


class PrivateFileScanUnavailable(RuntimeError):
    pass


async def _safe_rollback(session) -> None:
    rollback_task = asyncio.create_task(session.rollback())
    try:
        await asyncio.shield(rollback_task)
    except asyncio.CancelledError:
        try:
            await rollback_task
        except Exception:
            await session.close()
        raise
    except Exception:
        await session.close()


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
    repo = PrivateFileRepository(session); row = await repo.get(file_id, for_update=True)
    if row is None: raise HTTPException(404, "PRIVATE_FILE_NOT_FOUND")
    path = _path(row.object_key); data = await asyncio.to_thread(path.read_bytes)
    if row.status != "PENDING_SCAN":
        raise HTTPException(409, "PRIVATE_FILE_STATE_CONFLICT")
    if row.actual_size != len(data) or not hmac.compare_digest(
        row.actual_sha256 or "", hashlib.sha256(data).hexdigest()
    ):
        raise HTTPException(409, "PRIVATE_FILE_EVIDENCE_MISMATCH")
    try:
        result = await scanner.scan(path, mime_type=row.actual_mime_type)
    except asyncio.CancelledError:
        await _safe_rollback(session)
        raise
    except (PrivateFileScannerUnavailable, TimeoutError) as exc:
        await _safe_rollback(session)
        raise PrivateFileScanUnavailable(PRIVATE_FILE_SCANNER_UNAVAILABLE) from None
    except Exception as exc:
        del exc
        await _safe_rollback(session)
        raise PrivateFileScanUnavailable(PRIVATE_FILE_SCANNER_UNAVAILABLE) from None
    if result not in {"CLEAN", "REJECTED"}:
        await _safe_rollback(session)
        raise PrivateFileScanUnavailable(PRIVATE_FILE_SCANNER_UNAVAILABLE)
    value = _domain(row); now = datetime.now(timezone.utc); value.record_scan(result, now=now); row.status = value.status.value; row.scanned_at = now
    await _commit_private_file(session, row.file_id, {"status": row.status, "scanned_at": row.scanned_at})
    return {"file_id": row.file_id, "status": row.status}


async def mark_scan_failed(session, file_id: str) -> dict:
    row = await PrivateFileRepository(session).get(file_id, for_update=True)
    if row is None:
        raise HTTPException(404, "PRIVATE_FILE_NOT_FOUND")
    if row.status == "SCAN_FAILED":
        return {"file_id": row.file_id, "status": row.status}
    if row.status != "PENDING_SCAN":
        raise HTTPException(409, "PRIVATE_FILE_STATE_CONFLICT")
    row.status = "SCAN_FAILED"
    row.scanned_at = datetime.now(timezone.utc)
    await _commit_private_file(session, row.file_id, {"status": row.status, "scanned_at": row.scanned_at})
    return {"file_id": row.file_id, "status": row.status}


async def metadata(session, user_id: int, file_id: str) -> dict:
    row = await PrivateFileRepository(session).metadata(file_id)
    if row is None or row["owner_user_id"] != user_id: raise HTTPException(404, "PRIVATE_FILE_NOT_FOUND")
    return {"file_id": row["file_id"], "purpose": row["purpose"], "size": row["declared_size"], "mime_type": row["declared_mime_type"], "status": row["status"], "bound": row["bound_application_id"] is not None}


def _access_secret() -> bytes:
    secret = os.getenv("KG_PRIVATE_FILE_ACCESS_SIGNING_KEY")
    if not secret:
        raise RuntimeError("Private file access is unavailable")
    return secret.encode()


def issue_access_token(
    *, file_id: str, user_id: int, reason_code: str, expires_at: int,
    evidence_digest: str, access_scope: str = "OWNER",
) -> str:
    payload = json.dumps(
        {
            "expires_at": expires_at,
            "access_scope": access_scope,
            "evidence_digest": evidence_digest,
            "file_id": file_id,
            "reason_code": reason_code,
            "user_id": user_id,
            "version": 1,
        },
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
    ):
        raise HTTPException(403, "PRIVATE_FILE_ACCESS_INVALID")
    return values


def _content_evidence(*, file_id: str, user_id: int, data: bytes) -> str:
    payload = f"{file_id}\x1f{user_id}\x1f{len(data)}\x1f{hashlib.sha256(data).hexdigest()}".encode()
    return hmac.new(_access_secret(), b"private-file-content-v1\x00" + payload, hashlib.sha256).hexdigest()


async def authorize_file_access(
    session, user_id: int, file_id: str, reason_code: str, expires_at: int,
    *, reviewer: bool = False,
) -> str:
    row = await PrivateFileRepository(session).access_snapshot(file_id)
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
        access_scope="REVIEWER" if reviewer else "OWNER",
    )


async def read_authorized_content(
    session, user_id: int, file_id: str, token: str, *, reviewer: bool = False
) -> tuple[bytes, str]:
    token_values = verify_access_token(
        token=token, file_id=file_id, user_id=user_id,
        access_scope="REVIEWER" if reviewer else "OWNER",
    )
    row = await PrivateFileRepository(session).metadata(file_id)
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
    return data, row["declared_mime_type"]


async def delete_temporary(session, user_id: int, file_id: str) -> None:
    row = await PrivateFileRepository(session).get(file_id, for_update=True)
    if row is None or row.owner_user_id != user_id: raise HTTPException(404, "PRIVATE_FILE_NOT_FOUND")
    snapshot = await PrivateFileRepository(session).access_snapshot(file_id)
    if row.bound_application_id is not None or (snapshot and snapshot["qualification_bound"]):
        raise HTTPException(409, "PRIVATE_FILE_BOUND")
    path = _path(row.object_key)
    row.status = "DELETED"; row.deleted_at = datetime.now(timezone.utc)
    await _commit_private_file(session, row.file_id, {"status": row.status, "deleted_at": row.deleted_at})
    if path.exists(): await asyncio.to_thread(path.unlink)


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
    snapshot = await PrivateFileRepository(session).access_snapshot(file_id)
    if snapshot is not None and snapshot["qualification_bound"]:
        return False
    value = _domain(row)
    if not value.is_orphan_expired(datetime.now(timezone.utc)):
        return False
    row.status = "DELETED"
    row.deleted_at = datetime.now(timezone.utc)
    await _commit_private_file(session, row.file_id, {"status": row.status, "deleted_at": row.deleted_at})
    if path.exists():
        await asyncio.to_thread(path.unlink)
    return True
