from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from app.core.database import get_db_session
from app.core.responses import ok_response
from app.core.security import CurrentUser, get_current_user_from_jwt
from app.modules.auth.manual_identity_review_application import (
    PlatformAdminManualIdentityReviewConflict,
    PlatformAdminManualIdentityReviewForbidden,
    PlatformAdminManualIdentityReviewNotFound,
    PlatformAdminManualIdentityReviewRequest as ApplicationReviewRequest,
    PlatformAdminManualIdentityReviewUnavailable,
    PlatformIdentitySubmissionReviewService,
)
from app.modules.review.schemas import (
    IdentityReviewDetailEnvelope,
    PlatformAdminManualIdentityReviewRequest,
    PlatformAdminManualIdentityReviewEnvelope,
    PlatformAdminManualIdentityReviewResponse,
    IdentityReviewDetailResponse,
    IdentityReviewQueueItem,
    IdentityReviewQueueResponse,
    IdentityReviewQueueEnvelope,
    PlatformIdentityReviewRejectRequest,
    IdentityReviewRejectEnvelope,
    IdentityReviewRejectResponse,
    PlatformIdentityReviewStepUpEnvelope,
    PlatformIdentityReviewStepUpRequest,
    PlatformIdentityReviewStepUpResponse,
    ReviewErrorResponse,
    TenantReviewApproveRequest,
    TenantReviewQueueQuery,
    TenantReviewRejectRequest,
)
from app.modules.review.service import (
    approve_tenant_application,
    get_tenant_review_detail,
    list_tenant_review_queue,
    reject_tenant_application,
)


router = APIRouter(prefix="/api/v1/reviews", tags=["review"])


def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    return token


async def get_platform_identity_reviewer(
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
) -> CurrentUser:
    _require_platform_identity_reviewer(current_user)
    return current_user


def get_platform_admin_manual_identity_review_service(
    current_user: CurrentUser = Depends(get_platform_identity_reviewer),
):
    from app.composition.p1_verified_transition import (
        create_platform_admin_manual_identity_review_service,
    )
    from app.core.database import (
        get_session_factory,
        get_verification_writer_session_factory,
    )
    from app.core.uuid_generator import Uuid7Generator

    try:
        authority_session_factory = get_session_factory()
        verification_session_factory = (
            get_verification_writer_session_factory()
        )
    except RuntimeError:
        raise HTTPException(
            status_code=503,
            detail="Identity review service unavailable",
        ) from None
    return create_platform_admin_manual_identity_review_service(
        authority_session_factory=authority_session_factory,
        verification_session_factory=verification_session_factory,
        uuid_generator=Uuid7Generator(),
    )


def get_platform_identity_submission_review_service(
    session=Depends(get_db_session),
):
    from app.core.database import (
        get_session_factory,
        get_verification_writer_session_factory,
    )
    from app.modules.auth.identity_submission_crypto import (
        IdentitySubmissionCrypto,
        IdentitySubmissionCryptoUnavailable,
    )
    from app.modules.auth.manual_identity_review_repository import (
        SqlAlchemyPlatformIdentitySubmissionReviewRepository,
    )

    try:
        writer_factory = get_verification_writer_session_factory()
        crypto = IdentitySubmissionCrypto.from_environment()
    except (RuntimeError, IdentitySubmissionCryptoUnavailable):
        raise HTTPException(
            status_code=503, detail="Identity review service unavailable"
        ) from None
    return PlatformIdentitySubmissionReviewService(
        application_repository=SqlAlchemyPlatformIdentitySubmissionReviewRepository(session),
        writer_session_factory=writer_factory,
        crypto=crypto,
        application_session_factory=get_session_factory(),
    )


def get_platform_identity_detail_service(
    session=Depends(get_db_session),
):
    from app.core.database import (
        get_session_factory,
        get_verification_writer_session_factory,
    )
    from app.modules.auth.identity_review_step_up import (
        PlatformIdentityReviewStepUpUnavailable,
        SqlAlchemyPlatformIdentityReviewStepUpRepository,
        create_platform_identity_review_step_up_service,
    )
    from app.modules.auth.identity_submission_crypto import (
        IdentitySubmissionCrypto,
        IdentitySubmissionCryptoUnavailable,
    )
    from app.modules.auth.manual_identity_review_repository import (
        SqlAlchemyPlatformIdentitySubmissionReviewRepository,
    )

    repository = SqlAlchemyPlatformIdentitySubmissionReviewRepository(session)
    try:
        step_up_service = create_platform_identity_review_step_up_service(
            repository=SqlAlchemyPlatformIdentityReviewStepUpRepository(
                session=session, delegate=repository
            )
        )
        writer_factory = get_verification_writer_session_factory()
        crypto = IdentitySubmissionCrypto.from_environment()
    except (
        RuntimeError,
        IdentitySubmissionCryptoUnavailable,
        PlatformIdentityReviewStepUpUnavailable,
    ):
        raise HTTPException(
            status_code=503, detail="Identity review service unavailable"
        ) from None
    return PlatformIdentitySubmissionReviewService(
        application_repository=repository,
        writer_session_factory=writer_factory,
        crypto=crypto,
        step_up_service=step_up_service,
        application_session_factory=get_session_factory(),
    )


def get_platform_identity_step_up_service(session=Depends(get_db_session)):
    from app.modules.auth.identity_review_step_up import (
        PlatformIdentityReviewStepUpUnavailable,
        SqlAlchemyPlatformIdentityReviewStepUpRepository,
        create_platform_identity_review_step_up_service,
    )
    from app.modules.auth.manual_identity_review_repository import (
        SqlAlchemyPlatformIdentitySubmissionReviewRepository,
    )

    try:
        delegate = SqlAlchemyPlatformIdentitySubmissionReviewRepository(session)
        return create_platform_identity_review_step_up_service(
            repository=SqlAlchemyPlatformIdentityReviewStepUpRepository(
                session=session, delegate=delegate
            )
        )
    except PlatformIdentityReviewStepUpUnavailable:
        raise HTTPException(
            status_code=503, detail="Identity review service unavailable"
        ) from None


def _require_platform_identity_reviewer(current_user: CurrentUser) -> None:
    if (
        current_user.role != "super_admin"
        or current_user.tenant_id is not None
        or current_user.org_id is not None
    ):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post(
    "/users/{user_id}/identity/step-up",
    response_model=PlatformIdentityReviewStepUpEnvelope,
    responses={
        401: {"model": ReviewErrorResponse},
        403: {"model": ReviewErrorResponse},
        404: {"model": ReviewErrorResponse},
        429: {"model": ReviewErrorResponse},
        503: {"model": ReviewErrorResponse},
    },
)
async def issue_platform_identity_review_step_up_api(
    user_id: int,
    payload: PlatformIdentityReviewStepUpRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_platform_identity_reviewer),
    service=Depends(get_platform_identity_step_up_service),
) -> dict:
    from app.modules.auth.identity_review_step_up import (
        PlatformIdentityReviewStepUpForbidden,
        PlatformIdentityReviewStepUpNotFound,
        PlatformIdentityReviewStepUpRateLimited,
        PlatformIdentityReviewStepUpUnauthorized,
        PlatformIdentityReviewStepUpUnavailable,
    )

    try:
        result = await service.issue(
            current_user=current_user,
            subject_user_id=user_id,
            password=payload.password.get_secret_value(),
            access_token=_bearer_token(request),
        )
    except PlatformIdentityReviewStepUpUnauthorized:
        raise HTTPException(status_code=401, detail="Invalid re-authentication") from None
    except PlatformIdentityReviewStepUpForbidden:
        raise HTTPException(status_code=403, detail="Forbidden") from None
    except PlatformIdentityReviewStepUpNotFound:
        raise HTTPException(status_code=404, detail="Identity review subject not found") from None
    except PlatformIdentityReviewStepUpRateLimited:
        raise HTTPException(status_code=429, detail="Re-authentication rate limited") from None
    except PlatformIdentityReviewStepUpUnavailable:
        raise HTTPException(status_code=503, detail="Identity review service unavailable") from None
    return ok_response(
        PlatformIdentityReviewStepUpResponse(
            step_up_token=result.token,
            expires_in=result.expires_in,
        ).model_dump()
    )


@router.post(
    "/users/{user_id}/identity/approve",
    response_model=PlatformAdminManualIdentityReviewEnvelope,
    responses={
        401: {"model": ReviewErrorResponse},
        403: {"model": ReviewErrorResponse},
        404: {"model": ReviewErrorResponse},
        409: {"model": ReviewErrorResponse},
        503: {"model": ReviewErrorResponse},
    },
)
async def approve_platform_user_identity_review_api(
    user_id: int,
    payload: PlatformAdminManualIdentityReviewRequest,
    current_user: CurrentUser = Depends(get_platform_identity_reviewer),
    service=Depends(get_platform_admin_manual_identity_review_service),
    submission_review=Depends(get_platform_identity_submission_review_service),
) -> dict:
    decided_at = datetime.now(timezone.utc)
    try:
        async with submission_review.subject_coordination(user_id):
            evidence_digest = await submission_review.approval_evidence_digest(
                current_user=current_user,
                user_ref=user_id,
                request=payload,
                allow_verified_replay=True,
            )
            result = await service.execute(
                current_user=current_user,
                user_ref=user_id,
                request=ApplicationReviewRequest(
                    idempotency_key=payload.idempotency_key,
                    decided_at=decided_at,
                    evidence_digest=evidence_digest,
                    submission_version=payload.submission_version,
                    decision_basis_code=payload.decision_basis_code,
                ),
            )
            await submission_review.mark_verified(
                current_user=current_user, user_ref=user_id, request=payload,
                evidence_digest=evidence_digest,
                decided_at=getattr(result, "decided_at", decided_at),
            )
    except PlatformAdminManualIdentityReviewForbidden:
        raise HTTPException(status_code=403, detail="Forbidden") from None
    except PlatformAdminManualIdentityReviewNotFound:
        raise HTTPException(
            status_code=404,
            detail="Identity review subject not found",
        ) from None
    except PlatformAdminManualIdentityReviewConflict:
        raise HTTPException(
            status_code=409,
            detail="Identity review decision conflict",
        ) from None
    except PlatformAdminManualIdentityReviewUnavailable:
        raise HTTPException(
            status_code=503,
            detail="Identity review service unavailable",
        ) from None

    response = PlatformAdminManualIdentityReviewResponse(
        user_id=result.user_ref,
        submission_version=payload.submission_version,
        status=result.status,
        decision_ref=str(result.verification_decision_ref),
        replayed=result.replayed,
    )
    return ok_response(response.model_dump())


@router.get(
    "/identity",
    response_model=IdentityReviewQueueEnvelope,
    responses={
        401: {"model": ReviewErrorResponse},
        403: {"model": ReviewErrorResponse},
        503: {"model": ReviewErrorResponse},
    },
)
async def list_platform_identity_reviews_api(
    status: str = Query(default="submitted", pattern=r"^submitted$"),
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    current_user: CurrentUser = Depends(get_platform_identity_reviewer),
    service=Depends(get_platform_identity_submission_review_service),
) -> dict:
    del status
    try:
        items, total = await service.list_queue(
            current_user=current_user, page=page, page_size=page_size
        )
    except PlatformAdminManualIdentityReviewForbidden:
        raise HTTPException(status_code=403, detail="Forbidden") from None
    except PlatformAdminManualIdentityReviewUnavailable:
        raise HTTPException(
            status_code=503, detail="Identity review service unavailable"
        ) from None
    response = IdentityReviewQueueResponse(
        items=[IdentityReviewQueueItem.from_submission(item) for item in items],
        page=page, page_size=page_size, total=total,
    )
    return ok_response(response.model_dump())


@router.get(
    "/users/{user_id}/identity",
    response_model=IdentityReviewDetailEnvelope,
    responses={
        401: {"model": ReviewErrorResponse},
        403: {"model": ReviewErrorResponse},
        404: {"model": ReviewErrorResponse},
        503: {"model": ReviewErrorResponse},
    },
)
async def get_platform_identity_review_detail_api(
    user_id: int,
    request: Request,
    step_up_token: Annotated[
        str, Header(alias="X-Identity-Review-Step-Up", min_length=1)
    ],
    purpose_code: Literal["MANUAL_REVIEW"] = Query(),
    current_user: CurrentUser = Depends(get_platform_identity_reviewer),
    service=Depends(get_platform_identity_detail_service),
) -> dict:
    from app.modules.auth.identity_review_step_up import (
        PlatformIdentityReviewStepUpForbidden,
        PlatformIdentityReviewStepUpUnauthorized,
        PlatformIdentityReviewStepUpUnavailable,
    )

    try:
        model, name, card = await service.detail(
            current_user=current_user,
            user_ref=user_id,
            purpose_code=purpose_code,
            access_token=_bearer_token(request),
            step_up_token=step_up_token,
        )
    except PlatformIdentityReviewStepUpUnauthorized:
        raise HTTPException(status_code=401, detail="Invalid or expired step-up") from None
    except PlatformIdentityReviewStepUpForbidden:
        raise HTTPException(status_code=403, detail="Forbidden") from None
    except PlatformIdentityReviewStepUpUnavailable:
        raise HTTPException(status_code=503, detail="Identity review service unavailable") from None
    except PlatformAdminManualIdentityReviewForbidden:
        raise HTTPException(status_code=403, detail="Forbidden") from None
    except PlatformAdminManualIdentityReviewNotFound:
        raise HTTPException(status_code=404, detail="Identity review subject not found") from None
    except PlatformAdminManualIdentityReviewUnavailable:
        raise HTTPException(status_code=503, detail="Identity review service unavailable") from None
    return ok_response(
        IdentityReviewDetailResponse.from_review(model, name, card).model_dump()
    )


@router.post(
    "/users/{user_id}/identity/reject",
    response_model=IdentityReviewRejectEnvelope,
    responses={
        401: {"model": ReviewErrorResponse},
        403: {"model": ReviewErrorResponse},
        404: {"model": ReviewErrorResponse},
        409: {"model": ReviewErrorResponse},
        503: {"model": ReviewErrorResponse},
    },
)
async def reject_platform_identity_review_api(
    user_id: int,
    payload: PlatformIdentityReviewRejectRequest,
    current_user: CurrentUser = Depends(get_platform_identity_reviewer),
    service=Depends(get_platform_identity_submission_review_service),
) -> dict:
    try:
        model, replayed = await service.reject(
            current_user=current_user, user_ref=user_id, request=payload
        )
    except PlatformAdminManualIdentityReviewForbidden:
        raise HTTPException(status_code=403, detail="Forbidden") from None
    except PlatformAdminManualIdentityReviewNotFound:
        raise HTTPException(status_code=404, detail="Identity review subject not found") from None
    except PlatformAdminManualIdentityReviewConflict:
        raise HTTPException(status_code=409, detail="Identity review decision conflict") from None
    except PlatformAdminManualIdentityReviewUnavailable:
        raise HTTPException(status_code=503, detail="Identity review service unavailable") from None
    return ok_response(IdentityReviewRejectResponse(
        user_id=user_id, submission_version=model.version,
        status="rejected", replayed=replayed,
    ).model_dump())


@router.get("/queue/tenant")
async def get_tenant_review_queue(
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    keyword: str | None = None,
    province: str | None = None,
    city: str | None = None,
) -> dict:
    query = TenantReviewQueueQuery(
        page=page,
        page_size=page_size,
        keyword=keyword,
        province=province,
        city=city,
    )
    result = await list_tenant_review_queue(session, current_user, query)
    return ok_response(result.model_dump())


@router.get("/tenants/{tenant_id}")
async def get_tenant_review_detail_api(
    tenant_id: int,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    result = await get_tenant_review_detail(session, current_user, tenant_id)
    return ok_response(result.model_dump())


@router.post("/tenants/{tenant_id}/approve")
async def approve_tenant_review_api(
    tenant_id: int,
    payload: TenantReviewApproveRequest,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    result = await approve_tenant_application(session, current_user, tenant_id, payload)
    return ok_response(result.model_dump())


@router.post("/tenants/{tenant_id}/reject")
async def reject_tenant_review_api(
    tenant_id: int,
    payload: TenantReviewRejectRequest,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    result = await reject_tenant_application(session, current_user, tenant_id, payload)
    return ok_response(result.model_dump())
