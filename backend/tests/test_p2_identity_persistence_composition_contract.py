import importlib
from datetime import datetime, timezone
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import pytest
from sqlalchemy.exc import (
    DBAPIError,
    DisconnectionError,
    InterfaceError,
    OperationalError,
    TimeoutError as SqlAlchemyTimeoutError,
)

from app.modules.member.application.unit_of_work import (
    IdentityUnitOfWorkUnavailableError,
)
from app.modules.member.infrastructure.mapper import MemberMapper
from app.modules.member.infrastructure.orm_state_mapper import MemberOrmStateMapper
from app.modules.member.infrastructure.sqlalchemy_repository import (
    SqlAlchemyMemberRepository,
)
from app.modules.member.infrastructure.unit_of_work import (
    SqlAlchemyIdentityUnitOfWork,
)


COMPOSITION_MODULE = "app.composition.identity_persistence"
EXPECTED_RED = "Identity persistence composition root is not implemented"
NOW = datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc)
COMPOSITION_SCENARIOS = (
    "FACTORY-001",
    "ISOLATION-001",
    "LEAK-001",
    "REPOSITORY-001",
    "UOW-001",
)


class SessionFactoryBuilderSpy:
    def __init__(self):
        self.calls = []
        self.product = object()

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.product


class PostgreSqlFailureSentinel(RuntimeError):
    pass


class AsyncSessionSpy:
    def __init__(self, *, begin_failure=None):
        self.begin_failure = begin_failure
        self.calls = {"begin": 0, "commit": 0, "rollback": 0, "close": 0}

    async def begin(self):
        self.calls["begin"] += 1
        if self.begin_failure is not None:
            raise self.begin_failure

    async def commit(self):
        self.calls["commit"] += 1

    async def rollback(self):
        self.calls["rollback"] += 1

    async def close(self):
        self.calls["close"] += 1


class AsyncSessionFactorySpy:
    def __init__(self, *, begin_failure=None):
        self.begin_failure = begin_failure
        self.sessions = []

    def __call__(self):
        session = AsyncSessionSpy(begin_failure=self.begin_failure)
        self.sessions.append(session)
        return session


def _load_composition_module():
    try:
        return importlib.import_module(COMPOSITION_MODULE)
    except ModuleNotFoundError as exc:
        target_is_missing = (
            exc.name == COMPOSITION_MODULE
            or COMPOSITION_MODULE.startswith(f"{exc.name}.")
        )
        if target_is_missing:
            pytest.fail(EXPECTED_RED)
        raise


class TestIdentityPersistenceCompositionContract(IsolatedAsyncioTestCase):
    async def test_identity_persistence_composition_contract(self):
        module = _load_composition_module()
        factory_builder = getattr(module, "create_identity_session_factory", None)
        composition_type = getattr(module, "IdentityPersistenceComposition", None)

        self.assertTrue(callable(factory_builder))
        self.assertTrue(isinstance(composition_type, type))

        with self.subTest(scenario="FACTORY-001"):
            engine = object()
            builder_spy = SessionFactoryBuilderSpy()
            with patch.object(module, "async_sessionmaker", builder_spy):
                session_factory = factory_builder(engine)

            self.assertIs(session_factory, builder_spy.product)
            self.assertEqual(len(builder_spy.calls), 1)
            options = builder_spy.calls[0]
            self.assertIs(options["bind"], engine)
            self.assertIs(options["class_"], module.AsyncSession)
            self.assertIs(options["expire_on_commit"], False)
            self.assertIs(options["autoflush"], False)

        with self.subTest(scenario="UOW-001/REPOSITORY-001"):
            session_factory = AsyncSessionFactorySpy()
            clock = lambda: NOW
            composition = composition_type(
                session_factory=session_factory,
                clock=clock,
            )
            uow = composition.unit_of_work()

            self.assertIs(type(uow), SqlAlchemyIdentityUnitOfWork)
            self.assertEqual(session_factory.sessions, [])
            async with uow:
                repository = uow.members
                self.assertIs(type(repository), SqlAlchemyMemberRepository)
                self.assertIs(repository._session, session_factory.sessions[0])
                self.assertIs(type(repository._mapper), MemberMapper)
                self.assertIs(type(repository._orm_mapper), MemberOrmStateMapper)
                self.assertIs(repository._clock, clock)
                await uow.commit()

            self.assertEqual(
                session_factory.sessions[0].calls,
                {"begin": 1, "commit": 1, "rollback": 0, "close": 1},
            )

        with self.subTest(scenario="ISOLATION-001"):
            session_factory = AsyncSessionFactorySpy()
            composition = composition_type(
                session_factory=session_factory,
                clock=lambda: NOW,
            )
            first = composition.unit_of_work()
            second = composition.unit_of_work()

            self.assertIsNot(first, second)
            async with first:
                first_repository = first.members
            async with second:
                second_repository = second.members

            self.assertEqual(len(session_factory.sessions), 2)
            self.assertIsNot(session_factory.sessions[0], session_factory.sessions[1])
            self.assertIsNot(first_repository, second_repository)

        unavailable_failures = (
            OperationalError(
                "BEGIN", {}, PostgreSqlFailureSentinel("connection down")
            ),
            InterfaceError(
                "BEGIN", {}, PostgreSqlFailureSentinel("connection down")
            ),
            SqlAlchemyTimeoutError("connection pool timed out"),
            DisconnectionError("connection was disconnected"),
            DBAPIError(
                "BEGIN",
                {},
                PostgreSqlFailureSentinel("connection down"),
                connection_invalidated=True,
            ),
        )
        for failure in unavailable_failures:
            with self.subTest(
                scenario="UOW-001/unavailable-sqlalchemy",
                failure_type=type(failure),
            ):
                session_factory = AsyncSessionFactorySpy(
                    begin_failure=failure
                )
                composition = composition_type(
                    session_factory=session_factory,
                    clock=lambda: NOW,
                )
                with self.assertRaises(
                    IdentityUnitOfWorkUnavailableError
                ) as caught:
                    async with composition.unit_of_work():
                        pass
                self.assertEqual(
                    str(caught.exception),
                    "identity persistence is unavailable",
                )
                self.assertIs(caught.exception.__cause__, failure)
                self.assertNotIn("BEGIN", str(caught.exception))
                self.assertEqual(
                    session_factory.sessions[0].calls["rollback"], 1
                )
                self.assertEqual(
                    session_factory.sessions[0].calls["close"], 1
                )

        with self.subTest(scenario="LEAK-001"):
            public_names = set(vars(composition_type))
            self.assertNotIn("engine", public_names)
            self.assertNotIn("session", public_names)
            self.assertNotIn("repository", public_names)
