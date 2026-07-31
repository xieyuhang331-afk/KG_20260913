from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from app.modules.health_analysis.aggregation import STANDARD_INDICATORS, build_health_summary, build_health_trend
from app.modules.health_analysis.repository import (
    get_latest_indicators_for_summary,
    get_summary_profile,
    get_summary_user,
    list_indicator_trend_points,
)
from app.modules.health_analysis.schemas import HealthSummary, HealthTrend


async def get_health_summary(session, *, user_id: int) -> HealthSummary:
    user = await get_summary_user(session, user_id=user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    profile = await get_summary_profile(session, user_id=user_id)
    latest_indicators = await get_latest_indicators_for_summary(session, user_id=user_id)

    return build_health_summary(
        user=user,
        health_profile=profile,
        latest_indicators=latest_indicators,
    )


async def get_health_trend(
    session,
    *,
    user_id: int,
    indicator_type: str,
    start_at=None,
    end_at=None,
    limit: int | None = None,
) -> HealthTrend:
    user = await get_summary_user(session, user_id=user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if indicator_type not in STANDARD_INDICATORS:
        raise HTTPException(status_code=422, detail="Unknown indicator_type")

    if end_at is None:
        end_at = datetime.now(timezone.utc)
    if start_at is None:
        start_at = end_at - timedelta(days=30)
    if start_at > end_at:
        raise HTTPException(status_code=422, detail="Invalid time range")

    indicators = await list_indicator_trend_points(
        session,
        user_id=user_id,
        indicator_type=indicator_type,
        start_at=start_at,
        end_at=end_at,
        limit=limit or 200,
    )
    return build_health_trend(indicator_type=indicator_type, indicators=indicators)
