import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from tests.test_一期后端整改C2_1安全表达错误与请求上下文合同 import (
    assert_core_error_response,
)


class HealthProfileApiTests(unittest.TestCase):
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

    def _payload(self) -> dict:
        return {
            "gender": "F",
            "birth_date": "1990-01-01",
            "height": "165.5",
            "weight": "55.0",
            "blood_type": "A",
            "medical_history": ["hypertension"],
            "allergy_history": None,
            "family_history": {"items": ["diabetes"]},
            "smoking": "never",
            "drinking": "none",
            "symptoms": {"items": ["fatigue"]},
            "sleep_quality": "normal",
            "bowel_urination": "normal",
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

    def _response_profile(self):
        from app.modules.user_health.schemas import HealthProfileResponse

        return HealthProfileResponse(
            id=2001,
            user_id=1001,
            gender="F",
            birth_date=date(1990, 1, 1),
            height=Decimal("165.5"),
            weight=Decimal("55.0"),
            blood_type="A",
            medical_history=["hypertension"],
            allergy_history=None,
            family_history={"items": ["diabetes"]},
            smoking="never",
            drinking="none",
            symptoms={"items": ["fatigue"]},
            sleep_quality="normal",
            bowel_urination="normal",
            created_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
            updated_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
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

    def test_router_registers_post_health_profile(self):
        client, _ = self._client()

        response = client.get("/openapi.json")

        self.assertEqual(response.status_code, 200)
        self.assertIn("/api/v1/users/{user_id}/health-profile", response.json()["paths"])
        self.assertIn("post", response.json()["paths"]["/api/v1/users/{user_id}/health-profile"])
        self.assertIn("get", response.json()["paths"]["/api/v1/users/{user_id}/health-profile"])

    def test_member_creates_own_health_profile_successfully(self):
        client, session = self._client()

        async def create_profile(session_arg, *, user_id, payload):
            self.assertIs(session_arg, session)
            self.assertEqual(user_id, 1001)
            self.assertEqual(payload.gender, "F")
            self.assertEqual(payload.birth_date, date(1990, 1, 1))
            self.assertEqual(payload.medical_history, ["hypertension"])
            self.assertEqual(payload.family_history, {"items": ["diabetes"]})
            self.assertEqual(payload.symptoms, {"items": ["fatigue"]})
            return self._response_profile()

        with patch(
            "app.modules.user_health.api.create_health_profile",
            new=AsyncMock(side_effect=create_profile),
        ):
            response = client.post(
                "/api/v1/users/1001/health-profile",
                json=self._payload(),
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 201)
        data = response.json()["data"]
        self.assertEqual(data["id"], 2001)
        self.assertEqual(data["user_id"], 1001)
        self.assertEqual(data["gender"], "F")
        self.assertEqual(data["birth_date"], "1990-01-01")
        self.assertEqual(data["height"], "165.5")
        self.assertEqual(data["medical_history"], ["hypertension"])
        self.assertEqual(data["family_history"], {"items": ["diabetes"]})
        self.assertEqual(data["symptoms"], {"items": ["fatigue"]})

    def test_member_cannot_create_other_user_health_profile(self):
        client, _ = self._client()
        service_mock = AsyncMock()

        with patch("app.modules.user_health.api.create_health_profile", new=service_mock):
            response = client.post(
                "/api/v1/users/2002/health-profile",
                json=self._payload(),
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 403)
        service_mock.assert_not_awaited()

    def test_non_member_roles_cannot_create_health_profile(self):
        for role in ("org_admin", "super_admin", "province_admin", "city_admin"):
            with self.subTest(role=role):
                client, _ = self._client()
                service_mock = AsyncMock()

                with patch("app.modules.user_health.api.create_health_profile", new=service_mock):
                    response = client.post(
                        "/api/v1/users/1001/health-profile",
                        json=self._payload(),
                        headers=self._jwt_headers(user_id={
                            "org_admin": 1101, "super_admin": 1102,
                            "province_admin": 1103, "city_admin": 1104,
                        }[role], role=role),
                    )

                self.assertEqual(response.status_code, 403)
                service_mock.assert_not_awaited()

    def test_user_not_found_returns_404(self):
        client, _ = self._client()

        with patch(
            "app.modules.user_health.api.create_health_profile",
            new=AsyncMock(side_effect=HTTPException(status_code=404, detail="User not found")),
        ):
            response = client.post(
                "/api/v1/users/1001/health-profile",
                json=self._payload(),
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 404)
        assert_core_error_response(response, 404, "USER_NOT_FOUND")

    def test_duplicate_health_profile_returns_409(self):
        client, _ = self._client()

        with patch(
            "app.modules.user_health.api.create_health_profile",
            new=AsyncMock(side_effect=HTTPException(status_code=409, detail="Health profile already exists")),
        ):
            response = client.post(
                "/api/v1/users/1001/health-profile",
                json=self._payload(),
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 409)
        assert_core_error_response(response, 409, "HEALTH_PROFILE_EXISTS")

    def test_response_does_not_expose_sensitive_user_fields(self):
        client, _ = self._client()

        with patch(
            "app.modules.user_health.api.create_health_profile",
            new=AsyncMock(return_value=self._response_profile()),
        ):
            response = client.post(
                "/api/v1/users/1001/health-profile",
                json=self._payload(),
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 201)
        data = response.json()["data"]
        self.assertNotIn("password_hash", data)
        self.assertNotIn("id_card", data)
        self.assertNotIn("real_name", data)

    def test_member_queries_own_health_profile_successfully(self):
        client, session = self._client()

        async def get_profile(session_arg, *, user_id):
            self.assertIs(session_arg, session)
            self.assertEqual(user_id, 1001)
            return self._response_profile()

        with patch(
            "app.modules.user_health.api.get_health_profile",
            new=AsyncMock(side_effect=get_profile),
        ):
            response = client.get(
                "/api/v1/users/1001/health-profile",
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["id"], 2001)
        self.assertEqual(data["user_id"], 1001)
        self.assertEqual(data["birth_date"], "1990-01-01")
        self.assertEqual(data["height"], "165.5")
        self.assertEqual(data["medical_history"], ["hypertension"])
        self.assertEqual(data["family_history"], {"items": ["diabetes"]})
        self.assertEqual(data["symptoms"], {"items": ["fatigue"]})
        self.assertNotIn("password_hash", data)
        self.assertNotIn("id_card", data)
        self.assertNotIn("real_name", data)

    def test_member_cannot_query_other_user_health_profile(self):
        client, _ = self._client()
        service_mock = AsyncMock()

        with patch("app.modules.user_health.api.get_health_profile", new=service_mock):
            response = client.get(
                "/api/v1/users/2002/health-profile",
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 403)
        service_mock.assert_not_awaited()

    def test_non_member_roles_cannot_query_health_profile(self):
        for role in ("org_admin", "super_admin", "province_admin", "city_admin"):
            with self.subTest(role=role):
                client, _ = self._client()
                service_mock = AsyncMock()

                with patch("app.modules.user_health.api.get_health_profile", new=service_mock):
                    response = client.get(
                        "/api/v1/users/1001/health-profile",
                        headers=self._jwt_headers(user_id={
                            "org_admin": 1101, "super_admin": 1102,
                            "province_admin": 1103, "city_admin": 1104,
                        }[role], role=role),
                    )

                self.assertEqual(response.status_code, 403)
                service_mock.assert_not_awaited()

    def test_query_user_not_found_returns_404(self):
        client, _ = self._client()

        with patch(
            "app.modules.user_health.api.get_health_profile",
            new=AsyncMock(side_effect=HTTPException(status_code=404, detail="User not found")),
        ):
            response = client.get(
                "/api/v1/users/1001/health-profile",
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 404)
        assert_core_error_response(response, 404, "USER_NOT_FOUND")

    def test_query_health_profile_not_found_returns_404(self):
        client, _ = self._client()

        with patch(
            "app.modules.user_health.api.get_health_profile",
            new=AsyncMock(side_effect=HTTPException(status_code=404, detail="Health profile not found")),
        ):
            response = client.get(
                "/api/v1/users/1001/health-profile",
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 404)
        assert_core_error_response(response, 404, "HEALTH_PROFILE_NOT_FOUND")

    def test_query_invalid_path_returns_422(self):
        client, _ = self._client()

        response = client.get(
            "/api/v1/users/bad/health-profile",
            headers=self._jwt_headers(user_id=1001),
        )

        self.assertEqual(response.status_code, 422)

    def test_create_missing_token_returns_401(self):
        client, _ = self._client()

        response = client.post(
            "/api/v1/users/1001/health-profile",
            json=self._payload(),
        )

        self.assertEqual(response.status_code, 401)
        assert_core_error_response(response, 401, "AUTHENTICATION_REQUIRED")

    def test_query_missing_token_returns_401(self):
        client, _ = self._client()

        response = client.get("/api/v1/users/1001/health-profile")

        self.assertEqual(response.status_code, 401)
        assert_core_error_response(response, 401, "AUTHENTICATION_REQUIRED")
