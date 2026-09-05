from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


class AuthMeApiTests(unittest.TestCase):
    def setUp(self):
        from app.core.config import get_settings

        # Explicit unit-test authority rows, independent of token decoding.
        # Production currentness/role checks remain active; Fresh tests cover SQL.
        rows = {
            user_id: SimpleNamespace(
                id=user_id, role=role, tenant_id=tenant_id, tenant_org_id=None,
                status="active", exited_at=None, deletion_requested_at=None,
            )
            for user_id, role, tenant_id in (
                (1001, "member", 501), (2001, "org_admin", None),
                (3001, "province_admin", None), (4001, "city_admin", None),
            )
        }
        authority = patch(
            "app.core.认证当前性._read_authority",
            new=AsyncMock(side_effect=rows.get),
        )
        authority.start()
        self.addCleanup(authority.stop)
        context = patch.dict("os.environ", {"KG_AUTH_CONTEXT_MAP": (
            '{"2001":{"org_id":77},"3001":{"province":"浙江省"},'
            '"4001":{"province":"浙江省","city":"杭州市"}}'
        )})
        context.start()
        self.addCleanup(context.stop)
        get_settings.cache_clear()
        self.addCleanup(get_settings.cache_clear)

    def _client(self) -> TestClient:
        from app.main import create_app

        return TestClient(create_app())

    def _token(self, claims: dict) -> str:
        from app.core.security import create_access_token

        return create_access_token(claims)

    def _get_me(self, token: str | None):
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        return self._client().get("/api/v1/auth/me", headers=headers)

    def test_member_token_returns_current_user(self):
        token = self._token({"sub": "1001", "role": "member", "tenant_id": 501})

        response = self._get_me(token)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "code": 0,
                "message": "ok",
                "data": {
                    "id": 1001,
                    "role": "member",
                    "tenant_id": 501,
                    "org_id": None,
                    "province": None,
                    "city": None,
                },
            },
        )

    def test_missing_token_returns_401(self):
        response = self._get_me(None)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Authentication required")

    def test_invalid_token_returns_401(self):
        response = self._get_me("not-a-valid-token")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Invalid or expired token")

    def test_expired_token_returns_401(self):
        token = self._token(
            {
                "sub": "1001",
                "role": "member",
                "exp": datetime.now(timezone.utc) - timedelta(seconds=1),
            }
        )

        response = self._get_me(token)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Invalid or expired token")

    def test_org_admin_claims_are_returned(self):
        token = self._token({"sub": "2001", "role": "org_admin", "org_id": 77})

        response = self._get_me(token)

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["id"], 2001)
        self.assertEqual(data["role"], "org_admin")
        self.assertEqual(data["org_id"], 77)

    def test_province_admin_claims_are_returned(self):
        token = self._token({"sub": "3001", "role": "province_admin", "province": "浙江省"})

        response = self._get_me(token)

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["role"], "province_admin")
        self.assertEqual(data["province"], "浙江省")
        self.assertIsNone(data["city"])

    def test_city_admin_claims_are_returned(self):
        token = self._token(
            {
                "sub": "4001",
                "role": "city_admin",
                "province": "浙江省",
                "city": "杭州市",
            }
        )

        response = self._get_me(token)

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["role"], "city_admin")
        self.assertEqual(data["province"], "浙江省")
        self.assertEqual(data["city"], "杭州市")

    def test_response_does_not_expose_sensitive_or_internal_fields(self):
        token = self._token({"sub": "1001", "role": "member", "tenant_id": 501})

        response_text = self._get_me(token).text

        self.assertNotIn("password_hash", response_text)
        self.assertNotIn("jwt_secret", response_text)
        self.assertNotIn("auth_context_map", response_text)
