from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Annotated, Mapping
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.core.database import (
    get_slice5_assessment_writer_session,
    get_slice5_clinical_reader_session,
    get_slice5_oversight_reader_session,
    get_slice5_risk_workflow_writer_session,
    get_slice5_rule_governance_writer_session,
    get_slice5_session_factory,
)
from app.core.security import CurrentUser, get_current_user_from_jwt
from app.core.uuid_generator import Uuid7Generator

from .domain import MODULE_CODES
from .evaluator import DEFERRED_RULE_IDS, INCLUDED_RULE_IDS, RULE_SET_CODE
from .repository import HealthAssessmentRepository
from .schemas import (
    AssessmentDetailDTO,
    AssessmentDisputeRequest,
    AssessmentPageDTO,
    AssessmentStartRequest,
    AssessmentSummaryDTO,
    BlockingDTO,
    DisputeDTO,
    HighRiskTaskActionRequest,
    HighRiskTaskDTO,
    HighRiskTaskPageDTO,
    InputEvidenceDTO,
    ModuleResultDTO,
    RuleGovernanceRequest,
    RulePublishRequest,
    RuleReviewRequest,
    RuleSetCreateRequest,
    RuleSetPageDTO,
    RuleSetVersionDTO,
    RuleSetVersionDetailDTO,
    UuidV7,
    VersionRequest,
)
from .service import (
    Slice5Secrets,
    govern_rule_set,
    raise_assessment_dispute,
    start_assessment,
    transition_high_risk_task,
)


SLICE5_VALIDATION_STATUS = 422
_STATUS = {
    "AUTHENTICATION_REQUIRED": 401,
    "ACTOR_CURRENTNESS_FORBIDDEN": 403,
    "PRIMARY_THERAPIST_REQUIRED": 403,
    "THERAPIST_SCOPE_FORBIDDEN": 403,
    "INSTITUTION_SCOPE_FORBIDDEN": 403,
    "PROXY_PERMISSION_FORBIDDEN": 403,
    "RULE_GOVERNANCE_FORBIDDEN": 403,
    "RULE_AUTHOR_REVIEWER_CONFLICT": 403,
    "ASSESSMENT_NOT_FOUND": 404,
    "HIGH_RISK_TASK_NOT_FOUND": 404,
    "RULE_SET_NOT_FOUND": 404,
    "VERSION_CONFLICT": 409,
    "IDEMPOTENCY_CONFLICT": 409,
    "ASSESSMENT_NOT_READY": 409,
    "ASSESSMENT_ALREADY_RUNNING": 409,
    "ASSEMBLY_STALE": 409,
    "RULE_SET_NOT_ACTIVE": 409,
    "RULE_STATE_CONFLICT": 409,
    "INVALID_TASK_TRANSITION": 409,
    "HIGH_RISK_ACTION_INVALID": 409,
    "STATE_CONFLICT": 409,
    "INVALID_REQUEST": 422,
    "DEPENDENCY_UNAVAILABLE": 503,
    "COMMIT_OUTCOME_UNKNOWN": 503,
}

_DATABASE_ERROR_MAP = {
    "SLICE5_COMMIT_OUTCOME_UNKNOWN": "COMMIT_OUTCOME_UNKNOWN",
    "SLICE5_INVALID_PAYLOAD": "INVALID_REQUEST",
    "SLICE5_FOUR_MODULES_REQUIRED": "INVALID_REQUEST",
    "SLICE5_OVERALL_RISK_MISMATCH": "INVALID_REQUEST",
    "SLICE5_TRIGGER_SET_MISMATCH": "INVALID_REQUEST",
    "SLICE5_RESPONSE_MISMATCH": "COMMIT_OUTCOME_UNKNOWN",
    "RULE_OPERATION_INVALID": "INVALID_REQUEST",
    "RULE_SET_INVALID": "INVALID_REQUEST",
}


def _safe_error_code(error: Exception) -> str:
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        message = str(current)
        for line in message.splitlines():
            candidate = line.strip().split()[0].strip(":") if line.strip() else ""
            if candidate in _STATUS:
                return candidate
            mapped = _DATABASE_ERROR_MAP.get(candidate)
            if mapped is not None:
                return mapped
        current = current.__cause__ or current.__context__
    return "DEPENDENCY_UNAVAILABLE"

_AUTH = ("AUTHENTICATION_REQUIRED",)
_COMMON = ("INVALID_REQUEST", "DEPENDENCY_UNAVAILABLE")
_THERAPIST = _AUTH + ("ACTOR_CURRENTNESS_FORBIDDEN", "THERAPIST_SCOPE_FORBIDDEN")
_INSTITUTION = _AUTH + ("ACTOR_CURRENTNESS_FORBIDDEN", "INSTITUTION_SCOPE_FORBIDDEN")
_FAMILY = _AUTH + ("ACTOR_CURRENTNESS_FORBIDDEN", "PROXY_PERMISSION_FORBIDDEN")
_PLATFORM = _AUTH + ("ACTOR_CURRENTNESS_FORBIDDEN", "RULE_GOVERNANCE_FORBIDDEN")


def _route_codes(prefix: tuple[str, ...], *extra: str) -> tuple[str, ...]:
    return prefix + tuple(extra) + _COMMON


SLICE5_ROUTE_ERROR_CODES = {
    ("POST", "/api/v1/therapist/service-cases/{case_id}/assessments"): _route_codes(
        _THERAPIST, "PRIMARY_THERAPIST_REQUIRED", "ASSESSMENT_NOT_READY", "RULE_SET_NOT_ACTIVE",
        "ASSESSMENT_ALREADY_RUNNING", "ASSEMBLY_STALE", "VERSION_CONFLICT",
        "IDEMPOTENCY_CONFLICT", "COMMIT_OUTCOME_UNKNOWN"
    ),
    ("GET", "/api/v1/therapist/service-cases/{case_id}/assessments"): _route_codes(_THERAPIST),
    ("GET", "/api/v1/therapist/service-cases/{case_id}/assessments/{assessment_id}"): _route_codes(_THERAPIST, "ASSESSMENT_NOT_FOUND"),
    ("POST", "/api/v1/therapist/service-cases/{case_id}/assessments/{assessment_id}/disputes"): _route_codes(_THERAPIST, "ASSESSMENT_NOT_FOUND", "VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT"),
    ("GET", "/api/v1/therapist/high-risk-tasks"): _route_codes(_THERAPIST),
    ("GET", "/api/v1/therapist/high-risk-tasks/{task_id}"): _route_codes(_THERAPIST, "HIGH_RISK_TASK_NOT_FOUND"),
    ("POST", "/api/v1/therapist/high-risk-tasks/{task_id}/actions"): _route_codes(_THERAPIST, "HIGH_RISK_TASK_NOT_FOUND", "VERSION_CONFLICT", "STATE_CONFLICT", "INVALID_TASK_TRANSITION", "HIGH_RISK_ACTION_INVALID", "IDEMPOTENCY_CONFLICT"),
    ("GET", "/api/v1/institution/high-risk-tasks"): _route_codes(_INSTITUTION),
    ("GET", "/api/v1/institution/high-risk-tasks/{task_id}"): _route_codes(_INSTITUTION, "HIGH_RISK_TASK_NOT_FOUND"),
    ("POST", "/api/v1/institution/high-risk-tasks/{task_id}/actions"): _route_codes(_INSTITUTION, "HIGH_RISK_TASK_NOT_FOUND", "VERSION_CONFLICT", "STATE_CONFLICT", "INVALID_TASK_TRANSITION", "HIGH_RISK_ACTION_INVALID", "IDEMPOTENCY_CONFLICT"),
    ("GET", "/api/v1/institution/service-cases/{case_id}/assessments"): _route_codes(_INSTITUTION),
    ("GET", "/api/v1/family/assessments"): _route_codes(_FAMILY),
    ("GET", "/api/v1/family/assessments/{assessment_id}"): _route_codes(_FAMILY, "ASSESSMENT_NOT_FOUND"),
    ("POST", "/api/v1/family/assessments/{assessment_id}/disputes"): _route_codes(_FAMILY, "ASSESSMENT_NOT_FOUND", "VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT"),
    ("GET", "/api/v1/family/proxy-enrollments/{enrollment_id}/assessments"): _route_codes(_FAMILY),
    ("GET", "/api/v1/family/proxy-enrollments/{enrollment_id}/assessments/{assessment_id}"): _route_codes(_FAMILY, "ASSESSMENT_NOT_FOUND"),
    ("POST", "/api/v1/family/proxy-enrollments/{enrollment_id}/assessments/{assessment_id}/disputes"): _route_codes(_FAMILY, "ASSESSMENT_NOT_FOUND", "VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT"),
    ("POST", "/api/v1/platform/assessment-rule-sets"): _route_codes(_PLATFORM, "IDEMPOTENCY_CONFLICT"),
    ("GET", "/api/v1/platform/assessment-rule-sets"): _route_codes(_PLATFORM),
    ("GET", "/api/v1/platform/assessment-rule-sets/{version_id}"): _route_codes(_PLATFORM, "RULE_SET_NOT_FOUND"),
    ("POST", "/api/v1/platform/assessment-rule-sets/{version_id}/submit"): _route_codes(_PLATFORM, "RULE_SET_NOT_FOUND", "VERSION_CONFLICT", "RULE_STATE_CONFLICT", "IDEMPOTENCY_CONFLICT"),
    ("POST", "/api/v1/platform/assessment-rule-sets/{version_id}/review"): _route_codes(_PLATFORM, "RULE_SET_NOT_FOUND", "VERSION_CONFLICT", "RULE_STATE_CONFLICT", "RULE_AUTHOR_REVIEWER_CONFLICT", "IDEMPOTENCY_CONFLICT"),
    ("POST", "/api/v1/platform/assessment-rule-sets/{version_id}/publish"): _route_codes(_PLATFORM, "RULE_SET_NOT_FOUND", "VERSION_CONFLICT", "RULE_STATE_CONFLICT", "IDEMPOTENCY_CONFLICT"),
    ("POST", "/api/v1/platform/assessment-rule-sets/{version_id}/suspend"): _route_codes(_PLATFORM, "RULE_SET_NOT_FOUND", "VERSION_CONFLICT", "RULE_STATE_CONFLICT", "IDEMPOTENCY_CONFLICT"),
    ("POST", "/api/v1/platform/assessment-rule-sets/{version_id}/resume"): _route_codes(_PLATFORM, "RULE_SET_NOT_FOUND", "VERSION_CONFLICT", "RULE_STATE_CONFLICT", "IDEMPOTENCY_CONFLICT"),
    ("POST", "/api/v1/platform/assessment-rule-sets/{version_id}/retire"): _route_codes(_PLATFORM, "RULE_SET_NOT_FOUND", "VERSION_CONFLICT", "RULE_STATE_CONFLICT", "IDEMPOTENCY_CONFLICT"),
    ("GET", "/api/v1/platform/high-risk-tasks"): _route_codes(_PLATFORM),
    ("GET", "/api/v1/platform/high-risk-tasks/{task_id}"): _route_codes(_PLATFORM, "HIGH_RISK_TASK_NOT_FOUND"),
}


class Slice5Route(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            key = (next(iter(self.methods)), self.path)
            try:
                return await original(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=SLICE5_VALIDATION_STATUS,
                    content={"code": "INVALID_REQUEST", "message": "request rejected"},
                )
            except HTTPException as exc:
                detail = exc.detail
                code = detail.get("code") if isinstance(detail, dict) else detail if isinstance(detail, str) else None
                if exc.status_code == 401:
                    code = "AUTHENTICATION_REQUIRED"
                if code not in SLICE5_ROUTE_ERROR_CODES[key]:
                    code = "INVALID_REQUEST" if exc.status_code < 500 else "DEPENDENCY_UNAVAILABLE"
                return JSONResponse(
                    status_code=_STATUS[code],
                    content={"code": code, "message": "request rejected"},
                )
            except Exception as exc:
                code = _safe_error_code(exc)
                if code not in SLICE5_ROUTE_ERROR_CODES[key]:
                    code = "DEPENDENCY_UNAVAILABLE"
                return JSONResponse(
                    status_code=_STATUS[code],
                    content={"code": code, "message": "request rejected"},
                )

        return handler


def _error(code: str) -> HTTPException:
    return HTTPException(status_code=_STATUS[code], detail={"code": code, "message": "request rejected"})


def _require(actor: CurrentUser, roles: set[str], code: str) -> None:
    if actor.role not in roles:
        raise _error(code)


def _uuid(value: object) -> UUID:
    result = UUID(str(value))
    if result.version != 7:
        raise RuntimeError("DEPENDENCY_UNAVAILABLE") from None
    return result


def _summary(row: Mapping[str, object]) -> AssessmentSummaryDTO:
    return AssessmentSummaryDTO(
        assessment_id=_uuid(row["assessment_id"]),
        service_case_id=_uuid(row["service_case_id"]),
        sequence_no=int(row["sequence_no"]),
        status=str(row["status"]),
        overall_risk=row.get("overall_risk"),
        rule_version=str(row["rule_version"]),
        input_snapshot_ref=_uuid(row["input_snapshot_ref"]),
        supersedes_assessment_id=_uuid(row["supersedes_assessment_id"]) if row.get("supersedes_assessment_id") else None,
        initiated_at=row["initiated_at"],
        completed_at=row.get("completed_at"),
        version=int(row["version"]),
    )


def _detail(row: Mapping[str, object]) -> AssessmentDetailDTO:
    base = _summary(row).model_dump()
    module_results = []
    for item in row.get("module_results") or ():
        evidence = item.get("evidence_items") or ()
        module_results.append(
            ModuleResultDTO(
                module_code=item["module_code"],
                risk_level=item["risk_level"],
                reason_codes=tuple(item.get("reason_codes") or ()),
                evidence_items=tuple(
                    value.get("indicator_code", "") if isinstance(value, dict) else str(value)
                    for value in evidence
                ),
                message_codes=tuple(item.get("message_codes") or ()),
            )
        )
    evidence = row["input_evidence"]
    return AssessmentDetailDTO(
        **base,
        module_results=module_results,
        input_evidence=InputEvidenceDTO(
            assembly_ref=_uuid(evidence["assembly_ref"]),
            profile_revision_ref=_uuid(evidence["profile_revision_ref"]),
            data_as_of=evidence["data_as_of"],
            source_types=tuple(sorted(evidence.get("source_types") or ())),
            watermark_status=evidence["watermark_status"],
        ),
        dispute_status=row.get("dispute_status"),
        high_risk_task_ref=_uuid(row["high_risk_task_ref"]) if row.get("high_risk_task_ref") else None,
    )


def _task(row: Mapping[str, object]) -> HighRiskTaskDTO:
    blocking = row.get("blocking") or {"ordinary_plan": True, "case_completion": True}
    return HighRiskTaskDTO(
        task_id=_uuid(row["task_id"]),
        assessment_id=_uuid(row["assessment_id"]),
        service_case_id=_uuid(row["service_case_id"]),
        status=row["status"],
        reason_module_codes=tuple(row.get("reason_module_codes") or ()),
        assignee=row.get("assignee"),
        due_at=row["due_at"],
        last_action_at=row.get("last_action_at"),
        blocking=BlockingDTO(**blocking),
        version=int(row["version"]),
        created_at=row["created_at"],
        closed_at=row.get("closed_at"),
    )


def _rule(row: Mapping[str, object], *, detail: bool = False):
    approval = "APPROVED" if row.get("reviewer_user_id") else "PENDING"
    common = {
        "rule_set_version_id": _uuid(row["rule_set_version_id"]),
        "version_no": int(row["version_no"]),
        "status": row["status"],
        "module_metadata": MODULE_CODES,
        "author": int(row["author_user_id"]),
        "reviewer": row.get("reviewer_user_id"),
        "approval_state": approval,
        "effective_from": row.get("effective_from"),
        "suspended_at": row.get("suspended_at"),
        "retired_at": row.get("retired_at"),
        "version": int(row["version"]),
    }
    if detail:
        return RuleSetVersionDetailDTO(
            **common,
            rule_set_code=row["rule_set_code"],
            typed_rule_payload=row["typed_rule_payload"],
            approval_evidence_ref=row.get("approval_evidence_ref"),
        )
    return RuleSetVersionDTO(**common)


async def _authorize_row(repository: HealthAssessmentRepository, actor: CurrentUser, row: Mapping[str, object]) -> None:
    if not await repository.actor_read_is_current(
        actor_user_id=actor.id,
        actor_role=actor.role,
        service_case_id=_uuid(row["service_case_id"]),
        subject_member_id=_uuid(row["subject_member_id"]),
        tenant_id=int(row["tenant_id"]),
    ):
        code = "THERAPIST_SCOPE_FORBIDDEN" if actor.role == "therapist" else "INSTITUTION_SCOPE_FORBIDDEN" if actor.role in {"org_admin", "org_operator"} else "ACTOR_CURRENTNESS_FORBIDDEN"
        raise _error(code)


async def _assessment_detail_for_actor(repository, actor, assessment_id: UUID) -> dict:
    row = await repository.assessment_detail(assessment_id)
    if row is None:
        raise _error("ASSESSMENT_NOT_FOUND")
    await _authorize_row(repository, actor, row)
    return row


IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]

therapist_router = APIRouter(prefix="/api/v1/therapist", tags=["slice5-assessment"], route_class=Slice5Route)
institution_router = APIRouter(prefix="/api/v1/institution", tags=["slice5-assessment"], route_class=Slice5Route)
family_router = APIRouter(prefix="/api/v1/family", tags=["slice5-assessment"], route_class=Slice5Route)
platform_router = APIRouter(prefix="/api/v1/platform", tags=["slice5-assessment"], route_class=Slice5Route)
routers = (therapist_router, institution_router, family_router, platform_router)


def strip_slice5_validation_responses(schema: dict[str, object]) -> dict[str, object]:
    paths = schema.get("paths", {})
    if not isinstance(paths, dict):
        return schema
    for (method, path), codes in SLICE5_ROUTE_ERROR_CODES.items():
        operation = paths.get(path, {}).get(method.lower())
        if isinstance(operation, dict):
            operation["x-symbolic-error-codes"] = list(codes)
    return schema


@therapist_router.post("/service-cases/{case_id}/assessments", status_code=202, response_model=AssessmentSummaryDTO)
async def start_health_assessment(
    payload: AssessmentStartRequest,
    case_id: UuidV7,
    idempotency_key: IdempotencyKey,
    actor: CurrentUser = Depends(get_current_user_from_jwt),
    writer=Depends(get_slice5_assessment_writer_session),
) -> AssessmentSummaryDTO:
    _require(actor, {"therapist"}, "THERAPIST_SCOPE_FORBIDDEN")
    factory = await get_slice5_session_factory("assessment_writer")
    result = await start_assessment(
        HealthAssessmentRepository(writer),
        actor_user_id=actor.id,
        service_case_id=case_id,
        expected_case_version=payload.expected_case_version,
        idempotency_key=idempotency_key,
        request_id=Uuid7Generator().generate(),
        confirmation_session_factory=factory,
    )
    return AssessmentSummaryDTO(**result)


async def _case_assessments(case_id: UUID, actor: CurrentUser, clinical, limit: int, cursor: UUID | None) -> AssessmentPageDTO:
    repository = HealthAssessmentRepository(clinical)
    rows = await repository.assessment_page({"service_case_id": case_id, "cursor_id": cursor, "limit": limit})
    for row in rows:
        await _authorize_row(repository, actor, row)
    return AssessmentPageDTO(items=tuple(_summary(row) for row in rows))


@therapist_router.get("/service-cases/{case_id}/assessments", response_model=AssessmentPageDTO)
async def therapist_assessments(case_id: UuidV7, limit: int = Query(50, ge=1, le=100), cursor: UuidV7 | None = None, actor: CurrentUser = Depends(get_current_user_from_jwt), clinical=Depends(get_slice5_clinical_reader_session)) -> AssessmentPageDTO:
    _require(actor, {"therapist"}, "THERAPIST_SCOPE_FORBIDDEN")
    return await _case_assessments(case_id, actor, clinical, limit, cursor)


@therapist_router.get("/service-cases/{case_id}/assessments/{assessment_id}", response_model=AssessmentDetailDTO)
async def therapist_assessment_detail(case_id: UuidV7, assessment_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), clinical=Depends(get_slice5_clinical_reader_session)) -> AssessmentDetailDTO:
    _require(actor, {"therapist"}, "THERAPIST_SCOPE_FORBIDDEN")
    row = await _assessment_detail_for_actor(HealthAssessmentRepository(clinical), actor, assessment_id)
    if _uuid(row["service_case_id"]) != case_id:
        raise _error("ASSESSMENT_NOT_FOUND")
    return _detail(row)


async def _dispute(assessment_id: UUID, payload: AssessmentDisputeRequest, idempotency_key: str, actor: CurrentUser, clinical, writer, actor_context: str) -> DisputeDTO:
    await _assessment_detail_for_actor(HealthAssessmentRepository(clinical), actor, assessment_id)
    factory = await get_slice5_session_factory("assessment_writer")
    result = await raise_assessment_dispute(
        HealthAssessmentRepository(writer),
        assessment_id=assessment_id,
        actor_user_id=actor.id,
        actor_role=actor.role,
        actor_context=actor_context,
        expected_version=payload.expected_version,
        reason_code=payload.reason_code,
        idempotency_key=idempotency_key,
        occurred_at=datetime.now(timezone.utc),
        confirmation_session_factory=factory,
    )
    return DisputeDTO(**result)


@therapist_router.post("/service-cases/{case_id}/assessments/{assessment_id}/disputes", status_code=201, response_model=DisputeDTO)
async def therapist_dispute(payload: AssessmentDisputeRequest, case_id: UuidV7, assessment_id: UuidV7, idempotency_key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), clinical=Depends(get_slice5_clinical_reader_session), writer=Depends(get_slice5_assessment_writer_session)) -> DisputeDTO:
    _require(actor, {"therapist"}, "THERAPIST_SCOPE_FORBIDDEN")
    return await _dispute(assessment_id, payload, idempotency_key, actor, clinical, writer, "THERAPIST")


async def _task_page(actor: CurrentUser, clinical, *, tenant_id: int | None = None, case_id: UUID | None = None, status: str | None = None, cursor: UUID | None = None, limit: int = 50) -> HighRiskTaskPageDTO:
    repository = HealthAssessmentRepository(clinical)
    rows = await repository.task_page({"tenant_id": tenant_id, "service_case_id": case_id, "status": status, "cursor_id": cursor, "limit": limit})
    for row in rows:
        assessment = await repository.assessment_detail(_uuid(row["assessment_id"]))
        if assessment is None:
            raise _error("DEPENDENCY_UNAVAILABLE")
        await _authorize_row(repository, actor, assessment)
    return HighRiskTaskPageDTO(items=tuple(_task(row) for row in rows))


async def _task_detail(actor: CurrentUser, clinical, task_id: UUID) -> tuple[dict, dict]:
    repository = HealthAssessmentRepository(clinical)
    row = await repository.task_detail(task_id)
    if row is None:
        raise _error("HIGH_RISK_TASK_NOT_FOUND")
    assessment = await repository.assessment_detail(_uuid(row["assessment_id"]))
    if assessment is None:
        raise _error("DEPENDENCY_UNAVAILABLE")
    await _authorize_row(repository, actor, assessment)
    return row, assessment


@therapist_router.get("/high-risk-tasks", response_model=HighRiskTaskPageDTO)
async def therapist_tasks(status: str | None = None, limit: int = Query(50, ge=1, le=100), cursor: UuidV7 | None = None, actor: CurrentUser = Depends(get_current_user_from_jwt), clinical=Depends(get_slice5_clinical_reader_session)) -> HighRiskTaskPageDTO:
    _require(actor, {"therapist"}, "THERAPIST_SCOPE_FORBIDDEN")
    return await _task_page(actor, clinical, status=status, cursor=cursor, limit=limit)


@therapist_router.get("/high-risk-tasks/{task_id}", response_model=HighRiskTaskDTO)
async def therapist_task(task_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), clinical=Depends(get_slice5_clinical_reader_session)) -> HighRiskTaskDTO:
    _require(actor, {"therapist"}, "THERAPIST_SCOPE_FORBIDDEN")
    row, _ = await _task_detail(actor, clinical, task_id)
    return _task(row)


async def _task_action(task_id: UUID, payload: HighRiskTaskActionRequest, idempotency_key: str, actor: CurrentUser, clinical, writer) -> HighRiskTaskDTO:
    await _task_detail(actor, clinical, task_id)
    result = await transition_high_risk_task(
        HealthAssessmentRepository(writer), task_id=task_id, actor_user_id=actor.id,
        actor_role=actor.role, expected_version=payload.expected_version,
        action_code=payload.action_code, occurred_at=payload.occurred_at,
        contact_outcome_code=payload.contact_outcome_code, advice_code=payload.advice_code,
        reason_code=payload.reason_code, idempotency_key=idempotency_key,
    )
    row = await HealthAssessmentRepository(clinical).task_detail(_uuid(result["task_id"]))
    if row is None:
        raise _error("DEPENDENCY_UNAVAILABLE")
    return _task(row)


@therapist_router.post("/high-risk-tasks/{task_id}/actions", response_model=HighRiskTaskDTO)
async def therapist_task_action(payload: HighRiskTaskActionRequest, task_id: UuidV7, idempotency_key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), clinical=Depends(get_slice5_clinical_reader_session), writer=Depends(get_slice5_risk_workflow_writer_session)) -> HighRiskTaskDTO:
    _require(actor, {"therapist"}, "THERAPIST_SCOPE_FORBIDDEN")
    return await _task_action(task_id, payload, idempotency_key, actor, clinical, writer)


@institution_router.get("/high-risk-tasks", response_model=HighRiskTaskPageDTO)
async def institution_tasks(status: str | None = None, limit: int = Query(50, ge=1, le=100), cursor: UuidV7 | None = None, actor: CurrentUser = Depends(get_current_user_from_jwt), clinical=Depends(get_slice5_clinical_reader_session)) -> HighRiskTaskPageDTO:
    _require(actor, {"org_admin", "org_operator"}, "INSTITUTION_SCOPE_FORBIDDEN")
    return await _task_page(actor, clinical, tenant_id=actor.tenant_id, status=status, cursor=cursor, limit=limit)


@institution_router.get("/high-risk-tasks/{task_id}", response_model=HighRiskTaskDTO)
async def institution_task(task_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), clinical=Depends(get_slice5_clinical_reader_session)) -> HighRiskTaskDTO:
    _require(actor, {"org_admin", "org_operator"}, "INSTITUTION_SCOPE_FORBIDDEN")
    row, _ = await _task_detail(actor, clinical, task_id)
    return _task(row)


@institution_router.post("/high-risk-tasks/{task_id}/actions", response_model=HighRiskTaskDTO)
async def institution_task_action(payload: HighRiskTaskActionRequest, task_id: UuidV7, idempotency_key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), clinical=Depends(get_slice5_clinical_reader_session), writer=Depends(get_slice5_risk_workflow_writer_session)) -> HighRiskTaskDTO:
    _require(actor, {"org_admin", "org_operator"}, "INSTITUTION_SCOPE_FORBIDDEN")
    return await _task_action(task_id, payload, idempotency_key, actor, clinical, writer)


@institution_router.get("/service-cases/{case_id}/assessments", response_model=AssessmentPageDTO)
async def institution_assessments(case_id: UuidV7, limit: int = Query(50, ge=1, le=100), cursor: UuidV7 | None = None, actor: CurrentUser = Depends(get_current_user_from_jwt), clinical=Depends(get_slice5_clinical_reader_session)) -> AssessmentPageDTO:
    _require(actor, {"org_admin", "org_operator"}, "INSTITUTION_SCOPE_FORBIDDEN")
    return await _case_assessments(case_id, actor, clinical, limit, cursor)


async def _family_subject(repository: HealthAssessmentRepository, actor: CurrentUser, enrollment_id: UUID | None) -> UUID:
    _require(actor, {"member"}, "ACTOR_CURRENTNESS_FORBIDDEN")
    row = await repository.subject_scope(
        actor_user_id=actor.id,
        actor_context="SELF" if enrollment_id is None else "PROXY_DAILY_VIEW",
        enrollment_id=enrollment_id,
    )
    if row is None:
        raise _error("PROXY_PERMISSION_FORBIDDEN" if enrollment_id else "ACTOR_CURRENTNESS_FORBIDDEN")
    return _uuid(row["subject_member_id"])


async def _family_page(actor: CurrentUser, clinical, enrollment_id: UUID | None, limit: int, cursor: UUID | None) -> AssessmentPageDTO:
    repository = HealthAssessmentRepository(clinical)
    subject = await _family_subject(repository, actor, enrollment_id)
    rows = await repository.assessment_page({"subject_member_id": subject, "cursor_id": cursor, "limit": limit})
    for row in rows:
        await _authorize_row(repository, actor, row)
    return AssessmentPageDTO(items=tuple(_summary(row) for row in rows))


@family_router.get("/assessments", response_model=AssessmentPageDTO)
async def family_assessments(limit: int = Query(50, ge=1, le=100), cursor: UuidV7 | None = None, actor: CurrentUser = Depends(get_current_user_from_jwt), clinical=Depends(get_slice5_clinical_reader_session)) -> AssessmentPageDTO:
    return await _family_page(actor, clinical, None, limit, cursor)


async def _family_detail(actor: CurrentUser, clinical, assessment_id: UUID, enrollment_id: UUID | None) -> AssessmentDetailDTO:
    repository = HealthAssessmentRepository(clinical)
    subject = await _family_subject(repository, actor, enrollment_id)
    row = await _assessment_detail_for_actor(repository, actor, assessment_id)
    if _uuid(row["subject_member_id"]) != subject:
        raise _error("ASSESSMENT_NOT_FOUND")
    return _detail(row)


@family_router.get("/assessments/{assessment_id}", response_model=AssessmentDetailDTO)
async def family_assessment_detail(assessment_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), clinical=Depends(get_slice5_clinical_reader_session)) -> AssessmentDetailDTO:
    return await _family_detail(actor, clinical, assessment_id, None)


@family_router.post("/assessments/{assessment_id}/disputes", status_code=201, response_model=DisputeDTO)
async def family_dispute(payload: AssessmentDisputeRequest, assessment_id: UuidV7, idempotency_key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), clinical=Depends(get_slice5_clinical_reader_session), writer=Depends(get_slice5_assessment_writer_session)) -> DisputeDTO:
    await _family_subject(HealthAssessmentRepository(clinical), actor, None)
    return await _dispute(assessment_id, payload, idempotency_key, actor, clinical, writer, "SELF")


@family_router.get("/proxy-enrollments/{enrollment_id}/assessments", response_model=AssessmentPageDTO)
async def proxy_assessments(enrollment_id: UuidV7, limit: int = Query(50, ge=1, le=100), cursor: UuidV7 | None = None, actor: CurrentUser = Depends(get_current_user_from_jwt), clinical=Depends(get_slice5_clinical_reader_session)) -> AssessmentPageDTO:
    return await _family_page(actor, clinical, enrollment_id, limit, cursor)


@family_router.get("/proxy-enrollments/{enrollment_id}/assessments/{assessment_id}", response_model=AssessmentDetailDTO)
async def proxy_assessment_detail(enrollment_id: UuidV7, assessment_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), clinical=Depends(get_slice5_clinical_reader_session)) -> AssessmentDetailDTO:
    return await _family_detail(actor, clinical, assessment_id, enrollment_id)


@family_router.post("/proxy-enrollments/{enrollment_id}/assessments/{assessment_id}/disputes", status_code=201, response_model=DisputeDTO)
async def proxy_dispute(payload: AssessmentDisputeRequest, enrollment_id: UuidV7, assessment_id: UuidV7, idempotency_key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), clinical=Depends(get_slice5_clinical_reader_session), writer=Depends(get_slice5_assessment_writer_session)) -> DisputeDTO:
    await _family_subject(HealthAssessmentRepository(clinical), actor, enrollment_id)
    raise _error("PROXY_PERMISSION_FORBIDDEN")


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _validate_rule_payload(value: dict) -> None:
    if set(value) < {"included_rule_ids", "deferred_rule_ids", "golden_cases"}:
        raise _error("INVALID_REQUEST")
    if set(value["included_rule_ids"]) != set(INCLUDED_RULE_IDS) or set(value["deferred_rule_ids"]) != set(DEFERRED_RULE_IDS) or len(value["golden_cases"]) != 21:
        raise _error("INVALID_REQUEST")


async def _rule_detail(repository: HealthAssessmentRepository, version_id: UUID) -> dict:
    row = await repository.rule_set_detail(version_id)
    if row is None:
        raise _error("RULE_SET_NOT_FOUND")
    return row


@platform_router.post("/assessment-rule-sets", status_code=201, response_model=RuleSetVersionDetailDTO)
async def create_rule_set(payload: RuleSetCreateRequest, idempotency_key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), writer=Depends(get_slice5_rule_governance_writer_session)) -> RuleSetVersionDetailDTO:
    _require(actor, {"expert"}, "RULE_GOVERNANCE_FORBIDDEN")
    _validate_rule_payload(payload.typed_rule_payload)
    calculated = hashlib.sha256(_canonical(payload.typed_rule_payload)).hexdigest()
    if calculated != payload.medical_content_digest:
        raise _error("INVALID_REQUEST")
    repository = HealthAssessmentRepository(writer)
    result = await govern_rule_set(
        repository,
        operation="CREATE",
        actor_user_id=actor.id,
        actor_role=actor.role,
        idempotency_key=idempotency_key,
        details={
            "version_no": payload.version_no,
            "typed_rule_payload": payload.typed_rule_payload,
            "content_digest": calculated,
            "approval_evidence_ref": payload.approval_evidence_ref,
        },
    )
    return RuleSetVersionDetailDTO(**result)


@platform_router.get("/assessment-rule-sets", response_model=RuleSetPageDTO)
async def rule_sets(limit: int = Query(50, ge=1, le=100), cursor: UuidV7 | None = None, actor: CurrentUser = Depends(get_current_user_from_jwt), writer=Depends(get_slice5_rule_governance_writer_session)) -> RuleSetPageDTO:
    _require(actor, {"expert", "sys_admin", "super_admin"}, "RULE_GOVERNANCE_FORBIDDEN")
    rows = await HealthAssessmentRepository(writer).rule_set_page(cursor, limit)
    return RuleSetPageDTO(items=tuple(_rule(row) for row in rows))


@platform_router.get("/assessment-rule-sets/{version_id}", response_model=RuleSetVersionDetailDTO)
async def rule_set_detail(version_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), writer=Depends(get_slice5_rule_governance_writer_session)) -> RuleSetVersionDetailDTO:
    _require(actor, {"expert", "sys_admin", "super_admin"}, "RULE_GOVERNANCE_FORBIDDEN")
    return _rule(await _rule_detail(HealthAssessmentRepository(writer), version_id), detail=True)


async def _govern(operation: str, version_id: UUID, expected_version: int, idempotency_key: str, actor: CurrentUser, writer, extra: Mapping[str, object] | None = None) -> RuleSetVersionDetailDTO:
    repository = HealthAssessmentRepository(writer)
    row = await _rule_detail(repository, version_id)
    now = datetime.now(timezone.utc)
    response = _rule(row, detail=True).model_dump()
    response["version"] = expected_version + 1
    response["status"] = {
        "SUBMIT": "IN_REVIEW",
        "REVIEW_APPROVE": "IN_REVIEW",
        "REVIEW_CORRECTION": "NEEDS_CORRECTION",
        "PUBLISH": "PUBLISHED",
        "SUSPEND": "SUSPENDED",
        "RESUME": "PUBLISHED",
        "RETIRE": "RETIRED",
    }[operation]
    if operation in {"REVIEW_APPROVE", "REVIEW_CORRECTION"}:
        response["reviewer"] = actor.id
        response["approval_state"] = "APPROVED" if operation == "REVIEW_APPROVE" else "NEEDS_CORRECTION"
    if operation == "PUBLISH":
        response["effective_from"] = (extra or {}).get("effective_from")
    if operation == "SUSPEND":
        response["suspended_at"] = now
    if operation == "RESUME":
        response["suspended_at"] = None
    if operation == "RETIRE":
        response["retired_at"] = now
    details = dict(extra or {})
    details["_response"] = response
    result = await govern_rule_set(
        repository,
        operation=operation,
        actor_user_id=actor.id,
        actor_role=actor.role,
        idempotency_key=idempotency_key,
        rule_set_version_id=version_id,
        expected_version=expected_version,
        details=details,
        occurred_at=now,
    )
    return RuleSetVersionDetailDTO(**result)


@platform_router.post("/assessment-rule-sets/{version_id}/submit", response_model=RuleSetVersionDetailDTO)
async def submit_rule_set(payload: VersionRequest, version_id: UuidV7, idempotency_key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), writer=Depends(get_slice5_rule_governance_writer_session)) -> RuleSetVersionDetailDTO:
    _require(actor, {"expert"}, "RULE_GOVERNANCE_FORBIDDEN")
    return await _govern("SUBMIT", version_id, payload.expected_version, idempotency_key, actor, writer)


@platform_router.post("/assessment-rule-sets/{version_id}/review", response_model=RuleSetVersionDetailDTO)
async def review_rule_set(payload: RuleReviewRequest, version_id: UuidV7, idempotency_key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), writer=Depends(get_slice5_rule_governance_writer_session)) -> RuleSetVersionDetailDTO:
    _require(actor, {"expert"}, "RULE_GOVERNANCE_FORBIDDEN")
    operation = "REVIEW_APPROVE" if payload.decision == "APPROVE" else "REVIEW_CORRECTION"
    return await _govern(operation, version_id, payload.expected_version, idempotency_key, actor, writer, {"reason_code": payload.reason_code})


@platform_router.post("/assessment-rule-sets/{version_id}/publish", response_model=RuleSetVersionDetailDTO)
async def publish_rule_set(payload: RulePublishRequest, version_id: UuidV7, idempotency_key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), writer=Depends(get_slice5_rule_governance_writer_session)) -> RuleSetVersionDetailDTO:
    _require(actor, {"sys_admin", "super_admin"}, "RULE_GOVERNANCE_FORBIDDEN")
    return await _govern("PUBLISH", version_id, payload.expected_version, idempotency_key, actor, writer, {"effective_from": payload.effective_from})


def _governance_route(operation: str):
    async def endpoint(payload: RuleGovernanceRequest, version_id: UuidV7, idempotency_key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), writer=Depends(get_slice5_rule_governance_writer_session)) -> RuleSetVersionDetailDTO:
        _require(actor, {"sys_admin", "super_admin"}, "RULE_GOVERNANCE_FORBIDDEN")
        return await _govern(operation, version_id, payload.expected_version, idempotency_key, actor, writer, {"reason_code": payload.reason_code})
    endpoint.__name__ = f"{operation.lower()}_rule_set"
    return endpoint


platform_router.post("/assessment-rule-sets/{version_id}/suspend", response_model=RuleSetVersionDetailDTO)(_governance_route("SUSPEND"))
platform_router.post("/assessment-rule-sets/{version_id}/resume", response_model=RuleSetVersionDetailDTO)(_governance_route("RESUME"))
platform_router.post("/assessment-rule-sets/{version_id}/retire", response_model=RuleSetVersionDetailDTO)(_governance_route("RETIRE"))


@platform_router.get("/high-risk-tasks", response_model=HighRiskTaskPageDTO)
async def platform_tasks(status: str | None = None, limit: int = Query(50, ge=1, le=100), cursor: UuidV7 | None = None, actor: CurrentUser = Depends(get_current_user_from_jwt), oversight=Depends(get_slice5_oversight_reader_session)) -> HighRiskTaskPageDTO:
    _require(actor, {"sys_admin", "super_admin"}, "RULE_GOVERNANCE_FORBIDDEN")
    return await _task_page(actor, oversight, status=status, cursor=cursor, limit=limit)


@platform_router.get("/high-risk-tasks/{task_id}", response_model=HighRiskTaskDTO)
async def platform_task(task_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), oversight=Depends(get_slice5_oversight_reader_session)) -> HighRiskTaskDTO:
    _require(actor, {"sys_admin", "super_admin"}, "RULE_GOVERNANCE_FORBIDDEN")
    row, _ = await _task_detail(actor, oversight, task_id)
    return _task(row)
