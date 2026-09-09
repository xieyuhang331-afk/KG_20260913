from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal, Mapping, TypeVar, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel

from app.core.database import (
    get_institution_onboarding_reader_session,
    get_slice7_case_writer_session,
    get_slice7_family_reader_session,
    get_slice7_milestone_writer_session,
    get_slice7_oversight_reader_session,
    get_slice7_session_factory,
    get_slice7_transfer_writer_session,
)
from app.core.security import (
    CurrentUser,
    decode_access_token_for_step_up,
    get_current_user_from_jwt,
)
from app.core.uuid_generator import Uuid7Generator
from app.core.接口合同 import error_response

from .domain import SystemBusinessClock
from .ports import ExportArchiveAccessPort
from .repository import ServiceFulfillmentRepository, ServiceFulfillmentRepositoryError
from .schemas import (
    CaseStatusValue,
    CaseTransitionRequest,
    ClosingAssessmentCreateRequest,
    ClosingAssessmentDTO,
    ContinuationCaseLinkRequest,
    ContinuationHandoffDTO,
    DataExportCancelRequest,
    DataExportCreateRequest,
    DataExportDTO,
    DataExportPageDTO,
    DownloadAccessRequest,
    ExportStatusValue,
    MilestoneCompleteRequest,
    MilestoneDTO,
    MilestonePageDTO,
    MilestoneStatusValue,
    OneTimeDownloadDTO,
    ProxyMajorAuthorizationCreateRequest,
    ProxyMajorAuthorizationDTO,
    ProxyMajorAuthorizationRevokeRequest,
    SafetyTerminateRequest,
    ServiceFulfillmentDTO,
    ServiceFulfillmentPageDTO,
    ServiceSummaryCreateRequest,
    ServiceSummaryDTO,
    SummaryAcknowledgeRequest,
    TransferCreateRequest,
    TransferDecisionRequest,
    TransferDTO,
    TransferPageDTO,
    TransferScopeConfirmRequest,
    TransferSourceCloseRequest,
    TransferStatusValue,
    UnableToContactRequest,
    UuidV7,
)
from .service import (
    CommitOutcome,
    ServiceFulfillmentError,
    ServiceFulfillmentService,
    commit_with_confirmation,
    decode_page_cursor,
    encode_page_cursor,
)

_STATUS = {
    "UNAUTHENTICATED": 401,
    "ROLE_FORBIDDEN": 403,
    "CURRENTNESS_FORBIDDEN": 403,
    "PROXY_PERMISSION_FORBIDDEN": 403,
    "STEP_UP_FORBIDDEN": 403,
    "SERVICE_CASE_NOT_FOUND": 404,
    "RESOURCE_NOT_FOUND": 404,
    "MILESTONE_NOT_FOUND": 404,
    "TRANSFER_NOT_FOUND": 404,
    "HANDOFF_NOT_FOUND": 404,
    "EXPORT_NOT_FOUND": 404,
    "VERSION_CONFLICT": 409,
    "STALE_VERSION": 409,
    "IDEMPOTENCY_CONFLICT": 409,
    "MILESTONE_WINDOW_CLOSED": 409,
    "CASE_STATE_CONFLICT": 409,
    "HIGH_RISK_BLOCKS_COMPLETION": 409,
    "CLOSURE_PREREQUISITE_MISSING": 409,
    "TRANSFER_STATE_CONFLICT": 409,
    "EXPORT_NOT_READY": 409,
    "INVALID_REQUEST": 422,
    "DEPENDENCY_UNAVAILABLE": 503,
    "COMMIT_NOT_COMMITTED": 503,
    "COMMIT_OUTCOME_UNKNOWN": 503,
}


_SLICE7_BASE_ERROR_CODES = (
    "UNAUTHENTICATED",
    "ROLE_FORBIDDEN",
    "INVALID_REQUEST",
    "DEPENDENCY_UNAVAILABLE",
)
_SLICE7_MUTATION_ERROR_CODES = (
    *_SLICE7_BASE_ERROR_CODES,
    "RESOURCE_NOT_FOUND",
    "VERSION_CONFLICT",
    "STALE_VERSION",
    "IDEMPOTENCY_CONFLICT",
    "COMMIT_NOT_COMMITTED",
    "COMMIT_OUTCOME_UNKNOWN",
    "CURRENTNESS_FORBIDDEN",
)


def _slice7_error_codes(
    *specific: str,
    mutation: bool = False,
) -> tuple[str, ...]:
    common = (
        _SLICE7_MUTATION_ERROR_CODES
        if mutation
        else _SLICE7_BASE_ERROR_CODES
    )
    return tuple(dict.fromkeys((*common, *specific)))


# OpenAPI-only reachability catalog. Runtime error admission remains the global
# typed _STATUS whitelist in Slice7Route; this catalog must not narrow it.
SLICE7_ROUTE_ERROR_CODES: dict[tuple[str, str], tuple[str, ...]] = {
    ("GET", "/api/v1/therapist/service-cases/{case_id}/fulfillment"): _slice7_error_codes(),
    ("GET", "/api/v1/therapist/service-cases/{case_id}/milestones"): _slice7_error_codes(),
    ("GET", "/api/v1/therapist/milestones/{milestone_id}"): _slice7_error_codes("MILESTONE_NOT_FOUND"),
    ("POST", "/api/v1/therapist/milestones/{milestone_id}/complete"): _slice7_error_codes("CASE_STATE_CONFLICT", "MILESTONE_WINDOW_CLOSED", mutation=True),
    ("POST", "/api/v1/therapist/service-cases/{case_id}/pause"): _slice7_error_codes("CASE_STATE_CONFLICT", mutation=True),
    ("POST", "/api/v1/therapist/service-cases/{case_id}/resume"): _slice7_error_codes("CASE_STATE_CONFLICT", mutation=True),
    ("POST", "/api/v1/therapist/service-cases/{case_id}/closing-assessments"): _slice7_error_codes("CASE_STATE_CONFLICT", "CLOSURE_PREREQUISITE_MISSING", mutation=True),
    ("POST", "/api/v1/therapist/service-cases/{case_id}/summaries"): _slice7_error_codes("CASE_STATE_CONFLICT", "CLOSURE_PREREQUISITE_MISSING", mutation=True),
    ("POST", "/api/v1/therapist/service-cases/{case_id}/unable-to-contact"): _slice7_error_codes(mutation=True),
    ("POST", "/api/v1/therapist/service-cases/{case_id}/safety-terminate"): _slice7_error_codes(mutation=True),
    ("POST", "/api/v1/institutions/service-cases/{case_id}/pause"): _slice7_error_codes("CASE_STATE_CONFLICT", mutation=True),
    ("POST", "/api/v1/institutions/service-cases/{case_id}/resume"): _slice7_error_codes("CASE_STATE_CONFLICT", mutation=True),
    ("POST", "/api/v1/institutions/service-cases/{case_id}/terminate"): _slice7_error_codes(mutation=True),
    ("GET", "/api/v1/institutions/service-cases/{case_id}/fulfillment"): _slice7_error_codes(),
    ("GET", "/api/v1/institutions/service-cases"): _slice7_error_codes(),
    ("POST", "/api/v1/institutions/service-transfers/{transfer_id}/start-review"): _slice7_error_codes("TRANSFER_STATE_CONFLICT", mutation=True),
    ("POST", "/api/v1/institutions/service-transfers/{transfer_id}/accept"): _slice7_error_codes("TRANSFER_STATE_CONFLICT", "CURRENTNESS_FORBIDDEN", mutation=True),
    ("POST", "/api/v1/institutions/service-transfers/{transfer_id}/reject"): _slice7_error_codes("TRANSFER_STATE_CONFLICT", mutation=True),
    ("POST", "/api/v1/institutions/service-transfers/{transfer_id}/source-close"): _slice7_error_codes("TRANSFER_STATE_CONFLICT", mutation=True),
    ("GET", "/api/v1/institutions/service-transfers/{transfer_id}/continuation-handoff"): _slice7_error_codes("HANDOFF_NOT_FOUND"),
    ("POST", "/api/v1/institutions/service-transfers/{transfer_id}/continuation-case"): _slice7_error_codes("TRANSFER_STATE_CONFLICT", "CURRENTNESS_FORBIDDEN", mutation=True),
    ("GET", "/api/v1/institutions/service-transfers"): _slice7_error_codes(),
    ("GET", "/api/v1/institutions/service-transfers/{transfer_id}"): _slice7_error_codes("TRANSFER_NOT_FOUND"),
    ("POST", "/api/v1/family/service-cases/{case_id}/withdraw"): _slice7_error_codes(mutation=True),
    ("GET", "/api/v1/family/service-cases/{case_id}/fulfillment"): _slice7_error_codes(),
    ("GET", "/api/v1/family/service-cases/{case_id}/milestones"): _slice7_error_codes(),
    ("GET", "/api/v1/family/service-cases/{case_id}/summaries/current"): _slice7_error_codes(),
    ("POST", "/api/v1/family/service-summaries/{summary_id}/acknowledge"): _slice7_error_codes("CLOSURE_PREREQUISITE_MISSING", mutation=True),
    ("POST", "/api/v1/family/service-cases/{case_id}/transfers"): _slice7_error_codes("CURRENTNESS_FORBIDDEN", mutation=True),
    ("GET", "/api/v1/family/service-transfers/{transfer_id}"): _slice7_error_codes("TRANSFER_NOT_FOUND"),
    ("POST", "/api/v1/family/service-transfers/{transfer_id}/cancel"): _slice7_error_codes("TRANSFER_STATE_CONFLICT", mutation=True),
    ("POST", "/api/v1/family/service-transfers/{transfer_id}/confirm-scope"): _slice7_error_codes("TRANSFER_STATE_CONFLICT", mutation=True),
    ("POST", "/api/v1/family/data-exports"): _slice7_error_codes("STEP_UP_FORBIDDEN", "PROXY_PERMISSION_FORBIDDEN", mutation=True),
    ("GET", "/api/v1/family/data-exports/{export_id}"): _slice7_error_codes("EXPORT_NOT_FOUND", "PROXY_PERMISSION_FORBIDDEN"),
    ("POST", "/api/v1/family/data-exports/{export_id}/download-access"): _slice7_error_codes("STEP_UP_FORBIDDEN", "PROXY_PERMISSION_FORBIDDEN", "EXPORT_NOT_READY", mutation=True),
    ("POST", "/api/v1/family/data-exports/{export_id}/cancel"): _slice7_error_codes("PROXY_PERMISSION_FORBIDDEN", "EXPORT_NOT_READY", mutation=True),
    ("POST", "/api/v1/platform/service-cases/{case_id}/safety-terminate"): _slice7_error_codes(mutation=True),
    ("POST", "/api/v1/platform/service-transfers/{transfer_id}/coordinate-close"): _slice7_error_codes("TRANSFER_STATE_CONFLICT", "CURRENTNESS_FORBIDDEN", mutation=True),
    ("POST", "/api/v1/platform/proxy-major-authorizations"): _slice7_error_codes("CURRENTNESS_FORBIDDEN", mutation=True),
    ("POST", "/api/v1/platform/proxy-major-authorizations/{authorization_id}/revoke"): _slice7_error_codes("CURRENTNESS_FORBIDDEN", mutation=True),
    ("GET", "/api/v1/platform/service-transfers"): _slice7_error_codes(),
    ("GET", "/api/v1/platform/service-transfers/{transfer_id}"): _slice7_error_codes("TRANSFER_NOT_FOUND"),
    ("GET", "/api/v1/platform/service-fulfillment/cases"): _slice7_error_codes(),
    ("GET", "/api/v1/platform/service-fulfillment/cases/{case_id}"): _slice7_error_codes(),
    ("GET", "/api/v1/platform/data-exports"): _slice7_error_codes(),
    ("GET", "/api/v1/platform/data-exports/{export_id}"): _slice7_error_codes("EXPORT_NOT_FOUND"),
}


async def consume_personal_data_export_download(session, values: Mapping[str, object]) -> bool:
    repository = ServiceFulfillmentRepository(session)
    consumed = await repository.consume_export_download(values)
    if consumed:
        async def confirm() -> CommitOutcome:
            factory = await get_slice7_session_factory("transfer_writer")
            async with factory() as fresh:
                confirmed = await ServiceFulfillmentRepository(
                    fresh
                ).confirm_export_download(values)
                return (
                    CommitOutcome.COMMITTED
                    if confirmed
                    else CommitOutcome.UNKNOWN
                )

        await commit_with_confirmation(session, confirm=confirm)
    else:
        await session.rollback()
    return consumed


def _registered_service_error(
    exc: ServiceFulfillmentError | ServiceFulfillmentRepositoryError,
) -> str | None:
    if len(exc.args) != 1 or type(exc.args[0]) is not str:
        return None
    return exc.args[0] if exc.args[0] in _STATUS else None


class Slice7Route(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                response = await original(request)
            except RequestValidationError:
                response = error_response(request, 422, "INVALID_REQUEST")
            except HTTPException as exc:
                detail = exc.detail
                code = detail.get("code") if isinstance(detail, dict) else None
                if exc.status_code == 401:
                    code = "UNAUTHENTICATED"
                if code not in _STATUS:
                    code = "INVALID_REQUEST" if exc.status_code < 500 else "DEPENDENCY_UNAVAILABLE"
                status = _STATUS[code]
                response = error_response(
                    request, status, code,
                    retryable=status == 503 and code != "COMMIT_OUTCOME_UNKNOWN",
                    headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
                )
            except (ServiceFulfillmentError, ServiceFulfillmentRepositoryError) as exc:
                code = _registered_service_error(exc)
                if code is None:
                    response = error_response(request, 500, "INTERNAL_ERROR")
                else:
                    status = _STATUS[code]
                    response = error_response(
                        request, status, code,
                        retryable=status == 503 and code != "COMMIT_OUTCOME_UNKNOWN",
                    )
            except Exception:
                response = error_response(request, 500, "INTERNAL_ERROR")
            if request.method == "GET":
                response.headers.setdefault("Cache-Control", "no-store")
            return response

        return handler


def _router(prefix: str, tag: str) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=[tag])
    router.route_class = Slice7Route
    return router


therapist_router = _router("/api/v1/therapist", "service-fulfillment-therapist")
institution_router = _router("/api/v1/institutions", "service-fulfillment-institution")
family_router = _router("/api/v1/family", "service-fulfillment-family")
platform_router = _router("/api/v1/platform", "service-fulfillment-platform")
routers = (therapist_router, institution_router, family_router, platform_router)

IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]
StepUpToken = Annotated[str, Header(alias="X-Step-Up-Token", min_length=1, max_length=1024)]
T = TypeVar("T", bound=BaseModel)
_STEP_UP_MAX_AGE_SECONDS = 10 * 60


def strip_slice7_validation_responses(schema: dict[str, object]) -> dict[str, object]:
    paths = schema.get("paths", {})
    if not isinstance(paths, dict):
        return schema
    for (method, path), codes in SLICE7_ROUTE_ERROR_CODES.items():
        path_item = paths.get(path)
        if not isinstance(path_item, dict):
            continue
        operation = path_item.get(method.lower())
        if not isinstance(operation, dict) or "responses" not in operation:
            continue
        statuses = {str(_STATUS[code]) for code in codes} | {"422", "500"}
        for status in statuses:
            example_code = next(
                (code for code in codes if _STATUS[code] == int(status)),
                "INTERNAL_ERROR" if status == "500" else "INVALID_REQUEST",
            )
            response = operation.setdefault("responses", {}).setdefault(
                status, {"description": "Request rejected"}
            )
            examples = {"rejected": {"value": {
                "code": example_code,
                "message": "request rejected",
                "request_id": "01990000-0000-7000-8000-000000000201",
                "retryable": status == "503" and example_code != "COMMIT_OUTCOME_UNKNOWN",
                "field_errors": [],
            }}}
            if status in {"401", "503"}:
                examples["authentication"] = {"value": {
                    "code": "UNAUTHENTICATED" if status == "401" else "DEPENDENCY_UNAVAILABLE",
                    "message": "request rejected",
                    "request_id": "01990000-0000-7000-8000-000000000201",
                    "retryable": status == "503",
                    "field_errors": [],
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
                headers["WWW-Authenticate"] = {
                    "schema": {"type": "string", "enum": ["Bearer"]}
                }
            if status == "503":
                headers["Retry-After"] = {"schema": {"type": "integer", "minimum": 1}}
        operation["x-symbolic-error-codes"] = codes
        if method == "GET":
            success = operation["responses"].get("200")
            if isinstance(success, dict):
                success.setdefault("headers", {})["Cache-Control"] = {
                    "schema": {"type": "string", "const": "no-store"}
                }
    return schema


def _require(actor: CurrentUser, roles: set[str]) -> None:
    if actor.role not in roles:
        raise ServiceFulfillmentError("ROLE_FORBIDDEN")


def _require_current_step_up(token: str, actor: CurrentUser) -> None:
    try:
        claims = decode_access_token_for_step_up(token)
    except HTTPException:
        raise ServiceFulfillmentError("STEP_UP_FORBIDDEN") from None
    now = int(datetime.now(timezone.utc).timestamp())
    if (
        claims.get("sub") != str(actor.id)
        or claims.get("role") != actor.role
        or claims.get("tenant_id") != actor.tenant_id
        or now - int(claims["iat"]) > _STEP_UP_MAX_AGE_SECONDS
    ):
        raise ServiceFulfillmentError("STEP_UP_FORBIDDEN")


def _dto(model: type[T], row: Mapping[str, object]) -> T:
    return model.model_validate({key: row.get(key) for key in model.model_fields})


def _service(repository: ServiceFulfillmentRepository) -> ServiceFulfillmentService:
    return ServiceFulfillmentService(repository, SystemBusinessClock(), Uuid7Generator().generate)


async def _commit_mutation(
    session,
    repository: ServiceFulfillmentRepository,
    runtime_kind: str,
) -> None:
    if repository.mutation_replayed:
        await session.commit()
        return
    confirmation = repository.mutation_confirmation
    if confirmation is None:
        raise ServiceFulfillmentError("COMMIT_OUTCOME_UNKNOWN")

    async def confirm() -> CommitOutcome:
        factory = await get_slice7_session_factory(runtime_kind)
        async with factory() as fresh:
            row = await ServiceFulfillmentRepository(fresh).confirm_mutation_outcome(
                confirmation
            )
            return CommitOutcome(str(row["outcome"]))

    await commit_with_confirmation(session, confirm=confirm)


async def _read_one(session, resource: str, target_id: UUID, actor: CurrentUser) -> dict:
    row = await ServiceFulfillmentRepository(session).read_one(resource, target_id, actor.id, actor.role)
    if row is None:
        raise ServiceFulfillmentError(f"{resource}_NOT_FOUND")
    return row


_PAGE_ID_FIELDS = {
    "EXPORT": "export_id",
    "FULFILLMENT": "service_case_id",
    "MILESTONE": "milestone_id",
    "TRANSFER": "transfer_id",
}


async def _read_many(
    session,
    resource: str,
    scope: UUID | None,
    actor: CurrentUser,
    cursor: str | None,
    limit: int,
    *,
    status: str | None = None,
    risk: str | None = None,
) -> tuple[list[dict], str | None]:
    filters = {"risk": risk, "status": status}
    cursor_id, snapshot_ceiling = decode_page_cursor(
        cursor,
        resource=resource,
        scope_id=scope,
        actor_user_id=actor.id,
        actor_role=actor.role,
        actor_tenant_id=actor.tenant_id,
        filters=filters,
    )
    repository = ServiceFulfillmentRepository(session)
    rows, resolved_ceiling = await repository.read_many(
        resource,
        scope,
        actor.id,
        actor.role,
        cursor_id,
        snapshot_ceiling,
        limit,
        status,
        risk,
    )
    next_cursor = None
    if len(rows) == limit:
        last_id = UUID(str(rows[-1][_PAGE_ID_FIELDS[resource]]))
        probe, probe_ceiling = await repository.read_many(
            resource,
            scope,
            actor.id,
            actor.role,
            last_id,
            resolved_ceiling,
            1,
            status,
            risk,
        )
        if probe and resolved_ceiling is not None and probe_ceiling == resolved_ceiling:
            next_cursor = encode_page_cursor(
                last_id,
                snapshot_ceiling=resolved_ceiling,
                resource=resource,
                scope_id=scope,
                actor_user_id=actor.id,
                actor_role=actor.role,
                actor_tenant_id=actor.tenant_id,
                filters=filters,
            )
    return rows, next_cursor


async def _transition(session, *, operation: str, target_id: UUID | None, actor: CurrentUser, key: str, request: Mapping[str, object], kind: str) -> dict:
    repository = ServiceFulfillmentRepository(session)
    service = _service(repository)
    if kind == "transfer":
        row = await service.transfer_transition(operation=operation, transfer_id=target_id, actor_user_id=actor.id, actor_role=actor.role, actor_tenant_id=actor.tenant_id, idempotency_key=key, request=request)
    elif kind == "major_authorization":
        row = await service.major_authorization_transition(operation=operation, target_id=target_id, actor_user_id=actor.id, actor_role=actor.role, idempotency_key=key, request=request)
    elif kind == "export":
        row = await service.export_transition(operation=operation, export_or_member_id=target_id, actor_user_id=actor.id, actor_role=actor.role, actor_tenant_id=actor.tenant_id, idempotency_key=key, request=request)
    else:
        row = await service.case_transition(operation=operation, service_case_id=target_id, actor_user_id=actor.id, actor_role=actor.role, actor_tenant_id=actor.tenant_id, idempotency_key=key, request=request)
    await _commit_mutation(
        session,
        repository,
        "case_writer" if kind == "case" else "transfer_writer",
    )
    return row


@therapist_router.get("/service-cases/{case_id}/fulfillment", response_model=ServiceFulfillmentDTO)
async def therapist_fulfillment(case_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_milestone_writer_session)):
    _require(actor, {"therapist"})
    return _dto(ServiceFulfillmentDTO, await _read_one(session, "FULFILLMENT", case_id, actor))


@therapist_router.get("/service-cases/{case_id}/milestones", response_model=MilestonePageDTO)
async def therapist_milestones(case_id: UuidV7, cursor: str | None = Query(None, max_length=1024), limit: int = Query(50, ge=1, le=100), status: MilestoneStatusValue | None = Query(None), actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_milestone_writer_session)):
    _require(actor, {"therapist"})
    rows, next_cursor = await _read_many(session, "MILESTONE", case_id, actor, cursor, limit, status=status)
    return MilestonePageDTO(items=tuple(_dto(MilestoneDTO, row) for row in rows), next_cursor=next_cursor)


@therapist_router.get("/milestones/{milestone_id}", response_model=MilestoneDTO)
async def therapist_milestone(milestone_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_milestone_writer_session)):
    _require(actor, {"therapist"})
    return _dto(MilestoneDTO, await _read_one(session, "MILESTONE", milestone_id, actor))


@therapist_router.post("/milestones/{milestone_id}/complete", response_model=MilestoneDTO)
async def complete_milestone(milestone_id: UuidV7, payload: MilestoneCompleteRequest, key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_milestone_writer_session)):
    _require(actor, {"therapist"})
    repository = ServiceFulfillmentRepository(session)
    row = await _service(repository).complete_milestone(milestone_id=milestone_id, actor_user_id=actor.id, actor_role=actor.role, actor_tenant_id=actor.tenant_id or 0, idempotency_key=key, expected_version=payload.expected_version, record_summary=payload.record_summary, evidence_refs=payload.evidence_refs)
    await _commit_mutation(session, repository, "milestone_writer")
    return _dto(MilestoneDTO, row)


def _case_action(path: str, operation: str, roles: set[str], router: APIRouter, status_code: int = 200):
    async def endpoint(case_id: UuidV7, payload: CaseTransitionRequest, key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_case_writer_session)):
        _require(actor, roles)
        row = await _transition(session, operation=operation, target_id=case_id, actor=actor, key=key, request=payload.model_dump(), kind="case")
        return _dto(ServiceFulfillmentDTO, row)
    router.add_api_route(path, endpoint, methods=["POST"], response_model=ServiceFulfillmentDTO, status_code=status_code, name=operation.lower())


for _router_value, _roles_value in ((therapist_router, {"therapist"}), (institution_router, {"org_admin"})):
    _case_action("/service-cases/{case_id}/pause", "PAUSE_CASE", _roles_value, _router_value)
    _case_action("/service-cases/{case_id}/resume", "RESUME_CASE", _roles_value, _router_value)
_case_action("/service-cases/{case_id}/terminate", "TERMINATE_CASE", {"org_admin"}, institution_router)
_case_action("/service-cases/{case_id}/withdraw", "WITHDRAW_CASE", {"member"}, family_router)
_case_action("/service-cases/{case_id}/safety-terminate", "SAFETY_TERMINATE", {"super_admin", "sys_admin"}, platform_router)


@therapist_router.post("/service-cases/{case_id}/closing-assessments", response_model=ClosingAssessmentDTO, status_code=201)
async def create_closing_assessment(case_id: UuidV7, payload: ClosingAssessmentCreateRequest, key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_case_writer_session)):
    _require(actor, {"therapist"})
    row = await _transition(session, operation="CREATE_CLOSING_ASSESSMENT", target_id=case_id, actor=actor, key=key, request=payload.model_dump(), kind="case")
    return _dto(ClosingAssessmentDTO, row)


@therapist_router.post("/service-cases/{case_id}/summaries", response_model=ServiceSummaryDTO, status_code=201)
async def create_summary(case_id: UuidV7, payload: ServiceSummaryCreateRequest, key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_case_writer_session)):
    _require(actor, {"therapist"})
    row = await _transition(session, operation="CREATE_SUMMARY", target_id=case_id, actor=actor, key=key, request=payload.model_dump(), kind="case")
    return _dto(ServiceSummaryDTO, row)


@therapist_router.post("/service-cases/{case_id}/unable-to-contact", response_model=ServiceFulfillmentDTO)
async def unable_to_contact(case_id: UuidV7, payload: UnableToContactRequest, key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_case_writer_session)):
    _require(actor, {"therapist"})
    return _dto(ServiceFulfillmentDTO, await _transition(session, operation="UNABLE_TO_CONTACT", target_id=case_id, actor=actor, key=key, request=payload.model_dump(), kind="case"))


@therapist_router.post("/service-cases/{case_id}/safety-terminate", response_model=ServiceFulfillmentDTO)
async def therapist_safety_terminate(case_id: UuidV7, payload: SafetyTerminateRequest, key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_case_writer_session)):
    _require(actor, {"therapist"})
    return _dto(ServiceFulfillmentDTO, await _transition(session, operation="SAFETY_TERMINATE", target_id=case_id, actor=actor, key=key, request=payload.model_dump(), kind="case"))


@institution_router.get("/service-cases/{case_id}/fulfillment", response_model=ServiceFulfillmentDTO)
async def institution_fulfillment(case_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_case_writer_session)):
    _require(actor, {"org_admin", "org_operator"})
    return _dto(ServiceFulfillmentDTO, await _read_one(session, "FULFILLMENT", case_id, actor))


@institution_router.get("/service-cases", response_model=ServiceFulfillmentPageDTO)
async def institution_cases(cursor: str | None = Query(None, max_length=1024), limit: int = Query(50, ge=1, le=100), status: CaseStatusValue | None = Query(None), risk: Literal["AT_RISK"] | None = Query(None), actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_case_writer_session)):
    _require(actor, {"org_admin", "org_operator"})
    rows, next_cursor = await _read_many(session, "FULFILLMENT", None, actor, cursor, limit, status=status, risk=risk)
    return ServiceFulfillmentPageDTO(items=tuple(_dto(ServiceFulfillmentDTO, row) for row in rows), next_cursor=next_cursor)


@family_router.get("/service-cases/{case_id}/fulfillment", response_model=ServiceFulfillmentDTO)
async def family_fulfillment(case_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_family_reader_session)):
    _require(actor, {"member"})
    return _dto(ServiceFulfillmentDTO, await _read_one(session, "FULFILLMENT", case_id, actor))


@family_router.get("/service-cases/{case_id}/milestones", response_model=MilestonePageDTO)
async def family_milestones(case_id: UuidV7, cursor: str | None = Query(None, max_length=1024), limit: int = Query(50, ge=1, le=100), status: MilestoneStatusValue | None = Query(None), actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_family_reader_session)):
    _require(actor, {"member"})
    rows, next_cursor = await _read_many(session, "MILESTONE", case_id, actor, cursor, limit, status=status)
    return MilestonePageDTO(items=tuple(_dto(MilestoneDTO, row) for row in rows), next_cursor=next_cursor)


@family_router.get("/service-cases/{case_id}/summaries/current", response_model=ServiceSummaryDTO)
async def family_summary(case_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_family_reader_session)):
    _require(actor, {"member"})
    return _dto(ServiceSummaryDTO, await _read_one(session, "SUMMARY", case_id, actor))


@family_router.post("/service-summaries/{summary_id}/acknowledge", response_model=ServiceSummaryDTO)
async def acknowledge_summary(summary_id: UuidV7, payload: SummaryAcknowledgeRequest, key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_case_writer_session)):
    _require(actor, {"member"})
    return _dto(ServiceSummaryDTO, await _transition(session, operation="ACK_SUMMARY", target_id=summary_id, actor=actor, key=key, request=payload.model_dump(), kind="case"))


@family_router.post("/service-cases/{case_id}/transfers", response_model=TransferDTO, status_code=201)
async def create_transfer(case_id: UuidV7, payload: TransferCreateRequest, key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_transfer_writer_session)):
    _require(actor, {"member"})
    return _dto(TransferDTO, await _transition(session, operation="CREATE_TRANSFER", target_id=case_id, actor=actor, key=key, request=payload.model_dump(), kind="transfer"))


@family_router.get("/service-transfers/{transfer_id}", response_model=TransferDTO)
async def family_transfer(transfer_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_family_reader_session)):
    _require(actor, {"member"})
    return _dto(TransferDTO, await _read_one(session, "TRANSFER", transfer_id, actor))


def _transfer_action(path: str, operation: str, roles: set[str], router: APIRouter, model: type[BaseModel]):
    async def endpoint(transfer_id, payload, key, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_transfer_writer_session)):
        _require(actor, roles)
        row = await _transition(session, operation=operation, target_id=transfer_id, actor=actor, key=key, request=payload.model_dump(), kind="transfer")
        return _dto(TransferDTO, row)
    endpoint.__annotations__.update(
        {"transfer_id": UuidV7, "payload": model, "key": IdempotencyKey}
    )
    router.add_api_route(path, endpoint, methods=["POST"], response_model=TransferDTO, name=operation.lower())


_transfer_action("/service-transfers/{transfer_id}/cancel", "CANCEL_TRANSFER", {"member"}, family_router, TransferDecisionRequest)
_transfer_action("/service-transfers/{transfer_id}/confirm-scope", "CONFIRM_TRANSFER_SCOPE", {"member"}, family_router, TransferScopeConfirmRequest)
_transfer_action("/service-transfers/{transfer_id}/start-review", "START_REVIEW_TRANSFER", {"org_admin"}, institution_router, TransferDecisionRequest)
_transfer_action("/service-transfers/{transfer_id}/accept", "ACCEPT_TRANSFER", {"org_admin"}, institution_router, TransferDecisionRequest)
_transfer_action("/service-transfers/{transfer_id}/reject", "REJECT_TRANSFER", {"org_admin"}, institution_router, TransferDecisionRequest)
_transfer_action("/service-transfers/{transfer_id}/source-close", "SOURCE_CLOSE_TRANSFER", {"org_admin"}, institution_router, TransferSourceCloseRequest)
_transfer_action("/service-transfers/{transfer_id}/coordinate-close", "COORDINATE_TRANSFER_CLOSE", {"super_admin", "sys_admin"}, platform_router, TransferDecisionRequest)


@institution_router.get("/service-transfers/{transfer_id}/continuation-handoff", response_model=ContinuationHandoffDTO)
async def get_continuation_handoff(transfer_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_oversight_reader_session)):
    _require(actor, {"org_admin"})
    return _dto(ContinuationHandoffDTO, await _read_one(session, "HANDOFF", transfer_id, actor))


@institution_router.post("/service-transfers/{transfer_id}/continuation-case", response_model=ContinuationHandoffDTO)
async def link_continuation_case(transfer_id: UuidV7, payload: ContinuationCaseLinkRequest, key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_transfer_writer_session)):
    _require(actor, {"org_admin"})
    row = await _transition(session, operation="LINK_CONTINUATION_CASE", target_id=transfer_id, actor=actor, key=key, request=payload.model_dump(), kind="transfer")
    return _dto(ContinuationHandoffDTO, row)


@platform_router.post("/proxy-major-authorizations", response_model=ProxyMajorAuthorizationDTO, status_code=201)
async def authorize_proxy_major(payload: ProxyMajorAuthorizationCreateRequest, key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_transfer_writer_session)):
    _require(actor, {"super_admin", "sys_admin"})
    row = await _transition(session, operation="AUTHORIZE_PROXY_MAJOR", target_id=payload.proxy_grant_id, actor=actor, key=key, request=payload.model_dump(), kind="major_authorization")
    return _dto(ProxyMajorAuthorizationDTO, row)


@platform_router.post("/proxy-major-authorizations/{authorization_id}/revoke", response_model=ProxyMajorAuthorizationDTO)
async def revoke_proxy_major(authorization_id: UuidV7, payload: ProxyMajorAuthorizationRevokeRequest, key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_transfer_writer_session)):
    _require(actor, {"super_admin", "sys_admin"})
    row = await _transition(session, operation="REVOKE_PROXY_MAJOR", target_id=authorization_id, actor=actor, key=key, request=payload.model_dump(), kind="major_authorization")
    return _dto(ProxyMajorAuthorizationDTO, row)


@institution_router.get("/service-transfers", response_model=TransferPageDTO)
@platform_router.get("/service-transfers", response_model=TransferPageDTO)
async def list_transfers(cursor: str | None = Query(None, max_length=1024), limit: int = Query(50, ge=1, le=100), status: TransferStatusValue | None = Query(None), actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_oversight_reader_session)):
    _require(actor, {"org_admin", "org_operator", "super_admin", "sys_admin"})
    rows, next_cursor = await _read_many(session, "TRANSFER", None, actor, cursor, limit, status=status)
    return TransferPageDTO(items=tuple(_dto(TransferDTO, row) for row in rows), next_cursor=next_cursor)


@institution_router.get("/service-transfers/{transfer_id}", response_model=TransferDTO)
@platform_router.get("/service-transfers/{transfer_id}", response_model=TransferDTO)
async def get_transfer(transfer_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_oversight_reader_session)):
    _require(actor, {"org_admin", "org_operator", "super_admin", "sys_admin"})
    return _dto(TransferDTO, await _read_one(session, "TRANSFER", transfer_id, actor))


@family_router.post("/data-exports", response_model=DataExportDTO, status_code=202)
async def create_export(payload: DataExportCreateRequest, key: IdempotencyKey, step_up: StepUpToken, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_transfer_writer_session)):
    _require(actor, {"member"})
    _require_current_step_up(step_up, actor)
    return _dto(DataExportDTO, await _transition(session, operation="CREATE_EXPORT", target_id=None, actor=actor, key=key, request=payload.model_dump(), kind="export"))


@family_router.get("/data-exports/{export_id}", response_model=DataExportDTO)
async def family_export(export_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_family_reader_session)):
    _require(actor, {"member"})
    return _dto(DataExportDTO, await _read_one(session, "EXPORT", export_id, actor))


@family_router.post("/data-exports/{export_id}/download-access", response_model=OneTimeDownloadDTO, responses={200: {"headers": {"Cache-Control": {"schema": {"type": "string", "const": "no-store"}}}}})
async def export_download_access(export_id: UuidV7, payload: DownloadAccessRequest, key: IdempotencyKey, step_up: StepUpToken, request: Request, response: Response, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_transfer_writer_session), file_session=Depends(get_institution_onboarding_reader_session)):
    _require(actor, {"member"})
    _require_current_step_up(step_up, actor)
    repository = ServiceFulfillmentRepository(session)
    row = await _service(repository).export_transition(
        operation="EXPORT_DOWNLOAD_ACCESS",
        export_or_member_id=export_id,
        actor_user_id=actor.id,
        actor_role=actor.role,
        actor_tenant_id=actor.tenant_id,
        idempotency_key=key,
        request={**payload.model_dump(), "step_up_verified": True},
    )
    expires_at = row["expires_at"]
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    authorizer = cast(
        ExportArchiveAccessPort | None,
        getattr(request.app.state, "slice7_export_access_authorizer", None),
    )
    if authorizer is None:
        raise ServiceFulfillmentError("DEPENDENCY_UNAVAILABLE")
    token = await authorizer(
        file_session,
        user_id=actor.id,
        file_id=str(row["private_file_id"]),
        reason_code=payload.reason,
        expires_at=int(expires_at.timestamp()),
        token_id=str(row["access_id"]),
    )
    await _commit_mutation(session, repository, "transfer_writer")
    response.headers["Cache-Control"] = "no-store"
    return OneTimeDownloadDTO(
        access_token=token,
        expires_at=expires_at,
        filename=str(row["filename"]),
        content_type="application/zip",
    )


@family_router.post("/data-exports/{export_id}/cancel", response_model=DataExportDTO)
async def cancel_export(export_id: UuidV7, payload: DataExportCancelRequest, key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_transfer_writer_session)):
    _require(actor, {"member"})
    return _dto(DataExportDTO, await _transition(session, operation="CANCEL_EXPORT", target_id=export_id, actor=actor, key=key, request=payload.model_dump(), kind="export"))


@platform_router.get("/service-fulfillment/cases", response_model=ServiceFulfillmentPageDTO)
async def platform_cases(cursor: str | None = Query(None, max_length=1024), limit: int = Query(50, ge=1, le=100), status: CaseStatusValue | None = Query(None), risk: Literal["AT_RISK"] | None = Query(None), actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_oversight_reader_session)):
    _require(actor, {"super_admin", "sys_admin"})
    rows, next_cursor = await _read_many(session, "FULFILLMENT", None, actor, cursor, limit, status=status, risk=risk)
    return ServiceFulfillmentPageDTO(items=tuple(_dto(ServiceFulfillmentDTO, row) for row in rows), next_cursor=next_cursor)


@platform_router.get("/service-fulfillment/cases/{case_id}", response_model=ServiceFulfillmentDTO)
async def platform_case(case_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_oversight_reader_session)):
    _require(actor, {"super_admin", "sys_admin"})
    return _dto(ServiceFulfillmentDTO, await _read_one(session, "FULFILLMENT", case_id, actor))


@platform_router.get("/data-exports", response_model=DataExportPageDTO)
async def platform_exports(cursor: str | None = Query(None, max_length=1024), limit: int = Query(50, ge=1, le=100), status: ExportStatusValue | None = Query(None), actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_oversight_reader_session)):
    _require(actor, {"super_admin", "sys_admin"})
    rows, next_cursor = await _read_many(session, "EXPORT", None, actor, cursor, limit, status=status)
    return DataExportPageDTO(items=tuple(_dto(DataExportDTO, row) for row in rows), next_cursor=next_cursor)


@platform_router.get("/data-exports/{export_id}", response_model=DataExportDTO)
async def platform_export(export_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_oversight_reader_session)):
    _require(actor, {"super_admin", "sys_admin"})
    return _dto(DataExportDTO, await _read_one(session, "EXPORT", export_id, actor))
