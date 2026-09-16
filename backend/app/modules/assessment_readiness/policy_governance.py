from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from app.core.uuid_generator import Uuid7Generator
from app.modules.health_assessment.service import (
    CommitOutcome,
    HealthAssessmentError,
    Slice5Secrets,
    commit_with_confirmation,
)

_STATUS_BY_OPERATION = {
    "CREATE": "DRAFT",
    "UPDATE_DRAFT": "DRAFT",
    "SUBMIT": "IN_REVIEW",
    "REVIEW_APPROVE": "APPROVED",
    "REVIEW_CORRECTION": "NEEDS_CORRECTION",
    "PUBLISH": "PUBLISHED",
    "SUSPEND": "SUSPENDED",
    "RESUME": "PUBLISHED",
    "RETIRE": "RETIRED",
}


def _json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def policy_content(value: Mapping[str, object]) -> dict[str, object]:
    return {
        "required_profile_sections": list(value["required_profile_sections"]),
        "required_indicators": list(value["required_indicators"]),
        "allowed_states": list(value["allowed_states"]),
        "projection_version": int(value["projection_version"]),
        "rule_version": str(value["rule_version"]),
    }


async def govern_readiness_policy(
    repository,
    *,
    operation: str,
    actor_user_id: int,
    actor_role: str,
    idempotency_key: str,
    policy_version_id: UUID | None = None,
    expected_version: int | None = None,
    content: Mapping[str, object] | None = None,
    version_no: int | None = None,
    approval_evidence_ref: str | None = None,
    approval_package_digest: str | None = None,
    reason_code: str | None = None,
    confirmation_session_factory=None,
    secrets: Slice5Secrets | None = None,
) -> dict:
    if operation not in _STATUS_BY_OPERATION or actor_user_id < 1 or len(idempotency_key) < 8:
        raise HealthAssessmentError("INVALID_REQUEST") from None
    occurred_at = datetime.now(UTC)
    normalized_content = policy_content(content) if content is not None else None
    policy_digest = (
        hashlib.sha256(_json(normalized_content)).hexdigest()
        if normalized_content is not None
        else None
    )
    digest_box = secrets or Slice5Secrets()
    request_digest, digest_key_id = digest_box.digest(
        "REQUEST",
        {
            "operation": operation,
            "actor_user_id": actor_user_id,
            "actor_role": actor_role,
            "idempotency_key": idempotency_key,
            "policy_version_id": policy_version_id,
            "expected_version": expected_version,
            "version_no": version_no,
            "content": normalized_content,
            "approval_evidence_ref": approval_evidence_ref,
            "approval_package_digest": approval_package_digest,
            "reason_code": reason_code,
        },
    )
    target_id = policy_version_id or Uuid7Generator().generate()
    operation_receipt_id = Uuid7Generator().generate()
    audit_id = Uuid7Generator().generate()
    idempotency_uuid = UUID(
        bytes=hashlib.sha256(
            b"readiness-policy-idempotency-v1\0"
            + str(actor_user_id).encode("ascii")
            + b"\0"
            + operation.encode("ascii")
            + b"\0"
            + idempotency_key.encode("utf-8")
        ).digest()[:16]
    )
    payload: dict[str, object] = {
        "actor_user_id": actor_user_id,
        "actor_role": actor_role,
        "policy_version_id": str(target_id),
        "expected_version": expected_version,
        "version_no": version_no,
        "content": normalized_content,
        "policy_digest": policy_digest,
        "approval_evidence_ref": approval_evidence_ref,
        "approval_package_digest": approval_package_digest,
        "reason_code": reason_code,
        "idempotency_key": str(idempotency_uuid),
        "request_digest": request_digest.hex(),
        "digest_key_id": digest_key_id,
        "operation_receipt_id": str(operation_receipt_id),
        "audit_id": str(audit_id),
        "occurred_at": occurred_at.isoformat(),
    }
    result = await repository.govern_readiness_policy(operation, payload)
    if confirmation_session_factory is None:
        await repository._session.commit()
        return result

    confirmation = dict(payload)
    confirmation["operation"] = operation
    confirmation["expected_status"] = _STATUS_BY_OPERATION[operation]
    confirmation["policy_version_id"] = result["policy_version_id"]
    confirmation["operation_receipt_id"] = result["operation_receipt_id"]
    confirmation["audit_id"] = result["_audit_id"]
    confirmation["policy_preimage"] = result["_policy_preimage"]
    confirmation["preimage_digest"] = result["preimage_digest"]
    confirmation["postimage_digest"] = result["postimage_digest"]

    async def confirm() -> CommitOutcome:
        async with confirmation_session_factory() as session:
            value = await type(repository)(session).confirm_readiness_policy(confirmation)
            try:
                return CommitOutcome(value)
            except ValueError:
                return CommitOutcome.UNKNOWN

    outcome = await commit_with_confirmation(repository._session, confirm=confirm)
    if outcome is not CommitOutcome.COMMITTED:
        raise HealthAssessmentError("COMMIT_OUTCOME_UNKNOWN") from None
    return result
