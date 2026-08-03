import asyncio
import importlib
import inspect
from unittest import IsolatedAsyncioTestCase

import pytest


APPLICATION_MODULE = "app.modules.member.application.unit_of_work"
INFRASTRUCTURE_MODULE = "app.modules.member.infrastructure.unit_of_work"
EXPECTED_RED = "Identity Unit of Work contract is not implemented"
UOW_SCENARIOS = (
    "CANCEL-001",
    "ERROR-001",
    "LIFECYCLE-001",
    "PRIMARY-001",
    "STATE-001",
    "TX-COMMIT",
    "TX-ROLLBACK",
)


class FailureSentinel(RuntimeError):
    def __init__(self, category="generic", details="internal failure"):
        super().__init__(details)
        self.category = category


class AsyncSessionSpy:
    def __init__(
        self,
        *,
        begin_failure=None,
        commit_failure=None,
        rollback_failure=None,
        close_failure=None,
    ):
        self.begin_failure = begin_failure
        self.commit_failure = commit_failure
        self.rollback_failure = rollback_failure
        self.close_failure = close_failure
        self.calls = {"begin": 0, "commit": 0, "rollback": 0, "close": 0}

    async def begin(self):
        self.calls["begin"] += 1
        if self.begin_failure is not None:
            raise self.begin_failure

    async def commit(self):
        self.calls["commit"] += 1
        if self.commit_failure is not None:
            raise self.commit_failure

    async def rollback(self):
        self.calls["rollback"] += 1
        if self.rollback_failure is not None:
            raise self.rollback_failure

    async def close(self):
        self.calls["close"] += 1
        if self.close_failure is not None:
            raise self.close_failure


class AsyncSessionFactorySpy:
    def __init__(self, session=None, failure=None):
        self.session = session or AsyncSessionSpy()
        self.failure = failure
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return self.session


class MemberRepositoryFactorySpy:
    def __init__(self):
        self.repository = object()
        self.sessions = []

    def __call__(self, session):
        self.sessions.append(session)
        return self.repository


def _load_module(name):
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        target_is_missing = exc.name == name or name.startswith(f"{exc.name}.")
        if target_is_missing:
            pytest.fail(EXPECTED_RED)
        raise


class TestIdentityUnitOfWorkContract(IsolatedAsyncioTestCase):
    async def test_identity_unit_of_work_contract(self):
        application = _load_module(APPLICATION_MODULE)
        infrastructure = _load_module(INFRASTRUCTURE_MODULE)

        port = getattr(application, "IdentityUnitOfWork", None)
        base_error = getattr(application, "IdentityUnitOfWorkError", None)
        unavailable_error = getattr(
            application, "IdentityUnitOfWorkUnavailableError", None
        )
        state_error = getattr(
            application, "IdentityUnitOfWorkStateError", None
        )
        adapter_type = getattr(
            infrastructure, "SqlAlchemyIdentityUnitOfWork", None
        )

        self.assertTrue(getattr(port, "_is_protocol", False))
        self.assertTrue(inspect.isclass(adapter_type))
        self.assertTrue(issubclass(unavailable_error, base_error))
        self.assertTrue(issubclass(state_error, base_error))
        for operation in ("__aenter__", "__aexit__", "commit", "rollback"):
            self.assertTrue(
                inspect.iscoroutinefunction(getattr(adapter_type, operation))
            )

        def new_uow(session=None, session_failure=None):
            session_factory = AsyncSessionFactorySpy(
                session=session, failure=session_failure
            )
            repository_factory = MemberRepositoryFactorySpy()
            uow = adapter_type(
                session_factory=session_factory,
                repository_factory=repository_factory,
            )
            return uow, session_factory, repository_factory

        with self.subTest(scenario="LIFECYCLE-001/TX-COMMIT"):
            session = AsyncSessionSpy()
            uow, factory, repository_factory = new_uow(session)
            async with uow as entered:
                self.assertIs(entered, uow)
                self.assertIs(uow.members, repository_factory.repository)
                self.assertEqual(repository_factory.sessions, [session])
                await uow.commit()

            self.assertEqual(factory.calls, 1)
            self.assertEqual(
                session.calls,
                {"begin": 1, "commit": 1, "rollback": 0, "close": 1},
            )

        with self.subTest(scenario="TX-ROLLBACK/normal-exit"):
            session = AsyncSessionSpy()
            uow, _, _ = new_uow(session)
            async with uow:
                pass

            self.assertEqual(session.calls["rollback"], 1)
            self.assertEqual(session.calls["close"], 1)

        with self.subTest(scenario="TX-ROLLBACK/explicit"):
            session = AsyncSessionSpy()
            uow, _, _ = new_uow(session)
            async with uow:
                await uow.rollback()

            self.assertEqual(session.calls["rollback"], 1)
            self.assertEqual(session.calls["close"], 1)

        with self.subTest(scenario="STATE-001"):
            uow, _, _ = new_uow()
            with self.assertRaises(state_error) as caught:
                await uow.commit()
            self.assertEqual(
                str(caught.exception), "identity transaction state is invalid"
            )
            with self.assertRaises(state_error):
                await uow.rollback()

            async with uow:
                with self.assertRaises(state_error):
                    async with uow:
                        pass
                await uow.commit()
                with self.assertRaises(state_error):
                    await uow.commit()
            with self.assertRaises(state_error):
                async with uow:
                    pass

        with self.subTest(scenario="ERROR-001/unavailable"):
            failure = FailureSentinel("unavailable", "host and credential")
            uow, _, _ = new_uow(session_failure=failure)
            with self.assertRaises(unavailable_error) as caught:
                async with uow:
                    pass
            self.assertEqual(
                str(caught.exception), "identity persistence is unavailable"
            )
            self.assertIs(caught.exception.__cause__, failure)
            self.assertNotIn("credential", str(caught.exception))

        with self.subTest(scenario="ERROR-001/commit"):
            failure = FailureSentinel(details="driver and SQL details")
            session = AsyncSessionSpy(commit_failure=failure)
            uow, _, _ = new_uow(session)
            with self.assertRaises(base_error) as caught:
                async with uow:
                    await uow.commit()
            self.assertIs(type(caught.exception), base_error)
            self.assertEqual(
                str(caught.exception), "identity transaction operation failed"
            )
            self.assertIs(caught.exception.__cause__, failure)
            self.assertEqual(session.calls["rollback"], 1)
            self.assertEqual(session.calls["close"], 1)

        with self.subTest(scenario="PRIMARY-001"):
            primary = RuntimeError("primary application failure")
            session = AsyncSessionSpy(
                rollback_failure=FailureSentinel(),
                close_failure=FailureSentinel(),
            )
            uow, _, _ = new_uow(session)
            with self.assertRaises(RuntimeError) as caught:
                async with uow:
                    raise primary
            self.assertIs(caught.exception, primary)
            self.assertEqual(session.calls["rollback"], 1)
            self.assertEqual(session.calls["close"], 1)

        with self.subTest(scenario="CANCEL-001"):
            session = AsyncSessionSpy()
            uow, _, _ = new_uow(session)
            with self.assertRaises(asyncio.CancelledError):
                async with uow:
                    raise asyncio.CancelledError()
            self.assertEqual(session.calls["rollback"], 1)
            self.assertEqual(session.calls["close"], 1)
