from __future__ import annotations

import hashlib
import hmac
import json
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from app.modules.health_fact.domain import (
    CATALOG_V1,
    CanonicalHealthFactDraft,
    HealthFactDigestKeyring,
)


_DOMAIN = b"kg:p3:health-indicator-legacy-mapping:source-fingerprint:v1\0"


class HealthLegacyMappingError(Exception):
    pass


class HealthLegacyMappingConflict(HealthLegacyMappingError):
    pass


class HealthLegacyMappingUnavailable(HealthLegacyMappingError):
    pass


@dataclass(frozen=True, slots=True)
class HealthIndicatorSourceSnapshot:
    legacy_indicator_id: int
    user_id: int
    indicator_type: str
    value: Decimal
    unit: str
    source: str
    recorded_at: datetime
    created_at: datetime
    legacy_batch_id: str | None


@dataclass(frozen=True, slots=True)
class HealthIndicatorLegacyMapping:
    legacy_indicator_id: int
    legacy_recorded_at: datetime
    canonical_fact_id: int | None
    mapping_version: int
    batch_id: str
    source_fingerprint: str
    digest_key_id: str
    disposition: str
    reason_code: str
    id: int | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PreparedHealthMapping:
    mapping: HealthIndicatorLegacyMapping
    fact_draft: CanonicalHealthFactDraft | None


def prepare_health_mapping(
    *,
    source: HealthIndicatorSourceSnapshot,
    mapping_version: int,
    batch_id: str,
    keyring: HealthFactDigestKeyring,
) -> PreparedHealthMapping:
    reason, disposition = _classify(source)
    producer_key = _producer_key(source)
    fact_draft = None
    if disposition == "MAPPED":
        fact_draft = CanonicalHealthFactDraft(
            subject_user_id=source.user_id,
            indicator_code=source.indicator_type,
            numeric_value=source.value,
            unit=source.unit,
            measured_at=source.recorded_at,
            source_type="APP",
            source_identity=producer_key,
            producer_event_key=producer_key,
        )
    mapping = HealthIndicatorLegacyMapping(
        legacy_indicator_id=source.legacy_indicator_id,
        legacy_recorded_at=source.recorded_at,
        canonical_fact_id=None,
        mapping_version=mapping_version,
        batch_id=batch_id,
        source_fingerprint=health_source_fingerprint(
            source=source,
            mapping_version=mapping_version,
            keyring=keyring,
            key_id=keyring.current_key_id,
        ),
        digest_key_id=keyring.current_key_id,
        disposition=disposition,
        reason_code=reason,
    )
    return PreparedHealthMapping(mapping=mapping, fact_draft=fact_draft)


def _classify(source: HealthIndicatorSourceSnapshot) -> tuple[str, str]:
    if source.user_id < 1:
        return "SUBJECT_MISSING", "UNMAPPED"
    if source.indicator_type not in CATALOG_V1:
        return "INDICATOR_NOT_OPEN", "UNMAPPED"
    if source.unit != CATALOG_V1[source.indicator_type]:
        return "UNIT_MISMATCH", "UNMAPPED"
    if not isinstance(source.value, Decimal) or not source.value.is_finite() or source.value.as_tuple().exponent < -2:
        return "VALUE_INVALID", "UNMAPPED"
    if source.recorded_at.tzinfo is None or source.created_at.tzinfo is None:
        return "TIME_INVALID", "UNMAPPED"
    if source.source not in {"APP", "STORE", "DEVICE", "REPORT"}:
        return "SOURCE_INVALID", "UNMAPPED"
    if source.source != "APP":
        return "SOURCE_AUTHORITY_UNVERIFIED", "REVIEW_REQUIRED"
    return "MAPPED_EXACT", "MAPPED"


def health_source_fingerprint(
    *,
    source: HealthIndicatorSourceSnapshot,
    mapping_version: int,
    keyring: HealthFactDigestKeyring,
    key_id: str,
) -> str:
    payload = {
        "batch_id": None,
        "created_at": _utc(source.created_at),
        "indicator_type": unicodedata.normalize("NFC", source.indicator_type),
        "legacy_indicator_id": source.legacy_indicator_id,
        "legacy_recorded_at": _utc(source.recorded_at),
        "mapping_version": mapping_version,
        "source": source.source,
        "unit": unicodedata.normalize("NFC", source.unit),
        "user_id": source.user_id,
        "value": f"{source.value:.2f}",
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(keyring.require_key(key_id), _DOMAIN + encoded, hashlib.sha256).hexdigest()


def _producer_key(source: HealthIndicatorSourceSnapshot) -> str:
    return f"legacy-health-indicator:v1:{source.legacy_indicator_id}:{_utc(source.recorded_at)}"


def _utc(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise HealthLegacyMappingUnavailable("Health mapping time is invalid")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def normalize_health_high_watermark(value) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {"max_recorded_at", "max_id"}:
        raise HealthLegacyMappingUnavailable("Health mapping high-watermark is invalid")
    source_id = value["max_id"]
    recorded_at = value["max_recorded_at"]
    if not isinstance(source_id, int) or isinstance(source_id, bool) or source_id < 1:
        raise HealthLegacyMappingUnavailable("Health mapping high-watermark is invalid")
    if not isinstance(recorded_at, datetime) or recorded_at.tzinfo is None:
        raise HealthLegacyMappingUnavailable("Health mapping high-watermark is invalid")
    return {"max_id": source_id, "max_recorded_at": _utc(recorded_at)}


def decode_health_high_watermark(value) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {"max_recorded_at", "max_id"}:
        raise HealthLegacyMappingUnavailable("Health mapping high-watermark is invalid")
    source_id = value["max_id"]
    encoded = value["max_recorded_at"]
    if not isinstance(source_id, int) or isinstance(source_id, bool) or source_id < 1:
        raise HealthLegacyMappingUnavailable("Health mapping high-watermark is invalid")
    if not isinstance(encoded, str) or len(encoded) != 27 or not encoded.endswith("Z"):
        raise HealthLegacyMappingUnavailable("Health mapping high-watermark is invalid")
    try:
        parsed = datetime.strptime(encoded, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        raise HealthLegacyMappingUnavailable(
            "Health mapping high-watermark is invalid"
        ) from None
    canonical = _utc(parsed)
    if canonical != encoded:
        raise HealthLegacyMappingUnavailable("Health mapping high-watermark is invalid")
    return {"max_id": source_id, "max_recorded_at": canonical}


def health_high_watermarks_equal(first, second) -> bool:
    try:
        return decode_health_high_watermark(first) == decode_health_high_watermark(second)
    except HealthLegacyMappingUnavailable:
        return False


def canonical_health_high_watermark_bytes(value) -> bytes:
    normalized = normalize_health_high_watermark(value)
    return json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def parse_health_high_watermark_json(encoded: str) -> dict[str, object]:
    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError
            value[key] = item
        return value

    try:
        value = json.loads(encoded, object_pairs_hook=unique_object)
    except Exception:
        raise HealthLegacyMappingUnavailable(
            "Health mapping high-watermark is invalid"
        ) from None
    return decode_health_high_watermark(value)
