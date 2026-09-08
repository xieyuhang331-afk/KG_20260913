import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from tests.test_一期后端整改C2_1安全表达错误与请求上下文合同 import (
    assert_core_error_response,
)


class HealthSummaryApiTests(unittest.TestCase):
    def setUp(self):
        from app.core.config import get_settings

        # These fixed authority rows do not depend on decoded token claims.
        self.authority_rows = {
            user_id: SimpleNamespace(
                id=user_id, role=role, tenant_id=None, tenant_org_id=None,
                status="active", exited_at=None, deletion_requested_at=None,
            )
            for user_id, role in (
                (1001, "member"), (1101, "org_admin"), (1102, "super_admin"),
                (1103, "province_admin"), (1104, "city_admin"),
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

    def _jwt_headers(self, *, user_id: int = 1001, role: str = "member", tenant_id: int | None = None) -> dict:
        from app.core.security import create_access_token

        claims = {"sub": str(user_id), "role": role}
        claims.update({
            1103: {"province": "ZJ"}, 1104: {"province": "ZJ", "city": "HZ"},
        }.get(user_id, {}))
        if tenant_id is not None:
            claims["tenant_id"] = tenant_id
        token = create_access_token(claims)
        return {"Authorization": f"Bearer {token}"}

    def _summary(self, *, profile_exists: bool = True, indicators: bool = True):
        from app.modules.health_analysis.schemas import (
            DataCompleteness,
            HealthSummary,
            HealthSummaryProfile,
            HealthSummaryUser,
            LatestIndicatorSummary,
        )

        latest_indicators = []
        indicator_updated_at = None
        if indicators:
            indicator_updated_at = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
            latest_indicators.append(
                LatestIndicatorSummary(
                    indicator_type="systolic_bp",
                    display_name="收缩压",
                    category="blood_pressure",
                    value=Decimal("120.00"),
                    unit="mmHg",
                    source="APP",
                    recorded_at=indicator_updated_at,
                    created_at=datetime(2026, 7, 29, 8, 1, tzinfo=timezone.utc),
                    quality_flags=[],
                )
            )

        profile = HealthSummaryProfile(exists=False)
        if profile_exists:
            profile = HealthSummaryProfile(
                exists=True,
                gender="F",
                birth_date=date(1990, 1, 1),
                height=Decimal("165.5"),
                weight=Decimal("55.0"),
                blood_type="A",
                has_medical_history=True,
                has_allergy_history=False,
                has_family_history=True,
                has_symptoms=True,
                smoking="never",
                drinking="none",
                sleep_quality="normal",
                bowel_urination="normal",
            )

        return HealthSummary(
            user=HealthSummaryUser(user_id=1001, status="active", tenant_id=2001),
            profile=profile,
            latest_indicators=latest_indicators,
            indicator_updated_at=indicator_updated_at,
            data_completeness=DataCompleteness(
                profile_completed=profile_exists,
                indicator_count=len(latest_indicators),
                standard_indicator_count=15,
                missing_indicator_types=[] if indicators else ["systolic_bp"],
            ),
        )

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

    def test_router_registers_health_summary(self):
        client, _ = self._client()

        response = client.get("/openapi.json")

        self.assertEqual(response.status_code, 200)
        self.assertIn("/api/v1/users/{user_id}/health-summary", response.json()["paths"])
        self.assertIn("get", response.json()["paths"]["/api/v1/users/{user_id}/health-summary"])

    def test_member_queries_own_health_summary_successfully(self):
        client, session = self._client()

        async def get_summary(session_arg, *, user_id):
            self.assertIs(session_arg, session)
            self.assertEqual(user_id, 1001)
            return self._summary()

        with patch(
            "app.modules.health_analysis.api.get_health_summary",
            new=AsyncMock(side_effect=get_summary),
        ):
            response = client.get(
                "/api/v1/users/1001/health-summary",
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["user"]["user_id"], 1001)
        self.assertTrue(data["profile"]["exists"])
        self.assertEqual(data["latest_indicators"][0]["indicator_type"], "systolic_bp")
        self.assertEqual(data["indicator_updated_at"], "2026-07-29T08:00:00Z")

    def test_user_not_found_returns_404(self):
        client, _ = self._client()

        with patch(
            "app.modules.health_analysis.api.get_health_summary",
            new=AsyncMock(side_effect=HTTPException(status_code=404, detail="User not found")),
        ):
            response = client.get(
                "/api/v1/users/1001/health-summary",
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 404)
        assert_core_error_response(response, 404, "USER_NOT_FOUND")

    def test_user_without_health_profile_still_returns_summary(self):
        client, _ = self._client()

        with patch(
            "app.modules.health_analysis.api.get_health_summary",
            new=AsyncMock(return_value=self._summary(profile_exists=False)),
        ):
            response = client.get(
                "/api/v1/users/1001/health-summary",
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertFalse(data["profile"]["exists"])
        self.assertFalse(data["data_completeness"]["profile_completed"])

    def test_user_without_indicators_returns_empty_indicator_list(self):
        client, _ = self._client()

        with patch(
            "app.modules.health_analysis.api.get_health_summary",
            new=AsyncMock(return_value=self._summary(indicators=False)),
        ):
            response = client.get(
                "/api/v1/users/1001/health-summary",
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["latest_indicators"], [])
        self.assertIsNone(data["indicator_updated_at"])

    def test_health_summary_requires_jwt(self):
        client, _ = self._client()
        service_mock = AsyncMock()

        with patch("app.modules.health_analysis.api.get_health_summary", new=service_mock):
            response = client.get("/api/v1/users/1001/health-summary")

        self.assertEqual(response.status_code, 401)
        service_mock.assert_not_awaited()

    def test_member_cannot_query_other_user_health_summary(self):
        client, _ = self._client()
        service_mock = AsyncMock()

        with patch("app.modules.health_analysis.api.get_health_summary", new=service_mock):
            response = client.get(
                "/api/v1/users/2002/health-summary",
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 403)
        service_mock.assert_not_awaited()

    def test_non_member_roles_cannot_query_health_summary(self):
        for role in ("org_admin", "super_admin", "province_admin", "city_admin"):
            with self.subTest(role=role):
                client, _ = self._client()
                service_mock = AsyncMock()

                with patch("app.modules.health_analysis.api.get_health_summary", new=service_mock):
                    response = client.get(
                        "/api/v1/users/1001/health-summary",
                        headers=self._jwt_headers(user_id={
                            "org_admin": 1101, "super_admin": 1102,
                            "province_admin": 1103, "city_admin": 1104,
                        }[role], role=role),
                    )

                self.assertEqual(response.status_code, 403)
                service_mock.assert_not_awaited()

    def test_response_does_not_expose_sensitive_or_medical_judgement_fields(self):
        client, _ = self._client()

        with patch(
            "app.modules.health_analysis.api.get_health_summary",
            new=AsyncMock(return_value=self._summary()),
        ):
            response = client.get(
                "/api/v1/users/1001/health-summary",
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 200)
        serialized = response.text
        self.assertNotIn("password_hash", serialized)
        self.assertNotIn("id_card", serialized)
        self.assertNotIn("real_name", serialized)
        self.assertNotIn("risk_level", serialized)
        self.assertNotIn("health_score", serialized)
