from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from app.core.database import get_db_session, get_institution_onboarding_reader_session, get_institution_onboarding_writer_session, get_institution_review_writer_session
from app.core.responses import ok_response
from app.core.security import CurrentUser, get_current_user_from_jwt
from app.core.接口合同 import error_response
from app.modules.institution_onboarding.repository import InstitutionOnboardingRepository
from app.modules.institution_onboarding.schemas import (
    ActivationDTO, ActivationRequest, ApplicationCorrectionDTO, ApplicationDTO,
    ApplicationDraftRequest, ApplicationResubmitRequest, ApplicationSubmitRequest,
    InvitationCreate, InvitationIssuedDTO, InvitationResendRequest,
    InvitationRevokeRequest, InvitationRevokedDTO, InvitationSummaryDTO, ReviewDecisionRequest,
    OnboardingSuccessEnvelope, ReviewDetailDTO, ReviewQueueItemDTO,
)
from app.modules.institution_onboarding.service import (
    activate, create_invitation, get_application, resubmit_application,
    require_current_reviewer, resend_invitation, review_decision,
    review_draft_projection, revoke_invitation, save_draft, submit_application,
)


_ONBOARDING_CODES = frozenset({
    "ONBOARDING_ADMINISTRATIVE_REGION_INVALID", "ONBOARDING_APPLICATION_NOT_FOUND",
    "ONBOARDING_APPLICATION_STATE_CONFLICT", "ONBOARDING_COMMIT_OUTCOME_UNKNOWN",
    "ONBOARDING_COMMIT_ROLLED_BACK", "ONBOARDING_CORRECTION_FIELD_FORBIDDEN",
    "ONBOARDING_DRAFT_INCOMPLETE", "ONBOARDING_FROZEN_FIELD_MUTATION",
    "ONBOARDING_IDEMPOTENCY_CONFLICT", "ONBOARDING_INSTITUTION_TYPE_INVALID",
    "ONBOARDING_INVITATION_ATTEMPT_LIMIT", "ONBOARDING_INVITATION_CODE_MISMATCH",
    "ONBOARDING_INVITATION_EXPIRED", "ONBOARDING_INVITATION_NOT_FOUND",
    "ONBOARDING_INVITATION_PHONE_MISMATCH", "ONBOARDING_INVITATION_STATE_CONFLICT",
    "ONBOARDING_INVITATION_VERSION_CONFLICT", "ONBOARDING_PERSISTENCE_UNAVAILABLE",
    "ONBOARDING_PII_DECRYPTION_UNAVAILABLE", "ONBOARDING_PLATFORM_ROLE_REQUIRED",
    "ONBOARDING_REQUIRED_CLEAN_FILES_MISSING", "ONBOARDING_REQUIRED_LICENSE_MISSING",
    "ONBOARDING_REVIEW_CURRENTNESS_REQUIRED", "ONBOARDING_REVIEW_ROLE_REQUIRED",
    "ONBOARDING_TOTP_INVALID", "ONBOARDING_VERSION_CONFLICT",
    "PRIVATE_FILE_BIND_CONFLICT", "PRIVATE_FILE_NOT_CLEAN", "PRIVATE_FILE_PURPOSE_MISMATCH",
})
_ONBOARDING_DEFAULT_CODES = {
    400: "INVALID_REQUEST", 401: "AUTHENTICATION_REQUIRED", 403: "FORBIDDEN",
    404: "NOT_FOUND", 409: "CONFLICT", 422: "INVALID_REQUEST",
    429: "RATE_LIMITED", 503: "DEPENDENCY_UNAVAILABLE",
}


class OnboardingRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await original(request)
            except RequestValidationError:
                return error_response(request, 422, "INVALID_REQUEST")
            except HTTPException as exc:
                code = exc.detail if type(exc.detail) is str and exc.detail in _ONBOARDING_CODES else _ONBOARDING_DEFAULT_CODES.get(exc.status_code, "REQUEST_REJECTED")
                return error_response(
                    request, exc.status_code, code,
                    retryable=exc.status_code == 503 and code not in {
                        "ONBOARDING_COMMIT_OUTCOME_UNKNOWN", "ONBOARDING_COMMIT_ROLLED_BACK",
                    },
                    headers={"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None,
                )
            except Exception:
                return error_response(request, 500, "INTERNAL_ERROR")

        return handler


_ONBOARDING_ERROR_RESPONSES = {
    status: {
        "description": "Request rejected",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponseDTO"}}},
        "headers": {
            "X-Request-ID": {"schema": {"type": "string", "format": "uuid"}},
            "Cache-Control": {"schema": {"type": "string", "enum": ["no-store, private"]}},
            "Pragma": {"schema": {"type": "string", "const": "no-cache"}},
            **({"WWW-Authenticate": {"schema": {"type": "string", "enum": ["Bearer"]}}} if status == 401 else {}),
        },
    }
    for status in (400, 401, 403, 404, 409, 422, 429, 500, 503)
}


platform_router = APIRouter(prefix="/api/v1/platform", tags=["institution_onboarding"], route_class=OnboardingRoute, responses=_ONBOARDING_ERROR_RESPONSES)
onboarding_router = APIRouter(prefix="/api/v1/institution-onboarding", tags=["institution_onboarding"], route_class=OnboardingRoute, responses=_ONBOARDING_ERROR_RESPONSES)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", request.headers.get("x-request-id", "missing-request-id"))


async def _safe_call(awaitable):
    return await awaitable


@platform_router.post("/institution-invitations", response_model=OnboardingSuccessEnvelope[InvitationIssuedDTO])
async def post_invitation(payload: InvitationCreate, request: Request, idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)], current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_institution_onboarding_writer_session), identity_session=Depends(get_db_session)):
    await _safe_call(require_current_reviewer(identity_session, current_user))
    return ok_response(await _safe_call(create_invitation(
        session, current_user, payload, _request_id(request), idempotency_key,
        region_session=identity_session,
    )))


@platform_router.get("/institution-invitations", response_model=OnboardingSuccessEnvelope[list[InvitationSummaryDTO]])
async def get_invitations(current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_institution_onboarding_reader_session), identity_session=Depends(get_db_session)):
    await _safe_call(require_current_reviewer(identity_session, current_user))
    rows = await _safe_call(InstitutionOnboardingRepository(session).list_invitations())
    return ok_response([dict(row) for row in rows])


@platform_router.post("/institution-invitations/{invitation_id}/resend", response_model=OnboardingSuccessEnvelope[InvitationIssuedDTO])
async def post_invitation_resend(invitation_id: str, payload: InvitationResendRequest, request: Request, idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)], current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_institution_onboarding_writer_session), identity_session=Depends(get_db_session)):
    await _safe_call(require_current_reviewer(identity_session, current_user))
    return ok_response(await _safe_call(resend_invitation(
        session, current_user, invitation_id, payload, _request_id(request), idempotency_key,
    )))


@platform_router.post("/institution-invitations/{invitation_id}/revoke", response_model=OnboardingSuccessEnvelope[InvitationRevokedDTO])
async def post_invitation_revoke(invitation_id: str, payload: InvitationRevokeRequest, request: Request, idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)], current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_institution_onboarding_writer_session), identity_session=Depends(get_db_session)):
    await _safe_call(require_current_reviewer(identity_session, current_user))
    return ok_response(await _safe_call(revoke_invitation(
        session, current_user, invitation_id, payload, _request_id(request), idempotency_key,
    )))


@onboarding_router.post("/activate", response_model=OnboardingSuccessEnvelope[ActivationDTO])
async def post_activate(payload: ActivationRequest, request: Request, idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)], session=Depends(get_institution_onboarding_writer_session)):
    return ok_response(await _safe_call(activate(session, payload, _request_id(request), idempotency_key)))


@onboarding_router.get("/application", response_model=OnboardingSuccessEnvelope[ApplicationDTO], response_model_exclude_unset=True)
async def read_application(current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_institution_onboarding_reader_session)):
    return ok_response(await _safe_call(get_application(session, current_user.id)))


@onboarding_router.put("/application", response_model=OnboardingSuccessEnvelope[ApplicationDTO], response_model_exclude_unset=True)
async def put_application(payload: ApplicationDraftRequest, current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_institution_onboarding_writer_session)):
    return ok_response(await _safe_call(save_draft(session, current_user.id, payload)))


@onboarding_router.post("/application/submit", response_model=OnboardingSuccessEnvelope[ApplicationDTO], response_model_exclude_unset=True)
async def post_submit(payload: ApplicationSubmitRequest, idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)], current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_institution_onboarding_writer_session)):
    return ok_response(await _safe_call(submit_application(session, current_user.id, payload, idempotency_key)))


@onboarding_router.get("/application/corrections", response_model=OnboardingSuccessEnvelope[ApplicationCorrectionDTO])
async def get_corrections(current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_institution_onboarding_reader_session)):
    value = await _safe_call(get_application(session, current_user.id))
    return ok_response({"status": value["status"], "fields": value["correction_fields"], "reason_code": value["correction_reason_code"], "version": value["version"]})


@onboarding_router.post("/application/resubmit", response_model=OnboardingSuccessEnvelope[ApplicationDTO], response_model_exclude_unset=True)
async def post_resubmit(payload: ApplicationResubmitRequest, idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)], current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_institution_onboarding_writer_session)):
    return ok_response(await _safe_call(resubmit_application(session, current_user.id, payload, idempotency_key)))


@platform_router.get("/institution-reviews", response_model=OnboardingSuccessEnvelope[list[ReviewQueueItemDTO]])
async def get_review_queue(current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_institution_onboarding_reader_session), identity_session=Depends(get_db_session)):
    await _safe_call(require_current_reviewer(identity_session, current_user))
    rows = await _safe_call(InstitutionOnboardingRepository(session).list_reviews())
    return ok_response([{"application_id": row.application_id, "status": row.status, "submitted_at": row.submitted_at, "version": row.version} for row in rows])


@platform_router.get("/institution-reviews/{application_id}", response_model=OnboardingSuccessEnvelope[ReviewDetailDTO], response_model_exclude_unset=True)
async def get_review(application_id: str, current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_institution_onboarding_reader_session), identity_session=Depends(get_db_session)):
    await _safe_call(require_current_reviewer(identity_session, current_user))
    repo = InstitutionOnboardingRepository(session); row = await _safe_call(repo.get_application_for_review(application_id))
    if row is None: from fastapi import HTTPException; raise HTTPException(404, "ONBOARDING_APPLICATION_NOT_FOUND")
    revisions = await _safe_call(repo.list_revisions(application_id))
    materials = await _safe_call(repo.review_materials(application_id))
    try:
        draft = review_draft_projection(dict(row.draft_payload))
        public_revisions = [
            {
                "revision_no": value.revision_no,
                "snapshot": review_draft_projection(dict(value.snapshot)),
                "created_at": value.created_at,
            }
            for value in revisions
        ]
    except Exception:
        raise HTTPException(503, "ONBOARDING_PII_DECRYPTION_UNAVAILABLE") from None
    return ok_response({"application_id": row.application_id, "status": row.status, "draft": draft, "version": row.version, "revisions": public_revisions, "materials": [dict(value) for value in materials]})


@platform_router.post("/institution-reviews/{application_id}/decision", response_model=OnboardingSuccessEnvelope[ApplicationDTO], response_model_exclude_unset=True)
async def post_review_decision(application_id: str, payload: ReviewDecisionRequest, request: Request, idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)], current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_institution_review_writer_session), identity_session=Depends(get_db_session)):
    return ok_response(await _safe_call(review_decision(session, current_user, application_id, payload, _request_id(request), idempotency_key, currentness_session=identity_session)))
