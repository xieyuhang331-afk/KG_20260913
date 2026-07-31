import inspect
import unittest
from datetime import datetime, timezone


class MyTenantApplicationsRepositoryTests(unittest.IsolatedAsyncioTestCase):
    class ScalarResult:
        def scalar_one(self):
            return 1

    class MappingResult:
        def mappings(self):
            return self

        def all(self):
            return [
                {
                    "tenant_id": 8101,
                    "tenant_code": "P1-TENANT-8101",
                    "name": "P1 Pending Store 8101",
                    "status": "pending",
                    "province": "Zhejiang",
                    "city": "Hangzhou",
                    "contact_name": "Contact 8101",
                    "contact_phone": "13800008101",
                    "submitted_at": datetime(2026, 7, 30, tzinfo=timezone.utc),
                    "reviewed_at": None,
                    "approved_at": None,
                    "reject_reason": None,
                }
            ]

    class FakeSession:
        def __init__(self):
            self.statements = []
            self.commit_called = False
            self.rollback_called = False

        async def execute(self, statement):
            self.statements.append(statement)
            if len(self.statements) == 1:
                return MyTenantApplicationsRepositoryTests.ScalarResult()
            return MyTenantApplicationsRepositoryTests.MappingResult()

        async def commit(self):
            self.commit_called = True

        async def rollback(self):
            self.rollback_called = True

    def _compiled_sql(self, statement) -> str:
        return str(statement.compile(compile_kwargs={"literal_binds": True}))

    async def test_repository_filters_org_status_and_paginates_without_writes(self):
        from app.modules.tenant.repository import list_tenant_applications_by_org

        session = self.FakeSession()
        items, total = await list_tenant_applications_by_org(
            session,
            org_id=3001,
            status="pending",
            page=2,
            page_size=10,
        )

        count_sql = self._compiled_sql(session.statements[0])
        query_sql = self._compiled_sql(session.statements[1])
        self.assertEqual(total, 1)
        self.assertEqual(items[0]["tenant_id"], 8101)
        self.assertIn("tenant.org_id = 3001", count_sql)
        self.assertIn("tenant.status = 'pending'", count_sql)
        self.assertIn("tenant.org_id = 3001", query_sql)
        self.assertIn("tenant.status = 'pending'", query_sql)
        self.assertIn("LIMIT 10", query_sql)
        self.assertIn("OFFSET 10", query_sql)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_repository_without_status_still_scopes_to_org(self):
        from app.modules.tenant.repository import list_tenant_applications_by_org

        session = self.FakeSession()
        await list_tenant_applications_by_org(
            session,
            org_id=3001,
            status=None,
            page=1,
            page_size=20,
        )

        query_sql = self._compiled_sql(session.statements[1])
        self.assertIn("tenant.org_id = 3001", query_sql)
        self.assertNotIn("tenant.status =", query_sql)

    async def test_repository_signature_has_no_current_user(self):
        from app.modules.tenant.repository import list_tenant_applications_by_org

        params = inspect.signature(list_tenant_applications_by_org).parameters

        self.assertEqual(list(params), ["session", "org_id", "status", "page", "page_size"])
