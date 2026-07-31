import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.core.security import CurrentUser


class UserTenantBindingServiceTests(unittest.IsolatedAsyncioTestCase):
    class FakeSession:
        def __init__(self):
            self.commit_called = False
            self.rollback_called = False

        async def commit(self):
            self.commit_called = True

        async def rollback(self):
            self.rollback_called = True

    def _payload(self, tenant_id: int = 501):
        from app.modules.auth.schemas import TenantBindingRequest

        return TenantBindingRequest(tenant_id=tenant_id)

    def _current_user(self, *, user_id: int = 1001, role: str = "member") -> CurrentUser:
        return CurrentUser(id=user_id, role=role)

    def _user(self, *, tenant_id=None, status="active", role="member"):
        return SimpleNamespace(
            id=1001,
            role=role,
            status=status,
            tenant_id=tenant_id,
            updated_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
        )

    def _tenant(self, *, status="active"):
        return {
            "tenant_id": 501,
            "tenant_code": "TACTIVE001",
            "name": "Kanglin West Lake Store",
            "status": status,
        }

    async def test_member_binds_active_tenant_successfully(self):
        from app.modules.auth.service import bind_user_tenant

        session = self.FakeSession()
        user = self._user()

        with (
            patch("app.modules.auth.service.get_user_for_tenant_binding_update", new=AsyncMock(return_value=user)),
            patch("app.modules.auth.service.get_tenant_by_id_for_binding", new=AsyncMock(return_value=self._tenant())),
            patch("app.modules.auth.service.update_user_tenant_binding", new=AsyncMock(return_value=user)) as update_mock,
        ):
            response = await bind_user_tenant(session, self._current_user(), 1001, self._payload())

        self.assertEqual(response.user_id, 1001)
        self.assertEqual(response.tenant_id, 501)
        self.assertEqual(response.tenant_code, "TACTIVE001")
        self.assertEqual(response.tenant_name, "Kanglin West Lake Store")
        update_mock.assert_awaited_once()
        self.assertTrue(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_member_cannot_bind_other_user(self):
        from app.modules.auth.service import bind_user_tenant

        session = self.FakeSession()
        user_mock = AsyncMock()

        with patch("app.modules.auth.service.get_user_for_tenant_binding_update", new=user_mock):
            with self.assertRaises(HTTPException) as context:
                await bind_user_tenant(session, self._current_user(user_id=1001), 2002, self._payload())

        self.assertEqual(context.exception.status_code, 403)
        user_mock.assert_not_awaited()
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_non_member_cannot_bind_tenant(self):
        from app.modules.auth.service import bind_user_tenant

        session = self.FakeSession()

        with self.assertRaises(HTTPException) as context:
            await bind_user_tenant(session, self._current_user(role="org_admin"), 1001, self._payload())

        self.assertEqual(context.exception.status_code, 403)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_user_not_found_returns_404(self):
        from app.modules.auth.service import bind_user_tenant

        session = self.FakeSession()

        with patch("app.modules.auth.service.get_user_for_tenant_binding_update", new=AsyncMock(return_value=None)):
            with self.assertRaises(HTTPException) as context:
                await bind_user_tenant(session, self._current_user(), 1001, self._payload())

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "User not found")
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_already_bound_user_returns_409(self):
        from app.modules.auth.service import bind_user_tenant

        session = self.FakeSession()

        with patch(
            "app.modules.auth.service.get_user_for_tenant_binding_update",
            new=AsyncMock(return_value=self._user(tenant_id=900)),
        ):
            with self.assertRaises(HTTPException) as context:
                await bind_user_tenant(session, self._current_user(), 1001, self._payload())

        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(context.exception.detail, "User already bound to tenant")
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_inactive_user_returns_403(self):
        from app.modules.auth.service import bind_user_tenant

        session = self.FakeSession()

        with patch(
            "app.modules.auth.service.get_user_for_tenant_binding_update",
            new=AsyncMock(return_value=self._user(status="disabled")),
        ):
            with self.assertRaises(HTTPException) as context:
                await bind_user_tenant(session, self._current_user(), 1001, self._payload())

        self.assertEqual(context.exception.status_code, 403)
        self.assertEqual(context.exception.detail, "Forbidden")

    async def test_tenant_not_found_returns_404(self):
        from app.modules.auth.service import bind_user_tenant

        session = self.FakeSession()

        with (
            patch("app.modules.auth.service.get_user_for_tenant_binding_update", new=AsyncMock(return_value=self._user())),
            patch("app.modules.auth.service.get_tenant_by_id_for_binding", new=AsyncMock(return_value=None)),
        ):
            with self.assertRaises(HTTPException) as context:
                await bind_user_tenant(session, self._current_user(), 1001, self._payload())

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "Tenant not found")

    async def test_pending_tenant_returns_409(self):
        from app.modules.auth.service import bind_user_tenant

        session = self.FakeSession()

        with (
            patch("app.modules.auth.service.get_user_for_tenant_binding_update", new=AsyncMock(return_value=self._user())),
            patch("app.modules.auth.service.get_tenant_by_id_for_binding", new=AsyncMock(return_value=self._tenant(status="pending"))),
        ):
            with self.assertRaises(HTTPException) as context:
                await bind_user_tenant(session, self._current_user(), 1001, self._payload())

        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(context.exception.detail, "Tenant is not active")

    async def test_rejected_tenant_returns_409(self):
        from app.modules.auth.service import bind_user_tenant

        session = self.FakeSession()

        with (
            patch("app.modules.auth.service.get_user_for_tenant_binding_update", new=AsyncMock(return_value=self._user())),
            patch("app.modules.auth.service.get_tenant_by_id_for_binding", new=AsyncMock(return_value=self._tenant(status="rejected"))),
        ):
            with self.assertRaises(HTTPException) as context:
                await bind_user_tenant(session, self._current_user(), 1001, self._payload())

        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(context.exception.detail, "Tenant is not active")

    async def test_integrity_error_rolls_back(self):
        from app.modules.auth.service import bind_user_tenant

        session = self.FakeSession()
        integrity_error = IntegrityError("UPDATE user", {}, Exception("fk"))

        with (
            patch("app.modules.auth.service.get_user_for_tenant_binding_update", new=AsyncMock(return_value=self._user())),
            patch("app.modules.auth.service.get_tenant_by_id_for_binding", new=AsyncMock(return_value=self._tenant())),
            patch("app.modules.auth.service.update_user_tenant_binding", new=AsyncMock(side_effect=integrity_error)),
        ):
            with self.assertRaises(IntegrityError):
                await bind_user_tenant(session, self._current_user(), 1001, self._payload())

        self.assertFalse(session.commit_called)
        self.assertTrue(session.rollback_called)
