import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.dialects import postgresql


class HealthIndicatorRepositoryTests(unittest.IsolatedAsyncioTestCase):
    class MappingResult:
        def __init__(self, values):
            self.values = values

        def mappings(self):
            return self

        def all(self):
            return self.values

        def one(self):
            assert len(self.values) == 1
            return self.values[0]

    class FakeSession:
        def __init__(self, execute_results=None):
            self.commit_called = False
            self.rollback_called = False
            self.statements = []
            self.execute_results = list(execute_results or [])

        async def execute(self, statement):
            self.statements.append(statement)
            if self.execute_results:
                return self.execute_results.pop(0)
            return HealthIndicatorRepositoryTests.MappingResult([])

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

    async def test_create_health_indicator_records_uses_bounded_function_for_each_record(self):
        from app.modules.user_health.repository import create_health_indicator_records

        records = [
            self._record_data(indicator_type="systolic_bp"),
            self._record_data(indicator_type="diastolic_bp"),
        ]
        session = self.FakeSession(
            execute_results=[
                self.MappingResult([{"id": index, **record}])
                for index, record in enumerate(records, start=1)
            ]
        )

        indicators = await create_health_indicator_records(session, records=records)

        self.assertEqual(len(indicators), 2)
        self.assertEqual([indicator.id for indicator in indicators], [1, 2])
        self.assertEqual(indicators[0].indicator_type, "systolic_bp")
        self.assertEqual(indicators[1].indicator_type, "diastolic_bp")
        self.assertEqual(len(session.statements), 2)
        for statement in session.statements:
            sql = self._compiled_sql(statement)
            self.assertIn("r4_member_legacy_health_indicator_create_v1", sql)
            params = statement.compile().params
            self.assertEqual(params["actor_user_id"], 1001)
            self.assertEqual(params["target_user_id"], 1001)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_list_health_indicators_by_user_filters_user_and_orders_by_recorded_at_desc(self):
        from app.modules.user_health.repository import list_health_indicators_by_user

        indicator = {"id": 1, **self._record_data()}
        indicator.pop("user_id")
        session = self.FakeSession(execute_results=[self.MappingResult([indicator])])

        result = await list_health_indicators_by_user(session, user_id=1001)

        self.assertEqual(result[0].indicator_type, "systolic_bp")
        sql = self._compiled_sql(session.statements[0])
        self.assertIn("r4_member_legacy_health_indicator_history_v1", sql)
        self.assertIn("(1001,1001", sql)
        self.assertTrue(sql.rstrip().endswith("50)"))
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
        self.assertIn("r4_member_legacy_health_indicator_history_v1", sql)
        params = session.statements[0].compile().params
        self.assertEqual(params["actor_user_id"], 1001)
        self.assertEqual(params["target_user_id"], 1001)
        self.assertEqual(params["indicator_type"], "systolic_bp")
        self.assertEqual(params["start_at"], start_at)
        self.assertEqual(params["end_at"], end_at)
        self.assertEqual(params["limit"], 10)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_list_latest_health_indicators_by_user_uses_distinct_on_indicator_type(self):
        from app.modules.user_health.repository import list_latest_health_indicators_by_user

        latest = {"id": 1, **self._record_data()}
        latest.pop("user_id")
        session = self.FakeSession(execute_results=[self.MappingResult([latest])])

        result = await list_latest_health_indicators_by_user(session, user_id=1001)

        self.assertEqual(result[0].indicator_type, "systolic_bp")
        sql = self._compiled_sql(session.statements[0])
        self.assertIn("r4_member_legacy_health_indicator_latest_v1(1001,1001)", sql)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_list_health_indicators_by_user_returns_empty_result(self):
        from app.modules.user_health.repository import list_health_indicators_by_user

        session = self.FakeSession(execute_results=[self.MappingResult([])])

        result = await list_health_indicators_by_user(session, user_id=1001)

        self.assertEqual(result, [])
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_list_latest_health_indicators_by_user_returns_empty_result(self):
        from app.modules.user_health.repository import list_latest_health_indicators_by_user

        session = self.FakeSession(execute_results=[self.MappingResult([])])

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
