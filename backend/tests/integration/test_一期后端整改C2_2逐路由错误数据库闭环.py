from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.modules.health_assessment.repository import (
    HealthAssessmentRepository,
    HealthAssessmentRepositoryError,
)
from app.modules.user_health.repository import (
    Slice4HealthRecordRepository,
    UserHealthRepositoryError,
)
from tests.integration.conftest import (
    _get_application_database_url,
    _get_health_record_writer_database_url,
    _get_slice5_rule_governance_writer_database_url,
    _validated_role_name,
)

pytestmark = pytest.mark.integration


def _driver_error(error: DBAPIError) -> asyncpg.PostgresError | None:
    return next(
        (
            candidate
            for candidate in (error.orig, getattr(error.orig, "__cause__", None))
            if isinstance(candidate, asyncpg.PostgresError)
        ),
        None,
    )


def test_C2_2_RealDb_Slice4业务拒绝仅翻译精确P0001(pg_database) -> None:
    async def exercise() -> None:
        engine = create_async_engine(
            _get_health_record_writer_database_url(),
            pool_pre_ping=True,
        )
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                connected_role = await session.scalar(text("SELECT current_user"))
                assert connected_role == _validated_role_name(
                    "KG_TEST_HEALTH_RECORD_WRITER_ROLE"
                )
                repository = Slice4HealthRecordRepository(session)
                with pytest.raises(UserHealthRepositoryError) as caught:
                    await repository.create_detection_report(
                        actor_user_id=1,
                        actor_context="SELF",
                        subject_member_id=UUID("018f0f47-e4a8-7cc8-98f2-88d31f8b1001"),
                        service_case_id=UUID("018f0f47-e4a8-7cc8-98f2-88d31f8b1002"),
                        enrollment_id=UUID("018f0f47-e4a8-7cc8-98f2-88d31f8b1003"),
                        requested_report_id=UUID("018f0f47-e4a8-7cc8-98f2-88d31f8b1004"),
                        report_type="UNSUPPORTED",
                        measured_at=datetime.now(UTC),
                        source_type="APP",
                        private_file_ids=[UUID("018f0f47-e4a8-7cc8-98f2-88d31f8b1005")],
                        idempotency_key=UUID("018f0f47-e4a8-7cc8-98f2-88d31f8b1006"),
                        request_digest=b"0" * 32,
                        expected_postimage_digest=b"1" * 32,
                    )
                assert caught.value.args == ("INVALID_REQUEST",)
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(exercise())


def test_C2_2_RealDb_Slice5业务拒绝与权限拒绝严格分界(pg_database) -> None:
    before_count = pg_database.fetch_value(
        "SELECT count(*) FROM public.assessment_rule_set_version"
    )

    async def exercise() -> None:
        writer_engine = create_async_engine(
            _get_slice5_rule_governance_writer_database_url(),
            pool_pre_ping=True,
        )
        application_engine = create_async_engine(
            _get_application_database_url(),
            pool_pre_ping=True,
        )
        try:
            async with AsyncSession(writer_engine, expire_on_commit=False) as session:
                connected_role = await session.scalar(text("SELECT current_user"))
                assert connected_role == _validated_role_name(
                    "KG_TEST_SLICE5_RULE_GOVERNANCE_WRITER_ROLE"
                )
                with pytest.raises(HealthAssessmentRepositoryError) as caught:
                    await HealthAssessmentRepository(session).govern_rule_set(
                        "CREATE", {"rule_set_code": "UNSUPPORTED"}
                    )
                assert caught.value.args == ("INVALID_REQUEST",)
                await session.rollback()

            async with AsyncSession(application_engine, expire_on_commit=False) as session:
                connected_role = await session.scalar(text("SELECT current_user"))
                assert connected_role == _validated_role_name("KG_TEST_APPLICATION_ROLE")
                with pytest.raises(DBAPIError) as caught:
                    await HealthAssessmentRepository(session).govern_rule_set(
                        "CREATE", {"rule_set_code": "UNSUPPORTED"}
                    )
                driver_error = _driver_error(caught.value)
                assert driver_error is not None
                assert driver_error.sqlstate == "42501"
                await session.rollback()
        finally:
            await writer_engine.dispose()
            await application_engine.dispose()

    asyncio.run(exercise())
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.assessment_rule_set_version"
    ) == before_count
