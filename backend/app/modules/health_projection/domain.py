from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Sequence
from uuid import UUID
from zoneinfo import ZoneInfo

from app.modules.health_fact.domain import CATALOG_V1
from app.modules.organization_projection.domain import ProjectionSourceInvalid, ProjectionUnavailable


_FACT_DOMAIN = b"kg:projection:health:core-fact-row:v1\0"
_SELECTION_DOMAIN = b"kg:projection:health:core-selection:v1\0"
_FACT_DOMAIN_V2 = b"kg:projection:health:member-fact-row:v2\0"
_SELECTION_DOMAIN_V2 = b"kg:projection:health:member-selection:v2\0"
_SOURCE_PRIORITY = {"APP": 1, "REPORT": 2, "STORE": 3, "DEVICE": 4}
_SHANGHAI = ZoneInfo("Asia/Shanghai")
RULE_VERSION = "health-daily-selection-v1"
RULE_VERSION_V2 = "health-daily-selection-v2"
_SOURCE_PRIORITY_V2 = {"APP": 1, "REPORT": 2, "STORE": 3}


@dataclass(frozen=True, slots=True)
class HealthCurrentFact:
    id: int
    subject_user_id: int
    indicator_code: str
    numeric_value: Decimal
    unit: str
    measured_at: datetime
    received_at: datetime
    source_type: str


@dataclass(frozen=True, slots=True)
class HealthCurrentFactV2:
    id: int
    fact_ref: UUID
    subject_member_id: UUID
    subject_user_id: int | None
    indicator_code: str
    numeric_value: Decimal
    unit: str
    measured_at: datetime
    received_at: datetime
    source_type: str
    verification_state: str
    status_event_seq: int
    superseded: bool
    fact_payload_digest: str | None = None
    status_event_digest: str | None = None
    measurement_context: str | None = None


@dataclass(frozen=True, slots=True)
class HealthProjectionFactRowV2:
    fact_id: int
    fact_ref: UUID
    subject_member_id: UUID
    subject_user_id: int | None
    indicator_code: str
    numeric_value: Decimal
    unit: str
    measured_at: datetime
    received_at: datetime
    source_type: str
    business_day: date
    window_start_utc: datetime
    window_end_utc: datetime
    verification_state: str
    status_event_seq: int
    row_digest: str
    measurement_context: str | None = None


@dataclass(frozen=True, slots=True)
class HealthWindowSelectionV2:
    subject_member_id: UUID
    indicator_code: str
    business_day: date
    winner_fact_id: int
    winner_fact_ref: UUID
    rule_version: str
    selection_digest: str


def select_window_winner_v2(facts: Sequence[HealthCurrentFactV2]) -> HealthCurrentFactV2:
    if not facts or any(fact.source_type not in _SOURCE_PRIORITY_V2 for fact in facts):
        raise ProjectionSourceInvalid("Projection source is invalid")
    candidates = [fact for fact in facts if not fact.superseded and fact.verification_state != "DISPUTED"]
    if not candidates:
        raise ProjectionSourceInvalid("Projection source is invalid")
    return max(candidates, key=lambda fact: (_SOURCE_PRIORITY_V2[fact.source_type], _aware(fact.measured_at), _aware(fact.received_at), fact.id))


def build_health_projection_rows_v2(
    *, facts: Sequence[HealthCurrentFactV2], digest_key: bytes
) -> tuple[list[HealthProjectionFactRowV2], list[HealthWindowSelectionV2]]:
    key = _key(digest_key)
    rows: list[HealthProjectionFactRowV2] = []
    windows: dict[tuple[UUID, str, date], list[HealthCurrentFactV2]] = {}
    units = {
        "systolic_bp": "mmHg", "diastolic_bp": "mmHg", "heart_rate": "bpm",
        "fasting_glucose": "mmol/L", "postprandial_glucose_2h": "mmol/L",
        "hba1c": "%", "total_cholesterol": "mmol/L",
        "triglyceride": "mmol/L", "hdl_c": "mmol/L", "ldl_c": "mmol/L",
        "weight": "kg",
        "height": "cm", "waist": "cm",
    }
    for fact in facts:
        if (
            type(fact) is not HealthCurrentFactV2
            or fact.source_type not in _SOURCE_PRIORITY_V2
            or units.get(fact.indicator_code) != fact.unit
            or fact.subject_member_id.version != 7
            or fact.fact_ref.version != 7
            or fact.verification_state not in {"SELF_REPORTED", "VERIFIED", "UNKNOWN", "DISPUTED"}
            or fact.status_event_seq < 1
        ):
            raise ProjectionSourceInvalid("Projection source is invalid")
        start, end, day = business_window(fact.measured_at)
        payload = {
            "fact_id": fact.id, "fact_ref": str(fact.fact_ref),
            "subject_member_id": str(fact.subject_member_id),
            "subject_user_id": fact.subject_user_id,
            "indicator_code": fact.indicator_code,
            "numeric_value": str(fact.numeric_value), "unit": fact.unit,
            "measured_at": _utc_text(fact.measured_at),
            "received_at": _utc_text(fact.received_at),
            "source_type": fact.source_type, "business_day": day.isoformat(),
            "verification_state": fact.verification_state,
            "status_event_seq": fact.status_event_seq,
            "measurement_context": fact.measurement_context,
        }
        rows.append(HealthProjectionFactRowV2(
            fact.id, fact.fact_ref, fact.subject_member_id, fact.subject_user_id,
            fact.indicator_code, fact.numeric_value, fact.unit, _aware(fact.measured_at),
            _aware(fact.received_at), fact.source_type, day, start, end,
            fact.verification_state, fact.status_event_seq,
            _digest(key, _FACT_DOMAIN_V2, payload),
            fact.measurement_context,
        ))
        windows.setdefault((fact.subject_member_id, fact.indicator_code, day), []).append(fact)
    selections: list[HealthWindowSelectionV2] = []
    for (member_id, indicator, day), candidates in sorted(windows.items(), key=lambda item: (str(item[0][0]), item[0][1], item[0][2])):
        winner = select_window_winner_v2(candidates)
        payload = {
            "subject_member_id": str(member_id), "indicator_code": indicator,
            "business_day": day.isoformat(), "winner_fact_ref": str(winner.fact_ref),
            "rule_version": RULE_VERSION_V2,
        }
        selections.append(HealthWindowSelectionV2(
            member_id, indicator, day, winner.id, winner.fact_ref, RULE_VERSION_V2,
            _digest(key, _SELECTION_DOMAIN_V2, payload),
        ))
    return rows, selections


@dataclass(frozen=True, slots=True)
class HealthProjectionFactRow:
    fact_id: int
    subject_user_id: int
    indicator_code: str
    numeric_value: Decimal
    unit: str
    measured_at: datetime
    received_at: datetime
    source_type: str
    business_day: date
    window_start_utc: datetime
    window_end_utc: datetime
    row_digest: str


@dataclass(frozen=True, slots=True)
class HealthWindowSelection:
    subject_user_id: int
    indicator_code: str
    business_day: date
    winner_fact_id: int
    rule_version: str
    selection_digest: str


def _aware(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ProjectionSourceInvalid("Projection source is invalid")
    return value.astimezone(UTC)


def _key(value: bytes) -> bytes:
    if not isinstance(value, bytes) or len(value) < 32:
        raise ProjectionUnavailable("Projection digest is unavailable") from None
    return value


def _utc_text(value: datetime) -> str:
    return _aware(value).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _digest(key: bytes, domain: bytes, payload: dict) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hmac.new(_key(key), domain + encoded, hashlib.sha256).hexdigest()


def business_window(measured_at: datetime) -> tuple[datetime, datetime, date]:
    measured = _aware(measured_at).astimezone(_SHANGHAI)
    day = measured.date()
    start_local = datetime.combine(day, time.min, tzinfo=_SHANGHAI)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC), day


def select_window_winner(facts: Sequence[HealthCurrentFact]) -> HealthCurrentFact:
    if not facts or any(fact.source_type not in _SOURCE_PRIORITY for fact in facts):
        raise ProjectionSourceInvalid("Projection source is invalid")
    return max(
        facts,
        key=lambda fact: (
            _SOURCE_PRIORITY[fact.source_type],
            _aware(fact.measured_at),
            _aware(fact.received_at),
            fact.id,
        ),
    )


def _validate_fact(fact: HealthCurrentFact) -> None:
    if (
        not isinstance(fact.id, int)
        or isinstance(fact.id, bool)
        or fact.id < 1
        or not isinstance(fact.subject_user_id, int)
        or isinstance(fact.subject_user_id, bool)
        or fact.subject_user_id < 1
        or fact.source_type not in _SOURCE_PRIORITY
        or CATALOG_V1.get(fact.indicator_code) != fact.unit
        or not isinstance(fact.numeric_value, Decimal)
        or not fact.numeric_value.is_finite()
        or fact.numeric_value != fact.numeric_value.quantize(Decimal("0.01"))
    ):
        raise ProjectionSourceInvalid("Projection source is invalid")
    _aware(fact.measured_at)
    _aware(fact.received_at)


def build_health_projection_rows(
    *, facts: Sequence[HealthCurrentFact], digest_key: bytes
) -> tuple[list[HealthProjectionFactRow], list[HealthWindowSelection]]:
    key = _key(digest_key)
    rows: list[HealthProjectionFactRow] = []
    windows: dict[tuple[int, str, date], list[HealthCurrentFact]] = {}
    for fact in facts:
        _validate_fact(fact)
        start, end, day = business_window(fact.measured_at)
        payload = {
            "fact_id": fact.id,
            "subject_user_id": fact.subject_user_id,
            "indicator_code": fact.indicator_code,
            "numeric_value": format(fact.numeric_value, ".2f"),
            "unit": fact.unit,
            "measured_at": _utc_text(fact.measured_at),
            "received_at": _utc_text(fact.received_at),
            "source_type": fact.source_type,
            "business_day": day.isoformat(),
            "window_start_utc": _utc_text(start),
            "window_end_utc": _utc_text(end),
        }
        rows.append(
            HealthProjectionFactRow(
                fact.id,
                fact.subject_user_id,
                fact.indicator_code,
                fact.numeric_value,
                fact.unit,
                _aware(fact.measured_at),
                _aware(fact.received_at),
                fact.source_type,
                day,
                start,
                end,
                _digest(key, _FACT_DOMAIN, payload),
            )
        )
        windows.setdefault((fact.subject_user_id, fact.indicator_code, day), []).append(fact)

    selections: list[HealthWindowSelection] = []
    for (subject, indicator, day), candidates in sorted(windows.items()):
        winner = select_window_winner(candidates)
        payload = {
            "subject_user_id": subject,
            "indicator_code": indicator,
            "business_day": day.isoformat(),
            "winner_fact_id": winner.id,
            "rule_version": RULE_VERSION,
        }
        selections.append(
            HealthWindowSelection(
                subject,
                indicator,
                day,
                winner.id,
                RULE_VERSION,
                _digest(key, _SELECTION_DOMAIN, payload),
            )
        )
    return rows, selections
