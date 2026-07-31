import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.core.security import CurrentUser


class ActiveTenantQueryServiceTests(unittest.IsolatedAsyncioTestCase):
    class FakeSession:
        def __init__(self):
            self.commit_called = False
            self.rollback_called = False

        async def commit(self):
            self.commit_called = True

        async def rollback(self):
            self.rollback_called = True

    def _query(self, **overrides):
        from app.modules.tenant.schemas import ActiveTenantQuery

        values = {
            "province": None,
            "city": None,
            "keyword": None,
            "page": 1,
            "page_size": 20,
        }
        values.update(overrides)
        return ActiveTenantQuery(**values)

    def _current_user(self, role: str = "member") -> CurrentUser:
        return CurrentUser(id=1001, role=role)

    def _repo_rows(self):
        return [
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
                "approved_at": datetime(2026, 7, 29, tzinfo=timezone.utc),
                "credit_code": "SHOULD_NOT_LEAK",
            }
        ]

    async def test_member_lists_active_tenants_successfully(self):
        from app.modules.tenant.service import list_active_tenants

        session = self.FakeSession()

        with patch(
            "app.modules.tenant.service.list_active_tenant_options",
            new=AsyncMock(return_value=(self._repo_rows(), 1)),
        ) as repo_mock:
            response = await list_active_tenants(session, self._current_user(), self._query())

        self.assertEqual(response.total, 1)
        self.assertEqual(response.page, 1)
        self.assertEqual(response.page_size, 20)
        self.assertEqual(response.items[0].tenant_id, 501)
        self.assertEqual(response.items[0].name, "Kanglin West Lake Store")
        self.assertFalse(hasattr(response.items[0], "credit_code"))
        self.assertEqual(repo_mock.await_args.kwargs["province"], None)
        self.assertEqual(repo_mock.await_args.kwargs["city"], None)
        self.assertEqual(repo_mock.await_args.kwargs["keyword"], None)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_filters_are_passed_to_repository(self):
        from app.modules.tenant.service import list_active_tenants

        session = self.FakeSession()
        query = self._query(province="ZJ", city="HZ", keyword="West", page=2, page_size=10)

        with patch(
            "app.modules.tenant.service.list_active_tenant_options",
            new=AsyncMock(return_value=([], 0)),
        ) as repo_mock:
            response = await list_active_tenants(session, self._current_user(), query)

        self.assertEqual(response.total, 0)
        self.assertEqual(response.page, 2)
        self.assertEqual(response.page_size, 10)
        self.assertEqual(repo_mock.await_args.kwargs["province"], "ZJ")
        self.assertEqual(repo_mock.await_args.kwargs["city"], "HZ")
        self.assertEqual(repo_mock.await_args.kwargs["keyword"], "West")
        self.assertEqual(repo_mock.await_args.kwargs["page"], 2)
        self.assertEqual(repo_mock.await_args.kwargs["page_size"], 10)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_non_member_roles_are_forbidden(self):
        from app.modules.tenant.service import list_active_tenants

        for role in ("org_admin", "super_admin", "province_admin", "city_admin"):
            with self.subTest(role=role):
                session = self.FakeSession()
                repo_mock = AsyncMock()

                with patch("app.modules.tenant.service.list_active_tenant_options", new=repo_mock):
                    with self.assertRaises(HTTPException) as context:
                        await list_active_tenants(session, self._current_user(role), self._query())

                self.assertEqual(context.exception.status_code, 403)
                repo_mock.assert_not_awaited()
                self.assertFalse(session.commit_called)
                self.assertFalse(session.rollback_called)
