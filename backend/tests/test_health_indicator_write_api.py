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


class HealthIndicatorWriteApiTests(unittest.TestCase):
    def setUp(self):
        from app.core.config import get_settings

        # These fixed authority rows do not depend on decoded token claims.
        self.authority_rows = {
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
            "app.core.认证当前性._read_authority",
            new=AsyncMock(side_effect=self.authority_rows.get),
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

    def _payload(self, *, source="APP") -> dict:
        return {
            "indicators": [
                {
                    "indicator_type": "blood_pressure",
                    "value": "120.00",
                    "unit": "mmHg",
                    "source": source,
                    "recorded_at": "2026-07-29T08:00:00Z",
                }
            ]
        }

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

    def _response_indicator(self):
        from app.modules.user_health.schemas import HealthIndicatorResponse

        return HealthIndicatorResponse(
            id=3001,
            batch_id="batch-1",
            indicator_type="blood_pressure",
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

    def test_router_registers_post_health_indicators(self):
        client, _ = self._client()

        response = client.get("/openapi.json")

        self.assertEqual(response.status_code, 200)
        self.assertIn("/api/v1/users/{user_id}/health-indicators", response.json()["paths"])
        self.assertIn("post", response.json()["paths"]["/api/v1/users/{user_id}/health-indicators"])

    def test_member_writes_own_health_indicators_successfully(self):
        self.authority_rows[1001].tenant_id = 301
        client, session = self._client()

        async def create_indicators(session_arg, *, user_id, payload):
            self.assertIs(session_arg, session)
            self.assertEqual(user_id, 1001)
            self.assertEqual(len(payload.indicators), 1)
            self.assertEqual(payload.indicators[0].source, "APP")
            return [self._response_indicator()]

        with patch(
            "app.modules.user_health.api.create_health_indicators",
            new=AsyncMock(side_effect=create_indicators),
        ):
            response = client.post(
                "/api/v1/users/1001/health-indicators",
                json=self._payload(),
                headers=self._jwt_headers(user_id=1001, tenant_id=301),
            )

        self.assertEqual(response.status_code, 201)
        data = response.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], 3001)
        self.assertEqual(data[0]["batch_id"], "batch-1")
        self.assertEqual(data[0]["indicator_type"], "blood_pressure")
        self.assertEqual(data[0]["value"], "120.00")
        self.assertEqual(data[0]["source"], "APP")

    def test_member_token_preserves_tenant_context(self):
        self.authority_rows[1001].tenant_id = 301
        client, _ = self._client()
        captured = {}

        def guard(current_user, user_id):
            captured["tenant_id"] = current_user.tenant_id

        with (
            patch("app.modules.user_health.api.ensure_can_access_own_user_resource", side_effect=guard),
            patch(
                "app.modules.user_health.api.create_health_indicators",
                new=AsyncMock(return_value=[self._response_indicator()]),
            ),
        ):
            response = client.post(
                "/api/v1/users/1001/health-indicators",
                json=self._payload(),
                headers=self._jwt_headers(user_id=1001, tenant_id=301),
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(captured["tenant_id"], 301)

    def test_missing_current_user_returns_401(self):
        client, _ = self._client()
        service_mock = AsyncMock()

        with patch("app.modules.user_health.api.create_health_indicators", new=service_mock):
            response = client.post(
                "/api/v1/users/1001/health-indicators",
                json=self._payload(),
            )

        self.assertEqual(response.status_code, 401)
        service_mock.assert_not_awaited()

    def test_non_member_roles_cannot_write_health_indicators(self):
        for role in ("org_admin", "super_admin", "province_admin", "city_admin"):
            with self.subTest(role=role):
                client, _ = self._client()
                service_mock = AsyncMock()

                with patch("app.modules.user_health.api.create_health_indicators", new=service_mock):
                    response = client.post(
                        "/api/v1/users/1001/health-indicators",
                        json=self._payload(),
                        headers=self._jwt_headers(user_id={
                            "org_admin": 1101, "super_admin": 1102,
                            "province_admin": 1103, "city_admin": 1104,
                        }[role], role=role),
                    )

                self.assertEqual(response.status_code, 403)
                service_mock.assert_not_awaited()

    def test_member_cannot_write_other_user_health_indicators(self):
        client, _ = self._client()
        service_mock = AsyncMock()

        with patch("app.modules.user_health.api.create_health_indicators", new=service_mock):
            response = client.post(
                "/api/v1/users/2002/health-indicators",
                json=self._payload(),
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 403)
        service_mock.assert_not_awaited()

    def test_user_not_found_returns_404(self):
        client, _ = self._client()

        with patch(
            "app.modules.user_health.api.create_health_indicators",
            new=AsyncMock(side_effect=HTTPException(status_code=404, detail="User not found")),
        ):
            response = client.post(
                "/api/v1/users/1001/health-indicators",
                json=self._payload(),
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 404)
        assert_core_error_response(response, 404, "USER_NOT_FOUND")

    def test_user_inactive_returns_409(self):
        client, _ = self._client()

        with patch(
            "app.modules.user_health.api.create_health_indicators",
            new=AsyncMock(side_effect=HTTPException(status_code=409, detail="User is not active")),
        ):
            response = client.post(
                "/api/v1/users/1001/health-indicators",
                json=self._payload(),
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 409)
        assert_core_error_response(response, 409, "USER_INACTIVE")

    def test_missing_health_profile_returns_409(self):
        client, _ = self._client()

        with patch(
            "app.modules.user_health.api.create_health_indicators",
            new=AsyncMock(side_effect=HTTPException(status_code=409, detail="Health profile is required")),
        ):
            response = client.post(
                "/api/v1/users/1001/health-indicators",
                json=self._payload(),
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 409)
        assert_core_error_response(response, 409, "HEALTH_PROFILE_REQUIRED")

    def test_non_app_sources_return_422(self):
        for source in ("STORE", "DEVICE", "REPORT"):
            with self.subTest(source=source):
                client, _ = self._client()
                service_mock = AsyncMock()

                with patch("app.modules.user_health.api.create_health_indicators", new=service_mock):
                    response = client.post(
                        "/api/v1/users/1001/health-indicators",
                        json=self._payload(source=source),
                        headers=self._jwt_headers(user_id=1001),
                    )

                self.assertEqual(response.status_code, 422)
                assert_core_error_response(response, 422, "HEALTH_INDICATOR_SOURCE_INVALID")
                service_mock.assert_not_awaited()

    def test_invalid_payload_returns_422(self):
        client, _ = self._client()

        response = client.post(
            "/api/v1/users/1001/health-indicators",
            json={"indicators": [{"source": "APP"}]},
            headers=self._jwt_headers(user_id=1001),
        )

        self.assertEqual(response.status_code, 422)

    def test_response_does_not_expose_sensitive_user_fields(self):
        client, _ = self._client()

        with patch(
            "app.modules.user_health.api.create_health_indicators",
            new=AsyncMock(return_value=[self._response_indicator()]),
        ):
            response = client.post(
                "/api/v1/users/1001/health-indicators",
                json=self._payload(),
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 201)
        data = response.json()["data"][0]
        self.assertNotIn("password_hash", data)
        self.assertNotIn("id_card", data)
        self.assertNotIn("real_name", data)
