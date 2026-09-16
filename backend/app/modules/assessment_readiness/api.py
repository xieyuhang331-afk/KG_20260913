from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from app.core.database import (
    get_slice5_rule_governance_writer_session,
    get_slice5_session_factory,
)
from app.core.security import CurrentUser, get_current_user_from_jwt
from app.modules.health_assessment.service import HealthAssessmentError

from .policy_governance import govern_readiness_policy
from .repository import AssessmentReadinessRepository, ReadinessPolicyRepositoryError
from .schemas import (
    ReadinessPolicyCreateRequest,
    ReadinessPolicyDraftUpdateRequest,
    ReadinessPolicyGovernanceRequest,
    ReadinessPolicyReviewRequest,
    ReadinessPolicyVersionDTO,
    ReadinessPolicyVersionRequest,
)

router = APIRouter(prefix="/api/v1/platform/assessment-readiness-policies", tags=["platform"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]
_ACTOR_DEPENDENCY = Depends(get_current_user_from_jwt)
_WRITER_DEPENDENCY = Depends(get_slice5_rule_governance_writer_session)


def _forbidden() -> HTTPException:
    return HTTPException(
        status_code=403,
        detail={"code": "READINESS_POLICY_GOVERNANCE_FORBIDDEN", "message": "request rejected"},
        headers={"Cache-Control": "no-store"},
    )


def _require(actor: CurrentUser, roles: set[str]) -> None:
    if actor.role not in roles:
        raise _forbidden()


def _dto(row: Mapping[str, object]) -> ReadinessPolicyVersionDTO:
    return ReadinessPolicyVersionDTO(
        policy_version_id=row["policy_version_id"],
        version_no=row["version_no"],
        status=row["status"],
        content={
            "required_profile_sections": row["required_profile_sections"],
            "required_indicators": row["required_indicators"],
            "allowed_states": row["allowed_states"],
            "projection_version": row["projection_version"],
            "rule_version": row["rule_version"],
        },
        approval_evidence_ref=row.get("approval_evidence_ref"),
        approval_package_digest=row.get("approval_package_digest"),
        medical_approval_verified=bool(row.get("medical_approval_verified", False)),
        policy_digest=row["policy_digest"],
        effective_from=row.get("effective_from"),
        suspended_at=row.get("suspended_at"),
        retired_at=row.get("retired_at"),
        row_version=row["row_version"],
    )


async def _run(
    operation: str,
    actor: CurrentUser,
    writer,
    idempotency_key: str,
    **values,
) -> ReadinessPolicyVersionDTO:
    try:
        factory = await get_slice5_session_factory("rule_governance_writer")
        row = await govern_readiness_policy(
            AssessmentReadinessRepository(writer),
            operation=operation,
            actor_user_id=actor.id,
            actor_role=actor.role,
            idempotency_key=idempotency_key,
            confirmation_session_factory=factory,
            **values,
        )
        return _dto(row)
    except (HealthAssessmentError, ReadinessPolicyRepositoryError) as exc:
        code = str(exc)
        status = 403 if code == "READINESS_POLICY_GOVERNANCE_FORBIDDEN" else 409
        if code in {"READINESS_POLICY_INVALID", "READINESS_POLICY_REASON_INVALID"}:
            status = 422
        if code in {"COMMIT_OUTCOME_UNKNOWN", "DEPENDENCY_UNAVAILABLE"}:
            status = 503
        raise HTTPException(
            status_code=status,
            detail={"code": code, "message": "request rejected"},
            headers={"Cache-Control": "no-store"},
        ) from None


@router.post("", response_model=ReadinessPolicyVersionDTO, status_code=201)
async def create_policy(
    payload: ReadinessPolicyCreateRequest,
    response: Response,
    idempotency_key: IdempotencyKey,
    actor: CurrentUser = _ACTOR_DEPENDENCY,
    writer=_WRITER_DEPENDENCY,
) -> ReadinessPolicyVersionDTO:
    _require(actor, {"expert"})
    response.headers["Cache-Control"] = "no-store"
    return await _run(
        "CREATE",
        actor,
        writer,
        idempotency_key,
        version_no=payload.version_no,
        content=payload.content.model_dump(mode="json"),
        approval_evidence_ref=payload.approval_evidence_ref,
        approval_package_digest=payload.approval_package_digest,
    )


@router.patch("/{policy_version_id}/draft", response_model=ReadinessPolicyVersionDTO)
async def update_policy(
    policy_version_id: UUID,
    payload: ReadinessPolicyDraftUpdateRequest,
    response: Response,
    idempotency_key: IdempotencyKey,
    actor: CurrentUser = _ACTOR_DEPENDENCY,
    writer=_WRITER_DEPENDENCY,
) -> ReadinessPolicyVersionDTO:
    _require(actor, {"expert"})
    response.headers["Cache-Control"] = "no-store"
    return await _run(
        "UPDATE_DRAFT",
        actor,
        writer,
        idempotency_key,
        policy_version_id=policy_version_id,
        expected_version=payload.expected_version,
        content=payload.content.model_dump(mode="json"),
        approval_evidence_ref=payload.approval_evidence_ref,
        approval_package_digest=payload.approval_package_digest,
    )


@router.post("/{policy_version_id}/submit", response_model=ReadinessPolicyVersionDTO)
async def submit_policy(
    policy_version_id: UUID,
    payload: ReadinessPolicyVersionRequest,
    response: Response,
    idempotency_key: IdempotencyKey,
    actor: CurrentUser = _ACTOR_DEPENDENCY,
    writer=_WRITER_DEPENDENCY,
) -> ReadinessPolicyVersionDTO:
    _require(actor, {"expert"})
    response.headers["Cache-Control"] = "no-store"
    return await _run(
        "SUBMIT", actor, writer, idempotency_key,
        policy_version_id=policy_version_id, expected_version=payload.expected_version,
    )


@router.post("/{policy_version_id}/review", response_model=ReadinessPolicyVersionDTO)
async def review_policy(
    policy_version_id: UUID,
    payload: ReadinessPolicyReviewRequest,
    response: Response,
    idempotency_key: IdempotencyKey,
    actor: CurrentUser = _ACTOR_DEPENDENCY,
    writer=_WRITER_DEPENDENCY,
) -> ReadinessPolicyVersionDTO:
    _require(actor, {"expert"})
    response.headers["Cache-Control"] = "no-store"
    return await _run(
        "REVIEW_APPROVE" if payload.decision == "APPROVE" else "REVIEW_CORRECTION",
        actor,
        writer,
        idempotency_key,
        policy_version_id=policy_version_id,
        expected_version=payload.expected_version,
        reason_code=payload.reason_code,
    )


async def _admin_action(
    operation: str,
    policy_version_id: UUID,
    payload: ReadinessPolicyGovernanceRequest,
    response: Response,
    idempotency_key: str,
    actor: CurrentUser,
    writer,
) -> ReadinessPolicyVersionDTO:
    _require(actor, {"sys_admin", "super_admin"})
    response.headers["Cache-Control"] = "no-store"
    if operation in {"PUBLISH", "RESUME"}:
        raise HTTPException(
            status_code=409,
            detail={"code": "POLICY_MEDICAL_APPROVAL_REQUIRED", "message": "request rejected"},
            headers={"Cache-Control": "no-store"},
        )
    return await _run(
        operation,
        actor,
        writer,
        idempotency_key,
        policy_version_id=policy_version_id,
        expected_version=payload.expected_version,
        reason_code=payload.reason_code,
    )


def _admin_route(operation: str):
    async def endpoint(
        policy_version_id: UUID,
        payload: ReadinessPolicyGovernanceRequest,
        response: Response,
        idempotency_key: IdempotencyKey,
        actor: CurrentUser = _ACTOR_DEPENDENCY,
        writer=_WRITER_DEPENDENCY,
    ) -> ReadinessPolicyVersionDTO:
        return await _admin_action(
            operation, policy_version_id, payload, response, idempotency_key, actor, writer
        )

    endpoint.__name__ = f"{operation.lower()}_readiness_policy"
    return endpoint


for _operation in ("PUBLISH", "SUSPEND", "RESUME", "RETIRE"):
    router.post(
        f"/{{policy_version_id}}/{_operation.lower()}",
        response_model=ReadinessPolicyVersionDTO,
    )(_admin_route(_operation))


@router.get("", response_model=tuple[ReadinessPolicyVersionDTO, ...])
async def list_policies(
    response: Response,
    limit: int = Query(50, ge=1, le=100),
    actor: CurrentUser = _ACTOR_DEPENDENCY,
    writer=_WRITER_DEPENDENCY,
) -> tuple[ReadinessPolicyVersionDTO, ...]:
    _require(actor, {"expert", "sys_admin", "super_admin"})
    response.headers["Cache-Control"] = "no-store"
    return tuple(_dto(row) for row in await AssessmentReadinessRepository(writer).readiness_policy_page(limit))


@router.get("/{policy_version_id}", response_model=ReadinessPolicyVersionDTO)
async def policy_detail(
    policy_version_id: UUID,
    response: Response,
    actor: CurrentUser = _ACTOR_DEPENDENCY,
    writer=_WRITER_DEPENDENCY,
) -> ReadinessPolicyVersionDTO:
    _require(actor, {"expert", "sys_admin", "super_admin"})
    response.headers["Cache-Control"] = "no-store"
    row = await AssessmentReadinessRepository(writer).readiness_policy_detail(policy_version_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "not found"},
            headers={"Cache-Control": "no-store"},
        )
    return _dto(row)
