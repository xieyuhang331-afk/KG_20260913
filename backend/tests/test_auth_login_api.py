from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

from tests.test_一期后端整改C2_1安全表达错误与请求上下文合同 import (
    assert_core_error_response,
)


class AuthLoginApiTests(unittest.TestCase):
    def _client(self, *, raise_server_exceptions: bool = True):
        from app.core.database import get_db_session
        from app.main import create_app

        app = create_app()

        class FakeSession:
            async def execute(self, _statement):
                return SimpleNamespace(scalar_one_or_none=lambda: None)

        async def fake_session():
            yield FakeSession()

        app.dependency_overrides[get_db_session] = fake_session
        return TestClient(app, client=("127.0.0.1", 50000), raise_server_exceptions=raise_server_exceptions)

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

    @staticmethod
    def _org_admin_account(*, totp_enabled: bool = True):
        return SimpleNamespace(
            totp_enabled=totp_enabled,
            totp_secret_ciphertext=b"synthetic-ciphertext",
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
        assert_core_error_response(response, 401, "INVALID_CREDENTIALS")

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
        assert_core_error_response(response, 401, "INVALID_CREDENTIALS")

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
        assert_core_error_response(response, 403, "USER_INACTIVE")

    def test_org_admin_without_onboarding_account_is_rejected_without_token(self):
        token_issuer = Mock(return_value="must-not-be-issued")
        with (
            patch(
                "app.modules.auth.service.get_user_by_phone",
                new=AsyncMock(return_value=self._user(role="org_admin")),
            ),
            patch(
                "app.modules.auth.service.get_onboarding_account_for_login",
                new=AsyncMock(return_value=None),
            ),
            patch("app.modules.auth.service.create_access_token", token_issuer),
        ):
            response = self._client().post(
                "/api/v1/auth/login",
                json={"phone": "13800138001", "password": "Secret12345"},
            )

        self.assertEqual(response.status_code, 403)
        assert_core_error_response(response, 403, "LOGIN_CONTEXT_NOT_CONFIGURED")
        token_issuer.assert_not_called()

    def test_org_admin_with_disabled_totp_is_rejected_without_false_amr(self):
        token_issuer = Mock(return_value="must-not-be-issued")
        with (
            patch(
                "app.modules.auth.service.get_user_by_phone",
                new=AsyncMock(return_value=self._user(role="org_admin")),
            ),
            patch(
                "app.modules.auth.service.get_onboarding_account_for_login",
                new=AsyncMock(return_value=self._org_admin_account(totp_enabled=False)),
            ),
            patch("app.modules.auth.service.create_access_token", token_issuer),
        ):
            response = self._client().post(
                "/api/v1/auth/login",
                json={"phone": "13800138001", "password": "Secret12345"},
            )

        self.assertEqual(response.status_code, 403)
        assert_core_error_response(response, 403, "LOGIN_CONTEXT_NOT_CONFIGURED")
        token_issuer.assert_not_called()

    def test_org_admin_missing_totp_is_rejected_without_token(self):
        token_issuer = Mock(return_value="must-not-be-issued")
        with (
            patch(
                "app.modules.auth.service.get_user_by_phone",
                new=AsyncMock(return_value=self._user(role="org_admin")),
            ),
            patch(
                "app.modules.auth.service.get_onboarding_account_for_login",
                new=AsyncMock(return_value=self._org_admin_account()),
            ),
            patch(
                "app.modules.institution_onboarding.service.OnboardingSecrets",
                return_value=SimpleNamespace(decrypt=lambda _: "synthetic-secret"),
            ),
            patch("app.modules.auth.service.create_access_token", token_issuer),
        ):
            response = self._client().post(
                "/api/v1/auth/login",
                json={"phone": "13800138001", "password": "Secret12345"},
            )

        self.assertEqual(response.status_code, 401)
        assert_core_error_response(response, 401, "TOTP_REQUIRED_OR_INVALID")
        token_issuer.assert_not_called()

    def test_org_admin_invalid_totp_is_rejected_without_token(self):
        token_issuer = Mock(return_value="must-not-be-issued")
        with (
            patch(
                "app.modules.auth.service.get_user_by_phone",
                new=AsyncMock(return_value=self._user(role="org_admin")),
            ),
            patch(
                "app.modules.auth.service.get_onboarding_account_for_login",
                new=AsyncMock(return_value=self._org_admin_account()),
            ),
            patch(
                "app.modules.institution_onboarding.service.OnboardingSecrets",
                return_value=SimpleNamespace(decrypt=lambda _: "synthetic-secret"),
            ),
            patch(
                "app.modules.institution_onboarding.domain.verify_totp",
                return_value=False,
            ),
            patch("app.modules.auth.service.create_access_token", token_issuer),
        ):
            response = self._client().post(
                "/api/v1/auth/login",
                json={
                    "phone": "13800138001",
                    "password": "Secret12345",
                    "totp_code": "123456",
                },
            )

        self.assertEqual(response.status_code, 401)
        assert_core_error_response(response, 401, "TOTP_REQUIRED_OR_INVALID")
        token_issuer.assert_not_called()

    def test_org_admin_account_dependency_failure_is_redacted_and_fail_closed(self):
        token_issuer = Mock(return_value="must-not-be-issued")
        with (
            patch(
                "app.modules.auth.service.get_user_by_phone",
                new=AsyncMock(return_value=self._user(role="org_admin")),
            ),
            patch(
                "app.modules.auth.service.get_onboarding_account_for_login",
                new=AsyncMock(side_effect=RuntimeError("synthetic dependency detail")),
            ),
            patch("app.modules.auth.service.create_access_token", token_issuer),
        ):
            response = self._client(raise_server_exceptions=False).post(
                "/api/v1/auth/login",
                json={"phone": "13800138001", "password": "Secret12345"},
            )

        self.assertEqual(response.status_code, 503)
        assert_core_error_response(response, 503, "AUTHENTICATION_UNAVAILABLE", retryable=True)
        self.assertNotIn("synthetic dependency detail", response.text)
        token_issuer.assert_not_called()

    def test_org_admin_secret_failure_is_redacted_and_fail_closed(self):
        token_issuer = Mock(return_value="must-not-be-issued")
        with (
            patch(
                "app.modules.auth.service.get_user_by_phone",
                new=AsyncMock(return_value=self._user(role="org_admin")),
            ),
            patch(
                "app.modules.auth.service.get_onboarding_account_for_login",
                new=AsyncMock(return_value=self._org_admin_account()),
            ),
            patch(
                "app.modules.institution_onboarding.service.OnboardingSecrets",
                side_effect=RuntimeError("synthetic secret detail"),
            ),
            patch("app.modules.auth.service.create_access_token", token_issuer),
        ):
            response = self._client(raise_server_exceptions=False).post(
                "/api/v1/auth/login",
                json={
                    "phone": "13800138001",
                    "password": "Secret12345",
                    "totp_code": "123456",
                },
            )

        self.assertEqual(response.status_code, 503)
        assert_core_error_response(response, 503, "AUTHENTICATION_UNAVAILABLE", retryable=True)
        self.assertNotIn("synthetic secret detail", response.text)
        token_issuer.assert_not_called()

    def test_org_admin_secret_decryption_failure_is_redacted_and_fail_closed(self):
        token_issuer = Mock(return_value="must-not-be-issued")
        decrypt = Mock(side_effect=RuntimeError("synthetic decrypt detail"))
        with (
            patch(
                "app.modules.auth.service.get_user_by_phone",
                new=AsyncMock(return_value=self._user(role="org_admin")),
            ),
            patch(
                "app.modules.auth.service.get_onboarding_account_for_login",
                new=AsyncMock(return_value=self._org_admin_account()),
            ),
            patch(
                "app.modules.institution_onboarding.service.OnboardingSecrets",
                return_value=SimpleNamespace(decrypt=decrypt),
            ),
            patch("app.modules.auth.service.create_access_token", token_issuer),
        ):
            response = self._client(raise_server_exceptions=False).post(
                "/api/v1/auth/login",
                json={
                    "phone": "13800138001",
                    "password": "Secret12345",
                    "totp_code": "123456",
                },
            )

        self.assertEqual(response.status_code, 503)
        assert_core_error_response(response, 503, "AUTHENTICATION_UNAVAILABLE", retryable=True)
        self.assertNotIn("synthetic decrypt detail", response.text)
        decrypt.assert_called_once_with(b"synthetic-ciphertext")
        token_issuer.assert_not_called()

    def test_org_admin_login_uses_controlled_context_and_verified_totp_amr(self):
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
                new=AsyncMock(return_value=self._org_admin_account()),
                create=True,
            ),
            patch(
                "app.modules.institution_onboarding.service.OnboardingSecrets",
                return_value=SimpleNamespace(decrypt=lambda _: "synthetic-secret"),
            ),
            patch(
                "app.modules.institution_onboarding.domain.verify_totp",
                return_value=True,
            ),
        ):
            response = self._client().post(
                "/api/v1/auth/login",
                json={
                    "phone": "13800138001",
                    "password": "Secret12345",
                    "org_id": 999,
                    "totp_code": "123456",
                },
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        claims = decode_access_token(data["access_token"])
        self.assertEqual(claims["org_id"], 77)
        self.assertEqual(claims["amr"], ["pwd", "totp"])
        self.assertEqual(data["user"]["org_id"], 77)
        self.assertNotEqual(claims["org_id"], 999)

    def test_therapist_login_keeps_existing_verified_totp_contract(self):
        from app.core.security import decode_access_token

        therapist_id = "019d0000-0000-7000-8000-000000000001"
        account = {
            "therapist_id": therapist_id,
            "therapist_status": "APPROVED_ACTIVE",
            "tenant_id": 501,
            "tenant_public_id": "019d0000-0000-7000-8000-000000000002",
            "totp_secret_ciphertext": b"synthetic-ciphertext",
            "totp_encryption_key_id": "synthetic-key",
        }
        with (
            patch(
                "app.modules.auth.service.get_user_by_phone",
                new=AsyncMock(
                    return_value=self._user(role="therapist", tenant_id=501)
                ),
            ),
            patch(
                "app.modules.auth.service.get_therapist_account_for_login",
                new=AsyncMock(return_value=account),
            ),
            patch(
                "app.modules.therapist_qualification.service.TherapistSecrets",
                return_value=SimpleNamespace(
                    decrypt_totp=lambda *_: "synthetic-secret"
                ),
            ),
            patch(
                "app.modules.institution_onboarding.domain.verify_totp",
                return_value=True,
            ),
        ):
            response = self._client().post(
                "/api/v1/auth/login",
                json={
                    "phone": "13800138001",
                    "password": "Secret12345",
                    "totp_code": "123456",
                },
            )

        self.assertEqual(response.status_code, 200)
        claims = decode_access_token(response.json()["data"]["access_token"])
        self.assertEqual(claims["amr"], ["pwd", "totp"])
        self.assertEqual(claims["therapist_id"], therapist_id)

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
