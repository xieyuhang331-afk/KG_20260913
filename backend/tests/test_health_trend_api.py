import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient


class HealthTrendApiTests(unittest.TestCase):
    def _jwt_headers(self, *, user_id: int = 1001, role: str = "member", tenant_id: int | None = None) -> dict:
        from app.core.security import create_access_token

        claims = {"sub": str(user_id), "role": role}
        if tenant_id is not None:
            claims["tenant_id"] = tenant_id
        token = create_access_token(claims)
        return {"Authorization": f"Bearer {token}"}

    def _trend(self):
        from app.modules.health_analysis.schemas import HealthTrend, TrendPoint

        return HealthTrend(
            indicator_type="systolic_bp",
            display_name="收缩压",
            category="blood_pressure",
            unit="mmHg",
            points=[
                TrendPoint(
                    value=Decimal("120.00"),
                    recorded_at=datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc),
                    source="APP",
                )
            ],
        )

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

    def test_router_registers_health_trends(self):
        client, _ = self._client()

        response = client.get("/openapi.json")

        self.assertEqual(response.status_code, 200)
        self.assertIn("/api/v1/users/{user_id}/health-trends", response.json()["paths"])
        self.assertIn("get", response.json()["paths"]["/api/v1/users/{user_id}/health-trends"])

    def test_member_queries_own_health_trend_successfully(self):
        client, session = self._client()
        start_at = "2026-07-01T00:00:00Z"
        end_at = "2026-07-29T00:00:00Z"

        async def get_trend(session_arg, **kwargs):
            self.assertIs(session_arg, session)
            self.assertEqual(kwargs["user_id"], 1001)
            self.assertEqual(kwargs["indicator_type"], "systolic_bp")
            self.assertEqual(kwargs["limit"], 50)
            self.assertIsNotNone(kwargs["start_at"])
            self.assertIsNotNone(kwargs["end_at"])
            return self._trend()

        with patch(
            "app.modules.health_analysis.api.get_health_trend",
            new=AsyncMock(side_effect=get_trend),
        ):
            response = client.get(
                "/api/v1/users/1001/health-trends",
                params={
                    "indicator_type": "systolic_bp",
                    "start_at": start_at,
                    "end_at": end_at,
                    "limit": 50,
                },
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["indicator_type"], "systolic_bp")
        self.assertEqual(data["display_name"], "收缩压")
        self.assertEqual(data["points"][0]["value"], "120.00")
        self.assertEqual(data["points"][0]["source"], "APP")

    def test_indicator_type_is_required(self):
        client, _ = self._client()
        service_mock = AsyncMock()

        with patch("app.modules.health_analysis.api.get_health_trend", new=service_mock):
            response = client.get(
                "/api/v1/users/1001/health-trends",
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 422)
        service_mock.assert_not_awaited()

    def test_unknown_indicator_returns_422(self):
        client, _ = self._client()

        with patch(
            "app.modules.health_analysis.api.get_health_trend",
            new=AsyncMock(side_effect=HTTPException(status_code=422, detail="Unknown indicator_type")),
        ):
            response = client.get(
                "/api/v1/users/1001/health-trends",
                params={"indicator_type": "unknown_metric"},
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "Unknown indicator_type")

    def test_start_after_end_returns_422(self):
        client, _ = self._client()

        with patch(
            "app.modules.health_analysis.api.get_health_trend",
            new=AsyncMock(side_effect=HTTPException(status_code=422, detail="Invalid time range")),
        ):
            response = client.get(
                "/api/v1/users/1001/health-trends",
                params={
                    "indicator_type": "systolic_bp",
                    "start_at": "2026-07-30T00:00:00Z",
                    "end_at": "2026-07-29T00:00:00Z",
                },
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "Invalid time range")

    def test_api_passes_default_window_values_to_service_as_none(self):
        client, _ = self._client()

        async def get_trend(session_arg, **kwargs):
            self.assertIsNone(kwargs["start_at"])
            self.assertIsNone(kwargs["end_at"])
            self.assertIsNone(kwargs["limit"])
            return self._trend()

        with patch(
            "app.modules.health_analysis.api.get_health_trend",
            new=AsyncMock(side_effect=get_trend),
        ):
            response = client.get(
                "/api/v1/users/1001/health-trends",
                params={"indicator_type": "systolic_bp"},
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 200)

    def test_health_trend_requires_jwt(self):
        client, _ = self._client()
        service_mock = AsyncMock()

        with patch("app.modules.health_analysis.api.get_health_trend", new=service_mock):
            response = client.get(
                "/api/v1/users/1001/health-trends",
                params={"indicator_type": "systolic_bp"},
            )

        self.assertEqual(response.status_code, 401)
        service_mock.assert_not_awaited()

    def test_member_cannot_query_other_user_health_trend(self):
        client, _ = self._client()
        service_mock = AsyncMock()

        with patch("app.modules.health_analysis.api.get_health_trend", new=service_mock):
            response = client.get(
                "/api/v1/users/2002/health-trends",
                params={"indicator_type": "systolic_bp"},
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 403)
        service_mock.assert_not_awaited()

    def test_non_member_roles_cannot_query_health_trend(self):
        for role in ("org_admin", "super_admin", "province_admin", "city_admin"):
            with self.subTest(role=role):
                client, _ = self._client()
                service_mock = AsyncMock()

                with patch("app.modules.health_analysis.api.get_health_trend", new=service_mock):
                    response = client.get(
                        "/api/v1/users/1001/health-trends",
                        params={"indicator_type": "systolic_bp"},
                        headers=self._jwt_headers(user_id=1001, role=role),
                    )

                self.assertEqual(response.status_code, 403)
                service_mock.assert_not_awaited()

    def test_response_does_not_expose_medical_judgement_fields(self):
        client, _ = self._client()

        with patch(
            "app.modules.health_analysis.api.get_health_trend",
            new=AsyncMock(return_value=self._trend()),
        ):
            response = client.get(
                "/api/v1/users/1001/health-trends",
                params={"indicator_type": "systolic_bp"},
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 200)
        serialized = response.text
        self.assertNotIn("health_score", serialized)
        self.assertNotIn("risk_level", serialized)
        self.assertNotIn("diagnosis_hint", serialized)
        self.assertNotIn("normal_range", serialized)
        self.assertNotIn("abnormal_flag", serialized)
