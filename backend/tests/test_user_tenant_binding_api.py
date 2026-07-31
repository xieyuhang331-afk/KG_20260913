import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient


class UserTenantBindingApiTests(unittest.TestCase):
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

    def _service_response(self):
        from app.modules.auth.schemas import TenantBindingResponse

        return TenantBindingResponse(
            user_id=1001,
            tenant_id=501,
            tenant_code="TACTIVE001",
            tenant_name="Kanglin West Lake Store",
            bound_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
        )

    def test_router_registers_tenant_binding(self):
        client, _ = self._client()

        response = client.get("/openapi.json")

        self.assertEqual(response.status_code, 200)
        self.assertIn("/api/v1/users/{user_id}/tenant-binding", response.json()["paths"])
        self.assertIn("post", response.json()["paths"]["/api/v1/users/{user_id}/tenant-binding"])

    def test_member_binds_own_tenant_successfully(self):
        client, session = self._client()

        async def bind(session_arg, current_user, user_id, payload):
            self.assertIs(session_arg, session)
            self.assertEqual(current_user.id, 1001)
            self.assertEqual(current_user.role, "member")
            self.assertEqual(current_user.tenant_id, 301)
            self.assertEqual(user_id, 1001)
            self.assertEqual(payload.tenant_id, 501)
            return self._service_response()

        with patch("app.modules.auth.api.bind_user_tenant", new=AsyncMock(side_effect=bind)):
            response = client.post(
                "/api/v1/users/1001/tenant-binding",
                json={"tenant_id": 501},
                headers=self._jwt_headers(user_id=1001, tenant_id=301),
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["user_id"], 1001)
        self.assertEqual(data["tenant_id"], 501)
        self.assertEqual(data["tenant_code"], "TACTIVE001")
        self.assertEqual(data["tenant_name"], "Kanglin West Lake Store")
        self.assertNotIn("password_hash", data)
        self.assertNotIn("id_card", data)
        self.assertNotIn("real_name", data)
        self.assertNotIn("credit_code", data)
        self.assertNotIn("license_no", data)
        self.assertNotIn("org_id", data)

    def test_member_cannot_bind_other_user(self):
        client, _ = self._client()
        service_mock = AsyncMock()

        with patch("app.modules.auth.api.bind_user_tenant", new=service_mock):
            response = client.post(
                "/api/v1/users/2002/tenant-binding",
                json={"tenant_id": 501},
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 403)
        service_mock.assert_not_awaited()

    def test_non_member_cannot_bind_tenant(self):
        for role in ("org_admin", "super_admin", "province_admin", "city_admin"):
            with self.subTest(role=role):
                client, _ = self._client()
                service_mock = AsyncMock()

                with patch("app.modules.auth.api.bind_user_tenant", new=service_mock):
                    response = client.post(
                        "/api/v1/users/1001/tenant-binding",
                        json={"tenant_id": 501},
                        headers=self._jwt_headers(user_id=1001, role=role),
                    )

                self.assertEqual(response.status_code, 403)
                service_mock.assert_not_awaited()

    def test_invalid_path_and_body_return_422(self):
        client, _ = self._client()

        path_response = client.post(
            "/api/v1/users/bad/tenant-binding",
            json={"tenant_id": 501},
            headers=self._jwt_headers(),
        )
        body_response = client.post(
            "/api/v1/users/1001/tenant-binding",
            json={},
            headers=self._jwt_headers(),
        )
        invalid_tenant_response = client.post(
            "/api/v1/users/1001/tenant-binding",
            json={"tenant_id": 0},
            headers=self._jwt_headers(),
        )

        self.assertEqual(path_response.status_code, 422)
        self.assertEqual(body_response.status_code, 422)
        self.assertEqual(invalid_tenant_response.status_code, 422)

    def test_service_404_and_409_are_propagated(self):
        for status_code, detail in ((404, "Tenant not found"), (409, "Tenant is not active")):
            with self.subTest(status_code=status_code):
                client, _ = self._client()

                with patch(
                    "app.modules.auth.api.bind_user_tenant",
                    new=AsyncMock(side_effect=HTTPException(status_code=status_code, detail=detail)),
                ):
                    response = client.post(
                        "/api/v1/users/1001/tenant-binding",
                        json={"tenant_id": 501},
                        headers=self._jwt_headers(),
                    )

                self.assertEqual(response.status_code, status_code)
                self.assertEqual(response.json()["detail"], detail)

    def test_missing_token_returns_401(self):
        client, _ = self._client()

        response = client.post(
            "/api/v1/users/1001/tenant-binding",
            json={"tenant_id": 501},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Authentication required")
