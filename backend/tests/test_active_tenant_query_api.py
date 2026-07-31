import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


class ActiveTenantQueryApiTests(unittest.TestCase):
    def _jwt_headers(self, role: str = "member", *, user_id: int = 1001) -> dict:
        from app.core.security import create_access_token

        token = create_access_token({"sub": str(user_id), "role": role})
        return {"Authorization": f"Bearer {token}"}

    def _client(self):
        from app.core.database import get_db_session
        from app.main import create_app

        app = create_app()

        class FakeSession:
            pass

        session = FakeSession()

        async def fake_session():
            yield session

        app.dependency_overrides[get_db_session] = fake_session
        return TestClient(app), session

    def _service_result(self):
        from app.modules.tenant.schemas import ActiveTenantListItem, ActiveTenantListResponse

        return ActiveTenantListResponse(
            items=[
                ActiveTenantListItem(
                    tenant_id=501,
                    tenant_code="TACTIVE001",
                    name="Kanglin West Lake Store",
                    type="health_store",
                    grade="standard",
                    province="ZJ",
                    city="HZ",
                    district="XH",
                    address="No.100 Wensan Road",
                    logo_url=None,
                    contact_phone="13800138000",
                    approved_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
                )
            ],
            total=1,
            page=1,
            page_size=20,
        )

    def test_router_registers_active_tenant_query(self):
        client, _ = self._client()

        response = client.get("/openapi.json")

        self.assertEqual(response.status_code, 200)
        self.assertIn("/api/v1/tenants/active", response.json()["paths"])
        self.assertIn("get", response.json()["paths"]["/api/v1/tenants/active"])

    def test_member_can_list_active_tenants(self):
        client, session = self._client()

        async def list_tenants(session_arg, current_user, query):
            self.assertIs(session_arg, session)
            self.assertEqual(current_user.role, "member")
            self.assertEqual(query.page, 1)
            self.assertEqual(query.page_size, 20)
            self.assertIsNone(query.province)
            self.assertIsNone(query.city)
            self.assertIsNone(query.keyword)
            return self._service_result()

        with patch("app.modules.tenant.api.list_active_tenants", new=AsyncMock(side_effect=list_tenants)):
            response = client.get("/api/v1/tenants/active", headers=self._jwt_headers())

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["tenant_id"], 501)
        self.assertEqual(data["items"][0]["tenant_code"], "TACTIVE001")
        self.assertNotIn("credit_code", data["items"][0])
        self.assertNotIn("license_no", data["items"][0])
        self.assertNotIn("license_image", data["items"][0])
        self.assertNotIn("legal_person_name", data["items"][0])
        self.assertNotIn("org_id", data["items"][0])
        self.assertNotIn("reviewed_by", data["items"][0])

    def test_query_filters_are_passed_to_service(self):
        client, _ = self._client()

        async def list_tenants(session, current_user, query):
            self.assertEqual(query.province, "ZJ")
            self.assertEqual(query.city, "HZ")
            self.assertEqual(query.keyword, "West")
            self.assertEqual(query.page, 2)
            self.assertEqual(query.page_size, 10)
            return self._service_result()

        with patch("app.modules.tenant.api.list_active_tenants", new=AsyncMock(side_effect=list_tenants)):
            response = client.get(
                "/api/v1/tenants/active?province=ZJ&city=HZ&keyword=West&page=2&page_size=10",
                headers=self._jwt_headers(),
            )

        self.assertEqual(response.status_code, 200)

    def test_active_tenant_query_requires_jwt(self):
        client, _ = self._client()
        service_mock = AsyncMock()

        with patch("app.modules.tenant.api.list_active_tenants", new=service_mock):
            response = client.get("/api/v1/tenants/active")

        self.assertEqual(response.status_code, 401)
        service_mock.assert_not_awaited()

    def test_non_member_roles_cannot_list_active_tenants(self):
        for role in ("org_admin", "super_admin", "province_admin", "city_admin"):
            with self.subTest(role=role):
                client, _ = self._client()
                service_mock = AsyncMock()

                with patch("app.modules.tenant.api.list_active_tenants", new=service_mock):
                    response = client.get("/api/v1/tenants/active", headers=self._jwt_headers(role))

                self.assertEqual(response.status_code, 403)
                service_mock.assert_not_awaited()

    def test_invalid_pagination_returns_422(self):
        client, _ = self._client()

        page_response = client.get("/api/v1/tenants/active?page=0", headers=self._jwt_headers())
        size_response = client.get("/api/v1/tenants/active?page_size=0", headers=self._jwt_headers())
        oversized_response = client.get("/api/v1/tenants/active?page_size=101", headers=self._jwt_headers())

        self.assertEqual(page_response.status_code, 422)
        self.assertEqual(size_response.status_code, 422)
        self.assertEqual(oversized_response.status_code, 422)
