import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.core.security import CurrentUser


class MyTenantApplicationsServiceTests(unittest.IsolatedAsyncioTestCase):
    class FakeSession:
        def __init__(self):
            self.commit_called = False
            self.rollback_called = False

        async def commit(self):
            self.commit_called = True

        async def rollback(self):
            self.rollback_called = True

    def _query(self, **overrides):
        from app.modules.tenant.schemas import MyTenantApplicationQuery

        values = {"status": None, "page": 1, "page_size": 20}
        values.update(overrides)
        return MyTenantApplicationQuery(**values)

    def _current_user(self, *, role: str = "org_admin", org_id: int | None = 20) -> CurrentUser:
        return CurrentUser(id=100, role=role, org_id=org_id)

    def _repo_rows(self):
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
                "org_id": 20,
            }
        ]

    async def test_org_admin_lists_only_current_org_applications(self):
        from app.modules.tenant.service import list_my_applications

        session = self.FakeSession()
        with patch(
            "app.modules.tenant.service.list_tenant_applications_by_org",
            new=AsyncMock(return_value=(self._repo_rows(), 1)),
        ) as repo_mock:
            response = await list_my_applications(
                session,
                self._current_user(),
                self._query(status="pending", page=2, page_size=10),
            )

        self.assertEqual(response.total, 1)
        self.assertEqual(response.page, 2)
        self.assertEqual(response.page_size, 10)
        self.assertEqual(response.items[0].tenant_id, 8101)
        self.assertFalse(hasattr(response.items[0], "org_id"))
        self.assertEqual(repo_mock.await_args.kwargs["org_id"], 20)
        self.assertEqual(repo_mock.await_args.kwargs["status"], "pending")
        self.assertEqual(repo_mock.await_args.kwargs["page"], 2)
        self.assertEqual(repo_mock.await_args.kwargs["page_size"], 10)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_empty_application_list_is_returned(self):
        from app.modules.tenant.service import list_my_applications

        session = self.FakeSession()
        with patch(
            "app.modules.tenant.service.list_tenant_applications_by_org",
            new=AsyncMock(return_value=([], 0)),
        ):
            response = await list_my_applications(session, self._current_user(), self._query())

        self.assertEqual(response.items, [])
        self.assertEqual(response.total, 0)

    async def test_non_org_admin_and_missing_org_id_are_forbidden(self):
        from app.modules.tenant.service import list_my_applications

        for current_user in (
            self._current_user(role="super_admin"),
            self._current_user(role="member"),
            self._current_user(org_id=None),
        ):
            with self.subTest(current_user=current_user):
                repo_mock = AsyncMock()
                with patch("app.modules.tenant.service.list_tenant_applications_by_org", new=repo_mock):
                    with self.assertRaises(HTTPException) as context:
                        await list_my_applications(self.FakeSession(), current_user, self._query())

                self.assertEqual(context.exception.status_code, 403)
                repo_mock.assert_not_awaited()
