import unittest


class HealthProfileRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_health_profile_by_user_id_returns_profile(self):
        from app.modules.user_health.models import HealthProfile
        from app.modules.user_health.repository import get_health_profile_by_user_id

        profile = HealthProfile()
        profile.id = 2001
        profile.user_id = 1001

        class Result:
            def scalar_one_or_none(self):
                return profile

        class FakeSession:
            def __init__(self):
                self.execute_called = False

            async def execute(self, statement):
                self.execute_called = True
                return Result()

        session = FakeSession()
        result = await get_health_profile_by_user_id(session, 1001)

        self.assertIs(result, profile)
        self.assertTrue(session.execute_called)

    async def test_get_health_profile_by_user_id_returns_none_when_missing(self):
        from app.modules.user_health.repository import get_health_profile_by_user_id

        class Result:
            def scalar_one_or_none(self):
                return None

        class FakeSession:
            async def execute(self, statement):
                return Result()

        result = await get_health_profile_by_user_id(FakeSession(), 1001)

        self.assertIsNone(result)

    async def test_create_health_profile_record_uses_bounded_function(self):
        from app.modules.user_health.repository import create_health_profile_record

        profile_row = {
            "id": 2001,
            "user_id": 1001,
            "gender": "F",
            "birth_date": "1990-01-01",
            "height": "165.5",
            "weight": "55.0",
            "medical_history": ["hypertension"],
            "allergy_history": None,
            "family_history": {"items": ["diabetes"]},
            "symptoms": {"items": ["fatigue"]},
        }

        class Result:
            def mappings(self):
                return self

            def one(self):
                return profile_row

        class FakeSession:
            def __init__(self):
                self.statements = []
                self.commit_called = False
                self.rollback_called = False

            async def execute(self, statement):
                self.statements.append(statement)
                return Result()

            async def commit(self):
                self.commit_called = True

            async def rollback(self):
                self.rollback_called = True

        session = FakeSession()
        profile = await create_health_profile_record(
            session,
            profile_data={
                "user_id": 1001,
                "gender": "F",
                "birth_date": "1990-01-01",
                "height": "165.5",
                "weight": "55.0",
                "medical_history": ["hypertension"],
                "allergy_history": None,
                "family_history": {"items": ["diabetes"]},
                "symptoms": {"items": ["fatigue"]},
            },
        )

        self.assertEqual(profile.user_id, 1001)
        self.assertEqual(profile.gender, "F")
        self.assertEqual(profile.birth_date, "1990-01-01")
        self.assertEqual(profile.height, "165.5")
        self.assertEqual(profile.weight, "55.0")
        self.assertEqual(profile.medical_history, ["hypertension"])
        self.assertIsNone(profile.allergy_history)
        self.assertEqual(profile.family_history, {"items": ["diabetes"]})
        self.assertEqual(profile.symptoms, {"items": ["fatigue"]})
        self.assertEqual(len(session.statements), 1)
        statement = session.statements[0]
        self.assertIn(
            "r4_member_legacy_health_profile_create_v1",
            str(statement),
        )
        parameters = statement.compile().params
        self.assertEqual(parameters["actor_user_id"], 1001)
        self.assertEqual(parameters["target_user_id"], 1001)
        self.assertEqual(parameters["medical_history"], '["hypertension"]')
        self.assertIsNone(parameters["allergy_history"])
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

    async def test_repository_does_not_accept_current_user_or_permissions(self):
        import inspect
        from app.modules.user_health.repository import (
            create_health_profile_record,
            get_health_profile_by_user_id,
        )

        get_params = inspect.signature(get_health_profile_by_user_id).parameters
        create_params = inspect.signature(create_health_profile_record).parameters

        self.assertEqual(list(get_params), ["session", "user_id"])
        self.assertEqual(list(create_params), ["session", "profile_data"])
