import unittest

from pydantic import ValidationError


class UserIdentitySchemaTests(unittest.TestCase):
    def test_identity_request_accepts_valid_payload(self):
        from app.modules.auth.schemas import UserIdentityRequest

        payload = UserIdentityRequest(real_name="Zhang San", id_card="110101199001011234")

        self.assertEqual(payload.real_name, "Zhang San")
        self.assertEqual(payload.id_card, "110101199001011234")

    def test_identity_request_requires_real_name_and_id_card(self):
        from app.modules.auth.schemas import UserIdentityRequest

        with self.assertRaises(ValidationError):
            UserIdentityRequest(id_card="110101199001011234")
        with self.assertRaises(ValidationError):
            UserIdentityRequest(real_name="Zhang San")

    def test_identity_request_validates_real_name_length(self):
        from app.modules.auth.schemas import UserIdentityRequest

        with self.assertRaises(ValidationError):
            UserIdentityRequest(real_name="", id_card="110101199001011234")
        with self.assertRaises(ValidationError):
            UserIdentityRequest(real_name="A" * 51, id_card="110101199001011234")

    def test_identity_request_validates_id_card_format(self):
        from app.modules.auth.schemas import UserIdentityRequest

        invalid_values = ["", "11010119900101123", "1101011990010112345", "11010119900101123A"]
        for value in invalid_values:
            with self.subTest(id_card=value):
                with self.assertRaises(ValidationError):
                    UserIdentityRequest(real_name="Zhang San", id_card=value)

    def test_mask_id_card_masks_middle_digits(self):
        from app.modules.auth.service import mask_id_card

        self.assertEqual(mask_id_card("110101199001011234"), "110101********1234")

    def test_mask_id_card_rejects_invalid_values(self):
        from app.modules.auth.service import mask_id_card

        for value in ("", "11010119900101123", "1101011990010112345", "11010119900101123A"):
            with self.subTest(id_card=value):
                with self.assertRaises(ValueError):
                    mask_id_card(value)

    def test_identity_response_contains_only_safe_fields(self):
        from app.modules.auth.schemas import UserIdentityResponse

        response = UserIdentityResponse(
            user_id=1001,
            real_name="Zhang San",
            id_card_masked="110101********1234",
            verify_status="submitted",
        )
        data = response.model_dump()

        self.assertEqual(
            data,
            {
                "user_id": 1001,
                "real_name": "Zhang San",
                "id_card_masked": "110101********1234",
                "verify_status": "submitted",
            },
        )
        self.assertNotIn("id_card", data)
        self.assertNotIn("password_hash", data)

