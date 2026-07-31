import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import date

import pytest


pytestmark = pytest.mark.integration


@asynccontextmanager
async def orm_session():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.sqlalchemy_mapping import map_core_model_classes

    map_core_model_classes()
    engine = create_async_engine(os.environ["KG_TEST_DATABASE_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            yield session
    finally:
        await engine.dispose()


def _user_kwargs(phone: str):
    return {
        "phone": phone,
        "password_hash": "hashed-password",
        "role": "member",
        "status": "active",
    }


def _profile_kwargs(user_id: int):
    return {
        "user_id": user_id,
        "gender": "F",
        "birth_date": date(1988, 8, 8),
        "height": 166.5,
        "weight": 58.5,
        "blood_type": "A",
        "medical_history": ["hypertension"],
        "allergy_history": [],
        "family_history": ["diabetes"],
        "smoking": "never",
        "drinking": "some",
        "symptoms": {"items": ["fatigue", "poor_sleep"], "other": "mvp validation"},
        "sleep_quality": "normal",
        "bowel_urination": "normal",
    }


async def _create_user_and_profile(phone: str):
    from app.modules.auth.models import User
    from app.modules.user_health.models import HealthProfile

    async with orm_session() as session:
        user = User(**_user_kwargs(phone))
        session.add(user)
        await session.flush()

        profile = HealthProfile(**_profile_kwargs(user.id))
        session.add(profile)
        await session.flush()
        await session.commit()

        return user.id, profile.id


def test_user_to_health_profile_allows_real_orm_flush(pg_database):
    user_id, profile_id = asyncio.run(_create_user_and_profile("13900000210"))

    rows = pg_database.fetch_rows(
        """
        SELECT id, user_id, gender, height, weight, smoking, drinking, sleep_quality, bowel_urination
        FROM health_profile
        WHERE id = $1
        """,
        profile_id,
    )

    assert rows == [
        {
            "id": profile_id,
            "user_id": user_id,
            "gender": "F",
            "height": 166.5,
            "weight": 58.5,
            "smoking": "never",
            "drinking": "some",
            "sleep_quality": "normal",
            "bowel_urination": "normal",
        }
    ]


def test_health_profile_jsonb_fields_round_trip(pg_database):
    _, profile_id = asyncio.run(_create_user_and_profile("13900000211"))

    rows = pg_database.fetch_rows(
        """
        SELECT medical_history, allergy_history, family_history, symptoms
        FROM health_profile
        WHERE id = $1
        """,
        profile_id,
    )
    row = rows[0]

    assert json.loads(row["medical_history"]) == ["hypertension"]
    assert json.loads(row["allergy_history"]) == []
    assert json.loads(row["family_history"]) == ["diabetes"]
    assert json.loads(row["symptoms"]) == {
        "items": ["fatigue", "poor_sleep"],
        "other": "mvp validation",
    }


def test_health_profile_user_id_foreign_key_is_enforced(pg_database):
    async def create_profile_without_user():
        from sqlalchemy.exc import IntegrityError

        from app.modules.user_health.models import HealthProfile

        async with orm_session() as session:
            profile = HealthProfile(**_profile_kwargs(999999991))
            session.add(profile)
            with pytest.raises(IntegrityError):
                await session.flush()
            await session.rollback()

    asyncio.run(create_profile_without_user())


def test_health_profile_user_id_unique_constraint_is_enforced(pg_database):
    async def create_duplicate_profile():
        from sqlalchemy.exc import IntegrityError

        from app.modules.auth.models import User
        from app.modules.user_health.models import HealthProfile

        async with orm_session() as session:
            user = User(**_user_kwargs("13900000212"))
            session.add(user)
            await session.flush()

            first_profile = HealthProfile(**_profile_kwargs(user.id))
            session.add(first_profile)
            await session.flush()

            duplicate_profile = HealthProfile(**_profile_kwargs(user.id))
            session.add(duplicate_profile)
            with pytest.raises(IntegrityError):
                await session.flush()
            await session.rollback()

    asyncio.run(create_duplicate_profile())
