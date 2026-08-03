import importlib
import inspect
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from unittest import IsolatedAsyncioTestCase

import pytest

from app.modules.member import CreationSource, Member, MemberNo, MemberStatus
from app.modules.member.errors import (
    InvalidMemberIdError,
    InvalidMemberStatusError,
)
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
EXPECTED_RED = "Concrete MemberRepository Adapter is not implemented"
GENERIC_ERROR_MESSAGE = "member persistence operation failed"
STORED_STATE_ERROR_MESSAGE = "stored member state is invalid"
VALID_MEMBER_ID = uuid.UUID("01890f5d-6d12-7cc4-98c4-dc0c0c07398f")
NOW = datetime(2026, 8, 3, 6, 0, tzinfo=timezone.utc)
OPERATIONS = (
    "get_by_id",
    "get_by_member_no",
    "is_member_no_available",
    "add",
    "save",
)
BEHAVIOR_SCENARIOS = (
    "ASYNC-001",
    "ASYNC-002",
    "ERR-001",
    "ERR-002",
    "ERR-003",
    "ERR-004",
    "ERR-005",
    "ERR-006",
    "LEAK-001",
    "LEAK-002",
    "MAP-001",
    "MAP-002",
    "MAP-003",
    "MAP-004",
    "MAP-005",
    "TX-001",
    "TX-002",
    "TX-003",
    "TX-004",
    "UUID-001",
    "UUID-002",
    "UUID-003",
)


@dataclass(frozen=True, slots=True)
class PersistenceStateStub:
    member_id: uuid.UUID
    member_no: str
    creation_source: str
    status: str


class ResultStub:
    def __init__(self, *, value=None, rowcount=1):
        self.value = value
        self.rowcount = rowcount

    def scalar(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def one_or_none(self):
        return self.value


class FailureSentinel(RuntimeError):
    def __init__(self, category, details="test-only infrastructure failure"):
        super().__init__(details)
        self.category = category


class SessionSpy:
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
            raise AssertionError("SessionSpy has no configured result")
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
    def __init__(
        self,
        *,
        domain_value=None,
        domain_failure=None,
        persistence_state=None,
    ):
        self.domain_value = domain_value
        self.domain_failure = domain_failure
        self.persistence_state = persistence_state
        self.to_domain_calls = []
        self.to_persistence_calls = []

    def to_domain(self, state):
        self.to_domain_calls.append(state)
        if self.domain_failure is not None:
            raise self.domain_failure
        return self.domain_value

    restore = to_domain
    to_member = to_domain

    def to_persistence(self, member, expected_version=None):
        self.to_persistence_calls.append((member, expected_version))
        if self.persistence_state is None:
            self.persistence_state = PersistenceStateStub(
                member_id=member.member_id,
                member_no=member.member_no.value,
                creation_source=member.creation_source.value,
                status=member.status.value,
            )
        return self.persistence_state

    from_domain = to_persistence
    to_record = to_persistence


class OrmMapperSpy:
    def to_state(self, model):
        return model

    def to_new_model(
        self, state, *, initial_version, created_at, updated_at
    ):
        return state


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
        member_no=MemberNo("M-P2-CT1-0001"),
        creation_source=CreationSource.REGISTRATION,
        status=MemberStatus.CREATED,
    )


def _state():
    return PersistenceStateStub(
        member_id=VALID_MEMBER_ID,
        member_no="M-P2-CT1-0001",
        creation_source="registration",
        status="created",
    )


def _adapter(adapter_type, session, mapper):
    signature = inspect.signature(adapter_type)
    assert "session" in signature.parameters
    assert "mapper" in signature.parameters
    assert "orm_mapper" in signature.parameters
    assert "clock" in signature.parameters
    return adapter_type(
        session=session,
        mapper=mapper,
        orm_mapper=OrmMapperSpy(),
        clock=lambda: NOW,
    )


def _assert_no_transaction_finalization(test_case, session):
    test_case.assertEqual(session.calls["commit"], 0)
    test_case.assertEqual(session.calls["rollback"], 0)
    test_case.assertEqual(session.calls["close"], 0)


class TestConcreteAdapterBehavioralContract(IsolatedAsyncioTestCase):
    async def test_ct1_concrete_adapter_behavioral_contract(self):
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

        await self._assert_domain_mapping(adapter_type)
        await self._assert_error_translation(adapter_type)
        await self._assert_uuid_boundary(adapter_type)
        await self._assert_async_and_leakage_boundaries(adapter_type)

    async def _assert_domain_mapping(self, adapter_type):
        member = _member()
        state = _state()

        with self.subTest(scenario="MAP-001/TX-001/LEAK-001"):
            session = SessionSpy(ResultStub(value=state))
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
            self.assertEqual(mapper.to_domain_calls, [state])
            self.assertEqual(session.calls["flush"], 0)
            _assert_no_transaction_finalization(self, session)

        with self.subTest(scenario="MAP-002/TX-001"):
            session = SessionSpy(ResultStub(value=state))
            mapper = MapperSpy(domain_value=member)
            result = await _adapter(
                adapter_type, session, mapper
            ).get_by_member_no(member.member_no)

            self.assertIs(type(result), Member)
            self.assertEqual(result, member)
            self.assertEqual(mapper.to_domain_calls, [state])
            self.assertEqual(session.calls["flush"], 0)
            _assert_no_transaction_finalization(self, session)

        with self.subTest(scenario="MAP-003"):
            failure = InvalidMemberStatusError("unsupported stored status")
            session = SessionSpy(ResultStub(value=state))
            mapper = MapperSpy(domain_failure=failure)

            with self.assertRaises(MemberRepositoryError) as caught:
                await _adapter(
                    adapter_type, session, mapper
                ).get_by_id(VALID_MEMBER_ID)

            self.assertIs(type(caught.exception), MemberRepositoryError)
            self.assertEqual(
                str(caught.exception), STORED_STATE_ERROR_MESSAGE
            )
            self.assertIs(caught.exception.__cause__, failure)
            _assert_no_transaction_finalization(self, session)

        with self.subTest(scenario="MAP-003/non-member-output"):
            session = SessionSpy(ResultStub(value=state))
            mapper = MapperSpy(domain_value={"member_id": VALID_MEMBER_ID})

            with self.assertRaises(MemberRepositoryError):
                await _adapter(
                    adapter_type, session, mapper
                ).get_by_id(VALID_MEMBER_ID)
            _assert_no_transaction_finalization(self, session)

        with self.subTest(scenario="MAP-004/TX-002"):
            session = SessionSpy()
            persistence_state = _state()
            mapper = MapperSpy(persistence_state=persistence_state)
            result = await _adapter(adapter_type, session, mapper).add(member)

            self.assertIsNone(result)
            self.assertEqual(mapper.to_persistence_calls, [(member, None)])
            self.assertEqual(len(session.add_calls), 1)
            self.assertIs(session.add_calls[0], persistence_state)
            self.assertLessEqual(session.calls["flush"], 1)
            _assert_no_transaction_finalization(self, session)

        with self.subTest(scenario="MAP-005/TX-003"):
            expected_version = 3
            session = SessionSpy(ResultStub(rowcount=1))
            persistence_state = _state()
            mapper = MapperSpy(persistence_state=persistence_state)
            result = await _adapter(adapter_type, session, mapper).save(
                member, expected_version
            )

            self.assertIsNone(result)
            self.assertEqual(
                mapper.to_persistence_calls,
                [(member, expected_version)],
            )
            self.assertEqual(len(session.execute_calls), 1)
            statement = session.execute_calls[0][0][0]
            self.assertNotIsInstance(statement, str)
            self.assertLessEqual(session.calls["flush"], 1)
            _assert_no_transaction_finalization(self, session)

    async def _assert_error_translation(self, adapter_type):
        member = _member()

        with self.subTest(scenario="ERR-001"):
            session = SessionSpy(ResultStub(value=None))
            with self.assertRaises(MemberNotFoundError) as caught:
                await _adapter(
                    adapter_type, session, MapperSpy()
                ).get_by_id(VALID_MEMBER_ID)
            self.assertEqual(str(caught.exception), "member was not found")
            _assert_no_transaction_finalization(self, session)

        with self.subTest(scenario="ERR-002"):
            session = SessionSpy(ResultStub(value=None))
            with self.assertRaises(MemberNotFoundError) as caught:
                await _adapter(
                    adapter_type, session, MapperSpy()
                ).get_by_member_no(member.member_no)
            self.assertEqual(str(caught.exception), "member was not found")
            _assert_no_transaction_finalization(self, session)

        with self.subTest(scenario="ERR-001/save-target-missing"):
            failure = FailureSentinel("not_found")
            session = SessionSpy(execute_failure=failure)
            with self.assertRaises(MemberNotFoundError) as caught:
                await _adapter(
                    adapter_type, session, MapperSpy()
                ).save(member, 3)
            self.assertEqual(str(caught.exception), "member was not found")
            self.assertIs(caught.exception.__cause__, failure)
            _assert_no_transaction_finalization(self, session)

        with self.subTest(scenario="ERR-003"):
            failure = FailureSentinel("unique_conflict")
            session = SessionSpy(flush_failure=failure)
            with self.assertRaises(MemberUniquenessConflictError) as caught:
                await _adapter(
                    adapter_type, session, MapperSpy()
                ).add(member)
            self.assertEqual(
                str(caught.exception), "member uniqueness conflict"
            )
            self.assertIs(caught.exception.__cause__, failure)
            _assert_no_transaction_finalization(self, session)

        with self.subTest(scenario="ERR-004"):
            failure = FailureSentinel("version_conflict")
            session = SessionSpy(execute_failure=failure)
            with self.assertRaises(MemberVersionConflictError) as caught:
                await _adapter(
                    adapter_type, session, MapperSpy()
                ).save(member, 3)
            self.assertEqual(str(caught.exception), "member version conflict")
            self.assertIs(caught.exception.__cause__, failure)
            _assert_no_transaction_finalization(self, session)

        with self.subTest(scenario="ERR-005"):
            failure = FailureSentinel("unavailable")
            session = SessionSpy(execute_failure=failure)
            with self.assertRaises(MemberPersistenceUnavailableError) as caught:
                await _adapter(
                    adapter_type, session, MapperSpy()
                ).get_by_id(VALID_MEMBER_ID)
            self.assertEqual(
                str(caught.exception), "member persistence is unavailable"
            )
            self.assertIs(caught.exception.__cause__, failure)
            _assert_no_transaction_finalization(self, session)

        with self.subTest(scenario="ERR-006/TX-004"):
            failure = FailureSentinel(
                "generic",
                "driver details and sensitive parameters must be redacted"
            )
            session = SessionSpy(execute_failure=failure)
            with self.assertRaises(MemberRepositoryError) as caught:
                await _adapter(
                    adapter_type, session, MapperSpy()
                ).get_by_id(VALID_MEMBER_ID)
            self.assertIs(type(caught.exception), MemberRepositoryError)
            self.assertEqual(str(caught.exception), GENERIC_ERROR_MESSAGE)
            self.assertIs(caught.exception.__cause__, failure)
            self.assertNotIn("driver details", str(caught.exception))
            _assert_no_transaction_finalization(self, session)

        with self.subTest(scenario="TX-001/member-number-availability"):
            session = SessionSpy(ResultStub(value=None))
            available = await _adapter(
                adapter_type, session, MapperSpy()
            ).is_member_no_available(member.member_no)
            self.assertIs(available, True)
            self.assertEqual(session.calls["flush"], 0)
            _assert_no_transaction_finalization(self, session)

    async def _assert_uuid_boundary(self, adapter_type):
        with self.subTest(scenario="UUID-001"):
            session = SessionSpy(ResultStub(value=_state()))
            result = await _adapter(
                adapter_type, session, MapperSpy(domain_value=_member())
            ).get_by_id(VALID_MEMBER_ID)
            self.assertIs(type(result.member_id), uuid.UUID)
            self.assertEqual(result.member_id, VALID_MEMBER_ID)

        non_rfc_uuid = uuid.UUID(
            int=VALID_MEMBER_ID.int & ~(0b11 << 62)
        )
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
                scenario="UUID-002/UUID-003", invalid_type=type(invalid_id)
            ):
                session = SessionSpy()
                mapper = MapperSpy()
                with self.assertRaises(InvalidMemberIdError):
                    await _adapter(
                        adapter_type, session, mapper
                    ).get_by_id(invalid_id)
                self.assertEqual(session.execute_calls, [])
                self.assertEqual(mapper.to_domain_calls, [])
                self.assertEqual(mapper.to_persistence_calls, [])
                _assert_no_transaction_finalization(self, session)

    async def _assert_async_and_leakage_boundaries(self, adapter_type):
        with self.subTest(scenario="ASYNC-001/ASYNC-002"):
            for operation in OPERATIONS:
                self.assertTrue(
                    inspect.iscoroutinefunction(
                        getattr(adapter_type, operation, None)
                    )
                )
                self.assertNotIn(f"{operation}_async", vars(adapter_type))

        with self.subTest(scenario="LEAK-002"):
            session = SessionSpy(ResultStub(value=_state()))
            result = await _adapter(
                adapter_type, session, MapperSpy(domain_value=_member())
            ).get_by_id(VALID_MEMBER_ID)
            forbidden_attributes = {
                "_sa_instance_state",
                "connection",
                "engine",
                "result",
                "row",
                "session",
                "transaction",
            }
            self.assertTrue(forbidden_attributes.isdisjoint(dir(result)))
            self.assertTrue(
                forbidden_attributes.isdisjoint(dir(result.member_no))
            )
            _assert_no_transaction_finalization(self, session)
