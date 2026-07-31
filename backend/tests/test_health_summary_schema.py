import unittest
from datetime import date, datetime, timezone
from decimal import Decimal


class HealthSummarySchemaTests(unittest.TestCase):
    def _indicator_payload(self) -> dict:
        return {
            "indicator_type": "systolic_bp",
            "display_name": "收缩压",
            "category": "blood_pressure",
            "value": "120.00",
            "unit": "mmHg",
            "source": "APP",
            "recorded_at": datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc),
            "created_at": datetime(2026, 7, 29, 8, 1, tzinfo=timezone.utc),
            "quality_flags": [],
        }

    def test_health_summary_serializes_normal_payload(self):
        from app.modules.health_analysis.schemas import (
            DataCompleteness,
            HealthSummary,
            HealthSummaryProfile,
            HealthSummaryUser,
            LatestIndicatorSummary,
        )

        summary = HealthSummary(
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
                smoking="never",
                drinking="none",
                sleep_quality="normal",
                bowel_urination="normal",
            ),
            latest_indicators=[LatestIndicatorSummary(**self._indicator_payload())],
            indicator_updated_at=datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc),
            data_completeness=DataCompleteness(
                profile_completed=True,
                indicator_count=1,
                standard_indicator_count=15,
                missing_indicator_types=["diastolic_bp"],
            ),
        )

        data = summary.model_dump()

        self.assertEqual(data["user"]["user_id"], 1001)
        self.assertEqual(data["profile"]["gender"], "F")
        self.assertEqual(data["latest_indicators"][0]["indicator_type"], "systolic_bp")
        self.assertEqual(data["latest_indicators"][0]["value"], Decimal("120.00"))
        self.assertEqual(data["data_completeness"]["missing_indicator_types"], ["diastolic_bp"])

    def test_profile_can_represent_missing_profile(self):
        from app.modules.health_analysis.schemas import HealthSummaryProfile

        profile = HealthSummaryProfile(exists=False)
        data = profile.model_dump()

        self.assertFalse(data["exists"])
        self.assertIsNone(data["gender"])
        self.assertIsNone(data["birth_date"])
        self.assertIsNone(data["height"])
        self.assertIsNone(data["weight"])

    def test_summary_allows_empty_indicators_and_null_update_time(self):
        from app.modules.health_analysis.schemas import (
            DataCompleteness,
            HealthSummary,
            HealthSummaryProfile,
            HealthSummaryUser,
        )

        summary = HealthSummary(
            user=HealthSummaryUser(user_id=1001, status="active", tenant_id=None),
            profile=HealthSummaryProfile(exists=False),
            latest_indicators=[],
            indicator_updated_at=None,
            data_completeness=DataCompleteness(
                profile_completed=False,
                indicator_count=0,
                standard_indicator_count=15,
                missing_indicator_types=["systolic_bp", "diastolic_bp"],
            ),
        )

        data = summary.model_dump()

        self.assertEqual(data["latest_indicators"], [])
        self.assertIsNone(data["indicator_updated_at"])
        self.assertFalse(data["data_completeness"]["profile_completed"])

    def test_summary_does_not_include_sensitive_user_fields_or_medical_judgement(self):
        from app.modules.health_analysis.schemas import (
            DataCompleteness,
            HealthSummary,
            HealthSummaryProfile,
            HealthSummaryUser,
        )

        summary = HealthSummary(
            user=HealthSummaryUser(user_id=1001, status="active", tenant_id=2001),
            profile=HealthSummaryProfile(exists=False),
            latest_indicators=[],
            indicator_updated_at=None,
            data_completeness=DataCompleteness(
                profile_completed=False,
                indicator_count=0,
                standard_indicator_count=15,
                missing_indicator_types=[],
            ),
        )

        data = summary.model_dump()
        serialized_keys = set(data)
        serialized_keys.update(data["user"])
        serialized_keys.update(data["profile"])
        serialized_keys.update(data["data_completeness"])

        self.assertNotIn("password_hash", serialized_keys)
        self.assertNotIn("id_card", serialized_keys)
        self.assertNotIn("real_name", serialized_keys)
        self.assertNotIn("risk_level", serialized_keys)
        self.assertNotIn("health_score", serialized_keys)
