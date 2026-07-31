import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace


class HealthTrendAggregationTests(unittest.TestCase):
    def _indicator(
        self,
        indicator_type: str,
        recorded_at: datetime,
        *,
        value: str = "120.00",
        unit: str = "mmHg",
        source: str = "APP",
    ):
        return SimpleNamespace(
            indicator_type=indicator_type,
            value=Decimal(value),
            unit=unit,
            source=source,
            recorded_at=recorded_at,
        )

    def test_standard_indicator_generates_health_trend(self):
        from app.modules.health_analysis.aggregation import build_health_trend

        trend = build_health_trend(
            indicator_type="systolic_bp",
            indicators=[
                self._indicator("systolic_bp", datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc), value="120.00"),
            ],
        )

        self.assertEqual(trend.indicator_type, "systolic_bp")
        self.assertEqual(trend.display_name, "收缩压")
        self.assertEqual(trend.category, "blood_pressure")
        self.assertEqual(trend.unit, "mmHg")
        self.assertEqual(len(trend.points), 1)
        self.assertEqual(trend.points[0].value, Decimal("120.00"))
        self.assertEqual(trend.points[0].source, "APP")

    def test_points_are_sorted_by_recorded_at_ascending(self):
        from app.modules.health_analysis.aggregation import build_health_trend

        earlier = datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc)
        later = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)

        trend = build_health_trend(
            indicator_type="systolic_bp",
            indicators=[
                self._indicator("systolic_bp", later, value="121.00"),
                self._indicator("systolic_bp", earlier, value="118.00"),
            ],
        )

        self.assertEqual([point.recorded_at for point in trend.points], [earlier, later])
        self.assertEqual([point.value for point in trend.points], [Decimal("118.00"), Decimal("121.00")])

    def test_multiple_time_points_are_preserved(self):
        from app.modules.health_analysis.aggregation import build_health_trend

        trend = build_health_trend(
            indicator_type="heart_rate",
            indicators=[
                self._indicator(
                    "heart_rate",
                    datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc),
                    value="70.00",
                    unit="bpm",
                ),
                self._indicator(
                    "heart_rate",
                    datetime(2026, 7, 29, 9, 0, tzinfo=timezone.utc),
                    value="72.00",
                    unit="bpm",
                ),
            ],
        )

        self.assertEqual(len(trend.points), 2)
        self.assertEqual([point.value for point in trend.points], [Decimal("70.00"), Decimal("72.00")])

    def test_unknown_indicator_raises_value_error(self):
        from app.modules.health_analysis.aggregation import build_health_trend

        with self.assertRaises(ValueError) as context:
            build_health_trend(indicator_type="unknown_metric", indicators=[])

        self.assertEqual(str(context.exception), "Unknown indicator_type")

    def test_empty_points_are_allowed_for_standard_indicator(self):
        from app.modules.health_analysis.aggregation import build_health_trend

        trend = build_health_trend(indicator_type="systolic_bp", indicators=[])

        self.assertEqual(trend.points, [])

    def test_trend_does_not_generate_medical_or_ai_fields(self):
        from app.modules.health_analysis.aggregation import build_health_trend

        trend = build_health_trend(
            indicator_type="systolic_bp",
            indicators=[
                self._indicator("systolic_bp", datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)),
            ],
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
