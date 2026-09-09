import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, OperationalError

from tests.test_一期后端整改C2_1安全表达错误与请求上下文合同 import (
    assert_core_error_response,
)


class UserRegistrationApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._private_storage = tempfile.TemporaryDirectory()
        cls._private_storage_environment = patch.dict(
            os.environ,
            {
                "KG_PRIVATE_FILE_STORAGE_BACKEND": "local_filesystem",
                "KG_PRIVATE_FILE_STORAGE_ROOT": cls._private_storage.name,
            },
        )
        cls._private_storage_environment.start()
        from app.core.config import get_settings

        get_settings.cache_clear()

    @classmethod
    def tearDownClass(cls) -> None:
        from app.core.config import get_settings

        get_settings.cache_clear()
        cls._private_storage_environment.stop()
        cls._private_storage.cleanup()

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

            async def close(self):
                return None

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

        async def create_user(session, *, phone, password_hash):
            self.assertEqual(phone, "13800138001")
            self.assertNotEqual(password_hash, "Secret12345")
            self.assertTrue(password_hash.startswith("pbkdf2_sha256$"))
            return created_user

        with patch(
            "app.modules.auth.service.create_registered_member",
            new=AsyncMock(side_effect=create_user),
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
        class DriverUnique(Exception):
            sqlstate = "23505"
            constraint_name = "uq_user_phone"

        error = IntegrityError(None, None, DriverUnique())
        with patch(
            "app.modules.auth.service.create_registered_member",
            new=AsyncMock(side_effect=error),
        ):
            response = self._client().post(
                "/api/v1/users/register", json=self._payload()
            )

        self.assertEqual(response.status_code, 409)
        assert_core_error_response(response, 409, "USER_EXISTS")

    def test_unique_violation_returns_409_and_rolls_back(self):
        class FakeSession:
            def __init__(self):
                self.rollback_called = False

            async def commit(self):
                return None

            async def rollback(self):
                self.rollback_called = True

            async def close(self):
                return None

        fake_session_instance = FakeSession()

        async def fake_session():
            yield fake_session_instance

        from app.core.database import get_db_session
        from app.main import create_app

        app = create_app()
        app.dependency_overrides[get_db_session] = fake_session

        class DriverUnique(Exception):
            sqlstate = "23505"
            constraint_name = "uq_user_phone"

        integrity_error = IntegrityError(None, None, DriverUnique())

        with patch(
            "app.modules.auth.service.create_registered_member",
            new=AsyncMock(side_effect=integrity_error),
        ):
            response = TestClient(app, client=("127.0.0.1", 50000)).post(
                "/api/v1/users/register", json=self._payload()
            )

        self.assertEqual(response.status_code, 409)
        assert_core_error_response(response, 409, "USER_EXISTS")
        self.assertTrue(fake_session_instance.rollback_called)

    def test_non_phone_integrity_violation_is_not_misreported_as_user_exists(self):
        class DriverUnique(Exception):
            sqlstate = "23505"
            constraint_name = "uq_unrelated_constraint"

        error = IntegrityError(None, None, DriverUnique())
        with patch(
            "app.modules.auth.service.create_registered_member",
            new=AsyncMock(side_effect=error),
        ):
            response = self._client().post(
                "/api/v1/users/register", json=self._payload()
            )

        self.assertEqual(response.status_code, 500)
        assert_core_error_response(response, 500, "INTERNAL_ERROR")

    def test_registered_database_unavailability_returns_503(self):
        with patch(
            "app.modules.auth.service.create_registered_member",
            new=AsyncMock(side_effect=ConnectionError("synthetic unavailable")),
        ):
            response = self._client().post(
                "/api/v1/users/register", json=self._payload()
            )

        self.assertEqual(response.status_code, 503)
        assert_core_error_response(
            response, 503, "AUTHENTICATION_UNAVAILABLE", retryable=True
        )

    def test_extra_role_and_tenant_fields_cannot_change_fixed_result(self):
        created_user = SimpleNamespace(
            id=702,
            phone="13800138001",
            role="member",
            status="active",
            verify_status=None,
            tenant_id=None,
            created_at=None,
        )
        payload = {
            **self._payload(),
            "role": "super_admin",
            "status": "disabled",
            "tenant_id": 999999,
            "verify_status": "approved",
        }
        with patch(
            "app.modules.auth.service.create_registered_member",
            new=AsyncMock(return_value=created_user),
        ):
            response = self._client().post("/api/v1/users/register", json=payload)

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["role"], "member")
        self.assertEqual(data["status"], "active")
        self.assertIsNone(data["tenant_id"])
        self.assertIsNone(data["verify_status"])

    def test_commit_unknown_returns_original_snapshot_only_after_fresh_confirmation(self):
        from app.core.database import get_db_session
        from app.main import create_app

        class Session:
            def __init__(self):
                self.rollback_called = False
                self.close_called = False

            async def commit(self):
                raise ConnectionError("synthetic commit outcome unknown")

            async def rollback(self):
                self.rollback_called = True

            async def close(self):
                self.close_called = True

        session = Session()

        async def fake_session():
            yield session

        created_user = SimpleNamespace(
            id=703,
            phone="13800138001",
            role="member",
            status="active",
            verify_status=None,
            tenant_id=None,
            created_at=None,
        )
        app = create_app()
        app.dependency_overrides[get_db_session] = fake_session
        create = AsyncMock(return_value=created_user)
        confirm = AsyncMock(return_value=True)
        with (
            patch("app.modules.auth.service.create_registered_member", new=create),
            patch("app.modules.auth.service._registration_insert_is_visible", new=confirm),
        ):
            response = TestClient(app, client=("127.0.0.1", 50000)).post(
                "/api/v1/users/register", json=self._payload()
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["id"], 703)
        self.assertTrue(session.rollback_called)
        self.assertTrue(session.close_called)
        create.assert_awaited_once()
        confirm.assert_awaited_once()

    def test_commit_unknown_without_exact_confirmation_returns_503_and_does_not_rewrite(self):
        from app.core.database import get_db_session
        from app.main import create_app

        class Session:
            async def commit(self):
                raise ConnectionError("synthetic commit outcome unknown")

            async def rollback(self):
                return None

            async def close(self):
                return None

        async def fake_session():
            yield Session()

        created_user = SimpleNamespace(
            id=704,
            phone="13800138001",
            role="member",
            status="active",
            verify_status=None,
            tenant_id=None,
            created_at=None,
        )
        app = create_app()
        app.dependency_overrides[get_db_session] = fake_session
        create = AsyncMock(return_value=created_user)
        with (
            patch("app.modules.auth.service.create_registered_member", new=create),
            patch(
                "app.modules.auth.service._registration_insert_is_visible",
                new=AsyncMock(return_value=False),
            ),
        ):
            response = TestClient(app, client=("127.0.0.1", 50000)).post(
                "/api/v1/users/register", json=self._payload()
            )

        self.assertEqual(response.status_code, 503)
        assert_core_error_response(
            response, 503, "AUTHENTICATION_UNAVAILABLE", retryable=True
        )
        create.assert_awaited_once()

    def test_commit_server_rejection_returns_503_without_unknown_confirmation(self):
        from app.core.database import get_db_session
        from app.main import create_app

        class DriverRejected(Exception):
            def __init__(self, sqlstate):
                self.sqlstate = sqlstate

        class Session:
            def __init__(self, sqlstate):
                self.sqlstate = sqlstate

            async def commit(self):
                raise OperationalError(None, None, DriverRejected(self.sqlstate))

            async def rollback(self):
                return None

            async def close(self):
                return None

        created_user = SimpleNamespace(
            id=706,
            phone="13800138001",
            role="member",
            status="active",
            verify_status=None,
            tenant_id=None,
            created_at=None,
        )
        for sqlstate in ("40001", "40P01"):
            with self.subTest(sqlstate=sqlstate):
                app = create_app()

                async def fake_session(sqlstate=sqlstate):
                    yield Session(sqlstate)

                app.dependency_overrides[get_db_session] = fake_session
                confirm = AsyncMock()
                with (
                    patch(
                        "app.modules.auth.service.create_registered_member",
                        new=AsyncMock(return_value=created_user),
                    ),
                    patch(
                        "app.modules.auth.service._registration_insert_is_visible",
                        new=confirm,
                    ),
                ):
                    response = TestClient(
                        app, client=("127.0.0.1", 50000)
                    ).post("/api/v1/users/register", json=self._payload())

                self.assertEqual(response.status_code, 503)
                assert_core_error_response(
                    response, 503, "AUTHENTICATION_UNAVAILABLE", retryable=True
                )
                confirm.assert_not_awaited()

    def test_commit_cancellation_has_priority_and_does_not_confirm(self):
        from app.modules.auth.schemas import UserRegisterRequest
        from app.modules.auth.service import register_user

        class Session:
            async def commit(self):
                raise asyncio.CancelledError

        created_user = SimpleNamespace(
            id=705,
            phone="13800138001",
            role="member",
            status="active",
            verify_status=None,
            tenant_id=None,
            created_at=None,
        )
        confirm = AsyncMock()
        with (
            patch(
                "app.modules.auth.service.create_registered_member",
                new=AsyncMock(return_value=created_user),
            ),
            patch("app.modules.auth.service._registration_insert_is_visible", new=confirm),
            self.assertRaises(asyncio.CancelledError),
        ):
            asyncio.run(register_user(Session(), UserRegisterRequest(**self._payload())))
        confirm.assert_not_awaited()

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
