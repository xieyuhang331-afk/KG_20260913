import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from tests.test_一期后端整改C2_1安全表达错误与请求上下文合同 import (
    assert_core_error_response,
)


class TenantApplicationApiTests(unittest.TestCase):
    def setUp(self):
        from app.core.config import get_settings

        rows = {
            user_id: SimpleNamespace(
                id=user_id, role=role, tenant_id=200, tenant_org_id=org_id,
                status="active", exited_at=None, deletion_requested_at=None,
            )
            for user_id, role, org_id in (
                (100, "org_admin", 20), (101, "member", None), (102, "org_admin", None),
            )
        }
        authority = patch(
            "app.core.认证当前性._read_authority", new=AsyncMock(side_effect=rows.get),
        )
        authority.start()
        self.addCleanup(authority.stop)
        get_settings.cache_clear()
        self.addCleanup(get_settings.cache_clear)

    def _payload(self) -> dict:
        return {
            "name": "Kanglin West Lake Store",
            "type": "health_store",
            "credit_code": "91330100MA00000123",
            "license_no": "LIC-20260728",
            "license_image": "mock://license.png",
            "legal_person_name": "Alice",
            "province": "ZJ",
            "city": "HZ",
            "district": "XH",
            "address": "No.100 Wensan Road",
            "contact_name": "Bob",
            "contact_phone": "13800138000",
            "contact_email": "ops@example.com",
            "attachments": [
                {
                    "file_type": "business_license",
                    "file_url": "mock://license.png",
                }
            ],
        }

    def _headers(self, role: str = "org_admin", *, org_id: str | None = "20") -> dict:
        from app.core.security import create_access_token

        actor_id = "101" if role == "member" else ("102" if org_id is None else "100")
        claims = {"sub": actor_id, "role": role, "tenant_id": 200}
        if org_id is not None:
            claims["org_id"] = org_id
        token = create_access_token(claims)
        return {"Authorization": f"Bearer {token}"}

    def _client(self):
        from app.core.database import get_db_session
        from app.main import create_app

        app = create_app()

        class FakeSession:
            async def commit(self):
                return None

        async def fake_session():
            yield FakeSession()

        app.dependency_overrides[get_db_session] = fake_session
        return TestClient(app)

    def test_router_registers_post_tenants(self):
        response = self._client().get("/openapi.json")

        self.assertEqual(response.status_code, 200)
        self.assertIn("/api/v1/tenants", response.json()["paths"])
        self.assertIn("post", response.json()["paths"]["/api/v1/tenants"])

    def test_org_admin_successfully_submits_tenant_application(self):
        created_tenant = SimpleNamespace(
            id=501,
            tenant_code="T-100-abc123",
            name="Kanglin West Lake Store",
            status="pending",
        )
        created_attachments = [SimpleNamespace(file_type="business_license", file_url="mock://license.png")]

        async def create_record(session, *, tenant_data, attachments):
            self.assertEqual(tenant_data["status"], "pending")
            self.assertEqual(tenant_data["org_id"], 20)
            self.assertTrue(tenant_data["tenant_code"])
            self.assertEqual(tenant_data["credit_code"], "91330100MA00000123")
            self.assertEqual(len(attachments), 1)
            self.assertEqual(attachments[0].file_type, "business_license")
            self.assertEqual(attachments[0].file_url, "mock://license.png")
            return created_tenant, created_attachments

        with (
            patch("app.modules.tenant.service.tenant_exists_by_credit_code", new=AsyncMock(return_value=False)),
            patch("app.modules.tenant.service.create_tenant_application_record", new=AsyncMock(side_effect=create_record)),
        ):
            response = self._client().post("/api/v1/tenants", json=self._payload(), headers=self._headers())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "code": 0,
                "message": "ok",
                "data": {
                    "id": 501,
                    "tenant_code": "T-100-abc123",
                    "name": "Kanglin West Lake Store",
                    "status": "pending",
                    "attachment_count": 1,
                },
            },
        )

    def test_non_org_admin_cannot_submit_tenant_application(self):
        response = self._client().post("/api/v1/tenants", json=self._payload(), headers=self._headers("member", org_id=None))

        self.assertEqual(response.status_code, 403)
        assert_core_error_response(response, 403, "FORBIDDEN")

    def test_tenant_application_requires_jwt(self):
        create_mock = AsyncMock()
        with patch("app.modules.tenant.service.create_tenant_application_record", new=create_mock):
            response = self._client().post("/api/v1/tenants", json=self._payload())

        self.assertEqual(response.status_code, 401)
        create_mock.assert_not_awaited()

    def test_org_admin_without_org_id_cannot_submit_tenant_application(self):
        create_mock = AsyncMock()
        with patch("app.modules.tenant.service.create_tenant_application_record", new=create_mock):
            response = self._client().post("/api/v1/tenants", json=self._payload(), headers=self._headers(org_id=None))

        self.assertEqual(response.status_code, 403)
        assert_core_error_response(response, 403, "FORBIDDEN")
        create_mock.assert_not_awaited()

    def test_invalid_payload_returns_422(self):
        payload = self._payload()
        payload.pop("name")

        response = self._client().post("/api/v1/tenants", json=payload, headers=self._headers())

        self.assertEqual(response.status_code, 422)

    def test_duplicate_credit_code_returns_409(self):
        with patch("app.modules.tenant.service.tenant_exists_by_credit_code", new=AsyncMock(return_value=True)):
            response = self._client().post("/api/v1/tenants", json=self._payload(), headers=self._headers())

        self.assertEqual(response.status_code, 409)
        assert_core_error_response(response, 409, "TENANT_APPLICATION_EXISTS")
