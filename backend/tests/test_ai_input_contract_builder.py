import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.modules.health_analysis.schemas import (
    DataCompleteness,
    HealthSummary,
    HealthSummaryProfile,
    HealthSummaryUser,
    HealthTrend,
    LatestIndicatorSummary,
    TrendPoint,
)


class AIInputContractBuilderTests(unittest.TestCase):
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

    def _health_profile(self):
        return SimpleNamespace(
            gender="F",
            birth_date=date(1990, 1, 1),
            medical_history=["raw-history"],
            allergy_history=["raw-allergy"],
        )

    def _summary(self, *, with_profile: bool = True, with_indicators: bool = True) -> HealthSummary:
        indicators = []
        if with_indicators:
            indicators.append(
                LatestIndicatorSummary(
                    indicator_type="systolic_bp",
                    display_name="收缩压",
                    category="blood_pressure",
                    value=Decimal("121.00"),
                    unit="mmHg",
                    source="APP",
                    recorded_at=datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc),
                    created_at=datetime(2026, 7, 29, 8, 1, tzinfo=timezone.utc),
                    quality_flags=[],
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
            latest_indicators=indicators,
            indicator_updated_at=datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc) if indicators else None,
            data_completeness=DataCompleteness(
                profile_completed=with_profile,
                indicator_count=len(indicators),
                standard_indicator_count=15,
                missing_indicator_types=[] if indicators else ["systolic_bp"],
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
                ),
                TrendPoint(
                    value=Decimal("121.00"),
                    recorded_at=datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc),
                    source="APP",
                ),
            ],
        )

    def test_build_ai_health_input_contract_creates_normal_contract(self):
        from app.modules.health_analysis.ai_contract_builder import build_ai_health_input_contract

        contract = build_ai_health_input_contract(
            user=self._user(),
            health_profile=self._health_profile(),
            health_summary=self._summary(),
            health_trends=[self._trend()],
        )

        self.assertEqual(contract.contract_version, "f004.ai_input.v1")
        self.assertEqual(contract.user_context.user_id, 1001)
        self.assertEqual(contract.profile_context.gender, "F")
        self.assertEqual(contract.latest_indicators[0].indicator_type, "systolic_bp")
        self.assertEqual(contract.trend_context[0].points[0].value, Decimal("118.00"))
        self.assertEqual(contract.trend_context[0].window.start_at, datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc))
        self.assertEqual(contract.trend_context[0].window.end_at, datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc))

    def test_builder_filters_sensitive_user_and_profile_fields(self):
        from app.modules.health_analysis.ai_contract_builder import build_ai_health_input_contract

        contract = build_ai_health_input_contract(
            user=self._user(),
            health_profile=self._health_profile(),
            health_summary=self._summary(),
            health_trends=[self._trend()],
        )

        serialized = contract.model_dump_json()
        self.assertNotIn("phone", serialized)
        self.assertNotIn("password_hash", serialized)
        self.assertNotIn("real_name", serialized)
        self.assertNotIn("id_card", serialized)
        self.assertNotIn('"medical_history"', serialized)
        self.assertNotIn('"allergy_history"', serialized)

    def test_builder_supports_empty_profile_summary(self):
        from app.modules.health_analysis.ai_contract_builder import build_ai_health_input_contract

        contract = build_ai_health_input_contract(
            user=self._user(),
            health_profile=None,
            health_summary=self._summary(with_profile=False),
            health_trends=[self._trend()],
        )

        self.assertIsNone(contract.profile_context.gender)
        self.assertFalse(contract.data_completeness.profile_completed)

    def test_builder_supports_empty_latest_indicators(self):
        from app.modules.health_analysis.ai_contract_builder import build_ai_health_input_contract

        contract = build_ai_health_input_contract(
            user=self._user(),
            health_profile=self._health_profile(),
            health_summary=self._summary(with_indicators=False),
            health_trends=[self._trend()],
        )

        self.assertEqual(contract.latest_indicators, [])
        self.assertEqual(contract.data_completeness.indicator_count, 0)

    def test_builder_supports_empty_trends(self):
        from app.modules.health_analysis.ai_contract_builder import build_ai_health_input_contract

        contract = build_ai_health_input_contract(
            user=self._user(),
            health_profile=self._health_profile(),
            health_summary=self._summary(),
            health_trends=[],
        )

        self.assertEqual(contract.trend_context, [])

    def test_builder_generates_safety_policy(self):
        from app.modules.health_analysis.ai_contract_builder import build_ai_health_input_contract

        contract = build_ai_health_input_contract(
            user=self._user(),
            health_profile=self._health_profile(),
            health_summary=self._summary(),
            health_trends=[self._trend()],
        )

        self.assertTrue(contract.safety_policy.no_diagnosis)
        self.assertTrue(contract.safety_policy.no_prescription)
        self.assertTrue(contract.safety_policy.no_treatment_plan)
        self.assertTrue(contract.safety_policy.no_risk_prediction)
        self.assertTrue(contract.safety_policy.no_health_score)

    def test_builder_output_does_not_include_forbidden_ai_fields(self):
        from app.modules.health_analysis.ai_contract_builder import build_ai_health_input_contract

        contract = build_ai_health_input_contract(
            user=self._user(),
            health_profile=self._health_profile(),
            health_summary=self._summary(),
            health_trends=[self._trend()],
        )

        serialized = contract.model_dump_json()
        self.assertNotIn('"health_score"', serialized)
        self.assertNotIn('"risk_level"', serialized)
        self.assertNotIn('"diagnosis"', serialized)
        self.assertNotIn('"recommendation"', serialized)


if __name__ == "__main__":
    unittest.main()
