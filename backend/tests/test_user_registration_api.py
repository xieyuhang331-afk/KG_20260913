import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError


class UserRegistrationApiTests(unittest.TestCase):
    def _payload(self) -> dict:
        return {
            "phone": "13800138001",
            "password": "Secret12345",
        }

    def _client(self):
        from app.core.database import get_db_session
        from app.main import create_app

        app = create_app()

        class FakeSession:
            def __init__(self):
                self.rollback_called = False

            async def commit(self):
                return None

            async def rollback(self):
                self.rollback_called = True

        async def fake_session():
            yield FakeSession()

        app.dependency_overrides[get_db_session] = fake_session
        return TestClient(app, client=("127.0.0.1", 50000))

    def test_router_registers_post_user_register(self):
        response = self._client().get("/openapi.json")

        self.assertEqual(response.status_code, 200)
        self.assertIn("/api/v1/users/register", response.json()["paths"])
        self.assertIn("post", response.json()["paths"]["/api/v1/users/register"])

    def test_user_successfully_registers(self):
        created_user = SimpleNamespace(
            id=701,
            phone="13800138001",
            role="member",
            status="active",
            verify_status=None,
            tenant_id=None,
            created_at=None,
        )

        async def create_user(session, *, user_data):
            self.assertEqual(user_data["phone"], "13800138001")
            self.assertEqual(user_data["role"], "member")
            self.assertEqual(user_data["status"], "active")
            self.assertIsNone(user_data["tenant_id"])
            self.assertNotEqual(user_data["password_hash"], "Secret12345")
            self.assertTrue(user_data["password_hash"].startswith("pbkdf2_sha256$"))
            return created_user

        with (
            patch("app.modules.auth.service.user_exists_by_phone", new=AsyncMock(return_value=False)),
            patch("app.modules.auth.service.create_user_record", new=AsyncMock(side_effect=create_user)),
        ):
            response = self._client().post("/api/v1/users/register", json=self._payload())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "code": 0,
                "message": "ok",
                "data": {
                    "id": 701,
                    "phone": "13800138001",
                    "role": "member",
                    "status": "active",
                    "verify_status": None,
                    "tenant_id": None,
                    "created_at": None,
                },
            },
        )

    def test_duplicate_phone_returns_409(self):
        create_mock = AsyncMock()
        with (
            patch("app.modules.auth.service.user_exists_by_phone", new=AsyncMock(return_value=True)),
            patch("app.modules.auth.service.create_user_record", new=create_mock),
        ):
            response = self._client().post("/api/v1/users/register", json=self._payload())

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "User already exists")
        create_mock.assert_not_awaited()

    def test_unique_violation_returns_409_and_rolls_back(self):
        class FakeSession:
            def __init__(self):
                self.rollback_called = False

            async def commit(self):
                return None

            async def rollback(self):
                self.rollback_called = True

        fake_session_instance = FakeSession()

        async def fake_session():
            yield fake_session_instance

        from app.core.database import get_db_session
        from app.main import create_app

        app = create_app()
        app.dependency_overrides[get_db_session] = fake_session

        integrity_error = IntegrityError(
            statement="INSERT INTO user",
            params={"phone": "13800138001"},
            orig=Exception("duplicate key value violates unique constraint"),
        )

        with (
            patch("app.modules.auth.service.user_exists_by_phone", new=AsyncMock(return_value=False)),
            patch("app.modules.auth.service.create_user_record", new=AsyncMock(side_effect=integrity_error)),
        ):
            response = TestClient(app, client=("127.0.0.1", 50000)).post("/api/v1/users/register", json=self._payload())

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "User already exists")
        self.assertTrue(fake_session_instance.rollback_called)

    def test_invalid_payload_returns_422(self):
        payload = self._payload()
        payload["phone"] = "bad-phone"

        response = self._client().post("/api/v1/users/register", json=payload)

        self.assertEqual(response.status_code, 422)

    def test_short_password_returns_422(self):
        payload = self._payload()
        payload["password"] = "short"

        response = self._client().post("/api/v1/users/register", json=payload)

        self.assertEqual(response.status_code, 422)
