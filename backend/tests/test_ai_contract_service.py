import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from app.modules.health_analysis.schemas import (
    AIHealthInputContract,
    DataCompleteness,
    HealthSummary,
    HealthSummaryProfile,
    HealthSummaryUser,
    HealthTrend,
    LatestIndicatorSummary,
    TrendPoint,
)


class AIContractServiceTests(unittest.IsolatedAsyncioTestCase):
    def _user(self):
        return SimpleNamespace(
            id=1001,
            status="active",
            tenant_id=2001,
            phone="13800138000",
            password_hash="hashed-secret",
            real_name="Sensitive Name",
            id_card="110101199001011234",
        )

    def _profile(self):
        return SimpleNamespace(id=3001, user_id=1001)

    def _summary(self, *, with_profile: bool = True, with_indicators: bool = True) -> HealthSummary:
        latest_indicators = []
        if with_indicators:
            latest_indicators.append(
                LatestIndicatorSummary(
                    indicator_type="systolic_bp",
                    display_name="收缩压",
                    category="blood_pressure",
                    value=Decimal("121.00"),
                    unit="mmHg",
                    source="APP",
                    recorded_at=datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc),
                    created_at=datetime(2026, 7, 29, 8, 1, tzinfo=timezone.utc),
                )
            )

        return HealthSummary(
            user=HealthSummaryUser(user_id=1001, status="active", tenant_id=2001),
            profile=HealthSummaryProfile(
                exists=with_profile,
                gender="F" if with_profile else None,
                birth_date=date(1990, 1, 1) if with_profile else None,
                height=Decimal("165.5") if with_profile else None,
                weight=Decimal("55.0") if with_profile else None,
                blood_type="A" if with_profile else None,
                has_medical_history=True if with_profile else None,
                has_allergy_history=False if with_profile else None,
                has_family_history=True if with_profile else None,
                has_symptoms=True if with_profile else None,
            ),
            latest_indicators=latest_indicators,
            indicator_updated_at=datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc) if latest_indicators else None,
            data_completeness=DataCompleteness(
                profile_completed=with_profile,
                indicator_count=len(latest_indicators),
                standard_indicator_count=15,
                missing_indicator_types=[] if latest_indicators else ["systolic_bp"],
            ),
        )

    def _trend(self) -> HealthTrend:
        return HealthTrend(
            indicator_type="systolic_bp",
            display_name="收缩压",
            category="blood_pressure",
            unit="mmHg",
            points=[
                TrendPoint(
                    value=Decimal("121.00"),
                    recorded_at=datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc),
                    source="APP",
                )
            ],
        )

    def _contract(self, *, with_indicators: bool = True, with_trends: bool = True) -> AIHealthInputContract:
        from app.modules.health_analysis.ai_contract_builder import build_ai_health_input_contract

        return build_ai_health_input_contract(
            user=self._user(),
            health_profile=self._profile(),
            health_summary=self._summary(with_indicators=with_indicators),
            health_trends=[self._trend()] if with_trends else [],
        )

    async def test_get_ai_health_input_contract_generates_normal_contract(self):
        from app.modules.health_analysis.ai_contract_service import get_ai_health_input_contract

        session = object()
        with (
            patch("app.modules.health_analysis.ai_contract_service.get_summary_user", AsyncMock(return_value=self._user())),
            patch(
                "app.modules.health_analysis.ai_contract_service.get_summary_profile",
                AsyncMock(return_value=self._profile()),
            ),
            patch(
                "app.modules.health_analysis.ai_contract_service.get_health_summary",
                AsyncMock(return_value=self._summary()),
            ),
            patch("app.modules.health_analysis.ai_contract_service.get_health_trend", AsyncMock(return_value=self._trend())),
        ):
            contract = await get_ai_health_input_contract(session, user_id=1001)

        self.assertEqual(contract.contract_version, "f004.ai_input.v1")
        self.assertEqual(contract.user_context.user_id, 1001)
        self.assertEqual(contract.latest_indicators[0].indicator_type, "systolic_bp")
        self.assertEqual(contract.trend_context[0].indicator_type, "systolic_bp")

    async def test_get_ai_health_input_contract_user_missing_raises_404(self):
        from app.modules.health_analysis.ai_contract_service import get_ai_health_input_contract

        with patch("app.modules.health_analysis.ai_contract_service.get_summary_user", AsyncMock(return_value=None)):
            with self.assertRaises(HTTPException) as context:
                await get_ai_health_input_contract(object(), user_id=404)

        self.assertEqual(context.exception.status_code, 404)

    async def test_get_ai_health_input_contract_supports_missing_profile(self):
        from app.modules.health_analysis.ai_contract_service import get_ai_health_input_contract

        with (
            patch("app.modules.health_analysis.ai_contract_service.get_summary_user", AsyncMock(return_value=self._user())),
            patch("app.modules.health_analysis.ai_contract_service.get_summary_profile", AsyncMock(return_value=None)),
            patch(
                "app.modules.health_analysis.ai_contract_service.get_health_summary",
                AsyncMock(return_value=self._summary(with_profile=False)),
            ),
            patch("app.modules.health_analysis.ai_contract_service.get_health_trend", AsyncMock(return_value=self._trend())),
        ):
            contract = await get_ai_health_input_contract(object(), user_id=1001)

        self.assertIsNone(contract.profile_context.gender)
        self.assertFalse(contract.data_completeness.profile_completed)

    async def test_get_ai_health_input_contract_supports_no_indicator(self):
        from app.modules.health_analysis.ai_contract_service import get_ai_health_input_contract

        get_health_trend = AsyncMock(return_value=self._trend())
        with (
            patch("app.modules.health_analysis.ai_contract_service.get_summary_user", AsyncMock(return_value=self._user())),
            patch(
                "app.modules.health_analysis.ai_contract_service.get_summary_profile",
                AsyncMock(return_value=self._profile()),
            ),
            patch(
                "app.modules.health_analysis.ai_contract_service.get_health_summary",
                AsyncMock(return_value=self._summary(with_indicators=False)),
            ),
            patch("app.modules.health_analysis.ai_contract_service.get_health_trend", get_health_trend),
        ):
            contract = await get_ai_health_input_contract(object(), user_id=1001)

        self.assertEqual(contract.latest_indicators, [])
        self.assertEqual(contract.trend_context, [])
        get_health_trend.assert_not_called()

    async def test_get_ai_health_input_contract_supports_no_trend(self):
        from app.modules.health_analysis.ai_contract_service import get_ai_health_input_contract

        with (
            patch("app.modules.health_analysis.ai_contract_service.get_summary_user", AsyncMock(return_value=self._user())),
            patch(
                "app.modules.health_analysis.ai_contract_service.get_summary_profile",
                AsyncMock(return_value=self._profile()),
            ),
            patch(
                "app.modules.health_analysis.ai_contract_service.get_health_summary",
                AsyncMock(return_value=self._summary()),
            ),
            patch("app.modules.health_analysis.ai_contract_service.get_health_trend", AsyncMock(return_value=None)),
        ):
            contract = await get_ai_health_input_contract(object(), user_id=1001)

        self.assertEqual(contract.trend_context, [])

    async def test_get_ai_health_input_contract_calls_builder_and_validator(self):
        from app.modules.health_analysis.ai_contract_service import get_ai_health_input_contract

        build_contract = Mock(return_value=self._contract())
        validate_contract = Mock(return_value=True)
        with (
            patch("app.modules.health_analysis.ai_contract_service.get_summary_user", AsyncMock(return_value=self._user())),
            patch(
                "app.modules.health_analysis.ai_contract_service.get_summary_profile",
                AsyncMock(return_value=self._profile()),
            ),
            patch(
                "app.modules.health_analysis.ai_contract_service.get_health_summary",
                AsyncMock(return_value=self._summary()),
            ),
            patch("app.modules.health_analysis.ai_contract_service.get_health_trend", AsyncMock(return_value=self._trend())),
            patch("app.modules.health_analysis.ai_contract_service.build_ai_health_input_contract", build_contract),
            patch("app.modules.health_analysis.ai_contract_service.validate_ai_health_input_contract", validate_contract),
        ):
            contract = await get_ai_health_input_contract(object(), user_id=1001)

        self.assertIsInstance(contract, AIHealthInputContract)
        build_contract.assert_called_once()
        validate_contract.assert_called_once_with(contract)

    async def test_get_ai_health_input_contract_output_is_safe(self):
        from app.modules.health_analysis.ai_contract_service import get_ai_health_input_contract

        with (
            patch("app.modules.health_analysis.ai_contract_service.get_summary_user", AsyncMock(return_value=self._user())),
            patch(
                "app.modules.health_analysis.ai_contract_service.get_summary_profile",
                AsyncMock(return_value=self._profile()),
            ),
            patch(
                "app.modules.health_analysis.ai_contract_service.get_health_summary",
                AsyncMock(return_value=self._summary()),
            ),
            patch("app.modules.health_analysis.ai_contract_service.get_health_trend", AsyncMock(return_value=self._trend())),
        ):
            contract = await get_ai_health_input_contract(object(), user_id=1001)

        serialized = contract.model_dump_json()
        self.assertNotIn("phone", serialized)
        self.assertNotIn("password_hash", serialized)
        self.assertNotIn("real_name", serialized)
        self.assertNotIn("id_card", serialized)
        self.assertNotIn('"health_score"', serialized)
        self.assertNotIn('"risk_level"', serialized)
        self.assertNotIn('"diagnosis"', serialized)
        self.assertNotIn('"recommendation"', serialized)


if __name__ == "__main__":
    unittest.main()
