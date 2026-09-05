import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


class UserIdentityApiTests(unittest.TestCase):
    def setUp(self):
        from app.core.config import get_settings

        self.authority_rows = {
            user_id: SimpleNamespace(
                id=user_id, role=role, tenant_id=None, tenant_org_id=None,
                status="active", exited_at=None, deletion_requested_at=None,
            )
            for user_id, role in (
                (1001, "member"), (1101, "org_admin"), (1102, "super_admin"),
                (1103, "province_admin"), (1104, "city_admin"), (1105, "therapist"),
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

    def _payload(self) -> dict:
        return {
            "real_name": "Synthetic User",
            "id_card": "110101199001011234",
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

    def _client(self):
        from app.main import create_app

        return TestClient(create_app())

    def _assert_retired(self, response) -> None:
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(
            response.json(),
            {
                "code": "LEGACY_IDENTITY_ENDPOINT_RETIRED",
                "message": "request rejected",
            },
        )

    def test_member_submits_own_identity_successfully(self):
        response = self._client().post(
            "/api/v1/users/1001/identity",
            json=self._payload(),
            headers=self._jwt_headers(user_id=1001),
        )
        self._assert_retired(response)

    def test_member_cannot_submit_other_user_identity(self):
        response = self._client().post(
            "/api/v1/users/2002/identity",
            json=self._payload(),
            headers=self._jwt_headers(user_id=1001),
        )
        self._assert_retired(response)
        self.assertNotIn("2002", response.text)

    def test_non_member_roles_cannot_submit_identity(self):
        roles = ("org_admin", "therapist", "super_admin", "province_admin", "city_admin")
        for role in roles:
            with self.subTest(role=role):
                response = self._client().post(
                    "/api/v1/users/1001/identity",
                    json=self._payload(),
                    headers=self._jwt_headers(user_id={
                        "org_admin": 1101, "super_admin": 1102, "province_admin": 1103,
                        "city_admin": 1104, "therapist": 1105,
                    }[role], role=role),
                )
                self._assert_retired(response)

    def test_user_not_found_returns_404(self):
        response = self._client().post(
            "/api/v1/users/99999999/identity",
            json=self._payload(),
            headers=self._jwt_headers(user_id=1001),
        )
        self._assert_retired(response)
        self.assertNotIn("99999999", response.text)

    def test_invalid_payload_returns_422(self):
        response = self._client().post(
            "/api/v1/users/1001/identity",
            json={"real_name": "Synthetic User", "id_card": "bad"},
            headers=self._jwt_headers(user_id=1001),
        )
        self._assert_retired(response)

    def test_missing_token_returns_401(self):
        response = self._client().post(
            "/api/v1/users/1001/identity",
            json=self._payload(),
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Authentication required")
