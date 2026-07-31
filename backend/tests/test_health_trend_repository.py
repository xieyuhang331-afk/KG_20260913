import unittest
from datetime import datetime, timezone

from sqlalchemy.dialects import postgresql


class HealthTrendRepositoryTests(unittest.IsolatedAsyncioTestCase):
    class ScalarListResult:
        def __init__(self, values):
            self.values = values

        def scalars(self):
            return self

        def all(self):
            return self.values

    class FakeSession:
        def __init__(self, execute_result):
            self.execute_result = execute_result
            self.statements = []
            self.commit_called = False
            self.rollback_called = False

        async def execute(self, statement):
            self.statements.append(statement)
            return self.execute_result

        async def commit(self):
            self.commit_called = True

        async def rollback(self):
            self.rollback_called = True

    def _compiled_sql(self, statement) -> str:
        return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

    async def test_list_indicator_trend_points_returns_matching_indicators(self):
        from app.modules.health_analysis.repository import list_indicator_trend_points
        from app.modules.user_health.models import HealthIndicator

        indicator = HealthIndicator()
        indicator.indicator_type = "systolic_bp"
        session = self.FakeSession(self.ScalarListResult([indicator]))

        result = await list_indicator_trend_points(
            session,
            user_id=1001,
            indicator_type="systolic_bp",
            start_at=None,
            end_at=None,
            limit=200,
        )

        self.assertEqual(result, [indicator])
        sql = self._compiled_sql(session.statements[0])
        self.assertIn("health_indicator.user_id = 1001", sql)
        self.assertIn("health_indicator.indicator_type = 'systolic_bp'", sql)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_list_indicator_trend_points_returns_empty_result(self):
        from app.modules.health_analysis.repository import list_indicator_trend_points

        session = self.FakeSession(self.ScalarListResult([]))

        result = await list_indicator_trend_points(
            session,
            user_id=1001,
            indicator_type="systolic_bp",
            start_at=None,
            end_at=None,
            limit=200,
        )

        self.assertEqual(result, [])
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_list_indicator_trend_points_applies_time_range_filters(self):
        from app.modules.health_analysis.repository import list_indicator_trend_points

        session = self.FakeSession(self.ScalarListResult([]))
        start_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
        end_at = datetime(2026, 7, 29, tzinfo=timezone.utc)

        await list_indicator_trend_points(
            session,
            user_id=1001,
            indicator_type="systolic_bp",
            start_at=start_at,
            end_at=end_at,
            limit=200,
        )

        sql = self._compiled_sql(session.statements[0])
        self.assertIn("health_indicator.recorded_at >= '2026-07-01", sql)
        self.assertIn("health_indicator.recorded_at <= '2026-07-29", sql)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_list_indicator_trend_points_applies_limit(self):
        from app.modules.health_analysis.repository import list_indicator_trend_points

        session = self.FakeSession(self.ScalarListResult([]))

        await list_indicator_trend_points(
            session,
            user_id=1001,
            indicator_type="systolic_bp",
            start_at=None,
            end_at=None,
            limit=50,
        )

        sql = self._compiled_sql(session.statements[0])
        self.assertIn("LIMIT 50", sql)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_list_indicator_trend_points_orders_by_recorded_at_ascending(self):
        from app.modules.health_analysis.repository import list_indicator_trend_points

        session = self.FakeSession(self.ScalarListResult([]))

        await list_indicator_trend_points(
            session,
            user_id=1001,
            indicator_type="systolic_bp",
            start_at=None,
            end_at=None,
            limit=200,
        )

        sql = self._compiled_sql(session.statements[0])
        self.assertIn("ORDER BY health_indicator.recorded_at ASC", sql)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_repository_signature_does_not_accept_current_user_or_permissions(self):
        import inspect
        from app.modules.health_analysis.repository import list_indicator_trend_points

        self.assertEqual(
            list(inspect.signature(list_indicator_trend_points).parameters),
            ["session", "user_id", "indicator_type", "start_at", "end_at", "limit"],
        )
