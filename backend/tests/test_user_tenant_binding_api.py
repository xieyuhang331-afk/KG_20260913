import unittest

from fastapi.testclient import TestClient


class UserTenantBindingApiTests(unittest.TestCase):
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
        from app.main import create_app

        return TestClient(create_app())

    def _assert_retired(self, response) -> None:
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(
            response.json(),
            {
                "code": "LEGACY_MEMBER_TENANT_BINDING_RETIRED",
                "message": "request rejected",
            },
        )

    def test_router_registers_tenant_binding(self):
        document = self._client().get("/openapi.json").json()
        operation = document["paths"]["/api/v1/users/{user_id}/tenant-binding"]["post"]
        self.assertTrue(operation["deprecated"])
        self.assertIn("410", operation["responses"])

    def test_member_binds_own_tenant_successfully(self):
        response = self._client().post(
            "/api/v1/users/1001/tenant-binding",
            json={"tenant_id": 501},
            headers=self._jwt_headers(user_id=1001, tenant_id=301),
        )
        self._assert_retired(response)

    def test_member_cannot_bind_other_user(self):
        response = self._client().post(
            "/api/v1/users/2002/tenant-binding",
            json={"tenant_id": 501},
            headers=self._jwt_headers(user_id=1001),
        )
        self._assert_retired(response)
        self.assertNotIn("2002", response.text)

    def test_non_member_cannot_bind_tenant(self):
        roles = ("org_admin", "therapist", "super_admin", "province_admin", "city_admin")
        for role in roles:
            with self.subTest(role=role):
                response = self._client().post(
                    "/api/v1/users/1001/tenant-binding",
                    json={"tenant_id": 501},
                    headers=self._jwt_headers(user_id=1001, role=role),
                )
                self._assert_retired(response)

    def test_invalid_path_and_body_return_422(self):
        path_response = self._client().post(
            "/api/v1/users/bad/tenant-binding",
            json={"tenant_id": 501},
            headers=self._jwt_headers(),
        )
        body_response = self._client().post(
            "/api/v1/users/1001/tenant-binding",
            json={},
            headers=self._jwt_headers(),
        )
        invalid_tenant_response = self._client().post(
            "/api/v1/users/1001/tenant-binding",
            json={"tenant_id": 0},
            headers=self._jwt_headers(),
        )
        self.assertEqual(path_response.status_code, 422)
        self._assert_retired(body_response)
        self._assert_retired(invalid_tenant_response)

    def test_service_404_and_409_are_propagated(self):
        for user_id in (1001, 99999999):
            with self.subTest(user_id=user_id):
                response = self._client().post(
                    f"/api/v1/users/{user_id}/tenant-binding",
                    json={"tenant_id": 99999999},
                    headers=self._jwt_headers(),
                )
                self._assert_retired(response)
                self.assertNotIn("99999999", response.text)

    def test_missing_token_returns_401(self):
        response = self._client().post(
            "/api/v1/users/1001/tenant-binding",
            json={"tenant_id": 501},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Authentication required")
