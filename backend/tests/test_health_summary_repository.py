import unittest

from sqlalchemy.dialects import postgresql


class HealthSummaryRepositoryTests(unittest.IsolatedAsyncioTestCase):
    class ScalarOneResult:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

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

    async def test_get_summary_user_returns_user_when_exists(self):
        from app.modules.auth.models import User
        from app.modules.health_analysis.repository import get_summary_user

        user = User()
        user.id = 1001
        session = self.FakeSession(self.ScalarOneResult(user))

        result = await get_summary_user(session, user_id=1001)

        self.assertIs(result, user)
        sql = self._compiled_sql(session.statements[0])
        self.assertIn('"user".id = 1001', sql)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_get_summary_user_returns_none_when_missing(self):
        from app.modules.health_analysis.repository import get_summary_user

        result = await get_summary_user(self.FakeSession(self.ScalarOneResult(None)), user_id=1001)

        self.assertIsNone(result)

    async def test_get_summary_profile_returns_profile_when_exists(self):
        from app.modules.health_analysis.repository import get_summary_profile
        from app.modules.user_health.models import HealthProfile

        profile = HealthProfile()
        profile.user_id = 1001
        session = self.FakeSession(self.ScalarOneResult(profile))

        result = await get_summary_profile(session, user_id=1001)

        self.assertIs(result, profile)
        sql = self._compiled_sql(session.statements[0])
        self.assertIn("health_profile.user_id = 1001", sql)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_get_summary_profile_returns_none_when_missing(self):
        from app.modules.health_analysis.repository import get_summary_profile

        result = await get_summary_profile(self.FakeSession(self.ScalarOneResult(None)), user_id=1001)

        self.assertIsNone(result)

    async def test_get_latest_indicators_for_summary_returns_latest_per_indicator_type(self):
        from app.modules.health_analysis.repository import get_latest_indicators_for_summary
        from app.modules.user_health.models import HealthIndicator

        indicator = HealthIndicator()
        indicator.indicator_type = "systolic_bp"
        session = self.FakeSession(self.ScalarListResult([indicator]))

        result = await get_latest_indicators_for_summary(session, user_id=1001)

        self.assertEqual(result, [indicator])
        sql = self._compiled_sql(session.statements[0])
        self.assertIn("SELECT DISTINCT ON (health_indicator.indicator_type)", sql)
        self.assertIn("health_indicator.user_id = 1001", sql)
        self.assertIn(
            "ORDER BY health_indicator.indicator_type, health_indicator.recorded_at DESC, health_indicator.id DESC",
            sql,
        )
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_get_latest_indicators_for_summary_returns_empty_result(self):
        from app.modules.health_analysis.repository import get_latest_indicators_for_summary

        session = self.FakeSession(self.ScalarListResult([]))

        result = await get_latest_indicators_for_summary(session, user_id=1001)

        self.assertEqual(result, [])
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_repository_signatures_do_not_accept_current_user_or_permissions(self):
        import inspect
        from app.modules.health_analysis.repository import (
            get_latest_indicators_for_summary,
            get_summary_profile,
            get_summary_user,
        )

        self.assertEqual(list(inspect.signature(get_summary_user).parameters), ["session", "user_id"])
        self.assertEqual(list(inspect.signature(get_summary_profile).parameters), ["session", "user_id"])
        self.assertEqual(list(inspect.signature(get_latest_indicators_for_summary).parameters), ["session", "user_id"])
