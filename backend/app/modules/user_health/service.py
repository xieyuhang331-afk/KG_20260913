from __future__ import annotations

from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.modules.auth.repository import get_user_by_id
from app.modules.user_health.repository import (
    create_health_indicator_records,
    create_health_profile_record,
    get_health_profile_by_user_id,
    list_health_indicators_by_user,
    list_latest_health_indicators_by_user,
)
from app.modules.user_health.schemas import (
    HealthIndicatorBatchCreateRequest,
    HealthIndicatorResponse,
    HealthProfileCreateRequest,
    HealthProfileResponse,
)


ALLOWED_HEALTH_INDICATOR_SOURCES = {"APP", "STORE", "DEVICE", "REPORT"}


def _to_health_profile_response(profile) -> HealthProfileResponse:
    return HealthProfileResponse(
        id=profile.id,
        user_id=profile.user_id,
        gender=profile.gender,
        birth_date=profile.birth_date,
        height=profile.height,
        weight=profile.weight,
        blood_type=profile.blood_type,
        medical_history=profile.medical_history,
        allergy_history=profile.allergy_history,
        family_history=profile.family_history,
        smoking=profile.smoking,
        drinking=profile.drinking,
        symptoms=profile.symptoms,
        sleep_quality=profile.sleep_quality,
        bowel_urination=profile.bowel_urination,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _to_health_indicator_response(indicator) -> HealthIndicatorResponse:
    return HealthIndicatorResponse(
        id=indicator.id,
        batch_id=indicator.batch_id,
        indicator_type=indicator.indicator_type,
        value=indicator.value,
        unit=indicator.unit,
        source=indicator.source,
        recorded_at=indicator.recorded_at,
        created_at=indicator.created_at,
    )


async def create_health_profile(
    session,
    *,
    user_id: int,
    payload: HealthProfileCreateRequest,
) -> HealthProfileResponse:
    user = await get_user_by_id(session, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    existing_profile = await get_health_profile_by_user_id(session, user_id)
    if existing_profile is not None:
        raise HTTPException(status_code=409, detail="Health profile already exists")

    try:
        profile_data = payload.model_dump()
        profile_data["user_id"] = user_id
        profile = await create_health_profile_record(session, profile_data=profile_data)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Health profile already exists") from exc
    except Exception:
        await session.rollback()
        raise

    return _to_health_profile_response(profile)


async def get_health_profile(
    session,
    *,
    user_id: int,
) -> HealthProfileResponse:
    user = await get_user_by_id(session, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    profile = await get_health_profile_by_user_id(session, user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Health profile not found")

    return _to_health_profile_response(profile)


async def create_health_indicators(
    session,
    *,
    user_id: int,
    payload: HealthIndicatorBatchCreateRequest,
) -> list[HealthIndicatorResponse]:
    user = await get_user_by_id(session, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.status != "active":
        raise HTTPException(status_code=409, detail="User is not active")

    profile = await get_health_profile_by_user_id(session, user_id)
    if profile is None:
        raise HTTPException(status_code=409, detail="Health profile is required")

    generated_batch_id = str(uuid4())
    records = []
    for item in payload.indicators:
        if item.source not in ALLOWED_HEALTH_INDICATOR_SOURCES:
            raise HTTPException(status_code=422, detail="Invalid health indicator source")

        record = item.model_dump()
        record["user_id"] = user_id
        record["batch_id"] = record["batch_id"] or generated_batch_id
        records.append(record)

    try:
        indicators = await create_health_indicator_records(session, records=records)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Health indicator creation failed") from exc
    except Exception:
        await session.rollback()
        raise

    return [_to_health_indicator_response(indicator) for indicator in indicators]


async def list_health_indicators(
    session,
    *,
    user_id: int,
    indicator_type: str | None = None,
    start_at=None,
    end_at=None,
    limit: int = 50,
) -> list[HealthIndicatorResponse]:
    user = await get_user_by_id(session, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    indicators = await list_health_indicators_by_user(
        session,
        user_id=user_id,
        indicator_type=indicator_type,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
    )
    return [_to_health_indicator_response(indicator) for indicator in indicators]


async def get_latest_health_indicators(
    session,
    *,
    user_id: int,
) -> list[HealthIndicatorResponse]:
    user = await get_user_by_id(session, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    indicators = await list_latest_health_indicators_by_user(session, user_id=user_id)
    return [_to_health_indicator_response(indicator) for indicator in indicators]
