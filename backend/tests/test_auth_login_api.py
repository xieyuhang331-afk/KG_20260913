from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


class AuthLoginApiTests(unittest.TestCase):
    def _client(self):
        from app.core.database import get_db_session
        from app.main import create_app

        app = create_app()

        class FakeSession:
            async def execute(self, _statement):
                return SimpleNamespace(scalar_one_or_none=lambda: None)

        async def fake_session():
            yield FakeSession()

        app.dependency_overrides[get_db_session] = fake_session
        return TestClient(app)

    def _user(
        self,
        *,
        role: str = "member",
        status: str = "active",
        tenant_id: int | None = None,
    ):
        from app.modules.auth.service import hash_password

        return SimpleNamespace(
            id=1001,
            phone="13800138001",
            password_hash=hash_password("Secret12345"),
            role=role,
            status=status,
            tenant_id=tenant_id,
        )

    def test_member_logs_in_with_phone_and_password(self):
        from app.core.security import decode_access_token

        with patch(
            "app.modules.auth.service.get_user_by_phone",
            new=AsyncMock(return_value=self._user(tenant_id=501)),
            create=True,
        ):
            response = self._client().post(
                "/api/v1/auth/login",
                json={"phone": "13800138001", "password": "Secret12345"},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        claims = decode_access_token(data["access_token"])
        self.assertEqual(data["token_type"], "bearer")
        self.assertEqual(data["user"]["id"], 1001)
        self.assertEqual(data["user"]["phone"], "13800138001")
        self.assertEqual(data["user"]["role"], "member")
        self.assertEqual(data["user"]["status"], "active")
        self.assertEqual(data["user"]["tenant_id"], 501)
        self.assertEqual(claims["sub"], "1001")
        self.assertEqual(claims["role"], "member")
        self.assertEqual(claims["tenant_id"], 501)
        self.assertNotIn("password_hash", data["user"])
        self.assertNotIn("id_card", data["user"])

    def test_wrong_password_returns_401(self):
        with patch(
            "app.modules.auth.service.get_user_by_phone",
            new=AsyncMock(return_value=self._user()),
            create=True,
        ):
            response = self._client().post(
                "/api/v1/auth/login",
                json={"phone": "13800138001", "password": "WrongPassword123"},
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Invalid credentials")

    def test_missing_user_returns_401(self):
        with patch(
            "app.modules.auth.service.get_user_by_phone",
            new=AsyncMock(return_value=None),
            create=True,
        ):
            response = self._client().post(
                "/api/v1/auth/login",
                json={"phone": "13800138001", "password": "Secret12345"},
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Invalid credentials")

    def test_inactive_user_returns_403(self):
        with patch(
            "app.modules.auth.service.get_user_by_phone",
            new=AsyncMock(return_value=self._user(status="disabled")),
            create=True,
        ):
            response = self._client().post(
                "/api/v1/auth/login",
                json={"phone": "13800138001", "password": "Secret12345"},
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "User is not active")

    def test_org_admin_login_uses_controlled_context_claims(self):
        from app.core.security import decode_access_token

        with (
            patch(
                "app.modules.auth.service.get_user_by_phone",
                new=AsyncMock(return_value=self._user(role="org_admin")),
                create=True,
            ),
            patch(
                "app.modules.auth.service.get_controlled_auth_context",
                return_value={"org_id": 77},
                create=True,
            ),
            patch(
                "app.modules.auth.service.get_onboarding_account_for_login",
                new=AsyncMock(return_value=None),
                create=True,
            ),
        ):
            response = self._client().post(
                "/api/v1/auth/login",
                json={
                    "phone": "13800138001",
                    "password": "Secret12345",
                    "org_id": 999,
                },
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        claims = decode_access_token(data["access_token"])
        self.assertEqual(claims["org_id"], 77)
        self.assertEqual(data["user"]["org_id"], 77)
        self.assertNotEqual(claims["org_id"], 999)

    def test_province_admin_login_uses_controlled_region_claims(self):
        from app.core.security import decode_access_token

        with (
            patch(
                "app.modules.auth.service.get_user_by_phone",
                new=AsyncMock(return_value=self._user(role="province_admin")),
                create=True,
            ),
            patch(
                "app.modules.auth.service.get_controlled_auth_context",
                return_value={"province": "浙江省"},
                create=True,
            ),
        ):
            response = self._client().post(
                "/api/v1/auth/login",
                json={"phone": "13800138001", "password": "Secret12345"},
            )

        self.assertEqual(response.status_code, 200)
        claims = decode_access_token(response.json()["data"]["access_token"])
        self.assertEqual(claims["province"], "浙江省")
        self.assertIsNone(response.json()["data"]["user"]["city"])

    def test_city_admin_login_uses_controlled_region_claims(self):
        from app.core.security import decode_access_token

        with (
            patch(
                "app.modules.auth.service.get_user_by_phone",
                new=AsyncMock(return_value=self._user(role="city_admin")),
                create=True,
            ),
            patch(
                "app.modules.auth.service.get_controlled_auth_context",
                return_value={"province": "浙江省", "city": "杭州市"},
                create=True,
            ),
        ):
            response = self._client().post(
                "/api/v1/auth/login",
                json={"phone": "13800138001", "password": "Secret12345"},
            )

        self.assertEqual(response.status_code, 200)
        claims = decode_access_token(response.json()["data"]["access_token"])
        self.assertEqual(claims["province"], "浙江省")
        self.assertEqual(claims["city"], "杭州市")
