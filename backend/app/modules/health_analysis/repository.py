from __future__ import annotations

from sqlalchemy import select

from app.core.sqlalchemy_mapping import map_core_model_classes
from app.modules.auth.models import User
from app.modules.user_health.models import HealthIndicator, HealthProfile


def _ensure_mapped() -> None:
    map_core_model_classes()


async def get_summary_user(session, user_id: int):
    _ensure_mapped()
    result = await session.execute(select(User).where(User.id == user_id).limit(1))
    return result.scalar_one_or_none()


async def get_summary_profile(session, user_id: int):
    _ensure_mapped()
    result = await session.execute(select(HealthProfile).where(HealthProfile.user_id == user_id).limit(1))
    return result.scalar_one_or_none()


async def get_latest_indicators_for_summary(session, user_id: int):
    _ensure_mapped()
    statement = (
        select(HealthIndicator)
        .distinct(HealthIndicator.indicator_type)
        .where(HealthIndicator.user_id == user_id)
        .order_by(HealthIndicator.indicator_type, HealthIndicator.recorded_at.desc(), HealthIndicator.id.desc())
    )
    result = await session.execute(statement)
    return result.scalars().all()


async def list_indicator_trend_points(
    session,
    *,
    user_id: int,
    indicator_type: str,
    start_at,
    end_at,
    limit: int,
):
    _ensure_mapped()
    statement = select(HealthIndicator).where(
        HealthIndicator.user_id == user_id,
        HealthIndicator.indicator_type == indicator_type,
    )

    if start_at is not None:
        statement = statement.where(HealthIndicator.recorded_at >= start_at)
    if end_at is not None:
        statement = statement.where(HealthIndicator.recorded_at <= end_at)

    statement = statement.order_by(HealthIndicator.recorded_at.asc()).limit(limit)
    result = await session.execute(statement)
    return result.scalars().all()
