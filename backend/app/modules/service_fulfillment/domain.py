from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from io import BytesIO
from types import MappingProxyType
from typing import Mapping
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo
from zoneinfo import ZoneInfo


BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")


class MilestoneCode(StrEnum):
    D0 = "D0"
    D7 = "D7"
    D14 = "D14"
    D21 = "D21"
    D28 = "D28"


class MilestoneStatus(StrEnum):
    PENDING = "PENDING"
    DUE = "DUE"
    COMPLETED = "COMPLETED"
    MISSED = "MISSED"
    INVALIDATED = "INVALIDATED"


class CaseLifecycleStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    CLOSING = "CLOSING"
    COMPLETED = "COMPLETED"
    WITHDRAWN_BY_USER = "WITHDRAWN_BY_USER"
    TERMINATED_BY_INSTITUTION = "TERMINATED_BY_INSTITUTION"
    TRANSFERRED = "TRANSFERRED"
    UNABLE_TO_CONTACT = "UNABLE_TO_CONTACT"
    SAFETY_TERMINATED = "SAFETY_TERMINATED"


class ClosingReadiness(StrEnum):
    NOT_READY = "NOT_READY"
    READY_TO_CLOSE = "READY_TO_CLOSE"
    BLOCKED_BY_HIGH_RISK = "BLOCKED_BY_HIGH_RISK"
    BLOCKED_BY_MISSING_MILESTONE = "BLOCKED_BY_MISSING_MILESTONE"
    BLOCKED_BY_USER_ACK = "BLOCKED_BY_USER_ACK"


class TransferStatus(StrEnum):
    REQUESTED_BY_USER = "REQUESTED_BY_USER"
    NEW_INSTITUTION_REVIEWING = "NEW_INSTITUTION_REVIEWING"
    ACCEPTED = "ACCEPTED"
    OLD_INSTITUTION_CLOSING = "OLD_INSTITUTION_CLOSING"
    USER_SCOPE_CONFIRMED = "USER_SCOPE_CONFIRMED"
    TRANSFERRED = "TRANSFERRED"
    REJECTED_BY_NEW_INSTITUTION = "REJECTED_BY_NEW_INSTITUTION"
    CANCELLED_BY_USER = "CANCELLED_BY_USER"


class ExportStatus(StrEnum):
    REQUESTED = "REQUESTED"
    GENERATING = "GENERATING"
    READY = "READY"
    DOWNLOADED = "DOWNLOADED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


_EXPORT_SCOPES = frozenset(
    {
        "PROFILE",
        "REPORT",
        "CANONICAL_FACT",
        "ASSESSMENT",
        "APPROVED_PLAN",
        "MILESTONE",
        "SERVICE_SUMMARY",
    }
)
_EXPORT_FORBIDDEN_KEYS = frozenset(
    {
        "ciphertext",
        "identity_ciphertext",
        "credential",
        "password_hash",
        "receipt",
        "outbox",
        "audit",
        "database_id",
        "object_key",
        "storage_key",
    }
)


@dataclass(frozen=True, slots=True)
class PersonalDataExportArchive:
    data: bytes
    manifest_digest: str
    artifact_digest: str


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _assert_export_safe(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in _EXPORT_FORBIDDEN_KEYS or normalized.endswith("_ciphertext"):
                raise ValueError("EXPORT_SNAPSHOT_FORBIDDEN_FIELD")
            _assert_export_safe(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_export_safe(item)


def _zip_entry(name: str, value: bytes) -> tuple[ZipInfo, bytes]:
    info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    info.create_system = 3
    return info, value


def build_personal_data_export_archive(
    snapshot: Mapping[str, object],
    *,
    attachments: Mapping[str, bytes] | None = None,
) -> PersonalDataExportArchive:
    requested_scope = tuple(sorted(str(item) for item in snapshot.get("requested_scope", ())))
    data = snapshot.get("data")
    if (
        not requested_scope
        or any(scope not in _EXPORT_SCOPES for scope in requested_scope)
        or not isinstance(data, Mapping)
        or set(data) != set(requested_scope)
    ):
        raise ValueError("EXPORT_SNAPSHOT_SCOPE_MISMATCH")
    _assert_export_safe(snapshot)
    attachment_values = attachments or {}
    if any(
        not name.startswith("attachments/")
        or name.startswith("/")
        or ".." in name.split("/")
        or not isinstance(content, bytes)
        for name, content in attachment_values.items()
    ):
        raise ValueError("EXPORT_ATTACHMENT_INVALID")
    manifest = {
        "attachments": [
            {
                "path": name,
                "sha256": hashlib.sha256(attachment_values[name]).hexdigest(),
                "size": len(attachment_values[name]),
            }
            for name in sorted(attachment_values)
        ],
        "export_id": snapshot.get("export_id"),
        "subject_member_id": snapshot.get("subject_member_id"),
        "requested_scope": requested_scope,
        "source_versions": snapshot.get("source_versions", {}),
        "version": 1,
    }
    if not all(isinstance(manifest[key], str) for key in ("export_id", "subject_member_id")):
        raise ValueError("EXPORT_SNAPSHOT_INVALID")
    manifest_bytes = _canonical_json(manifest)
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in (
            ("manifest.json", manifest_bytes),
            *(
                (f"data/{scope.lower()}.json", _canonical_json(data[scope]))
                for scope in requested_scope
            ),
            *((name, attachment_values[name]) for name in sorted(attachment_values)),
        ):
            info, payload = _zip_entry(name, content)
            archive.writestr(info, payload, compress_type=ZIP_DEFLATED, compresslevel=9)
    archive_data = buffer.getvalue()
    return PersonalDataExportArchive(
        data=archive_data,
        manifest_digest=hashlib.sha256(manifest_bytes).hexdigest(),
        artifact_digest=hashlib.sha256(archive_data).hexdigest(),
    )


_TRANSFER_TRANSITIONS = frozenset(
    {
        (TransferStatus.REQUESTED_BY_USER, TransferStatus.NEW_INSTITUTION_REVIEWING),
        (TransferStatus.REQUESTED_BY_USER, TransferStatus.CANCELLED_BY_USER),
        (TransferStatus.NEW_INSTITUTION_REVIEWING, TransferStatus.ACCEPTED),
        (TransferStatus.NEW_INSTITUTION_REVIEWING, TransferStatus.REJECTED_BY_NEW_INSTITUTION),
        (TransferStatus.NEW_INSTITUTION_REVIEWING, TransferStatus.CANCELLED_BY_USER),
        (TransferStatus.ACCEPTED, TransferStatus.OLD_INSTITUTION_CLOSING),
        (TransferStatus.OLD_INSTITUTION_CLOSING, TransferStatus.USER_SCOPE_CONFIRMED),
        (TransferStatus.USER_SCOPE_CONFIRMED, TransferStatus.TRANSFERRED),
    }
)


def next_transfer_status(current: TransferStatus, requested: TransferStatus) -> TransferStatus:
    if (current, requested) not in _TRANSFER_TRANSITIONS:
        raise ValueError("TRANSFER_STATE_CONFLICT")
    return requested


@dataclass(frozen=True, slots=True)
class MilestoneWindow:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("MILESTONE_WINDOW_INVALID")

    def contains(self, value: date) -> bool:
        return self.start <= value <= self.end


_WINDOW_OFFSETS: Mapping[MilestoneCode, tuple[int, int]] = MappingProxyType(
    {
        MilestoneCode.D0: (0, 0),
        MilestoneCode.D7: (4, 8),
        MilestoneCode.D14: (12, 16),
        MilestoneCode.D21: (19, 23),
        MilestoneCode.D28: (25, 31),
    }
)


class SystemBusinessClock:
    def __init__(self, *, override: datetime | None = None) -> None:
        if override is not None:
            raise ValueError("SYNTHETIC_CLOCK_FORBIDDEN")

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class SyntheticBusinessClock:
    def __init__(self, value: datetime) -> None:
        self._value = _aware_utc(value)

    def now(self) -> datetime:
        return self._value

    def advance(self, delta: timedelta) -> None:
        if delta < timedelta(0):
            raise ValueError("BUSINESS_CLOCK_CANNOT_REWIND")
        self._value += delta


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("AWARE_DATETIME_REQUIRED")
    return value.astimezone(timezone.utc)


def business_date(value: datetime) -> date:
    return _aware_utc(value).astimezone(BUSINESS_TIMEZONE).date()


def build_milestone_windows(activated_at: datetime) -> Mapping[MilestoneCode, MilestoneWindow]:
    anchor = business_date(activated_at)
    return MappingProxyType(
        {
            code: MilestoneWindow(anchor + timedelta(days=start), anchor + timedelta(days=end))
            for code, (start, end) in _WINDOW_OFFSETS.items()
        }
    )


def derive_milestone_status(
    window: MilestoneWindow,
    *,
    on_date: date,
    completed: bool,
    invalidated: bool = False,
) -> MilestoneStatus:
    if invalidated:
        return MilestoneStatus.INVALIDATED
    if completed:
        return MilestoneStatus.COMPLETED
    if on_date > window.end:
        return MilestoneStatus.MISSED
    if window.contains(on_date):
        return MilestoneStatus.DUE
    return MilestoneStatus.PENDING


def derive_case_risk(statuses: Mapping[MilestoneCode, MilestoneStatus]) -> str | None:
    consecutive = 0
    for code in MilestoneCode:
        if statuses.get(code) is MilestoneStatus.MISSED:
            consecutive += 1
            if consecutive >= 2:
                return "AT_RISK"
        else:
            consecutive = 0
    return None


def resume_schedule(
    windows: Mapping[MilestoneCode, MilestoneWindow],
    *,
    paused_on: date,
    resumed_on: date,
    statuses: Mapping[MilestoneCode, MilestoneStatus],
    can_fit_within_limit: bool = True,
) -> Mapping[MilestoneCode, MilestoneWindow]:
    if resumed_on < paused_on:
        raise ValueError("RESUME_DATE_INVALID")
    if not can_fit_within_limit:
        raise ValueError("RESUME_REQUIRES_CLOSING")
    delta = resumed_on - paused_on
    revised: dict[MilestoneCode, MilestoneWindow] = {}
    for code in MilestoneCode:
        window = windows[code]
        status = statuses[code]
        if status is MilestoneStatus.PENDING and window.start > paused_on:
            revised[code] = MilestoneWindow(window.start + delta, window.end + delta)
        else:
            revised[code] = window
    return MappingProxyType(revised)


def closing_readiness(
    statuses: Mapping[MilestoneCode, MilestoneStatus],
    *,
    closing_assessment_complete: bool,
    summary_complete: bool,
    user_acknowledged: bool,
    open_high_risk_count: int,
) -> ClosingReadiness:
    if any(statuses.get(code) is not MilestoneStatus.COMPLETED for code in MilestoneCode):
        return ClosingReadiness.BLOCKED_BY_MISSING_MILESTONE
    if open_high_risk_count > 0:
        return ClosingReadiness.BLOCKED_BY_HIGH_RISK
    if not closing_assessment_complete or not summary_complete:
        return ClosingReadiness.NOT_READY
    if not user_acknowledged:
        return ClosingReadiness.BLOCKED_BY_USER_ACK
    return ClosingReadiness.READY_TO_CLOSE
