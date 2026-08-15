from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.core.database import get_db_session, get_institution_onboarding_reader_session, get_institution_onboarding_writer_session, get_institution_review_writer_session
from app.core.responses import ok_response
from app.core.security import CurrentUser, get_current_user_from_jwt
from app.modules.institution_onboarding.repository import InstitutionOnboardingRepository
from app.modules.institution_onboarding.schemas import (
    ActivationRequest, ApplicationDraftRequest, ApplicationResubmitRequest,
    ApplicationSubmitRequest, InvitationCreate, InvitationResendRequest,
    InvitationRevokeRequest, ReviewDecisionRequest,
)
from app.modules.institution_onboarding.service import (
    activate, create_invitation, get_application, resubmit_application,
    require_current_reviewer, resend_invitation, review_decision,
    review_draft_projection, revoke_invitation, save_draft, submit_application,
)


platform_router = APIRouter(prefix="/api/v1/platform", tags=["institution_onboarding"])
onboarding_router = APIRouter(prefix="/api/v1/institution-onboarding", tags=["institution_onboarding"])


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", request.headers.get("x-request-id", "missing-request-id"))


async def _safe_call(awaitable):
    try:
        return await awaitable
    except asyncio.CancelledError:
        raise
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "ONBOARDING_PERSISTENCE_UNAVAILABLE") from None


@platform_router.post("/institution-invitations")
async def post_invitation(payload: InvitationCreate, request: Request, idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)], current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_institution_onboarding_writer_session), identity_session=Depends(get_db_session)):
    await _safe_call(require_current_reviewer(identity_session, current_user))
    return ok_response(await _safe_call(create_invitation(
        session, current_user, payload, _request_id(request), idempotency_key,
        region_session=identity_session,
    )))


@platform_router.get("/institution-invitations")
async def get_invitations(current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_institution_onboarding_reader_session), identity_session=Depends(get_db_session)):
    await _safe_call(require_current_reviewer(identity_session, current_user))
    rows = await _safe_call(InstitutionOnboardingRepository(session).list_invitations())
    return ok_response([dict(row) for row in rows])


@platform_router.post("/institution-invitations/{invitation_id}/resend")
async def post_invitation_resend(invitation_id: str, payload: InvitationResendRequest, request: Request, idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)], current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_institution_onboarding_writer_session), identity_session=Depends(get_db_session)):
    await _safe_call(require_current_reviewer(identity_session, current_user))
    return ok_response(await _safe_call(resend_invitation(
        session, current_user, invitation_id, payload, _request_id(request), idempotency_key,
    )))


@platform_router.post("/institution-invitations/{invitation_id}/revoke")
async def post_invitation_revoke(invitation_id: str, payload: InvitationRevokeRequest, request: Request, idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)], current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_institution_onboarding_writer_session), identity_session=Depends(get_db_session)):
    await _safe_call(require_current_reviewer(identity_session, current_user))
    return ok_response(await _safe_call(revoke_invitation(
        session, current_user, invitation_id, payload, _request_id(request), idempotency_key,
    )))


@onboarding_router.post("/activate")
async def post_activate(payload: ActivationRequest, request: Request, idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)], session=Depends(get_institution_onboarding_writer_session)):
    return ok_response(await _safe_call(activate(session, payload, _request_id(request), idempotency_key)))


@onboarding_router.get("/application")
async def read_application(current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_institution_onboarding_reader_session)):
    return ok_response(await _safe_call(get_application(session, current_user.id)))


@onboarding_router.put("/application")
async def put_application(payload: ApplicationDraftRequest, current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_institution_onboarding_writer_session)):
    return ok_response(await _safe_call(save_draft(session, current_user.id, payload)))


@onboarding_router.post("/application/submit")
async def post_submit(payload: ApplicationSubmitRequest, idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)], current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_institution_onboarding_writer_session)):
    return ok_response(await _safe_call(submit_application(session, current_user.id, payload, idempotency_key)))


@onboarding_router.get("/application/corrections")
async def get_corrections(current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_institution_onboarding_reader_session)):
    value = await _safe_call(get_application(session, current_user.id))
    return ok_response({"status": value["status"], "fields": value["correction_fields"], "reason_code": value["correction_reason_code"], "version": value["version"]})


@onboarding_router.post("/application/resubmit")
async def post_resubmit(payload: ApplicationResubmitRequest, idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)], current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_institution_onboarding_writer_session)):
    return ok_response(await _safe_call(resubmit_application(session, current_user.id, payload, idempotency_key)))


@platform_router.get("/institution-reviews")
async def get_review_queue(current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_institution_onboarding_reader_session), identity_session=Depends(get_db_session)):
    await _safe_call(require_current_reviewer(identity_session, current_user))
    rows = await _safe_call(InstitutionOnboardingRepository(session).list_reviews())
    return ok_response([{"application_id": row.application_id, "status": row.status, "submitted_at": row.submitted_at, "version": row.version} for row in rows])


@platform_router.get("/institution-reviews/{application_id}")
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


@platform_router.post("/institution-reviews/{application_id}/decision")
async def post_review_decision(application_id: str, payload: ReviewDecisionRequest, request: Request, idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)], current_user: CurrentUser = Depends(get_current_user_from_jwt), session=Depends(get_institution_review_writer_session), identity_session=Depends(get_db_session)):
    return ok_response(await _safe_call(review_decision(session, current_user, application_id, payload, _request_id(request), idempotency_key, currentness_session=identity_session)))
