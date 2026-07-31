import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.dialects import postgresql


class HealthIndicatorRepositoryTests(unittest.IsolatedAsyncioTestCase):
    class ScalarListResult:
        def __init__(self, values):
            self.values = values

        def scalars(self):
            return self

        def all(self):
            return self.values

    class FakeSession:
        def __init__(self, execute_result=None):
            self.added_all = None
            self.flush_called = False
            self.commit_called = False
            self.rollback_called = False
            self.statements = []
            self.execute_result = execute_result or HealthIndicatorRepositoryTests.ScalarListResult([])

        def add_all(self, values):
            self.added_all = values

        async def flush(self):
            self.flush_called = True
            for index, value in enumerate(self.added_all or [], start=1):
                value.id = index

        async def execute(self, statement):
            self.statements.append(statement)
            return self.execute_result

        async def commit(self):
            self.commit_called = True

        async def rollback(self):
            self.rollback_called = True

    def _compiled_sql(self, statement) -> str:
        return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

    def _record_data(self, indicator_type="systolic_bp", recorded_at=None):
        return {
            "user_id": 1001,
            "batch_id": "batch-001",
            "indicator_type": indicator_type,
            "value": Decimal("120.50"),
            "unit": "mmHg",
            "source": "STORE",
            "recorded_at": recorded_at or datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
        }

    async def test_create_health_indicator_records_creates_orm_objects_and_flushes(self):
        from app.modules.user_health.models import HealthIndicator
        from app.modules.user_health.repository import create_health_indicator_records

        session = self.FakeSession()
        records = [
            self._record_data(indicator_type="systolic_bp"),
            self._record_data(indicator_type="diastolic_bp"),
        ]

        indicators = await create_health_indicator_records(session, records=records)

        self.assertEqual(len(indicators), 2)
        self.assertTrue(all(isinstance(indicator, HealthIndicator) for indicator in indicators))
        self.assertIs(session.added_all, indicators)
        self.assertTrue(session.flush_called)
        self.assertEqual([indicator.id for indicator in indicators], [1, 2])
        self.assertEqual(indicators[0].indicator_type, "systolic_bp")
        self.assertEqual(indicators[1].indicator_type, "diastolic_bp")
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_list_health_indicators_by_user_filters_user_and_orders_by_recorded_at_desc(self):
        from app.modules.user_health.models import HealthIndicator
        from app.modules.user_health.repository import list_health_indicators_by_user

        indicator = HealthIndicator()
        indicator.user_id = 1001
        session = self.FakeSession(execute_result=self.ScalarListResult([indicator]))

        result = await list_health_indicators_by_user(session, user_id=1001)

        self.assertEqual(result, [indicator])
        sql = self._compiled_sql(session.statements[0])
        self.assertIn("health_indicator.user_id = 1001", sql)
        self.assertIn("ORDER BY health_indicator.recorded_at DESC", sql)
        self.assertIn("LIMIT 50", sql)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_list_health_indicators_by_user_applies_type_time_and_limit_filters(self):
        from app.modules.user_health.repository import list_health_indicators_by_user

        session = self.FakeSession()
        start_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
        end_at = start_at + timedelta(days=7)

        await list_health_indicators_by_user(
            session,
            user_id=1001,
            indicator_type="systolic_bp",
            start_at=start_at,
            end_at=end_at,
            limit=10,
        )

        sql = self._compiled_sql(session.statements[0])
        self.assertIn("health_indicator.user_id = 1001", sql)
        self.assertIn("health_indicator.indicator_type = 'systolic_bp'", sql)
        self.assertIn("health_indicator.recorded_at >= '2026-07-01", sql)
        self.assertIn("health_indicator.recorded_at <= '2026-07-08", sql)
        self.assertIn("LIMIT 10", sql)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_list_latest_health_indicators_by_user_uses_distinct_on_indicator_type(self):
        from app.modules.user_health.models import HealthIndicator
        from app.modules.user_health.repository import list_latest_health_indicators_by_user

        latest = HealthIndicator()
        latest.indicator_type = "systolic_bp"
        session = self.FakeSession(execute_result=self.ScalarListResult([latest]))

        result = await list_latest_health_indicators_by_user(session, user_id=1001)

        self.assertEqual(result, [latest])
        sql = self._compiled_sql(session.statements[0])
        self.assertIn("SELECT DISTINCT ON (health_indicator.indicator_type)", sql)
        self.assertIn("health_indicator.user_id = 1001", sql)
        self.assertIn(
            "ORDER BY health_indicator.indicator_type, health_indicator.recorded_at DESC, health_indicator.id DESC",
            sql,
        )
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_list_health_indicators_by_user_returns_empty_result(self):
        from app.modules.user_health.repository import list_health_indicators_by_user

        session = self.FakeSession(execute_result=self.ScalarListResult([]))

        result = await list_health_indicators_by_user(session, user_id=1001)

        self.assertEqual(result, [])
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_list_latest_health_indicators_by_user_returns_empty_result(self):
        from app.modules.user_health.repository import list_latest_health_indicators_by_user

        session = self.FakeSession(execute_result=self.ScalarListResult([]))

        result = await list_latest_health_indicators_by_user(session, user_id=1001)

        self.assertEqual(result, [])
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_repository_signatures_do_not_accept_current_user_or_permissions(self):
        import inspect
        from app.modules.user_health.repository import (
            create_health_indicator_records,
            list_health_indicators_by_user,
            list_latest_health_indicators_by_user,
        )

        self.assertEqual(list(inspect.signature(create_health_indicator_records).parameters), ["session", "records"])
        self.assertEqual(
            list(inspect.signature(list_health_indicators_by_user).parameters),
            ["session", "user_id", "indicator_type", "start_at", "end_at", "limit"],
        )
        self.assertEqual(
            list(inspect.signature(list_latest_health_indicators_by_user).parameters),
            ["session", "user_id"],
        )
