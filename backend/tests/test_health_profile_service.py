import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError


class HealthProfileServiceTests(unittest.IsolatedAsyncioTestCase):
    def _payload(self):
        from app.modules.user_health.schemas import HealthProfileCreateRequest

        return HealthProfileCreateRequest(
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
        )

    def _profile(self):
        return SimpleNamespace(
            id=2001,
            user_id=1001,
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

    class FakeSession:
        def __init__(self):
            self.commit_called = False
            self.rollback_called = False

        async def commit(self):
            self.commit_called = True

        async def rollback(self):
            self.rollback_called = True

    async def test_create_health_profile_successfully(self):
        from app.modules.user_health.service import create_health_profile

        session = self.FakeSession()
        created_profile = self._profile()

        async def create_profile(session_arg, *, profile_data):
            self.assertIs(session_arg, session)
            self.assertEqual(profile_data["user_id"], 1001)
            self.assertEqual(profile_data["gender"], "F")
            self.assertEqual(profile_data["birth_date"], date(1990, 1, 1))
            return created_profile

        with (
            patch("app.modules.user_health.service.get_user_by_id", new=AsyncMock(return_value=SimpleNamespace(id=1001))),
            patch("app.modules.user_health.service.get_health_profile_by_user_id", new=AsyncMock(return_value=None)),
            patch("app.modules.user_health.service.create_health_profile_record", new=AsyncMock(side_effect=create_profile)),
        ):
            response = await create_health_profile(session, user_id=1001, payload=self._payload())

        self.assertEqual(response.id, 2001)
        self.assertEqual(response.user_id, 1001)
        self.assertEqual(response.gender, "F")
        self.assertTrue(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_create_health_profile_returns_404_when_user_missing(self):
        from app.modules.user_health.service import create_health_profile

        session = self.FakeSession()
        create_mock = AsyncMock()

        with (
            patch("app.modules.user_health.service.get_user_by_id", new=AsyncMock(return_value=None)),
            patch("app.modules.user_health.service.get_health_profile_by_user_id", new=AsyncMock()),
            patch("app.modules.user_health.service.create_health_profile_record", new=create_mock),
        ):
            with self.assertRaises(HTTPException) as context:
                await create_health_profile(session, user_id=1001, payload=self._payload())

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "User not found")
        create_mock.assert_not_awaited()
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_create_health_profile_returns_409_when_profile_exists(self):
        from app.modules.user_health.service import create_health_profile

        session = self.FakeSession()
        create_mock = AsyncMock()

        with (
            patch("app.modules.user_health.service.get_user_by_id", new=AsyncMock(return_value=SimpleNamespace(id=1001))),
            patch("app.modules.user_health.service.get_health_profile_by_user_id", new=AsyncMock(return_value=self._profile())),
            patch("app.modules.user_health.service.create_health_profile_record", new=create_mock),
        ):
            with self.assertRaises(HTTPException) as context:
                await create_health_profile(session, user_id=1001, payload=self._payload())

        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(context.exception.detail, "Health profile already exists")
        create_mock.assert_not_awaited()
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_create_health_profile_preserves_jsonb_fields(self):
        from app.modules.user_health.service import create_health_profile

        session = self.FakeSession()
        created_profile = self._profile()
        created_profile.medical_history = ["a", "b"]
        created_profile.family_history = {"items": ["c"]}
        created_profile.symptoms = [{"name": "fatigue"}]

        with (
            patch("app.modules.user_health.service.get_user_by_id", new=AsyncMock(return_value=SimpleNamespace(id=1001))),
            patch("app.modules.user_health.service.get_health_profile_by_user_id", new=AsyncMock(return_value=None)),
            patch("app.modules.user_health.service.create_health_profile_record", new=AsyncMock(return_value=created_profile)),
        ):
            response = await create_health_profile(session, user_id=1001, payload=self._payload())

        self.assertEqual(response.medical_history, ["a", "b"])
        self.assertEqual(response.family_history, {"items": ["c"]})
        self.assertEqual(response.symptoms, [{"name": "fatigue"}])

    async def test_create_health_profile_translates_integrity_error_to_409_and_rolls_back(self):
        from app.modules.user_health.service import create_health_profile

        session = self.FakeSession()
        integrity_error = IntegrityError(
            statement="INSERT INTO health_profile",
            params={"user_id": 1001},
            orig=Exception("duplicate key value violates unique constraint"),
        )

        with (
            patch("app.modules.user_health.service.get_user_by_id", new=AsyncMock(return_value=SimpleNamespace(id=1001))),
            patch("app.modules.user_health.service.get_health_profile_by_user_id", new=AsyncMock(return_value=None)),
            patch("app.modules.user_health.service.create_health_profile_record", new=AsyncMock(side_effect=integrity_error)),
        ):
            with self.assertRaises(HTTPException) as context:
                await create_health_profile(session, user_id=1001, payload=self._payload())

        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(context.exception.detail, "Health profile already exists")
        self.assertFalse(session.commit_called)
        self.assertTrue(session.rollback_called)

    async def test_create_health_profile_rolls_back_repository_exception(self):
        from app.modules.user_health.service import create_health_profile

        session = self.FakeSession()

        with (
            patch("app.modules.user_health.service.get_user_by_id", new=AsyncMock(return_value=SimpleNamespace(id=1001))),
            patch("app.modules.user_health.service.get_health_profile_by_user_id", new=AsyncMock(return_value=None)),
            patch("app.modules.user_health.service.create_health_profile_record", new=AsyncMock(side_effect=RuntimeError("boom"))),
        ):
            with self.assertRaises(RuntimeError):
                await create_health_profile(session, user_id=1001, payload=self._payload())

        self.assertFalse(session.commit_called)
        self.assertTrue(session.rollback_called)

    async def test_get_health_profile_successfully(self):
        from app.modules.user_health.service import get_health_profile

        session = self.FakeSession()

        with (
            patch("app.modules.user_health.service.get_user_by_id", new=AsyncMock(return_value=SimpleNamespace(id=1001))),
            patch("app.modules.user_health.service.get_health_profile_by_user_id", new=AsyncMock(return_value=self._profile())),
        ):
            response = await get_health_profile(session, user_id=1001)

        self.assertEqual(response.id, 2001)
        self.assertEqual(response.user_id, 1001)
        self.assertEqual(response.gender, "F")
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_get_health_profile_returns_404_when_user_missing(self):
        from app.modules.user_health.service import get_health_profile

        session = self.FakeSession()
        profile_mock = AsyncMock()

        with (
            patch("app.modules.user_health.service.get_user_by_id", new=AsyncMock(return_value=None)),
            patch("app.modules.user_health.service.get_health_profile_by_user_id", new=profile_mock),
        ):
            with self.assertRaises(HTTPException) as context:
                await get_health_profile(session, user_id=1001)

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "User not found")
        profile_mock.assert_not_awaited()
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_get_health_profile_returns_404_when_profile_missing(self):
        from app.modules.user_health.service import get_health_profile

        session = self.FakeSession()

        with (
            patch("app.modules.user_health.service.get_user_by_id", new=AsyncMock(return_value=SimpleNamespace(id=1001))),
            patch("app.modules.user_health.service.get_health_profile_by_user_id", new=AsyncMock(return_value=None)),
        ):
            with self.assertRaises(HTTPException) as context:
                await get_health_profile(session, user_id=1001)

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "Health profile not found")
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_get_health_profile_preserves_jsonb_fields(self):
        from app.modules.user_health.service import get_health_profile

        session = self.FakeSession()
        profile = self._profile()
        profile.medical_history = ["a", "b"]
        profile.allergy_history = {"drug": "penicillin"}
        profile.family_history = None
        profile.symptoms = [{"name": "fatigue"}]

        with (
            patch("app.modules.user_health.service.get_user_by_id", new=AsyncMock(return_value=SimpleNamespace(id=1001))),
            patch("app.modules.user_health.service.get_health_profile_by_user_id", new=AsyncMock(return_value=profile)),
        ):
            response = await get_health_profile(session, user_id=1001)

        self.assertEqual(response.medical_history, ["a", "b"])
        self.assertEqual(response.allergy_history, {"drug": "penicillin"})
        self.assertIsNone(response.family_history)
        self.assertEqual(response.symptoms, [{"name": "fatigue"}])
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)
