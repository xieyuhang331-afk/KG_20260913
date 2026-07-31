from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.core.database import get_db_session
from app.core.permissions import ensure_can_access_own_user_resource
from app.core.responses import ok_response
from app.core.security import CurrentUser, get_current_user_from_jwt
from app.modules.health_analysis.ai_contract_service import get_ai_health_input_contract
from app.modules.health_analysis.service import get_health_summary, get_health_trend


router = APIRouter(prefix="/api/v1/users", tags=["health_analysis"])
internal_router = APIRouter(prefix="/internal/v1/users", tags=["health_analysis_internal"])


@router.get("/{user_id}/health-summary")
async def get_health_summary_api(
    user_id: int,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    ensure_can_access_own_user_resource(current_user, user_id)
    result = await get_health_summary(session, user_id=user_id)
    return ok_response(result.model_dump(mode="json"))


@router.get("/{user_id}/health-trends")
async def get_health_trend_api(
    user_id: int,
    indicator_type: str = Query(..., min_length=1, max_length=30),
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int | None = Query(default=None, ge=1, le=1000),
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    ensure_can_access_own_user_resource(current_user, user_id)
    result = await get_health_trend(
        session,
        user_id=user_id,
        indicator_type=indicator_type,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
    )
    return ok_response(result.model_dump(mode="json"))


@internal_router.get("/{user_id}/ai-health-input")
async def get_ai_health_input_api(
    user_id: int,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    ensure_can_access_own_user_resource(current_user, user_id)
    result = await get_ai_health_input_contract(session, user_id=user_id)
    return ok_response(result.model_dump(mode="json"))
