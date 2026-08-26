from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Mapping, TypeVar, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel

from app.core.database import (
    get_institution_onboarding_reader_session,
    get_slice7_case_writer_session,
    get_slice7_family_reader_session,
    get_slice7_milestone_writer_session,
    get_slice7_oversight_reader_session,
    get_slice7_transfer_writer_session,
)
from app.core.security import (
    CurrentUser,
    decode_access_token_for_step_up,
    get_current_user_from_jwt,
)
from app.core.uuid_generator import Uuid7Generator
from .domain import SystemBusinessClock
from .ports import ExportArchiveAccessPort
from .repository import ServiceFulfillmentRepository
from .schemas import (
    CaseTransitionRequest,
    ClosingAssessmentCreateRequest,
    ClosingAssessmentDTO,
    ContinuationCaseLinkRequest,
    ContinuationHandoffDTO,
    DataExportCancelRequest,
    DataExportCreateRequest,
    DataExportDTO,
    DownloadAccessRequest,
    MilestoneCompleteRequest,
    MilestoneDTO,
    MilestonePageDTO,
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
    UnableToContactRequest,
    UuidV7,
)
from .service import ServiceFulfillmentError, ServiceFulfillmentService


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
    "COMMIT_OUTCOME_UNKNOWN": 503,
}


async def consume_personal_data_export_download(session, values: Mapping[str, object]) -> bool:
    consumed = await ServiceFulfillmentRepository(session).consume_export_download(
        values
    )
    if consumed:
        await session.commit()
    else:
        await session.rollback()
    return consumed


def _safe_code(exc: BaseException) -> str:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for line in str(current).splitlines():
            token = line.strip().split()[0].strip(":") if line.strip() else ""
            if token in _STATUS:
                return token
        current = current.__cause__ or current.__context__
    return "DEPENDENCY_UNAVAILABLE"


class Slice7Route(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await original(request)
            except RequestValidationError:
                return JSONResponse(status_code=422, content={"code": "INVALID_REQUEST", "message": "request rejected"})
            except HTTPException as exc:
                detail = exc.detail
                code = detail.get("code") if isinstance(detail, dict) else None
                if exc.status_code == 401:
                    code = "UNAUTHENTICATED"
                if code not in _STATUS:
                    code = "INVALID_REQUEST" if exc.status_code < 500 else "DEPENDENCY_UNAVAILABLE"
                return JSONResponse(status_code=_STATUS[code], content={"code": code, "message": "request rejected"})
            except Exception as exc:
                code = _safe_code(exc)
                return JSONResponse(status_code=_STATUS[code], content={"code": code, "message": "request rejected"})

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
    slice7_tags = {
        "service-fulfillment-therapist",
        "service-fulfillment-institution",
        "service-fulfillment-family",
        "service-fulfillment-platform",
    }
    for path_item in schema.get("paths", {}).values():
        if not isinstance(path_item, dict):
            continue
        for operation in path_item.values():
            if (
                isinstance(operation, dict)
                and "responses" in operation
                and slice7_tags.intersection(operation.get("tags", ()))
            ):
                operation["x-symbolic-error-codes"] = tuple(_STATUS)
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


async def _read_one(session, resource: str, target_id: UUID, actor: CurrentUser) -> dict:
    row = await ServiceFulfillmentRepository(session).read_one(resource, target_id, actor.id, actor.role)
    if row is None:
        raise ServiceFulfillmentError(f"{resource}_NOT_FOUND")
    return row


async def _read_many(session, resource: str, scope: UUID | None, actor: CurrentUser, cursor: UUID | None, limit: int) -> list[dict]:
    return await ServiceFulfillmentRepository(session).read_many(resource, scope, actor.id, actor.role, cursor, limit)


async def _transition(session, *, operation: str, target_id: UUID, actor: CurrentUser, key: str, request: Mapping[str, object], kind: str) -> dict:
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
    await session.commit()
    return row


@therapist_router.get("/service-cases/{case_id}/fulfillment", response_model=ServiceFulfillmentDTO)
async def therapist_fulfillment(case_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_milestone_writer_session)):
    _require(actor, {"therapist"})
    return _dto(ServiceFulfillmentDTO, await _read_one(session, "FULFILLMENT", case_id, actor))


@therapist_router.get("/service-cases/{case_id}/milestones", response_model=MilestonePageDTO)
async def therapist_milestones(case_id: UuidV7, cursor_id: UuidV7 | None = None, limit: int = Query(50, ge=1, le=100), actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_milestone_writer_session)):
    _require(actor, {"therapist"})
    rows = await _read_many(session, "MILESTONE", case_id, actor, cursor_id, limit)
    return MilestonePageDTO(items=tuple(_dto(MilestoneDTO, row) for row in rows))


@therapist_router.get("/milestones/{milestone_id}", response_model=MilestoneDTO)
async def therapist_milestone(milestone_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_milestone_writer_session)):
    _require(actor, {"therapist"})
    return _dto(MilestoneDTO, await _read_one(session, "MILESTONE", milestone_id, actor))


@therapist_router.post("/milestones/{milestone_id}/complete", response_model=MilestoneDTO)
async def complete_milestone(milestone_id: UuidV7, payload: MilestoneCompleteRequest, key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_milestone_writer_session)):
    _require(actor, {"therapist"})
    row = await _service(ServiceFulfillmentRepository(session)).complete_milestone(milestone_id=milestone_id, actor_user_id=actor.id, actor_role=actor.role, actor_tenant_id=actor.tenant_id or 0, idempotency_key=key, expected_version=payload.expected_version, record_summary=payload.record_summary, evidence_refs=payload.evidence_refs)
    await session.commit()
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
async def institution_cases(cursor_id: UuidV7 | None = None, limit: int = Query(50, ge=1, le=100), actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_case_writer_session)):
    _require(actor, {"org_admin", "org_operator"})
    rows = await _read_many(session, "FULFILLMENT", None, actor, cursor_id, limit)
    return ServiceFulfillmentPageDTO(items=tuple(_dto(ServiceFulfillmentDTO, row) for row in rows))


@family_router.get("/service-cases/{case_id}/fulfillment", response_model=ServiceFulfillmentDTO)
async def family_fulfillment(case_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_family_reader_session)):
    _require(actor, {"member"})
    return _dto(ServiceFulfillmentDTO, await _read_one(session, "FULFILLMENT", case_id, actor))


@family_router.get("/service-cases/{case_id}/milestones", response_model=MilestonePageDTO)
async def family_milestones(case_id: UuidV7, cursor_id: UuidV7 | None = None, limit: int = Query(50, ge=1, le=100), actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_family_reader_session)):
    _require(actor, {"member"})
    rows = await _read_many(session, "MILESTONE", case_id, actor, cursor_id, limit)
    return MilestonePageDTO(items=tuple(_dto(MilestoneDTO, row) for row in rows))


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
async def list_transfers(cursor_id: UuidV7 | None = None, limit: int = Query(50, ge=1, le=100), actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_oversight_reader_session)):
    _require(actor, {"org_admin", "org_operator", "super_admin", "sys_admin"})
    rows = await _read_many(session, "TRANSFER", None, actor, cursor_id, limit)
    return TransferPageDTO(items=tuple(_dto(TransferDTO, row) for row in rows))


@institution_router.get("/service-transfers/{transfer_id}", response_model=TransferDTO)
@platform_router.get("/service-transfers/{transfer_id}", response_model=TransferDTO)
async def get_transfer(transfer_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_oversight_reader_session)):
    _require(actor, {"org_admin", "org_operator", "super_admin", "sys_admin"})
    return _dto(TransferDTO, await _read_one(session, "TRANSFER", transfer_id, actor))


@family_router.post("/data-exports", response_model=DataExportDTO, status_code=202)
async def create_export(payload: DataExportCreateRequest, key: IdempotencyKey, step_up: StepUpToken, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_transfer_writer_session)):
    _require(actor, {"member"})
    _require_current_step_up(step_up, actor)
    member_id = Uuid7Generator().generate()
    return _dto(DataExportDTO, await _transition(session, operation="CREATE_EXPORT", target_id=member_id, actor=actor, key=key, request=payload.model_dump(), kind="export"))


@family_router.get("/data-exports/{export_id}", response_model=DataExportDTO)
async def family_export(export_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_family_reader_session)):
    _require(actor, {"member"})
    return _dto(DataExportDTO, await _read_one(session, "EXPORT", export_id, actor))


@family_router.post("/data-exports/{export_id}/download-access", response_model=OneTimeDownloadDTO, responses={200: {"headers": {"Cache-Control": {"schema": {"type": "string", "const": "no-store"}}}}})
async def export_download_access(export_id: UuidV7, payload: DownloadAccessRequest, key: IdempotencyKey, step_up: StepUpToken, request: Request, response: Response, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_transfer_writer_session), file_session=Depends(get_institution_onboarding_reader_session)):
    _require(actor, {"member"})
    _require_current_step_up(step_up, actor)
    row = await _service(ServiceFulfillmentRepository(session)).export_transition(
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
    await session.commit()
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
async def platform_cases(cursor_id: UuidV7 | None = None, limit: int = Query(50, ge=1, le=100), actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_oversight_reader_session)):
    _require(actor, {"super_admin", "sys_admin"})
    rows = await _read_many(session, "FULFILLMENT", None, actor, cursor_id, limit)
    return ServiceFulfillmentPageDTO(items=tuple(_dto(ServiceFulfillmentDTO, row) for row in rows))


@platform_router.get("/service-fulfillment/cases/{case_id}", response_model=ServiceFulfillmentDTO)
async def platform_case(case_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_oversight_reader_session)):
    _require(actor, {"super_admin", "sys_admin"})
    return _dto(ServiceFulfillmentDTO, await _read_one(session, "FULFILLMENT", case_id, actor))


@platform_router.get("/data-exports", response_model=tuple[DataExportDTO, ...])
async def platform_exports(cursor_id: UuidV7 | None = None, limit: int = Query(50, ge=1, le=100), actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_oversight_reader_session)):
    _require(actor, {"super_admin", "sys_admin"})
    rows = await _read_many(session, "EXPORT", None, actor, cursor_id, limit)
    return tuple(_dto(DataExportDTO, row) for row in rows)


@platform_router.get("/data-exports/{export_id}", response_model=DataExportDTO)
async def platform_export(export_id: UuidV7, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_slice7_oversight_reader_session)):
    _require(actor, {"super_admin", "sys_admin"})
    return _dto(DataExportDTO, await _read_one(session, "EXPORT", export_id, actor))
