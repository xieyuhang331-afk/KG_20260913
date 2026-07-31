import unittest
from datetime import datetime, timezone
from decimal import Decimal

from pydantic import ValidationError


class HealthIndicatorSchemaTests(unittest.TestCase):
    def _valid_item(self) -> dict:
        return {
            "indicator_type": "systolic_bp",
            "value": "120.50",
            "unit": "mmHg",
            "source": "STORE",
            "recorded_at": "2026-07-29T12:00:00+08:00",
            "batch_id": "batch-001",
        }

    def test_create_item_accepts_valid_payload(self):
        from app.modules.user_health.schemas import HealthIndicatorCreateItem

        item = HealthIndicatorCreateItem(**self._valid_item())

        self.assertEqual(item.indicator_type, "systolic_bp")
        self.assertEqual(item.value, Decimal("120.50"))
        self.assertEqual(item.unit, "mmHg")
        self.assertEqual(item.source, "STORE")
        self.assertEqual(item.batch_id, "batch-001")
        self.assertIsInstance(item.recorded_at, datetime)

    def test_create_item_requires_value_unit_and_recorded_at(self):
        from app.modules.user_health.schemas import HealthIndicatorCreateItem

        for field in ("value", "unit", "recorded_at"):
            with self.subTest(field=field):
                payload = self._valid_item()
                payload.pop(field)
                with self.assertRaises(ValidationError):
                    HealthIndicatorCreateItem(**payload)

    def test_create_item_validates_decimal_precision(self):
        from app.modules.user_health.schemas import HealthIndicatorCreateItem

        HealthIndicatorCreateItem(**self._valid_item())

        too_many_decimal_places = self._valid_item()
        too_many_decimal_places["value"] = "120.555"
        with self.assertRaises(ValidationError):
            HealthIndicatorCreateItem(**too_many_decimal_places)

        too_many_digits = self._valid_item()
        too_many_digits["value"] = "123456789.12"
        with self.assertRaises(ValidationError):
            HealthIndicatorCreateItem(**too_many_digits)

    def test_create_item_validates_text_lengths(self):
        from app.modules.user_health.schemas import HealthIndicatorCreateItem

        valid = self._valid_item()
        valid["indicator_type"] = "x" * 30
        valid["batch_id"] = "x" * 36
        HealthIndicatorCreateItem(**valid)

        invalid_type = self._valid_item()
        invalid_type["indicator_type"] = "x" * 31
        with self.assertRaises(ValidationError):
            HealthIndicatorCreateItem(**invalid_type)

        invalid_batch = self._valid_item()
        invalid_batch["batch_id"] = "x" * 37
        with self.assertRaises(ValidationError):
            HealthIndicatorCreateItem(**invalid_batch)

    def test_create_item_validates_source_values(self):
        from app.modules.user_health.schemas import HealthIndicatorCreateItem

        for source in ("APP", "STORE", "DEVICE", "REPORT"):
            payload = self._valid_item()
            payload["source"] = source
            self.assertEqual(HealthIndicatorCreateItem(**payload).source, source)

        invalid = self._valid_item()
        invalid["source"] = "manual"
        with self.assertRaises(ValidationError):
            HealthIndicatorCreateItem(**invalid)

    def test_batch_create_request_requires_indicators(self):
        from app.modules.user_health.schemas import HealthIndicatorBatchCreateRequest

        request = HealthIndicatorBatchCreateRequest(indicators=[self._valid_item()])

        self.assertEqual(len(request.indicators), 1)
        self.assertEqual(request.indicators[0].indicator_type, "systolic_bp")

        with self.assertRaises(ValidationError):
            HealthIndicatorBatchCreateRequest(indicators=[])

    def test_response_contains_health_indicator_fields_only(self):
        from app.modules.user_health.schemas import HealthIndicatorResponse

        response = HealthIndicatorResponse(
            id=1,
            batch_id="batch-001",
            indicator_type="systolic_bp",
            value=Decimal("120.50"),
            unit="mmHg",
            source="STORE",
            recorded_at=datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
            created_at=datetime(2026, 7, 29, 12, 1, tzinfo=timezone.utc),
        )

        data = response.model_dump()

        self.assertEqual(data["id"], 1)
        self.assertEqual(data["batch_id"], "batch-001")
        self.assertEqual(data["indicator_type"], "systolic_bp")
        self.assertEqual(data["value"], Decimal("120.50"))
        self.assertNotIn("password_hash", data)
        self.assertNotIn("id_card", data)
        self.assertNotIn("real_name", data)
