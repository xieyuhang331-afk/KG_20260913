from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class PrivateFileConflict(ValueError):
    pass


class PrivateFileStatus(StrEnum):
    UPLOAD_INITIATED = "UPLOAD_INITIATED"
    PENDING_SCAN = "PENDING_SCAN"
    CLEAN = "CLEAN"
    REJECTED = "REJECTED"
    SCAN_FAILED = "SCAN_FAILED"


_ALLOWED_TYPES = {"application/pdf", "image/jpeg", "image/png"}
_MAX_SIZE = 10 * 1024 * 1024


@dataclass(slots=True)
class PrivateFile:
    file_id: UUID
    purpose: str
    owner_user_id: int
    declared_size: int
    declared_mime_type: str
    declared_sha256: str
    object_key: str
    expires_at: datetime
    created_at: datetime
    status: PrivateFileStatus = PrivateFileStatus.UPLOAD_INITIATED
    bound_application_id: UUID | None = None
    bound_at: datetime | None = None

    @classmethod
    def initiate(cls, **values: object) -> PrivateFile:
        now = values.pop("now")
        mime_type = values.get("declared_mime_type")
        size = values.get("declared_size")
        digest = values.get("declared_sha256")
        if mime_type not in _ALLOWED_TYPES:
            raise PrivateFileConflict("PRIVATE_FILE_TYPE_NOT_ALLOWED")
        if type(size) is not int or not 1 <= size <= _MAX_SIZE:
            raise PrivateFileConflict("PRIVATE_FILE_SIZE_INVALID")
        if not isinstance(digest, str) or len(digest) != 64:
            raise PrivateFileConflict("PRIVATE_FILE_HASH_INVALID")
        return cls(created_at=now, **values)  # type: ignore[arg-type]

    def complete_upload(
        self,
        *,
        actual_size: int,
        actual_mime_type: str,
        actual_sha256: str,
        now: datetime,
    ) -> None:
        if self.status is not PrivateFileStatus.UPLOAD_INITIATED:
            raise PrivateFileConflict("PRIVATE_FILE_STATE_CONFLICT")
        if now >= self.expires_at:
            raise PrivateFileConflict("PRIVATE_FILE_UPLOAD_EXPIRED")
        if (
            actual_size != self.declared_size
            or actual_mime_type != self.declared_mime_type
            or not hmac.compare_digest(actual_sha256, self.declared_sha256)
        ):
            raise PrivateFileConflict("PRIVATE_FILE_EVIDENCE_MISMATCH")
        self.status = PrivateFileStatus.PENDING_SCAN

    def record_scan(self, result: str, *, now: datetime) -> None:
        del now
        if self.status is not PrivateFileStatus.PENDING_SCAN:
            raise PrivateFileConflict("PRIVATE_FILE_STATE_CONFLICT")
        try:
            self.status = PrivateFileStatus(result)
        except ValueError as exc:
            raise PrivateFileConflict("PRIVATE_FILE_SCAN_RESULT_INVALID") from exc
        if self.status is PrivateFileStatus.PENDING_SCAN:
            raise PrivateFileConflict("PRIVATE_FILE_SCAN_RESULT_INVALID")

    def bind(self, *, application_id: UUID, now: datetime) -> None:
        if self.status is not PrivateFileStatus.CLEAN or self.bound_application_id is not None:
            raise PrivateFileConflict("PRIVATE_FILE_NOT_CLEAN")
        self.bound_application_id = application_id
        self.bound_at = now

    def is_orphan_expired(self, now: datetime) -> bool:
        return self.bound_application_id is None and now >= self.expires_at
