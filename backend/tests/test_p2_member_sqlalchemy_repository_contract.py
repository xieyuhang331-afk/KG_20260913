import importlib
import inspect
import uuid
from dataclasses import dataclass
from unittest import IsolatedAsyncioTestCase

import pytest

from app.modules.member import CreationSource, Member, MemberNo, MemberStatus
from app.modules.member.errors import InvalidMemberIdError
from app.modules.member.repository import (
    MemberNotFoundError,
    MemberPersistenceUnavailableError,
    MemberRepository,
    MemberRepositoryError,
    MemberUniquenessConflictError,
    MemberVersionConflictError,
)


ADAPTER_MODULE = "app.modules.member.infrastructure.sqlalchemy_repository"
ADAPTER_CLASS = "SqlAlchemyMemberRepository"
EXPECTED_RED = (
    "Concrete SQLAlchemy MemberRepository Adapter is not implemented"
)
GENERIC_ERROR_MESSAGE = "member persistence operation failed"
VALID_MEMBER_ID = uuid.UUID("01890f5d-6d12-7cc4-98c4-dc0c0c07398f")
OPERATIONS = (
    "get_by_id",
    "get_by_member_no",
    "is_member_no_available",
    "add",
    "save",
)
TRANSACTION_FINALIZERS = ("commit", "rollback", "close")
LEAKAGE_ATTRIBUTES = {
    "_sa_instance_state",
    "connection",
    "engine",
    "result",
    "row",
    "session",
    "transaction",
}
BEHAVIOR_SCENARIOS = (
    "ASYNC-001",
    "ERR-GENERIC",
    "ERR-INVALID-ID",
    "ERR-NOT-FOUND",
    "ERR-UNAVAILABLE",
    "ERR-UNIQUE",
    "ERR-VERSION",
    "LEAK-001",
    "MAP-ADD",
    "MAP-READ",
    "MAP-SAVE",
    "TX-COMMAND",
    "TX-QUERY",
    "UUID-001",
)


@dataclass(frozen=True, slots=True)
class PersistenceStateStub:
    member_id: uuid.UUID
    member_no: str
    creation_source: str
    status: str
    version: int


class ResultStub:
    def __init__(self, *, value=None, rowcount=1):
        self.value = value
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return self.value


class FailureSentinel(RuntimeError):
    def __init__(self, category, details="test-only infrastructure failure"):
        super().__init__(details)
        self.category = category


class AsyncSessionSpy:
    def __init__(self, *results, execute_failure=None, flush_failure=None):
        self._results = list(results)
        self._execute_failure = execute_failure
        self._flush_failure = flush_failure
        self.execute_calls = []
        self.add_calls = []
        self.calls = {
            "flush": 0,
            "commit": 0,
            "rollback": 0,
            "close": 0,
        }

    async def execute(self, *args, **kwargs):
        self.execute_calls.append((args, kwargs))
        if self._execute_failure is not None:
            raise self._execute_failure
        if not self._results:
            raise AssertionError("AsyncSessionSpy has no configured result")
        return self._results.pop(0)

    def add(self, value):
        self.add_calls.append(value)

    async def flush(self):
        self.calls["flush"] += 1
        if self._flush_failure is not None:
            raise self._flush_failure

    async def commit(self):
        self.calls["commit"] += 1

    async def rollback(self):
        self.calls["rollback"] += 1

    async def close(self):
        self.calls["close"] += 1


class MapperSpy:
    def __init__(self, *, domain_value=None, persistence_state=None):
        self.domain_value = domain_value
        self.persistence_state = persistence_state
        self.to_domain_calls = []
        self.to_persistence_calls = []

    def to_domain(self, state):
        self.to_domain_calls.append(state)
        return self.domain_value

    def to_persistence(self, member, expected_version=None):
        self.to_persistence_calls.append((member, expected_version))
        return self.persistence_state


class ThirdPartyUuidStub:
    def __init__(self, value):
        self.value = value

    def __str__(self):
        return str(self.value)


def _load_adapter_module():
    try:
        return importlib.import_module(ADAPTER_MODULE)
    except ModuleNotFoundError as exc:
        target_is_missing = (
            exc.name == ADAPTER_MODULE
            or ADAPTER_MODULE.startswith(f"{exc.name}.")
        )
        if target_is_missing:
            pytest.fail(EXPECTED_RED)
        raise


def _member():
    return Member(
        member_id=VALID_MEMBER_ID,
        member_no=MemberNo("M-P2-AT1-0001"),
        creation_source=CreationSource.REGISTRATION,
        status=MemberStatus.CREATED,
    )


def _state(*, version=3):
    return PersistenceStateStub(
        member_id=VALID_MEMBER_ID,
        member_no="M-P2-AT1-0001",
        creation_source="registration",
        status="created",
        version=version,
    )


def _adapter(adapter_type, session, mapper):
    signature = inspect.signature(adapter_type)
    assert "session" in signature.parameters
    assert "mapper" in signature.parameters
    return adapter_type(session=session, mapper=mapper)


def _call_contains_identity(call, target):
    args, kwargs = call
    return any(value is target for value in args) or any(
        value is target for value in kwargs.values()
    )


class TestSqlAlchemyRepositoryAdapterContract(IsolatedAsyncioTestCase):
    def assertNoTransactionFinalization(self, session):
        for operation in TRANSACTION_FINALIZERS:
            self.assertEqual(session.calls[operation], 0)

    async def test_at1_sqlalchemy_repository_adapter_contract(self):
        module = _load_adapter_module()
        adapter_type = getattr(module, ADAPTER_CLASS, None)

        self.assertTrue(inspect.isclass(adapter_type))
        self.assertIsNot(adapter_type, MemberRepository)
        self.assertTrue(
            all(
                inspect.iscoroutinefunction(
                    getattr(adapter_type, operation, None)
                )
                for operation in OPERATIONS
            )
        )
        self.assertTrue(
            {
                f"{operation}_async" for operation in OPERATIONS
            }.isdisjoint(vars(adapter_type))
        )

        await self._assert_mapping(adapter_type)
        await self._assert_error_translation(adapter_type)
        await self._assert_uuid_boundary(adapter_type)
        await self._assert_async_and_leakage(adapter_type)

    async def _assert_mapping(self, adapter_type):
        member = _member()
        state = _state()

        with self.subTest(scenario="MAP-READ/TX-QUERY/UUID-001"):
            session = AsyncSessionSpy(ResultStub(value=state))
            mapper = MapperSpy(domain_value=member)
            result = await _adapter(adapter_type, session, mapper).get_by_id(
                VALID_MEMBER_ID
            )

            self.assertIs(type(result), Member)
            self.assertIs(type(result.member_id), uuid.UUID)
            self.assertEqual(result.member_id, VALID_MEMBER_ID)
            self.assertEqual(result.member_id.version, 7)
            self.assertEqual(result.member_id.variant, uuid.RFC_4122)
            self.assertIs(type(result.member_no), MemberNo)
            self.assertIs(result.status, MemberStatus.CREATED)
            self.assertIs(
                result.creation_source, CreationSource.REGISTRATION
            )
            self.assertEqual(len(mapper.to_domain_calls), 1)
            self.assertIs(mapper.to_domain_calls[0], state)
            self.assertEqual(mapper.to_domain_calls[0].version, 3)
            self.assertEqual(session.calls["flush"], 0)
            self.assertNoTransactionFinalization(session)
            self.assertTrue(LEAKAGE_ATTRIBUTES.isdisjoint(dir(result)))

        with self.subTest(scenario="MAP-READ/member-no"):
            session = AsyncSessionSpy(ResultStub(value=state))
            mapper = MapperSpy(domain_value=member)
            result = await _adapter(
                adapter_type, session, mapper
            ).get_by_member_no(member.member_no)

            self.assertIs(result, member)
            self.assertIs(mapper.to_domain_calls[0], state)
            self.assertEqual(session.calls["flush"], 0)
            self.assertNoTransactionFinalization(session)

        with self.subTest(scenario="TX-QUERY/member-no-available"):
            session = AsyncSessionSpy(ResultStub(value=None))
            available = await _adapter(
                adapter_type, session, MapperSpy()
            ).is_member_no_available(member.member_no)

            self.assertIs(available, True)
            self.assertEqual(session.calls["flush"], 0)
            self.assertNoTransactionFinalization(session)

        with self.subTest(scenario="TX-QUERY/member-no-unavailable"):
            session = AsyncSessionSpy(ResultStub(value=state))
            available = await _adapter(
                adapter_type, session, MapperSpy()
            ).is_member_no_available(member.member_no)

            self.assertIs(available, False)
            self.assertEqual(session.calls["flush"], 0)
            self.assertNoTransactionFinalization(session)

        with self.subTest(scenario="MAP-ADD/TX-COMMAND"):
            persistence_state = _state(version=1)
            session = AsyncSessionSpy()
            mapper = MapperSpy(persistence_state=persistence_state)
            result = await _adapter(adapter_type, session, mapper).add(member)

            self.assertIsNone(result)
            self.assertEqual(mapper.to_persistence_calls, [(member, None)])
            self.assertEqual(len(session.add_calls), 1)
            self.assertIs(session.add_calls[0], persistence_state)
            self.assertLessEqual(session.calls["flush"], 1)
            self.assertNoTransactionFinalization(session)

        with self.subTest(scenario="MAP-SAVE/TX-COMMAND"):
            expected_version = object()
            persistence_state = _state(version=3)
            session = AsyncSessionSpy(ResultStub(rowcount=1))
            mapper = MapperSpy(persistence_state=persistence_state)
            result = await _adapter(adapter_type, session, mapper).save(
                member, expected_version
            )

            self.assertIsNone(result)
            self.assertEqual(len(mapper.to_persistence_calls), 1)
            mapped_member, mapped_version = mapper.to_persistence_calls[0]
            self.assertIs(mapped_member, member)
            self.assertIs(mapped_version, expected_version)
            self.assertEqual(len(session.execute_calls), 1)
            self.assertTrue(
                _call_contains_identity(
                    session.execute_calls[0], persistence_state
                )
            )
            self.assertTrue(
                _call_contains_identity(
                    session.execute_calls[0], expected_version
                )
            )
            self.assertLessEqual(session.calls["flush"], 1)
            self.assertNoTransactionFinalization(session)

    async def _assert_error_translation(self, adapter_type):
        member = _member()

        with self.subTest(scenario="ERR-NOT-FOUND/by-id"):
            session = AsyncSessionSpy(ResultStub(value=None))
            with self.assertRaises(MemberNotFoundError) as caught:
                await _adapter(
                    adapter_type, session, MapperSpy()
                ).get_by_id(VALID_MEMBER_ID)
            self.assertEqual(str(caught.exception), "member was not found")
            self.assertNoTransactionFinalization(session)

        with self.subTest(scenario="ERR-NOT-FOUND/by-member-no"):
            session = AsyncSessionSpy(ResultStub(value=None))
            with self.assertRaises(MemberNotFoundError):
                await _adapter(
                    adapter_type, session, MapperSpy()
                ).get_by_member_no(member.member_no)
            self.assertNoTransactionFinalization(session)

        with self.subTest(scenario="ERR-NOT-FOUND/save"):
            failure = FailureSentinel("not_found")
            session = AsyncSessionSpy(execute_failure=failure)
            mapper = MapperSpy(persistence_state=_state())
            with self.assertRaises(MemberNotFoundError) as caught:
                await _adapter(adapter_type, session, mapper).save(
                    member, object()
                )
            self.assertEqual(str(caught.exception), "member was not found")
            self.assertIs(caught.exception.__cause__, failure)
            self.assertNoTransactionFinalization(session)

        with self.subTest(scenario="ERR-UNIQUE"):
            failure = FailureSentinel("unique_conflict")
            session = AsyncSessionSpy(flush_failure=failure)
            mapper = MapperSpy(persistence_state=_state())
            with self.assertRaises(MemberUniquenessConflictError) as caught:
                await _adapter(adapter_type, session, mapper).add(member)
            self.assertEqual(
                str(caught.exception), "member uniqueness conflict"
            )
            self.assertIs(caught.exception.__cause__, failure)
            self.assertNoTransactionFinalization(session)

        with self.subTest(scenario="ERR-UNIQUE/save"):
            failure = FailureSentinel("unique_conflict")
            session = AsyncSessionSpy(execute_failure=failure)
            mapper = MapperSpy(persistence_state=_state())
            with self.assertRaises(MemberUniquenessConflictError) as caught:
                await _adapter(adapter_type, session, mapper).save(
                    member, object()
                )
            self.assertEqual(
                str(caught.exception), "member uniqueness conflict"
            )
            self.assertIs(caught.exception.__cause__, failure)
            self.assertNoTransactionFinalization(session)

        with self.subTest(scenario="ERR-VERSION"):
            failure = FailureSentinel("version_conflict")
            session = AsyncSessionSpy(execute_failure=failure)
            mapper = MapperSpy(persistence_state=_state())
            with self.assertRaises(MemberVersionConflictError) as caught:
                await _adapter(adapter_type, session, mapper).save(
                    member, object()
                )
            self.assertEqual(str(caught.exception), "member version conflict")
            self.assertIs(caught.exception.__cause__, failure)
            self.assertNoTransactionFinalization(session)

        with self.subTest(scenario="ERR-UNAVAILABLE"):
            failure = FailureSentinel("unavailable")
            session = AsyncSessionSpy(execute_failure=failure)
            with self.assertRaises(MemberPersistenceUnavailableError) as caught:
                await _adapter(
                    adapter_type, session, MapperSpy()
                ).get_by_id(VALID_MEMBER_ID)
            self.assertEqual(
                str(caught.exception), "member persistence is unavailable"
            )
            self.assertIs(caught.exception.__cause__, failure)
            self.assertNoTransactionFinalization(session)

        with self.subTest(scenario="ERR-GENERIC"):
            failure = FailureSentinel(
                "generic",
                "driver details and sensitive parameters must be redacted",
            )
            session = AsyncSessionSpy(execute_failure=failure)
            with self.assertRaises(MemberRepositoryError) as caught:
                await _adapter(
                    adapter_type, session, MapperSpy()
                ).get_by_id(VALID_MEMBER_ID)
            self.assertIs(type(caught.exception), MemberRepositoryError)
            self.assertEqual(str(caught.exception), GENERIC_ERROR_MESSAGE)
            self.assertIs(caught.exception.__cause__, failure)
            self.assertNotIn("driver details", str(caught.exception))
            self.assertNoTransactionFinalization(session)

    async def _assert_uuid_boundary(self, adapter_type):
        non_rfc_uuid = uuid.UUID(int=VALID_MEMBER_ID.int & ~(0b11 << 62))
        invalid_ids = (
            uuid.uuid4(),
            non_rfc_uuid,
            str(VALID_MEMBER_ID),
            VALID_MEMBER_ID.bytes,
            VALID_MEMBER_ID.int,
            None,
            ThirdPartyUuidStub(VALID_MEMBER_ID),
        )

        for invalid_id in invalid_ids:
            with self.subTest(
                scenario="ERR-INVALID-ID", invalid_type=type(invalid_id)
            ):
                session = AsyncSessionSpy()
                mapper = MapperSpy()
                with self.assertRaises(InvalidMemberIdError):
                    await _adapter(
                        adapter_type, session, mapper
                    ).get_by_id(invalid_id)
                self.assertEqual(session.execute_calls, [])
                self.assertEqual(mapper.to_domain_calls, [])
                self.assertEqual(mapper.to_persistence_calls, [])
                self.assertEqual(session.calls["flush"], 0)
                self.assertNoTransactionFinalization(session)

    async def _assert_async_and_leakage(self, adapter_type):
        with self.subTest(scenario="ASYNC-001"):
            for operation in OPERATIONS:
                self.assertTrue(
                    inspect.iscoroutinefunction(
                        getattr(adapter_type, operation, None)
                    )
                )
                self.assertNotIn(f"{operation}_async", vars(adapter_type))

        with self.subTest(scenario="LEAK-001"):
            member = _member()
            session = AsyncSessionSpy(ResultStub(value=_state()))
            result = await _adapter(
                adapter_type, session, MapperSpy(domain_value=member)
            ).get_by_id(VALID_MEMBER_ID)

            self.assertIs(type(result), Member)
            self.assertTrue(LEAKAGE_ATTRIBUTES.isdisjoint(dir(result)))
            self.assertTrue(
                LEAKAGE_ATTRIBUTES.isdisjoint(dir(result.member_no))
            )
            self.assertNoTransactionFinalization(session)
