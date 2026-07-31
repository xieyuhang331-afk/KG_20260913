import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace


class HealthSummaryAggregationTests(unittest.TestCase):
    def _user(self):
        return SimpleNamespace(id=1001, status="active", tenant_id=2001)

    def _profile(self):
        return SimpleNamespace(
            gender="F",
            birth_date=date(1990, 1, 1),
            height=Decimal("165.5"),
            weight=Decimal("55.0"),
            blood_type="A",
            medical_history=["hypertension"],
            allergy_history=None,
            family_history={"items": ["diabetes"]},
            symptoms=["fatigue"],
            smoking="never",
            drinking="none",
            sleep_quality="normal",
            bowel_urination="normal",
        )

    def _indicator(self, indicator_type: str, recorded_at: datetime, *, value: str = "1.00", unit: str = "mmHg"):
        return SimpleNamespace(
            indicator_type=indicator_type,
            value=Decimal(value),
            unit=unit,
            source="APP",
            recorded_at=recorded_at,
            created_at=recorded_at,
        )

    def test_build_summary_with_complete_profile_and_multiple_indicators(self):
        from app.modules.health_analysis.aggregation import build_health_summary

        summary = build_health_summary(
            user=self._user(),
            health_profile=self._profile(),
            latest_indicators=[
                self._indicator("systolic_bp", datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc), value="120.00"),
                self._indicator("diastolic_bp", datetime(2026, 7, 29, 8, 1, tzinfo=timezone.utc), value="80.00"),
                self._indicator(
                    "fasting_glucose",
                    datetime(2026, 7, 29, 8, 2, tzinfo=timezone.utc),
                    value="5.60",
                    unit="mmol/L",
                ),
            ],
        )

        self.assertEqual(summary.user.user_id, 1001)
        self.assertTrue(summary.profile.exists)
        self.assertTrue(summary.profile.has_medical_history)
        self.assertFalse(summary.profile.has_allergy_history)
        self.assertEqual(len(summary.latest_indicators), 3)
        self.assertEqual(summary.data_completeness.indicator_count, 3)
        self.assertEqual(summary.data_completeness.standard_indicator_count, 15)

    def test_build_summary_without_profile(self):
        from app.modules.health_analysis.aggregation import build_health_summary

        summary = build_health_summary(user=self._user(), health_profile=None, latest_indicators=[])

        self.assertFalse(summary.profile.exists)
        self.assertIsNone(summary.profile.gender)
        self.assertFalse(summary.data_completeness.profile_completed)

    def test_build_summary_without_indicators(self):
        from app.modules.health_analysis.aggregation import build_health_summary

        summary = build_health_summary(user=self._user(), health_profile=self._profile(), latest_indicators=[])

        self.assertEqual(summary.latest_indicators, [])
        self.assertIsNone(summary.indicator_updated_at)
        self.assertEqual(summary.data_completeness.indicator_count, 0)
        self.assertIn("systolic_bp", summary.data_completeness.missing_indicator_types)

    def test_unknown_indicator_is_not_in_core_summary(self):
        from app.modules.health_analysis.aggregation import build_health_summary

        summary = build_health_summary(
            user=self._user(),
            health_profile=self._profile(),
            latest_indicators=[
                self._indicator("unknown_metric", datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)),
                self._indicator("systolic_bp", datetime(2026, 7, 29, 8, 1, tzinfo=timezone.utc), value="120.00"),
            ],
        )

        indicator_types = [item.indicator_type for item in summary.latest_indicators]
        self.assertEqual(indicator_types, ["systolic_bp"])
        self.assertNotIn("unknown_metric", indicator_types)

    def test_indicator_updated_at_uses_max_recorded_at(self):
        from app.modules.health_analysis.aggregation import build_health_summary

        latest_time = datetime(2026, 7, 29, 9, 0, tzinfo=timezone.utc)
        summary = build_health_summary(
            user=self._user(),
            health_profile=self._profile(),
            latest_indicators=[
                self._indicator("systolic_bp", datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)),
                self._indicator("heart_rate", latest_time, value="72.00", unit="bpm"),
            ],
        )

        self.assertEqual(summary.indicator_updated_at, latest_time)

    def test_missing_indicator_types_are_standard_set_minus_present_standard_indicators(self):
        from app.modules.health_analysis.aggregation import build_health_summary

        summary = build_health_summary(
            user=self._user(),
            health_profile=self._profile(),
            latest_indicators=[
                self._indicator("systolic_bp", datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)),
                self._indicator("diastolic_bp", datetime(2026, 7, 29, 8, 1, tzinfo=timezone.utc)),
            ],
        )

        self.assertNotIn("systolic_bp", summary.data_completeness.missing_indicator_types)
        self.assertNotIn("diastolic_bp", summary.data_completeness.missing_indicator_types)
        self.assertIn("fasting_glucose", summary.data_completeness.missing_indicator_types)

    def test_blood_pressure_indicators_remain_independent_without_synthetic_blood_pressure(self):
        from app.modules.health_analysis.aggregation import build_health_summary

        summary = build_health_summary(
            user=self._user(),
            health_profile=self._profile(),
            latest_indicators=[
                self._indicator("systolic_bp", datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc), value="120.00"),
                self._indicator("diastolic_bp", datetime(2026, 7, 29, 8, 1, tzinfo=timezone.utc), value="80.00"),
            ],
        )

        indicator_types = [item.indicator_type for item in summary.latest_indicators]
        self.assertIn("systolic_bp", indicator_types)
        self.assertIn("diastolic_bp", indicator_types)
        self.assertNotIn("blood_pressure", indicator_types)

    def test_summary_does_not_generate_medical_judgement_fields(self):
        from app.modules.health_analysis.aggregation import build_health_summary

        summary = build_health_summary(
            user=self._user(),
            health_profile=self._profile(),
            latest_indicators=[self._indicator("systolic_bp", datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc))],
        )

        data = summary.model_dump()
        serialized_keys = set(data)
        serialized_keys.update(data["data_completeness"])

        self.assertNotIn("health_score", serialized_keys)
        self.assertNotIn("risk_level", serialized_keys)
        self.assertNotIn("diagnosis_hint", serialized_keys)
