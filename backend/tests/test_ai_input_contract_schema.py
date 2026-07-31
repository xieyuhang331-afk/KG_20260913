import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from pydantic import ValidationError


class AIInputContractSchemaTests(unittest.TestCase):
    def _contract_payload(self) -> dict:
        return {
            "user_context": {
                "user_id": 1001,
                "status": "active",
                "tenant_id": 2001,
            },
            "profile_context": {
                "gender": "F",
                "birth_date": date(1990, 1, 1),
                "height": Decimal("165.5"),
                "weight": Decimal("55.0"),
                "blood_type": "A",
                "has_medical_history": True,
                "has_allergy_history": False,
                "has_family_history": True,
                "has_symptoms": True,
            },
            "latest_indicators": [
                {
                    "indicator_type": "systolic_bp",
                    "display_name": "收缩压",
                    "category": "blood_pressure",
                    "value": Decimal("121.00"),
                    "unit": "mmHg",
                    "source": "APP",
                    "recorded_at": datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc),
                }
            ],
            "trend_context": [
                {
                    "indicator_type": "systolic_bp",
                    "display_name": "收缩压",
                    "category": "blood_pressure",
                    "unit": "mmHg",
                    "window": {
                        "start_at": datetime(2026, 6, 29, 0, 0, tzinfo=timezone.utc),
                        "end_at": datetime(2026, 7, 29, 23, 59, tzinfo=timezone.utc),
                        "days": 30,
                    },
                    "points": [
                        {
                            "value": Decimal("118.00"),
                            "recorded_at": datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc),
                            "source": "APP",
                        }
                    ],
                }
            ],
            "data_completeness": {
                "profile_completed": True,
                "indicator_count": 1,
                "standard_indicator_count": 15,
                "missing_indicator_types": ["diastolic_bp"],
            },
            "source_refs": {
                "source_tables": ["user", "health_profile", "health_indicator"],
                "summary_source": "HealthSummary",
                "trend_source": "HealthTrend",
                "indicator_ids": [3001],
            },
            "safety_policy": {
                "no_diagnosis": True,
                "no_prescription": True,
                "no_treatment_plan": True,
                "no_risk_prediction": True,
                "no_health_score": True,
            },
        }

    def test_ai_input_contract_creates_normal_payload_with_default_version(self):
        from app.modules.health_analysis.schemas import AIHealthInputContract

        contract = AIHealthInputContract(**self._contract_payload())

        self.assertEqual(contract.contract_version, "f004.ai_input.v1")
        self.assertEqual(contract.user_context.user_id, 1001)
        self.assertEqual(contract.latest_indicators[0].indicator_type, "systolic_bp")
        self.assertEqual(contract.trend_context[0].points[0].value, Decimal("118.00"))
        self.assertTrue(contract.safety_policy.no_diagnosis)

    def test_ai_input_contract_serializes_to_json(self):
        from app.modules.health_analysis.schemas import AIHealthInputContract

        contract = AIHealthInputContract(**self._contract_payload())
        serialized = contract.model_dump_json()

        self.assertIn('"contract_version":"f004.ai_input.v1"', serialized)
        self.assertIn('"indicator_type":"systolic_bp"', serialized)
        self.assertIn('"value":"121.00"', serialized)

    def test_ai_input_contract_requires_core_context_fields(self):
        from app.modules.health_analysis.schemas import AIHealthInputContract

        payload = self._contract_payload()
        payload.pop("user_context")

        with self.assertRaises(ValidationError):
            AIHealthInputContract(**payload)

    def test_user_context_rejects_sensitive_fields(self):
        from app.modules.health_analysis.schemas import AIUserContext

        for field in ("phone", "password_hash", "real_name", "id_card"):
            with self.subTest(field=field):
                payload = {"user_id": 1001, "status": "active", "tenant_id": 2001, field: "secret"}
                with self.assertRaises(ValidationError):
                    AIUserContext(**payload)

    def test_profile_context_rejects_raw_history_fields(self):
        from app.modules.health_analysis.schemas import AIProfileContext

        valid_payload = {
            "gender": "F",
            "birth_date": date(1990, 1, 1),
            "height": Decimal("165.5"),
            "weight": Decimal("55.0"),
            "blood_type": "A",
            "has_medical_history": True,
            "has_allergy_history": False,
            "has_family_history": True,
            "has_symptoms": True,
        }

        for field in ("medical_history", "allergy_history"):
            with self.subTest(field=field):
                payload = {**valid_payload, field: ["raw"]}
                with self.assertRaises(ValidationError):
                    AIProfileContext(**payload)

    def test_latest_indicator_rejects_forbidden_medical_judgement_fields(self):
        from app.modules.health_analysis.schemas import AILatestIndicator

        valid_payload = self._contract_payload()["latest_indicators"][0]

        for field in ("risk_level", "health_score", "abnormal_flag"):
            with self.subTest(field=field):
                payload = {**valid_payload, field: "forbidden"}
                with self.assertRaises(ValidationError):
                    AILatestIndicator(**payload)

    def test_ai_input_contract_supports_empty_trends_and_indicators(self):
        from app.modules.health_analysis.schemas import AIHealthInputContract

        payload = self._contract_payload()
        payload["latest_indicators"] = []
        payload["trend_context"] = []

        contract = AIHealthInputContract(**payload)

        self.assertEqual(contract.latest_indicators, [])
        self.assertEqual(contract.trend_context, [])


if __name__ == "__main__":
    unittest.main()
