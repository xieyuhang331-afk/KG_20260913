import unittest


class UserIdentityRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_user_by_id_returns_user(self):
        from app.modules.auth.models import User
        from app.modules.auth.repository import get_user_by_id

        user = User()
        user.id = 1001

        class Result:
            def scalar_one_or_none(self):
                return user

        class FakeSession:
            def __init__(self):
                self.execute_called = False

            async def execute(self, statement):
                self.execute_called = True
                return Result()

        session = FakeSession()
        result = await get_user_by_id(session, 1001)

        self.assertIs(result, user)
        self.assertTrue(session.execute_called)

    async def test_update_user_identity_is_retired_without_plaintext_write(self):
        from app.modules.auth.models import User
        from app.modules.auth.repository import update_user_identity

        user = User()
        user.id = 1001
        user.real_name = None
        user.id_card = None
        user.verify_status = None

        class FakeSession:
            def __init__(self):
                self.flush_called = False
                self.commit_called = False
                self.rollback_called = False

            async def flush(self):
                self.flush_called = True

            async def commit(self):
                self.commit_called = True

            async def rollback(self):
                self.rollback_called = True

        session = FakeSession()
        with self.assertRaisesRegex(RuntimeError, "LEGACY_IDENTITY_ENDPOINT_RETIRED"):
            await update_user_identity(
                session,
                user,
                real_name="Zhang San",
                id_card="110101199001011234",
                verify_status="submitted",
            )

        self.assertIsNone(user.real_name)
        self.assertIsNone(user.id_card)
        self.assertIsNone(user.verify_status)
        self.assertFalse(session.flush_called)
        self.assertFalse(session.commit_called)
        self.assertFalse(session.rollback_called)

