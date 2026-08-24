from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping
from uuid import UUID


CATALOG_VERSION = 1
CATALOG_V1 = MappingProxyType(
    {
        "systolic_bp": "mmHg",
        "diastolic_bp": "mmHg",
        "heart_rate": "bpm",
        "fasting_glucose": "mmol/L",
        "postprandial_glucose_2h": "mmol/L",
        "hba1c": "%",
        "total_cholesterol": "mmol/L",
        "triglyceride": "mmol/L",
        "hdl_c": "mmol/L",
        "ldl_c": "mmol/L",
        "weight": "kg",
        "bmi": "kg/m2",
        "uric_acid": "umol/L",
        "spo2": "%",
        "bone_density_t_score": "T-score",
    }
)
CATALOG_V2 = MappingProxyType(
    {
        "systolic_bp": "mmHg",
        "diastolic_bp": "mmHg",
        "heart_rate": "bpm",
        "fasting_glucose": "mmol/L",
        "postprandial_glucose_2h": "mmol/L",
        "hba1c": "%",
        "total_cholesterol": "mmol/L",
        "triglyceride": "mmol/L",
        "hdl_c": "mmol/L",
        "ldl_c": "mmol/L",
        "weight": "kg",
        "height": "cm",
        "waist": "cm",
    }
)
SOURCE_TYPES = frozenset({"APP", "STORE", "DEVICE", "REPORT"})
FORMAL_SOURCE_TYPES = frozenset({"APP", "STORE", "REPORT"})
_KEY_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_MAX_NUMERIC = Decimal("99999999.99")


class HealthFactError(Exception):
    pass


class HealthFactRequestInvalid(HealthFactError):
    pass


class HealthFactCatalogUnknown(HealthFactError):
    pass


class HealthFactSourceForbidden(HealthFactError):
    pass


class HealthFactIdempotencyConflict(HealthFactError):
    pass


class HealthFactCorrectionConflict(HealthFactError):
    pass


class HealthFactUnavailable(HealthFactError):
    pass


class HealthFactCommitOutcomeUnknown(HealthFactError):
    pass


@dataclass(frozen=True, slots=True)
class HealthFactCorrectionPlan:
    supersedes_fact_ref: UUID
    new_fact_ref: UUID
    initial_state: str
    reason_code: str
    updates_predecessor: bool = False


def compute_bmi(*, height_cm: Decimal, weight_kg: Decimal) -> Decimal:
    if (
        type(height_cm) is not Decimal
        or type(weight_kg) is not Decimal
        or not height_cm.is_finite()
        or not weight_kg.is_finite()
        or height_cm <= 0
        or weight_kg <= 0
    ):
        raise HealthFactRequestInvalid("BMI inputs are invalid")
    height_m = height_cm / Decimal("100")
    return (weight_kg / (height_m * height_m)).quantize(Decimal("0.1"))


def initial_verification_state(source_type: str) -> str:
    if type(source_type) is not str or source_type not in FORMAL_SOURCE_TYPES:
        raise HealthFactSourceForbidden("Health fact source is forbidden")
    return "SELF_REPORTED" if source_type == "APP" else "UNKNOWN"


def build_status_transition_plan(
    *,
    predecessor: Mapping[str, object],
    target_state: str,
    reason_code: str,
    new_fact_ref: UUID,
) -> HealthFactCorrectionPlan:
    predecessor_ref = predecessor.get("fact_ref")
    if (
        predecessor.get("state") != "DISPUTED"
        or target_state != "SELF_REPORTED"
        or type(predecessor_ref) is not UUID
        or predecessor_ref.version != 7
        or type(new_fact_ref) is not UUID
        or new_fact_ref.version != 7
        or new_fact_ref == predecessor_ref
        or type(reason_code) is not str
        or not _KEY_ID.fullmatch(reason_code)
    ):
        raise HealthFactCorrectionConflict("Health fact correction conflict")
    return HealthFactCorrectionPlan(
        supersedes_fact_ref=predecessor_ref,
        new_fact_ref=new_fact_ref,
        initial_state="SELF_REPORTED",
        reason_code=reason_code,
    )


@dataclass(frozen=True, slots=True)
class HealthFactDigestKeyring:
    current_key_id: str
    keys: Mapping[str, bytes]

    @classmethod
    def from_json(
        cls, *, current_key_id: str | None, keyring_json: str | None
    ) -> "HealthFactDigestKeyring":
        try:
            data = (
                json.loads(keyring_json, object_pairs_hook=_unique_json_object)
                if keyring_json
                else None
            )
            if not isinstance(data, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in data.items()
            ):
                raise ValueError
        except Exception:
            raise HealthFactUnavailable("Health fact digest keyring is unavailable") from None
        return cls.from_base64(
            current_key_id=current_key_id or "", encoded_keys=data
        )

    @classmethod
    def from_base64(
        cls,
        *,
        current_key_id: str,
        encoded_keys: Mapping[str, str],
    ) -> "HealthFactDigestKeyring":
        if not _KEY_ID.fullmatch(current_key_id) or current_key_id not in encoded_keys:
            raise HealthFactUnavailable("Health fact digest keyring is unavailable")
        decoded: dict[str, bytes] = {}
        try:
            for key_id, encoded in encoded_keys.items():
                if not _KEY_ID.fullmatch(key_id) or key_id in decoded:
                    raise ValueError
                key = base64.b64decode(encoded, validate=True)
                if len(key) < 32:
                    raise ValueError
                decoded[key_id] = key
        except Exception:
            raise HealthFactUnavailable("Health fact digest keyring is unavailable") from None
        return cls(current_key_id=current_key_id, keys=MappingProxyType(decoded))

    def require_key(self, key_id: str) -> bytes:
        key = self.keys.get(key_id)
        if key is None:
            raise HealthFactUnavailable("Health fact digest key is unavailable")
        return key


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class CanonicalHealthFactDraft:
    subject_user_id: int | None
    indicator_code: str
    numeric_value: Decimal
    unit: str
    measured_at: datetime
    source_type: str
    source_identity: str
    producer_event_key: str
    supersedes_fact_id: int | None = None
    correction_reason_code: str | None = None
    created_by: int | None = None
    subject_member_id: UUID | None = None
    fact_ref: UUID | None = None
    report_id: UUID | None = None
    catalog_version: int = CATALOG_VERSION
    measurement_context: str | None = None


@dataclass(frozen=True, slots=True)
class CanonicalHealthFact:
    subject_user_id: int | None
    indicator_code: str
    catalog_version: int
    value_kind: str
    numeric_value: Decimal
    unit: str
    measured_at: datetime
    source_type: str
    source_identity_digest: str
    producer_event_key: str
    payload_digest: str
    digest_key_id: str
    supersedes_fact_id: int | None
    correction_reason_code: str | None
    created_by: int | None
    id: int | None = None
    received_at: datetime | None = None
    created_at: datetime | None = None
    subject_member_id: UUID | None = None
    fact_ref: UUID | None = None
    report_id: UUID | None = None
    measurement_context: str | None = None


def _validate_draft(draft: CanonicalHealthFactDraft) -> None:
    if draft.catalog_version == 1:
        identity_valid = (
            type(draft.subject_user_id) is int
            and draft.subject_user_id >= 1
            and draft.subject_member_id is None
            and draft.fact_ref is None
        )
        catalog = CATALOG_V1
    elif draft.catalog_version == 2:
        identity_valid = (
            (draft.subject_user_id is None or (type(draft.subject_user_id) is int and draft.subject_user_id >= 1))
            and type(draft.subject_member_id) is UUID
            and draft.subject_member_id.version == 7
            and type(draft.fact_ref) is UUID
            and draft.fact_ref.version == 7
        )
        catalog = CATALOG_V2
    else:
        identity_valid = False
        catalog = {}
    if not identity_valid:
        raise HealthFactRequestInvalid("Health fact request is invalid")
    expected_unit = catalog.get(draft.indicator_code)
    if expected_unit is None:
        raise HealthFactCatalogUnknown("Health fact catalog entry is unknown")
    if draft.unit != expected_unit:
        raise HealthFactRequestInvalid("Health fact canonical unit is invalid")
    value = draft.numeric_value
    if not isinstance(value, Decimal) or not value.is_finite():
        raise HealthFactRequestInvalid("Health fact numeric value is invalid")
    if value.as_tuple().exponent < -2 or abs(value) > _MAX_NUMERIC:
        raise HealthFactRequestInvalid("Health fact numeric value is invalid")
    if draft.measured_at.tzinfo is None or draft.measured_at.utcoffset() is None:
        raise HealthFactRequestInvalid("Health fact measured_at is invalid")
    if draft.source_type not in (
        SOURCE_TYPES if draft.catalog_version == 1 else FORMAL_SOURCE_TYPES
    ):
        raise HealthFactSourceForbidden("Health fact source is forbidden")
    if draft.catalog_version == 2 and (
        (draft.source_type == "REPORT") != (type(draft.report_id) is UUID)
        or (draft.report_id is not None and draft.report_id.version != 7)
    ):
        raise HealthFactRequestInvalid("Health fact report binding is invalid")
    if draft.catalog_version == 1 and draft.report_id is not None:
        raise HealthFactRequestInvalid("Health fact report binding is invalid")
    contexts = {
        "systolic_bp": {"OFFICE", "HOME_AVERAGE", "ABPM_24H_AVERAGE", "ABPM_DAY_AVERAGE", "ABPM_NIGHT_AVERAGE"},
        "diastolic_bp": {"OFFICE", "HOME_AVERAGE", "ABPM_24H_AVERAGE", "ABPM_DAY_AVERAGE", "ABPM_NIGHT_AVERAGE"},
        "fasting_glucose": {"FASTING_VENOUS"},
        "postprandial_glucose_2h": {"OGTT_2H_VENOUS"},
        "hba1c": {"LAB"},
        "total_cholesterol": {"FASTING_LAB"}, "triglyceride": {"FASTING_LAB"},
        "hdl_c": {"FASTING_LAB"}, "ldl_c": {"FASTING_LAB"},
    }
    if draft.measurement_context is not None and draft.measurement_context not in contexts.get(draft.indicator_code, set()):
        raise HealthFactRequestInvalid("Health fact measurement context is invalid")
    if not draft.source_identity or len(draft.source_identity) > 512:
        raise HealthFactSourceForbidden("Health fact source is forbidden")
    if not draft.producer_event_key or len(draft.producer_event_key) > 128:
        raise HealthFactRequestInvalid("Health fact producer event key is invalid")
    correction_complete = (draft.supersedes_fact_id is None) == (
        draft.correction_reason_code is None
    )
    if not correction_complete:
        raise HealthFactRequestInvalid("Health fact correction identity is incomplete")
    if draft.supersedes_fact_id is not None and draft.supersedes_fact_id < 1:
        raise HealthFactRequestInvalid("Health fact correction identity is invalid")
    if draft.correction_reason_code is not None and not _KEY_ID.fullmatch(
        draft.correction_reason_code
    ):
        raise HealthFactRequestInvalid("Health fact correction reason is invalid")


def _hmac_hex(key: bytes, domain: bytes, payload: bytes) -> str:
    return hmac.new(key, domain + b"\0" + payload, hashlib.sha256).hexdigest()


def _source_identity_payload(source_identity: str) -> bytes:
    return unicodedata.normalize("NFC", source_identity).encode("utf-8")


def source_identity_digests(
    *, source_identity: str, keyring: HealthFactDigestKeyring
) -> dict[str, str]:
    payload = _source_identity_payload(source_identity)
    return {
        key_id: _hmac_hex(key, b"health-fact-source-identity-v1", payload)
        for key_id, key in keyring.keys.items()
    }


def _payload_bytes(draft: CanonicalHealthFactDraft) -> bytes:
    measured_at = draft.measured_at.astimezone(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    payload = {
        "catalog_version": draft.catalog_version,
        "correction_reason_code": draft.correction_reason_code,
        "created_by": draft.created_by,
        "indicator_code": draft.indicator_code,
        "measured_at": measured_at,
        "measurement_context": draft.measurement_context,
        "numeric_value": format(draft.numeric_value.quantize(Decimal("0.01")), "f"),
        "producer_event_key": draft.producer_event_key,
        "source_type": draft.source_type,
        "subject_user_id": draft.subject_user_id,
        "subject_member_id": (
            str(draft.subject_member_id) if draft.subject_member_id is not None else None
        ),
        "fact_ref": str(draft.fact_ref) if draft.fact_ref is not None else None,
        "report_id": str(draft.report_id) if draft.report_id is not None else None,
        "supersedes_fact_id": draft.supersedes_fact_id,
        "unit": draft.unit,
        "value_kind": "NUMERIC",
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def prepare_fact(
    draft: CanonicalHealthFactDraft, keyring: HealthFactDigestKeyring
) -> CanonicalHealthFact:
    _validate_draft(draft)
    key = keyring.require_key(keyring.current_key_id)
    source_digest = source_identity_digests(
        source_identity=draft.source_identity, keyring=keyring
    )[keyring.current_key_id]
    payload_digest = _hmac_hex(
        key,
        (
            b"health-fact-payload-v1"
            if draft.catalog_version == 1
            else b"health-fact-payload-v2"
        ),
        _payload_bytes(draft),
    )
    return CanonicalHealthFact(
        subject_user_id=draft.subject_user_id,
        indicator_code=draft.indicator_code,
        catalog_version=draft.catalog_version,
        value_kind="NUMERIC",
        numeric_value=draft.numeric_value.quantize(Decimal("0.01")),
        unit=draft.unit,
        measured_at=draft.measured_at,
        source_type=draft.source_type,
        source_identity_digest=source_digest,
        producer_event_key=draft.producer_event_key,
        payload_digest=payload_digest,
        digest_key_id=keyring.current_key_id,
        supersedes_fact_id=draft.supersedes_fact_id,
        correction_reason_code=draft.correction_reason_code,
        created_by=draft.created_by,
        subject_member_id=draft.subject_member_id,
        fact_ref=draft.fact_ref,
        report_id=draft.report_id,
        measurement_context=draft.measurement_context,
    )


def verify_payload(
    *,
    stored: CanonicalHealthFact,
    draft: CanonicalHealthFactDraft,
    keyring: HealthFactDigestKeyring,
) -> bool:
    _validate_draft(draft)
    key = keyring.require_key(stored.digest_key_id)
    expected = _hmac_hex(
        key,
        (
            b"health-fact-payload-v1"
            if draft.catalog_version == 1
            else b"health-fact-payload-v2"
        ),
        _payload_bytes(draft),
    )
    return hmac.compare_digest(stored.payload_digest, expected)


def semantic_lock_key(*, source_type: str, producer_event_key: str) -> int:
    digest = hashlib.sha256(
        b"health-fact-semantic-lock-v1\0"
        + source_type.encode("ascii")
        + b"\0"
        + producer_event_key.encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)
