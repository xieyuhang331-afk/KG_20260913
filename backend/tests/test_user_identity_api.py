import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


class UserIdentityApiTests(unittest.TestCase):
    def _payload(self) -> dict:
        return {
            "real_name": "Zhang San",
            "id_card": "110101199001011234",
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
        if tenant_id is not None:
            claims["tenant_id"] = tenant_id
        token = create_access_token(claims)
        return {"Authorization": f"Bearer {token}"}

    def _client(self, fake_session_instance=None):
        from app.core.database import get_db_session
        from app.main import create_app

        app = create_app()

        class FakeSession:
            def __init__(self):
                self.commit_called = False
                self.rollback_called = False

            async def commit(self):
                self.commit_called = True

            async def rollback(self):
                self.rollback_called = True

        session = fake_session_instance or FakeSession()

        async def fake_session():
            yield session

        app.dependency_overrides[get_db_session] = fake_session
        return TestClient(app), session

    def test_member_submits_own_identity_successfully(self):
        user = SimpleNamespace(
            id=1001,
            real_name=None,
            id_card=None,
            verify_status=None,
        )

        async def update_identity(session, target_user, *, real_name, id_card, verify_status):
            self.assertIs(target_user, user)
            self.assertEqual(real_name, "Zhang San")
            self.assertEqual(id_card, "110101199001011234")
            self.assertEqual(verify_status, "submitted")
            target_user.real_name = real_name
            target_user.id_card = id_card
            target_user.verify_status = verify_status
            return target_user

        client, session = self._client()
        with (
            patch("app.modules.auth.service.get_user_by_id", new=AsyncMock(return_value=user)),
            patch("app.modules.auth.service.update_user_identity", new=AsyncMock(side_effect=update_identity)),
        ):
            response = client.post(
                "/api/v1/users/1001/identity",
                json=self._payload(),
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(user.real_name, "Zhang San")
        self.assertEqual(user.id_card, "110101199001011234")
        self.assertEqual(user.verify_status, "submitted")
        self.assertTrue(session.commit_called)
        self.assertEqual(
            response.json(),
            {
                "code": 0,
                "message": "ok",
                "data": {
                    "user_id": 1001,
                    "real_name": "Zhang San",
                    "id_card_masked": "110101********1234",
                    "verify_status": "submitted",
                },
            },
        )
        self.assertNotIn("id_card", response.json()["data"])
        self.assertNotIn("password_hash", response.json()["data"])

    def test_member_cannot_submit_other_user_identity(self):
        get_user_mock = AsyncMock()
        client, _ = self._client()
        with patch("app.modules.auth.service.get_user_by_id", new=get_user_mock):
            response = client.post(
                "/api/v1/users/2002/identity",
                json=self._payload(),
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 403)
        get_user_mock.assert_not_awaited()

    def test_non_member_roles_cannot_submit_identity(self):
        for role in ("org_admin", "super_admin", "province_admin", "city_admin"):
            with self.subTest(role=role):
                get_user_mock = AsyncMock()
                client, _ = self._client()
                with patch("app.modules.auth.service.get_user_by_id", new=get_user_mock):
                    response = client.post(
                        "/api/v1/users/1001/identity",
                        json=self._payload(),
                        headers=self._jwt_headers(user_id=1001, role=role),
                    )

                self.assertEqual(response.status_code, 403)
                get_user_mock.assert_not_awaited()

    def test_user_not_found_returns_404(self):
        update_mock = AsyncMock()
        client, _ = self._client()
        with (
            patch("app.modules.auth.service.get_user_by_id", new=AsyncMock(return_value=None)),
            patch("app.modules.auth.service.update_user_identity", new=update_mock),
        ):
            response = client.post(
                "/api/v1/users/1001/identity",
                json=self._payload(),
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "User not found")
        update_mock.assert_not_awaited()

    def test_invalid_payload_returns_422(self):
        client, _ = self._client()

        response = client.post(
            "/api/v1/users/1001/identity",
            json={"real_name": "Zhang San", "id_card": "bad"},
            headers=self._jwt_headers(user_id=1001),
        )

        self.assertEqual(response.status_code, 422)

    def test_missing_token_returns_401(self):
        client, _ = self._client()

        response = client.post(
            "/api/v1/users/1001/identity",
            json=self._payload(),
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Authentication required")
