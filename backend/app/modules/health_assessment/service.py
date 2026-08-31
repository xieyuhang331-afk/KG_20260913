from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum
import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets as stdlib_secrets
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Mapping
from uuid import UUID
from zoneinfo import ZoneInfo

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings
from app.modules.user_health.service import Slice4Secrets

from .domain import AssessmentFacts, EvaluationContext, MODULE_CODES, RISK_RANK
from .evaluator import RULE_SET_CODE, evaluate_cn_adult_baseline_v1


@dataclass(frozen=True, slots=True)
class CompletionMutationPlan:
    preimage: Mapping[str, Any]
    expected_postimage: Mapping[str, Any]


class CommitOutcome(StrEnum):
    COMMITTED = "COMMITTED"
    NOT_COMMITTED = "NOT_COMMITTED"
    UNKNOWN = "UNKNOWN"


class HealthAssessmentError(RuntimeError):
    pass


_CURSOR_DOMAIN = b"slice5-health-assessment-cursor:v1:\x00"
_PUBLIC_USER_REF_DOMAIN = b"slice5-public-user-ref:v1:\x00"


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _cursor_key() -> bytes:
    value = os.getenv("KG_JWT_SECRET_KEY")
    if value is None or len(value.encode("utf-8")) < 32:
        raise HealthAssessmentError("DEPENDENCY_UNAVAILABLE")
    return value.encode("utf-8")


def encode_slice5_cursor(cursor_id: UUID, scope: Mapping[str, object]) -> str:
    if cursor_id.version != 7 or type(scope) is not dict:
        raise HealthAssessmentError("INVALID_REQUEST")
    raw = json.dumps(
        {"cursor_id": str(cursor_id), "scope": _json_value(dict(scope)), "v": 1},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    signature = hmac.new(_cursor_key(), _CURSOR_DOMAIN + raw, hashlib.sha256).digest()
    return f"{_base64url(raw)}.{_base64url(signature)}"


def decode_slice5_cursor(cursor: str | None, scope: Mapping[str, object]) -> UUID | None:
    if cursor is None:
        return None
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
        if _base64url(raw) != encoded_payload or _base64url(signature) != encoded_signature:
            raise ValueError
        expected = hmac.new(_cursor_key(), _CURSOR_DOMAIN + raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        payload = json.loads(raw)
        if set(payload) != {"cursor_id", "scope", "v"} or payload["v"] != 1:
            raise ValueError
        if payload["scope"] != _json_value(dict(scope)):
            raise ValueError
        cursor_id = UUID(payload["cursor_id"])
        if cursor_id.version != 7:
            raise ValueError
        return cursor_id
    except (AttributeError, TypeError, UnicodeError, ValueError, json.JSONDecodeError):
        raise HealthAssessmentError("INVALID_REQUEST") from None


def public_user_reference(actor_user_id: int) -> str:
    if type(actor_user_id) is not int or actor_user_id < 1:
        raise HealthAssessmentError("DEPENDENCY_UNAVAILABLE")
    digest = hmac.new(
        _cursor_key(),
        _PUBLIC_USER_REF_DOMAIN + str(actor_user_id).encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"usr_{_base64url(digest)}"


class Slice5Secrets:
    def __init__(self) -> None:
        settings = get_settings()
        self._digest_id, self._digest_keys = self._load(
            settings.slice5_digest_current_key_id,
            settings.slice5_digest_keyring_json,
        )
        self._phi_id, self._phi_keys = self._load(
            settings.slice5_phi_current_key_id,
            settings.slice5_phi_keyring_json,
        )
        if (
            self._digest_id == self._phi_id
            or self._digest_keys[self._digest_id] == self._phi_keys[self._phi_id]
        ):
            raise RuntimeError("DEPENDENCY_UNAVAILABLE") from None

    @staticmethod
    def _load(current_id: str | None, raw: str | None) -> tuple[str, dict[str, bytes]]:
        try:
            parsed = json.loads(raw or "")
            if (
                type(current_id) is not str
                or not current_id
                or type(parsed) is not dict
                or set(parsed) != {current_id}
            ):
                raise ValueError
            keys = {key: base64.b64decode(value, validate=True) for key, value in parsed.items()}
            if any(len(value) != 32 for value in keys.values()):
                raise ValueError
            return current_id, keys
        except Exception:
            raise RuntimeError("DEPENDENCY_UNAVAILABLE") from None

    @staticmethod
    def canonical(value: object) -> bytes:
        return json.dumps(
            _json_value(value), ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")

    def digest(self, domain: str, value: object) -> tuple[bytes, str]:
        if domain not in {
            "REQUEST", "SNAPSHOT", "AUDIT", "OUTBOX", "REPLAY", "RULE_FRAGMENT", "EVIDENCE"
        }:
            raise RuntimeError("DEPENDENCY_UNAVAILABLE") from None
        material = self.canonical({"domain": f"slice5.{domain.lower()}.v1", "value": value})
        return hmac.new(self._digest_keys[self._digest_id], material, hashlib.sha256).digest(), self._digest_id

    def encrypt_dispute(self, value: object, *, dispute_id: UUID, assessment_id: UUID) -> tuple[bytes, str]:
        aad = self.canonical(
            {
                "domain": "slice5.dispute.v1",
                "dispute_id": dispute_id,
                "assessment_id": assessment_id,
                "key_id": self._phi_id,
            }
        )
        nonce = stdlib_secrets.token_bytes(12)
        return nonce + AESGCM(self._phi_keys[self._phi_id]).encrypt(nonce, self.canonical(value), aad), self._phi_id

    def decrypt_dispute(self, value: bytes, key_id: str, *, dispute_id: UUID, assessment_id: UUID) -> object:
        try:
            aad = self.canonical(
                {
                    "domain": "slice5.dispute.v1",
                    "dispute_id": dispute_id,
                    "assessment_id": assessment_id,
                    "key_id": key_id,
                }
            )
            return json.loads(AESGCM(self._phi_keys[key_id]).decrypt(value[:12], value[12:], aad))
        except (KeyError, ValueError, TypeError, InvalidTag, json.JSONDecodeError):
            raise RuntimeError("DEPENDENCY_UNAVAILABLE") from None


def _json_value(value: Any) -> Any:
    if type(value) is UUID:
        return str(value)
    if type(value) is datetime:
        return value.isoformat()
    if type(value) is bytes:
        return value.hex()
    if type(value) is dict:
        return {str(key): _json_value(item) for key, item in value.items()}
    if type(value) in (tuple, list):
        return [_json_value(item) for item in value]
    if hasattr(value, "as_tuple"):
        return str(value)
    return value


async def commit_with_confirmation(
    session,
    *,
    confirm: Callable[[], Awaitable[CommitOutcome]],
) -> CommitOutcome:
    try:
        await session.commit()
        return CommitOutcome.COMMITTED
    except BaseException as error:
        if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise
        try:
            await session.rollback()
        except Exception:
            pass
        try:
            outcome = await confirm()
        except Exception:
            outcome = CommitOutcome.UNKNOWN
        if outcome is CommitOutcome.COMMITTED:
            return outcome
        if outcome is CommitOutcome.NOT_COMMITTED:
            raise RuntimeError("DEPENDENCY_UNAVAILABLE") from None
        raise RuntimeError("COMMIT_OUTCOME_UNKNOWN") from None


def _uuid7_from(timestamp: datetime, material: bytes, discriminator: int) -> UUID:
    milliseconds = int(timestamp.timestamp() * 1000)
    raw = bytearray(milliseconds.to_bytes(6, "big") + material[:10])
    raw[6] = (raw[6] & 0x0F) | 0x70
    raw[8] = (raw[8] & 0x3F) | 0x80
    raw[-1] ^= discriminator
    return UUID(bytes=bytes(raw))


def _derived_uuid7(source: UUID, discriminator: int) -> UUID:
    raw = bytearray(source.bytes)
    raw[-1] ^= discriminator
    raw[6] = (raw[6] & 0x0F) | 0x70
    raw[8] = (raw[8] & 0x3F) | 0x80
    return UUID(bytes=bytes(raw))


def _stable_uuid7(material: bytes, discriminator: int) -> UUID:
    if len(material) < 16:
        raise ValueError("INVALID_REQUEST")
    raw = bytearray(material[:16])
    raw[-1] ^= discriminator
    raw[6] = (raw[6] & 0x0F) | 0x70
    raw[8] = (raw[8] & 0x3F) | 0x80
    return UUID(bytes=bytes(raw))


def _timestamp_us(value: datetime) -> int:
    if type(value) is not datetime or value.utcoffset() is None:
        raise ValueError("INVALID_REQUEST")
    return int(value.timestamp() * 1_000_000)


def _digest_hex(value: object) -> str:
    if type(value) is bytes and len(value) == 32:
        return value.hex()
    if type(value) is str and len(value) == 64:
        try:
            bytes.fromhex(value)
        except ValueError:
            pass
        else:
            return value.lower()
    raise ValueError("DEPENDENCY_UNAVAILABLE") from None


def build_assessment_start_postimage(
    payload: Mapping[str, Any],
    *,
    response: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "assessment": {
            "assessment_id": str(payload["assessment_id"]),
            "service_case_id": str(payload["service_case_id"]),
            "subject_member_id": str(payload["subject_member_id"]),
            "tenant_id": int(payload["tenant_id"]),
            "sequence_no": int(payload["sequence_no"]),
            "snapshot_id": str(payload["snapshot_id"]),
            "rule_set_version_id": str(payload["rule_set_version_id"]),
            "supersedes_assessment_id": (
                str(payload["supersedes_assessment_id"])
                if payload.get("supersedes_assessment_id")
                else None
            ),
            "status": "DRAFT_SNAPSHOT",
            "initiated_by": int(payload["actor_user_id"]),
            "initiated_at_us": _timestamp_us(payload["created_at"]),
            "version": 1,
        },
        "snapshot": {
            "snapshot_id": str(payload["snapshot_id"]),
            "assessment_id": str(payload["assessment_id"]),
            "service_case_id": str(payload["service_case_id"]),
            "subject_member_id": str(payload["subject_member_id"]),
            "tenant_id": int(payload["tenant_id"]),
            "assembly_id": str(payload["assembly_id"]),
            "assembly_digest": _digest_hex(payload["assembly_digest"]),
            "source_vector_digest": _digest_hex(payload["source_vector_digest"]),
            "profile_revision_id": str(payload["profile_revision_id"]),
            "consent_version_ids": _json_value(payload["consent_version_ids"]),
            "rule_set_version_id": str(payload["rule_set_version_id"]),
            "projection_version": int(payload["projection_version"]),
            "projection_rule_version": str(payload["projection_rule_version"]),
            "required_max_fact_id": int(payload["required_max_fact_id"]),
            "required_max_status_event_seq": int(payload["required_max_status_event_seq"]),
            "source_snapshot": str(payload["source_snapshot"]),
            "fact_ref_manifest_digest": _digest_hex(payload["fact_ref_manifest_digest"]),
            "snapshot_digest": _digest_hex(payload["snapshot_digest"]),
            "digest_key_id": str(payload["digest_key_id"]),
            "created_at_us": _timestamp_us(payload["created_at"]),
        },
        "module_results": [],
        "high_risk_task": None,
        "audit": {
            "audit_id": str(payload["audit_id"]),
            "action": "ASSESSMENT_STARTED",
            "actor_user_id": int(payload["actor_user_id"]),
            "actor_role": "therapist",
            "target_type": "ASSESSMENT",
            "target_id": str(payload["assessment_id"]),
            "evidence_digest": _digest_hex(payload["audit_digest"]),
            "occurred_at_us": _timestamp_us(payload["created_at"]),
        },
        "outbox": {
            "event_id": str(payload["event_id"]),
            "aggregate_type": "ASSESSMENT",
            "aggregate_ref": str(payload["assessment_id"]),
            "event_type": "ASSESSMENT_RUN_REQUESTED",
            "payload_digest": _digest_hex(payload["outbox_digest"]),
            "status": "PENDING",
            "attempts": 0,
            "created_at_us": _timestamp_us(payload["created_at"]),
        },
        "receipt": {
            "receipt_id": str(payload["receipt_id"]),
            "actor_scope": str(payload["actor_user_id"]),
            "operation": "ASSESSMENT_START",
            "target_id": str(payload["assessment_id"]),
            "idempotency_key": str(payload["idempotency_key"]),
            "request_digest": _digest_hex(payload["request_digest"]),
            "response": _json_value(dict(response)),
            "response_key_id": str(payload["digest_key_id"]),
            "created_at_us": _timestamp_us(payload["created_at"]),
        },
    }


def build_completion_mutation_plan(
    *,
    assessment_id: UUID,
    module_risks: Mapping[str, str],
    trigger_codes: tuple[str, ...],
    completed_at: datetime,
    preimage: Mapping[str, Any] | None = None,
) -> CompletionMutationPlan:
    if assessment_id.version != 7 or completed_at.utcoffset() is None:
        raise ValueError("INVALID_REQUEST")
    if set(module_risks) != set(MODULE_CODES):
        raise ValueError("RULE_EVALUATION_UNAVAILABLE")
    if any(value not in RISK_RANK for value in module_risks.values()):
        raise ValueError("RULE_EVALUATION_UNAVAILABLE")
    overall = max(module_risks.values(), key=RISK_RANK.__getitem__)
    if (overall == "HIGH_RISK") != bool(trigger_codes):
        raise ValueError("RULE_EVALUATION_UNAVAILABLE")
    timestamp = completed_at.isoformat()
    postimage: dict[str, Any] = {
        "assessment": {
            "assessment_id": str(assessment_id),
            "status": "COMPLETED",
            "overall_risk": overall,
            "completed_at": timestamp,
        },
        "module_results": tuple(
            {"module_code": code, "risk_level": module_risks[code]}
            for code in MODULE_CODES
        ),
        "high_risk_task": None,
        "audit": {"event": "ASSESSMENT_COMPLETED", "aggregate_id": str(assessment_id)},
        "outbox": {"event": "ASSESSMENT_COMPLETED", "aggregate_id": str(assessment_id)},
        "receipt": {"aggregate_id": str(assessment_id)},
    }
    if overall == "HIGH_RISK":
        postimage["high_risk_task"] = {
            "task_id": str(_derived_uuid7(assessment_id, 1)),
            "assessment_id": str(assessment_id),
            "status": "OPEN",
            "reason_module_codes": tuple(
                code for code in MODULE_CODES if module_risks[code] == "HIGH_RISK"
            ),
            "trigger_codes": tuple(dict.fromkeys(trigger_codes)),
            "created_at": timestamp,
            "due_at": timestamp,
        }
    return CompletionMutationPlan(
        preimage=MappingProxyType(dict(preimage or {})),
        expected_postimage=MappingProxyType(postimage),
    )


def build_assessment_completion_postimage(
    payload: Mapping[str, Any], *, response: Mapping[str, Any]
) -> dict[str, Any]:
    completed_at = payload["completed_at"]
    overall_risk = str(payload["overall_risk"])
    module_results = sorted([
        {
            "module_result_id": str(row["module_result_id"]),
            "assessment_id": str(payload["assessment_id"]),
            "module_code": str(row["module_code"]),
            "risk_level": str(row["risk_level"]),
            "reason_codes": _json_value(row["reason_codes"]),
            "evidence_manifest": _json_value(row["evidence_manifest"]),
            "message_codes": _json_value(row["message_codes"]),
            "rule_fragment_digest": bytes(row["rule_fragment_digest"]).hex(),
            "created_at_us": _timestamp_us(completed_at),
        }
        for row in payload["module_results"]
    ], key=lambda row: row["module_code"])
    high_risk_task = None
    if overall_risk == "HIGH_RISK":
        high_risk_task = {
            "task_id": str(payload["task_id"]),
            "assessment_id": str(payload["assessment_id"]),
            "service_case_id": str(payload["service_case_id"]),
            "tenant_id": int(payload["tenant_id"]),
            "subject_member_id": str(payload["subject_member_id"]),
            "status": "OPEN",
            "reason_module_codes": [
                row["module_code"] for row in module_results if row["risk_level"] == "HIGH_RISK"
            ],
            "trigger_codes": _json_value(payload["trigger_codes"]),
            "assigned_actor_id": None,
            "due_at_us": _timestamp_us(completed_at),
            "claimed_at_us": None,
            "closed_at_us": None,
            "close_reason_code": None,
            "created_at_us": _timestamp_us(completed_at),
            "version": 1,
        }
    superseded_assessment = None
    superseded_dispute = None
    if payload.get("supersedes_assessment_id"):
        superseded_assessment = {
            "assessment_id": str(payload["supersedes_assessment_id"]),
            "status": "SUPERSEDED",
            "version": int(payload["superseded_assessment_version"]) + 1,
        }
        superseded_dispute = {
            "dispute_id": str(payload["superseded_dispute_id"]),
            "assessment_id": str(payload["supersedes_assessment_id"]),
            "status": "SUPERSEDED",
            "superseding_assessment_id": str(payload["assessment_id"]),
            "resolved_at_us": _timestamp_us(completed_at),
            "version": int(payload["superseded_dispute_version"]) + 1,
        }
    return {
        "assessment": {
            "assessment_id": str(payload["assessment_id"]),
            "status": "COMPLETED",
            "overall_risk": overall_risk,
            "completed_at_us": _timestamp_us(completed_at),
            "version": int(payload["expected_version"]) + 1,
        },
        "module_results": module_results,
        "high_risk_task": high_risk_task,
        "superseded_assessment": superseded_assessment,
        "superseded_dispute": superseded_dispute,
        "audit": {
            "audit_id": str(payload["audit_id"]),
            "action": "ASSESSMENT_COMPLETED",
            "actor_user_id": 0,
            "actor_role": "workflow_worker",
            "target_type": "ASSESSMENT",
            "target_id": str(payload["assessment_id"]),
            "evidence_digest": bytes(payload["audit_digest"]).hex(),
            "occurred_at_us": _timestamp_us(completed_at),
        },
        "outbox": {
            "event_id": str(payload["event_id"]),
            "aggregate_type": "ASSESSMENT",
            "aggregate_ref": str(payload["assessment_id"]),
            "event_type": "ASSESSMENT_COMPLETED",
            "payload_digest": bytes(payload["outbox_digest"]).hex(),
            "status": "PENDING",
            "attempts": 0,
            "created_at_us": _timestamp_us(completed_at),
        },
        "receipt": {
            "receipt_id": str(payload["receipt_id"]),
            "actor_scope": "workflow_worker",
            "operation": "ASSESSMENT_COMPLETE",
            "target_id": str(payload["assessment_id"]),
            "idempotency_key": str(payload["assessment_id"]),
            "request_digest": bytes(payload["request_digest"]).hex(),
            "response": _json_value(dict(response)),
            "response_key_id": str(payload["digest_key_id"]),
            "created_at_us": _timestamp_us(completed_at),
        },
    }


def build_assessment_failure_postimage(
    payload: Mapping[str, Any], *, response: Mapping[str, Any]
) -> dict[str, Any]:
    failed_at = payload["failed_at"]
    return {
        "assessment": {
            "assessment_id": str(payload["assessment_id"]),
            "status": "FAILED",
            "failure_code": str(payload["failure_code"]),
            "failed_at_us": _timestamp_us(failed_at),
            "version": int(payload["expected_version"]) + 1,
        },
        "audit": {
            "audit_id": str(payload["audit_id"]),
            "action": "ASSESSMENT_FAILED",
            "actor_user_id": 0,
            "actor_role": "workflow_worker",
            "target_type": "ASSESSMENT",
            "target_id": str(payload["assessment_id"]),
            "evidence_digest": bytes(payload["audit_digest"]).hex(),
            "occurred_at_us": _timestamp_us(failed_at),
        },
        "outbox": {
            "event_id": str(payload["event_id"]),
            "aggregate_type": "ASSESSMENT",
            "aggregate_ref": str(payload["assessment_id"]),
            "event_type": "ASSESSMENT_FAILED",
            "payload_digest": bytes(payload["outbox_digest"]).hex(),
            "status": "PENDING",
            "attempts": 0,
            "created_at_us": _timestamp_us(failed_at),
        },
        "receipt": {
            "receipt_id": str(payload["receipt_id"]),
            "actor_scope": "workflow_worker",
            "operation": "ASSESSMENT_FAIL",
            "target_id": str(payload["assessment_id"]),
            "idempotency_key": str(payload["assessment_id"]),
            "request_digest": bytes(payload["request_digest"]).hex(),
            "response": _json_value(dict(response)),
            "response_key_id": str(payload["digest_key_id"]),
            "created_at_us": _timestamp_us(failed_at),
        },
    }


async def fail_assessment(
    repository,
    *,
    assessment_id: UUID,
    expected_version: int,
    failure_code: str,
    failed_at: datetime,
    confirmation_session_factory=None,
    secrets: Slice5Secrets | None = None,
) -> dict[str, Any]:
    if failure_code not in {"RULE_EVALUATION_UNAVAILABLE"} or failed_at.utcoffset() is None:
        raise RuntimeError("INVALID_REQUEST") from None
    secret_box = secrets or Slice5Secrets()
    request_digest, digest_key_id = secret_box.digest(
        "REQUEST",
        {
            "assessment_id": assessment_id,
            "expected_version": expected_version,
            "failure_code": failure_code,
        },
    )
    audit_digest, _ = secret_box.digest(
        "AUDIT", {"action": "ASSESSMENT_FAILED", "assessment_id": assessment_id, "failure_code": failure_code}
    )
    outbox_digest, _ = secret_box.digest(
        "OUTBOX", {"event": "ASSESSMENT_FAILED", "assessment_id": assessment_id, "failure_code": failure_code}
    )
    response = {
        "assessment_id": assessment_id,
        "status": "FAILED",
        "failure_code": failure_code,
        "version": expected_version + 1,
    }
    payload = {
        "assessment_id": assessment_id,
        "expected_version": expected_version,
        "failure_code": failure_code,
        "failed_at": failed_at,
        "audit_id": _uuid7_from(failed_at, request_digest, 31),
        "event_id": _uuid7_from(failed_at, request_digest, 32),
        "receipt_id": _uuid7_from(failed_at, request_digest, 33),
        "request_digest": request_digest,
        "audit_digest": audit_digest,
        "outbox_digest": outbox_digest,
        "digest_key_id": digest_key_id,
        "response": response,
    }
    expected = build_assessment_failure_postimage(payload, response=response)
    postimage_digest, _ = secret_box.digest("REPLAY", expected)
    expected["receipt"]["postimage_digest"] = postimage_digest.hex()
    payload["postimage_digest"] = postimage_digest
    result = await repository.fail_assessment(payload)
    if confirmation_session_factory is None:
        await repository.session.commit()
        return result

    async def confirm() -> CommitOutcome:
        async with confirmation_session_factory() as fresh:
            actual = await type(repository)(fresh).confirm_assessment(assessment_id)
            if actual is None:
                return CommitOutcome.NOT_COMMITTED
            return CommitOutcome.COMMITTED if actual == expected else CommitOutcome.UNKNOWN

    outcome = await commit_with_confirmation(repository.session, confirm=confirm)
    if outcome is not CommitOutcome.COMMITTED:
        raise RuntimeError("COMMIT_OUTCOME_UNKNOWN") from None
    return result


def classify_completion_confirmation(
    plan: CompletionMutationPlan, actual_postimage: Mapping[str, Any]
) -> str:
    if actual_postimage == plan.expected_postimage:
        return "COMMITTED"
    if actual_postimage == plan.preimage:
        return "NOT_COMMITTED"
    return "UNKNOWN"


def ordinary_plan_authority(
    *,
    current_assessment_status: str | None,
    current_overall_risk: str | None,
    has_open_high_risk_task: bool,
    all_modules_not_assessed: bool,
) -> bool:
    return bool(
        current_assessment_status == "COMPLETED"
        and current_overall_risk in {"WITHIN_RANGE", "ATTENTION"}
        and not has_open_high_risk_task
        and not all_modules_not_assessed
    )


async def start_assessment(
    repository,
    *,
    actor_user_id: int,
    service_case_id: UUID,
    expected_case_version: int,
    idempotency_key: str,
    request_id: UUID,
    confirmation_session_factory=None,
    secrets: Slice5Secrets | None = None,
) -> dict[str, Any]:
    if (
        actor_user_id < 1
        or service_case_id.version != 7
        or expected_case_version < 1
        or len(idempotency_key) < 8
        or request_id.version != 7
    ):
        raise RuntimeError("INVALID_REQUEST") from None
    secret_box = secrets or Slice5Secrets()
    request_digest, digest_key_id = secret_box.digest(
        "REQUEST",
        {
            "actor_user_id": actor_user_id,
            "service_case_id": service_case_id,
            "expected_case_version": expected_case_version,
            "idempotency_key": idempotency_key,
        },
    )
    replay = await repository.assessment_start_replay(
        actor_user_id, idempotency_key, request_digest
    )
    if replay is not None:
        return replay
    authority = await repository.start_authority(service_case_id, actor_user_id)
    if authority is None:
        raise RuntimeError("PRIMARY_THERAPIST_REQUIRED") from None
    if int(authority["case_version"]) != expected_case_version:
        raise RuntimeError("VERSION_CONFLICT") from None
    if authority.get("assembly_status") != "ASSESSMENT_READY":
        raise RuntimeError("ASSESSMENT_NOT_READY") from None
    if authority.get("rule_set_code") != RULE_SET_CODE:
        raise RuntimeError("RULE_SET_NOT_ACTIVE") from None
    now = authority.get("transaction_time")
    if type(now) is str:
        now = datetime.fromisoformat(now)
    if type(now) is not datetime or now.utcoffset() is None:
        raise RuntimeError("DEPENDENCY_UNAVAILABLE") from None
    assessment_id = _uuid7_from(now, request_digest, 1)
    snapshot_id = _uuid7_from(now, request_digest, 2)
    audit_id = _uuid7_from(now, request_digest, 3)
    event_id = _uuid7_from(now, request_digest, 4)
    receipt_id = _uuid7_from(now, request_digest, 5)
    snapshot_digest, _ = secret_box.digest(
        "SNAPSHOT",
        {
            "assembly_id": authority["assembly_id"],
            "assembly_digest": authority["assembly_digest"],
            "source_vector_digest": authority["source_vector_digest"],
            "profile_revision_id": authority["profile_revision_id"],
            "consent_version_ids": authority["consent_version_ids"],
            "rule_set_version_id": authority["rule_set_version_id"],
            "max_fact_id": authority["required_max_fact_id"],
            "max_status_event_seq": authority["required_max_status_event_seq"],
        },
    )
    payload = {
        "assessment_id": assessment_id,
        "snapshot_id": snapshot_id,
        "service_case_id": service_case_id,
        "subject_member_id": authority["subject_member_id"],
        "tenant_id": authority["tenant_id"],
        "case_version": int(authority["case_version"]),
        "assembly_id": authority["assembly_id"],
        "assembly_digest": authority["assembly_digest"],
        "source_vector_digest": authority["source_vector_digest"],
        "profile_revision_id": authority["profile_revision_id"],
        "consent_version_ids": authority["consent_version_ids"],
        "rule_set_version_id": authority["rule_set_version_id"],
        "projection_version": authority["projection_version"],
        "projection_rule_version": authority["projection_rule_version"],
        "required_max_fact_id": authority["required_max_fact_id"],
        "required_max_status_event_seq": authority["required_max_status_event_seq"],
        "source_snapshot": authority["source_snapshot"],
        "fact_ref_manifest_digest": authority["fact_ref_manifest_digest"],
        "snapshot_digest": snapshot_digest,
        "digest_key_id": digest_key_id,
        "actor_user_id": actor_user_id,
        "idempotency_key": idempotency_key,
        "request_digest": request_digest,
        "request_id": request_id,
        "audit_id": audit_id,
        "event_id": event_id,
        "receipt_id": receipt_id,
        "created_at": now,
        "sequence_no": int(authority["next_sequence_no"]),
        "supersedes_assessment_id": authority.get("supersedes_assessment_id"),
    }
    response = {
        "assessment_id": assessment_id,
        "service_case_id": service_case_id,
        "sequence_no": int(authority["next_sequence_no"]),
        "status": "DRAFT_SNAPSHOT",
        "overall_risk": None,
        "rule_version": str(authority["rule_version"]),
        "input_snapshot_ref": snapshot_id,
        "supersedes_assessment_id": authority.get("supersedes_assessment_id"),
        "initiated_at": now,
        "completed_at": None,
        "version": 1,
    }
    audit_digest, _ = secret_box.digest(
        "AUDIT",
        {"action": "ASSESSMENT_STARTED", "assessment_id": assessment_id, "actor": actor_user_id},
    )
    outbox_digest, _ = secret_box.digest(
        "OUTBOX",
        {"event": "ASSESSMENT_RUN_REQUESTED", "assessment_id": assessment_id},
    )
    payload.update(
        {
            "audit_digest": audit_digest,
            "outbox_digest": outbox_digest,
            "response": response,
        }
    )
    expected = build_assessment_start_postimage(payload, response=response)
    postimage_digest, _ = secret_box.digest("REPLAY", expected)
    expected["receipt"]["postimage_digest"] = postimage_digest.hex()
    payload["postimage_digest"] = postimage_digest
    result = await repository.write_assessment_start(payload)
    if confirmation_session_factory is None:
        await repository.session.commit()
        return result

    async def confirm() -> CommitOutcome:
        async with confirmation_session_factory() as fresh:
            actual = await type(repository)(fresh).confirm_assessment(assessment_id)
            if actual is None:
                return CommitOutcome.NOT_COMMITTED
            return CommitOutcome.COMMITTED if actual == expected else CommitOutcome.UNKNOWN

    outcome = await commit_with_confirmation(repository.session, confirm=confirm)
    if outcome is not CommitOutcome.COMMITTED:
        raise RuntimeError("COMMIT_OUTCOME_UNKNOWN") from None
    return result


_INDICATOR_ALIASES = {
    "triglyceride": "triglycerides",
    "hdl_c": "hdl_cholesterol",
    "ldl_c": "ldl_cholesterol",
}

_SEVERE_GLUCOSE_PROFILE_CODES = {
    "requires_assistance": "REQUIRES_ASSISTANCE",
    "altered_consciousness": "ALTERED_CONSCIOUSNESS",
    "unsafe_swallowing": "UNSAFE_SWALLOWING",
}


def build_current_evaluation_context(
    identity_summary,
    profile: Mapping[str, Any],
    *,
    as_of: datetime,
) -> EvaluationContext:
    try:
        if (
            as_of.utcoffset() is None
            or identity_summary.evidence_status != "VERIFIED"
            or type(identity_summary.source_version) is not int
            or identity_summary.source_version < 1
            or type(identity_summary.birth_date) is not date
        ):
            raise ValueError
        business_date = as_of.astimezone(ZoneInfo("Asia/Shanghai")).date()
        birth_date = identity_summary.birth_date
        age_years = business_date.year - birth_date.year - (
            (business_date.month, business_date.day) < (birth_date.month, birth_date.day)
        )
        symptoms = profile.get("symptoms")
        if type(symptoms) is not list:
            symptoms = []
        symptom_codes = tuple(
            dict.fromkeys(
                approved
                for item in symptoms
                if type(item) is dict
                for approved in (_SEVERE_GLUCOSE_PROFILE_CODES.get(item.get("code")),)
                if approved is not None
            )
        )
        return EvaluationContext(
            age_years=age_years,
            sex=identity_summary.gender,
            pregnancy_status=str(profile.get("pregnancy_status", "UNKNOWN")),
            lactation_status=str(profile.get("lactation_status", "UNKNOWN")),
            ascvd_risk_profile=None,
            acute_symptom_codes=symptom_codes,
        )
    except (AttributeError, TypeError, ValueError):
        raise RuntimeError("RULE_EVALUATION_UNAVAILABLE") from None


def _module_measurement_contexts(
    indicator_codes: set[str],
    indicator_contexts: Mapping[str, str],
    measured_at: Mapping[str, datetime],
) -> dict[str, str]:
    contexts = dict(indicator_contexts)
    if (
        contexts.get("systolic_bp") == contexts.get("diastolic_bp")
        and contexts.get("systolic_bp") is not None
        and measured_at.get("systolic_bp") is not None
        and measured_at.get("systolic_bp") == measured_at.get("diastolic_bp")
    ):
        contexts["blood_pressure"] = contexts["systolic_bp"]
    lipid_codes = {
        "total_cholesterol", "triglycerides", "ldl_cholesterol", "hdl_cholesterol"
    }
    present_lipids = lipid_codes & indicator_codes
    if present_lipids and {contexts.get(code) for code in present_lipids} == {"FASTING_LAB"}:
        contexts["lipids"] = "FASTING_LAB"
    return contexts


async def execute_assessment(
    repository,
    *,
    assessment_id: UUID,
    lease_owner: str,
    identity_authority,
    slice4_secrets: Slice4Secrets | None = None,
    slice5_secrets: Slice5Secrets | None = None,
    confirmation_session_factory=None,
) -> dict[str, Any]:
    claimed = await repository.claim_assessment(assessment_id, lease_owner)
    if claimed is None:
        raise RuntimeError("ASSESSMENT_ALREADY_RUNNING") from None
    await repository.session.commit()
    source = await repository.assessment_input(assessment_id)
    if source is None or source.get("rule_set_code") != RULE_SET_CODE:
        raise RuntimeError("RULE_EVALUATION_UNAVAILABLE") from None
    slice4_box = slice4_secrets or Slice4Secrets()
    slice5_box = slice5_secrets or Slice5Secrets()
    profile = slice4_box.decrypt_profile(
        bytes.fromhex(source["profile_ciphertext"]),
        source["profile_key_id"],
        tenant_public_id=UUID(str(source["tenant_public_id"])),
        subject_member_id=UUID(str(source["subject_member_id"])),
        revision_id=UUID(str(source["profile_revision_id"])),
        identity_source_version=int(source["identity_source_version"]),
    )
    identity_summary = await identity_authority.verified_identity_summary(
        subject_member_id=UUID(str(source["subject_member_id"])),
        service_case_id=UUID(str(source["service_case_id"])),
    )
    if (
        identity_summary.identity_revision_ref
        != UUID(str(source["identity_revision_ref"]))
        or identity_summary.source_version != int(source["identity_source_version"])
        or identity_summary.tenant_public_id != UUID(str(source["tenant_public_id"]))
    ):
        raise RuntimeError("RULE_EVALUATION_UNAVAILABLE") from None
    values: dict[str, object] = {}
    units: dict[str, str] = {}
    contexts: dict[str, str] = {}
    measured_at: dict[str, datetime] = {}
    source_types: set[str] = set()
    for row in source.get("facts", ()):
        original_code = row["indicator_code"]
        code = _INDICATOR_ALIASES.get(original_code, original_code)
        values[code] = slice4_box.decrypt_assembly_fact(
            bytes.fromhex(row["value_ciphertext"]),
            row["value_key_id"],
            tenant_public_id=UUID(str(source["tenant_public_id"])),
            subject_member_id=UUID(str(source["subject_member_id"])),
            service_case_id=UUID(str(source["service_case_id"])),
            assembly_id=UUID(str(source["assembly_id"])),
            fact_ref=UUID(str(row["fact_ref"])),
            indicator_code=original_code,
        )
        units[code] = row["unit"]
        if row.get("measurement_context"):
            contexts[code] = row["measurement_context"]
        if row.get("measured_at"):
            measured_at[code] = row["measured_at"]
        source_types.add(row["source_type"])
    contexts = _module_measurement_contexts(set(values), contexts, measured_at)
    if "bmi" not in values and "height" in values and "weight" in values:
        height = Decimal(str(values["height"]))
        weight = Decimal(str(values["weight"]))
        if height > 0 and weight > 0:
            values["bmi"] = weight / ((height / Decimal("100")) ** 2)
            units["bmi"] = "kg/m2"
    facts = AssessmentFacts.from_strings(values, units=units, measurement_contexts=contexts)
    evaluation = evaluate_cn_adult_baseline_v1(
        facts,
        build_current_evaluation_context(
            identity_summary,
            profile,
            as_of=source["snapshot_created_at"],
        ),
    )
    now = datetime.now(timezone.utc)
    module_rows = []
    for index, item in enumerate(evaluation.module_results, 1):
        fragment_digest, digest_key_id = slice5_box.digest(
            "RULE_FRAGMENT",
            {
                "rule_set": RULE_SET_CODE,
                "module_code": item.module_code,
                "risk_level": item.risk_level,
                "reason_codes": item.reason_codes,
            },
        )
        module_rows.append(
            {
                "module_result_id": _uuid7_from(now, fragment_digest, index),
                "module_code": item.module_code,
                "risk_level": item.risk_level,
                "reason_codes": item.reason_codes,
                "evidence_manifest": tuple({"indicator_code": value} for value in item.evidence_codes),
                "message_codes": item.message_codes,
                "rule_fragment_digest": fragment_digest,
                "digest_key_id": digest_key_id,
            }
        )
    completion_digest, digest_key_id = slice5_box.digest(
        "EVIDENCE", {"assessment_id": assessment_id, "modules": module_rows}
    )
    payload = {
        "assessment_id": assessment_id,
        "expected_version": claimed["version"],
        "overall_risk": evaluation.overall_risk,
        "service_case_id": UUID(str(source["service_case_id"])),
        "subject_member_id": UUID(str(source["subject_member_id"])),
        "tenant_id": int(source["tenant_id"]),
        "module_results": module_rows,
        "trigger_codes": evaluation.high_risk_trigger_codes,
        "task_id": _uuid7_from(now, completion_digest, 11),
        "audit_id": _uuid7_from(now, completion_digest, 12),
        "event_id": _uuid7_from(now, completion_digest, 13),
        "receipt_id": _uuid7_from(now, completion_digest, 14),
        "digest_key_id": digest_key_id,
        "completed_at": now,
        "source_types": tuple(sorted(source_types)),
        "supersedes_assessment_id": source.get("supersedes_assessment_id"),
        "superseded_assessment_version": source.get("superseded_assessment_version"),
        "superseded_dispute_id": source.get("superseded_dispute_id"),
        "superseded_dispute_version": source.get("superseded_dispute_version"),
    }
    response = {
        "assessment_id": assessment_id,
        "status": "COMPLETED",
        "overall_risk": evaluation.overall_risk,
        "version": int(claimed["version"]) + 1,
    }
    request_digest, _ = slice5_box.digest(
        "REQUEST",
        {
            "assessment_id": assessment_id,
            "expected_version": claimed["version"],
            "module_results": module_rows,
            "trigger_codes": evaluation.high_risk_trigger_codes,
        },
    )
    audit_digest, _ = slice5_box.digest(
        "AUDIT", {"action": "ASSESSMENT_COMPLETED", "assessment_id": assessment_id}
    )
    outbox_digest, _ = slice5_box.digest(
        "OUTBOX",
        {
            "event": "ASSESSMENT_COMPLETED",
            "assessment_id": assessment_id,
            "overall_risk": evaluation.overall_risk,
        },
    )
    payload.update(
        {
            "request_digest": request_digest,
            "audit_digest": audit_digest,
            "outbox_digest": outbox_digest,
            "response": response,
        }
    )
    expected = build_assessment_completion_postimage(payload, response=response)
    postimage_digest, _ = slice5_box.digest("REPLAY", expected)
    expected["receipt"]["postimage_digest"] = postimage_digest.hex()
    payload["postimage_digest"] = postimage_digest
    result = await repository.complete_assessment(payload)
    if confirmation_session_factory is None:
        await repository.session.commit()
        return result

    async def confirm() -> CommitOutcome:
        async with confirmation_session_factory() as fresh:
            actual = await type(repository)(fresh).confirm_assessment(assessment_id)
            if actual is None:
                return CommitOutcome.NOT_COMMITTED
            return CommitOutcome.COMMITTED if actual == expected else CommitOutcome.UNKNOWN

    outcome = await commit_with_confirmation(repository.session, confirm=confirm)
    if outcome is not CommitOutcome.COMMITTED:
        raise RuntimeError("COMMIT_OUTCOME_UNKNOWN") from None
    return result


async def transition_high_risk_task(
    repository,
    *,
    task_id: UUID,
    actor_user_id: int,
    actor_role: str,
    expected_version: int,
    action_code: str,
    occurred_at: datetime,
    contact_outcome_code: str | None,
    advice_code: str | None,
    reason_code: str | None,
    idempotency_key: str,
    secrets: Slice5Secrets | None = None,
) -> dict[str, Any]:
    secret_box = secrets or Slice5Secrets()
    request_digest, digest_key_id = secret_box.digest(
        "REQUEST",
        {
            "task_id": task_id,
            "actor_user_id": actor_user_id,
            "actor_role": actor_role,
            "expected_version": expected_version,
            "action_code": action_code,
            "contact_outcome_code": contact_outcome_code,
            "advice_code": advice_code,
            "reason_code": reason_code,
            "occurred_at": occurred_at,
            "idempotency_key": idempotency_key,
        },
    )
    evidence_digest, _ = secret_box.digest(
        "AUDIT",
        {
            "task_id": task_id,
            "actor_user_id": actor_user_id,
            "action_code": action_code,
            "occurred_at": occurred_at,
            "contact_outcome_code": contact_outcome_code,
            "advice_code": advice_code,
            "reason_code": reason_code,
        },
    )
    to_status = {
        "CLAIM": "CLAIMED",
        "ESCALATE": "ESCALATED",
        "REFER": "REFERRED",
        "RESOLVE": "RESOLVED",
    }.get(action_code)
    if to_status is None:
        raise RuntimeError("INVALID_TASK_TRANSITION") from None
    response = {"task_id": task_id, "status": to_status, "version": expected_version + 1}
    outbox_digest, _ = secret_box.digest(
        "OUTBOX",
        {"event": "HIGH_RISK_TASK_TRANSITIONED", "task_id": task_id, "status": to_status},
    )
    postimage_digest, _ = secret_box.digest(
        "REPLAY",
        {
            "response": response,
            "action_id": _uuid7_from(occurred_at, evidence_digest, 1),
            "audit_id": _uuid7_from(occurred_at, evidence_digest, 2),
            "event_id": _uuid7_from(occurred_at, evidence_digest, 3),
            "evidence_digest": evidence_digest,
            "outbox_digest": outbox_digest,
        },
    )
    payload = {
        "task_id": task_id,
        "actor_user_id": actor_user_id,
        "actor_role": actor_role,
        "expected_version": expected_version,
        "action_code": action_code,
        "contact_outcome_code": contact_outcome_code,
        "advice_code": advice_code,
        "reason_code": reason_code,
        "occurred_at": occurred_at,
        "idempotency_key": idempotency_key,
        "action_id": _uuid7_from(occurred_at, evidence_digest, 1),
        "audit_id": _uuid7_from(occurred_at, evidence_digest, 2),
        "event_id": _uuid7_from(occurred_at, evidence_digest, 3),
        "receipt_id": _uuid7_from(occurred_at, evidence_digest, 4),
        "request_digest": request_digest,
        "evidence_digest": evidence_digest,
        "outbox_digest": outbox_digest,
        "postimage_digest": postimage_digest,
        "response": response,
        "digest_key_id": digest_key_id,
    }
    result = await repository.transition_high_risk_task(payload)
    await repository.session.commit()
    return result


async def govern_rule_set(
    repository,
    *,
    operation: str,
    actor_user_id: int,
    actor_role: str,
    idempotency_key: str,
    rule_set_version_id: UUID | None = None,
    expected_version: int | None = None,
    details: Mapping[str, object] | None = None,
    occurred_at: datetime | None = None,
    secrets: Slice5Secrets | None = None,
    confirmation_session_factory=None,
) -> dict[str, Any]:
    if operation not in {
        "CREATE", "UPDATE_DRAFT", "SUBMIT", "REVIEW_APPROVE", "REVIEW_CORRECTION",
        "PUBLISH", "SUSPEND", "RESUME", "RETIRE",
    }:
        raise RuntimeError("INVALID_REQUEST") from None
    now = occurred_at or datetime.now(timezone.utc)
    if now.utcoffset() is None or actor_user_id < 1 or len(idempotency_key) < 8:
        raise RuntimeError("INVALID_REQUEST") from None
    detail_payload = dict(details or {})
    response_override = detail_payload.pop("_response", None)
    secret_box = secrets or Slice5Secrets()
    request_digest, digest_key_id = secret_box.digest(
        "REQUEST",
        {
            "operation": operation,
            "actor_user_id": actor_user_id,
            "actor_role": actor_role,
            "rule_set_version_id": rule_set_version_id,
            "expected_version": expected_version,
            "details": detail_payload,
            "idempotency_key": idempotency_key,
        },
    )
    target_id = rule_set_version_id or _stable_uuid7(request_digest, 31)
    audit_id = _stable_uuid7(request_digest, 32)
    event_id = _stable_uuid7(request_digest, 33)
    receipt_id = _stable_uuid7(request_digest, 34)
    evidence_digest, _ = secret_box.digest(
        "AUDIT", {"operation": operation, "target_id": target_id, "actor": actor_user_id}
    )
    outbox_digest, _ = secret_box.digest(
        "OUTBOX", {"event": f"RULE_{operation}", "target_id": target_id}
    )
    expected_status = {
        "CREATE": "DRAFT",
        "UPDATE_DRAFT": "DRAFT",
        "SUBMIT": "IN_REVIEW",
        "REVIEW_APPROVE": "IN_REVIEW",
        "REVIEW_CORRECTION": "NEEDS_CORRECTION",
        "PUBLISH": "PUBLISHED",
        "SUSPEND": "SUSPENDED",
        "RESUME": "PUBLISHED",
        "RETIRE": "RETIRED",
    }[operation]
    if response_override is not None:
        response = dict(response_override)
    elif operation == "CREATE":
        response = {
            "rule_set_version_id": target_id,
            "version_no": int(detail_payload["version_no"]),
            "status": "DRAFT",
            "module_metadata": MODULE_CODES,
            "author": actor_user_id,
            "reviewer": None,
            "approval_state": "PENDING",
            "effective_from": None,
            "suspended_at": None,
            "retired_at": None,
            "version": 1,
            "rule_set_code": RULE_SET_CODE,
            "typed_rule_payload": detail_payload["typed_rule_payload"],
            "approval_evidence_ref": detail_payload.get("approval_evidence_ref"),
        }
    elif operation == "UPDATE_DRAFT":
        response = {
            "rule_set_version_id": target_id,
            "status": detail_payload["current_status"],
            "version": int(expected_version or 0) + 1,
            "rule_set_code": RULE_SET_CODE,
            "typed_rule_payload": detail_payload["typed_rule_payload"],
            "approval_evidence_ref": detail_payload.get("approval_evidence_ref"),
        }
    else:
        response = {
            "rule_set_version_id": target_id,
            "status": expected_status,
            "version": int(expected_version or 0) + 1,
        }
    postimage_digest, _ = secret_box.digest(
        "REPLAY",
        {
            "response": response,
            "audit_id": audit_id,
            "event_id": event_id,
            "evidence_digest": evidence_digest,
            "outbox_digest": outbox_digest,
        },
    )
    payload: dict[str, object] = {
        "rule_set_version_id": target_id,
        "rule_set_code": RULE_SET_CODE,
        "actor_user_id": actor_user_id,
        "actor_role": actor_role,
        "expected_version": expected_version,
        "idempotency_key": idempotency_key,
        "request_digest": request_digest,
        "audit_id": audit_id,
        "event_id": event_id,
        "receipt_id": receipt_id,
        "evidence_digest": evidence_digest,
        "outbox_digest": outbox_digest,
        "postimage_digest": postimage_digest,
        "digest_key_id": digest_key_id,
        "response": response,
        "created_at": now,
    }
    payload.update(detail_payload)
    result = await repository.govern_rule_set(operation, payload)
    if confirmation_session_factory is None:
        await repository.session.commit()
        return result

    confirmation_payload = {
        "operation": operation,
        "rule_set_version_id": target_id,
        "actor_user_id": actor_user_id,
        "actor_role": payload["actor_role"],
        "expected_version": expected_version,
        "idempotency_key": idempotency_key,
        "request_digest": request_digest,
        "audit_id": audit_id,
        "event_id": event_id,
        "receipt_id": payload["receipt_id"],
        "evidence_digest": evidence_digest,
        "outbox_digest": outbox_digest,
        "postimage_digest": postimage_digest,
        "digest_key_id": payload["digest_key_id"],
        "response": response,
        "created_at": payload["created_at"],
    }
    if operation == "CREATE":
        confirmation_payload.update(
            {
                "rule_set_code": payload["rule_set_code"],
                "version_no": payload["version_no"],
                "author_user_id": payload["actor_user_id"],
                "typed_rule_payload": payload["typed_rule_payload"],
                "content_digest": payload["content_digest"],
                "approval_evidence_ref": payload.get("approval_evidence_ref"),
            }
        )

    async def confirm() -> CommitOutcome:
        async with confirmation_session_factory() as fresh:
            value = await type(repository)(fresh).confirm_rule_governance(confirmation_payload)
            try:
                return CommitOutcome(value)
            except ValueError:
                return CommitOutcome.UNKNOWN

    outcome = await commit_with_confirmation(repository.session, confirm=confirm)
    if outcome is not CommitOutcome.COMMITTED:
        raise RuntimeError("COMMIT_OUTCOME_UNKNOWN") from None
    return result


async def raise_assessment_dispute(
    repository,
    *,
    assessment_id: UUID,
    actor_user_id: int,
    actor_role: str,
    actor_context: str,
    expected_version: int,
    reason_code: str,
    idempotency_key: str,
    occurred_at: datetime,
    confirmation_session_factory=None,
    secrets: Slice5Secrets | None = None,
) -> dict[str, Any]:
    if occurred_at.utcoffset() is None:
        raise RuntimeError("INVALID_REQUEST") from None
    secret_box = secrets or Slice5Secrets()
    request_digest, digest_key_id = secret_box.digest(
        "REQUEST",
        {
            "assessment_id": assessment_id,
            "actor_user_id": actor_user_id,
            "actor_context": actor_context,
            "expected_version": expected_version,
            "reason_code": reason_code,
            "idempotency_key": idempotency_key,
        },
    )
    evidence_digest, _ = secret_box.digest(
        "AUDIT",
        {
            "assessment_id": assessment_id,
            "actor_user_id": actor_user_id,
            "reason_code": reason_code,
            "occurred_at": occurred_at,
        },
    )
    outbox_digest, _ = secret_box.digest(
        "OUTBOX",
        {
            "event": "ASSESSMENT_DISPUTED",
            "assessment_id": assessment_id,
            "reason_code": reason_code,
        },
    )
    dispute_id = _uuid7_from(occurred_at, evidence_digest, 21)
    response = {
        "dispute_id": dispute_id,
        "assessment_id": assessment_id,
        "status": "OPEN",
        "reason_code": reason_code,
        "created_at": occurred_at,
        "version": 1,
    }
    payload = {
        "assessment_id": assessment_id,
        "actor_user_id": actor_user_id,
        "actor_role": actor_role,
        "actor_context": actor_context,
        "expected_version": expected_version,
        "reason_code": reason_code,
        "idempotency_key": idempotency_key,
        "request_digest": request_digest,
        "evidence_digest": evidence_digest,
        "digest_key_id": digest_key_id,
        "dispute_id": dispute_id,
        "audit_id": _uuid7_from(occurred_at, evidence_digest, 22),
        "event_id": _uuid7_from(occurred_at, evidence_digest, 23),
        "receipt_id": _uuid7_from(occurred_at, evidence_digest, 24),
        "created_at": occurred_at,
        "outbox_digest": outbox_digest,
        "response": response,
    }
    expected = {
        "assessment": {
            "assessment_id": str(assessment_id),
            "status": "UNDER_REVIEW",
            "version": expected_version + 1,
        },
        "dispute": {
            "dispute_id": str(dispute_id),
            "assessment_id": str(assessment_id),
            "raised_by": actor_user_id,
            "actor_context": actor_context,
            "reason_code": reason_code,
            "status": "OPEN",
            "created_at_us": _timestamp_us(occurred_at),
            "version": 1,
        },
        "audit": {
            "audit_id": str(payload["audit_id"]),
            "action": "ASSESSMENT_DISPUTED",
            "actor_user_id": actor_user_id,
            "actor_role": actor_role,
            "target_type": "ASSESSMENT",
            "target_id": str(assessment_id),
            "evidence_digest": evidence_digest.hex(),
            "occurred_at_us": _timestamp_us(occurred_at),
        },
        "outbox": {
            "event_id": str(payload["event_id"]),
            "aggregate_type": "ASSESSMENT",
            "aggregate_ref": str(assessment_id),
            "event_type": "ASSESSMENT_DISPUTED",
            "payload_digest": outbox_digest.hex(),
            "status": "PENDING",
            "attempts": 0,
            "created_at_us": _timestamp_us(occurred_at),
        },
        "receipt": {
            "receipt_id": str(payload["receipt_id"]),
            "actor_scope": str(actor_user_id),
            "operation": "ASSESSMENT_DISPUTE",
            "target_id": str(assessment_id),
            "idempotency_key": idempotency_key,
            "request_digest": request_digest.hex(),
            "response": _json_value(response),
            "response_key_id": digest_key_id,
            "created_at_us": _timestamp_us(occurred_at),
        },
    }
    postimage_digest, _ = secret_box.digest("REPLAY", expected)
    expected["receipt"]["postimage_digest"] = postimage_digest.hex()
    payload["postimage_digest"] = postimage_digest
    result = await repository.raise_dispute(payload)
    if confirmation_session_factory is None:
        await repository.session.commit()
        return result

    async def confirm() -> CommitOutcome:
        async with confirmation_session_factory() as fresh:
            actual = await type(repository)(fresh).confirm_assessment(assessment_id)
            if actual is None:
                return CommitOutcome.NOT_COMMITTED
            return CommitOutcome.COMMITTED if actual == expected else CommitOutcome.UNKNOWN

    outcome = await commit_with_confirmation(repository.session, confirm=confirm)
    if outcome is not CommitOutcome.COMMITTED:
        raise RuntimeError("COMMIT_OUTCOME_UNKNOWN") from None
    return result
