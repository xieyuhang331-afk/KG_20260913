from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
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
    MemberSelfHealthProfileData,
    MemberSelfHealthProfileResult,
    MemberSelfHealthProfileWriteRequest,
)


ALLOWED_HEALTH_INDICATOR_SOURCES = {"APP", "STORE", "DEVICE", "REPORT"}


def _member_profile_gender(value: str) -> str:
    normalized = {"M": "male", "F": "female"}.get(value, value)
    if normalized not in {"male", "female"}:
        raise HTTPException(status_code=409, detail="Health profile is unavailable for current member")
    return normalized


def _member_profile_state(profile) -> str:
    if profile is None:
        return "NOT_CREATED"
    if profile.height is None or profile.weight is None:
        return "INCOMPLETE"
    return "COMPLETE"


def _member_profile_bmi(profile) -> Decimal | None:
    if profile is None or profile.height is None or profile.weight is None:
        return None
    height_m = Decimal(profile.height) / Decimal("100")
    if height_m <= 0:
        return None
    return (Decimal(profile.weight) / (height_m * height_m)).quantize(
        Decimal("0.1"),
        rounding=ROUND_HALF_UP,
    )


def _to_member_self_profile_result(profile, *, outcome=None) -> MemberSelfHealthProfileResult:
    if profile is None:
        return MemberSelfHealthProfileResult(
            state="NOT_CREATED",
            version=None,
            profile=None,
            bmi=None,
            outcome=outcome,
        )
    return MemberSelfHealthProfileResult(
        state=_member_profile_state(profile),
        version=profile.updated_at,
        profile=MemberSelfHealthProfileData(
            gender=_member_profile_gender(profile.gender),
            birth_date=profile.birth_date,
            height=profile.height,
            weight=profile.weight,
            blood_type=profile.blood_type,
        ),
        bmi=_member_profile_bmi(profile),
        outcome=outcome,
    )


def _ensure_current_verified_member(user) -> None:
    if (
        user is None
        or user.role != "member"
        or user.status != "active"
        or user.verify_status != "verified"
    ):
        raise HTTPException(
            status_code=409,
            detail="Health profile is unavailable for current member",
        )


def _member_profile_matches(profile, payload: MemberSelfHealthProfileWriteRequest) -> bool:
    return bool(
        profile is not None
        and _member_profile_gender(profile.gender) == payload.gender
        and profile.birth_date == payload.birth_date
        and Decimal(profile.height) == payload.height
        and Decimal(profile.weight) == payload.weight
        and profile.blood_type == payload.blood_type
    )


async def get_member_self_health_profile(
    session,
    *,
    user_id: int,
) -> MemberSelfHealthProfileResult:
    user = await get_user_by_id(session, user_id)
    _ensure_current_verified_member(user)
    profile = await get_health_profile_by_user_id(session, user_id)
    return _to_member_self_profile_result(profile)


async def put_member_self_health_profile(
    session,
    *,
    confirmation_session_factory_provider,
    user_id: int,
    payload: MemberSelfHealthProfileWriteRequest,
) -> MemberSelfHealthProfileResult:
    del confirmation_session_factory_provider  # Checkpoint B owns fresh-session confirmation.
    user = await get_user_by_id(session, user_id)
    _ensure_current_verified_member(user)
    profile = await get_health_profile_by_user_id(session, user_id)

    if profile is not None and _member_profile_matches(profile, payload):
        return _to_member_self_profile_result(profile, outcome="REPLAYED")

    if profile is not None:
        if payload.expected_version is None or profile.updated_at != payload.expected_version:
            raise HTTPException(status_code=409, detail="HEALTH_PROFILE_VERSION_CONFLICT")
        profile.gender = payload.gender
        profile.birth_date = payload.birth_date
        profile.height = payload.height
        profile.weight = payload.weight
        profile.blood_type = payload.blood_type
        profile.updated_at = datetime.now(timezone.utc)
        outcome = "UPDATED"
    else:
        if payload.expected_version is not None:
            raise HTTPException(status_code=409, detail="HEALTH_PROFILE_VERSION_CONFLICT")
        profile = await create_health_profile_record(
            session,
            profile_data={
                "user_id": user_id,
                "gender": payload.gender,
                "birth_date": payload.birth_date,
                "height": payload.height,
                "weight": payload.weight,
                "blood_type": payload.blood_type,
                "updated_at": datetime.now(timezone.utc),
            },
        )
        outcome = "CREATED"

    try:
        await session.commit()
    except asyncio.CancelledError:
        await session.rollback()
        raise
    except Exception:
        await session.rollback()
        raise
    return _to_member_self_profile_result(profile, outcome=outcome)


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
