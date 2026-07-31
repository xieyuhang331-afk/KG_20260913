import unittest
from datetime import datetime, timezone
from decimal import Decimal

from pydantic import ValidationError


class HealthTrendSchemaTests(unittest.TestCase):
    def _point_payload(self) -> dict:
        return {
            "value": "120.50",
            "recorded_at": datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc),
            "source": "APP",
        }

    def test_health_trend_serializes_normal_payload(self):
        from app.modules.health_analysis.schemas import HealthTrend, TrendPoint

        trend = HealthTrend(
            indicator_type="systolic_bp",
            display_name="收缩压",
            category="blood_pressure",
            unit="mmHg",
            points=[TrendPoint(**self._point_payload())],
        )

        data = trend.model_dump()

        self.assertEqual(data["indicator_type"], "systolic_bp")
        self.assertEqual(data["display_name"], "收缩压")
        self.assertEqual(data["category"], "blood_pressure")
        self.assertEqual(data["unit"], "mmHg")
        self.assertEqual(data["points"][0]["value"], Decimal("120.50"))
        self.assertEqual(data["points"][0]["source"], "APP")

    def test_health_trend_allows_empty_points(self):
        from app.modules.health_analysis.schemas import HealthTrend

        trend = HealthTrend(
            indicator_type="systolic_bp",
            display_name="收缩压",
            category="blood_pressure",
            unit="mmHg",
            points=[],
        )

        self.assertEqual(trend.points, [])

    def test_trend_point_validates_source_values(self):
        from app.modules.health_analysis.schemas import TrendPoint

        for source in ("APP", "STORE", "DEVICE", "REPORT"):
            with self.subTest(source=source):
                payload = self._point_payload()
                payload["source"] = source
                self.assertEqual(TrendPoint(**payload).source, source)

        invalid = self._point_payload()
        invalid["source"] = "MANUAL"
        with self.assertRaises(ValidationError):
            TrendPoint(**invalid)

    def test_health_trend_does_not_include_medical_judgement_fields(self):
        from app.modules.health_analysis.schemas import HealthTrend

        trend = HealthTrend(
            indicator_type="systolic_bp",
            display_name="收缩压",
            category="blood_pressure",
            unit="mmHg",
            points=[],
        )

        data = trend.model_dump()
        serialized_keys = set(data)
        for point in data["points"]:
            serialized_keys.update(point)

        self.assertNotIn("health_score", serialized_keys)
        self.assertNotIn("risk_level", serialized_keys)
        self.assertNotIn("diagnosis_hint", serialized_keys)
        self.assertNotIn("normal_range", serialized_keys)
        self.assertNotIn("abnormal_flag", serialized_keys)

    def test_trend_point_preserves_decimal_value(self):
        from app.modules.health_analysis.schemas import TrendPoint

        point = TrendPoint(**self._point_payload())

        self.assertEqual(point.value, Decimal("120.50"))
