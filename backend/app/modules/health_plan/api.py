from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Mapping
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from app.core.database import (
    get_slice5_clinical_reader_session,
    get_slice6_clinical_reader_session,
    get_slice6_family_reader_session,
    get_slice6_institution_writer_session,
    get_slice6_review_writer_session,
    get_slice6_session_factory,
    get_slice6_template_writer_session,
)
from app.core.security import CurrentUser, get_current_user_from_jwt
from app.core.uuid_generator import Uuid7Generator
from app.core.接口合同 import error_response

from .repository import HealthPlanRepository, HealthPlanRepositoryError
from .schemas import (
    ClaimRequest,
    CreatePlanGenerationRequest,
    HealthPlanTemplateCreateRequest,
    HealthPlanTemplateDTO,
    HealthPlanTemplatePageDTO,
    PlanDetailDTO,
    PlanExplanationRequest,
    PlanGenerationEligibilityDTO,
    PlanGenerationRequestDTO,
    PlanPageDTO,
    PlanReviewDetailDTO,
    PlanReviewPageDTO,
    PlanSummaryDTO,
    ReviewDecisionRequest,
    ReviewStatus,
    TemplateVersionRequest,
    UserDecisionRequest,
    UuidV7,
)
from .service import (
    CommitOutcome,
    HealthPlanError,
    claim_plan_review,
    commit_with_confirmation,
    decide_plan,
    decode_review_cursor,
    encode_review_cursor,
    explain_plan,
    get_generation_eligibility,
    govern_template,
    request_plan_generation,
    review_detail_dto,
    review_list_item,
    review_plan,
)

_STATUS = {
    "UNAUTHENTICATED": 401,
    "FORBIDDEN": 403,
    "USER_DECISION_FORBIDDEN": 403,
    "REVIEW_NOT_CLAIMED": 403,
    "SERVICE_CASE_NOT_FOUND": 404,
    "PLAN_NOT_FOUND": 404,
    "SERVICE_CASE_NOT_CURRENT": 409,
    "TENANT_NOT_SERVICE_READY": 409,
    "CONSENT_NOT_CURRENT": 409,
    "PRIMARY_THERAPIST_NOT_CURRENT": 409,
    "ASSESSMENT_INPUT_NOT_READY": 409,
    "ASSESSMENT_NOT_COMPLETED": 409,
    "ASSESSMENT_DISPUTED": 409,
    "ASSESSMENT_SUPERSEDED": 409,
    "HIGH_RISK_BLOCKING": 409,
    "TEMPLATE_NOT_AVAILABLE": 409,
    "ACTIVE_GENERATION_EXISTS": 409,
    "ACTIVE_PLAN_CONFLICT": 409,
    "STALE_VERSION": 409,
    "IDEMPOTENCY_CONFLICT": 409,
    "REVIEW_DECISION_CONFLICT": 409,
    "USER_DECISION_CONFLICT": 409,
    "INVALID_REQUEST": 422,
    "DEPENDENCY_UNAVAILABLE": 503,
    "COMMIT_OUTCOME_UNKNOWN": 503,
}
_COMMON = ("UNAUTHENTICATED", "INVALID_REQUEST", "DEPENDENCY_UNAVAILABLE")


def _codes(*extra: str) -> tuple[str, ...]:
    return _COMMON + extra


SLICE6_ROUTE_ERROR_CODES = {
    ("GET", "/api/v1/institutions/service-cases/{service_case_id}/plan-generation-eligibility"): _codes("FORBIDDEN", "SERVICE_CASE_NOT_FOUND", "SERVICE_CASE_NOT_CURRENT", "TENANT_NOT_SERVICE_READY", "CONSENT_NOT_CURRENT", "PRIMARY_THERAPIST_NOT_CURRENT", "ASSESSMENT_INPUT_NOT_READY", "ASSESSMENT_NOT_COMPLETED", "ASSESSMENT_DISPUTED", "ASSESSMENT_SUPERSEDED", "HIGH_RISK_BLOCKING", "TEMPLATE_NOT_AVAILABLE", "ACTIVE_GENERATION_EXISTS", "ACTIVE_PLAN_CONFLICT"),
    ("POST", "/api/v1/institutions/service-cases/{service_case_id}/plan-generations"): _codes("FORBIDDEN", "SERVICE_CASE_NOT_FOUND", "SERVICE_CASE_NOT_CURRENT", "TENANT_NOT_SERVICE_READY", "CONSENT_NOT_CURRENT", "PRIMARY_THERAPIST_NOT_CURRENT", "ASSESSMENT_INPUT_NOT_READY", "ASSESSMENT_NOT_COMPLETED", "ASSESSMENT_DISPUTED", "ASSESSMENT_SUPERSEDED", "HIGH_RISK_BLOCKING", "TEMPLATE_NOT_AVAILABLE", "ACTIVE_GENERATION_EXISTS", "ACTIVE_PLAN_CONFLICT", "STALE_VERSION", "IDEMPOTENCY_CONFLICT", "COMMIT_OUTCOME_UNKNOWN"),
    ("GET", "/api/v1/institutions/plan-generations/{request_id}"): _codes("FORBIDDEN", "PLAN_NOT_FOUND"),
    ("GET", "/api/v1/institutions/service-cases/{service_case_id}/plans"): _codes("FORBIDDEN", "SERVICE_CASE_NOT_FOUND"),
    ("GET", "/api/v1/institutions/plans/{plan_id}"): _codes("FORBIDDEN", "PLAN_NOT_FOUND"),
    ("POST", "/api/v1/platform/health-plan-templates"): _codes("FORBIDDEN", "IDEMPOTENCY_CONFLICT", "COMMIT_OUTCOME_UNKNOWN"),
    ("GET", "/api/v1/platform/health-plan-templates"): _codes("FORBIDDEN"),
    ("GET", "/api/v1/platform/health-plan-templates/{template_version_id}"): _codes("FORBIDDEN", "PLAN_NOT_FOUND"),
    ("POST", "/api/v1/platform/health-plan-templates/{template_version_id}/publish"): _codes("FORBIDDEN", "STALE_VERSION", "IDEMPOTENCY_CONFLICT", "COMMIT_OUTCOME_UNKNOWN"),
    ("POST", "/api/v1/platform/health-plan-templates/{template_version_id}/retire"): _codes("FORBIDDEN", "STALE_VERSION", "IDEMPOTENCY_CONFLICT", "COMMIT_OUTCOME_UNKNOWN"),
    ("GET", "/api/v1/platform/health-plan-reviews"): _codes("FORBIDDEN"),
    ("GET", "/api/v1/platform/health-plan-reviews/{review_id}"): _codes("FORBIDDEN", "PLAN_NOT_FOUND"),
    ("POST", "/api/v1/platform/health-plan-reviews/{review_id}/claim"): _codes("FORBIDDEN", "REVIEW_NOT_CLAIMED", "STALE_VERSION", "IDEMPOTENCY_CONFLICT", "COMMIT_OUTCOME_UNKNOWN"),
    ("POST", "/api/v1/platform/health-plan-reviews/{review_id}/decision"): _codes("FORBIDDEN", "REVIEW_NOT_CLAIMED", "REVIEW_DECISION_CONFLICT", "STALE_VERSION", "IDEMPOTENCY_CONFLICT", "COMMIT_OUTCOME_UNKNOWN"),
    ("GET", "/api/v1/therapist/service-cases/{service_case_id}/plans"): _codes("FORBIDDEN", "SERVICE_CASE_NOT_FOUND"),
    ("GET", "/api/v1/therapist/plans/{plan_id}"): _codes("FORBIDDEN", "PLAN_NOT_FOUND"),
    ("POST", "/api/v1/therapist/plans/{plan_id}/explanations"): _codes("FORBIDDEN", "PLAN_NOT_FOUND", "STALE_VERSION", "IDEMPOTENCY_CONFLICT", "COMMIT_OUTCOME_UNKNOWN"),
    ("GET", "/api/v1/family/service-cases/{service_case_id}/plans"): _codes("FORBIDDEN", "SERVICE_CASE_NOT_FOUND"),
    ("GET", "/api/v1/family/plans/{plan_id}"): _codes("FORBIDDEN", "PLAN_NOT_FOUND"),
    ("POST", "/api/v1/family/plans/{plan_id}/decision"): _codes("FORBIDDEN", "USER_DECISION_FORBIDDEN", "PLAN_NOT_FOUND", "USER_DECISION_CONFLICT", "STALE_VERSION", "IDEMPOTENCY_CONFLICT", "COMMIT_OUTCOME_UNKNOWN"),
}


def _registered_plan_error(exc: HealthPlanError | HealthPlanRepositoryError) -> str | None:
    if len(exc.args) != 1 or type(exc.args[0]) is not str:
        return None
    return exc.args[0] if exc.args[0] in _STATUS else None


class Slice6Route(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            key = (next(iter(self.methods)), self.path)
            try:
                return await original(request)
            except RequestValidationError:
                return error_response(request, 422, "INVALID_REQUEST")
            except HTTPException as exc:
                detail = exc.detail
                code = detail.get("code") if isinstance(detail, dict) else None
                if exc.status_code == 401:
                    code = "UNAUTHENTICATED"
                if code not in SLICE6_ROUTE_ERROR_CODES[key]:
                    code = "INVALID_REQUEST" if exc.status_code < 500 else "DEPENDENCY_UNAVAILABLE"
                status = _STATUS[code]
                return error_response(
                    request, status, code,
                    retryable=status == 503 and code != "COMMIT_OUTCOME_UNKNOWN",
                    headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
                )
            except (HealthPlanError, HealthPlanRepositoryError) as exc:
                code = _registered_plan_error(exc)
                if code in SLICE6_ROUTE_ERROR_CODES[key]:
                    status = _STATUS[code]
                    return error_response(
                        request, status, code,
                        retryable=status == 503 and code != "COMMIT_OUTCOME_UNKNOWN",
                    )
                return error_response(request, 500, "INTERNAL_ERROR")
            except Exception:
                return error_response(request, 500, "INTERNAL_ERROR")

        return handler


def _router(prefix: str, tags: list[str]) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=tags)
    router.route_class = Slice6Route
    return router


institution_router = _router("/api/v1/institutions", ["health-plan-institution"])
platform_router = _router("/api/v1/platform", ["health-plan-platform"])
therapist_router = _router("/api/v1/therapist", ["health-plan-therapist"])
family_router = _router("/api/v1/family", ["health-plan-family"])
routers = (institution_router, platform_router, therapist_router, family_router)

IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]
_NO_STORE = {"Cache-Control": {"schema": {"type": "string", "const": "no-store"}}}


def strip_slice6_validation_responses(schema: dict[str, object]) -> dict[str, object]:
    paths = schema.get("paths", {})
    if not isinstance(paths, dict):
        return schema
    for (method, path), codes in SLICE6_ROUTE_ERROR_CODES.items():
        operation = paths.get(path, {}).get(method.lower())
        if isinstance(operation, dict):
            statuses = {*(str(_STATUS[code]) for code in codes), "422", "500"}
            for status in sorted(statuses, key=int):
                example_code = "INTERNAL_ERROR" if status == "500" else next(
                    code for code in codes if _STATUS[code] == int(status)
                )
                response = operation.setdefault("responses", {}).setdefault(status, {"description": "Request rejected"})
                examples = {"rejected": {"value": {
                    "code": example_code,
                    "message": "request rejected", "request_id": "01990000-0000-7000-8000-000000000201",
                    "retryable": status == "503" and example_code != "COMMIT_OUTCOME_UNKNOWN", "field_errors": [],
                }}}
                if status in {"401", "503"}:
                    examples["authentication"] = {"value": {
                        "code": "UNAUTHENTICATED" if status == "401" else "DEPENDENCY_UNAVAILABLE",
                        "message": "request rejected", "request_id": "01990000-0000-7000-8000-000000000201",
                        "retryable": status == "503", "field_errors": [],
                    }}
                response.setdefault("content", {})["application/json"] = {
                    "schema": {"$ref": "#/components/schemas/ErrorResponseDTO"},
                    "examples": examples,
                }
                headers = response.setdefault("headers", {})
                headers.update({
                    "X-Request-ID": {"schema": {"type": "string", "format": "uuid"}},
                    "Cache-Control": {"schema": {"type": "string", "enum": ["no-store, private"]}},
                    "Pragma": {"schema": {"type": "string", "const": "no-cache"}},
                })
                if status == "401":
                    headers["WWW-Authenticate"] = {"schema": {"type": "string", "enum": ["Bearer"]}}
                if status in {"429", "503"}:
                    headers["Retry-After"] = {"schema": {"type": "integer", "minimum": 0}}
            operation["x-symbolic-error-codes"] = list(codes)
    return schema


def _error(code: str) -> HTTPException:
    return HTTPException(_STATUS[code], {"code": code, "message": "request rejected"})


def _require(actor: CurrentUser, roles: set[str]) -> None:
    if actor.role not in roles:
        raise _error("FORBIDDEN")


def _uuid(value: object) -> UUID:
    result = value if type(value) is UUID else UUID(str(value))
    if result.version != 7:
        raise HealthPlanError("DEPENDENCY_UNAVAILABLE")
    return result


def _generation(row: Mapping[str, object]) -> PlanGenerationRequestDTO:
    return PlanGenerationRequestDTO.model_validate(
        {key: row.get(key) for key in PlanGenerationRequestDTO.model_fields}
    )


def _template(row: Mapping[str, object]) -> HealthPlanTemplateDTO:
    data = dict(row)
    content = data.pop("content", {})
    for key in ("applicable_modules", "goals_by_module", "stage_codes", "milestone_codes", "sop_codes", "contraindication_codes", "user_message_codes", "therapist_action_codes"):
        data[key] = content.get(key)
    return HealthPlanTemplateDTO.model_validate(
        {key: data.get(key) for key in HealthPlanTemplateDTO.model_fields}
    )


def _summary(row: Mapping[str, object]) -> PlanSummaryDTO:
    return PlanSummaryDTO.model_validate(
        {key: row.get(key) for key in PlanSummaryDTO.model_fields}
    )


def _detail(row: Mapping[str, object]) -> PlanDetailDTO:
    return PlanDetailDTO.model_validate(
        {key: row.get(key) for key in PlanDetailDTO.model_fields}
    )


async def _authorize(repository: HealthPlanRepository, actor: CurrentUser, row: Mapping[str, object]) -> None:
    if not await repository.actor_read_is_current(
        actor_user_id=actor.id,
        actor_role=actor.role,
        service_case_id=_uuid(row["service_case_id"]),
        subject_member_id=_uuid(row["subject_member_id"]),
        tenant_id=int(row["tenant_id"]),
    ):
        raise _error("FORBIDDEN")


async def _commit_mutation(writer, repository: HealthPlanRepository, kind: str) -> None:
    if repository.mutation_replayed:
        await writer.commit()
        return
    confirmation = repository.mutation_confirmation
    if confirmation is None:
        raise _error("COMMIT_OUTCOME_UNKNOWN")

    async def confirm() -> CommitOutcome:
        factory = await get_slice6_session_factory(kind)
        async with factory() as fresh:
            row = await HealthPlanRepository(fresh).confirm_mutation_outcome(confirmation)
            return CommitOutcome(str(row["outcome"]))

    outcome = await commit_with_confirmation(writer, confirm=confirm)
    if outcome is not CommitOutcome.COMMITTED:
        raise _error("COMMIT_OUTCOME_UNKNOWN")


@institution_router.get("/service-cases/{service_case_id}/plan-generation-eligibility", response_model=PlanGenerationEligibilityDTO)
async def generation_eligibility(service_case_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), writer=Depends(get_slice6_institution_writer_session)):
    _require(actor, {"org_admin", "org_operator"})
    return await get_generation_eligibility(HealthPlanRepository(writer), service_case_id=service_case_id, actor_user_id=actor.id, actor_role=actor.role, evaluated_at=datetime.now(timezone.utc))


@institution_router.post("/service-cases/{service_case_id}/plan-generations", status_code=202, response_model=PlanGenerationRequestDTO)
async def create_generation(payload: CreatePlanGenerationRequest, service_case_id: UuidV7, idempotency_key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), writer=Depends(get_slice6_institution_writer_session)):
    _require(actor, {"org_admin", "org_operator"})
    repository = HealthPlanRepository(writer)
    row = await request_plan_generation(repository, service_case_id=service_case_id, expected_service_case_version=payload.expected_service_case_version, actor_user_id=actor.id, actor_role=actor.role, idempotency_key=idempotency_key, now=datetime.now(timezone.utc), id_factory=Uuid7Generator().generate)
    await _commit_mutation(writer, repository, "institution_writer")
    return _generation(row)


@institution_router.get("/plan-generations/{request_id}", response_model=PlanGenerationRequestDTO)
async def generation_progress(request_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), reader=Depends(get_slice6_clinical_reader_session)):
    _require(actor, {"org_admin", "org_operator"})
    repository = HealthPlanRepository(reader)
    row = await repository.generation_detail(request_id)
    if row is None:
        raise _error("PLAN_NOT_FOUND")
    await _authorize(repository, actor, row)
    return _generation(row)


async def _plan_page(service_case_id: UUID, actor: CurrentUser, reader) -> PlanPageDTO:
    repository = HealthPlanRepository(reader)
    authority = await repository.case_read_authority(
        actor_user_id=actor.id,
        actor_role=actor.role,
        service_case_id=service_case_id,
    )
    if authority.get("exists") is not True:
        raise _error("SERVICE_CASE_NOT_FOUND")
    if authority.get("allowed") is not True:
        raise _error("FORBIDDEN")
    rows = await repository.plan_page(service_case_id=service_case_id, cursor_id=None, limit=50)
    for row in rows:
        await _authorize(repository, actor, row)
    return PlanPageDTO(items=tuple(_summary(row) for row in rows))


async def _plan_detail(plan_id: UUID, actor: CurrentUser, reader) -> PlanDetailDTO:
    repository = HealthPlanRepository(reader)
    row = await repository.plan_detail(plan_id)
    if row is None:
        raise _error("PLAN_NOT_FOUND")
    await _authorize(repository, actor, row)
    return _detail(row)


@institution_router.get("/service-cases/{service_case_id}/plans", response_model=PlanPageDTO)
async def institution_plans(service_case_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), reader=Depends(get_slice6_clinical_reader_session)):
    _require(actor, {"org_admin", "org_operator"})
    return await _plan_page(service_case_id, actor, reader)


@institution_router.get("/plans/{plan_id}", response_model=PlanDetailDTO, responses={200: {"headers": _NO_STORE}})
async def institution_plan(plan_id: UuidV7, response: Response, actor: CurrentUser = Depends(get_current_user_from_jwt), reader=Depends(get_slice6_clinical_reader_session)):
    _require(actor, {"org_admin", "org_operator"})
    response.headers["Cache-Control"] = "no-store"
    return await _plan_detail(plan_id, actor, reader)


@platform_router.post("/health-plan-templates", status_code=201, response_model=HealthPlanTemplateDTO)
async def create_template(payload: HealthPlanTemplateCreateRequest, idempotency_key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), writer=Depends(get_slice6_template_writer_session)):
    _require(actor, {"expert", "sys_admin", "super_admin"})
    repository = HealthPlanRepository(writer)
    result = await govern_template(repository, operation="CREATE", template_version_id=Uuid7Generator().generate(), template_payload=payload.model_dump(), expected_version=None, actor_user_id=actor.id, actor_role=actor.role, idempotency_key=idempotency_key, now=datetime.now(timezone.utc), id_factory=Uuid7Generator().generate)
    await _commit_mutation(writer, repository, "template_writer")
    return _template(result)


@platform_router.get("/health-plan-templates", response_model=HealthPlanTemplatePageDTO)
async def templates(limit: int = Query(50, ge=1, le=100), cursor: UuidV7 | None = None, actor: CurrentUser = Depends(get_current_user_from_jwt), reader=Depends(get_slice6_template_writer_session)):
    _require(actor, {"expert", "sys_admin", "super_admin"})
    rows = await HealthPlanRepository(reader).template_page(cursor, limit)
    return HealthPlanTemplatePageDTO(items=tuple(_template(row) for row in rows))


@platform_router.get("/health-plan-templates/{template_version_id}", response_model=HealthPlanTemplateDTO)
async def template_detail(template_version_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), reader=Depends(get_slice6_template_writer_session)):
    _require(actor, {"expert", "sys_admin", "super_admin"})
    row = await HealthPlanRepository(reader).template_detail(template_version_id)
    if row is None:
        raise _error("PLAN_NOT_FOUND")
    return _template(row)


async def _template_transition(operation: str, template_version_id: UUID, payload: TemplateVersionRequest, idempotency_key: str, actor: CurrentUser, writer) -> HealthPlanTemplateDTO:
    repository = HealthPlanRepository(writer)
    result = await govern_template(repository, operation=operation, template_version_id=template_version_id, template_payload=None, expected_version=payload.expected_version, actor_user_id=actor.id, actor_role=actor.role, idempotency_key=idempotency_key, now=datetime.now(timezone.utc), id_factory=Uuid7Generator().generate)
    await _commit_mutation(writer, repository, "template_writer")
    return _template(result)


@platform_router.post("/health-plan-templates/{template_version_id}/publish", response_model=HealthPlanTemplateDTO)
async def publish_template(payload: TemplateVersionRequest, template_version_id: UuidV7, idempotency_key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), writer=Depends(get_slice6_template_writer_session)):
    _require(actor, {"expert", "sys_admin", "super_admin"})
    return await _template_transition("PUBLISH", template_version_id, payload, idempotency_key, actor, writer)


@platform_router.post("/health-plan-templates/{template_version_id}/retire", response_model=HealthPlanTemplateDTO)
async def retire_template(payload: TemplateVersionRequest, template_version_id: UuidV7, idempotency_key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), writer=Depends(get_slice6_template_writer_session)):
    _require(actor, {"expert", "sys_admin", "super_admin"})
    return await _template_transition("RETIRE", template_version_id, payload, idempotency_key, actor, writer)


@platform_router.get(
    "/health-plan-reviews",
    response_model=PlanReviewPageDTO,
    responses={200: {"headers": _NO_STORE}},
)
async def reviews(
    response: Response,
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = None,
    status: ReviewStatus | None = None,
    actor: CurrentUser = Depends(get_current_user_from_jwt),
    reader=Depends(get_slice6_review_writer_session),
    plan_reader=Depends(get_slice6_clinical_reader_session),
):
    _require(actor, {"expert"})
    cursor_id = decode_review_cursor(cursor, status)
    rows = await HealthPlanRepository(reader).review_page(
        cursor_id=cursor_id, status=status, limit=limit + 1
    )
    page_rows = rows[:limit]
    plans = await HealthPlanRepository(plan_reader).plan_details(
        tuple(_uuid(row["plan_id"]) for row in page_rows)
    )
    plans_by_id = {_uuid(row["plan_id"]): row for row in plans}
    try:
        items = tuple(
            review_list_item(row, plans_by_id[_uuid(row["plan_id"])]) for row in page_rows
        )
    except KeyError:
        raise _error("DEPENDENCY_UNAVAILABLE") from None
    next_cursor = (
        encode_review_cursor(_uuid(page_rows[-1]["review_id"]), status)
        if len(rows) > limit
        else None
    )
    response.headers["Cache-Control"] = "no-store"
    return PlanReviewPageDTO(items=items, next_cursor=next_cursor)


async def _review_detail(
    review_id: UUID, review_reader, plan_reader, assessment_reader
) -> PlanReviewDetailDTO:
    review_repository = HealthPlanRepository(review_reader)
    row = await review_repository.review_detail(review_id)
    if row is None:
        raise _error("PLAN_NOT_FOUND")
    plan_repository = HealthPlanRepository(plan_reader)
    plan = await plan_repository.plan_detail(_uuid(row["plan_id"]))
    if plan is None:
        raise _error("DEPENDENCY_UNAVAILABLE")
    previous_plan = await plan_repository.previous_plan_detail(
        service_case_id=_uuid(row["service_case_id"]),
        plan_version_no=int(row["plan_version_no"]),
    )
    plan_history = await plan_repository.plan_history(
        service_case_id=_uuid(row["service_case_id"]),
        through_version_no=int(row["plan_version_no"]),
    )
    review_history = await review_repository.review_history(
        service_case_id=_uuid(row["service_case_id"]),
        through_version_no=int(row["plan_version_no"]),
    )
    assessment_context = await HealthPlanRepository(
        assessment_reader
    ).formal_assessment_context(
        service_case_id=_uuid(row["service_case_id"]),
        plan_created_at=plan["created_at"],
    )
    if assessment_context is None:
        raise _error("DEPENDENCY_UNAVAILABLE")
    confirmed_plan = await plan_repository.plan_detail(_uuid(row["plan_id"]))
    confirmed_row = await review_repository.review_detail(review_id)
    if confirmed_plan is None or confirmed_row is None:
        raise _error("DEPENDENCY_UNAVAILABLE")
    return review_detail_dto(
        row,
        plan,
        previous_plan,
        confirmed_review=confirmed_row,
        confirmed_plan=confirmed_plan,
        assessment_context=assessment_context,
        plan_history=tuple(plan_history),
        review_history=tuple(review_history),
    )


@platform_router.get(
    "/health-plan-reviews/{review_id}",
    response_model=PlanReviewDetailDTO,
    responses={200: {"headers": _NO_STORE}},
)
async def review_detail(
    review_id: UuidV7,
    response: Response,
    actor: CurrentUser = Depends(get_current_user_from_jwt),
    reader=Depends(get_slice6_review_writer_session),
    plan_reader=Depends(get_slice6_clinical_reader_session),
    assessment_reader=Depends(get_slice5_clinical_reader_session),
):
    _require(actor, {"expert"})
    result = await _review_detail(review_id, reader, plan_reader, assessment_reader)
    response.headers["Cache-Control"] = "no-store"
    return result


@platform_router.post(
    "/health-plan-reviews/{review_id}/claim",
    response_model=PlanReviewDetailDTO,
    responses={200: {"headers": _NO_STORE}},
)
async def claim_review(
    payload: ClaimRequest,
    review_id: UuidV7,
    idempotency_key: IdempotencyKey,
    response: Response,
    actor: CurrentUser = Depends(get_current_user_from_jwt),
    writer=Depends(get_slice6_review_writer_session),
    plan_reader=Depends(get_slice6_clinical_reader_session),
    assessment_reader=Depends(get_slice5_clinical_reader_session),
):
    _require(actor, {"expert"})
    repository = HealthPlanRepository(writer)
    await claim_plan_review(repository, review_id=review_id, expected_version=payload.expected_version, actor_user_id=actor.id, actor_role=actor.role, idempotency_key=idempotency_key, now=datetime.now(timezone.utc), id_factory=Uuid7Generator().generate)
    await _commit_mutation(writer, repository, "review_writer")
    result = await _review_detail(review_id, writer, plan_reader, assessment_reader)
    response.headers["Cache-Control"] = "no-store"
    return result


@platform_router.post(
    "/health-plan-reviews/{review_id}/decision",
    response_model=PlanReviewDetailDTO,
    responses={200: {"headers": _NO_STORE}},
)
async def review_decision(
    payload: ReviewDecisionRequest,
    review_id: UuidV7,
    idempotency_key: IdempotencyKey,
    response: Response,
    actor: CurrentUser = Depends(get_current_user_from_jwt),
    writer=Depends(get_slice6_review_writer_session),
    plan_reader=Depends(get_slice6_clinical_reader_session),
    assessment_reader=Depends(get_slice5_clinical_reader_session),
):
    _require(actor, {"expert"})
    repository = HealthPlanRepository(writer)
    await review_plan(repository, review_id=review_id, decision=payload.decision, reason_codes=payload.reason_codes, expected_version=payload.expected_version, actor_user_id=actor.id, actor_role=actor.role, idempotency_key=idempotency_key, now=datetime.now(timezone.utc), id_factory=Uuid7Generator().generate)
    await _commit_mutation(writer, repository, "review_writer")
    result = await _review_detail(review_id, writer, plan_reader, assessment_reader)
    response.headers["Cache-Control"] = "no-store"
    return result


@therapist_router.get("/service-cases/{service_case_id}/plans", response_model=PlanPageDTO)
async def therapist_plans(service_case_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), reader=Depends(get_slice6_clinical_reader_session)):
    _require(actor, {"therapist"})
    return await _plan_page(service_case_id, actor, reader)


@therapist_router.get("/plans/{plan_id}", response_model=PlanDetailDTO, responses={200: {"headers": _NO_STORE}})
async def therapist_plan(plan_id: UuidV7, response: Response, actor: CurrentUser = Depends(get_current_user_from_jwt), reader=Depends(get_slice6_clinical_reader_session)):
    _require(actor, {"therapist"})
    response.headers["Cache-Control"] = "no-store"
    return await _plan_detail(plan_id, actor, reader)


@therapist_router.post("/plans/{plan_id}/explanations", response_model=PlanDetailDTO, responses={200: {"headers": _NO_STORE}})
async def explain(payload: PlanExplanationRequest, plan_id: UuidV7, idempotency_key: IdempotencyKey, response: Response, actor: CurrentUser = Depends(get_current_user_from_jwt), writer=Depends(get_slice6_institution_writer_session), reader=Depends(get_slice6_clinical_reader_session)):
    _require(actor, {"therapist"})
    await _plan_detail(plan_id, actor, reader)
    await reader.rollback()
    repository = HealthPlanRepository(writer)
    await explain_plan(repository, plan_id=plan_id, explanation_codes=payload.explanation_codes, expected_version=payload.expected_version, actor_user_id=actor.id, actor_role=actor.role, idempotency_key=idempotency_key, now=datetime.now(timezone.utc), id_factory=Uuid7Generator().generate)
    await _commit_mutation(writer, repository, "institution_writer")
    response.headers["Cache-Control"] = "no-store"
    return await _plan_detail(plan_id, actor, reader)


@family_router.get("/service-cases/{service_case_id}/plans", response_model=PlanPageDTO)
async def family_plans(service_case_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), reader=Depends(get_slice6_family_reader_session)):
    _require(actor, {"member"})
    return await _plan_page(service_case_id, actor, reader)


@family_router.get("/plans/{plan_id}", response_model=PlanDetailDTO, responses={200: {"headers": _NO_STORE}})
async def family_plan(plan_id: UuidV7, response: Response, actor: CurrentUser = Depends(get_current_user_from_jwt), reader=Depends(get_slice6_family_reader_session)):
    _require(actor, {"member"})
    response.headers["Cache-Control"] = "no-store"
    return await _plan_detail(plan_id, actor, reader)


@family_router.post("/plans/{plan_id}/decision", response_model=PlanDetailDTO, responses={200: {"headers": _NO_STORE}})
async def user_decision(payload: UserDecisionRequest, plan_id: UuidV7, idempotency_key: IdempotencyKey, response: Response, actor: CurrentUser = Depends(get_current_user_from_jwt), writer=Depends(get_slice6_institution_writer_session), reader=Depends(get_slice6_family_reader_session)):
    _require(actor, {"member"})
    await _plan_detail(plan_id, actor, reader)
    await reader.rollback()
    repository = HealthPlanRepository(writer)
    await decide_plan(repository, plan_id=plan_id, decision=payload.decision, expected_version=payload.expected_version, actor_user_id=actor.id, actor_role=actor.role, actor_context="AUTO", proxy_grant_id=None, idempotency_key=idempotency_key, now=datetime.now(timezone.utc), id_factory=Uuid7Generator().generate)
    await _commit_mutation(writer, repository, "institution_writer")
    response.headers["Cache-Control"] = "no-store"
    return await _plan_detail(plan_id, actor, reader)
