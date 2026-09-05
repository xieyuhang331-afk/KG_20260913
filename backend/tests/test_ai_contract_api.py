import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


class AIContractApiTests(unittest.TestCase):
    def setUp(self):
        from app.core.config import get_settings

        rows = {
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
            "app.core.认证当前性._read_authority", new=AsyncMock(side_effect=rows.get),
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

    def _contract(self, *, empty_health_data: bool = False):
        from app.modules.health_analysis.schemas import (
            AIDataCompleteness,
            AIHealthInputContract,
            AILatestIndicator,
            AIProfileContext,
            AISafetyPolicy,
            AISourceRefs,
            AITrendContext,
            AITrendPoint,
            AITrendWindow,
            AIUserContext,
        )

        return AIHealthInputContract(
            user_context=AIUserContext(user_id=1001, status="active", tenant_id=2001),
            profile_context=AIProfileContext(
                gender=None if empty_health_data else "F",
                birth_date=None if empty_health_data else date(1990, 1, 1),
                height=None if empty_health_data else Decimal("165.5"),
                weight=None if empty_health_data else Decimal("55.0"),
                blood_type=None if empty_health_data else "A",
                has_medical_history=None if empty_health_data else True,
                has_allergy_history=None if empty_health_data else False,
                has_family_history=None if empty_health_data else True,
                has_symptoms=None if empty_health_data else True,
            ),
            latest_indicators=[]
            if empty_health_data
            else [
                AILatestIndicator(
                    indicator_type="systolic_bp",
                    display_name="收缩压",
                    category="blood_pressure",
                    value=Decimal("121.00"),
                    unit="mmHg",
                    source="APP",
                    recorded_at=datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc),
                )
            ],
            trend_context=[]
            if empty_health_data
            else [
                AITrendContext(
                    indicator_type="systolic_bp",
                    display_name="收缩压",
                    category="blood_pressure",
                    unit="mmHg",
                    window=AITrendWindow(
                        start_at=datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc),
                        end_at=datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc),
                        days=1,
                    ),
                    points=[
                        AITrendPoint(
                            value=Decimal("121.00"),
                            recorded_at=datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc),
                            source="APP",
                        )
                    ],
                )
            ],
            data_completeness=AIDataCompleteness(
                profile_completed=not empty_health_data,
                indicator_count=0 if empty_health_data else 1,
                standard_indicator_count=15,
                missing_indicator_types=["systolic_bp"] if empty_health_data else [],
            ),
            source_refs=AISourceRefs(
                source_tables=["user", "health_profile", "health_indicator"],
                summary_source="HealthSummary",
                trend_source="HealthTrend",
                indicator_ids=[],
            ),
            safety_policy=AISafetyPolicy(
                no_diagnosis=True,
                no_prescription=True,
                no_treatment_plan=True,
                no_risk_prediction=True,
                no_health_score=True,
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

    def test_router_registers_internal_ai_health_input(self):
        client, _ = self._client()

        response = client.get("/openapi.json")

        self.assertEqual(response.status_code, 200)
        self.assertIn("/internal/v1/users/{user_id}/ai-health-input", response.json()["paths"])
        self.assertIn("get", response.json()["paths"]["/internal/v1/users/{user_id}/ai-health-input"])

    def test_internal_ai_health_input_returns_contract(self):
        client, _ = self._client()

        with patch(
            "app.modules.health_analysis.api.get_ai_health_input_contract",
            new=AsyncMock(return_value=self._contract()),
        ):
            response = client.get(
                "/internal/v1/users/1001/ai-health-input",
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["contract_version"], "f004.ai_input.v1")
        self.assertEqual(data["user_context"]["user_id"], 1001)
        self.assertEqual(data["latest_indicators"][0]["indicator_type"], "systolic_bp")

    def test_internal_ai_health_input_calls_service_with_session(self):
        client, session = self._client()

        async def get_contract(session_arg, *, user_id):
            self.assertIs(session_arg, session)
            self.assertEqual(user_id, 1001)
            return self._contract()

        service_mock = AsyncMock(side_effect=get_contract)
        with patch("app.modules.health_analysis.api.get_ai_health_input_contract", new=service_mock):
            response = client.get(
                "/internal/v1/users/1001/ai-health-input",
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 200)
        service_mock.assert_awaited_once()

    def test_internal_ai_health_input_supports_empty_health_data(self):
        client, _ = self._client()

        with patch(
            "app.modules.health_analysis.api.get_ai_health_input_contract",
            new=AsyncMock(return_value=self._contract(empty_health_data=True)),
        ):
            response = client.get(
                "/internal/v1/users/1001/ai-health-input",
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["latest_indicators"], [])
        self.assertEqual(data["trend_context"], [])
        self.assertFalse(data["data_completeness"]["profile_completed"])

    def test_internal_ai_health_input_response_is_safe(self):
        client, _ = self._client()

        with patch(
            "app.modules.health_analysis.api.get_ai_health_input_contract",
            new=AsyncMock(return_value=self._contract()),
        ):
            response = client.get(
                "/internal/v1/users/1001/ai-health-input",
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 200)
        serialized = response.text
        self.assertNotIn("password_hash", serialized)
        self.assertNotIn("phone", serialized)
        self.assertNotIn("id_card", serialized)
        self.assertNotIn("real_name", serialized)
        self.assertNotIn('"health_score"', serialized)
        self.assertNotIn('"risk_level"', serialized)
        self.assertNotIn('"diagnosis"', serialized)
        self.assertNotIn('"recommendation"', serialized)

    def test_internal_ai_health_input_does_not_call_ai(self):
        client, _ = self._client()

        with patch(
            "app.modules.health_analysis.api.get_ai_health_input_contract",
            new=AsyncMock(return_value=self._contract()),
        ):
            response = client.get(
                "/internal/v1/users/1001/ai-health-input",
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 200)
        serialized = response.text
        self.assertNotIn("prompt", serialized)
        self.assertNotIn("llm", serialized)
        self.assertNotIn("model_response", serialized)

    def test_internal_ai_health_input_requires_jwt(self):
        client, _ = self._client()
        service_mock = AsyncMock()

        with patch("app.modules.health_analysis.api.get_ai_health_input_contract", new=service_mock):
            response = client.get("/internal/v1/users/1001/ai-health-input")

        self.assertEqual(response.status_code, 401)
        service_mock.assert_not_awaited()

    def test_member_cannot_query_other_user_ai_health_input(self):
        client, _ = self._client()
        service_mock = AsyncMock()

        with patch("app.modules.health_analysis.api.get_ai_health_input_contract", new=service_mock):
            response = client.get(
                "/internal/v1/users/2002/ai-health-input",
                headers=self._jwt_headers(user_id=1001),
            )

        self.assertEqual(response.status_code, 403)
        service_mock.assert_not_awaited()

    def test_non_member_roles_cannot_query_ai_health_input(self):
        for role in ("org_admin", "super_admin", "province_admin", "city_admin"):
            with self.subTest(role=role):
                client, _ = self._client()
                service_mock = AsyncMock()

                with patch("app.modules.health_analysis.api.get_ai_health_input_contract", new=service_mock):
                    response = client.get(
                        "/internal/v1/users/1001/ai-health-input",
                        headers=self._jwt_headers(user_id={
                            "org_admin": 1101, "super_admin": 1102,
                            "province_admin": 1103, "city_admin": 1104,
                        }[role], role=role),
                    )

                self.assertEqual(response.status_code, 403)
                service_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
