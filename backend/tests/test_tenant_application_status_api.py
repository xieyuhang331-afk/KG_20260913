import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


class TenantApplicationStatusApiTests(unittest.TestCase):
    def setUp(self):
        from app.core.config import get_settings

        # Distinct pending-admin-without-context, linked admin and member actors.
        rows = {
            user_id: SimpleNamespace(
                id=user_id, role=role, tenant_id=None, tenant_org_id=None,
                status="active", exited_at=None, deletion_requested_at=None,
            )
            for user_id, role in ((100, "org_admin"), (101, "member"), (102, "org_admin"))
        }
        authority = patch(
            "app.core.认证当前性._read_authority", new=AsyncMock(side_effect=rows.get),
        )
        authority.start()
        self.addCleanup(authority.stop)
        context = patch.dict("os.environ", {"KG_AUTH_CONTEXT_MAP": '{"100":{"org_id":20}}'})
        context.start()
        self.addCleanup(context.stop)
        get_settings.cache_clear()
        self.addCleanup(get_settings.cache_clear)

    def _client(self):
        from app.core.database import get_db_session
        from app.main import create_app

        app = create_app()

        class FakeSession:
            pass

        async def fake_session():
            yield FakeSession()

        app.dependency_overrides[get_db_session] = fake_session
        return TestClient(app)

    def _headers(self, role: str = "org_admin", *, org_id: str | None = "20") -> dict:
        from app.core.security import create_access_token

        actor_id = "101" if role == "member" else ("102" if org_id is None else "100")
        claims = {"sub": actor_id, "role": role}
        if org_id is not None:
            claims["org_id"] = org_id
        token = create_access_token(claims)
        return {"Authorization": f"Bearer {token}"}

    def _status_row(self, *, status: str = "pending", org_id: int = 20) -> dict:
        return {
            "tenant_id": 501,
            "tenant_code": "T-100-abc123",
            "name": "Kanglin West Lake Store",
            "org_id": org_id,
            "status": status,
            "submitted_at": datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc),
            "reviewed_at": datetime(2026, 7, 28, 12, 10, tzinfo=timezone.utc) if status in {"active", "rejected"} else None,
            "approved_at": datetime(2026, 7, 28, 12, 10, tzinfo=timezone.utc) if status == "active" else None,
            "reject_reason": "license image is unclear" if status == "rejected" else None,
        }

    def test_router_registers_tenant_application_status(self):
        response = self._client().get("/openapi.json")

        self.assertEqual(response.status_code, 200)
        self.assertIn("/api/v1/tenants/{tenant_id}/application-status", response.json()["paths"])
        self.assertIn("get", response.json()["paths"]["/api/v1/tenants/{tenant_id}/application-status"])

    def test_org_admin_can_read_own_pending_application_status(self):
        with patch(
            "app.modules.tenant.service.get_tenant_application_status",
            new=AsyncMock(return_value=self._status_row(status="pending")),
            create=True,
        ):
            response = self._client().get(
                "/api/v1/tenants/501/application-status",
                headers=self._headers(),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["tenant_id"], 501)
        self.assertEqual(response.json()["data"]["tenant_code"], "T-100-abc123")
        self.assertEqual(response.json()["data"]["name"], "Kanglin West Lake Store")
        self.assertEqual(response.json()["data"]["status"], "pending")
        self.assertIsNone(response.json()["data"]["reviewed_at"])
        self.assertIsNone(response.json()["data"]["approved_at"])
        self.assertIsNone(response.json()["data"]["reject_reason"])
        self.assertNotIn("org_id", response.json()["data"])

    def test_org_admin_can_read_own_active_application_status(self):
        with patch(
            "app.modules.tenant.service.get_tenant_application_status",
            new=AsyncMock(return_value=self._status_row(status="active")),
            create=True,
        ):
            response = self._client().get(
                "/api/v1/tenants/501/application-status",
                headers=self._headers(),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["status"], "active")
        self.assertIsNotNone(response.json()["data"]["reviewed_at"])
        self.assertIsNotNone(response.json()["data"]["approved_at"])
        self.assertIsNone(response.json()["data"]["reject_reason"])

    def test_org_admin_can_read_own_rejected_application_status(self):
        with patch(
            "app.modules.tenant.service.get_tenant_application_status",
            new=AsyncMock(return_value=self._status_row(status="rejected")),
            create=True,
        ):
            response = self._client().get(
                "/api/v1/tenants/501/application-status",
                headers=self._headers(),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["status"], "rejected")
        self.assertIsNotNone(response.json()["data"]["reviewed_at"])
        self.assertIsNone(response.json()["data"]["approved_at"])
        self.assertEqual(response.json()["data"]["reject_reason"], "license image is unclear")

    def test_non_org_admin_cannot_read_application_status(self):
        repo_mock = AsyncMock(return_value=self._status_row())
        with patch("app.modules.tenant.service.get_tenant_application_status", new=repo_mock, create=True):
            response = self._client().get(
                "/api/v1/tenants/501/application-status",
                headers=self._headers("member", org_id=None),
            )

        self.assertEqual(response.status_code, 403)
        repo_mock.assert_not_awaited()

    def test_tenant_application_status_requires_jwt(self):
        repo_mock = AsyncMock(return_value=self._status_row())
        with patch("app.modules.tenant.service.get_tenant_application_status", new=repo_mock, create=True):
            response = self._client().get("/api/v1/tenants/501/application-status")

        self.assertEqual(response.status_code, 401)
        repo_mock.assert_not_awaited()

    def test_org_admin_without_org_id_cannot_read_application_status(self):
        repo_mock = AsyncMock(return_value=self._status_row())
        with patch("app.modules.tenant.service.get_tenant_application_status", new=repo_mock, create=True):
            response = self._client().get(
                "/api/v1/tenants/501/application-status",
                headers=self._headers(org_id=None),
            )

        self.assertEqual(response.status_code, 403)
        repo_mock.assert_not_awaited()

    def test_missing_tenant_application_status_returns_404(self):
        with patch(
            "app.modules.tenant.service.get_tenant_application_status",
            new=AsyncMock(return_value=None),
            create=True,
        ):
            response = self._client().get(
                "/api/v1/tenants/999/application-status",
                headers=self._headers(),
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Tenant application not found")

    def test_cross_org_application_status_returns_403(self):
        with patch(
            "app.modules.tenant.service.get_tenant_application_status",
            new=AsyncMock(return_value=self._status_row(org_id=30)),
            create=True,
        ):
            response = self._client().get(
                "/api/v1/tenants/501/application-status",
                headers=self._headers(org_id="20"),
            )

        self.assertEqual(response.status_code, 403)

    def test_invalid_tenant_id_returns_422(self):
        response = self._client().get(
            "/api/v1/tenants/not-a-number/application-status",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 422)
