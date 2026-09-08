import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from tests.test_一期后端整改C2_1安全表达错误与请求上下文合同 import (
    assert_core_error_response,
)


class HealthIndicatorQueryApiTests(unittest.TestCase):
    def setUp(self):
        from app.core.config import get_settings

        rows = {
            user_id: SimpleNamespace(
                id=user_id, role=role, tenant_id=None, tenant_org_id=None,
                status="active", exited_at=None, deletion_requested_at=None,
            )
            for user_id, role in (
                (1001, "member"), (1101, "org_admin"), (1102, "super_admin"),
                (1103, "province_admin"), (1104, "city_admin"),
            )
        }
        authority = patch(
            "app.core.认证当前性._read_authority", new=AsyncMock(side_effect=rows.get),
        )
        authority.start()
        self.addCleanup(authority.stop)
        context = patch.dict("os.environ", {"KG_AUTH_CONTEXT_MAP": (
            '{"1103":{"province":"ZJ"},"1104":{"province":"ZJ","city":"HZ"}}'
        )})
        context.start()
        self.addCleanup(context.stop)
        get_settings.cache_clear()
        self.addCleanup(get_settings.cache_clear)

    def _headers(self, *, user_id: int = 1001, role: str = "member") -> dict:
        return {
            "x-user-id": str(user_id),
            "x-user-role": role,
        }

    def _jwt_headers(
        self,
        *,
        user_id: int = 1001,
        role: str = "member",
        tenant_id: int | None = None,
    ) -> dict:
        from app.core.security import create_access_token

        claims = {"sub": str(user_id), "role": role}
        claims.update({
            1103: {"province": "ZJ"}, 1104: {"province": "ZJ", "city": "HZ"},
        }.get(user_id, {}))
        if tenant_id is not None:
            claims["tenant_id"] = tenant_id
        token = create_access_token(claims)
        return {"Authorization": f"Bearer {token}"}

    def _response_indicator(self, *, indicator_type="systolic_bp"):
        from app.modules.user_health.schemas import HealthIndicatorResponse

        return HealthIndicatorResponse(
            id=3001,
            batch_id="batch-1",
            indicator_type=indicator_type,
            value=Decimal("120.00"),
            unit="mmHg",
            source="APP",
            recorded_at=datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc),
            created_at=datetime(2026, 7, 29, 8, 1, tzinfo=timezone.utc),
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

    def test_router_registers_health_indicator_query_routes(self):
        client, _ = self._client()

        response = client.get("/openapi.json")

        self.assertEqual(response.status_code, 200)
        paths = response.json()["paths"]
        self.assertIn("/api/v1/users/{user_id}/health-indicators", paths)
        self.assertIn("get", paths["/api/v1/users/{user_id}/health-indicators"])
        self.assertIn("/api/v1/users/{user_id}/health-indicators/latest", paths)
        self.assertIn("get", paths["/api/v1/users/{user_id}/health-indicators/latest"])

    def test_member_queries_own_health_indicators_successfully(self):
        client, session = self._client()
        start_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
        end_at = datetime(2026, 7, 29, tzinfo=timezone.utc)

        async def list_indicators(session_arg, *, user_id, indicator_type, start_at, end_at, limit):
            self.assertIs(session_arg, session)
            self.assertEqual(user_id, 1001)
            self.assertEqual(indicator_type, "systolic_bp")
            self.assertEqual(start_at, datetime(2026, 7, 1, tzinfo=timezone.utc))
            self.assertEqual(end_at, datetime(2026, 7, 29, tzinfo=timezone.utc))
            self.assertEqual(limit, 20)
            return [self._response_indicator()]

        with patch(
            "app.modules.user_health.api.list_health_indicators",
            new=AsyncMock(side_effect=list_indicators),
        ):
            response = client.get(
                "/api/v1/users/1001/health-indicators",
                params={
                    "indicator_type": "systolic_bp",
                    "start_at": start_at.isoformat(),
                    "end_at": end_at.isoformat(),
                    "limit": 20,
                },
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], 3001)
        self.assertEqual(data[0]["indicator_type"], "systolic_bp")
        self.assertEqual(data[0]["value"], "120.00")

    def test_member_queries_empty_health_indicators_successfully(self):
        client, _ = self._client()

        with patch("app.modules.user_health.api.list_health_indicators", new=AsyncMock(return_value=[])):
            response = client.get(
                "/api/v1/users/1001/health-indicators",
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], [])

    def test_member_queries_latest_health_indicators_successfully(self):
        client, session = self._client()

        async def latest_indicators(session_arg, *, user_id):
            self.assertIs(session_arg, session)
            self.assertEqual(user_id, 1001)
            return [self._response_indicator(indicator_type="systolic_bp")]

        with patch(
            "app.modules.user_health.api.get_latest_health_indicators",
            new=AsyncMock(side_effect=latest_indicators),
        ):
            response = client.get(
                "/api/v1/users/1001/health-indicators/latest",
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["indicator_type"], "systolic_bp")

    def test_member_cannot_query_other_user_health_indicators(self):
        client, _ = self._client()
        service_mock = AsyncMock()

        with patch("app.modules.user_health.api.list_health_indicators", new=service_mock):
            response = client.get(
                "/api/v1/users/2002/health-indicators",
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 403)
        service_mock.assert_not_awaited()

    def test_non_member_roles_cannot_query_health_indicators(self):
        for role in ("org_admin", "super_admin", "province_admin", "city_admin"):
            with self.subTest(role=role):
                client, _ = self._client()
                service_mock = AsyncMock()

                with patch("app.modules.user_health.api.list_health_indicators", new=service_mock):
                    response = client.get(
                        "/api/v1/users/1001/health-indicators",
                        headers=self._jwt_headers(user_id={
                            "org_admin": 1101, "super_admin": 1102,
                            "province_admin": 1103, "city_admin": 1104,
                        }[role], role=role),
                    )

                self.assertEqual(response.status_code, 403)
                service_mock.assert_not_awaited()

    def test_user_not_found_returns_404_for_history_query(self):
        client, _ = self._client()

        with patch(
            "app.modules.user_health.api.list_health_indicators",
            new=AsyncMock(side_effect=HTTPException(status_code=404, detail="User not found")),
        ):
            response = client.get(
                "/api/v1/users/1001/health-indicators",
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 404)
        assert_core_error_response(response, 404, "USER_NOT_FOUND")

    def test_user_not_found_returns_404_for_latest_query(self):
        client, _ = self._client()

        with patch(
            "app.modules.user_health.api.get_latest_health_indicators",
            new=AsyncMock(side_effect=HTTPException(status_code=404, detail="User not found")),
        ):
            response = client.get(
                "/api/v1/users/1001/health-indicators/latest",
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 404)
        assert_core_error_response(response, 404, "USER_NOT_FOUND")

    def test_query_response_does_not_expose_sensitive_user_fields(self):
        client, _ = self._client()

        with patch(
            "app.modules.user_health.api.list_health_indicators",
            new=AsyncMock(return_value=[self._response_indicator()]),
        ):
            response = client.get(
                "/api/v1/users/1001/health-indicators",
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"][0]
        self.assertNotIn("password_hash", data)
        self.assertNotIn("id_card", data)
        self.assertNotIn("real_name", data)

    def test_missing_token_returns_401_for_history_query(self):
        client, _ = self._client()
        service_mock = AsyncMock()

        with patch("app.modules.user_health.api.list_health_indicators", new=service_mock):
            response = client.get("/api/v1/users/1001/health-indicators")

        self.assertEqual(response.status_code, 401)
        assert_core_error_response(response, 401, "AUTHENTICATION_REQUIRED")
        service_mock.assert_not_awaited()

    def test_missing_token_returns_401_for_latest_query(self):
        client, _ = self._client()
        service_mock = AsyncMock()

        with patch("app.modules.user_health.api.get_latest_health_indicators", new=service_mock):
            response = client.get("/api/v1/users/1001/health-indicators/latest")

        self.assertEqual(response.status_code, 401)
        assert_core_error_response(response, 401, "AUTHENTICATION_REQUIRED")
        service_mock.assert_not_awaited()
