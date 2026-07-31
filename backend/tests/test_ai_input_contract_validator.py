import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.modules.health_analysis.ai_contract_builder import build_ai_health_input_contract
from app.modules.health_analysis.schemas import (
    DataCompleteness,
    HealthSummary,
    HealthSummaryProfile,
    HealthSummaryUser,
    HealthTrend,
    LatestIndicatorSummary,
    TrendPoint,
)


class AIInputContractValidatorTests(unittest.TestCase):
    def _user(self):
        return SimpleNamespace(id=1001, status="active", tenant_id=2001)

    def _summary(self, *, with_indicators: bool = True) -> HealthSummary:
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
            ),
            latest_indicators=latest_indicators,
            indicator_updated_at=datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc) if latest_indicators else None,
            data_completeness=DataCompleteness(
                profile_completed=True,
                indicator_count=len(latest_indicators),
                standard_indicator_count=15,
                missing_indicator_types=[],
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
                    value=Decimal("118.00"),
                    recorded_at=datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc),
                    source="APP",
                )
            ],
        )

    def _contract(self, *, with_indicators: bool = True, with_trends: bool = True):
        return build_ai_health_input_contract(
            user=self._user(),
            health_profile=SimpleNamespace(),
            health_summary=self._summary(with_indicators=with_indicators),
            health_trends=[self._trend()] if with_trends else [],
        )

    def test_valid_contract_passes(self):
        from app.modules.health_analysis.ai_contract_validator import validate_ai_health_input_contract

        self.assertTrue(validate_ai_health_input_contract(self._contract()))

    def test_wrong_version_fails(self):
        from app.modules.health_analysis.ai_contract_validator import validate_ai_health_input_contract

        payload = self._contract().model_dump(mode="json")
        payload["contract_version"] = "f004.ai_input.v2"

        self.assertFalse(validate_ai_health_input_contract(payload))

    def test_missing_required_field_fails(self):
        from app.modules.health_analysis.ai_contract_validator import validate_ai_health_input_contract

        for field in (
            "user_context",
            "profile_context",
            "latest_indicators",
            "trend_context",
            "data_completeness",
            "safety_policy",
        ):
            with self.subTest(field=field):
                payload = self._contract().model_dump(mode="json")
                payload.pop(field)
                self.assertFalse(validate_ai_health_input_contract(payload))

    def test_safety_policy_false_value_fails(self):
        from app.modules.health_analysis.ai_contract_validator import validate_ai_health_input_contract

        for field in (
            "no_diagnosis",
            "no_prescription",
            "no_treatment_plan",
            "no_risk_prediction",
            "no_health_score",
        ):
            with self.subTest(field=field):
                payload = self._contract().model_dump(mode="json")
                payload["safety_policy"][field] = False
                self.assertFalse(validate_ai_health_input_contract(payload))

    def test_forbidden_fields_fail_validation(self):
        from app.modules.health_analysis.ai_contract_validator import validate_ai_health_input_contract

        for field in ("health_score", "risk_level", "diagnosis", "recommendation", "prescription"):
            with self.subTest(field=field):
                payload = self._contract().model_dump(mode="json")
                payload[field] = "forbidden"
                self.assertFalse(validate_ai_health_input_contract(payload))

    def test_missing_latest_indicator_required_field_fails(self):
        from app.modules.health_analysis.ai_contract_validator import validate_ai_health_input_contract

        for field in ("indicator_type", "value", "unit"):
            with self.subTest(field=field):
                payload = self._contract().model_dump(mode="json")
                payload["latest_indicators"][0].pop(field)
                self.assertFalse(validate_ai_health_input_contract(payload))

    def test_missing_trend_required_field_fails(self):
        from app.modules.health_analysis.ai_contract_validator import validate_ai_health_input_contract

        for field in ("indicator_type", "points"):
            with self.subTest(field=field):
                payload = self._contract().model_dump(mode="json")
                payload["trend_context"][0].pop(field)
                self.assertFalse(validate_ai_health_input_contract(payload))

    def test_empty_health_data_passes(self):
        from app.modules.health_analysis.ai_contract_validator import validate_ai_health_input_contract

        contract = self._contract(with_indicators=False, with_trends=False)

        self.assertTrue(validate_ai_health_input_contract(contract))


if __name__ == "__main__":
    unittest.main()
