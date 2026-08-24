from __future__ import annotations

import asyncio
import base64
import binascii
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.modules.auth.repository import get_user_by_id
from app.modules.health_fact.domain import (
    CanonicalHealthFactDraft,
    HealthFactDigestKeyring,
    compute_bmi,
    initial_verification_state,
    prepare_fact,
    semantic_lock_key,
    source_identity_digests,
    verify_payload,
)
from app.modules.user_health.repository import (
    create_health_indicator_records,
    create_health_profile_record,
    get_health_profile_by_user_id,
    get_member_detection_report,
    get_member_profile_user_state,
    list_member_detection_reports,
    list_member_health_indicator_history,
    list_member_latest_health_indicators,
    list_health_indicators_by_user,
    list_latest_health_indicators_by_user,
    update_health_profile_record,
)
from app.modules.user_health.schemas import (
    DetectionReportAttachmentDTO,
    DetectionReportDTO,
    DetectionReportDetailDTO,
    DetectionReportPageDTO,
    HealthIdentitySummaryDTO,
    HealthIndicatorBatchCreateRequest,
    HealthIndicatorResponse,
    HealthProfileCreateRequest,
    HealthProfileResponse,
    MemberSelfHealthProfileData,
    MemberSelfHealthProfileResult,
    MemberSelfHealthProfileWriteRequest,
    MemberSelfHealthIndicatorItem,
    MemberSelfHealthIndicatorLatest,
    MemberSelfHealthIndicatorPage,
    DetectionReportStoredData,
    MemberSelfDetectionReportDetail,
    MemberSelfDetectionReportListItem,
    MemberSelfDetectionReportPage,
    HealthProfileDTO,
    HealthFactBatchDTO,
    HealthFactDTO,
)


ALLOWED_HEALTH_INDICATOR_SOURCES = {"APP", "STORE", "DEVICE", "REPORT"}


_SLICE4_SECRET_DOMAINS = (
    "PROFILE_PHI",
    "ASSEMBLY_PHI",
    "REQUEST_DIGEST",
    "AUDIT_DIGEST",
    "OUTBOX_DIGEST",
    "REPLAY_DIGEST",
    "DELIVERY",
    "COORDINATION",
)


def _dependency_unavailable() -> RuntimeError:
    return RuntimeError("DEPENDENCY_UNAVAILABLE")


def _load_slice4_keyring(domain: str) -> tuple[str, dict[str, bytes]]:
    prefix = f"KG_SLICE4_{domain}"
    current_key_id = os.environ.get(f"{prefix}_CURRENT_KEY_ID")
    raw_keyring = os.environ.get(f"{prefix}_KEYRING_JSON")
    if not current_key_id or not raw_keyring:
        raise _dependency_unavailable()

    def reject_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key id")
            result[key] = value
        return result

    try:
        encoded = json.loads(raw_keyring, object_pairs_hook=reject_duplicate_keys)
        if type(encoded) is not dict or not encoded or current_key_id not in encoded:
            raise ValueError("invalid keyring")
        keyring: dict[str, bytes] = {}
        for key_id, material in encoded.items():
            if type(key_id) is not str or not key_id or type(material) is not str:
                raise ValueError("invalid key")
            decoded = base64.b64decode(material, validate=True)
            if len(decoded) != 32:
                raise ValueError("invalid key length")
            keyring[key_id] = decoded
    except (ValueError, TypeError, json.JSONDecodeError, binascii.Error):
        raise _dependency_unavailable() from None
    return current_key_id, keyring


class Slice4Secrets:
    """Fail-closed, domain-separated Slice 4 key material and AEAD boundary."""

    def __init__(self) -> None:
        loaded = {domain: _load_slice4_keyring(domain) for domain in _SLICE4_SECRET_DOMAINS}
        current_ids = [current_id for current_id, _ in loaded.values()]
        current_materials = [ring[current_id] for current_id, ring in loaded.values()]
        if len(set(current_ids)) != len(current_ids) or len(set(current_materials)) != len(
            current_materials
        ):
            raise _dependency_unavailable()
        self._keys = loaded

    @staticmethod
    def _uuid(value) -> str:
        if type(value) is not UUID or value.version != 7:
            raise _dependency_unavailable()
        return str(value)

    @staticmethod
    def _canonical_json(value) -> bytes:
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError):
            raise _dependency_unavailable() from None

    @staticmethod
    def _source_version(value) -> int:
        if type(value) is not int or value < 1:
            raise _dependency_unavailable()
        return value

    def profile_aad(
        self,
        *,
        tenant_public_id,
        subject_member_id,
        revision_id,
        identity_source_version,
        key_id: str,
    ) -> bytes:
        if type(key_id) is not str or not key_id:
            raise _dependency_unavailable()
        return self._canonical_json(
            {
                "domain": "slice4.profile.snapshot.v1",
                "identity_source_version": self._source_version(identity_source_version),
                "key_id": key_id,
                "profile_revision_id": self._uuid(revision_id),
                "subject_member_id": self._uuid(subject_member_id),
                "tenant_public_id": self._uuid(tenant_public_id),
            }
        )

    def assembly_fact_aad(
        self,
        *,
        tenant_public_id,
        subject_member_id,
        service_case_id,
        assembly_id,
        fact_ref,
        indicator_code,
        key_id: str,
    ) -> bytes:
        if type(key_id) is not str or not key_id or type(indicator_code) is not str or not indicator_code:
            raise _dependency_unavailable()
        return self._canonical_json(
            {
                "assembly_id": self._uuid(assembly_id),
                "domain": "slice4.assembly.fact.v1",
                "fact_ref": self._uuid(fact_ref),
                "indicator_code": indicator_code,
                "key_id": key_id,
                "service_case_id": self._uuid(service_case_id),
                "subject_member_id": self._uuid(subject_member_id),
                "tenant_public_id": self._uuid(tenant_public_id),
            }
        )

    def _encrypt(self, domain: str, value, aad: bytes) -> tuple[bytes, str]:
        key_id, keyring = self._keys[domain]
        nonce = secrets.token_bytes(12)
        plaintext = self._canonical_json(value)
        return nonce + AESGCM(keyring[key_id]).encrypt(nonce, plaintext, aad), key_id

    def _decrypt(self, domain: str, ciphertext, key_id: str, aad: bytes):
        try:
            if type(ciphertext) is not bytes or len(ciphertext) <= 28:
                raise ValueError("invalid ciphertext")
            key = self._keys[domain][1][key_id]
            plaintext = AESGCM(key).decrypt(ciphertext[:12], ciphertext[12:], aad)
            return json.loads(plaintext)
        except (KeyError, ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, InvalidTag):
            raise _dependency_unavailable() from None

    def encrypt_profile(self, value, **context) -> tuple[bytes, str]:
        key_id = self._keys["PROFILE_PHI"][0]
        return self._encrypt("PROFILE_PHI", value, self.profile_aad(key_id=key_id, **context))

    def decrypt_profile(self, ciphertext, key_id: str, **context):
        aad = self.profile_aad(key_id=key_id, **context)
        return self._decrypt("PROFILE_PHI", ciphertext, key_id, aad)

    def encrypt_assembly_fact(self, value, **context) -> tuple[bytes, str]:
        key_id = self._keys["ASSEMBLY_PHI"][0]
        return self._encrypt(
            "ASSEMBLY_PHI", value, self.assembly_fact_aad(key_id=key_id, **context)
        )

    def decrypt_assembly_fact(self, ciphertext, key_id: str, **context):
        aad = self.assembly_fact_aad(key_id=key_id, **context)
        return self._decrypt("ASSEMBLY_PHI", ciphertext, key_id, aad)

    def digest(self, domain: str, value) -> tuple[bytes, str]:
        if domain not in {
            "REQUEST_DIGEST",
            "AUDIT_DIGEST",
            "OUTBOX_DIGEST",
            "REPLAY_DIGEST",
            "DELIVERY",
            "COORDINATION",
        }:
            raise _dependency_unavailable()
        key_id, keyring = self._keys[domain]
        return hmac.new(keyring[key_id], self._canonical_json(value), hashlib.sha256).digest(), key_id


@dataclass(frozen=True, slots=True)
class ProfileMutationPlan:
    preimage: dict[str, object]
    expected_postimage: dict[str, object]


def build_profile_mutation_plan(
    *, subject_ref: UUID, profile_id: UUID, revision_id: UUID,
    actor_user_id: int, created_at: datetime, request_digest: str,
    snapshot_digest: str, preimage: dict[str, object],
) -> ProfileMutationPlan:
    if (
        any(type(value) is not UUID or value.version != 7 for value in (subject_ref, profile_id, revision_id))
        or type(actor_user_id) is not int or actor_user_id < 1
        or created_at.tzinfo is None or created_at.utcoffset() is None
        or any(type(value) is not str or len(value) != 64 for value in (request_digest, snapshot_digest))
        or type(preimage) is not dict
        or type(preimage.get("version")) is not int
    ):
        raise ValueError("PROFILE_SNAPSHOT_INVALID")
    next_version = preimage["version"] + 1
    expected = {
        "profile": {
            "profile_id": str(profile_id), "subject_ref": str(subject_ref),
            "current_revision_id": str(revision_id), "version": next_version,
        },
        "revision": {
            "revision_id": str(revision_id), "profile_id": str(profile_id),
            "revision_no": next_version, "snapshot_digest": snapshot_digest,
        },
        "audit": {"event_type": "HEALTH_PROFILE_REVISED", "actor_user_id": actor_user_id},
        "outbox": {"event_type": "HEALTH_PROFILE_REVISED", "aggregate_id": str(profile_id)},
        "receipt": {"request_digest": request_digest, "created_at": created_at.isoformat()},
    }
    return ProfileMutationPlan(preimage=dict(preimage), expected_postimage=expected)


def classify_commit_outcome(*, plan: ProfileMutationPlan, actual: dict[str, object]) -> str:
    if actual == plan.expected_postimage:
        return "COMMITTED"
    if actual == plan.preimage:
        return "NOT_COMMITTED"
    return "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ReportAttachmentPlanItem:
    file_id: UUID
    position: int


def build_report_attachment_plan(
    *, files, subject_member_id: UUID, service_case_id: UUID, tenant_id: int,
) -> tuple[ReportAttachmentPlanItem, ...]:
    if not 1 <= len(files) <= 10 or len({row["file_id"] for row in files}) != len(files):
        raise ValueError("PRIVATE_FILE_BIND_CONFLICT")
    ordered = sorted(files, key=lambda row: row["file_id"].int)
    for row in ordered:
        if (
            row.get("status") != "CLEAN"
            or row.get("purpose") != "DETECTION_REPORT"
            or row.get("subject_member_id") != subject_member_id
            or row.get("service_case_id") != service_case_id
            or row.get("tenant_id") != tenant_id
            or row.get("bound") is not False
        ):
            raise ValueError("PRIVATE_FILE_BIND_CONFLICT")
    return tuple(
        ReportAttachmentPlanItem(file_id=row["file_id"], position=index)
        for index, row in enumerate(ordered, start=1)
    )


def _uuid7(value) -> UUID:
    try:
        parsed = value if type(value) is UUID else UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise _dependency_unavailable() from None
    if parsed.version != 7:
        raise _dependency_unavailable()
    return parsed


def _stable_uuid7(timestamp: datetime, entropy: bytes, *, discriminator: int) -> UUID:
    if timestamp.utcoffset() is None or type(entropy) is not bytes or len(entropy) < 16:
        raise _dependency_unavailable()
    milliseconds = int(timestamp.timestamp() * 1000)
    if (
        milliseconds < 0
        or milliseconds >= 1 << 48
        or type(discriminator) is not int
        or not 1 <= discriminator <= 255
    ):
        raise _dependency_unavailable()
    raw = bytearray(milliseconds.to_bytes(6, "big") + entropy[:10])
    raw[6] = (raw[6] & 0x0F) | 0x70
    raw[8] = (raw[8] & 0x3F) | 0x80
    raw[-1] ^= discriminator
    return UUID(bytes=bytes(raw))


async def read_formal_health_profile(
    repository,
    identity_authority,
    *,
    actor_user_id: int,
    actor_context: str,
    subject_member_id: UUID,
    service_case_id: UUID,
    enrollment_id: UUID,
) -> HealthProfileDTO:
    row = await repository.clinical_profile(
        actor_user_id=actor_user_id,
        actor_context=actor_context,
        subject_member_id=subject_member_id,
        service_case_id=service_case_id,
        enrollment_id=enrollment_id,
    )
    if row is None:
        raise ValueError("HEALTH_PROFILE_NOT_FOUND")
    summary = await identity_authority.verified_identity_summary(
        subject_member_id=subject_member_id,
        service_case_id=service_case_id,
    )
    revision_id = _uuid7(row["revision_id"])
    tenant_public_id = _uuid7(row["tenant_public_id"])
    identity_revision_ref = _uuid7(row["identity_revision_ref"])
    if (
        summary.identity_revision_ref != identity_revision_ref
        or summary.source_version != row["identity_source_version"]
        or summary.tenant_public_id != tenant_public_id
        or summary.evidence_status != "VERIFIED"
    ):
        raise RuntimeError("ACTOR_CURRENTNESS_FORBIDDEN")
    snapshot = Slice4Secrets().decrypt_profile(
        bytes(row["snapshot_ciphertext"]),
        row["snapshot_key_id"],
        tenant_public_id=tenant_public_id,
        subject_member_id=subject_member_id,
        revision_id=revision_id,
        identity_source_version=row["identity_source_version"],
    )
    if type(snapshot) is not dict:
        raise _dependency_unavailable()
    public_snapshot = {
        key: snapshot[key]
        for key in (
            "medical_history",
            "allergies",
            "medications",
            "symptoms",
            "pregnancy_status",
            "pregnancy_week",
            "lactation_status",
        )
        if key in snapshot
    }
    metric_rows = await repository.latest_profile_metrics(
        actor_user_id=actor_user_id,
        actor_context=actor_context,
        subject_member_id=subject_member_id,
        service_case_id=service_case_id,
        enrollment_id=enrollment_id,
    )
    metrics = {}
    for metric in metric_rows:
        code = metric.get("indicator_code")
        if code in {"height", "weight", "waist"} and code not in metrics:
            metrics[code] = Decimal(str(metric["numeric_value"]))
    bmi = None
    if "height" in metrics and "weight" in metrics:
        bmi = compute_bmi(height_cm=metrics["height"], weight_kg=metrics["weight"])
    return HealthProfileDTO(
        subject_ref=subject_member_id,
        profile_id=_uuid7(row["profile_public_id"]),
        revision_id=revision_id,
        revision_no=row["revision_no"],
        version=row["version"],
        identity_summary=HealthIdentitySummaryDTO(
            gender=summary.gender,
            birth_date=summary.birth_date,
            identity_revision_ref=identity_revision_ref,
            source_version=summary.source_version,
            tenant_public_id=tenant_public_id,
            evidence_status=summary.evidence_status,
        ),
        reconfirmed_at=row["reconfirmed_at"],
        height_cm=metrics.get("height"),
        weight_kg=metrics.get("weight"),
        waist_cm=metrics.get("waist"),
        bmi=bmi,
        updated_at=row["updated_at"],
        **public_snapshot,
    )


async def create_formal_profile_root(
    writer_repository,
    identity_authority,
    *,
    actor_user_id: int,
    actor_context: str,
    subject_member_id: UUID,
    service_case_id: UUID,
    enrollment_id: UUID,
    payload,
    idempotency_key: str,
) -> dict[str, object]:
    if payload.source_type != "APP" or actor_context not in {"SELF", "PROXY_DAILY_INPUT"}:
        raise ValueError("PROFILE_SNAPSHOT_INVALID")
    subject = await writer_repository.subject_authority(
        actor_user_id=actor_user_id,
        actor_context=actor_context,
        enrollment_id=enrollment_id,
        service_case_id=service_case_id,
    )
    if subject is None or UUID(str(subject["subject_member_id"])) != subject_member_id:
        raise RuntimeError("ACTOR_CURRENTNESS_FORBIDDEN")
    summary = await identity_authority.verified_identity_summary(
        subject_member_id=subject_member_id,
        service_case_id=service_case_id,
    )
    if summary.evidence_status != "VERIFIED":
        raise RuntimeError("ACTOR_CURRENTNESS_FORBIDDEN")
    await writer_repository.require_identity_summary_current(
        subject_member_id=subject_member_id,
        service_case_id=service_case_id,
        identity_revision_ref=summary.identity_revision_ref,
        identity_source_version=summary.source_version,
        tenant_public_id=summary.tenant_public_id,
    )
    snapshot = payload.model_dump(
        mode="json", exclude={"expected_version", "source_type", "reconfirmed_at"}
    )
    secrets_boundary = Slice4Secrets()
    request_value = {
        "actor_context": actor_context,
        "subject_ref": str(subject_member_id),
        "service_case_id": str(service_case_id),
        "enrollment_id": str(enrollment_id),
        "payload": payload.model_dump(mode="json"),
    }
    request_digest, _ = secrets_boundary.digest("REQUEST_DIGEST", request_value)
    coordination, _ = secrets_boundary.digest(
        "COORDINATION",
        {"request_digest": request_digest.hex(), "idempotency_key": idempotency_key},
    )
    preimage = await writer_repository.profile_preimage(subject_member_id)
    if preimage is None:
        if payload.expected_version:
            raise ValueError("VERSION_CONFLICT")
        profile_id = _stable_uuid7(payload.reconfirmed_at, coordination, discriminator=1)
    else:
        profile_id = _uuid7(preimage["profile_public_id"])
    revision_id = _stable_uuid7(payload.reconfirmed_at, coordination, discriminator=2)
    ciphertext, encryption_key_id = secrets_boundary.encrypt_profile(
        snapshot,
        tenant_public_id=summary.tenant_public_id,
        subject_member_id=subject_member_id,
        revision_id=revision_id,
        identity_source_version=summary.source_version,
    )
    snapshot_digest, digest_key_id = secrets_boundary.digest(
        "AUDIT_DIGEST",
        {"subject_ref": str(subject_member_id), "revision_id": str(revision_id), "snapshot": snapshot},
    )
    expected_digest, _ = secrets_boundary.digest(
        "REPLAY_DIGEST",
        {
            "operation": "PUT_HEALTH_PROFILE",
            "profile_id": str(profile_id),
            "revision_id": str(revision_id),
            "subject_ref": str(subject_member_id),
            "version": payload.expected_version + 1,
        },
    )
    result = await writer_repository.create_profile_root(
        actor_user_id=actor_user_id,
        actor_context="SELF" if actor_context == "SELF" else "PROXY",
        subject_member_id=subject_member_id,
        service_case_id=service_case_id,
        enrollment_id=enrollment_id,
        profile_public_id=profile_id,
        profile_revision_id=revision_id,
        tenant_public_id=summary.tenant_public_id,
        identity_revision_ref=summary.identity_revision_ref,
        identity_source_version=summary.source_version,
        snapshot_ciphertext=ciphertext,
        snapshot_key_id=encryption_key_id,
        snapshot_digest=snapshot_digest,
        digest_key_id=digest_key_id,
        reconfirmed_at=payload.reconfirmed_at,
        source_type=payload.source_type,
        changed_fields=sorted(snapshot),
        expected_version=payload.expected_version,
        idempotency_key=uuid5(
            NAMESPACE_URL,
            f"slice4/profile/{subject_member_id}/{idempotency_key}",
        ),
        request_digest=request_digest,
        expected_postimage_digest=expected_digest,
    )
    if (
        UUID(str(result["subject_member_id"])) != subject_member_id
        or result["version"] != payload.expected_version + 1
    ):
        raise RuntimeError("COMMIT_OUTCOME_UNKNOWN")
    return result


async def create_formal_detection_report(
    repository,
    *,
    actor_user_id: int,
    actor_context: str,
    subject_member_id: UUID,
    service_case_id: UUID,
    enrollment_id: UUID,
    payload,
    idempotency_key: str,
) -> dict[str, object]:
    if payload.source_type != "APP" or actor_context not in {"SELF", "PROXY_REPORT_UPLOAD"}:
        raise ValueError("INVALID_REQUEST")
    secrets_boundary = Slice4Secrets()
    request_value = {
        "actor_context": actor_context,
        "subject_ref": str(subject_member_id),
        "service_case_id": str(service_case_id),
        "enrollment_id": str(enrollment_id),
        "payload": payload.model_dump(mode="json"),
    }
    request_digest, _ = secrets_boundary.digest("REQUEST_DIGEST", request_value)
    coordination, _ = secrets_boundary.digest(
        "COORDINATION",
        {"request_digest": request_digest.hex(), "idempotency_key": idempotency_key},
    )
    report_id = _stable_uuid7(payload.measured_at, coordination, discriminator=3)
    expected_postimage_digest, _ = secrets_boundary.digest(
        "REPLAY_DIGEST",
        {
            "operation": "CREATE_DETECTION_REPORT",
            "report_id": str(report_id),
            "subject_ref": str(subject_member_id),
            "file_ids": sorted(str(value) for value in payload.file_ids),
            "version": 1,
        },
    )
    return await repository.create_detection_report(
        actor_user_id=actor_user_id,
        actor_context="SELF" if actor_context == "SELF" else "PROXY",
        subject_member_id=subject_member_id,
        service_case_id=service_case_id,
        enrollment_id=enrollment_id,
        requested_report_id=report_id,
        report_type=payload.report_type,
        measured_at=payload.measured_at,
        source_type=payload.source_type,
        private_file_ids=sorted(payload.file_ids),
        idempotency_key=uuid5(
            NAMESPACE_URL,
            f"slice4/report/{subject_member_id}/{idempotency_key}",
        ),
        request_digest=request_digest,
        expected_postimage_digest=expected_postimage_digest,
    )


def _health_fact_keyring() -> HealthFactDigestKeyring:
    settings = get_settings()
    return HealthFactDigestKeyring.from_json(
        current_key_id=settings.health_fact_digest_current_key_id,
        keyring_json=settings.health_fact_digest_keyring_json,
    )


def _formal_fact_dto(fact, state: str) -> HealthFactDTO:
    if fact.fact_ref is None or fact.received_at is None:
        raise _dependency_unavailable()
    return HealthFactDTO(
        fact_ref=_uuid7(fact.fact_ref),
        indicator_code=fact.indicator_code,
        value=fact.numeric_value,
        unit=fact.unit,
        measured_at=fact.measured_at,
        received_at=fact.received_at,
        source=fact.source_type,
        verification_state=state,
    )


async def create_formal_health_facts(
    repository,
    *,
    actor_user_id: int,
    actor_context: str,
    subject_member_id: UUID,
    subject_user_id: int | None,
    service_case_id: UUID,
    enrollment_id: UUID,
    payload,
    idempotency_key: str,
) -> HealthFactBatchDTO:
    if actor_context in {"SELF", "PROXY_DAILY_INPUT"}:
        if any(item.source_type != "APP" for item in payload.items):
            raise ValueError("HEALTH_FACT_SOURCE_FORBIDDEN")
    elif actor_context == "THERAPIST":
        if any(item.source_type not in {"REPORT", "STORE"} for item in payload.items):
            raise ValueError("HEALTH_FACT_SOURCE_FORBIDDEN")
    else:
        raise RuntimeError("ACTOR_CURRENTNESS_FORBIDDEN")

    secrets_boundary = Slice4Secrets()
    request_value = {
        "actor_context": actor_context,
        "subject_ref": str(subject_member_id),
        "service_case_id": str(service_case_id),
        "enrollment_id": str(enrollment_id),
        "items": payload.model_dump(mode="json")["items"],
    }
    request_digest, _ = secrets_boundary.digest("REQUEST_DIGEST", request_value)
    coordination, _ = secrets_boundary.digest(
        "COORDINATION",
        {"request_digest": request_digest.hex(), "idempotency_key": idempotency_key},
    )
    keyring = _health_fact_keyring()
    results: list[HealthFactDTO] = []
    for index, item in enumerate(sorted(payload.items, key=lambda value: value.indicator_code), 1):
        if item.source_type == "REPORT":
            if not await repository.require_report_scope(
                report_id=item.report_id,
                subject_member_id=subject_member_id,
                service_case_id=service_case_id,
            ):
                raise ValueError("INVALID_REQUEST")
        producer_digest, _ = secrets_boundary.digest(
            "COORDINATION",
            {
                "idempotency_key": idempotency_key,
                "indicator_code": item.indicator_code,
                "subject_ref": str(subject_member_id),
            },
        )
        producer_event_key = producer_digest.hex()
        fact_ref = _stable_uuid7(item.measured_at, coordination, discriminator=10 + index)
        draft = CanonicalHealthFactDraft(
            subject_user_id=subject_user_id,
            subject_member_id=subject_member_id,
            fact_ref=fact_ref,
            report_id=item.report_id,
            measurement_context=item.measurement_context,
            indicator_code=item.indicator_code,
            numeric_value=item.value,
            unit=item.unit,
            measured_at=item.measured_at,
            source_type=item.source_type,
            source_identity=f"slice4:{actor_context}:{subject_member_id}",
            producer_event_key=producer_event_key,
            created_by=actor_user_id,
            catalog_version=2,
        )
        await repository.acquire_health_fact_lock(
            semantic_lock_key(
                source_type=item.source_type, producer_event_key=producer_event_key
            )
        )
        existing = await repository.find_health_fact(
            source_type=item.source_type,
            producer_event_key=producer_event_key,
            source_identity_digests=tuple(
                source_identity_digests(
                    source_identity=draft.source_identity, keyring=keyring
                ).values()
            ),
        )
        if existing is not None:
            if not verify_payload(stored=existing, draft=draft, keyring=keyring):
                raise ValueError("INVALID_REQUEST") from None
            stored = existing
        else:
            stored = await repository.add_health_fact(prepare_fact(draft, keyring))

        state = initial_verification_state(item.source_type)
        event_digest, _ = secrets_boundary.digest(
            "AUDIT_DIGEST",
            {
                "fact_ref": str(fact_ref),
                "indicator_code": item.indicator_code,
                "state": state,
                "payload_digest": stored.payload_digest,
            },
        )
        transition = await repository.health_fact_state_transition(
            fact_ref=fact_ref,
            target_state=state,
            expected_state="NONE",
            actor_user_id=actor_user_id,
            service_case_id=service_case_id,
            expected_version=0,
            reason_code="FACT_CREATED",
            event_digest=event_digest.hex(),
        )
        results.append(_formal_fact_dto(stored, transition["state"]))
    return HealthFactBatchDTO(items=results)


def _formal_fact_row_dto(row: dict, *, state: str | None = None) -> HealthFactDTO:
    return HealthFactDTO(
        fact_ref=_uuid7(row["fact_ref"]),
        indicator_code=row["indicator_code"],
        value=row["numeric_value"],
        unit=row["unit"],
        measured_at=row["measured_at"],
        received_at=row["received_at"],
        source=row["source_type"],
        verification_state=state or row["verification_state"],
    )


async def read_formal_health_fact(
    repository,
    *,
    actor_user_id: int,
    actor_context: str,
    subject_member_id: UUID,
    service_case_id: UUID,
    enrollment_id: UUID | None,
    fact_ref: UUID,
) -> tuple[dict, HealthFactDTO]:
    rows = await repository.clinical_facts(
        actor_user_id=actor_user_id,
        actor_context=actor_context,
        subject_member_id=subject_member_id,
        service_case_id=service_case_id,
        enrollment_id=enrollment_id,
        page={"fact_ref": str(fact_ref), "limit": 2},
    )
    if len(rows) != 1:
        raise ValueError("HEALTH_FACT_NOT_FOUND")
    return rows[0], _formal_fact_row_dto(rows[0])


async def transition_formal_health_fact_state(
    repository,
    *,
    actor_user_id: int,
    service_case_id: UUID,
    fact_row: dict,
    target_state: str,
    expected_version: int,
    reason_code: str,
) -> HealthFactDTO:
    current_version = fact_row.get("event_no")
    if current_version != expected_version:
        raise ValueError("VERSION_CONFLICT")
    event_digest, _ = Slice4Secrets().digest(
        "AUDIT_DIGEST",
        {
            "fact_ref": str(fact_row["fact_ref"]),
            "from_state": fact_row["verification_state"],
            "to_state": target_state,
            "expected_version": expected_version,
            "reason_code": reason_code,
        },
    )
    result = await repository.health_fact_state_transition(
        fact_ref=fact_row["fact_ref"],
        target_state=target_state,
        expected_state=fact_row["verification_state"],
        actor_user_id=actor_user_id,
        service_case_id=service_case_id,
        expected_version=expected_version,
        reason_code=reason_code,
        event_digest=event_digest.hex(),
    )
    return _formal_fact_row_dto(fact_row, state=result["state"])


async def correct_formal_health_fact(
    repository,
    *,
    actor_user_id: int,
    actor_context: str,
    subject_member_id: UUID,
    subject_user_id: int | None,
    service_case_id: UUID,
    enrollment_id: UUID | None,
    fact_row: dict,
    payload,
    idempotency_key: str,
) -> HealthFactDTO:
    if fact_row["verification_state"] != "DISPUTED":
        raise ValueError("STATE_CONFLICT")
    predecessor = await repository.health_fact_by_ref(fact_row["fact_ref"])
    if predecessor is None or predecessor.id is None:
        raise ValueError("HEALTH_FACT_NOT_FOUND")
    if payload.indicator_code != predecessor.indicator_code:
        raise ValueError("HEALTH_FACT_CORRECTION_CONFLICT")
    secrets_boundary = Slice4Secrets()
    request_digest, _ = secrets_boundary.digest(
        "REQUEST_DIGEST",
        {
            "actor_context": actor_context,
            "subject_ref": str(subject_member_id),
            "service_case_id": str(service_case_id),
            "enrollment_id": str(enrollment_id) if enrollment_id else None,
            "predecessor": str(fact_row["fact_ref"]),
            "payload": payload.model_dump(mode="json"),
        },
    )
    coordination, _ = secrets_boundary.digest(
        "COORDINATION",
        {"request_digest": request_digest.hex(), "idempotency_key": idempotency_key},
    )
    fact_ref = _stable_uuid7(payload.measured_at, coordination, discriminator=31)
    producer_event_key = coordination.hex()
    draft = CanonicalHealthFactDraft(
        subject_user_id=subject_user_id,
        subject_member_id=subject_member_id,
        fact_ref=fact_ref,
        report_id=predecessor.report_id,
        measurement_context=payload.measurement_context,
        indicator_code=payload.indicator_code,
        numeric_value=payload.value,
        unit=payload.unit,
        measured_at=payload.measured_at,
        source_type=predecessor.source_type,
        source_identity=f"slice4:{actor_context}:{subject_member_id}:correction",
        producer_event_key=producer_event_key,
        supersedes_fact_id=predecessor.id,
        correction_reason_code=payload.reason_code,
        created_by=actor_user_id,
        catalog_version=2,
    )
    keyring = _health_fact_keyring()
    await repository.acquire_health_fact_lock(
        semantic_lock_key(source_type=draft.source_type, producer_event_key=producer_event_key)
    )
    existing = await repository.find_health_fact(
        source_type=draft.source_type,
        producer_event_key=producer_event_key,
        source_identity_digests=tuple(
            source_identity_digests(
                source_identity=draft.source_identity, keyring=keyring
            ).values()
        ),
    )
    if existing is not None:
        if not verify_payload(stored=existing, draft=draft, keyring=keyring):
            raise ValueError("INVALID_REQUEST")
        stored = existing
    else:
        stored = await repository.add_health_fact(prepare_fact(draft, keyring))
    event_digest, _ = secrets_boundary.digest(
        "AUDIT_DIGEST",
        {
            "fact_ref": str(fact_ref),
            "supersedes_fact_ref": str(fact_row["fact_ref"]),
            "state": "SELF_REPORTED",
            "payload_digest": stored.payload_digest,
        },
    )
    transition = await repository.health_fact_state_transition(
        fact_ref=fact_ref,
        target_state="SELF_REPORTED",
        expected_state="NONE",
        actor_user_id=actor_user_id,
        service_case_id=service_case_id,
        expected_version=0,
        reason_code="FACT_CREATED",
        event_digest=event_digest.hex(),
    )
    return _formal_fact_dto(stored, transition["state"])


def _report_dto(row: dict, *, detail: bool):
    values = {
        "report_id": _uuid7(row["report_id"]),
        "subject_ref": _uuid7(row["subject_ref"]),
        "report_type": row["report_type"],
        "measured_at": row["measured_at"],
        "received_at": row["received_at"],
        "status": row["status"],
        "source": row["source_type"],
        "attachment_count": row["attachment_count"],
        "structured_indicator_codes": (),
        "supersedes_report_id": (
            _uuid7(row["supersedes_report_id"])
            if row.get("supersedes_report_id") is not None
            else None
        ),
        "version": row["version"],
        "created_at": row["created_at"],
    }
    if not detail:
        return DetectionReportDTO(**values)
    attachments = tuple(
        DetectionReportAttachmentDTO(
            file_id=_uuid7(item["file_id"]),
            mime_type=item["mime_type"],
            size=item["size"],
            status=item["status"],
        )
        for item in row.get("attachments", ())
    )
    if len(attachments) != row["attachment_count"]:
        raise _dependency_unavailable()
    return DetectionReportDetailDTO(**values, attachments=attachments)


async def read_formal_detection_reports(
    repository,
    *,
    actor_user_id: int,
    actor_context: str,
    subject_member_id: UUID,
    service_case_id: UUID,
    enrollment_id: UUID,
    limit: int,
    cursor: str | None,
) -> DetectionReportPageDTO:
    page: dict[str, object] = {"limit": limit + 1}
    if cursor is not None:
        try:
            decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if set(decoded) != {"measured_at", "report_id"}:
                raise ValueError
            measured_at = datetime.fromisoformat(decoded["measured_at"])
            report_id = _uuid7(decoded["report_id"])
            if measured_at.utcoffset() is None:
                raise ValueError
        except Exception:
            raise ValueError("INVALID_REQUEST") from None
        page.update(cursor_measured_at=measured_at.isoformat(), cursor_report_id=str(report_id))
    rows = await repository.clinical_reports(
        actor_user_id=actor_user_id,
        actor_context=actor_context,
        subject_member_id=subject_member_id,
        service_case_id=service_case_id,
        enrollment_id=enrollment_id,
        page=page,
    )
    items = tuple(_report_dto(row, detail=False) for row in rows[:limit])
    next_cursor = None
    if len(rows) > limit:
        marker = rows[limit - 1]
        raw = json.dumps(
            {"measured_at": marker["measured_at"].isoformat(), "report_id": str(marker["report_id"])},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        next_cursor = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return DetectionReportPageDTO(items=list(items), next_cursor=next_cursor)


async def read_formal_detection_report(
    repository,
    *,
    actor_user_id: int,
    actor_context: str,
    subject_member_id: UUID,
    service_case_id: UUID,
    enrollment_id: UUID,
    report_id: UUID,
) -> DetectionReportDetailDTO:
    rows = await repository.clinical_reports(
        actor_user_id=actor_user_id,
        actor_context=actor_context,
        subject_member_id=subject_member_id,
        service_case_id=service_case_id,
        enrollment_id=enrollment_id,
        page={"limit": 2, "report_id": str(report_id)},
    )
    if len(rows) != 1:
        raise ValueError("DETECTION_REPORT_NOT_FOUND")
    return _report_dto(rows[0], detail=True)


def _member_profile_gender(value: str) -> str:
    normalized = {"M": "male", "F": "female"}.get(value, value)
    if normalized not in {"male", "female"}:
        raise HTTPException(status_code=409, detail="Health profile is unavailable for current member")
    return normalized


def _member_profile_state(profile) -> str:
    if profile is None:
        return "NOT_CREATED"
    if profile.height is None or profile.weight is None:
        return "INCOMPLETE"
    return "COMPLETE"


def _member_profile_bmi(profile) -> Decimal | None:
    if profile is None or profile.height is None or profile.weight is None:
        return None
    height_m = Decimal(profile.height) / Decimal("100")
    if height_m <= 0:
        return None
    return (Decimal(profile.weight) / (height_m * height_m)).quantize(
        Decimal("0.1"),
        rounding=ROUND_HALF_UP,
    )


def _to_member_self_profile_result(profile, *, outcome=None) -> MemberSelfHealthProfileResult:
    if profile is None:
        return MemberSelfHealthProfileResult(
            state="NOT_CREATED",
            version=None,
            profile=None,
            bmi=None,
            outcome=outcome,
        )
    return MemberSelfHealthProfileResult(
        state=_member_profile_state(profile),
        version=profile.updated_at,
        profile=MemberSelfHealthProfileData(
            gender=_member_profile_gender(profile.gender),
            birth_date=profile.birth_date,
            height=profile.height,
            weight=profile.weight,
            blood_type=profile.blood_type,
        ),
        bmi=_member_profile_bmi(profile),
        outcome=outcome,
    )


def _ensure_current_verified_member(user) -> None:
    if (
        user is None
        or user.role != "member"
        or user.status != "active"
        or user.verify_status != "verified"
    ):
        raise HTTPException(
            status_code=409,
            detail="Health profile is unavailable for current member",
        )


def ensure_current_health_data_member(user) -> None:
    if (
        user is None
        or user.role != "member"
        or user.status != "active"
        or user.verify_status != "verified"
    ):
        raise HTTPException(status_code=409, detail="HEALTH_DATA_UNAVAILABLE")


def _indicator_item(row) -> MemberSelfHealthIndicatorItem:
    return MemberSelfHealthIndicatorItem(
        id=row.id,
        batch_id=row.batch_id,
        indicator_type=row.indicator_type,
        value=row.value,
        unit=row.unit,
        source=row.source,
        recorded_at=row.recorded_at,
    )


def _encode_indicator_cursor(row) -> str:
    raw = json.dumps(
        {"recorded_at": row.recorded_at.isoformat(), "id": row.id},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_indicator_cursor(cursor: str | None):
    if cursor is None:
        return None, None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        recorded_at = datetime.fromisoformat(payload["recorded_at"])
        row_id = int(payload["id"])
        if recorded_at.tzinfo is None or row_id < 1:
            raise ValueError
        return recorded_at, row_id
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise HTTPException(status_code=422, detail="HEALTH_INDICATOR_CURSOR_INVALID") from None


async def list_member_self_health_indicators(
    session,
    *,
    user_id: int,
    indicator_type: str | None,
    start_at,
    end_at,
    limit: int,
    cursor: str | None,
) -> MemberSelfHealthIndicatorPage:
    if start_at is not None and end_at is not None and start_at > end_at:
        raise HTTPException(status_code=422, detail="HEALTH_INDICATOR_TIME_RANGE_INVALID")
    cursor_recorded_at, cursor_id = _decode_indicator_cursor(cursor)
    try:
        user = await get_member_profile_user_state(session, user_id)
        ensure_current_health_data_member(user)
        rows = await list_member_health_indicator_history(
            session,
            user_id=user_id,
            indicator_type=indicator_type,
            start_at=start_at,
            end_at=end_at,
            cursor_recorded_at=cursor_recorded_at,
            cursor_id=cursor_id,
            limit=limit + 1,
        )
    except asyncio.CancelledError:
        raise
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="HEALTH_DATA_UNAVAILABLE") from None
    visible = rows[:limit]
    return MemberSelfHealthIndicatorPage(
        state="AVAILABLE" if visible else "EMPTY",
        items=[_indicator_item(row) for row in visible],
        next_cursor=_encode_indicator_cursor(visible[-1]) if len(rows) > limit else None,
    )


async def get_member_self_latest_health_indicators(
    session,
    *,
    user_id: int,
) -> MemberSelfHealthIndicatorLatest:
    try:
        user = await get_member_profile_user_state(session, user_id)
        ensure_current_health_data_member(user)
        rows = await list_member_latest_health_indicators(session, user_id=user_id)
    except asyncio.CancelledError:
        raise
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="HEALTH_DATA_UNAVAILABLE") from None
    return MemberSelfHealthIndicatorLatest(
        state="AVAILABLE" if rows else "EMPTY",
        items=[_indicator_item(row) for row in rows],
    )


def _ensure_current_detection_report_member(user) -> None:
    if (
        user is None
        or user.role != "member"
        or user.status != "active"
        or user.verify_status != "verified"
    ):
        raise HTTPException(status_code=403, detail="MEMBER_DETECTION_REPORT_ACCESS_DENIED")


def _detection_report_list_item(row) -> MemberSelfDetectionReportListItem:
    if row.report_schema_version != 1:
        raise HTTPException(status_code=409, detail="DETECTION_REPORT_CONTENT_INCONSISTENT")
    return MemberSelfDetectionReportListItem(
        report_id=row.id,
        report_type=row.report_type,
        detection_time=row.detection_time,
        view_status=row.view_status,
        summary=row.summary,
        is_initial_baseline=row.is_initial_baseline,
    )


def _detection_report_cursor_key() -> bytes:
    return get_settings().jwt_secret_key.encode("utf-8")


def _encode_detection_report_cursor(row) -> str:
    raw = json.dumps(
        {"detection_time": row.detection_time.isoformat(), "id": row.id},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    payload = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    signature = hmac.new(
        _detection_report_cursor_key(),
        b"member-detection-report-cursor:v1:" + raw,
        hashlib.sha256,
    ).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{payload}.{encoded_signature}"


def _decode_detection_report_cursor(cursor: str | None):
    if cursor is None:
        return None, None
    try:
        encoded_payload, encoded_signature = cursor.split(".")
        raw = base64.b64decode(
            (encoded_payload + "=" * (-len(encoded_payload) % 4)).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        signature = base64.b64decode(
            (encoded_signature + "=" * (-len(encoded_signature) % 4)).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        expected_signature = hmac.new(
            _detection_report_cursor_key(),
            b"member-detection-report-cursor:v1:" + raw,
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError
        payload = json.loads(raw)
        if set(payload) != {"detection_time", "id"}:
            raise ValueError
        detection_time = datetime.fromisoformat(payload["detection_time"])
        row_id = int(payload["id"])
        if detection_time.tzinfo is None or detection_time.utcoffset() is None or row_id < 1:
            raise ValueError
        return detection_time, row_id
    except (binascii.Error, KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise HTTPException(status_code=422, detail="DETECTION_REPORT_QUERY_INVALID") from None


async def list_member_self_detection_reports_service(
    session,
    *,
    user_id: int,
    report_type: str | None,
    start_at,
    end_at,
    limit: int,
    cursor: str | None,
) -> MemberSelfDetectionReportPage:
    for boundary in (start_at, end_at):
        if boundary is not None and (boundary.tzinfo is None or boundary.utcoffset() is None):
            raise HTTPException(status_code=422, detail="DETECTION_REPORT_QUERY_INVALID")
    if start_at is not None and end_at is not None and start_at > end_at:
        raise HTTPException(status_code=422, detail="DETECTION_REPORT_QUERY_INVALID")
    cursor_time, cursor_id = _decode_detection_report_cursor(cursor)
    try:
        user = await get_member_profile_user_state(session, user_id)
        _ensure_current_detection_report_member(user)
        rows = await list_member_detection_reports(
            session,
            user_id=user_id,
            report_type=report_type,
            start_at=start_at,
            end_at=end_at,
            cursor_detection_time=cursor_time,
            cursor_id=cursor_id,
            limit=limit + 1,
        )
        visible = rows[:limit]
        items = [_detection_report_list_item(row) for row in visible]
    except asyncio.CancelledError:
        raise
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="DETECTION_REPORT_UNAVAILABLE") from None
    return MemberSelfDetectionReportPage(
        state="AVAILABLE" if items else "EMPTY",
        items=items,
        next_cursor=_encode_detection_report_cursor(visible[-1]) if len(rows) > limit else None,
    )


async def get_member_self_detection_report_service(
    session,
    *,
    user_id: int,
    report_id: int,
) -> MemberSelfDetectionReportDetail:
    try:
        user = await get_member_profile_user_state(session, user_id)
        _ensure_current_detection_report_member(user)
        row = await get_member_detection_report(session, user_id=user_id, report_id=report_id)
        if row is None:
            raise HTTPException(status_code=404, detail="DETECTION_REPORT_NOT_FOUND")
        item = _detection_report_list_item(row)
        stored = DetectionReportStoredData.model_validate(row.report_data)
    except asyncio.CancelledError:
        raise
    except HTTPException:
        raise
    except ValidationError:
        raise HTTPException(status_code=409, detail="DETECTION_REPORT_CONTENT_INCONSISTENT") from None
    except Exception:
        raise HTTPException(status_code=503, detail="DETECTION_REPORT_UNAVAILABLE") from None
    return MemberSelfDetectionReportDetail(
        **item.model_dump(),
        metrics=stored.metrics,
    )


def _member_profile_matches(profile, payload: MemberSelfHealthProfileWriteRequest) -> bool:
    return bool(
        profile is not None
        and _member_profile_gender(profile.gender) == payload.gender
        and profile.birth_date == payload.birth_date
        and profile.height is not None
        and profile.weight is not None
        and Decimal(profile.height) == payload.height
        and Decimal(profile.weight) == payload.weight
        and profile.blood_type == payload.blood_type
    )


def _profile_write_data(payload: MemberSelfHealthProfileWriteRequest) -> dict:
    return {
        "gender": {"male": "M", "female": "F"}[payload.gender],
        "birth_date": payload.birth_date,
        "height": payload.height,
        "weight": payload.weight,
        "blood_type": payload.blood_type,
    }


def _known_profile_create_conflict(exc: IntegrityError) -> bool:
    original = exc.orig
    driver_error = getattr(original, "__cause__", None)
    sqlstate = getattr(original, "sqlstate", None) or getattr(
        driver_error,
        "sqlstate",
        None,
    )
    constraint_name = getattr(original, "constraint_name", None) or getattr(
        driver_error,
        "constraint_name",
        None,
    )
    return (
        sqlstate == "23505"
        and constraint_name == "uq_health_profile_user_id"
    )


async def _rollback(session) -> None:
    try:
        await session.rollback()
    except Exception:
        return


async def _read_fresh_member_profile(
    confirmation_session_factory_provider,
    *,
    user_id: int,
):
    session_factory = confirmation_session_factory_provider()
    async with session_factory() as confirmation_session:
        user = await get_member_profile_user_state(confirmation_session, user_id)
        _ensure_current_verified_member(user)
        return await get_health_profile_by_user_id(confirmation_session, user_id)


async def _confirm_profile_write(
    confirmation_session_factory_provider,
    *,
    user_id: int,
    payload: MemberSelfHealthProfileWriteRequest,
    mismatch_detail: str,
) -> MemberSelfHealthProfileResult:
    profile = await _read_fresh_member_profile(
        confirmation_session_factory_provider,
        user_id=user_id,
    )
    if _member_profile_matches(profile, payload):
        return _to_member_self_profile_result(profile, outcome="REPLAYED")
    raise HTTPException(status_code=409 if "CONFLICT" in mismatch_detail else 503, detail=mismatch_detail)


async def get_member_self_health_profile(
    session,
    *,
    user_id: int,
) -> MemberSelfHealthProfileResult:
    user = await get_member_profile_user_state(session, user_id)
    _ensure_current_verified_member(user)
    profile = await get_health_profile_by_user_id(session, user_id)
    return _to_member_self_profile_result(profile)


async def put_member_self_health_profile(
    session,
    *,
    confirmation_session_factory_provider,
    user_id: int,
    payload: MemberSelfHealthProfileWriteRequest,
) -> MemberSelfHealthProfileResult:
    user = await get_member_profile_user_state(session, user_id)
    _ensure_current_verified_member(user)
    profile = await get_health_profile_by_user_id(session, user_id)

    if profile is not None and _member_profile_matches(profile, payload):
        return _to_member_self_profile_result(profile, outcome="REPLAYED")

    if profile is not None:
        if payload.expected_version is None or profile.updated_at != payload.expected_version:
            raise HTTPException(status_code=409, detail="HEALTH_PROFILE_VERSION_CONFLICT")
        profile = await update_health_profile_record(
            session,
            user_id=user_id,
            expected_updated_at=payload.expected_version,
            profile_data=_profile_write_data(payload),
            updated_at=datetime.now(timezone.utc),
        )
        if profile is None:
            await _rollback(session)
            return await _confirm_profile_write(
                confirmation_session_factory_provider,
                user_id=user_id,
                payload=payload,
                mismatch_detail="HEALTH_PROFILE_VERSION_CONFLICT",
            )
        outcome = "UPDATED"
    else:
        if payload.expected_version is not None:
            raise HTTPException(status_code=409, detail="HEALTH_PROFILE_VERSION_CONFLICT")
        try:
            profile = await create_health_profile_record(
                session,
                profile_data={
                    "user_id": user_id,
                    **_profile_write_data(payload),
                    "updated_at": datetime.now(timezone.utc),
                },
            )
        except asyncio.CancelledError:
            await _rollback(session)
            raise
        except IntegrityError as exc:
            known_conflict = _known_profile_create_conflict(exc)
            await _rollback(session)
            if known_conflict:
                return await _confirm_profile_write(
                    confirmation_session_factory_provider,
                    user_id=user_id,
                    payload=payload,
                    mismatch_detail="HEALTH_PROFILE_VERSION_CONFLICT",
                )
            unknown_integrity = True
        except Exception:
            await _rollback(session)
            raise
        else:
            unknown_integrity = False
        if unknown_integrity:
            raise HTTPException(
                status_code=503,
                detail="Health profile persistence is unavailable",
            ) from None
        outcome = "CREATED"

    try:
        await session.commit()
    except asyncio.CancelledError:
        await _rollback(session)
        raise
    except Exception:
        await _rollback(session)
        commit_unknown = True
    else:
        commit_unknown = False
    if commit_unknown:
        return await _confirm_profile_write(
            confirmation_session_factory_provider,
            user_id=user_id,
            payload=payload,
            mismatch_detail="HEALTH_PROFILE_OUTCOME_UNKNOWN",
        )
    return _to_member_self_profile_result(profile, outcome=outcome)


def _to_health_profile_response(profile) -> HealthProfileResponse:
    return HealthProfileResponse(
        id=profile.id,
        user_id=profile.user_id,
        gender=profile.gender,
        birth_date=profile.birth_date,
        height=profile.height,
        weight=profile.weight,
        blood_type=profile.blood_type,
        medical_history=profile.medical_history,
        allergy_history=profile.allergy_history,
        family_history=profile.family_history,
        smoking=profile.smoking,
        drinking=profile.drinking,
        symptoms=profile.symptoms,
        sleep_quality=profile.sleep_quality,
        bowel_urination=profile.bowel_urination,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _to_health_indicator_response(indicator) -> HealthIndicatorResponse:
    return HealthIndicatorResponse(
        id=indicator.id,
        batch_id=indicator.batch_id,
        indicator_type=indicator.indicator_type,
        value=indicator.value,
        unit=indicator.unit,
        source=indicator.source,
        recorded_at=indicator.recorded_at,
        created_at=indicator.created_at,
    )


async def create_health_profile(
    session,
    *,
    user_id: int,
    payload: HealthProfileCreateRequest,
) -> HealthProfileResponse:
    user = await get_user_by_id(session, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    existing_profile = await get_health_profile_by_user_id(session, user_id)
    if existing_profile is not None:
        raise HTTPException(status_code=409, detail="Health profile already exists")

    try:
        profile_data = payload.model_dump()
        profile_data["user_id"] = user_id
        profile = await create_health_profile_record(session, profile_data=profile_data)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Health profile already exists") from exc
    except Exception:
        await session.rollback()
        raise

    return _to_health_profile_response(profile)


async def get_health_profile(
    session,
    *,
    user_id: int,
) -> HealthProfileResponse:
    user = await get_user_by_id(session, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    profile = await get_health_profile_by_user_id(session, user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Health profile not found")

    return _to_health_profile_response(profile)


async def create_health_indicators(
    session,
    *,
    user_id: int,
    payload: HealthIndicatorBatchCreateRequest,
) -> list[HealthIndicatorResponse]:
    user = await get_user_by_id(session, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.status != "active":
        raise HTTPException(status_code=409, detail="User is not active")

    profile = await get_health_profile_by_user_id(session, user_id)
    if profile is None:
        raise HTTPException(status_code=409, detail="Health profile is required")

    generated_batch_id = str(uuid4())
    records = []
    for item in payload.indicators:
        if item.source not in ALLOWED_HEALTH_INDICATOR_SOURCES:
            raise HTTPException(status_code=422, detail="Invalid health indicator source")

        record = item.model_dump()
        record["user_id"] = user_id
        record["batch_id"] = record["batch_id"] or generated_batch_id
        records.append(record)

    try:
        indicators = await create_health_indicator_records(session, records=records)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Health indicator creation failed") from exc
    except Exception:
        await session.rollback()
        raise

    return [_to_health_indicator_response(indicator) for indicator in indicators]


async def list_health_indicators(
    session,
    *,
    user_id: int,
    indicator_type: str | None = None,
    start_at=None,
    end_at=None,
    limit: int = 50,
) -> list[HealthIndicatorResponse]:
    user = await get_user_by_id(session, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    indicators = await list_health_indicators_by_user(
        session,
        user_id=user_id,
        indicator_type=indicator_type,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
    )
    return [_to_health_indicator_response(indicator) for indicator in indicators]


async def get_latest_health_indicators(
    session,
    *,
    user_id: int,
) -> list[HealthIndicatorResponse]:
    user = await get_user_by_id(session, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    indicators = await list_latest_health_indicators_by_user(session, user_id=user_id)
    return [_to_health_indicator_response(indicator) for indicator in indicators]
