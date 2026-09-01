import unittest

from fastapi import HTTPException

from app.core.security import CurrentUser


class UserTenantBindingServiceTests(unittest.IsolatedAsyncioTestCase):
    class NoDatabaseSession:
        async def execute(self, _statement):
            raise AssertionError("retired tenant binding reached the database")

        async def commit(self):
            raise AssertionError("retired tenant binding attempted to commit")

        async def rollback(self):
            raise AssertionError("retired tenant binding attempted to rollback")

    async def _assert_retired(
        self,
        *,
        current_user_id: int = 1001,
        role: str = "member",
        path_user_id: int = 1001,
        tenant_id: int = 501,
    ) -> None:
        from app.modules.auth.schemas import TenantBindingRequest
        from app.modules.auth.service import bind_user_tenant

        with self.assertRaises(HTTPException) as caught:
            await bind_user_tenant(
                self.NoDatabaseSession(),
                CurrentUser(id=current_user_id, role=role),
                path_user_id,
                TenantBindingRequest(tenant_id=tenant_id),
            )

        self.assertEqual(caught.exception.status_code, 410)
        self.assertEqual(
            caught.exception.detail,
            "LEGACY_MEMBER_TENANT_BINDING_RETIRED",
        )

    async def test_active_member_binding_is_retired(self):
        await self._assert_retired()

    async def test_cross_user_path_does_not_restore_binding(self):
        await self._assert_retired(path_user_id=2002)

    async def test_org_admin_cannot_use_member_binding(self):
        await self._assert_retired(role="org_admin")

    async def test_missing_user_cannot_be_enumerated(self):
        await self._assert_retired(path_user_id=9999)

    async def test_existing_tenant_binding_cannot_be_changed(self):
        await self._assert_retired(tenant_id=900)

    async def test_disabled_state_cannot_reenable_legacy_binding(self):
        await self._assert_retired()

    async def test_missing_tenant_cannot_be_enumerated(self):
        await self._assert_retired(tenant_id=9999)

    async def test_pending_tenant_cannot_be_bound(self):
        await self._assert_retired(tenant_id=502)

    async def test_rejected_tenant_cannot_be_bound(self):
        await self._assert_retired(tenant_id=503)

    async def test_database_failures_are_not_reached_by_retired_service(self):
        await self._assert_retired()
