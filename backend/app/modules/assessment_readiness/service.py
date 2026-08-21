from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
import hashlib
import json
from typing import Any, Mapping
from uuid import UUID

from .ports import AssessmentReadinessRepositoryPort
from .domain import AssessmentReadinessInputs, evaluate_readiness
from app.modules.projection_read.domain import ProjectionReadUnavailable
from app.modules.projection_read.service import (
    HealthProjectionCoverageAuthorityService,
    LatestReadyHealthProjectionResolverService,
    MemberHealthProjectionReadService,
)
from app.modules.user_health.service import Slice4Secrets


@dataclass(frozen=True, slots=True)
class AssemblyMutationPlan:
    preimage: Mapping[str, Any]
    expected_postimage: Mapping[str, Any]


def build_assembly_mutation_plan(
    *,
    assembly_id: UUID,
    service_case_id: UUID,
    source_vector_digest: str,
    assembly_digest: str,
    status: str,
    indicator_codes: tuple[str, ...],
    generated_at: datetime,
    preimage: Mapping[str, Any],
) -> AssemblyMutationPlan:
    pointer_version = int(preimage["pointer_version"]) + 1
    expected = {
        "assembly": {
            "assembly_id": str(assembly_id),
            "service_case_id": str(service_case_id),
            "source_vector_digest": source_vector_digest,
            "assembly_digest": assembly_digest,
            "status": status,
            "generated_at": generated_at.isoformat(),
        },
        "facts": tuple(sorted(set(indicator_codes))),
        "pointer": {"current_assembly_id": str(assembly_id), "version": pointer_version},
        "audit": {"event": "ASSESSMENT_INPUT_ASSEMBLED", "aggregate_id": str(assembly_id)},
        "outbox": {"event": "ASSESSMENT_READINESS_COMPUTED", "aggregate_id": str(assembly_id)},
        "receipt": {"aggregate_id": str(assembly_id), "version": pointer_version},
    }
    return AssemblyMutationPlan(preimage=dict(preimage), expected_postimage=expected)


def classify_assembly_confirmation(
    plan: AssemblyMutationPlan, actual_postimage: Mapping[str, Any]
) -> str:
    if actual_postimage == plan.expected_postimage:
        return "COMMITTED"
    if actual_postimage == plan.preimage:
        return "NOT_COMMITTED"
    return "UNKNOWN"


async def read_current_readiness(
    repository: AssessmentReadinessRepositoryPort,
    *,
    service_case_id: UUID,
) -> dict[str, Any]:
    current = await repository.current_readiness(service_case_id)
    if current is None:
        return {
            "service_case_id": service_case_id,
            "status": "DATA_SYNC_PENDING",
            "reason_codes": ("RECOMPUTE_PENDING",),
            "projection_status": "SYNC_PENDING",
        }
    result = {
        "service_case_id": current["service_case_id"],
        "status": current["status"],
        "reason_codes": tuple(current["reason_codes"]),
        "missing_indicator_codes": tuple(current["missing_codes"]),
        "expired_indicator_codes": tuple(current["expired_codes"]),
        "disputed_indicator_codes": tuple(current["disputed_codes"]),
        "profile_revision_id": current["profile_revision_id"],
        "policy_version": current["policy_version"],
        "projection_status": current["projection_status"],
        "data_as_of": current["data_as_of"],
        "generated_at": current["generated_at"],
    }
    if current["status"] == "ASSESSMENT_READY":
        result["assembly_id"] = current["assembly_id"]
    return result


def _uuid7_from(timestamp: datetime, material: bytes, discriminator: int) -> UUID:
    milliseconds = int(timestamp.timestamp() * 1000)
    raw = bytearray(milliseconds.to_bytes(6, "big") + material[:10])
    raw[6] = (raw[6] & 0x0F) | 0x70
    raw[8] = (raw[8] & 0x3F) | 0x80
    raw[-1] ^= discriminator
    return UUID(bytes=bytes(raw))


def _policy_requirements(policy: Mapping[str, Any]) -> tuple[tuple[str, int | None], ...]:
    values = []
    for item in policy.get("required_indicators", ()):
        if type(item) is str:
            code, max_age_days = item, None
        elif type(item) is dict and set(item).issubset({"indicator_code", "max_age_days"}):
            code, max_age_days = item.get("indicator_code"), item.get("max_age_days")
        else:
            raise RuntimeError("DEPENDENCY_UNAVAILABLE") from None
        if (
            type(code) is not str
            or not code
            or (max_age_days is not None and (type(max_age_days) is not int or max_age_days < 0))
        ):
            raise RuntimeError("DEPENDENCY_UNAVAILABLE") from None
        values.append((code, max_age_days))
    if not values or len({code for code, _ in values}) != len(values):
        raise RuntimeError("DEPENDENCY_UNAVAILABLE") from None
    return tuple(values)


def _json_value(value):
    if is_dataclass(value):
        return _json_value(asdict(value))
    if type(value) is UUID:
        return str(value)
    if type(value) is datetime:
        return value.isoformat()
    if type(value) is Decimal:
        return str(value)
    if type(value) is bytes:
        return value.hex()
    if type(value) is dict:
        return {str(key): _json_value(item) for key, item in value.items()}
    if type(value) in (tuple, list):
        return [_json_value(item) for item in value]
    return value


async def recompute_assessment_readiness(
    repository,
    *,
    service_case_id: UUID,
    secrets: Slice4Secrets | None = None,
) -> dict[str, Any]:
    """Freeze one immutable readiness assembly from pre-write authorities and P3 ports."""
    current = await repository.currentness(service_case_id)
    if current is None:
        raise RuntimeError("SERVICE_CASE_NOT_FOUND") from None
    secret_box = secrets or Slice4Secrets()
    generated_at = current["transaction_time"]
    if type(generated_at) is str:
        generated_at = datetime.fromisoformat(generated_at)
    if type(generated_at) is not datetime or generated_at.utcoffset() is None:
        raise RuntimeError("DEPENDENCY_UNAVAILABLE") from None

    policy = current.get("policy")
    requirements: tuple[tuple[str, int | None], ...] = ()
    coverage = None
    resolved = None
    selected = {}
    missing: set[str] = set()
    expired: set[str] = set()
    disputed: set[str] = set()
    if policy is not None:
        requirements = _policy_requirements(policy)
        codes = tuple(code for code, _ in requirements)
        coverage = await HealthProjectionCoverageAuthorityService().capture(
            subject_member_id=UUID(str(current["subject_member_id"])),
            required_indicator_codes=codes,
        )
        missing.update(item.indicator_code for item in coverage.items if item.fact_count == 0)
        if not missing:
            resolved = await LatestReadyHealthProjectionResolverService().resolve(
                coverage_token=coverage,
                required_projection_version=int(policy["projection_version"]),
            )
        if resolved is not None:
            page = await MemberHealthProjectionReadService().list_current_facts(
                resolved_generation=resolved,
                subject_member_id=UUID(str(current["subject_member_id"])),
                indicator_codes=codes,
                measured_from=datetime(1970, 1, 1, tzinfo=timezone.utc),
                measured_to=generated_at + timedelta(microseconds=1),
                limit=200,
            )
            for fact in page.items:
                selected.setdefault(fact.indicator_code, fact)
            missing.update(code for code in codes if code not in selected)
            allowed_states = set(policy.get("allowed_states") or ())
            business_day = generated_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
            for code, max_age_days in requirements:
                fact = selected.get(code)
                if fact is None:
                    continue
                if fact.verification_state == "DISPUTED":
                    disputed.add(code)
                elif allowed_states and fact.verification_state not in allowed_states:
                    missing.add(code)
                if max_age_days is not None and (business_day - fact.business_day).days > max_age_days:
                    expired.add(code)

    result = evaluate_readiness(
        AssessmentReadinessInputs(
            authorization_complete=bool(current["authorization_complete"]),
            profile_current=current.get("profile_revision_id") is not None,
            policy_available=policy is not None,
            missing_indicator_codes=tuple(missing),
            expired_indicator_codes=tuple(expired),
            disputed_indicator_codes=tuple(disputed),
            projection_current=resolved is not None,
        )
    )
    source_basis = {
        "case": current,
        "coverage": coverage,
        "resolved_generation": resolved.generation_id if resolved else None,
        "selected": tuple(
            (code, fact.fact_ref, fact.status_event_seq)
            for code, fact in sorted(selected.items())
        ),
        "result": result,
    }
    coordination, _ = secret_box.digest("COORDINATION", _json_value(source_basis))
    assembly_id = _uuid7_from(generated_at, coordination, 1)
    audit_id = _uuid7_from(generated_at, coordination, 2)
    event_id = _uuid7_from(generated_at, coordination, 3)
    receipt_id = _uuid7_from(generated_at, coordination, 4)
    idempotency_key = _uuid7_from(generated_at, coordination, 5)

    encrypted_rows = []
    for code, fact in sorted(selected.items()):
        ciphertext, value_key_id = secret_box.encrypt_assembly_fact(
            str(fact.numeric_value),
            tenant_public_id=UUID(str(current["tenant_public_id"])),
            subject_member_id=UUID(str(current["subject_member_id"])),
            service_case_id=service_case_id,
            assembly_id=assembly_id,
            fact_ref=fact.fact_ref,
            indicator_code=code,
        )
        row_digest, _ = secret_box.digest(
            "AUDIT_DIGEST",
            _json_value((assembly_id, code, fact.fact_ref, fact.status_event_seq)),
        )
        encrypted_rows.append(
            {
                "indicator_code": code,
                "fact_ref": str(fact.fact_ref),
                "measured_at": fact.measured_at.isoformat(),
                "received_at": fact.received_at.isoformat(),
                "source_type": fact.source_type,
                "verification_state": fact.verification_state,
                "value_ciphertext": ciphertext.hex(),
                "value_key_id": value_key_id,
                "unit": fact.unit,
                "row_digest": row_digest.hex(),
            }
        )
    source_digest, digest_key_id = secret_box.digest("AUDIT_DIGEST", _json_value(source_basis))
    source_vector_digest, _ = secret_box.digest("REPLAY_DIGEST", _json_value(source_basis))
    assembly_digest, _ = secret_box.digest(
        "AUDIT_DIGEST", _json_value((source_basis, encrypted_rows))
    )
    source_vector = {
        "consent_version_ids": _json_value(current["consent_version_ids"]),
        "resolved_generation_id": resolved.generation_id if resolved else "",
        "required_max_fact_id": max((item.max_fact_id for item in coverage.items), default=0) if coverage else 0,
        "required_max_status_event_seq": max((item.max_status_event_seq for item in coverage.items), default=0) if coverage else 0,
        "missing_codes": list(result.missing_indicator_codes),
        "expired_codes": list(result.expired_indicator_codes),
        "disputed_codes": list(result.disputed_indicator_codes),
        "source_vector_digest": source_vector_digest.hex(),
        "source_digest": source_digest.hex(),
        "assembly_digest": assembly_digest.hex(),
        "digest_key_id": digest_key_id,
        "generated_at": generated_at.isoformat(),
        "audit_id": str(audit_id),
        "event_id": str(event_id),
        "receipt_id": str(receipt_id),
    }
    request_digest, _ = secret_box.digest(
        "REQUEST_DIGEST", _json_value((service_case_id, source_vector_digest))
    )
    expected = {
        "assembly_id": str(assembly_id),
        "service_case_id": str(service_case_id),
        "status": result.status,
        "source_vector": source_vector,
        "facts": encrypted_rows,
    }
    expected_digest, _ = secret_box.digest("REPLAY_DIGEST", expected)
    row = await repository.write_assembly(
        service_case_id=service_case_id,
        requested_assembly_id=assembly_id,
        subject_member_id=UUID(str(current["subject_member_id"])),
        tenant_public_id=UUID(str(current["tenant_public_id"])),
        primary_therapist_id=UUID(str(current["primary_therapist_id"])),
        profile_revision_id=(
            UUID(str(current["profile_revision_id"]))
            if current.get("profile_revision_id") is not None else None
        ),
        policy_version_id=(
            UUID(str(policy["policy_version_id"])) if policy is not None else None
        ),
        projection_version=int(policy["projection_version"]) if policy else 2,
        rule_version=policy["rule_version"] if policy else "UNAVAILABLE",
        source_snapshot=coverage.source_snapshot if coverage else "UNAVAILABLE",
        source_vector=source_vector,
        encrypted_fact_rows=encrypted_rows,
        readiness_status=result.status,
        reason_codes=list(result.reason_codes),
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        expected_postimage_digest=expected_digest,
    )
    return row
