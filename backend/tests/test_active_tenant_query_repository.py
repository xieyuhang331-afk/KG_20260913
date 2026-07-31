import unittest


class ActiveTenantQueryRepositoryTests(unittest.IsolatedAsyncioTestCase):
    class ScalarResult:
        def __init__(self, value):
            self.value = value

        def scalar_one(self):
            return self.value

    class MappingResult:
        def __init__(self, rows):
            self.rows = rows

        def mappings(self):
            return self

        def all(self):
            return self.rows

    class FakeSession:
        def __init__(self):
            self.statements = []
            self.commit_called = False
            self.rollback_called = False

        async def execute(self, statement):
            self.statements.append(statement)
            if len(self.statements) == 1:
                return ActiveTenantQueryRepositoryTests.ScalarResult(1)
            return ActiveTenantQueryRepositoryTests.MappingResult(
                [
                    {
                        "tenant_id": 501,
                        "tenant_code": "TACTIVE001",
                        "name": "Kanglin West Lake Store",
                        "type": "health_store",
                        "grade": "standard",
                        "province": "ZJ",
                        "city": "HZ",
                        "district": "XH",
                        "address": "No.100 Wensan Road",
                        "logo_url": None,
                        "contact_phone": "13800138000",
                        "approved_at": None,
                    }
                ]
            )

        async def commit(self):
            self.commit_called = True

        async def rollback(self):
            self.rollback_called = True

    def _compiled_sql(self, statement) -> str:
        return str(statement.compile(compile_kwargs={"literal_binds": True}))

    async def test_list_active_tenant_options_filters_active_status_and_returns_items(self):
        from app.modules.tenant.repository import list_active_tenant_options

        session = self.FakeSession()

        items, total = await list_active_tenant_options(
            session,
            province=None,
            city=None,
            keyword=None,
            page=1,
            page_size=20,
        )

        self.assertEqual(total, 1)
        self.assertEqual(items[0]["tenant_id"], 501)
        self.assertEqual(items[0]["tenant_code"], "TACTIVE001")
        self.assertIn("tenant.status = 'active'", self._compiled_sql(session.statements[0]))
        self.assertIn("tenant.status = 'active'", self._compiled_sql(session.statements[1]))
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_list_active_tenant_options_applies_filters_keyword_and_pagination(self):
        from app.modules.tenant.repository import list_active_tenant_options

        session = self.FakeSession()

        await list_active_tenant_options(
            session,
            province="ZJ",
            city="HZ",
            keyword="West",
            page=2,
            page_size=10,
        )

        query_sql = self._compiled_sql(session.statements[1])
        self.assertIn("tenant.province = 'ZJ'", query_sql)
        self.assertIn("tenant.city = 'HZ'", query_sql)
        self.assertIn("'%West%'", query_sql)
        self.assertIn("LIMIT 10", query_sql)
        self.assertIn("OFFSET 10", query_sql)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_repository_signature_does_not_accept_current_user(self):
        import inspect
        from app.modules.tenant.repository import list_active_tenant_options

        params = inspect.signature(list_active_tenant_options).parameters

        self.assertEqual(list(params), ["session", "province", "city", "keyword", "page", "page_size"])
