import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from pydantic import ValidationError


class HealthProfileSchemaTests(unittest.TestCase):
    def _valid_payload(self) -> dict:
        return {
            "gender": "F",
            "birth_date": "1990-01-01",
            "height": "165.5",
            "weight": "55.0",
            "blood_type": "A",
            "medical_history": ["hypertension"],
            "allergy_history": None,
            "family_history": {"items": ["diabetes"]},
            "smoking": "never",
            "drinking": "none",
            "symptoms": {"items": ["fatigue"]},
            "sleep_quality": "normal",
            "bowel_urination": "normal",
        }

    def test_create_request_accepts_valid_payload(self):
        from app.modules.user_health.schemas import HealthProfileCreateRequest

        payload = HealthProfileCreateRequest(**self._valid_payload())

        self.assertEqual(payload.gender, "F")
        self.assertEqual(payload.birth_date, date(1990, 1, 1))
        self.assertEqual(payload.height, Decimal("165.5"))
        self.assertEqual(payload.weight, Decimal("55.0"))
        self.assertEqual(payload.medical_history, ["hypertension"])
        self.assertIsNone(payload.allergy_history)
        self.assertEqual(payload.family_history, {"items": ["diabetes"]})
        self.assertEqual(payload.symptoms, {"items": ["fatigue"]})

    def test_create_request_requires_gender(self):
        from app.modules.user_health.schemas import HealthProfileCreateRequest

        payload = self._valid_payload()
        payload.pop("gender")

        with self.assertRaises(ValidationError):
            HealthProfileCreateRequest(**payload)

    def test_create_request_requires_birth_date(self):
        from app.modules.user_health.schemas import HealthProfileCreateRequest

        payload = self._valid_payload()
        payload.pop("birth_date")

        with self.assertRaises(ValidationError):
            HealthProfileCreateRequest(**payload)

    def test_create_request_validates_height_and_weight_decimal_shape(self):
        from app.modules.user_health.schemas import HealthProfileCreateRequest

        HealthProfileCreateRequest(**self._valid_payload())

        invalid_height = self._valid_payload()
        invalid_height["height"] = "165.55"
        with self.assertRaises(ValidationError):
            HealthProfileCreateRequest(**invalid_height)

        invalid_weight = self._valid_payload()
        invalid_weight["weight"] = "12345.6"
        with self.assertRaises(ValidationError):
            HealthProfileCreateRequest(**invalid_weight)

    def test_jsonb_fields_accept_list_dict_and_null(self):
        from app.modules.user_health.schemas import HealthProfileCreateRequest

        payload = self._valid_payload()
        payload["medical_history"] = ["a", "b"]
        payload["allergy_history"] = {"drug": "penicillin"}
        payload["family_history"] = None
        payload["symptoms"] = [{"name": "cough"}]

        profile = HealthProfileCreateRequest(**payload)

        self.assertEqual(profile.medical_history, ["a", "b"])
        self.assertEqual(profile.allergy_history, {"drug": "penicillin"})
        self.assertIsNone(profile.family_history)
        self.assertEqual(profile.symptoms, [{"name": "cough"}])

    def test_create_request_validates_text_length_limits(self):
        from app.modules.user_health.schemas import HealthProfileCreateRequest

        valid = self._valid_payload()
        valid["smoking"] = "x" * 10
        valid["drinking"] = "x" * 10
        valid["sleep_quality"] = "x" * 50
        valid["bowel_urination"] = "x" * 100
        HealthProfileCreateRequest(**valid)

        for field, length in (
            ("smoking", 11),
            ("drinking", 11),
            ("sleep_quality", 51),
            ("bowel_urination", 101),
        ):
            with self.subTest(field=field):
                payload = self._valid_payload()
                payload[field] = "x" * length
                with self.assertRaises(ValidationError):
                    HealthProfileCreateRequest(**payload)

    def test_response_contains_health_profile_fields_only(self):
        from app.modules.user_health.schemas import HealthProfileResponse

        response = HealthProfileResponse(
            id=1,
            user_id=101,
            gender="F",
            birth_date=date(1990, 1, 1),
            height=Decimal("165.5"),
            weight=Decimal("55.0"),
            blood_type="A",
            medical_history=["hypertension"],
            allergy_history=None,
            family_history={"items": ["diabetes"]},
            smoking="never",
            drinking="none",
            symptoms={"items": ["fatigue"]},
            sleep_quality="normal",
            bowel_urination="normal",
            created_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
            updated_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
        )

        data = response.model_dump()

        self.assertEqual(data["id"], 1)
        self.assertEqual(data["user_id"], 101)
        self.assertEqual(data["gender"], "F")
        self.assertNotIn("password_hash", data)
        self.assertNotIn("id_card", data)
