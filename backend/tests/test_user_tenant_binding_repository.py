import unittest
from types import SimpleNamespace


class UserTenantBindingRepositoryTests(unittest.IsolatedAsyncioTestCase):
    class ScalarResult:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class MappingResult:
        def __init__(self, value):
            self.value = value

        def mappings(self):
            return self

        def one_or_none(self):
            return self.value

    class FakeSession:
        def __init__(self, result):
            self.result = result
            self.statement = None
            self.flush_called = False
            self.commit_called = False
            self.rollback_called = False

        async def execute(self, statement):
            self.statement = statement
            return self.result

        async def flush(self):
            self.flush_called = True

        async def commit(self):
            self.commit_called = True

        async def rollback(self):
            self.rollback_called = True

    def _compiled_sql(self, statement) -> str:
        return str(statement.compile(compile_kwargs={"literal_binds": True}))

    async def test_get_user_for_tenant_binding_update_uses_row_lock(self):
        from app.modules.auth.repository import get_user_for_tenant_binding_update

        user = SimpleNamespace(id=1001)
        session = self.FakeSession(self.ScalarResult(user))

        result = await get_user_for_tenant_binding_update(session, 1001)

        self.assertIs(result, user)
        self.assertIn("FOR UPDATE", self._compiled_sql(session.statement))
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_update_user_tenant_binding_writes_tenant_id_and_flushes(self):
        from app.modules.auth.repository import update_user_tenant_binding

        user = SimpleNamespace(id=1001, tenant_id=None)
        session = self.FakeSession(None)

        result = await update_user_tenant_binding(session, user, tenant_id=501)

        self.assertIs(result, user)
        self.assertEqual(user.tenant_id, 501)
        self.assertTrue(session.flush_called)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_get_tenant_by_id_for_binding_returns_tenant_summary(self):
        from app.modules.tenant.repository import get_tenant_by_id_for_binding

        row = {
            "tenant_id": 501,
            "tenant_code": "TACTIVE001",
            "name": "Kanglin West Lake Store",
            "status": "active",
        }
        session = self.FakeSession(self.MappingResult(row))

        result = await get_tenant_by_id_for_binding(session, 501)

        self.assertEqual(result, row)
        self.assertIn("tenant.id = 501", self._compiled_sql(session.statement))
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_get_tenant_by_id_for_binding_returns_none_when_missing(self):
        from app.modules.tenant.repository import get_tenant_by_id_for_binding

        session = self.FakeSession(self.MappingResult(None))

        result = await get_tenant_by_id_for_binding(session, 9999)

        self.assertIsNone(result)

    async def test_repository_signatures_do_not_accept_current_user(self):
        import inspect
        from app.modules.auth.repository import (
            get_user_for_tenant_binding_update,
            update_user_tenant_binding,
        )
        from app.modules.tenant.repository import get_tenant_by_id_for_binding

        self.assertEqual(list(inspect.signature(get_user_for_tenant_binding_update).parameters), ["session", "user_id"])
        self.assertEqual(list(inspect.signature(update_user_tenant_binding).parameters), ["session", "user", "tenant_id"])
        self.assertEqual(list(inspect.signature(get_tenant_by_id_for_binding).parameters), ["session", "tenant_id"])
