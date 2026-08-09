from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from app.modules.health_analysis.aggregation import STANDARD_INDICATORS, build_health_summary, build_health_trend
from app.modules.health_analysis.repository import (
    get_latest_indicators_for_summary,
    get_summary_profile,
    get_summary_user,
    list_indicator_trend_points,
    list_member_indicator_trend_points,
)
from app.modules.health_analysis.schemas import (
    HealthSummary,
    HealthTrend,
    MemberSelfHealthTrend,
    TrendPoint,
)
from app.modules.user_health.repository import get_member_profile_user_state
from app.modules.user_health.service import ensure_current_health_data_member


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


async def get_member_self_health_trend(
    session,
    *,
    user_id: int,
    indicator_type: str,
    start_at,
    end_at,
    limit: int,
) -> MemberSelfHealthTrend:
    if end_at is None:
        end_at = datetime.now(timezone.utc)
    if start_at is None:
        start_at = end_at - timedelta(days=30)
    if start_at > end_at:
        raise HTTPException(status_code=422, detail="HEALTH_INDICATOR_TIME_RANGE_INVALID")
    try:
        user = await get_member_profile_user_state(session, user_id)
        ensure_current_health_data_member(user)
        rows = await list_member_indicator_trend_points(
            session,
            user_id=user_id,
            indicator_type=indicator_type,
            start_at=start_at,
            end_at=end_at,
            limit=limit,
        )
    except asyncio.CancelledError:
        raise
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="HEALTH_DATA_UNAVAILABLE") from None

    units = {row.unit for row in rows}
    if len(units) > 1:
        raise HTTPException(status_code=409, detail="HEALTH_INDICATOR_UNIT_INCONSISTENT")
    return MemberSelfHealthTrend(
        state="AVAILABLE" if rows else "EMPTY",
        indicator_type=indicator_type,
        unit=next(iter(units), None),
        points=[
            TrendPoint(value=row.value, recorded_at=row.recorded_at, source=row.source)
            for row in rows
        ],
    )
