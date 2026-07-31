import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


class TenantReviewDetailApiTests(unittest.TestCase):
    def _headers(
        self,
        role: str = "super_admin",
        *,
        province: str | None = None,
        city: str | None = None,
    ) -> dict:
        from app.core.security import create_access_token

        claims = {"sub": "100", "role": role}
        if province is not None:
            claims["province"] = province
        if city is not None:
            claims["city"] = city
        token = create_access_token(claims)
        return {"Authorization": f"Bearer {token}"}

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

    def _detail(self, *, province: str = "GD", city: str = "GZ") -> dict:
        created_at = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc)
        return {
            "tenant": {
                "id": 501,
                "tenant_code": "TABC123",
                "name": "Kanglin Yuexiu Store",
                "short_name": None,
                "type": "health_store",
                "credit_code": "91330100MA00000123",
                "license_no": "LIC-20260728",
                "license_image": "mock://license.png",
                "legal_person_name": "Alice",
                "province": province,
                "city": city,
                "district": "YX",
                "address": "No.100 Wensan Road",
                "grade": "standard",
                "contact_name": "Bob",
                "contact_phone": "13800138000",
                "contact_email": "ops@example.com",
                "status": "pending",
                "reviewed_by": None,
                "reviewed_at": None,
                "reject_reason": None,
                "approved_at": None,
                "created_at": created_at,
            },
            "attachments": [
                {
                    "id": 9001,
                    "file_type": "business_license",
                    "file_url": "mock://license.png",
                    "created_at": created_at,
                }
            ],
        }

    def test_router_registers_tenant_review_detail(self):
        response = self._client().get("/openapi.json")

        self.assertEqual(response.status_code, 200)
        self.assertIn("/api/v1/reviews/tenants/{tenant_id}", response.json()["paths"])
        self.assertIn("get", response.json()["paths"]["/api/v1/reviews/tenants/{tenant_id}"])

    def test_super_admin_can_view_tenant_review_detail(self):
        with patch("app.modules.review.service.get_tenant_application_detail", new=AsyncMock(return_value=self._detail())):
            response = self._client().get(
                "/api/v1/reviews/tenants/501",
                headers=self._headers("super_admin"),
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["tenant"]["id"], 501)
        self.assertEqual(data["tenant"]["name"], "Kanglin Yuexiu Store")
        self.assertEqual(data["contact"]["contact_phone"], "13800138000")
        self.assertEqual(data["attachments"][0]["file_type"], "business_license")
        self.assertEqual(data["status"]["current"], "pending")
        self.assertIn("submitted_at", data)

    def test_province_admin_can_view_own_province_tenant(self):
        with patch("app.modules.review.service.get_tenant_application_detail", new=AsyncMock(return_value=self._detail(province="GD"))):
            response = self._client().get(
                "/api/v1/reviews/tenants/501",
                headers=self._headers("province_admin", province="GD"),
            )

        self.assertEqual(response.status_code, 200)

    def test_province_admin_cannot_view_cross_province_tenant(self):
        with patch("app.modules.review.service.get_tenant_application_detail", new=AsyncMock(return_value=self._detail(province="ZJ"))):
            response = self._client().get(
                "/api/v1/reviews/tenants/501",
                headers=self._headers("province_admin", province="GD"),
            )

        self.assertEqual(response.status_code, 403)

    def test_city_admin_can_view_own_city_tenant(self):
        with patch("app.modules.review.service.get_tenant_application_detail", new=AsyncMock(return_value=self._detail(province="GD", city="GZ"))):
            response = self._client().get(
                "/api/v1/reviews/tenants/501",
                headers=self._headers("city_admin", province="GD", city="GZ"),
            )

        self.assertEqual(response.status_code, 200)

    def test_city_admin_cannot_view_cross_city_tenant(self):
        with patch("app.modules.review.service.get_tenant_application_detail", new=AsyncMock(return_value=self._detail(province="GD", city="SZ"))):
            response = self._client().get(
                "/api/v1/reviews/tenants/501",
                headers=self._headers("city_admin", province="GD", city="GZ"),
            )

        self.assertEqual(response.status_code, 403)

    def test_non_review_role_cannot_view_tenant_review_detail(self):
        response = self._client().get(
            "/api/v1/reviews/tenants/501",
            headers=self._headers("org_admin", province="GD", city="GZ"),
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Forbidden")

    def test_tenant_review_detail_requires_jwt(self):
        service_mock = AsyncMock()
        with patch("app.modules.review.service.get_tenant_application_detail", new=service_mock):
            response = self._client().get("/api/v1/reviews/tenants/501")

        self.assertEqual(response.status_code, 401)
        service_mock.assert_not_awaited()

    def test_missing_tenant_returns_404(self):
        with patch("app.modules.review.service.get_tenant_application_detail", new=AsyncMock(return_value=None)):
            response = self._client().get(
                "/api/v1/reviews/tenants/999",
                headers=self._headers("super_admin"),
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Tenant not found")

    def test_invalid_tenant_id_returns_422(self):
        response = self._client().get(
            "/api/v1/reviews/tenants/not-a-number",
            headers=self._headers("super_admin"),
        )

        self.assertEqual(response.status_code, 422)

    def test_idor_cross_region_access_is_forbidden(self):
        with patch("app.modules.review.service.get_tenant_application_detail", new=AsyncMock(return_value=self._detail(province="ZJ", city="HZ"))):
            response = self._client().get(
                "/api/v1/reviews/tenants/501",
                headers=self._headers("city_admin", province="GD", city="GZ"),
            )

        self.assertEqual(response.status_code, 403)
