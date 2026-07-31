import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


class TenantReviewQueueApiTests(unittest.TestCase):
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

    def _service_result(self):
        from app.modules.review.schemas import TenantReviewQueueItem, TenantReviewQueueResponse

        return TenantReviewQueueResponse(
            items=[
                TenantReviewQueueItem(
                    tenant_id=501,
                    tenant_code="TABC123",
                    name="Kanglin Yuexiu Store",
                    type="health_store",
                    credit_code="91330100MA00000123",
                    province="GD",
                    city="GZ",
                    district="YX",
                    contact_name="Bob",
                    contact_phone="13800138000",
                    status="pending",
                    submitted_at=datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc),
                    attachment_count=1,
                )
            ],
            page=1,
            page_size=20,
            total=1,
        )

    def test_router_registers_tenant_review_queue(self):
        response = self._client().get("/openapi.json")

        self.assertEqual(response.status_code, 200)
        self.assertIn("/api/v1/reviews/queue/tenant", response.json()["paths"])
        self.assertIn("get", response.json()["paths"]["/api/v1/reviews/queue/tenant"])

    def test_super_admin_can_list_pending_tenant_applications(self):
        with patch(
            "app.modules.review.api.list_tenant_review_queue",
            new=AsyncMock(return_value=self._service_result()),
        ) as mocked_service:
            response = self._client().get(
                "/api/v1/reviews/queue/tenant",
                headers=self._headers("super_admin"),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["total"], 1)
        self.assertEqual(response.json()["data"]["items"][0]["status"], "pending")
        query = mocked_service.await_args.args[2]
        self.assertEqual(query.page, 1)
        self.assertEqual(query.page_size, 20)
        self.assertIsNone(query.province)
        self.assertIsNone(query.city)

    def test_province_admin_is_limited_to_own_province(self):
        with patch("app.modules.review.service.list_pending_tenant_applications", new=AsyncMock(return_value=([], 0))) as mocked_repo:
            response = self._client().get(
                "/api/v1/reviews/queue/tenant?city=GZ",
                headers=self._headers("province_admin", province="GD"),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mocked_repo.await_args.kwargs["province"], "GD")
        self.assertEqual(mocked_repo.await_args.kwargs["city"], "GZ")

    def test_city_admin_is_limited_to_own_city(self):
        with patch("app.modules.review.service.list_pending_tenant_applications", new=AsyncMock(return_value=([], 0))) as mocked_repo:
            response = self._client().get(
                "/api/v1/reviews/queue/tenant",
                headers=self._headers("city_admin", province="GD", city="GZ"),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mocked_repo.await_args.kwargs["province"], "GD")
        self.assertEqual(mocked_repo.await_args.kwargs["city"], "GZ")

    def test_region_admin_cannot_query_outside_scope(self):
        province_response = self._client().get(
            "/api/v1/reviews/queue/tenant?province=ZJ",
            headers=self._headers("province_admin", province="GD"),
        )
        city_response = self._client().get(
            "/api/v1/reviews/queue/tenant?city=SZ",
            headers=self._headers("city_admin", province="GD", city="GZ"),
        )

        self.assertEqual(province_response.status_code, 403)
        self.assertEqual(city_response.status_code, 403)

    def test_non_review_role_cannot_access_queue(self):
        response = self._client().get(
            "/api/v1/reviews/queue/tenant",
            headers=self._headers("org_admin", province="GD", city="GZ"),
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Forbidden")

    def test_review_queue_requires_jwt(self):
        service_mock = AsyncMock()
        with patch("app.modules.review.api.list_tenant_review_queue", new=service_mock):
            response = self._client().get("/api/v1/reviews/queue/tenant")

        self.assertEqual(response.status_code, 401)
        service_mock.assert_not_awaited()

    def test_invalid_pagination_returns_422(self):
        response = self._client().get(
            "/api/v1/reviews/queue/tenant?page=0&page_size=20",
            headers=self._headers("super_admin"),
        )
        oversized_response = self._client().get(
            "/api/v1/reviews/queue/tenant?page=1&page_size=101",
            headers=self._headers("super_admin"),
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(oversized_response.status_code, 422)

    def test_keyword_is_passed_to_repository(self):
        with patch("app.modules.review.service.list_pending_tenant_applications", new=AsyncMock(return_value=([], 0))) as mocked_repo:
            response = self._client().get(
                "/api/v1/reviews/queue/tenant?keyword=Yuexiu",
                headers=self._headers("super_admin"),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mocked_repo.await_args.kwargs["keyword"], "Yuexiu")
