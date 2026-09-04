from __future__ import annotations

import hmac
import hashlib
from io import BytesIO
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from enum import StrEnum
from uuid import UUID
from zipfile import BadZipFile, ZipFile


class PrivateFileConflict(ValueError):
    pass


class PrivateFileStatus(StrEnum):
    UPLOAD_INITIATED = "UPLOAD_INITIATED"
    PENDING_SCAN = "PENDING_SCAN"
    CLEAN = "CLEAN"
    REJECTED = "REJECTED"
    SCAN_FAILED = "SCAN_FAILED"


PRIVATE_FILE_SCAN_MAX_ATTEMPTS = 4
PRIVATE_FILE_SCAN_RETRY_DELAYS = (5, 30, 120)
PRIVATE_FILE_SCAN_LEASE = timedelta(minutes=5)


class PrivateFileScanFailure(StrEnum):
    OBJECT_MISSING = "OBJECT_MISSING"
    EVIDENCE_MISMATCH = "EVIDENCE_MISMATCH"
    SCAN_SERVICE_UNAVAILABLE = "SCAN_SERVICE_UNAVAILABLE"
    WORKER_LOST = "WORKER_LOST"
    SCAN_STATE_UNKNOWN = "SCAN_STATE_UNKNOWN"
    COMMIT_OUTCOME_UNKNOWN = "COMMIT_OUTCOME_UNKNOWN"


def private_file_scan_retry_delay(attempt_count: int) -> int | None:
    if type(attempt_count) is not int or not 1 <= attempt_count <= 4:
        raise PrivateFileConflict("PRIVATE_FILE_SCAN_ATTEMPT_INVALID")
    if attempt_count == PRIVATE_FILE_SCAN_MAX_ATTEMPTS:
        return None
    return PRIVATE_FILE_SCAN_RETRY_DELAYS[attempt_count - 1]


@dataclass(frozen=True, slots=True)
class DetectionReportAccessContext:
    actor_type: str
    actor_current: bool
    subject_matches: bool
    case_current: bool
    tenant_matches: bool
    permission_codes: tuple[str, ...]

    def may_read_original(self) -> bool:
        if not all((self.actor_current, self.subject_matches, self.case_current, self.tenant_matches)):
            return False
        if self.actor_type == "SELF":
            return True
        if self.actor_type == "PROXY":
            return "DAILY_VIEW" in self.permission_codes
        return self.actor_type == "THERAPIST"


def build_detection_report_access_evidence(
    *, file_id: UUID, subject_member_id: UUID, service_case_id: UUID,
    actor_user_id: int, actor_version: int, grant_or_assignment_version: int,
    expires_at: datetime,
) -> dict[str, object]:
    if (
        any(type(value) is not UUID or value.version != 7 for value in (file_id, subject_member_id, service_case_id))
        or any(type(value) is not int or value < 1 for value in (actor_user_id, actor_version, grant_or_assignment_version))
        or expires_at.tzinfo is None or expires_at.utcoffset() is None
    ):
        raise PrivateFileConflict("PRIVATE_FILE_ACCESS_INVALID")
    return {
        "file_id": file_id, "subject_member_id": subject_member_id,
        "service_case_id": service_case_id, "actor_user_id": actor_user_id,
        "actor_version": actor_version,
        "grant_or_assignment_version": grant_or_assignment_version,
        "expires_at": expires_at,
    }


_ALLOWED_TYPES = {"application/pdf", "image/jpeg", "image/png"}
_MAX_SIZE = 10 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class GeneratedExportArchiveEvidence:
    size: int
    sha256: str
    mime_type: str = "application/zip"


def validate_generated_export_archive(
    *, purpose: str, data: bytes
) -> GeneratedExportArchiveEvidence:
    if purpose != "PERSONAL_DATA_EXPORT":
        raise PrivateFileConflict("PRIVATE_FILE_EXPORT_PURPOSE_REQUIRED")
    if type(data) is not bytes or not 1 <= len(data) <= _MAX_SIZE:
        raise PrivateFileConflict("PRIVATE_FILE_EXPORT_ARCHIVE_INVALID")
    try:
        with ZipFile(BytesIO(data), "r") as archive:
            entries = archive.infolist()
            if (
                not entries
                or "manifest.json" not in {entry.filename for entry in entries}
                or any(
                    entry.flag_bits & 0x1
                    or entry.is_dir()
                    or entry.filename.startswith(("/", "\\"))
                    or ".." in entry.filename.replace("\\", "/").split("/")
                    for entry in entries
                )
                or sum(entry.file_size for entry in entries) > _MAX_SIZE
            ):
                raise PrivateFileConflict("PRIVATE_FILE_EXPORT_ARCHIVE_INVALID")
            for entry in entries:
                with archive.open(entry, "r") as source:
                    while source.read(64 * 1024):
                        pass
    except (BadZipFile, OSError, RuntimeError):
        raise PrivateFileConflict("PRIVATE_FILE_EXPORT_ARCHIVE_INVALID") from None
    return GeneratedExportArchiveEvidence(
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


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
