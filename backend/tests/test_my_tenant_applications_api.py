import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


class MyTenantApplicationsApiTests(unittest.TestCase):
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

    def _headers(self, role: str = "org_admin", *, org_id: int | None = 20) -> dict:
        from app.core.security import create_access_token

        claims = {"sub": "100", "role": role}
        if org_id is not None:
            claims["org_id"] = org_id
        token = create_access_token(claims)
        return {"Authorization": f"Bearer {token}"}

    def _service_result(self):
        from app.modules.tenant.schemas import MyTenantApplicationListItem, MyTenantApplicationListResponse

        return MyTenantApplicationListResponse(
            items=[
                MyTenantApplicationListItem(
                    tenant_id=8101,
                    tenant_code="P1-TENANT-8101",
                    name="P1 Pending Store 8101",
                    status="pending",
                    province="Zhejiang",
                    city="Hangzhou",
                    contact_name="Contact 8101",
                    contact_phone="13800008101",
                    submitted_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
                    reviewed_at=None,
                    approved_at=None,
                    reject_reason=None,
                )
            ],
            total=1,
            page=1,
            page_size=20,
        )

    def test_router_registers_my_applications_before_dynamic_tenant_route(self):
        client, _ = self._client()

        response = client.get("/openapi.json")

        self.assertEqual(response.status_code, 200)
        self.assertIn("/api/v1/tenants/my-applications", response.json()["paths"])
        parameters = response.json()["paths"]["/api/v1/tenants/my-applications"]["get"]["parameters"]
        self.assertNotIn("org_id", {parameter["name"] for parameter in parameters})

    def test_org_admin_lists_applications_using_jwt_org_id(self):
        client, session = self._client()

        async def list_applications(session_arg, current_user, query):
            self.assertIs(session_arg, session)
            self.assertEqual(current_user.role, "org_admin")
            self.assertEqual(current_user.org_id, 20)
            self.assertEqual(query.status, "pending")
            self.assertEqual(query.page, 2)
            self.assertEqual(query.page_size, 10)
            return self._service_result()

        with patch(
            "app.modules.tenant.api.list_my_applications",
            new=AsyncMock(side_effect=list_applications),
        ):
            response = client.get(
                "/api/v1/tenants/my-applications?status=pending&page=2&page_size=10&org_id=999",
                headers=self._headers(),
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["items"][0]["tenant_id"], 8101)
        self.assertEqual(data["total"], 1)
        self.assertNotIn("org_id", data["items"][0])

    def test_my_applications_requires_jwt(self):
        client, _ = self._client()
        service_mock = AsyncMock()

        with patch("app.modules.tenant.api.list_my_applications", new=service_mock):
            response = client.get("/api/v1/tenants/my-applications")

        self.assertEqual(response.status_code, 401)
        service_mock.assert_not_awaited()

    def test_non_org_admin_is_forbidden(self):
        client, _ = self._client()

        response = client.get("/api/v1/tenants/my-applications", headers=self._headers("member"))

        self.assertEqual(response.status_code, 403)

    def test_org_admin_without_org_id_is_forbidden(self):
        client, _ = self._client()

        response = client.get("/api/v1/tenants/my-applications", headers=self._headers(org_id=None))

        self.assertEqual(response.status_code, 403)

    def test_invalid_filters_and_pagination_return_422(self):
        client, _ = self._client()

        invalid_status = client.get(
            "/api/v1/tenants/my-applications?status=closed",
            headers=self._headers(),
        )
        invalid_page = client.get(
            "/api/v1/tenants/my-applications?page=0",
            headers=self._headers(),
        )
        invalid_page_size = client.get(
            "/api/v1/tenants/my-applications?page_size=101",
            headers=self._headers(),
        )

        self.assertEqual(invalid_status.status_code, 422)
        self.assertEqual(invalid_page.status_code, 422)
        self.assertEqual(invalid_page_size.status_code, 422)
