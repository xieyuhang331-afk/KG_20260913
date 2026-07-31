import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException


class HealthSummaryServiceTests(unittest.IsolatedAsyncioTestCase):
    class FakeSession:
        def __init__(self):
            self.commit_called = False
            self.rollback_called = False

        async def commit(self):
            self.commit_called = True

        async def rollback(self):
            self.rollback_called = True

    def _user(self):
        return SimpleNamespace(
            id=1001,
            status="active",
            tenant_id=2001,
            password_hash="secret",
            id_card="110101199001011234",
            real_name="Zhang San",
        )

    def _profile(self):
        return SimpleNamespace(
            gender="F",
            birth_date=date(1990, 1, 1),
            height=Decimal("165.5"),
            weight=Decimal("55.0"),
            blood_type="A",
            medical_history=["hypertension"],
            allergy_history=None,
            family_history={"items": ["diabetes"]},
            symptoms=["fatigue"],
            smoking="never",
            drinking="none",
            sleep_quality="normal",
            bowel_urination="normal",
        )

    def _indicator(self, indicator_type="systolic_bp"):
        return SimpleNamespace(
            indicator_type=indicator_type,
            value=Decimal("120.00"),
            unit="mmHg",
            source="APP",
            recorded_at=datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc),
            created_at=datetime(2026, 7, 29, 8, 1, tzinfo=timezone.utc),
        )

    async def test_get_health_summary_returns_summary_when_user_exists(self):
        from app.modules.health_analysis.aggregation import build_health_summary
        from app.modules.health_analysis.service import get_health_summary

        session = self.FakeSession()
        user = self._user()
        profile = self._profile()
        indicators = [self._indicator()]

        with (
            patch("app.modules.health_analysis.service.get_summary_user", new=AsyncMock(return_value=user)),
            patch("app.modules.health_analysis.service.get_summary_profile", new=AsyncMock(return_value=profile)),
            patch(
                "app.modules.health_analysis.service.get_latest_indicators_for_summary",
                new=AsyncMock(return_value=indicators),
            ),
            patch(
                "app.modules.health_analysis.service.build_health_summary",
                new=Mock(wraps=build_health_summary),
            ) as aggregation_mock,
        ):
            summary = await get_health_summary(session, user_id=1001)

        aggregation_mock.assert_called_once_with(user=user, health_profile=profile, latest_indicators=indicators)
        self.assertEqual(summary.user.user_id, 1001)
        self.assertEqual(summary.latest_indicators[0].indicator_type, "systolic_bp")
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_get_health_summary_returns_404_when_user_missing(self):
        from app.modules.health_analysis.service import get_health_summary

        session = self.FakeSession()
        profile_mock = AsyncMock()
        indicators_mock = AsyncMock()

        with (
            patch("app.modules.health_analysis.service.get_summary_user", new=AsyncMock(return_value=None)),
            patch("app.modules.health_analysis.service.get_summary_profile", new=profile_mock),
            patch("app.modules.health_analysis.service.get_latest_indicators_for_summary", new=indicators_mock),
        ):
            with self.assertRaises(HTTPException) as context:
                await get_health_summary(session, user_id=1001)

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "User not found")
        profile_mock.assert_not_awaited()
        indicators_mock.assert_not_awaited()
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_get_health_summary_returns_summary_when_profile_missing(self):
        from app.modules.health_analysis.service import get_health_summary

        session = self.FakeSession()

        with (
            patch("app.modules.health_analysis.service.get_summary_user", new=AsyncMock(return_value=self._user())),
            patch("app.modules.health_analysis.service.get_summary_profile", new=AsyncMock(return_value=None)),
            patch("app.modules.health_analysis.service.get_latest_indicators_for_summary", new=AsyncMock(return_value=[])),
        ):
            summary = await get_health_summary(session, user_id=1001)

        self.assertFalse(summary.profile.exists)
        self.assertFalse(summary.data_completeness.profile_completed)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_get_health_summary_returns_empty_indicator_list_when_no_indicators(self):
        from app.modules.health_analysis.service import get_health_summary

        session = self.FakeSession()

        with (
            patch("app.modules.health_analysis.service.get_summary_user", new=AsyncMock(return_value=self._user())),
            patch("app.modules.health_analysis.service.get_summary_profile", new=AsyncMock(return_value=self._profile())),
            patch("app.modules.health_analysis.service.get_latest_indicators_for_summary", new=AsyncMock(return_value=[])),
        ):
            summary = await get_health_summary(session, user_id=1001)

        self.assertEqual(summary.latest_indicators, [])
        self.assertIsNone(summary.indicator_updated_at)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_get_health_summary_calls_repositories_with_session_and_user_id(self):
        from app.modules.health_analysis.service import get_health_summary

        session = self.FakeSession()
        user_mock = AsyncMock(return_value=self._user())
        profile_mock = AsyncMock(return_value=self._profile())
        indicators_mock = AsyncMock(return_value=[self._indicator()])

        with (
            patch("app.modules.health_analysis.service.get_summary_user", new=user_mock),
            patch("app.modules.health_analysis.service.get_summary_profile", new=profile_mock),
            patch("app.modules.health_analysis.service.get_latest_indicators_for_summary", new=indicators_mock),
        ):
            await get_health_summary(session, user_id=1001)

        user_mock.assert_awaited_once_with(session, user_id=1001)
        profile_mock.assert_awaited_once_with(session, user_id=1001)
        indicators_mock.assert_awaited_once_with(session, user_id=1001)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_get_health_summary_does_not_expose_sensitive_user_fields(self):
        from app.modules.health_analysis.service import get_health_summary

        session = self.FakeSession()

        with (
            patch("app.modules.health_analysis.service.get_summary_user", new=AsyncMock(return_value=self._user())),
            patch("app.modules.health_analysis.service.get_summary_profile", new=AsyncMock(return_value=self._profile())),
            patch("app.modules.health_analysis.service.get_latest_indicators_for_summary", new=AsyncMock(return_value=[])),
        ):
            summary = await get_health_summary(session, user_id=1001)

        data = summary.model_dump()
        serialized_keys = set(data)
        serialized_keys.update(data["user"])
        serialized_keys.update(data["profile"])

        self.assertNotIn("password_hash", serialized_keys)
        self.assertNotIn("id_card", serialized_keys)
        self.assertNotIn("real_name", serialized_keys)
