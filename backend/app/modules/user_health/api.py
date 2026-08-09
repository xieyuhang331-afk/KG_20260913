from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.database import get_db_session, get_session_factory
from app.core.permissions import ensure_can_access_own_user_resource, ensure_is_member
from app.core.responses import ok_response
from app.core.security import CurrentUser, get_current_user, get_current_user_from_jwt
from app.modules.user_health.schemas import (
    HealthIndicatorBatchCreateRequest,
    HealthProfileCreateRequest,
    MemberSelfHealthProfileWriteRequest,
)
from app.modules.user_health.service import (
    create_health_indicators,
    create_health_profile,
    get_member_self_health_profile,
    get_member_self_latest_health_indicators,
    get_health_profile,
    get_latest_health_indicators,
    list_health_indicators,
    list_member_self_health_indicators,
    put_member_self_health_profile,
)


router = APIRouter(prefix="/api/v1/users", tags=["user_health"])


@router.get("/me/health-indicators")
async def list_member_self_health_indicators_api(
    indicator_type: str | None = Query(default=None, max_length=30),
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None, max_length=512),
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    ensure_is_member(current_user)
    result = await list_member_self_health_indicators(
        session,
        user_id=current_user.id,
        indicator_type=indicator_type,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
        cursor=cursor,
    )
    return ok_response(result.model_dump(mode="json"))


@router.get("/me/health-indicators/latest")
async def get_member_self_latest_health_indicators_api(
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    ensure_is_member(current_user)
    result = await get_member_self_latest_health_indicators(session, user_id=current_user.id)
    return ok_response(result.model_dump(mode="json"))


@router.get("/me/health-profile")
async def get_member_self_health_profile_api(
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    ensure_is_member(current_user)
    result = await get_member_self_health_profile(
        session,
        user_id=current_user.id,
    )
    return ok_response(result.model_dump(mode="json"))


@router.put("/me/health-profile")
async def put_member_self_health_profile_api(
    payload: MemberSelfHealthProfileWriteRequest,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    ensure_is_member(current_user)
    result = await put_member_self_health_profile(
        session,
        confirmation_session_factory_provider=get_session_factory,
        user_id=current_user.id,
        payload=payload,
    )
    return ok_response(result.model_dump(mode="json"))


@router.post("/{user_id}/health-profile", status_code=201)
async def create_health_profile_api(
    user_id: int,
    payload: HealthProfileCreateRequest,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    ensure_can_access_own_user_resource(current_user, user_id)
    result = await create_health_profile(session, user_id=user_id, payload=payload)
    return ok_response(result.model_dump(mode="json"))


@router.get("/{user_id}/health-profile")
async def get_health_profile_api(
    user_id: int,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    ensure_can_access_own_user_resource(current_user, user_id)
    result = await get_health_profile(session, user_id=user_id)
    return ok_response(result.model_dump(mode="json"))


@router.post("/{user_id}/health-indicators", status_code=201)
async def create_health_indicators_api(
    user_id: int,
    payload: HealthIndicatorBatchCreateRequest,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    ensure_can_access_own_user_resource(current_user, user_id)
    if any(indicator.source != "APP" for indicator in payload.indicators):
        raise HTTPException(status_code=422, detail="Only APP source is allowed for member write API")

    result = await create_health_indicators(session, user_id=user_id, payload=payload)
    return ok_response([indicator.model_dump(mode="json") for indicator in result])


@router.get("/{user_id}/health-indicators")
async def list_health_indicators_api(
    user_id: int,
    indicator_type: str | None = Query(default=None, max_length=30),
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    ensure_can_access_own_user_resource(current_user, user_id)
    result = await list_health_indicators(
        session,
        user_id=user_id,
        indicator_type=indicator_type,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
    )
    return ok_response([indicator.model_dump(mode="json") for indicator in result])


@router.get("/{user_id}/health-indicators/latest")
async def get_latest_health_indicators_api(
    user_id: int,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    ensure_can_access_own_user_resource(current_user, user_id)
    result = await get_latest_health_indicators(session, user_id=user_id)
    return ok_response([indicator.model_dump(mode="json") for indicator in result])
