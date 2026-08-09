from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import and_, or_, select, update

from app.core.sqlalchemy_mapping import map_core_model_classes
from app.modules.auth.models import User
from app.modules.user_health.models import HealthIndicator, HealthProfile


def _ensure_mapped() -> None:
    map_core_model_classes()


async def get_member_profile_user_state(session, user_id: int):
    _ensure_mapped()
    statement = (
        select(
            User.id,
            User.role,
            User.status,
            User.verify_status,
        )
        .where(User.id == user_id)
        .limit(1)
    )
    row = (await session.execute(statement)).one_or_none()
    if row is None:
        return None
    return SimpleNamespace(
        id=row.id,
        role=row.role,
        status=row.status,
        verify_status=row.verify_status,
    )


async def get_health_profile_by_user_id(session, user_id: int):
    _ensure_mapped()
    result = await session.execute(select(HealthProfile).where(HealthProfile.user_id == user_id).limit(1))
    return result.scalar_one_or_none()


async def create_health_profile_record(session, *, profile_data: dict):
    _ensure_mapped()
    profile = HealthProfile()
    for key, value in profile_data.items():
        setattr(profile, key, value)

    session.add(profile)
    await session.flush()
    return profile


async def update_health_profile_record(
    session,
    *,
    user_id: int,
    expected_updated_at,
    profile_data: dict,
    updated_at,
):
    _ensure_mapped()
    table = HealthProfile.__table__
    statement = (
        update(table)
        .where(
            table.c.user_id == user_id,
            table.c.updated_at == expected_updated_at,
        )
        .values(**profile_data, updated_at=updated_at)
        .returning(*table.c)
    )
    row = (await session.execute(statement)).mappings().one_or_none()
    return SimpleNamespace(**row) if row is not None else None


async def create_health_indicator_records(session, *, records: list[dict]):
    _ensure_mapped()
    indicators = []
    for record in records:
        indicator = HealthIndicator()
        for key, value in record.items():
            setattr(indicator, key, value)
        indicators.append(indicator)

    session.add_all(indicators)
    await session.flush()
    return indicators


async def list_health_indicators_by_user(
    session,
    *,
    user_id: int,
    indicator_type: str | None = None,
    start_at=None,
    end_at=None,
    limit: int = 50,
):
    _ensure_mapped()
    statement = select(HealthIndicator).where(HealthIndicator.user_id == user_id)

    if indicator_type is not None:
        statement = statement.where(HealthIndicator.indicator_type == indicator_type)
    if start_at is not None:
        statement = statement.where(HealthIndicator.recorded_at >= start_at)
    if end_at is not None:
        statement = statement.where(HealthIndicator.recorded_at <= end_at)

    statement = statement.order_by(HealthIndicator.recorded_at.desc()).limit(limit)
    result = await session.execute(statement)
    return result.scalars().all()


async def list_latest_health_indicators_by_user(session, *, user_id: int):
    _ensure_mapped()
    statement = (
        select(HealthIndicator)
        .distinct(HealthIndicator.indicator_type)
        .where(HealthIndicator.user_id == user_id)
        .order_by(HealthIndicator.indicator_type, HealthIndicator.recorded_at.desc(), HealthIndicator.id.desc())
    )
    result = await session.execute(statement)
    return result.scalars().all()


def _member_indicator_projection():
    table = HealthIndicator.__table__
    return (
        table.c.id,
        table.c.batch_id,
        table.c.indicator_type,
        table.c.value,
        table.c.unit,
        table.c.source,
        table.c.recorded_at,
    )


async def list_member_health_indicator_history(
    session,
    *,
    user_id: int,
    indicator_type: str | None,
    start_at,
    end_at,
    cursor_recorded_at,
    cursor_id: int | None,
    limit: int,
):
    _ensure_mapped()
    table = HealthIndicator.__table__
    statement = select(*_member_indicator_projection()).where(table.c.user_id == user_id)
    if indicator_type is not None:
        statement = statement.where(table.c.indicator_type == indicator_type)
    if start_at is not None:
        statement = statement.where(table.c.recorded_at >= start_at)
    if end_at is not None:
        statement = statement.where(table.c.recorded_at <= end_at)
    if cursor_recorded_at is not None and cursor_id is not None:
        statement = statement.where(
            or_(
                table.c.recorded_at < cursor_recorded_at,
                and_(table.c.recorded_at == cursor_recorded_at, table.c.id < cursor_id),
            )
        )
    statement = statement.order_by(table.c.recorded_at.desc(), table.c.id.desc()).limit(limit)
    return (await session.execute(statement)).mappings().all()


async def list_member_latest_health_indicators(session, *, user_id: int):
    _ensure_mapped()
    table = HealthIndicator.__table__
    statement = (
        select(*_member_indicator_projection())
        .distinct(table.c.indicator_type)
        .where(table.c.user_id == user_id)
        .order_by(table.c.indicator_type, table.c.recorded_at.desc(), table.c.id.desc())
    )
    return (await session.execute(statement)).mappings().all()
