from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import text

from app.core.database import (
    get_db_session,
    get_institution_onboarding_reader_session,
    get_member_case_writer_session,
    get_member_enrollment_reader_session,
    get_member_enrollment_writer_session,
    get_member_identity_review_writer_session,
    get_slice3_session_factory,
)
from app.core.security import CurrentUser, get_current_user_from_jwt
from app.core.uuid_generator import Uuid7Generator
from app.modules.auth.service import verify_password
from app.modules.member_enrollment.domain import (
    InvitationAttemptRejected,
    MemberEnrollmentConflict,
)
from app.modules.member_enrollment.identity_authority import (
    verified_adult_eligibility_for_update,
)
from app.modules.member_enrollment.repository import MemberEnrollmentRepository
from app.modules.member_enrollment.schemas import (
    AcceptEnrollmentRequest,
    AssignmentDTO,
    AssignmentCancelRequest,
    AssignmentDeclineRequest,
    AssignmentDetailDTO,
    AssignmentPageDTO,
    ConsentDocumentDTO,
    ConsentRetireRequest,
    ConsentPresentationListDTO,
    ConsentPresentationQuery,
    ConsentRecordDTO,
    ConsentWithdrawRequest,
    CreateAssignmentRequest,
    CreateConsentDocumentRequest,
    CreateMemberInvitationRequest,
    EnrollmentDTO,
    EnrollmentDetailDTO,
    EnrollmentPageDTO,
    EnrollmentSummaryPageDTO,
    ErrorEnvelopeDTO,
    FamilyEnrollmentListQuery,
    IdentityPiiDTO,
    IdentityResubmitRequest,
    IdentityReviewDetailDTO,
    IdentityReviewPageDTO,
    IdentityStatusDTO,
    InstitutionEnrollmentListQuery,
    InstitutionIdentityCheckRequest,
    InvitationDTO,
    InvitationRevokeRequest,
    InvitationListQuery,
    InvitationPageDTO,
    InvitationSecretDTO,
    PiiAccessRequest,
    PlatformIdentityDecisionRequest,
    PreparingCaseDTO,
    ProxyGrantDTO,
    ProxyRevokeRequest,
    PublishConsentDocumentRequest,
    ReasonedVersionRequest,
    RecordConsentRequest,
    TherapistAssignmentListQuery,
    UuidV7,
    VersionRequest,
    IdentitySubmissionRequest,
)
from app.modules.member_enrollment.service import (
    CommitOutcome,
    MemberEnrollmentSecrets,
    MemberEnrollmentService,
    MutationContext,
    commit_with_confirmation,
    reviewer_credential_proof,
    safe_error_code,
)


_STATUS = {
    "INVALID_REQUEST":400,"INVALID_CURSOR":400,"CONSENT_LOCALE_UNAVAILABLE":400,
    "ACTOR_CURRENTNESS_FORBIDDEN":403,"TENANT_SCOPE_FORBIDDEN":403,"PROXY_PERMISSION_FORBIDDEN":403,
    "REVIEWER_CURRENTNESS_FORBIDDEN":403,"STEP_UP_FORBIDDEN":403,
    "THERAPIST_CURRENTNESS_FORBIDDEN":403,"THERAPIST_SCOPE_FORBIDDEN":403,
    "INVITATION_NOT_FOUND":404,"ENROLLMENT_NOT_FOUND":404,"IDENTITY_REVIEW_NOT_FOUND":404,
    "ASSIGNMENT_NOT_FOUND":404,"CASE_NOT_FOUND":404,"CONSENT_RECORD_NOT_FOUND":404,
    "PROXY_GRANT_NOT_FOUND":404,"CONSENT_DOCUMENT_NOT_FOUND":404,
    "STEP_UP_RATE_LIMITED":429,"DEPENDENCY_UNAVAILABLE":503,"COMMIT_OUTCOME_UNKNOWN":503,
}


def _route_errors(*codes: str, mutation: bool = False):
    values=("AUTHENTICATION_REQUIRED",*codes,"DEPENDENCY_UNAVAILABLE") + (("COMMIT_OUTCOME_UNKNOWN",) if mutation else ())
    grouped={}
    for code in values: grouped.setdefault(_STATUS.get(code,401 if code=="AUTHENTICATION_REQUIRED" else 409),[]).append(code)
    return {status:tuple(items) for status,items in grouped.items()}


SLICE3_ROUTE_ERROR_CODES = {
    ("POST","/api/v1/institution/member-invitations"):_route_errors("INVALID_REQUEST","ACTOR_CURRENTNESS_FORBIDDEN","TENANT_SCOPE_FORBIDDEN","SERVICE_NOT_READY","INVITATION_OPEN_CONFLICT",mutation=True),
    ("GET","/api/v1/institution/member-invitations"):_route_errors("INVALID_CURSOR","ACTOR_CURRENTNESS_FORBIDDEN","TENANT_SCOPE_FORBIDDEN"),
    ("POST","/api/v1/institution/member-invitations/{invitation_id}/resend"):_route_errors("INVALID_REQUEST","ACTOR_CURRENTNESS_FORBIDDEN","TENANT_SCOPE_FORBIDDEN","INVITATION_NOT_FOUND","VERSION_CONFLICT","STATE_CONFLICT","INVITATION_EXHAUSTED",mutation=True),
    ("POST","/api/v1/institution/member-invitations/{invitation_id}/revoke"):_route_errors("INVALID_REQUEST","ACTOR_CURRENTNESS_FORBIDDEN","TENANT_SCOPE_FORBIDDEN","INVITATION_NOT_FOUND","VERSION_CONFLICT","STATE_CONFLICT",mutation=True),
    ("GET","/api/v1/institution/member-enrollments"):_route_errors("INVALID_CURSOR","ACTOR_CURRENTNESS_FORBIDDEN","TENANT_SCOPE_FORBIDDEN"),
    ("GET","/api/v1/institution/member-enrollments/{enrollment_id}"):_route_errors("ACTOR_CURRENTNESS_FORBIDDEN","TENANT_SCOPE_FORBIDDEN","ENROLLMENT_NOT_FOUND"),
    ("POST","/api/v1/institution/member-enrollments/{enrollment_id}/identity-check"):_route_errors("INVALID_REQUEST","ACTOR_CURRENTNESS_FORBIDDEN","TENANT_SCOPE_FORBIDDEN","ENROLLMENT_NOT_FOUND","VERSION_CONFLICT","STATE_CONFLICT","IDENTITY_REVISION_STALE",mutation=True),
    ("POST","/api/v1/institution/member-enrollments/{enrollment_id}/primary-assignments"):_route_errors("INVALID_REQUEST","ACTOR_CURRENTNESS_FORBIDDEN","TENANT_SCOPE_FORBIDDEN","ENROLLMENT_NOT_FOUND","VERSION_CONFLICT","STATE_CONFLICT","SERVICE_NOT_READY","CONSENT_REQUIRED","THERAPIST_NOT_ELIGIBLE",mutation=True),
    ("POST","/api/v1/institution/primary-assignments/{assignment_id}/cancel"):_route_errors("INVALID_REQUEST","ACTOR_CURRENTNESS_FORBIDDEN","TENANT_SCOPE_FORBIDDEN","ASSIGNMENT_NOT_FOUND","VERSION_CONFLICT","STATE_CONFLICT",mutation=True),
    ("GET","/api/v1/institution/service-cases/{case_id}"):_route_errors("ACTOR_CURRENTNESS_FORBIDDEN","TENANT_SCOPE_FORBIDDEN","CASE_NOT_FOUND"),
    ("POST","/api/v1/family/member-enrollments/accept"):_route_errors("INVALID_REQUEST","ACTOR_CURRENTNESS_FORBIDDEN","INVITATION_NOT_FOUND","INVITATION_CODE_INVALID","INVITATION_EXHAUSTED","SERVICE_NOT_READY","ACTIVE_ENROLLMENT_EXISTS","PROXY_ADULT_IDENTITY_REQUIRED","PROXY_LIMIT_REACHED",mutation=True),
    ("GET","/api/v1/family/member-enrollments"):_route_errors("INVALID_CURSOR","ACTOR_CURRENTNESS_FORBIDDEN"),
    ("GET","/api/v1/family/member-enrollments/{enrollment_id}"):_route_errors("ACTOR_CURRENTNESS_FORBIDDEN","PROXY_PERMISSION_FORBIDDEN","ENROLLMENT_NOT_FOUND"),
    ("PUT","/api/v1/family/member-enrollments/{enrollment_id}/identity-submission"):_route_errors("INVALID_REQUEST","ACTOR_CURRENTNESS_FORBIDDEN","PROXY_PERMISSION_FORBIDDEN","ENROLLMENT_NOT_FOUND","VERSION_CONFLICT","STATE_CONFLICT","DUPLICATE_IDENTITY",mutation=True),
    ("POST","/api/v1/family/member-enrollments/{enrollment_id}/identity-resubmit"):_route_errors("INVALID_REQUEST","ACTOR_CURRENTNESS_FORBIDDEN","PROXY_PERMISSION_FORBIDDEN","ENROLLMENT_NOT_FOUND","VERSION_CONFLICT","STATE_CONFLICT","CORRECTION_SCOPE_CONFLICT","DUPLICATE_IDENTITY",mutation=True),
    ("GET","/api/v1/family/member-enrollments/{enrollment_id}/consent-presentations"):_route_errors("CONSENT_LOCALE_UNAVAILABLE","ACTOR_CURRENTNESS_FORBIDDEN","PROXY_PERMISSION_FORBIDDEN","ENROLLMENT_NOT_FOUND","IDENTITY_REQUIRED"),
    ("POST","/api/v1/family/member-enrollments/{enrollment_id}/consent-records"):_route_errors("INVALID_REQUEST","ACTOR_CURRENTNESS_FORBIDDEN","PROXY_PERMISSION_FORBIDDEN","ENROLLMENT_NOT_FOUND","VERSION_CONFLICT","STATE_CONFLICT","CONSENT_VERSION_STALE","PROXY_GRANT_INVALID",mutation=True),
    ("POST","/api/v1/family/consent-records/{consent_record_id}/withdraw"):_route_errors("INVALID_REQUEST","ACTOR_CURRENTNESS_FORBIDDEN","PROXY_PERMISSION_FORBIDDEN","CONSENT_RECORD_NOT_FOUND","VERSION_CONFLICT","STATE_CONFLICT",mutation=True),
    ("POST","/api/v1/family/proxy-grants/{grant_id}/revoke"):_route_errors("INVALID_REQUEST","ACTOR_CURRENTNESS_FORBIDDEN","PROXY_PERMISSION_FORBIDDEN","PROXY_GRANT_NOT_FOUND","VERSION_CONFLICT","STATE_CONFLICT",mutation=True),
    ("GET","/api/v1/platform/member-identity-reviews"):_route_errors("INVALID_CURSOR","REVIEWER_CURRENTNESS_FORBIDDEN"),
    ("GET","/api/v1/platform/member-identity-reviews/{review_id}"):_route_errors("REVIEWER_CURRENTNESS_FORBIDDEN","IDENTITY_REVIEW_NOT_FOUND"),
    ("POST","/api/v1/platform/member-identity-reviews/{review_id}/claim"):_route_errors("INVALID_REQUEST","REVIEWER_CURRENTNESS_FORBIDDEN","IDENTITY_REVIEW_NOT_FOUND","VERSION_CONFLICT","STATE_CONFLICT",mutation=True),
    ("POST","/api/v1/platform/member-identity-reviews/{review_id}/pii-access"):_route_errors("INVALID_REQUEST","REVIEWER_CURRENTNESS_FORBIDDEN","STEP_UP_FORBIDDEN","IDENTITY_REVIEW_NOT_FOUND","STEP_UP_REPLAYED","STEP_UP_RATE_LIMITED",mutation=True),
    ("POST","/api/v1/platform/member-identity-reviews/{review_id}/decision"):_route_errors("INVALID_REQUEST","REVIEWER_CURRENTNESS_FORBIDDEN","IDENTITY_REVIEW_NOT_FOUND","VERSION_CONFLICT","STATE_CONFLICT","IDENTITY_REVISION_STALE","DUPLICATE_IDENTITY",mutation=True),
    ("POST","/api/v1/platform/consent-documents"):_route_errors("INVALID_REQUEST","REVIEWER_CURRENTNESS_FORBIDDEN","CONSENT_VERSION_CONFLICT",mutation=True),
    ("POST","/api/v1/platform/consent-documents/{document_version_id}/publish"):_route_errors("INVALID_REQUEST","REVIEWER_CURRENTNESS_FORBIDDEN","CONSENT_DOCUMENT_NOT_FOUND","VERSION_CONFLICT","STATE_CONFLICT","CONSENT_RENDITION_INCOMPLETE",mutation=True),
    ("POST","/api/v1/platform/consent-documents/{document_version_id}/retire"):_route_errors("INVALID_REQUEST","REVIEWER_CURRENTNESS_FORBIDDEN","CONSENT_DOCUMENT_NOT_FOUND","VERSION_CONFLICT","STATE_CONFLICT",mutation=True),
    ("GET","/api/v1/therapist/primary-assignments"):_route_errors("INVALID_CURSOR","THERAPIST_CURRENTNESS_FORBIDDEN"),
    ("GET","/api/v1/therapist/primary-assignments/{assignment_id}"):_route_errors("THERAPIST_CURRENTNESS_FORBIDDEN","THERAPIST_SCOPE_FORBIDDEN","ASSIGNMENT_NOT_FOUND"),
    ("POST","/api/v1/therapist/primary-assignments/{assignment_id}/accept"):_route_errors("INVALID_REQUEST","THERAPIST_CURRENTNESS_FORBIDDEN","THERAPIST_SCOPE_FORBIDDEN","ASSIGNMENT_NOT_FOUND","VERSION_CONFLICT","STATE_CONFLICT","SERVICE_NOT_READY","IDENTITY_REQUIRED","CONSENT_REQUIRED","PROXY_GRANT_INVALID","ACTIVE_ENROLLMENT_EXISTS","THERAPIST_NOT_ELIGIBLE","THERAPIST_CAPACITY_REACHED",mutation=True),
    ("POST","/api/v1/therapist/primary-assignments/{assignment_id}/decline"):_route_errors("INVALID_REQUEST","THERAPIST_CURRENTNESS_FORBIDDEN","THERAPIST_SCOPE_FORBIDDEN","ASSIGNMENT_NOT_FOUND","VERSION_CONFLICT","STATE_CONFLICT",mutation=True),
    ("GET","/api/v1/therapist/service-cases/{case_id}"):_route_errors("THERAPIST_CURRENTNESS_FORBIDDEN","THERAPIST_SCOPE_FORBIDDEN","CASE_NOT_FOUND"),
}


class MemberEnrollmentRoute(APIRoute):
    def get_route_handler(self):
        original=super().get_route_handler()
        async def handler(request:Request):
            try: return await original(request)
            except RequestValidationError: return JSONResponse(status_code=400,content={"code":"INVALID_REQUEST","message":"request rejected"})
            except HTTPException as exc:
                key=(next(iter(self.methods)),self.path); allowed=SLICE3_ROUTE_ERROR_CODES[key]; detail=exc.detail; code=detail.get("code") if isinstance(detail,dict) else detail if isinstance(detail,str) else None
                status=400 if exc.status_code==422 else exc.status_code
                if status == 401:
                    code = "AUTHENTICATION_REQUIRED"
                if type(code) is not str or code not in allowed.get(status,()):
                    status,code=(400,"INVALID_REQUEST") if "INVALID_REQUEST" in allowed.get(400,()) else (503,"DEPENDENCY_UNAVAILABLE")
                return JSONResponse(status_code=status,content={"code":code,"message":"request rejected"})
            except Exception: return JSONResponse(status_code=503,content={"code":"DEPENDENCY_UNAVAILABLE","message":"request rejected"})
        return handler


institution_router = APIRouter(prefix="/api/v1/institution", tags=["member-enrollment"],route_class=MemberEnrollmentRoute)
family_router = APIRouter(prefix="/api/v1/family", tags=["member-enrollment"],route_class=MemberEnrollmentRoute)
platform_router = APIRouter(prefix="/api/v1/platform", tags=["member-enrollment"],route_class=MemberEnrollmentRoute)
therapist_router = APIRouter(prefix="/api/v1/therapist", tags=["member-enrollment"],route_class=MemberEnrollmentRoute)
routers = (institution_router, family_router, platform_router, therapist_router)


def strip_member_enrollment_validation_responses(schema: dict[str,object]):
    paths=schema.get("paths",{})
    if not isinstance(paths,dict): return schema
    for (method,path),errors in SLICE3_ROUTE_ERROR_CODES.items():
        operation=paths.get(path,{}).get(method.lower())
        if not isinstance(operation,dict): continue
        responses=operation.get("responses",{})
        if isinstance(responses,dict): responses.pop("422",None)
        operation["x-symbolic-error-codes"]={str(status):list(codes) for status,codes in errors.items()}
    return schema

IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]


def _error(code: str) -> HTTPException:
    statuses = {
        "INVALID_REQUEST": 400, "INVALID_CURSOR": 400,
        "ACTOR_CURRENTNESS_FORBIDDEN": 403, "TENANT_SCOPE_FORBIDDEN": 403,
        "PROXY_PERMISSION_FORBIDDEN": 403, "REVIEWER_CURRENTNESS_FORBIDDEN": 403,
        "THERAPIST_CURRENTNESS_FORBIDDEN": 403, "THERAPIST_SCOPE_FORBIDDEN": 403,
        "STEP_UP_FORBIDDEN": 403, "STEP_UP_RATE_LIMITED": 429,
        "INVITATION_NOT_FOUND": 404, "ENROLLMENT_NOT_FOUND": 404,
        "IDENTITY_REVIEW_NOT_FOUND": 404, "ASSIGNMENT_NOT_FOUND": 404,
        "CASE_NOT_FOUND": 404,
        "DEPENDENCY_UNAVAILABLE": 503, "COMMIT_OUTCOME_UNKNOWN": 503,
    }
    return HTTPException(status_code=statuses.get(code, 409), detail={"code": code, "message": code})


async def _safe(call):
    try:
        return await call
    except HTTPException:
        raise
    except Exception as error:
        raise _error(safe_error_code(error)) from None


def _require_institution(actor: CurrentUser) -> int:
    if actor.role not in {"org_admin", "org_operator"} or actor.tenant_id is None:
        raise _error("ACTOR_CURRENTNESS_FORBIDDEN")
    return actor.tenant_id


def _require_platform(actor: CurrentUser) -> None:
    if actor.role != "super_admin" or actor.tenant_id is not None or actor.org_id is not None:
        raise _error("REVIEWER_CURRENTNESS_FORBIDDEN")


async def _approved_institution(
    institution_authority,
    *,
    tenant_id: int | None = None,
    tenant_public_id: UUID | None = None,
    error_code: str = "TENANT_SCOPE_FORBIDDEN",
) -> tuple[int, UUID]:
    if (tenant_id is None) == (tenant_public_id is None):
        raise _error(error_code)
    predicate = (
        "tenant_internal_id=:tenant_id"
        if tenant_id is not None
        else "tenant_public_id=:tenant_public_id"
    )
    parameters = (
        {"tenant_id": tenant_id}
        if tenant_id is not None
        else {"tenant_public_id": tenant_public_id}
    )
    result = await institution_authority.execute(
        text(
            "SELECT tenant_internal_id,tenant_public_id,status "
            "FROM public.institution_application "
            f"WHERE {predicate}"
        ),
        parameters,
    )
    row = result.mappings().one_or_none()
    if (
        row is None
        or row["status"] != "APPROVED"
        or row["tenant_internal_id"] is None
        or row["tenant_public_id"] is None
        or (tenant_id is not None and row["tenant_internal_id"] != tenant_id)
        or (
            tenant_public_id is not None
            and UUID(str(row["tenant_public_id"])) != tenant_public_id
        )
    ):
        raise _error(error_code)
    return row["tenant_internal_id"], UUID(str(row["tenant_public_id"]))


async def _tenant_public_id(institution_authority, tenant_id: int) -> UUID:
    _, public_id = await _approved_institution(
        institution_authority, tenant_id=tenant_id
    )
    return public_id


async def _current_institution(
    authority, institution_authority, actor: CurrentUser
) -> tuple[int, UUID]:
    tenant_id = _require_institution(actor)
    result = await authority.execute(
        text(
            'SELECT u.id,u.role,u.status,u.tenant_id,t.status AS tenant_status '
            'FROM public."user" u JOIN public.tenant t ON t.id=u.tenant_id '
            "WHERE u.id=:user_id FOR SHARE OF u,t"
        ),
        {"user_id": actor.id},
    )
    row = result.mappings().one_or_none()
    if (
        row is None
        or row["role"] not in {"org_admin", "org_operator"}
        or row["status"] != "active"
        or row["tenant_id"] != tenant_id
        or row["tenant_status"] != "active"
    ):
        raise _error("ACTOR_CURRENTNESS_FORBIDDEN")
    approved_tenant_id, tenant_public_id = await _approved_institution(
        institution_authority, tenant_id=tenant_id
    )
    if approved_tenant_id != tenant_id:
        raise _error("TENANT_SCOPE_FORBIDDEN")
    return tenant_id, tenant_public_id


def _context(request: Request, actor: CurrentUser, tenant_id: int | None, tenant_public_id: UUID, key: str, *, platform_scope: bool = False) -> MutationContext:
    request_id = getattr(request.state, "request_id", None)
    try:
        parsed = UUID(str(request_id))
        if parsed.version != 7:
            raise ValueError
    except Exception:
        parsed = Uuid7Generator().generate()
    return MutationContext(actor=actor, tenant_id=tenant_id, tenant_public_id=tenant_public_id, idempotency_key=key, request_id=parsed, platform_scope=platform_scope)


def _service(session) -> MemberEnrollmentService:
    try:
        secrets = MemberEnrollmentSecrets()
    except Exception:
        raise _error("DEPENDENCY_UNAVAILABLE") from None
    return MemberEnrollmentService(MemberEnrollmentRepository(session), secrets_port=secrets)


async def _member_for_actor(
    authority, actor: CurrentUser, *, expected_phone: str | None = None
) -> UUID:
    if (
        actor.role != "member"
        or actor.tenant_id is not None
        or actor.org_id is not None
    ):
        raise _error("ACTOR_CURRENTNESS_FORBIDDEN")
    result = await authority.execute(text(
        "SELECT l.member_id,u.phone,u.role,u.status,u.tenant_id,m.status AS member_status "
        "FROM public.\"user\" u JOIN identity.user_member_self_link l "
        "ON l.user_ref=u.id JOIN identity.member m ON m.member_id=l.member_id "
        "WHERE u.id=:user_id FOR SHARE OF u,l,m"
    ), {"user_id": actor.id})
    row = result.mappings().one_or_none()
    if (
        row is None
        or row["role"] != "member"
        or row["status"] != "active"
        or row["tenant_id"] is not None
        or row["member_status"] != "created"
        or (expected_phone is not None and row["phone"] != expected_phone)
    ):
        raise _error("ACTOR_CURRENTNESS_FORBIDDEN")
    return UUID(str(row["member_id"]))


async def _tenant_context(
    request: Request,
    actor: CurrentUser,
    tenant_id: int,
    key: str,
    institution_authority,
) -> MutationContext:
    return _context(
        request,
        actor,
        tenant_id,
        await _tenant_public_id(institution_authority, tenant_id),
        key,
    )


def _public_row(row) -> dict:
    value = dict(row)
    if "tenant_public_id" in value:
        value["tenant_id"] = value.pop("tenant_public_id")
    return value


def _cursor_id(cursor: str | None) -> UUID | None:
    if cursor is None:
        return None
    try:
        value = UUID(cursor)
    except (TypeError, ValueError, AttributeError):
        raise _error("INVALID_CURSOR") from None
    if value.version != 7 or str(value) != cursor.lower():
        raise _error("INVALID_CURSOR")
    return value


async def _view_page(
    session, view: str, predicates: dict[str, object], order: str, limit: int,
    *, cursor: str | None = None,
):
    rows = await MemberEnrollmentRepository(session).safe_view_rows(
        view, predicates=predicates, order=order, cursor_id=_cursor_id(cursor), limit=limit + 1
    )
    return {
        "items": tuple(_public_row(row) for row in rows[:limit]),
        "next_cursor": str(rows[limit][order]) if len(rows) > limit else None,
    }


def _enrollment_detail(row) -> dict:
    item = _public_row(row)
    item["identity"] = item.pop("identity", None)
    item["proxy"] = item.pop("proxy", None)
    item["consents"] = tuple(item.pop("consents", ()) or ())
    item["assignment"] = item.pop("assignment", None)
    return item


def _receipt_target(context: MutationContext, operation: str, target_id: UUID | None) -> UUID:
    if target_id is not None:
        return target_id
    return uuid5(
        NAMESPACE_URL,
        f"phase1-slice3/{context.actor_scope}/{operation}/{context.idempotency_key}",
    )


def _request_value(payload: object, target_id: UUID | None = None) -> object:
    value = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
    return {"target_id": str(target_id) if target_id is not None else None, "payload": value}


def _prewrite_confirmation_postimage(
    operation: str,
    request_value: object,
    *,
    target_id: UUID,
    request_id: UUID,
    request_digest: str,
    secrets: MemberEnrollmentSecrets,
) -> dict[str, object]:
    request_map = request_value if type(request_value) is dict else {}
    payload = request_map.get("payload") if type(request_map.get("payload")) is dict else {}
    event_by_operation = {
        "INVITATION_CREATE": "MEMBER_INVITATION_CREATED",
        "INVITATION_RESEND": "MEMBER_INVITATION_RESENT",
        "INVITATION_REVOKE": "MEMBER_INVITATION_REVOKED",
        "ENROLLMENT_ACCEPT": "MEMBER_ENROLLMENT_ACCEPTED",
        "IDENTITY_SUBMIT": "MEMBER_IDENTITY_SUBMITTED",
        "IDENTITY_RESUBMIT": "MEMBER_IDENTITY_RESUBMITTED",
        "INSTITUTION_IDENTITY_CHECK": "MEMBER_IDENTITY_INSTITUTION_CHECKED",
        "PROXY_REVOKE": "PROXY_GRANT_REVOKED",
        "CONSENT_WITHDRAW": "CONSENT_WITHDRAWN",
        "ASSIGNMENT_CREATE": "PRIMARY_ASSIGNMENT_CREATED",
        "ASSIGNMENT_CANCEL": "PRIMARY_ASSIGNMENT_CANCELLED",
        "CONSENT_DOCUMENT_PUBLISH": "CONSENT_DOCUMENT_PUBLISHED",
        "CONSENT_DOCUMENT_RETIRE": "CONSENT_DOCUMENT_RETIRED",
        "ASSIGNMENT_ACCEPT": "SERVICE_CASE_PREPARING_CREATED",
        "ASSIGNMENT_DECLINE": "PRIMARY_ASSIGNMENT_DECLINED",
    }
    if operation == "CONSENT_RECORD":
        event_by_operation[operation] = (
            "CONSENT_ACCEPTED"
            if payload.get("decision") == "ACCEPTED"
            else "CONSENT_DECLINED"
        )
    if operation == "IDENTITY_REVIEW_DECIDE":
        event_by_operation[operation] = {
            "APPROVED": "MEMBER_IDENTITY_VERIFIED",
            "NEEDS_CORRECTION": "MEMBER_IDENTITY_CORRECTION_REQUESTED",
            "REJECTED": "MEMBER_IDENTITY_REJECTED",
        }.get(payload.get("decision"), "")
    audit_action = {
        "IDENTITY_REVIEW_CLAIM": "IDENTITY_REVIEW_CLAIMED",
        "IDENTITY_PII_ACCESS": "IDENTITY_PII_STEP_UP_RESULT",
    }.get(operation, event_by_operation.get(operation, ""))
    event_type = event_by_operation.get(operation, "")
    plan = {
        "operation": operation,
        "target_id": str(target_id),
        "request_id": str(request_id),
        "preimage": {"request_digest": request_digest, "rows": []},
        "postimage": {
            "decision": payload.get("decision"),
            "expected_version": payload.get(
                "expected_version", payload.get("expected_review_version")
            ),
            "operation": operation,
            "target_id": str(target_id),
        },
        "audit": {"action": audit_action, "expected_count": 0 if not audit_action else 1},
        "outbox": {"event_type": event_type, "expected_count": 0 if not event_type else 1},
        "receipt": {
            "expected_count": 1,
            "response_digest": request_digest,
            "expected_confirmed_digest": None,
        },
    }
    return plan


async def _replay_result(
    session, context: MutationContext, operation: str, target_id: UUID,
    request_value: object, secrets: MemberEnrollmentSecrets,
) -> object | None:
    request_digest = secrets.request_digest(request_value)
    row = await MemberEnrollmentRepository(session).replay_idempotency(
        context.actor_scope, operation, target_id, context.idempotency_key,
        request_digest,
    )
    if not row["found"]:
        return None
    result = secrets.decrypt_replay(
        row["response_ciphertext"], row["response_key_id"],
        actor_scope=context.actor_scope, operation=operation,
        target_id=target_id, key=context.idempotency_key,
    )
    if secrets.audit_digest({"operation": operation, "result": result}) != row["postimage_digest"]:
        raise _error("COMMIT_OUTCOME_UNKNOWN")
    return result


async def _begin_mutation(
    session, context: MutationContext, operation: str,
    request_value: object, *, target_id: UUID | None = None,
) -> tuple[UUID, MemberEnrollmentSecrets, object | None]:
    target = _receipt_target(context, operation, target_id)
    try:
        secrets = MemberEnrollmentSecrets()
        repository = MemberEnrollmentRepository(session)
        kind = (
            "review_writer"
            if operation.startswith("IDENTITY_REVIEW_")
            or operation.startswith("IDENTITY_PII_")
            or operation.startswith("CONSENT_DOCUMENT_")
            else "case_writer"
            if operation in {"ASSIGNMENT_ACCEPT", "ASSIGNMENT_DECLINE"}
            else "enrollment_writer"
        )
        if not await repository.digest_algorithm_guard(
            secrets.digest_guard_payload(kind)
        ):
            raise RuntimeError("MEMBER_ENROLLMENT_DEPENDENCY_UNAVAILABLE") from None
        await repository.lock_operation(
            context.actor_scope, operation, target, context.idempotency_key,
        )
        request_digest = secrets.request_digest(request_value)
        expected = _prewrite_confirmation_postimage(
            operation,
            request_value,
            target_id=target,
            request_id=context.request_id,
            request_digest=request_digest,
            secrets=secrets,
        )
        session.info[("slice3-prewrite", operation, target, context.idempotency_key)] = expected
        session.info["slice3-mutation-plan"] = {
            "pre_rows": [],
            "rows": [],
            "collections": [],
        }
        replay = await _replay_result(
            session, context, operation, target, request_value, secrets,
        )
        if (
            type(replay) is dict
            and set(replay) == {"error_code"}
            and type(replay["error_code"]) is str
        ):
            raise _error(replay["error_code"])
        if replay is not None:
            session.info.pop(
                ("slice3-prewrite", operation, target, context.idempotency_key), None
            )
        return target, secrets, replay
    except HTTPException:
        raise
    except Exception as error:
        raise _error(safe_error_code(error)) from None


async def _finish_mutation(
    session, *, kind: str, context: MutationContext, operation: str,
    target_id: UUID, request_value: object, result: object,
    secrets: MemberEnrollmentSecrets,
) -> object:
    request_digest = secrets.request_digest(request_value)
    expectation_key = ("slice3-prewrite", operation, target_id, context.idempotency_key)
    expected_envelope = session.info.pop(expectation_key, None)
    if type(expected_envelope) is not dict:
        raise _error("COMMIT_OUTCOME_UNKNOWN")
    if operation == "IDENTITY_PII_ACCESS":
        step_up_result = session.info.pop(
            ("slice3-step-up-result", context.request_id), None
        )
        if type(step_up_result) is not dict or step_up_result.get("result_variant") not in {
            "ELIGIBLE", "FAILED", "RATE_LIMITED", "REPLAYED"
        }:
            raise _error("COMMIT_OUTCOME_UNKNOWN")
        result_variant = step_up_result["result_variant"]
        expected_envelope["postimage"].update(
            {
                "result_variant": result_variant,
                "failure_count": step_up_result.get("failure_count"),
                "locked_until": step_up_result.get("locked_until"),
                "budget_postimage_digest": step_up_result.get(
                    "budget_postimage_digest"
                ),
            }
        )
        expected_envelope["audit"]["action"] = {
            "ELIGIBLE": "IDENTITY_PII_ACCESSED",
            "FAILED": "IDENTITY_PII_STEP_UP_FAILED",
            "RATE_LIMITED": "IDENTITY_PII_STEP_UP_RATE_LIMITED",
            "REPLAYED": "",
        }[result_variant]
        expected_envelope["audit"]["expected_count"] = (
            0 if result_variant == "REPLAYED" else 1
        )
    mutation_plan = session.info.pop("slice3-mutation-plan", None)
    if (
        type(mutation_plan) is not dict
        or type(mutation_plan.get("pre_rows")) is not list
        or type(mutation_plan.get("rows")) is not list
    ):
        raise _error("COMMIT_OUTCOME_UNKNOWN")
    expected_envelope["preimage"]["rows"] = mutation_plan["pre_rows"]
    expected_envelope["postimage"]["rows"] = mutation_plan["rows"]
    collections = mutation_plan.get("collections")
    if type(collections) is not list:
        raise _error("COMMIT_OUTCOME_UNKNOWN")
    expected_envelope["preimage"]["collections"] = [
        {
            "table": item["table"],
            "scope": item["scope"],
            "key_field": item["key_field"],
            "keys": item["pre_keys"],
        }
        for item in collections
    ]
    expected_envelope["postimage"]["collections"] = [
        {
            "table": item["table"],
            "scope": item["scope"],
            "key_field": item["key_field"],
            "keys": item["post_keys"],
        }
        for item in collections
    ]
    audit_rows = [
        row for row in mutation_plan["rows"]
        if row.get("table") == "member_enrollment_audit"
    ]
    outbox_rows = [
        row for row in mutation_plan["rows"]
        if row.get("table") == "member_enrollment_outbox"
    ]
    expected_envelope["audit"]["expected_count"] = len(audit_rows)
    expected_envelope["outbox"]["expected_count"] = len(outbox_rows)
    postimage_digest = secrets.audit_digest({"operation": operation, "result": result})
    expected_envelope["receipt"]["response_digest"] = postimage_digest
    ciphertext, key_id = secrets.encrypt_replay(
        result, actor_scope=context.actor_scope, operation=operation,
        target_id=target_id, key=context.idempotency_key,
    )
    receipt_created_at = datetime.now(timezone.utc)
    expected_envelope["receipt"].update(
        {
            "actor_scope": context.actor_scope,
            "operation": operation,
            "target_id": str(target_id),
            "idempotency_key": context.idempotency_key,
            "request_digest": request_digest,
            "response_key_id": key_id,
            "response_ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
            "created_at": receipt_created_at,
            "expected_confirmed_digest": None,
        }
    )
    expected_digest = await MemberEnrollmentRepository(session).expected_mutation_postimage(
        kind,
        actor_scope=context.actor_scope,
        operation=operation,
        target_id=target_id,
        idempotency_key=context.idempotency_key,
        request_digest=request_digest,
        expected_postimage=expected_envelope,
    )
    if type(expected_digest) is not str or len(expected_digest) != 64:
        raise _error("COMMIT_OUTCOME_UNKNOWN")
    expected_envelope["receipt"]["expected_confirmed_digest"] = expected_digest
    row = await MemberEnrollmentRepository(session).record_idempotency(
        context.actor_scope, operation, target_id, context.idempotency_key,
        request_digest, ciphertext, key_id, postimage_digest,
        receipt_created_at,
    )
    if not row["found"] or row["postimage_digest"] != postimage_digest:
        raise _error("COMMIT_OUTCOME_UNKNOWN")
    async def confirm() -> CommitOutcome:
        factory = await get_slice3_session_factory(kind)
        async with factory() as fresh:
            row = await MemberEnrollmentRepository(fresh).confirm_mutation_outcome(
                kind,
                actor_scope=context.actor_scope,
                operation=operation,
                target_id=target_id,
                idempotency_key=context.idempotency_key,
                request_digest=request_digest,
                expected_postimage=expected_envelope,
            )
            return CommitOutcome(row["outcome"])

    try:
        outcome = await commit_with_confirmation(session, confirm=confirm)
    except Exception as error:
        raise _error(safe_error_code(error)) from None
    if outcome is not CommitOutcome.COMMITTED:
        raise _error("COMMIT_OUTCOME_UNKNOWN")
    return result


def _invitation(row, tenant_public_id: UUID, *, short_code: str | None = None) -> dict:
    value = {
        "invitation_id": row["invitation_id"], "tenant_id": tenant_public_id,
        "mode": row["mode"], "phone_masked": row["phone_masked"],
        "expires_at": row["expires_at"], "status": row["status"],
        "failed_attempts": row["failed_attempts"], "issued_at": row["issued_at"],
        "accepted_at": row["accepted_at"], "revoked_at": row["revoked_at"],
        "version": row["version"],
    }
    if short_code is not None:
        value["short_code"] = short_code
    return value


def _enrollment(row, tenant_public_id: UUID) -> dict:
    return {
        "enrollment_id": row["enrollment_id"], "tenant_id": tenant_public_id,
        "subject_member_id": row["subject_member_id"],
        "proxy_member_id": row["proxy_member_id"], "mode": row["mode"],
        "status": row["status"], "service_scope_tags": tuple(row["service_scope_tags"]),
        "current_identity_verification_id": row["current_identity_verification_id"],
        "current_assignment_id": row["current_assignment_id"],
        "service_case_id": row["service_case_id"], "accepted_at": row["accepted_at"],
        "identity_verified_at": row["identity_verified_at"],
        "case_created_at": row["case_created_at"], "version": row["version"],
    }


def _identity(row, revision, reasons=()) -> dict:
    return {
        "verification_id": row["verification_id"], "enrollment_id": row["enrollment_id"],
        "member_id": row["member_id"], "current_revision_id": row["current_revision_id"],
        "status": row["status"], "id_masked": revision["id_masked"],
        "submitted_at": row["submitted_at"],
        "institution_checked_at": row["institution_checked_at"],
        "platform_decided_at": row["platform_decided_at"],
        "reason_codes": tuple(reasons), "version": row["version"],
    }


def _assignment(row, tenant_public_id: UUID) -> dict:
    return {
        "assignment_id": row["assignment_id"], "enrollment_id": row["enrollment_id"],
        "tenant_id": tenant_public_id, "subject_member_id": row["subject_member_id"],
        "therapist_id": row["therapist_id"], "status": row["status"],
        "service_scope_tags": tuple(row["service_scope_tags"]),
        "reason_code": row["reason_code"], "service_case_id": row["service_case_id"],
        "created_at": row["created_at"], "decided_at": row["decided_at"],
        "version": row["version"],
    }


def _case(row, tenant_public_id: UUID) -> dict:
    return {
        "case_id": row["case_id"], "enrollment_id": row["enrollment_id"],
        "subject_member_id": row["subject_member_id"], "tenant_id": tenant_public_id,
        "primary_therapist_id": row["primary_therapist_id"],
        "assignment_id": row["assignment_id"], "status": row["status"],
        "service_scope_tags": tuple(row["service_scope_tags"]),
        "created_at": row["created_at"], "version": row["version"],
    }


def _proxy(row) -> dict:
    return {
        "grant_id": row["grant_id"], "principal_member_id": row["principal_member_id"],
        "proxy_member_id": row["proxy_member_id"],
        "permission_codes": tuple(row["permission_codes"]),
        "authorization_document_version_id": row["authorization_document_version_id"],
        "status": row["status"], "valid_from": row["valid_from"],
        "valid_until": row["valid_until"], "version": row["version"],
    }


def _purposes(document_type: str) -> tuple[str, ...]:
    return {
        "USER_AGREEMENT": ("ACCOUNT_AND_SERVICE_ONBOARDING",),
        "PRIVACY_POLICY": ("ACCOUNT_AND_SERVICE_ONBOARDING",),
        "HEALTH_DATA_PROCESSING": ("HEALTH_DATA_PROCESSING",),
        "INSTITUTION_SERVICE": ("CARE_SERVICE_DELIVERY",),
        "NON_MEDICAL_RISK": ("CARE_SERVICE_DELIVERY",),
        "PROXY_AUTHORIZATION": ("FAMILY_PROXY_AUTHORIZATION",),
    }[document_type]


def _review(row) -> dict:
    return {
        "review_id": row["verification_id"], "enrollment_id": row["enrollment_id"],
        "member_id": row["member_id"], "mode": row["mode"], "status": row["status"],
        "id_masked": row["id_masked"], "submitted_at": row["submitted_at"],
        "version": row["version"],
    }


def _review_detail(row) -> dict:
    return {
        **_review(row), "current_revision_id": row["current_revision_id"],
        "current_revision_no": row["current_revision_no"],
        "institution_attestation": row["institution_attestation"],
        "correction_fields": tuple(row["correction_fields"] or ()),
        "proxy_witness_status": row["proxy_witness_status"],
    }


def _document(row, renditions) -> dict:
    return {
        "document_version_id": row["document_version_id"],
        "document_type": row["document_type"], "semantic_version": row["semantic_version"],
        "status": row["status"], "requires_reconsent": row["requires_reconsent"],
        "effective_at": row["effective_at"], "retired_at": row["retired_at"],
        "renditions": tuple({
            "rendition_id": item["rendition_id"], "locale": item["locale"],
            "title": item["title"], "content_sha256": item["content_sha256"],
        } for item in renditions), "version": row["version"],
    }


async def _current_reviewer(authority, actor: CurrentUser):
    _require_platform(actor)
    await authority.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": 6052316115572200000 + actor.id},
    )
    result = await authority.execute(text(
        "SELECT id,role,status,tenant_id,password_hash,1::bigint AS version,updated_at FROM public.user "
        "WHERE id=:user_id FOR SHARE"
    ), {"user_id": actor.id})
    row = result.mappings().one_or_none()
    if row is None or row["role"] != "super_admin" or row["status"] != "active" or row["tenant_id"] is not None:
        raise _error("REVIEWER_CURRENTNESS_FORBIDDEN")
    return row


async def _platform_tenant(
    member_reader, institution_authority, enrollment_id: UUID
) -> tuple[int, UUID]:
    rows = await _safe(
        MemberEnrollmentRepository(member_reader).safe_view_rows(
            "slice3_family_enrollment_read_v1",
            predicates={"enrollment_id": enrollment_id},
            order="enrollment_id",
            limit=2,
        )
    )
    if len(rows) != 1:
        raise _error("DEPENDENCY_UNAVAILABLE")
    try:
        tenant_public_id = UUID(str(rows[0]["tenant_public_id"]))
    except (KeyError, TypeError, ValueError, AttributeError):
        raise _error("DEPENDENCY_UNAVAILABLE") from None
    return await _approved_institution(
        institution_authority,
        tenant_public_id=tenant_public_id,
        error_code="DEPENDENCY_UNAVAILABLE",
    )


async def _therapist_current(authority, actor: CurrentUser):
    if actor.role != "therapist" or actor.tenant_id is None: raise _error("THERAPIST_CURRENTNESS_FORBIDDEN")
    result=await authority.execute(text('SELECT p.therapist_id,p.tenant_id,p.status,u.role,u.status AS user_status,u.tenant_id AS user_tenant_id FROM public.therapist_profile p JOIN public."user" u ON u.id=p.user_id WHERE u.id=:user_id FOR SHARE OF p,u'),{"user_id":actor.id}); row=result.mappings().one_or_none()
    if row is None or row["status"]!="APPROVED_ACTIVE" or row["role"]!="therapist" or row["user_status"]!="active" or row["tenant_id"]!=actor.tenant_id or row["user_tenant_id"]!=actor.tenant_id: raise _error("THERAPIST_CURRENTNESS_FORBIDDEN")
    return row


@institution_router.post("/member-invitations", response_model=InvitationSecretDTO, status_code=201)
async def create_invitation(payload: CreateMemberInvitationRequest, request: Request, key: IdempotencyKey, actor: CurrentUser=Depends(get_current_user_from_jwt), session=Depends(get_member_enrollment_writer_session), authority=Depends(get_db_session), institution_authority=Depends(get_institution_onboarding_reader_session)):
    tenant_id, public_id = await _current_institution(authority, institution_authority, actor); context = _context(request, actor, tenant_id, public_id, key)
    request_value = _request_value(payload); target, secrets, replay = await _begin_mutation(session, context, "INVITATION_CREATE", request_value)
    if replay is not None: return replay
    invitation_id, short_code = await _safe(_service(session).create_invitation(context, payload))
    row = await MemberEnrollmentRepository(session).invitation_for_update(invitation_id)
    result = _invitation(row, public_id, short_code=short_code)
    return await _finish_mutation(session, kind="enrollment_writer", context=context, operation="INVITATION_CREATE", target_id=target, request_value=request_value, result=result, secrets=secrets)


@institution_router.get("/member-invitations", response_model=InvitationPageDTO)
async def list_invitations(status: str|None=None, cursor: str|None=None, limit: int=Query(50,ge=1,le=100), actor: CurrentUser=Depends(get_current_user_from_jwt), session=Depends(get_member_enrollment_reader_session), authority=Depends(get_db_session), institution_authority=Depends(get_institution_onboarding_reader_session)):
    tenant_id, public_id = await _current_institution(authority, institution_authority, actor)
    predicates = {"tenant_public_id": public_id}
    if status is not None: predicates["status"] = status
    return await _safe(_view_page(session, "slice3_institution_enrollment_read_v1", predicates, "invitation_id", limit, cursor=cursor))


@institution_router.post("/member-invitations/{invitation_id}/resend", response_model=InvitationSecretDTO)
async def resend_invitation(invitation_id: UuidV7, payload: VersionRequest, request:Request, key: IdempotencyKey, actor: CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_enrollment_writer_session),authority=Depends(get_db_session),institution_authority=Depends(get_institution_onboarding_reader_session)):
    tenant_id,public_id=await _current_institution(authority,institution_authority,actor); context=_context(request,actor,tenant_id,public_id,key); request_value=_request_value(payload,invitation_id)
    target,secrets,replay=await _begin_mutation(session,context,"INVITATION_RESEND",request_value,target_id=invitation_id)
    if replay is not None: return replay
    _,code=await _safe(_service(session).resend_invitation(context,invitation_id,expected_version=payload.expected_version))
    row=await MemberEnrollmentRepository(session).invitation_for_update(invitation_id); result=_invitation(row,public_id,short_code=code)
    return await _finish_mutation(session,kind="enrollment_writer",context=context,operation="INVITATION_RESEND",target_id=target,request_value=request_value,result=result,secrets=secrets)


@institution_router.post("/member-invitations/{invitation_id}/revoke", response_model=InvitationDTO)
async def revoke_invitation(invitation_id: UuidV7, payload: InvitationRevokeRequest, request:Request,key: IdempotencyKey, actor: CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_enrollment_writer_session),authority=Depends(get_db_session),institution_authority=Depends(get_institution_onboarding_reader_session)):
    tenant_id,public_id=await _current_institution(authority,institution_authority,actor); context=_context(request,actor,tenant_id,public_id,key); request_value=_request_value(payload,invitation_id)
    target,secrets,replay=await _begin_mutation(session,context,"INVITATION_REVOKE",request_value,target_id=invitation_id)
    if replay is not None: return replay
    await _safe(_service(session).revoke_invitation(context,invitation_id,payload)); row=await MemberEnrollmentRepository(session).invitation_for_update(invitation_id); result=_invitation(row,public_id)
    return await _finish_mutation(session,kind="enrollment_writer",context=context,operation="INVITATION_REVOKE",target_id=target,request_value=request_value,result=result,secrets=secrets)


@institution_router.get("/member-enrollments", response_model=EnrollmentSummaryPageDTO)
async def institution_enrollments(status: str|None=None,cursor:str|None=None,limit:int=Query(50,ge=1,le=100),actor:CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_enrollment_reader_session),authority=Depends(get_db_session),institution_authority=Depends(get_institution_onboarding_reader_session)):
    tenant_id,public_id=await _current_institution(authority,institution_authority,actor); predicates={"tenant_public_id":public_id}
    if status is not None: predicates["status"]=status
    page=await _safe(_view_page(session,"slice3_family_enrollment_read_v1",predicates,"enrollment_id",limit,cursor=cursor))
    page["items"]=tuple({k:v for k,v in item.items() if k in {"enrollment_id","mode","status","accepted_at","identity_verified_at","case_created_at","version"}} for item in page["items"]); return page


@institution_router.get("/member-enrollments/{enrollment_id}", response_model=EnrollmentDetailDTO)
async def institution_enrollment(enrollment_id: UuidV7,actor:CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_enrollment_reader_session),authority=Depends(get_db_session),institution_authority=Depends(get_institution_onboarding_reader_session)):
    tenant_id,public_id=await _current_institution(authority,institution_authority,actor); rows=await _safe(MemberEnrollmentRepository(session).safe_view_rows("slice3_family_enrollment_read_v1",predicates={"tenant_public_id":public_id,"enrollment_id":enrollment_id},order="enrollment_id",limit=2))
    if len(rows)!=1: raise _error("ENROLLMENT_NOT_FOUND")
    return _enrollment_detail(rows[0])


@institution_router.post("/member-enrollments/{enrollment_id}/identity-check", response_model=IdentityStatusDTO)
async def institution_identity_check(enrollment_id: UuidV7,payload:InstitutionIdentityCheckRequest,request:Request,key:IdempotencyKey,actor:CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_enrollment_writer_session),authority=Depends(get_db_session),institution_authority=Depends(get_institution_onboarding_reader_session)):
    tenant_id,public_id=await _current_institution(authority,institution_authority,actor); context=_context(request,actor,tenant_id,public_id,key); repo=MemberEnrollmentRepository(session); enrollment=await repo.enrollment_for_update(enrollment_id)
    if enrollment is None or enrollment["tenant_id"]!=tenant_id: raise _error("ENROLLMENT_NOT_FOUND")
    verification=await repo.verification_by_enrollment_for_update(enrollment_id)
    if verification is None: raise _error("IDENTITY_REVIEW_NOT_FOUND")
    target_id=verification["verification_id"]; request_value=_request_value(payload,target_id)
    target,secrets,replay=await _begin_mutation(session,context,"INSTITUTION_IDENTITY_CHECK",request_value,target_id=target_id)
    if replay is not None: return replay
    await _safe(_service(session).institution_identity_check(context,target_id,payload)); row=await repo.verification_for_update(target_id); revision=await repo.current_identity_revision(row["verification_id"],row["current_revision_id"]); result=_identity(row,revision)
    return await _finish_mutation(session,kind="enrollment_writer",context=context,operation="INSTITUTION_IDENTITY_CHECK",target_id=target,request_value=request_value,result=result,secrets=secrets)


@institution_router.post("/member-enrollments/{enrollment_id}/primary-assignments", response_model=AssignmentDTO,status_code=201)
async def create_assignment(enrollment_id:UuidV7,payload:CreateAssignmentRequest,request:Request,key:IdempotencyKey,actor:CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_enrollment_writer_session),authority=Depends(get_db_session),institution_authority=Depends(get_institution_onboarding_reader_session)):
    tenant_id,public_id=await _current_institution(authority,institution_authority,actor); context=_context(request,actor,tenant_id,public_id,key); request_value=_request_value(payload,enrollment_id)
    target,secrets,replay=await _begin_mutation(session,context,"ASSIGNMENT_CREATE",request_value,target_id=enrollment_id)
    if replay is not None: return replay
    assignment_id=await _safe(_service(session).create_assignment(context,enrollment_id,payload)); row=await MemberEnrollmentRepository(session).assignment_for_update(assignment_id); result=_assignment(row,public_id)
    return await _finish_mutation(session,kind="enrollment_writer",context=context,operation="ASSIGNMENT_CREATE",target_id=target,request_value=request_value,result=result,secrets=secrets)


@institution_router.post("/primary-assignments/{assignment_id}/cancel", response_model=AssignmentDTO)
async def cancel_assignment(assignment_id:UuidV7,payload:AssignmentCancelRequest,request:Request,key:IdempotencyKey,actor:CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_enrollment_writer_session),authority=Depends(get_db_session),institution_authority=Depends(get_institution_onboarding_reader_session)):
    tenant_id,public_id=await _current_institution(authority,institution_authority,actor); context=_context(request,actor,tenant_id,public_id,key); request_value=_request_value(payload,assignment_id)
    target,secrets,replay=await _begin_mutation(session,context,"ASSIGNMENT_CANCEL",request_value,target_id=assignment_id)
    if replay is not None: return replay
    await _safe(_service(session).decide_assignment(context,assignment_id,payload,target="CANCELLED")); row=await MemberEnrollmentRepository(session).assignment_for_update(assignment_id); result=_assignment(row,public_id)
    return await _finish_mutation(session,kind="enrollment_writer",context=context,operation="ASSIGNMENT_CANCEL",target_id=target,request_value=request_value,result=result,secrets=secrets)


@institution_router.get("/service-cases/{case_id}", response_model=PreparingCaseDTO)
async def institution_case(case_id:UuidV7,actor:CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_enrollment_reader_session),authority=Depends(get_db_session),institution_authority=Depends(get_institution_onboarding_reader_session)):
    tenant_id,public_id=await _current_institution(authority,institution_authority,actor); rows=await _safe(MemberEnrollmentRepository(session).safe_view_rows("slice3_service_case_read_v1",predicates={"tenant_public_id":public_id,"case_id":case_id},order="case_id",limit=2))
    if len(rows)!=1: raise _error("CASE_NOT_FOUND")
    return _public_row(rows[0])


@family_router.post("/member-enrollments/accept", response_model=EnrollmentDTO,status_code=201)
async def accept_enrollment(payload:AcceptEnrollmentRequest,request:Request,key:IdempotencyKey,actor:CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_enrollment_writer_session),authority=Depends(get_db_session),institution_authority=Depends(get_institution_onboarding_reader_session)):
    member_id=await _member_for_actor(authority,actor,expected_phone=payload.phone)
    repo=MemberEnrollmentRepository(session); invitation=await repo.invitation_for_update(payload.invitation_id)
    if invitation is None: raise _error("INVITATION_NOT_FOUND")
    public_id=await _tenant_public_id(institution_authority,invitation["tenant_id"]); context=_context(request,actor,invitation["tenant_id"],public_id,key); request_value=_request_value(payload,payload.invitation_id)
    target,secrets,replay=await _begin_mutation(session,context,"ENROLLMENT_ACCEPT",request_value,target_id=payload.invitation_id)
    if replay is not None: return replay
    adult=None
    if invitation["mode"]=="PROXY_ELDER":
        try:
            evidence=await verified_adult_eligibility_for_update(
                authority,user_id=actor.id,member_id=member_id,
                as_of_date=_service(session).now().date(),
            )
            adult=evidence.eligible
        except ValueError:
            raise _error("PROXY_ADULT_IDENTITY_REQUIRED") from None
    try:
        enrollment_id,_=await _service(session).accept_invitation(
            context,payload,actor_member_id=member_id,adult_eligible=adult
        )
    except InvitationAttemptRejected as error:
        code=safe_error_code(error)
        await _finish_mutation(
            session,kind="enrollment_writer",context=context,
            operation="ENROLLMENT_ACCEPT",target_id=target,
            request_value=request_value,result={"error_code":code},secrets=secrets,
        )
        raise _error(code) from None
    except Exception as error:
        raise _error(safe_error_code(error)) from None
    row=await repo.enrollment_for_update(enrollment_id); result=_enrollment(row,public_id)
    return await _finish_mutation(session,kind="enrollment_writer",context=context,operation="ENROLLMENT_ACCEPT",target_id=target,request_value=request_value,result=result,secrets=secrets)


@family_router.get("/member-enrollments", response_model=EnrollmentPageDTO)
async def family_enrollments(cursor:str|None=None,limit:int=Query(50,ge=1,le=100),actor:CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_enrollment_reader_session),authority=Depends(get_db_session)):
    member_id=await _member_for_actor(authority,actor); rows=await _safe(MemberEnrollmentRepository(session).family_enrollment_rows(member_id,cursor_id=_cursor_id(cursor),limit=limit+1)); return {"items":tuple(_public_row(row) for row in rows[:limit]),"next_cursor":str(rows[limit]["enrollment_id"]) if len(rows)>limit else None}


@family_router.get("/member-enrollments/{enrollment_id}", response_model=EnrollmentDetailDTO)
async def family_enrollment(enrollment_id:UuidV7,actor:CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_enrollment_reader_session),authority=Depends(get_db_session)):
    member_id=await _member_for_actor(authority,actor); row=await _safe(MemberEnrollmentRepository(session).family_enrollment_detail(member_id,enrollment_id,limit=2))
    if row is None: raise _error("ENROLLMENT_NOT_FOUND")
    return _enrollment_detail(row)


@family_router.put("/member-enrollments/{enrollment_id}/identity-submission", response_model=IdentityStatusDTO)
async def identity_submission(enrollment_id:UuidV7,payload:IdentitySubmissionRequest,request:Request,key:IdempotencyKey,actor:CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_enrollment_writer_session),authority=Depends(get_db_session),institution_authority=Depends(get_institution_onboarding_reader_session)):
    repo=MemberEnrollmentRepository(session); enrollment=await repo.enrollment_for_update(enrollment_id)
    if enrollment is None: raise _error("ENROLLMENT_NOT_FOUND")
    member_id=await _member_for_actor(authority,actor)
    await _safe(_service(session).require_proxy_permission(enrollment,member_id,"IDENTITY_SUBMIT"))
    public_id=await _tenant_public_id(institution_authority,enrollment["tenant_id"]); context=_context(request,actor,enrollment["tenant_id"],public_id,key); request_value=_request_value(payload,enrollment_id); target,secrets,replay=await _begin_mutation(session,context,"IDENTITY_SUBMIT",request_value,target_id=enrollment_id)
    if replay is not None: return replay
    verification_id,revision_id=await _safe(_service(session).submit_identity(context,enrollment_id,payload,submitted_by_member_id=member_id)); row=await repo.verification_for_update(verification_id); revision=await repo.current_identity_revision(verification_id,revision_id); result=_identity(row,revision)
    return await _finish_mutation(session,kind="enrollment_writer",context=context,operation="IDENTITY_SUBMIT",target_id=target,request_value=request_value,result=result,secrets=secrets)


@family_router.post("/member-enrollments/{enrollment_id}/identity-resubmit", response_model=IdentityStatusDTO)
async def identity_resubmit(enrollment_id:UuidV7,payload:IdentityResubmitRequest,request:Request,key:IdempotencyKey,actor:CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_enrollment_writer_session),authority=Depends(get_db_session),institution_authority=Depends(get_institution_onboarding_reader_session)):
    repo=MemberEnrollmentRepository(session); enrollment=await repo.enrollment_for_update(enrollment_id)
    if enrollment is None: raise _error("ENROLLMENT_NOT_FOUND")
    member_id=await _member_for_actor(authority,actor)
    await _safe(_service(session).require_proxy_permission(enrollment,member_id,"IDENTITY_SUBMIT"))
    public_id=await _tenant_public_id(institution_authority,enrollment["tenant_id"]); context=_context(request,actor,enrollment["tenant_id"],public_id,key); request_value=_request_value(payload,enrollment_id); target,secrets,replay=await _begin_mutation(session,context,"IDENTITY_RESUBMIT",request_value,target_id=enrollment_id)
    if replay is not None: return replay
    verification_id,revision_id=await _safe(_service(session).resubmit_identity(context,enrollment_id,payload,submitted_by_member_id=member_id)); row=await repo.verification_for_update(verification_id); revision=await repo.current_identity_revision(verification_id,revision_id); result=_identity(row,revision)
    return await _finish_mutation(session,kind="enrollment_writer",context=context,operation="IDENTITY_RESUBMIT",target_id=target,request_value=request_value,result=result,secrets=secrets)


@family_router.get("/member-enrollments/{enrollment_id}/consent-presentations", response_model=ConsentPresentationListDTO)
async def consent_presentations(enrollment_id:UuidV7,locale:str,document_types:list[str]=Query(),actor:CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_enrollment_writer_session),authority=Depends(get_db_session)):
    member_id=await _member_for_actor(authority,actor); repo=MemberEnrollmentRepository(session); enrollment=await repo.enrollment_for_update(enrollment_id)
    if enrollment is None: raise _error("ENROLLMENT_NOT_FOUND")
    await _safe(_service(session).require_proxy_permission(enrollment,member_id,"CONSENT_ACCEPT"))
    rows=await _safe(repo.current_consent_documents(tuple(document_types),locale)); items=tuple({**dict(row),"purpose_codes":_purposes(row["document_type"])} for row in rows); return {"items":items}


@family_router.post("/member-enrollments/{enrollment_id}/consent-records", response_model=ConsentRecordDTO,status_code=201)
async def consent_record(enrollment_id:UuidV7,payload:RecordConsentRequest,request:Request,key:IdempotencyKey,actor:CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_enrollment_writer_session),authority=Depends(get_db_session),institution_authority=Depends(get_institution_onboarding_reader_session)):
    member_id=await _member_for_actor(authority,actor); repo=MemberEnrollmentRepository(session); enrollment=await repo.enrollment_for_update(enrollment_id)
    if enrollment is None: raise _error("ENROLLMENT_NOT_FOUND")
    await _safe(_service(session).require_proxy_permission(enrollment,member_id,"CONSENT_ACCEPT"))
    public_id=await _tenant_public_id(institution_authority,enrollment["tenant_id"]); docs=await repo.current_consent_documents(("USER_AGREEMENT","PRIVACY_POLICY","HEALTH_DATA_PROCESSING","INSTITUTION_SERVICE","NON_MEDICAL_RISK","PROXY_AUTHORIZATION"),"zh-CN"); match=next((d for d in docs if d["document_version_id"]==payload.document_version_id),None)
    if match is None or tuple(payload.purpose_codes)!=_purposes(match["document_type"]): raise _error("CONSENT_VERSION_STALE")
    context=_context(request,actor,enrollment["tenant_id"],public_id,key); request_value=_request_value(payload,enrollment_id); target,secrets,replay=await _begin_mutation(session,context,"CONSENT_RECORD",request_value,target_id=enrollment_id)
    if replay is not None: return replay
    record_id=await _safe(_service(session).record_consent(context,enrollment_id,payload,subject_member_id=enrollment["subject_member_id"],proxy_member_id=enrollment["proxy_member_id"],document_type=match["document_type"],locale="zh-CN")); row=await repo.consent_with_locale(record_id); result=dict(row)
    return await _finish_mutation(session,kind="enrollment_writer",context=context,operation="CONSENT_RECORD",target_id=target,request_value=request_value,result=result,secrets=secrets)


@family_router.post("/consent-records/{consent_record_id}/withdraw", response_model=ConsentRecordDTO)
async def withdraw_consent(consent_record_id:UuidV7,payload:ConsentWithdrawRequest,request:Request,key:IdempotencyKey,actor:CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_enrollment_writer_session),authority=Depends(get_db_session),institution_authority=Depends(get_institution_onboarding_reader_session)):
    repo=MemberEnrollmentRepository(session); row=await repo.consent_for_update(consent_record_id)
    if row is None: raise _error("CONSENT_RECORD_NOT_FOUND")
    enrollment=await repo.enrollment_for_update(row["enrollment_id"]); member_id=await _member_for_actor(authority,actor)
    await _safe(_service(session).require_proxy_permission(enrollment,member_id,"CONSENT_ACCEPT"))
    public_id=await _tenant_public_id(institution_authority,enrollment["tenant_id"]); context=_context(request,actor,enrollment["tenant_id"],public_id,key); request_value=_request_value(payload,consent_record_id); target,secrets,replay=await _begin_mutation(session,context,"CONSENT_WITHDRAW",request_value,target_id=consent_record_id)
    if replay is not None: return replay
    await _safe(_service(session).withdraw_consent(context,consent_record_id,payload)); result=dict(await repo.consent_with_locale(consent_record_id))
    return await _finish_mutation(session,kind="enrollment_writer",context=context,operation="CONSENT_WITHDRAW",target_id=target,request_value=request_value,result=result,secrets=secrets)


@family_router.post("/proxy-grants/{grant_id}/revoke", response_model=ProxyGrantDTO)
async def revoke_proxy(grant_id:UuidV7,payload:ProxyRevokeRequest,request:Request,key:IdempotencyKey,actor:CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_enrollment_writer_session),authority=Depends(get_db_session),institution_authority=Depends(get_institution_onboarding_reader_session)):
    repo=MemberEnrollmentRepository(session); row=await repo.proxy_grant_for_update(grant_id)
    if row is None: raise _error("PROXY_GRANT_NOT_FOUND")
    member_id=await _member_for_actor(authority,actor)
    if member_id not in {row["principal_member_id"],row["proxy_member_id"]}: raise _error("PROXY_PERMISSION_FORBIDDEN")
    enrollment=await repo.enrollment_for_update(row["enrollment_id"]); public_id=await _tenant_public_id(institution_authority,enrollment["tenant_id"]); context=_context(request,actor,enrollment["tenant_id"],public_id,key); request_value=_request_value(payload,grant_id); target,secrets,replay=await _begin_mutation(session,context,"PROXY_REVOKE",request_value,target_id=grant_id)
    if replay is not None: return replay
    await _safe(_service(session).revoke_proxy(context,grant_id,payload)); result=_proxy(await repo.proxy_grant_for_update(grant_id))
    return await _finish_mutation(session,kind="enrollment_writer",context=context,operation="PROXY_REVOKE",target_id=target,request_value=request_value,result=result,secrets=secrets)


@platform_router.get("/member-identity-reviews", response_model=IdentityReviewPageDTO)
async def identity_reviews(status:str|None=None,cursor:str|None=None,limit:int=Query(50,ge=1,le=100),actor:CurrentUser=Depends(get_current_user_from_jwt),authority=Depends(get_db_session)):
    await _current_reviewer(authority,actor)
    async with (await get_slice3_session_factory("identity_review_writer"))() as session:
        predicates={}
        if status is not None: predicates["status"]=status
        rows=await _safe(MemberEnrollmentRepository(session).safe_view_rows("slice3_platform_identity_review_read_v1",predicates=predicates,order="verification_id",cursor_id=_cursor_id(cursor),limit=limit+1)); return {"items":tuple(_review(row) for row in rows[:limit]),"next_cursor":str(rows[limit]["verification_id"]) if len(rows)>limit else None}


@platform_router.get("/member-identity-reviews/{review_id}", response_model=IdentityReviewDetailDTO)
async def identity_review(review_id:UuidV7,actor:CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_identity_review_writer_session),authority=Depends(get_db_session)):
    await _current_reviewer(authority,actor); rows=await _safe(MemberEnrollmentRepository(session).safe_view_rows("slice3_platform_identity_review_read_v1",predicates={"verification_id":review_id},order="verification_id",limit=2))
    if len(rows)!=1: raise _error("IDENTITY_REVIEW_NOT_FOUND")
    return _review_detail(rows[0])


@platform_router.post("/member-identity-reviews/{review_id}/claim", response_model=IdentityReviewDetailDTO)
async def claim_review(review_id:UuidV7,payload:VersionRequest,request:Request,key:IdempotencyKey,actor:CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_identity_review_writer_session),authority=Depends(get_db_session),member_reader=Depends(get_member_enrollment_reader_session),institution_authority=Depends(get_institution_onboarding_reader_session)):
    reviewer=await _current_reviewer(authority,actor); rows=await _safe(MemberEnrollmentRepository(session).safe_view_rows("slice3_platform_identity_review_read_v1",predicates={"verification_id":review_id},order="verification_id",limit=2))
    if len(rows)!=1: raise _error("IDENTITY_REVIEW_NOT_FOUND")
    tenant_id,tenant_public_id=await _platform_tenant(member_reader,institution_authority,rows[0]["enrollment_id"]); context=_context(request,actor,tenant_id,tenant_public_id,key,platform_scope=True); request_value=_request_value(payload,review_id); target,secrets,replay=await _begin_mutation(session,context,"IDENTITY_REVIEW_CLAIM",request_value,target_id=review_id)
    if replay is not None: return replay
    await _safe(_service(session).claim_identity_review(context,review_id,expected_version=payload.expected_version)); updated=await MemberEnrollmentRepository(session).safe_view_rows("slice3_platform_identity_review_read_v1",predicates={"verification_id":review_id},order="verification_id",limit=2); result=_review_detail(updated[0])
    return await _finish_mutation(session,kind="identity_review_writer",context=context,operation="IDENTITY_REVIEW_CLAIM",target_id=target,request_value=request_value,result=result,secrets=secrets)


@platform_router.post("/member-identity-reviews/{review_id}/pii-access", response_model=IdentityPiiDTO)
async def pii_access(review_id:UuidV7,payload:PiiAccessRequest,request:Request,response:Response,key:IdempotencyKey,actor:CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_identity_review_writer_session),authority=Depends(get_db_session),member_reader=Depends(get_member_enrollment_reader_session),institution_authority=Depends(get_institution_onboarding_reader_session)):
    reviewer=await _current_reviewer(authority,actor)
    rows=await _safe(MemberEnrollmentRepository(session).safe_view_rows("slice3_platform_identity_review_read_v1",predicates={"verification_id":review_id},order="verification_id",limit=2))
    if len(rows)!=1: raise _error("IDENTITY_REVIEW_NOT_FOUND")
    tenant_id,tenant_public_id=await _platform_tenant(member_reader,institution_authority,rows[0]["enrollment_id"])
    context=_context(request,actor,tenant_id,tenant_public_id,key,platform_scope=True); request_value=_request_value({"reason_code":payload.reason_code},review_id)
    proof_secrets=MemberEnrollmentSecrets(); request_digest=proof_secrets.request_digest(request_value)
    currentness=proof_secrets.audit_digest({"id":reviewer["id"],"role":reviewer["role"],"status":reviewer["status"],"tenant_id":reviewer["tenant_id"],"version":reviewer["version"],"updated_at":reviewer["updated_at"]})
    access_token_digest=proof_secrets.request_digest({"authorization":request.headers.get("authorization",""),"reviewer_user_id":actor.id})
    password_valid=verify_password(payload.current_password,reviewer["password_hash"])
    proof_issued_at=datetime.now(timezone.utc); proof_expires_at=proof_issued_at+timedelta(seconds=15)
    proof_values={"proof_version":1,"reviewer_user_id":actor.id,"user_version":reviewer["version"],"user_updated_at":reviewer["updated_at"],"verification_id":review_id,"current_revision_id":rows[0]["current_revision_id"],"actor_scope":context.actor_scope,"idempotency_key":context.idempotency_key,"request_id":context.request_id,"request_digest":request_digest,"access_token_digest":access_token_digest,"currentness_digest":currentness,"reason_code":payload.reason_code,"password_valid":password_valid,"proof_issued_at":proof_issued_at,"proof_expires_at":proof_expires_at}
    credential_proof_digest=reviewer_credential_proof(reviewer["password_hash"],proof_values)
    await authority.rollback()
    target,secrets,replay=await _begin_mutation(session,context,"IDENTITY_PII_ACCESS",request_value,target_id=review_id)
    if replay is not None: response.headers["Cache-Control"]="no-store"; return replay
    result=await _safe(_service(session).access_identity_pii(context,review_id,payload,currentness_digest=currentness,access_token_digest=access_token_digest,password_valid=password_valid,request_digest=request_digest,proof_values=proof_values,credential_proof_digest=credential_proof_digest))
    result=await _finish_mutation(session,kind="identity_review_writer",context=context,operation="IDENTITY_PII_ACCESS",target_id=target,request_value=request_value,result=result,secrets=secrets)
    if type(result) is dict and type(result.get("error_code")) is str: raise _error(result["error_code"])
    response.headers["Cache-Control"]="no-store"; return result


@platform_router.post("/member-identity-reviews/{review_id}/decision", response_model=IdentityStatusDTO)
async def platform_decision(review_id:UuidV7,payload:PlatformIdentityDecisionRequest,request:Request,key:IdempotencyKey,actor:CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_identity_review_writer_session),authority=Depends(get_db_session),member_reader=Depends(get_member_enrollment_reader_session),institution_authority=Depends(get_institution_onboarding_reader_session)):
    reviewer=await _current_reviewer(authority,actor); rows=await _safe(MemberEnrollmentRepository(session).safe_view_rows("slice3_platform_identity_review_read_v1",predicates={"verification_id":review_id},order="verification_id",limit=2))
    if len(rows)!=1: raise _error("IDENTITY_REVIEW_NOT_FOUND")
    tenant_id,tenant_public_id=await _platform_tenant(member_reader,institution_authority,rows[0]["enrollment_id"]); context=_context(request,actor,tenant_id,tenant_public_id,key,platform_scope=True); request_value=_request_value(payload,review_id); target,secrets,replay=await _begin_mutation(session,context,"IDENTITY_REVIEW_DECIDE",request_value,target_id=review_id)
    if replay is not None: return replay
    currentness=secrets.audit_digest({"id":reviewer["id"],"role":reviewer["role"],"status":reviewer["status"],"tenant_id":reviewer["tenant_id"],"version":reviewer["version"]}); access_token_digest=secrets.request_digest({"authorization":request.headers.get("authorization",""),"reviewer_user_id":actor.id})
    await _safe(_service(session).platform_identity_decide(context,review_id,payload,source_member_id=rows[0]["member_id"],enrollment_mode=rows[0]["mode"],access_token_digest=access_token_digest,currentness_digest=currentness)); repo=MemberEnrollmentRepository(session); row=await repo.verification_for_update(review_id); revision=await repo.current_identity_revision(review_id,row["current_revision_id"]); result=_identity(row,revision)
    return await _finish_mutation(session,kind="identity_review_writer",context=context,operation="IDENTITY_REVIEW_DECIDE",target_id=target,request_value=request_value,result=result,secrets=secrets)


@platform_router.post("/consent-documents", response_model=ConsentDocumentDTO,status_code=201)
async def create_document(payload:CreateConsentDocumentRequest,request:Request,key:IdempotencyKey,actor:CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_identity_review_writer_session),authority=Depends(get_db_session)):
    await _current_reviewer(authority,actor); context=_context(request,actor,None,Uuid7Generator().generate(),key,platform_scope=True); request_value=_request_value(payload); target,secrets,replay=await _begin_mutation(session,context,"CONSENT_DOCUMENT_CREATE",request_value)
    if replay is not None: return replay
    document_id=await _safe(_service(session).create_consent_document(context,payload)); repo=MemberEnrollmentRepository(session); row=await repo.document_for_update(document_id); result=_document(row,await repo.document_renditions(document_id))
    return await _finish_mutation(session,kind="identity_review_writer",context=context,operation="CONSENT_DOCUMENT_CREATE",target_id=target,request_value=request_value,result=result,secrets=secrets)


@platform_router.post("/consent-documents/{document_version_id}/publish", response_model=ConsentDocumentDTO)
async def publish_document(document_version_id:UuidV7,payload:PublishConsentDocumentRequest,request:Request,key:IdempotencyKey,actor:CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_identity_review_writer_session),authority=Depends(get_db_session)):
    await _current_reviewer(authority,actor); context=_context(request,actor,None,Uuid7Generator().generate(),key,platform_scope=True); request_value=_request_value(payload,document_version_id); target,secrets,replay=await _begin_mutation(session,context,"CONSENT_DOCUMENT_PUBLISH",request_value,target_id=document_version_id)
    if replay is not None: return replay
    await _safe(_service(session).publish_consent_document(context,document_version_id,payload)); repo=MemberEnrollmentRepository(session); row=await repo.document_for_update(document_version_id); result=_document(row,await repo.document_renditions(document_version_id))
    return await _finish_mutation(session,kind="identity_review_writer",context=context,operation="CONSENT_DOCUMENT_PUBLISH",target_id=target,request_value=request_value,result=result,secrets=secrets)


@platform_router.post("/consent-documents/{document_version_id}/retire", response_model=ConsentDocumentDTO)
async def retire_document(document_version_id:UuidV7,payload:ConsentRetireRequest,request:Request,key:IdempotencyKey,actor:CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_identity_review_writer_session),authority=Depends(get_db_session)):
    await _current_reviewer(authority,actor); context=_context(request,actor,None,Uuid7Generator().generate(),key,platform_scope=True); request_value=_request_value(payload,document_version_id); target,secrets,replay=await _begin_mutation(session,context,"CONSENT_DOCUMENT_RETIRE",request_value,target_id=document_version_id)
    if replay is not None: return replay
    await _safe(_service(session).retire_consent_document(context,document_version_id,payload)); repo=MemberEnrollmentRepository(session); row=await repo.document_for_update(document_version_id); result=_document(row,await repo.document_renditions(document_version_id))
    return await _finish_mutation(session,kind="identity_review_writer",context=context,operation="CONSENT_DOCUMENT_RETIRE",target_id=target,request_value=request_value,result=result,secrets=secrets)


@therapist_router.get("/primary-assignments", response_model=AssignmentPageDTO)
async def therapist_assignments(status:str|None=None,cursor:str|None=None,limit:int=Query(50,ge=1,le=100),actor:CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_enrollment_reader_session),authority=Depends(get_db_session)):
    therapist=await _therapist_current(authority,actor); predicates={"therapist_id":therapist["therapist_id"]}
    if status is not None: predicates["status"]=status
    rows=await _safe(MemberEnrollmentRepository(session).safe_view_rows("slice3_therapist_assignment_read_v1",predicates=predicates,order="assignment_id",cursor_id=_cursor_id(cursor),limit=limit+1)); return {"items":tuple(_public_row(row) for row in rows[:limit]),"next_cursor":str(rows[limit]["assignment_id"]) if len(rows)>limit else None}


@therapist_router.get("/primary-assignments/{assignment_id}", response_model=AssignmentDetailDTO, summary="Therapist Assignment")
async def get_primary_therapist_assignment(assignment_id:UuidV7,actor:CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_enrollment_reader_session),authority=Depends(get_db_session)):
    therapist=await _therapist_current(authority,actor); rows=await _safe(MemberEnrollmentRepository(session).safe_view_rows("slice3_therapist_assignment_read_v1",predicates={"therapist_id":therapist["therapist_id"],"assignment_id":assignment_id},order="assignment_id",limit=2))
    if len(rows)!=1: raise _error("ASSIGNMENT_NOT_FOUND")
    return _public_row(rows[0])


@therapist_router.post("/primary-assignments/{assignment_id}/accept", response_model=PreparingCaseDTO,status_code=201)
async def accept_assignment(assignment_id:UuidV7,payload:VersionRequest,request:Request,key:IdempotencyKey,actor:CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_case_writer_session),authority=Depends(get_db_session),institution_authority=Depends(get_institution_onboarding_reader_session)):
    therapist=await _therapist_current(authority,actor); repo=MemberEnrollmentRepository(session); assignment=await repo.assignment_for_update(assignment_id)
    if assignment is None: raise _error("ASSIGNMENT_NOT_FOUND")
    if assignment["therapist_id"]!=therapist["therapist_id"]: raise _error("THERAPIST_SCOPE_FORBIDDEN")
    enrollment=await repo.enrollment_for_update(assignment["enrollment_id"]); public_id=await _tenant_public_id(institution_authority,assignment["tenant_id"]); context=_context(request,actor,assignment["tenant_id"],public_id,key); request_value=_request_value(payload,assignment_id); target,secrets,replay=await _begin_mutation(session,context,"ASSIGNMENT_ACCEPT",request_value,target_id=assignment_id)
    if replay is not None: return replay
    required=("USER_AGREEMENT","PRIVACY_POLICY","HEALTH_DATA_PROCESSING","INSTITUTION_SERVICE","NON_MEDICAL_RISK") + (("PROXY_AUTHORIZATION",) if enrollment["mode"]=="PROXY_ELDER" else ())
    case_id=await _safe(_service(session).accept_assignment(context,assignment_id,expected_version=payload.expected_version,required_document_types=required)); row=await repo.service_case_for_update(case_id); result=_case(row,public_id)
    return await _finish_mutation(session,kind="case_writer",context=context,operation="ASSIGNMENT_ACCEPT",target_id=target,request_value=request_value,result=result,secrets=secrets)


@therapist_router.post("/primary-assignments/{assignment_id}/decline", response_model=AssignmentDTO)
async def decline_assignment(assignment_id:UuidV7,payload:AssignmentDeclineRequest,request:Request,key:IdempotencyKey,actor:CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_case_writer_session),authority=Depends(get_db_session),institution_authority=Depends(get_institution_onboarding_reader_session)):
    therapist=await _therapist_current(authority,actor); repo=MemberEnrollmentRepository(session); row=await repo.assignment_for_update(assignment_id)
    if row is None: raise _error("ASSIGNMENT_NOT_FOUND")
    if row["therapist_id"]!=therapist["therapist_id"]: raise _error("THERAPIST_SCOPE_FORBIDDEN")
    public_id=await _tenant_public_id(institution_authority,row["tenant_id"]); context=_context(request,actor,row["tenant_id"],public_id,key); request_value=_request_value(payload,assignment_id); target,secrets,replay=await _begin_mutation(session,context,"ASSIGNMENT_DECLINE",request_value,target_id=assignment_id)
    if replay is not None: return replay
    await _safe(_service(session).decide_assignment(context,assignment_id,payload,target="DECLINED")); updated=await repo.assignment_for_update(assignment_id); result=_assignment(updated,public_id)
    return await _finish_mutation(session,kind="case_writer",context=context,operation="ASSIGNMENT_DECLINE",target_id=target,request_value=request_value,result=result,secrets=secrets)


@therapist_router.get("/service-cases/{case_id}", response_model=PreparingCaseDTO)
async def therapist_case(case_id:UuidV7,actor:CurrentUser=Depends(get_current_user_from_jwt),session=Depends(get_member_enrollment_reader_session),authority=Depends(get_db_session)):
    therapist=await _therapist_current(authority,actor); rows=await _safe(MemberEnrollmentRepository(session).safe_view_rows("slice3_service_case_read_v1",predicates={"primary_therapist_id":therapist["therapist_id"],"case_id":case_id},order="case_id",limit=2))
    if len(rows)!=1: raise _error("CASE_NOT_FOUND")
    return _public_row(rows[0])
