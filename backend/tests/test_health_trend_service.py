import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException


class HealthTrendServiceTests(unittest.IsolatedAsyncioTestCase):
    class FakeSession:
        def __init__(self):
            self.commit_called = False
            self.rollback_called = False

        async def commit(self):
            self.commit_called = True

        async def rollback(self):
            self.rollback_called = True

    def _user(self):
        return SimpleNamespace(id=1001, status="active", tenant_id=2001)

    def _indicator(self, recorded_at=None):
        return SimpleNamespace(
            indicator_type="systolic_bp",
            value=Decimal("120.00"),
            unit="mmHg",
            source="APP",
            recorded_at=recorded_at or datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc),
        )

    async def test_get_health_trend_returns_trend_successfully(self):
        from app.modules.health_analysis.aggregation import build_health_trend
        from app.modules.health_analysis.service import get_health_trend

        session = self.FakeSession()
        start_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
        end_at = datetime(2026, 7, 29, tzinfo=timezone.utc)
        indicators = [self._indicator()]

        with (
            patch("app.modules.health_analysis.service.get_summary_user", new=AsyncMock(return_value=self._user())),
            patch("app.modules.health_analysis.service.list_indicator_trend_points", new=AsyncMock(return_value=indicators)),
            patch("app.modules.health_analysis.service.build_health_trend", new=Mock(wraps=build_health_trend)) as aggregation_mock,
        ):
            trend = await get_health_trend(
                session,
                user_id=1001,
                indicator_type="systolic_bp",
                start_at=start_at,
                end_at=end_at,
                limit=50,
            )

        aggregation_mock.assert_called_once_with(indicator_type="systolic_bp", indicators=indicators)
        self.assertEqual(trend.indicator_type, "systolic_bp")
        self.assertEqual(trend.display_name, "收缩压")
        self.assertEqual(len(trend.points), 1)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_get_health_trend_returns_404_when_user_missing(self):
        from app.modules.health_analysis.service import get_health_trend

        session = self.FakeSession()
        repository_mock = AsyncMock()

        with (
            patch("app.modules.health_analysis.service.get_summary_user", new=AsyncMock(return_value=None)),
            patch("app.modules.health_analysis.service.list_indicator_trend_points", new=repository_mock),
        ):
            with self.assertRaises(HTTPException) as context:
                await get_health_trend(session, user_id=1001, indicator_type="systolic_bp")

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "User not found")
        repository_mock.assert_not_awaited()
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_get_health_trend_returns_422_for_unknown_indicator(self):
        from app.modules.health_analysis.service import get_health_trend

        session = self.FakeSession()
        repository_mock = AsyncMock()

        with (
            patch("app.modules.health_analysis.service.get_summary_user", new=AsyncMock(return_value=self._user())),
            patch("app.modules.health_analysis.service.list_indicator_trend_points", new=repository_mock),
        ):
            with self.assertRaises(HTTPException) as context:
                await get_health_trend(session, user_id=1001, indicator_type="unknown_metric")

        self.assertEqual(context.exception.status_code, 422)
        self.assertEqual(context.exception.detail, "Unknown indicator_type")
        repository_mock.assert_not_awaited()
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_get_health_trend_defaults_to_30_day_window_and_default_limit(self):
        from app.modules.health_analysis.service import get_health_trend

        session = self.FakeSession()
        repository_mock = AsyncMock(return_value=[])

        with (
            patch("app.modules.health_analysis.service.get_summary_user", new=AsyncMock(return_value=self._user())),
            patch("app.modules.health_analysis.service.list_indicator_trend_points", new=repository_mock),
        ):
            await get_health_trend(session, user_id=1001, indicator_type="systolic_bp")

        call = repository_mock.await_args
        self.assertIs(call.args[0], session)
        self.assertEqual(call.kwargs["user_id"], 1001)
        self.assertEqual(call.kwargs["indicator_type"], "systolic_bp")
        self.assertEqual(call.kwargs["end_at"] - call.kwargs["start_at"], timedelta(days=30))
        self.assertEqual(call.kwargs["limit"], 200)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_get_health_trend_returns_422_when_start_after_end(self):
        from app.modules.health_analysis.service import get_health_trend

        session = self.FakeSession()
        repository_mock = AsyncMock()

        with (
            patch("app.modules.health_analysis.service.get_summary_user", new=AsyncMock(return_value=self._user())),
            patch("app.modules.health_analysis.service.list_indicator_trend_points", new=repository_mock),
        ):
            with self.assertRaises(HTTPException) as context:
                await get_health_trend(
                    session,
                    user_id=1001,
                    indicator_type="systolic_bp",
                    start_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
                    end_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
                )

        self.assertEqual(context.exception.status_code, 422)
        self.assertEqual(context.exception.detail, "Invalid time range")
        repository_mock.assert_not_awaited()
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_get_health_trend_calls_repository_with_explicit_parameters(self):
        from app.modules.health_analysis.service import get_health_trend

        session = self.FakeSession()
        start_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
        end_at = datetime(2026, 7, 29, tzinfo=timezone.utc)
        repository_mock = AsyncMock(return_value=[self._indicator()])

        with (
            patch("app.modules.health_analysis.service.get_summary_user", new=AsyncMock(return_value=self._user())),
            patch("app.modules.health_analysis.service.list_indicator_trend_points", new=repository_mock),
        ):
            await get_health_trend(
                session,
                user_id=1001,
                indicator_type="systolic_bp",
                start_at=start_at,
                end_at=end_at,
                limit=50,
            )

        repository_mock.assert_awaited_once_with(
            session,
            user_id=1001,
            indicator_type="systolic_bp",
            start_at=start_at,
            end_at=end_at,
            limit=50,
        )
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_get_health_trend_calls_aggregation(self):
        from app.modules.health_analysis.service import get_health_trend
        from app.modules.health_analysis.schemas import HealthTrend

        session = self.FakeSession()
        expected_trend = HealthTrend(
            indicator_type="systolic_bp",
            display_name="收缩压",
            category="blood_pressure",
            unit="mmHg",
            points=[],
        )
        aggregation_mock = Mock(return_value=expected_trend)

        with (
            patch("app.modules.health_analysis.service.get_summary_user", new=AsyncMock(return_value=self._user())),
            patch("app.modules.health_analysis.service.list_indicator_trend_points", new=AsyncMock(return_value=[])),
            patch("app.modules.health_analysis.service.build_health_trend", new=aggregation_mock),
        ):
            trend = await get_health_trend(session, user_id=1001, indicator_type="systolic_bp")

        aggregation_mock.assert_called_once_with(indicator_type="systolic_bp", indicators=[])
        self.assertIs(trend, expected_trend)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)
