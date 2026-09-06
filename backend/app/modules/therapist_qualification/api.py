from __future__ import annotations

import asyncio
import base64
from datetime import datetime
import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.core.database import (
    get_db_session,
    get_therapist_onboarding_writer_session,
    get_therapist_reader_session,
    get_therapist_review_writer_session,
)
from app.core.responses import ok_response
from app.core.security import CurrentUser, get_current_user_from_jwt
from app.core.uuid_generator import Uuid7Generator
from app.modules.therapist_qualification.repository import TherapistQualificationRepository
from app.modules.therapist_qualification.schemas import (
    CorrectionDTO,
    ErrorEnvelope,
    EvidenceDTO,
    InvitationCreatedDTO,
    InvitationDTO,
    InvitationStatusValue,
    MutationProfileDTO,
    PageDTO,
    ProfileDTO,
    ProfileStatusValue,
    ReadinessDTO,
    ReviewDecisionDTO,
    ReviewDetailDTO,
    ReviewItemDTO,
    ReviewStatusValue,
    SelfTherapistDetailDTO,
    SuccessEnvelope,
    TherapistDetailDTO,
    TherapistActivate,
    TherapistInvitationCreate,
    TherapistInvitationRevoke,
    TherapistProfileDraft,
    TherapistRenew,
    TherapistRenewalResubmit,
    TherapistResubmit,
    TherapistResumeRequest,
    TherapistReviewDecisionRequest,
    TherapistStatusRequest,
    TherapistSubmit,
)
from app.modules.therapist_qualification.service import (
    TherapistSecrets,
    activate,
    change_status,
    create_invitation,
    require_institution_actor,
    require_reviewer,
    require_therapist,
    read_readiness_fail_closed,
    renewal_resubmit,
    resubmit,
    renew,
    review_decision,
    revoke_invitation,
    save_profile,
    submit,
    translate_error,
)
from app.tasks.celery_app import celery_app


async def _request_readiness_refresh(tenant_id: int) -> None:
    try:
        await asyncio.to_thread(
            celery_app.send_task,
            "phase1.therapist.recompute_readiness",
            args=(tenant_id, str(Uuid7Generator().generate())),
            queue="therapist-workflow",
        )
    except Exception:
        return


IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]


_SYMBOLIC_ERROR_STATUS = {
    "INVALID_REQUEST": 400,
    "INVALID_CURSOR": 400,
    "THERAPIST_FIELD_FORBIDDEN": 400,
    "AUTHENTICATION_REQUIRED": 401,
    "THERAPIST_INVITATION_INVALID": 401,
    "THERAPIST_INVITATION_EXPIRED": 401,
    "THERAPIST_ACTIVATION_ATTEMPTS_EXHAUSTED": 401,
    "ACTOR_CURRENTNESS_FORBIDDEN": 403,
    "REVIEWER_CURRENTNESS_FORBIDDEN": 403,
    "THERAPIST_SCOPE_FORBIDDEN": 403,
    "THERAPIST_INVITATION_NOT_FOUND": 404,
    "THERAPIST_NOT_FOUND": 404,
    "THERAPIST_CORRECTION_NOT_FOUND": 404,
    "THERAPIST_REVIEW_ITEM_NOT_FOUND": 404,
    "READINESS_NOT_FOUND": 404,
    "DEPENDENCY_UNAVAILABLE": 503,
    "COMMIT_OUTCOME_UNKNOWN": 503,
}


def _fixed_route_errors(*codes: str, mutation: bool = False):
    complete = ["AUTHENTICATION_REQUIRED", *codes, "DEPENDENCY_UNAVAILABLE"]
    if mutation:
        complete.append("COMMIT_OUTCOME_UNKNOWN")
    grouped: dict[int, list[str]] = {}
    for code in complete:
        status = _SYMBOLIC_ERROR_STATUS.get(code, 409)
        grouped.setdefault(status, []).append(code)
    return {status: tuple(values) for status, values in grouped.items()}


SLICE2_ROUTE_ERROR_CODES = {
    ("POST", "/api/v1/institution/therapist-invitations"): _fixed_route_errors("INVALID_REQUEST", "ACTOR_CURRENTNESS_FORBIDDEN", "TENANT_NOT_ACTIVE", "THERAPIST_INVITATION_CONFLICT", "IDEMPOTENCY_CONFLICT", mutation=True),
    ("GET", "/api/v1/institution/therapist-invitations"): _fixed_route_errors("INVALID_CURSOR", "ACTOR_CURRENTNESS_FORBIDDEN"),
    ("POST", "/api/v1/institution/therapist-invitations/{invitation_id}/revoke"): _fixed_route_errors("INVALID_REQUEST", "ACTOR_CURRENTNESS_FORBIDDEN", "THERAPIST_INVITATION_NOT_FOUND", "THERAPIST_INVITATION_STATE_CONFLICT", "THERAPIST_VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT", mutation=True),
    ("GET", "/api/v1/institution/therapists"): _fixed_route_errors("INVALID_CURSOR", "ACTOR_CURRENTNESS_FORBIDDEN"),
    ("GET", "/api/v1/institution/therapists/{therapist_id}"): _fixed_route_errors("ACTOR_CURRENTNESS_FORBIDDEN", "THERAPIST_NOT_FOUND"),
    ("GET", "/api/v1/institution/service-readiness"): _fixed_route_errors("ACTOR_CURRENTNESS_FORBIDDEN", "READINESS_NOT_FOUND"),
    ("GET", "/api/v1/institution/service-readiness/evidence"): _fixed_route_errors("INVALID_CURSOR", "ACTOR_CURRENTNESS_FORBIDDEN"),
    ("POST", "/api/v1/therapist-onboarding/activate"): _fixed_route_errors("INVALID_REQUEST", "THERAPIST_INVITATION_INVALID", "THERAPIST_INVITATION_EXPIRED", "THERAPIST_ACTIVATION_ATTEMPTS_EXHAUSTED", "IDEMPOTENCY_CONFLICT", mutation=True),
    ("GET", "/api/v1/therapist-onboarding/profile"): _fixed_route_errors("ACTOR_CURRENTNESS_FORBIDDEN", "THERAPIST_NOT_FOUND"),
    ("PUT", "/api/v1/therapist-onboarding/profile"): _fixed_route_errors("INVALID_REQUEST", "ACTOR_CURRENTNESS_FORBIDDEN", "THERAPIST_FIELD_FORBIDDEN", "THERAPIST_VERSION_CONFLICT", "THERAPIST_STATE_CONFLICT", "IDEMPOTENCY_CONFLICT", mutation=True),
    ("POST", "/api/v1/therapist-onboarding/qualifications/submit"): _fixed_route_errors("INVALID_REQUEST", "ACTOR_CURRENTNESS_FORBIDDEN", "THERAPIST_SUBMISSION_INCOMPLETE", "PRIVATE_FILE_NOT_CLEAN", "PRIVATE_FILE_BIND_CONFLICT", "THERAPIST_VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT", mutation=True),
    ("GET", "/api/v1/therapist-onboarding/corrections"): _fixed_route_errors("ACTOR_CURRENTNESS_FORBIDDEN", "THERAPIST_CORRECTION_NOT_FOUND"),
    ("POST", "/api/v1/therapist-onboarding/resubmit"): _fixed_route_errors("INVALID_REQUEST", "ACTOR_CURRENTNESS_FORBIDDEN", "THERAPIST_CORRECTION_SCOPE_CONFLICT", "PRIVATE_FILE_NOT_CLEAN", "PRIVATE_FILE_BIND_CONFLICT", "THERAPIST_VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT", mutation=True),
    ("POST", "/api/v1/therapist-onboarding/qualifications/renew"): _fixed_route_errors("INVALID_REQUEST", "ACTOR_CURRENTNESS_FORBIDDEN", "THERAPIST_RENEWAL_CONFLICT", "PRIVATE_FILE_NOT_CLEAN", "PRIVATE_FILE_BIND_CONFLICT", "THERAPIST_VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT", mutation=True),
    ("POST", "/api/v1/therapist-onboarding/qualification-renewals/{review_item_id}/resubmit"): _fixed_route_errors("INVALID_REQUEST", "ACTOR_CURRENTNESS_FORBIDDEN", "THERAPIST_REVIEW_ITEM_NOT_FOUND", "THERAPIST_RENEWAL_CORRECTION_SCOPE_CONFLICT", "THERAPIST_RENEWAL_REVIEW_CONFLICT", "IDEMPOTENCY_CONFLICT", mutation=True),
    ("POST", "/api/v1/therapist-onboarding/exit"): _fixed_route_errors("INVALID_REQUEST", "ACTOR_CURRENTNESS_FORBIDDEN", "THERAPIST_ACTIVE_CASES_REMAIN", "THERAPIST_VERSION_CONFLICT", "THERAPIST_STATE_CONFLICT", "IDEMPOTENCY_CONFLICT", mutation=True),
    ("GET", "/api/v1/platform/therapist-reviews"): _fixed_route_errors("INVALID_CURSOR", "REVIEWER_CURRENTNESS_FORBIDDEN"),
    ("GET", "/api/v1/platform/therapist-reviews/{therapist_id}"): _fixed_route_errors("REVIEWER_CURRENTNESS_FORBIDDEN", "THERAPIST_NOT_FOUND", "THERAPIST_REVIEW_ITEM_NOT_FOUND"),
    ("POST", "/api/v1/platform/therapist-reviews/{therapist_id}/decision"): _fixed_route_errors("INVALID_REQUEST", "REVIEWER_CURRENTNESS_FORBIDDEN", "THERAPIST_NOT_FOUND", "THERAPIST_REVIEW_STATE_CONFLICT", "THERAPIST_CORRECTION_FIELD_FORBIDDEN", "THERAPIST_APPROVAL_PRECONDITION_FAILED", "THERAPIST_VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT", mutation=True),
    ("GET", "/api/v1/platform/therapist-renewal-reviews"): _fixed_route_errors("INVALID_CURSOR", "REVIEWER_CURRENTNESS_FORBIDDEN"),
    ("POST", "/api/v1/platform/therapist-renewal-reviews/{review_item_id}/decision"): _fixed_route_errors("INVALID_REQUEST", "REVIEWER_CURRENTNESS_FORBIDDEN", "THERAPIST_REVIEW_ITEM_NOT_FOUND", "THERAPIST_RENEWAL_REVIEW_CONFLICT", "THERAPIST_RESUME_PRECONDITION_FAILED", "IDEMPOTENCY_CONFLICT", mutation=True),
    ("POST", "/api/v1/platform/therapists/{therapist_id}/suspend"): _fixed_route_errors("INVALID_REQUEST", "REVIEWER_CURRENTNESS_FORBIDDEN", "THERAPIST_NOT_FOUND", "THERAPIST_SUSPEND_STATE_CONFLICT", "THERAPIST_VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT", mutation=True),
    ("POST", "/api/v1/platform/therapists/{therapist_id}/resume"): _fixed_route_errors("INVALID_REQUEST", "REVIEWER_CURRENTNESS_FORBIDDEN", "THERAPIST_NOT_FOUND", "THERAPIST_RESUME_PRECONDITION_FAILED", "THERAPIST_VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT", mutation=True),
    ("POST", "/api/v1/platform/therapists/{therapist_id}/exit"): _fixed_route_errors("INVALID_REQUEST", "REVIEWER_CURRENTNESS_FORBIDDEN", "THERAPIST_NOT_FOUND", "THERAPIST_ACTIVE_CASES_REMAIN", "THERAPIST_VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT", mutation=True),
    ("GET", "/api/v1/platform/tenants/{tenant_id}/service-readiness"): _fixed_route_errors("REVIEWER_CURRENTNESS_FORBIDDEN", "READINESS_NOT_FOUND"),
    ("GET", "/api/v1/platform/tenants/{tenant_id}/service-readiness/evidence"): _fixed_route_errors("INVALID_CURSOR", "REVIEWER_CURRENTNESS_FORBIDDEN", "READINESS_NOT_FOUND"),
}


def _error_response(status_code: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": "request rejected"},
        headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"} if status_code == 401 else {"Cache-Control": "no-store"} if status_code == 503 else None,
    )


def _http_error_code(request: Request, exc: HTTPException, allowed: dict[int, tuple[str, ...]]) -> tuple[int, str]:
    def catalogued(status_code: int, code: str) -> tuple[int, str]:
        if code in allowed.get(status_code, ()):
            return status_code, code
        if "INVALID_REQUEST" in allowed.get(400, ()):
            return 400, "INVALID_REQUEST"
        return 503, "DEPENDENCY_UNAVAILABLE"

    status_code = 400 if exc.status_code == 422 else exc.status_code
    detail = exc.detail
    if status_code == 401:
        return 401, "AUTHENTICATION_REQUIRED"
    if status_code == 503 and detail not in allowed.get(503, ()):
        return 503, "DEPENDENCY_UNAVAILABLE"
    if (
        isinstance(detail, str)
        and detail
        and detail == detail.upper()
        and all(character.isascii() and (character.isalnum() or character == "_") for character in detail)
    ):
        return catalogued(status_code, detail)
    if status_code == 401:
        return catalogued(status_code, "AUTHENTICATION_REQUIRED")
    if status_code == 403:
        code = (
            "REVIEWER_CURRENTNESS_FORBIDDEN"
            if request.url.path.startswith("/api/v1/platform/")
            else "ACTOR_CURRENTNESS_FORBIDDEN"
        )
        return catalogued(status_code, code)
    if status_code == 404:
        return catalogued(status_code, "THERAPIST_NOT_FOUND")
    if status_code == 409:
        return catalogued(status_code, "THERAPIST_STATE_CONFLICT")
    if status_code == 503:
        return catalogued(status_code, "DEPENDENCY_UNAVAILABLE")
    return catalogued(400, "INVALID_REQUEST")


class TherapistQualificationRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await original(request)
            except RequestValidationError:
                return _error_response(400, "INVALID_REQUEST")
            except HTTPException as exc:
                key = (next(iter(self.methods)), self.path)
                status_code, code = _http_error_code(
                    request, exc, SLICE2_ROUTE_ERROR_CODES[key]
                )
                return _error_response(status_code, code)
            except Exception:
                return _error_response(503, "DEPENDENCY_UNAVAILABLE")

        return handler


def strip_therapist_validation_responses(schema: dict[str, object]) -> dict[str, object]:
    paths = schema.get("paths")
    if not isinstance(paths, dict):
        return schema
    prefixes = (
        "/api/v1/institution/therapist",
        "/api/v1/institution/service-readiness",
        "/api/v1/therapist-onboarding",
        "/api/v1/platform/therapist",
    )
    for path, methods in paths.items():
        is_slice2_path = isinstance(path, str) and (
            path.startswith(prefixes)
            or (
                path.startswith("/api/v1/platform/tenants/")
                and "/service-readiness" in path
            )
        )
        if not is_slice2_path or not isinstance(methods, dict):
            continue
        for operation in methods.values():
            if isinstance(operation, dict):
                responses = operation.get("responses")
                if isinstance(responses, dict):
                    responses.pop("422", None)
        for method, operation in methods.items():
            key = (method.upper(), path)
            if key in SLICE2_ROUTE_ERROR_CODES and isinstance(operation, dict):
                responses = operation.get("responses")
                if isinstance(responses, dict):
                    allowed_statuses = {
                        "200",
                        "201",
                        *(str(status) for status in SLICE2_ROUTE_ERROR_CODES[key]),
                    }
                    for status in tuple(responses):
                        if status not in allowed_statuses:
                            responses.pop(status)
                for status, auth_code in (("401", "AUTHENTICATION_REQUIRED"), ("503", "DEPENDENCY_UNAVAILABLE")):
                    response = operation.setdefault("responses", {}).setdefault(status, {"description": "Request rejected"})
                    media = response.setdefault("content", {}).setdefault("application/json", {
                        "schema": {"type": "object", "required": ["code", "message"], "properties": {
                            "code": {"type": "string"}, "message": {"type": "string"},
                        }},
                    })
                    media.setdefault("examples", {})["authentication"] = {
                        "value": {"code": auth_code, "message": "request rejected"},
                    }
                    headers = response.setdefault("headers", {})
                    headers["Cache-Control"] = {"schema": {"type": "string", "enum": ["no-store"]}}
                    if status == "401":
                        headers["WWW-Authenticate"] = {"schema": {"type": "string", "enum": ["Bearer"]}}
                operation["x-symbolic-error-codes"] = {
                    str(status): list(codes)
                    for status, codes in SLICE2_ROUTE_ERROR_CODES[key].items()
                }
    return schema


institution_router = APIRouter(
    prefix="/api/v1/institution",
    tags=["therapist_qualification"],
    route_class=TherapistQualificationRoute,
)
therapist_router = APIRouter(
    prefix="/api/v1/therapist-onboarding",
    tags=["therapist_qualification"],
    route_class=TherapistQualificationRoute,
)
platform_router = APIRouter(
    prefix="/api/v1/platform",
    tags=["therapist_qualification"],
    route_class=TherapistQualificationRoute,
)


def _errors(*statuses: int) -> dict[int, dict[str, object]]:
    return {status: {"model": ErrorEnvelope} for status in sorted({400, *statuses})}


def _ok(schema, value):
    return ok_response(schema.model_validate(value).model_dump(mode="json"))


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _encode_cursor(value: dict[str, object]) -> str:
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: str | None, kind: str):
    if value is None:
        return None
    try:
        if not isinstance(value, str) or not value or len(value) > 512:
            raise ValueError
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_json_pairs)
        if type(payload) is not dict:
            raise ValueError
        if kind == "invitation":
            if set(payload) != {"issued_at", "invitation_id"} or type(payload["issued_at"]) is not str or type(payload["invitation_id"]) is not str:
                raise ValueError
            issued_at = datetime.fromisoformat(payload["issued_at"])
            invitation_id = UUID(payload["invitation_id"])
            if issued_at.tzinfo is None or invitation_id.version != 7:
                raise ValueError
            return issued_at, str(invitation_id)
        if kind == "profile":
            if set(payload) != {"therapist_id"} or type(payload["therapist_id"]) is not str:
                raise ValueError
            therapist_id = UUID(payload["therapist_id"])
            if therapist_id.version != 7:
                raise ValueError
            return str(therapist_id)
        if kind == "review":
            if set(payload) != {"created_at", "review_item_id"} or type(payload["created_at"]) is not str or type(payload["review_item_id"]) is not str:
                raise ValueError
            created_at = datetime.fromisoformat(payload["created_at"])
            review_item_id = UUID(payload["review_item_id"])
            if created_at.tzinfo is None or review_item_id.version != 7:
                raise ValueError
            return created_at, str(review_item_id)
        if kind == "evidence":
            if set(payload) != {"evidence_version"} or type(payload["evidence_version"]) is not int or payload["evidence_version"] < 1:
                raise ValueError
            return payload["evidence_version"]
        raise ValueError
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        raise HTTPException(400, "INVALID_CURSOR") from None


def _page(rows, limit: int, *, item_schema, cursor_factory):
    visible = rows[:limit]
    next_cursor = cursor_factory(visible[-1]) if len(rows) > limit and visible else None
    return PageDTO[item_schema].model_validate({
        "items": [item_schema.model_validate(dict(row)) for row in visible],
        "next_cursor": next_cursor,
    })


def _profile_dto(row, tenant_public_id: str) -> dict[str, object]:
    value = dict(row)
    value["tenant_id"] = tenant_public_id
    value["service_tags"] = (
        tuple(value["service_tags"]) if value.get("service_tags") is not None else None
    )
    return value


def _invitation_dto(row) -> dict[str, object]:
    value = dict(row)
    result = {
        "invitation_id": value["invitation_id"],
        "masked_phone": value["phone_masked"],
        "status": value["status"],
        "expires_at": value["expires_at"],
        "issued_at": value["issued_at"],
        "activated_at": value.get("activated_at"),
        "revoked_at": value.get("revoked_at"),
        "version": value["version"],
    }
    return result


def _evidence_dto(row, tenant_public_id: str) -> dict[str, object]:
    value = dict(row)
    value["tenant_id"] = tenant_public_id
    value["reason_codes"] = tuple(value.get("reason_codes") or ())
    return value


def _mutation_dto(value: dict[str, object]) -> dict[str, object]:
    return MutationProfileDTO.model_validate(value).model_dump(mode="json")


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", request.headers.get("x-request-id", "00000000-0000-7000-8000-000000000000"))


async def _safe(awaitable):
    try:
        return await awaitable
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise translate_error(exc) from None


async def _tenant_public_id(authority_session, tenant_id: int) -> str:
    row = (
        await authority_session.execute(
            __import__("sqlalchemy").text(
                "SELECT public.slice2_institution_identity_authority_v1(:tenant_id)"
            ),
            {"tenant_id": tenant_id},
        )
    ).one_or_none()
    if row is None or row[0] is None:
        raise HTTPException(403, "ACTOR_CURRENTNESS_FORBIDDEN")
    return str(row[0])


def _actor_precommit(authority_session, actor: CurrentUser, kind: str, tenant_public_id: str | None = None):
    async def check() -> None:
        if kind == "institution":
            await require_institution_actor(authority_session, actor)
        elif kind == "therapist":
            await require_therapist(authority_session, actor)
        elif kind == "reviewer":
            await require_reviewer(authority_session, actor)
        else:
            raise HTTPException(403, "ACTOR_CURRENTNESS_FORBIDDEN")
        if tenant_public_id is not None:
            current = await _tenant_public_id(authority_session, actor.tenant_id)
            if current != tenant_public_id:
                raise HTTPException(403, "ACTOR_CURRENTNESS_FORBIDDEN")
    return check


def _tenant_precommit(authority_session, tenant_id: int, tenant_public_id: str):
    async def check() -> None:
        current = await _tenant_public_id(authority_session, tenant_id)
        if current != tenant_public_id:
            raise HTTPException(403, "ACTOR_CURRENTNESS_FORBIDDEN")
    return check


async def _review_decision_dto(repo, therapist_id: str, result: dict[str, object]) -> dict[str, object]:
    item = await repo.current_review_item(therapist_id)
    profile = await repo.profile_summary(therapist_id)
    if item is None or profile is None:
        raise HTTPException(404, "THERAPIST_NOT_FOUND")
    revisions = await repo.revisions(therapist_id)
    revision_no = next(
        (row["revision_no"] for row in revisions if row["revision_id"] == item["revision_id"]),
        None,
    )
    return {
        "review_item": dict(item),
        "profile": {
            "therapist_id": therapist_id,
            "status": profile["status"],
            "revision_id": item["revision_id"],
            "revision_no": revision_no,
            "review_item_id": item["review_item_id"],
            "version": profile["version"],
        },
        "decision_id": result.get("decision_id"),
    }


@institution_router.post(
    "/therapist-invitations", status_code=201,
    response_model=SuccessEnvelope[InvitationCreatedDTO],
    responses=_errors(400, 401, 403, 409, 503),
)
async def post_invitation(payload: TherapistInvitationCreate, request: Request, idempotency_key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_therapist_onboarding_writer_session), authority_session=Depends(get_db_session)):
    await _safe(require_institution_actor(authority_session, actor))
    public_id = await _safe(_tenant_public_id(authority_session, actor.tenant_id))
    value = await _safe(create_invitation(session, actor, payload, _request_id(request), idempotency_key, tenant_public_id=public_id, precommit_check=_actor_precommit(authority_session, actor, "institution", public_id)))
    return _ok(InvitationCreatedDTO, value)


@institution_router.get(
    "/therapist-invitations",
    response_model=SuccessEnvelope[PageDTO[InvitationDTO]],
    responses=_errors(400, 401, 403, 503),
)
async def get_invitations(status: InvitationStatusValue | None = None, cursor: str | None = Query(None, max_length=512), limit: int = Query(20, ge=1, le=100), actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_therapist_reader_session), authority_session=Depends(get_db_session)):
    decoded = _decode_cursor(cursor, "invitation")
    await _safe(require_institution_actor(authority_session, actor))
    rows = await _safe(TherapistQualificationRepository(session).list_invitations(actor.tenant_id, status=status, cursor=decoded, limit=limit))
    public_rows = tuple(_invitation_dto(row) for row in rows)
    page = _page(
        public_rows, limit, item_schema=InvitationDTO,
        cursor_factory=lambda row: _encode_cursor({"issued_at": row["issued_at"].isoformat(), "invitation_id": str(row["invitation_id"])}),
    )
    return _ok(PageDTO[InvitationDTO], page)


@institution_router.post(
    "/therapist-invitations/{invitation_id}/revoke",
    response_model=SuccessEnvelope[InvitationDTO],
    responses=_errors(400, 401, 403, 404, 409, 503),
)
async def post_revoke(invitation_id: UUID, payload: TherapistInvitationRevoke, request: Request, idempotency_key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_therapist_onboarding_writer_session), authority_session=Depends(get_db_session)):
    await _safe(require_institution_actor(authority_session, actor))
    value = await _safe(revoke_invitation(session, actor, str(invitation_id), payload, _request_id(request), idempotency_key, precommit_check=_actor_precommit(authority_session, actor, "institution")))
    return _ok(InvitationDTO, value)


@institution_router.get(
    "/therapists",
    response_model=SuccessEnvelope[PageDTO[ProfileDTO]],
    responses=_errors(400, 401, 403, 503),
)
async def get_therapists(status: ProfileStatusValue | None = None, cursor: str | None = Query(None, max_length=512), limit: int = Query(20, ge=1, le=100), actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_therapist_reader_session), authority_session=Depends(get_db_session)):
    decoded = _decode_cursor(cursor, "profile")
    await _safe(require_institution_actor(authority_session, actor))
    repo = TherapistQualificationRepository(session)
    guard = await _safe(repo.readiness_guard(actor.tenant_id))
    if guard is None:
        raise HTTPException(403, "ACTOR_CURRENTNESS_FORBIDDEN")
    rows = await _safe(repo.list_profiles(actor.tenant_id, status=status, cursor=decoded, limit=limit))
    public_rows = tuple(_profile_dto(row, str(guard["tenant_public_id"])) for row in rows)
    page = _page(
        public_rows, limit, item_schema=ProfileDTO,
        cursor_factory=lambda row: _encode_cursor({"therapist_id": str(row["therapist_id"])}),
    )
    return _ok(PageDTO[ProfileDTO], page)


@institution_router.get(
    "/therapists/{therapist_id}", response_model=SuccessEnvelope[TherapistDetailDTO],
    responses=_errors(401, 403, 404, 503),
)
async def get_therapist(therapist_id: UUID, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_therapist_reader_session), authority_session=Depends(get_db_session)):
    await _safe(require_institution_actor(authority_session, actor))
    repo = TherapistQualificationRepository(session)
    profile = await _safe(repo.profile_summary(str(therapist_id)))
    if profile is None or profile["tenant_id"] != actor.tenant_id:
        raise HTTPException(404, "THERAPIST_NOT_FOUND")
    guard = await _safe(repo.readiness_guard(actor.tenant_id))
    if guard is None:
        raise HTTPException(404, "THERAPIST_NOT_FOUND")
    value = {
        "profile": _profile_dto(profile, str(guard["tenant_public_id"])),
        "qualifications": [dict(row) for row in await _safe(repo.qualifications(str(therapist_id)))],
    }
    return _ok(TherapistDetailDTO, value)


@institution_router.get(
    "/service-readiness", response_model=SuccessEnvelope[ReadinessDTO],
    responses=_errors(401, 403, 404, 503),
)
async def get_service_readiness(actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_therapist_reader_session), authority_session=Depends(get_db_session)):
    await _safe(require_institution_actor(authority_session, actor))
    value, stale = await _safe(read_readiness_fail_closed(session, actor.tenant_id))
    if value is None:
        raise HTTPException(404, "READINESS_NOT_FOUND")
    if stale:
        await _request_readiness_refresh(actor.tenant_id)
    return _ok(ReadinessDTO, value)


@institution_router.get(
    "/service-readiness/evidence",
    response_model=SuccessEnvelope[PageDTO[EvidenceDTO]],
    responses=_errors(400, 401, 403, 503),
)
async def get_readiness_evidence(cursor: str | None = Query(None, max_length=512), limit: int = Query(20, ge=1, le=100), actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_therapist_reader_session), authority_session=Depends(get_db_session)):
    decoded = _decode_cursor(cursor, "evidence")
    await _safe(require_institution_actor(authority_session, actor))
    repo = TherapistQualificationRepository(session)
    guard = await _safe(repo.readiness_guard(actor.tenant_id))
    if guard is None:
        raise HTTPException(403, "ACTOR_CURRENTNESS_FORBIDDEN")
    rows = await _safe(repo.readiness_evidence(actor.tenant_id, cursor=decoded, limit=limit))
    public_rows = tuple(_evidence_dto(row, str(guard["tenant_public_id"])) for row in rows)
    page = _page(
        public_rows, limit, item_schema=EvidenceDTO,
        cursor_factory=lambda row: _encode_cursor({"evidence_version": row["evidence_version"]}),
    )
    return _ok(PageDTO[EvidenceDTO], page)


@therapist_router.post(
    "/activate", status_code=201,
    response_model=SuccessEnvelope[MutationProfileDTO],
    responses=_errors(400, 401, 409, 503),
)
async def post_activate(payload: TherapistActivate, request: Request, idempotency_key: IdempotencyKey, session=Depends(get_therapist_onboarding_writer_session), authority_session=Depends(get_db_session)):
    tenant_id = await _safe(
        TherapistQualificationRepository(session).invitation_tenant_id(str(payload.invitation_id))
    )
    if tenant_id is None:
        raise HTTPException(401, "THERAPIST_INVITATION_INVALID")
    public_id = await _safe(_tenant_public_id(authority_session, tenant_id))
    value = await _safe(activate(session, payload, _request_id(request), idempotency_key, tenant_public_id=public_id, precommit_check=_tenant_precommit(authority_session, tenant_id, public_id)))
    return _ok(MutationProfileDTO, value)


@therapist_router.get(
    "/profile", response_model=SuccessEnvelope[SelfTherapistDetailDTO],
    responses=_errors(401, 403, 404, 503),
)
async def get_profile(actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_therapist_reader_session), private_session=Depends(get_therapist_onboarding_writer_session), authority_session=Depends(get_db_session)):
    await _safe(require_therapist(authority_session, actor))
    repo = TherapistQualificationRepository(session)
    row = await _safe(repo.profile_for_user_summary(actor.id))
    if row is None or actor.role != "therapist" or row["tenant_id"] != actor.tenant_id:
        raise HTTPException(403, "ACTOR_CURRENTNESS_FORBIDDEN")
    tenant_public_id = await _safe(_tenant_public_id(authority_session, actor.tenant_id))
    real_name = None
    if row["status"] != "ACTIVATED":
        private_row = await _safe(TherapistQualificationRepository(private_session).self_profile_private(actor.id))
        if (
            private_row is None
            or private_row["therapist_id"] != row["therapist_id"]
            or private_row["real_name_ciphertext"] is None
            or private_row["real_name_encryption_key_id"] is None
        ):
            raise HTTPException(403, "ACTOR_CURRENTNESS_FORBIDDEN")
        real_name = TherapistSecrets().decrypt_pii(
            private_row["real_name_ciphertext"], private_row["real_name_encryption_key_id"],
            field="profile-real-name", tenant_public_id=tenant_public_id,
            object_id=private_row["therapist_id"],
        )
    public_profile = _profile_dto(row, tenant_public_id)
    public_profile["real_name"] = real_name
    value = {
        "profile": public_profile,
        "qualifications": [dict(item) for item in await _safe(repo.qualifications(row["therapist_id"]))],
        "correction": await _safe(repo.correction(row["therapist_id"])),
    }
    return _ok(SelfTherapistDetailDTO, value)


@therapist_router.put(
    "/profile", response_model=SuccessEnvelope[MutationProfileDTO],
    responses=_errors(400, 401, 403, 404, 409, 503),
)
async def put_profile(payload: TherapistProfileDraft, request: Request, idempotency_key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_therapist_onboarding_writer_session), authority_session=Depends(get_db_session)):
    await _safe(require_therapist(authority_session, actor))
    public_id = await _safe(_tenant_public_id(authority_session, actor.tenant_id))
    return _ok(MutationProfileDTO, await _safe(save_profile(session, actor, payload, _request_id(request), idempotency_key, tenant_public_id=public_id, precommit_check=_actor_precommit(authority_session, actor, "therapist", public_id))))


@therapist_router.post(
    "/qualifications/submit", status_code=201,
    response_model=SuccessEnvelope[MutationProfileDTO],
    responses=_errors(400, 401, 403, 409, 503),
)
async def post_submit(payload: TherapistSubmit, request: Request, idempotency_key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_therapist_onboarding_writer_session), authority_session=Depends(get_db_session)):
    await _safe(require_therapist(authority_session, actor))
    public_id = await _safe(_tenant_public_id(authority_session, actor.tenant_id))
    return _ok(MutationProfileDTO, await _safe(submit(session, actor, payload, _request_id(request), idempotency_key, tenant_public_id=public_id, precommit_check=_actor_precommit(authority_session, actor, "therapist", public_id))))


@therapist_router.get(
    "/corrections", response_model=SuccessEnvelope[CorrectionDTO],
    responses=_errors(401, 403, 404, 503),
)
async def get_corrections(actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_therapist_reader_session), authority_session=Depends(get_db_session)):
    await _safe(require_therapist(authority_session, actor))
    repo = TherapistQualificationRepository(session)
    row = await _safe(repo.profile_for_user_summary(actor.id))
    if row is None or row["status"] != "NEEDS_CORRECTION":
        raise HTTPException(404, "THERAPIST_CORRECTION_NOT_FOUND")
    correction = await _safe(repo.correction(row["therapist_id"]))
    if correction is None:
        raise HTTPException(404, "THERAPIST_CORRECTION_NOT_FOUND")
    return _ok(CorrectionDTO, correction)


@therapist_router.post(
    "/resubmit", status_code=201,
    response_model=SuccessEnvelope[MutationProfileDTO],
    responses=_errors(400, 401, 403, 409, 503),
)
async def post_resubmit(payload: TherapistResubmit, request: Request, idempotency_key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_therapist_onboarding_writer_session), authority_session=Depends(get_db_session)):
    await _safe(require_therapist(authority_session, actor))
    public_id = await _safe(_tenant_public_id(authority_session, actor.tenant_id))
    return _ok(MutationProfileDTO, await _safe(resubmit(session, actor, payload, _request_id(request), idempotency_key, tenant_public_id=public_id, precommit_check=_actor_precommit(authority_session, actor, "therapist", public_id))))


@therapist_router.post(
    "/qualifications/renew", status_code=201,
    response_model=SuccessEnvelope[MutationProfileDTO],
    responses=_errors(400, 401, 403, 409, 503),
)
async def post_renew(payload: TherapistRenew, request: Request, idempotency_key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_therapist_onboarding_writer_session), authority_session=Depends(get_db_session)):
    await _safe(require_therapist(authority_session, actor))
    public_id = await _safe(_tenant_public_id(authority_session, actor.tenant_id))
    return _ok(MutationProfileDTO, await _safe(renew(session, actor, payload, _request_id(request), idempotency_key, tenant_public_id=public_id, precommit_check=_actor_precommit(authority_session, actor, "therapist", public_id))))


@therapist_router.post(
    "/qualification-renewals/{review_item_id}/resubmit", status_code=201,
    response_model=SuccessEnvelope[MutationProfileDTO],
    responses=_errors(400, 401, 403, 404, 409, 503),
)
async def post_renewal_resubmit(review_item_id: UUID, payload: TherapistRenewalResubmit, request: Request, idempotency_key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_therapist_onboarding_writer_session), authority_session=Depends(get_db_session)):
    await _safe(require_therapist(authority_session, actor))
    public_id = await _safe(_tenant_public_id(authority_session, actor.tenant_id))
    return _ok(MutationProfileDTO, await _safe(renewal_resubmit(session, actor, str(review_item_id), payload, _request_id(request), idempotency_key, tenant_public_id=public_id, precommit_check=_actor_precommit(authority_session, actor, "therapist", public_id))))


@therapist_router.post(
    "/exit", response_model=SuccessEnvelope[MutationProfileDTO],
    responses=_errors(400, 401, 403, 404, 409, 503),
)
async def post_self_exit(payload: TherapistStatusRequest, request: Request, idempotency_key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_therapist_review_writer_session), authority_session=Depends(get_db_session)):
    await _safe(require_therapist(authority_session, actor))
    profile = await _safe(TherapistQualificationRepository(session).current_profile_for_user(actor.id))
    if profile is None:
        raise HTTPException(404, "THERAPIST_NOT_FOUND")
    return _ok(MutationProfileDTO, await _safe(change_status(session, actor, profile.therapist_id, expected_version=payload.expected_version, decision="EXITED", reason_code=payload.reason_code, request_id=_request_id(request), idempotency_key=idempotency_key, precommit_check=_actor_precommit(authority_session, actor, "therapist"))))


@platform_router.get(
    "/therapist-reviews", response_model=SuccessEnvelope[PageDTO[ReviewItemDTO]],
    responses=_errors(400, 401, 403, 503),
)
async def get_reviews(kind: Annotated[str | None, Query(pattern="^(INITIAL|RENEWAL)$")] = None, status: ReviewStatusValue | None = None, cursor: str | None = Query(None, max_length=512), limit: int = Query(20, ge=1, le=100), actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_therapist_reader_session), authority_session=Depends(get_db_session)):
    decoded = _decode_cursor(cursor, "review")
    await _safe(require_reviewer(authority_session, actor))
    rows = await _safe(TherapistQualificationRepository(session).review_items(kind=kind, status=status, cursor=decoded, limit=limit))
    page = _page(
        rows, limit, item_schema=ReviewItemDTO,
        cursor_factory=lambda row: _encode_cursor({"created_at": row["created_at"].isoformat(), "review_item_id": str(row["review_item_id"])}),
    )
    return _ok(PageDTO[ReviewItemDTO], page)


@platform_router.get(
    "/therapist-reviews/{therapist_id}", response_model=SuccessEnvelope[ReviewDetailDTO],
    responses=_errors(401, 403, 404, 503),
)
async def get_review_detail(therapist_id: UUID, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_therapist_reader_session), authority_session=Depends(get_db_session)):
    await _safe(require_reviewer(authority_session, actor))
    repo = TherapistQualificationRepository(session)
    profile = await _safe(repo.profile_summary(str(therapist_id)))
    if profile is None:
        raise HTTPException(404, "THERAPIST_NOT_FOUND")
    guard = await _safe(repo.readiness_guard(profile["tenant_id"]))
    item = await _safe(repo.current_review_item(str(therapist_id)))
    if guard is None or item is None:
        raise HTTPException(404, "THERAPIST_REVIEW_ITEM_NOT_FOUND")
    value = {
        "profile": _profile_dto(profile, str(guard["tenant_public_id"])),
        "revisions": [dict(row) for row in await _safe(repo.revisions(str(therapist_id)))],
        "qualifications": [dict(row) for row in await _safe(repo.qualifications(str(therapist_id)))],
        "current_qualification_ids": list(await _safe(
            repo.qualification_ids_for_revision(str(therapist_id), str(item["revision_id"]))
        )),
        "review_item": dict(item),
    }
    return _ok(ReviewDetailDTO, value)


@platform_router.post(
    "/therapist-reviews/{therapist_id}/decision",
    response_model=SuccessEnvelope[ReviewDecisionDTO],
    responses=_errors(400, 401, 403, 404, 409, 503),
)
async def post_review_decision(therapist_id: UUID, payload: TherapistReviewDecisionRequest, request: Request, idempotency_key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_therapist_review_writer_session), authority_session=Depends(get_db_session)):
    await _safe(require_reviewer(authority_session, actor))
    result = await _safe(review_decision(session, actor, str(therapist_id), payload, _request_id(request), idempotency_key, precommit_check=_actor_precommit(authority_session, actor, "reviewer")))
    value = await _safe(_review_decision_dto(TherapistQualificationRepository(session), str(therapist_id), result))
    return _ok(ReviewDecisionDTO, value)


@platform_router.get(
    "/therapist-renewal-reviews", response_model=SuccessEnvelope[PageDTO[ReviewItemDTO]],
    responses=_errors(400, 401, 403, 503),
)
async def get_renewal_reviews(status: ReviewStatusValue | None = None, cursor: str | None = Query(None, max_length=512), limit: int = Query(20, ge=1, le=100), actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_therapist_reader_session), authority_session=Depends(get_db_session)):
    decoded = _decode_cursor(cursor, "review")
    await _safe(require_reviewer(authority_session, actor))
    rows = await _safe(TherapistQualificationRepository(session).review_items(kind="RENEWAL", status=status, cursor=decoded, limit=limit))
    page = _page(
        rows, limit, item_schema=ReviewItemDTO,
        cursor_factory=lambda row: _encode_cursor({"created_at": row["created_at"].isoformat(), "review_item_id": str(row["review_item_id"])}),
    )
    return _ok(PageDTO[ReviewItemDTO], page)


@platform_router.post(
    "/therapist-renewal-reviews/{review_item_id}/decision",
    response_model=SuccessEnvelope[ReviewDecisionDTO],
    responses=_errors(400, 401, 403, 404, 409, 503),
)
async def post_renewal_decision(review_item_id: UUID, payload: TherapistReviewDecisionRequest, request: Request, idempotency_key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_therapist_review_writer_session), authority_session=Depends(get_db_session)):
    await _safe(require_reviewer(authority_session, actor))
    repo = TherapistQualificationRepository(session)
    item = await _safe(repo.review_item_for_update(str(review_item_id)))
    if item is None:
        raise HTTPException(404, "THERAPIST_REVIEW_ITEM_NOT_FOUND")
    result = await _safe(review_decision(session, actor, item.therapist_id, payload, _request_id(request), idempotency_key, review_item_id=str(review_item_id), precommit_check=_actor_precommit(authority_session, actor, "reviewer")))
    value = await _safe(_review_decision_dto(repo, item.therapist_id, result))
    return _ok(ReviewDecisionDTO, value)


async def _platform_status(therapist_id: str, decision: str, payload, request, key, actor, session, authority_session):
    await _safe(require_reviewer(authority_session, actor))
    reason = "QUALIFICATION_RENEWED" if decision == "RESUMED" else payload.reason_code
    return _ok(MutationProfileDTO, await _safe(change_status(session, actor, therapist_id, expected_version=payload.expected_version, decision=decision, reason_code=reason, request_id=_request_id(request), idempotency_key=key, precommit_check=_actor_precommit(authority_session, actor, "reviewer"))))


@platform_router.post(
    "/therapists/{therapist_id}/suspend",
    response_model=SuccessEnvelope[MutationProfileDTO], responses=_errors(400, 401, 403, 404, 409, 503),
)
async def post_suspend(therapist_id: UUID, payload: TherapistStatusRequest, request: Request, idempotency_key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_therapist_review_writer_session), authority_session=Depends(get_db_session)):
    return await _platform_status(str(therapist_id), "SUSPENDED", payload, request, idempotency_key, actor, session, authority_session)


@platform_router.post(
    "/therapists/{therapist_id}/resume",
    response_model=SuccessEnvelope[MutationProfileDTO], responses=_errors(400, 401, 403, 404, 409, 503),
)
async def post_resume(therapist_id: UUID, payload: TherapistResumeRequest, request: Request, idempotency_key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_therapist_review_writer_session), authority_session=Depends(get_db_session)):
    return await _platform_status(str(therapist_id), "RESUMED", payload, request, idempotency_key, actor, session, authority_session)


@platform_router.post(
    "/therapists/{therapist_id}/exit",
    response_model=SuccessEnvelope[MutationProfileDTO], responses=_errors(400, 401, 403, 404, 409, 503),
)
async def post_exit(therapist_id: UUID, payload: TherapistStatusRequest, request: Request, idempotency_key: IdempotencyKey, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_therapist_review_writer_session), authority_session=Depends(get_db_session)):
    return await _platform_status(str(therapist_id), "EXITED", payload, request, idempotency_key, actor, session, authority_session)


@platform_router.get(
    "/tenants/{tenant_id}/service-readiness",
    response_model=SuccessEnvelope[ReadinessDTO], responses=_errors(401, 403, 404, 503),
)
async def get_platform_readiness(tenant_id: UUID, actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_therapist_reader_session), authority_session=Depends(get_db_session)):
    await _safe(require_reviewer(authority_session, actor))
    await session.execute(__import__("sqlalchemy").text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    guard = await _safe(TherapistQualificationRepository(session).readiness_guard_by_public_id(str(tenant_id)))
    if guard is None:
        raise HTTPException(404, "READINESS_NOT_FOUND")
    value, stale = await _safe(read_readiness_fail_closed(session, guard["tenant_id"], establish_transaction=False))
    if value is None:
        raise HTTPException(404, "READINESS_NOT_FOUND")
    if stale:
        await _request_readiness_refresh(guard["tenant_id"])
    return _ok(ReadinessDTO, value)


@platform_router.get(
    "/tenants/{tenant_id}/service-readiness/evidence",
    response_model=SuccessEnvelope[PageDTO[EvidenceDTO]], responses=_errors(400, 401, 403, 404, 503),
)
async def get_platform_evidence(tenant_id: UUID, cursor: str | None = Query(None, max_length=512), limit: int = Query(20, ge=1, le=100), actor: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_therapist_reader_session), authority_session=Depends(get_db_session)):
    decoded = _decode_cursor(cursor, "evidence")
    await _safe(require_reviewer(authority_session, actor))
    guard = await _safe(TherapistQualificationRepository(session).readiness_guard_by_public_id(str(tenant_id)))
    if guard is None:
        raise HTTPException(404, "READINESS_NOT_FOUND")
    rows = await _safe(TherapistQualificationRepository(session).readiness_evidence(guard["tenant_id"], cursor=decoded, limit=limit))
    public_rows = tuple(_evidence_dto(row, str(tenant_id)) for row in rows)
    page = _page(
        public_rows, limit, item_schema=EvidenceDTO,
        cursor_factory=lambda row: _encode_cursor({"evidence_version": row["evidence_version"]}),
    )
    return _ok(PageDTO[EvidenceDTO], page)
