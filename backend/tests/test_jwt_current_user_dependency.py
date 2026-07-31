from __future__ import annotations

import unittest

from fastapi import HTTPException
from starlette.requests import Request


class JwtCurrentUserDependencyTests(unittest.IsolatedAsyncioTestCase):
    def _request(self, token: str | None) -> Request:
        headers = []
        if token is not None:
            headers.append((b"authorization", f"Bearer {token}".encode("utf-8")))
        return Request({"type": "http", "headers": headers})

    def _token(self, claims: dict) -> str:
        from app.core.security import create_access_token

        return create_access_token(claims)

    async def test_missing_token_returns_401(self):
        from app.core.security import get_current_user_from_jwt

        with self.assertRaises(HTTPException) as context:
            await get_current_user_from_jwt(self._request(None))

        self.assertEqual(context.exception.status_code, 401)
        self.assertEqual(context.exception.detail, "Authentication required")

    async def test_invalid_token_returns_401(self):
        from app.core.security import get_current_user_from_jwt

        with self.assertRaises(HTTPException) as context:
            await get_current_user_from_jwt(self._request("invalid-token"))

        self.assertEqual(context.exception.status_code, 401)
        self.assertEqual(context.exception.detail, "Invalid or expired token")

    async def test_member_token_builds_current_user(self):
        from app.core.security import get_current_user_from_jwt

        current_user = await get_current_user_from_jwt(
            self._request(self._token({"sub": "1001", "role": "member", "tenant_id": 501}))
        )

        self.assertEqual(current_user.id, 1001)
        self.assertEqual(current_user.role, "member")
        self.assertEqual(current_user.tenant_id, 501)

    async def test_org_admin_token_builds_current_user_with_org_context(self):
        from app.core.security import get_current_user_from_jwt

        current_user = await get_current_user_from_jwt(
            self._request(self._token({"sub": "2001", "role": "org_admin", "org_id": 77}))
        )

        self.assertEqual(current_user.id, 2001)
        self.assertEqual(current_user.role, "org_admin")
        self.assertEqual(current_user.org_id, 77)

    async def test_region_admin_token_builds_current_user_with_region_context(self):
        from app.core.security import get_current_user_from_jwt

        current_user = await get_current_user_from_jwt(
            self._request(
                self._token(
                    {
                        "sub": "3001",
                        "role": "city_admin",
                        "province": "浙江省",
                        "city": "杭州市",
                    }
                )
            )
        )

        self.assertEqual(current_user.role, "city_admin")
        self.assertEqual(current_user.province, "浙江省")
        self.assertEqual(current_user.city, "杭州市")
