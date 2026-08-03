from datetime import datetime, timezone
from unittest import IsolatedAsyncioTestCase
from uuid import UUID

from sqlalchemy.exc import (
    DBAPIError,
    DisconnectionError,
    IntegrityError,
    InterfaceError,
    MultipleResultsFound,
    OperationalError,
    TimeoutError as SqlAlchemyTimeoutError,
)
from sqlalchemy.sql.dml import Update
from sqlalchemy.sql.selectable import Select

from app.modules.member import CreationSource, Member, MemberNo, MemberStatus
from app.modules.member.infrastructure.mapper import MemberMapper
from app.modules.member.infrastructure.models import MemberOrmModel
from app.modules.member.infrastructure.orm_state_mapper import (
    MemberOrmStateMapper,
)
from app.modules.member.infrastructure.sqlalchemy_repository import (
    SqlAlchemyMemberRepository,
)
from app.modules.member.repository import (
    MemberPersistenceUnavailableError,
    MemberRepositoryError,
    MemberUniquenessConflictError,
    MemberVersionConflictError,
)


VALID_MEMBER_ID = UUID("01890f5d-6d12-7cc4-98c4-dc0c0c07398f")
NOW = datetime(2026, 8, 3, 6, 0, tzinfo=timezone.utc)


class ResultStub:
    def __init__(self, *, value=None, rowcount=1, scalar_failure=None):
        self.value = value
        self.rowcount = rowcount
        self.scalar_failure = scalar_failure

    def scalar_one_or_none(self):
        if self.scalar_failure is not None:
            raise self.scalar_failure
        return self.value


class PostgreSqlFailureSentinel(RuntimeError):
    def __init__(self, *, sqlstate=None, constraint_name=None):
        super().__init__("sensitive driver details")
        self.sqlstate = sqlstate
        self.constraint_name = constraint_name


class AsyncSessionSpy:
    def __init__(self, *results, execute_failure=None, flush_failure=None):
        self._results = list(results)
        self._execute_failure = execute_failure
        self._flush_failure = flush_failure
        self.execute_calls = []
        self.add_calls = []
        self.flush_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0

    async def execute(self, statement):
        self.execute_calls.append(statement)
        if self._execute_failure is not None:
            raise self._execute_failure
        return self._results.pop(0)

    def add(self, value):
        self.add_calls.append(value)

    async def flush(self):
        self.flush_count += 1
        if self._flush_failure is not None:
            raise self._flush_failure


def _member() -> Member:
    return Member(
        member_id=VALID_MEMBER_ID,
        member_no=MemberNo("M-P2-ORM-ALIGN-0001"),
        creation_source=CreationSource.REGISTRATION,
        status=MemberStatus.CREATED,
    )


def _model(*, version=3) -> MemberOrmModel:
    return MemberOrmModel(
        member_id=VALID_MEMBER_ID,
        member_no="M-P2-ORM-ALIGN-0001",
        creation_source="registration",
        status="created",
        version=version,
        created_at=NOW,
        updated_at=NOW,
    )


def _repository(session: AsyncSessionSpy) -> SqlAlchemyMemberRepository:
    return SqlAlchemyMemberRepository(
        session=session,
        mapper=MemberMapper(),
        orm_mapper=MemberOrmStateMapper(),
        clock=lambda: NOW,
    )


class TestMemberRepositoryOrmAlignment(IsolatedAsyncioTestCase):
    async def test_repository_uses_orm_model_without_owning_transaction(self):
        member = _member()

        with self.subTest(operation="get_by_id"):
            session = AsyncSessionSpy(ResultStub(value=_model()))
            restored = await _repository(session).get_by_id(VALID_MEMBER_ID)

            self.assertEqual(restored, member)
            self.assertIsInstance(session.execute_calls[0], Select)
            self.assertEqual(session.flush_count, 0)

        with self.subTest(operation="get_by_member_no"):
            session = AsyncSessionSpy(ResultStub(value=_model()))
            restored = await _repository(session).get_by_member_no(
                member.member_no
            )

            self.assertEqual(restored, member)
            self.assertIsInstance(session.execute_calls[0], Select)
            self.assertEqual(session.flush_count, 0)

        with self.subTest(operation="is_member_no_available"):
            session = AsyncSessionSpy(ResultStub(value=None))
            available = await _repository(session).is_member_no_available(
                member.member_no
            )

            self.assertIs(available, True)
            self.assertIsInstance(session.execute_calls[0], Select)
            self.assertEqual(session.flush_count, 0)

        with self.subTest(operation="add"):
            session = AsyncSessionSpy()
            await _repository(session).add(member)

            self.assertEqual(len(session.add_calls), 1)
            model = session.add_calls[0]
            self.assertIs(type(model), MemberOrmModel)
            self.assertEqual(model.member_id, member.member_id)
            self.assertEqual(model.version, 1)
            self.assertIs(model.created_at, NOW)
            self.assertIs(model.updated_at, NOW)
            self.assertEqual(session.flush_count, 1)

        with self.subTest(operation="save"):
            session = AsyncSessionSpy(ResultStub(rowcount=1))
            await _repository(session).save(member, 3)

            self.assertEqual(len(session.execute_calls), 1)
            statement = session.execute_calls[0]
            self.assertIsInstance(statement, Update)
            self.assertEqual(
                statement.table.fullname,
                MemberOrmModel.__table__.fullname,
            )
            self.assertEqual(session.flush_count, 0)

        with self.subTest(operation="save-version-conflict"):
            session = AsyncSessionSpy(ResultStub(rowcount=0))
            with self.assertRaises(MemberVersionConflictError):
                await _repository(session).save(member, 3)

        for session in (
            AsyncSessionSpy(),
            AsyncSessionSpy(),
        ):
            self.assertEqual(session.commit_count, 0)
            self.assertEqual(session.rollback_count, 0)
            self.assertEqual(session.close_count, 0)

    async def test_repository_translates_sqlalchemy_failures(self):
        member = _member()

        with self.subTest(operation="result-consumption"):
            failure = MultipleResultsFound(
                "sensitive driver/result details"
            )
            session = AsyncSessionSpy(
                ResultStub(scalar_failure=failure)
            )
            with self.assertRaises(MemberRepositoryError) as caught:
                await _repository(session).get_by_id(VALID_MEMBER_ID)
            self.assertIs(type(caught.exception), MemberRepositoryError)
            self.assertEqual(
                str(caught.exception),
                "member persistence operation failed",
            )
            self.assertIs(caught.exception.__cause__, failure)
            self.assertNotIn("sensitive", str(caught.exception))

        with self.subTest(operation="member-no-unique-conflict"):
            driver_failure = PostgreSqlFailureSentinel(
                sqlstate="23505",
                constraint_name="uq_member_member_no",
            )
            adapted_failure = PostgreSqlFailureSentinel(sqlstate="23505")
            adapted_failure.__cause__ = driver_failure
            failure = IntegrityError(
                "INSERT INTO identity.member",
                {"member_no": "sensitive"},
                adapted_failure,
            )
            session = AsyncSessionSpy(flush_failure=failure)
            with self.assertRaises(MemberUniquenessConflictError) as caught:
                await _repository(session).add(member)
            self.assertEqual(
                str(caught.exception), "member uniqueness conflict"
            )
            self.assertIs(caught.exception.__cause__, failure)
            self.assertNotIn("sensitive", str(caught.exception))

        with self.subTest(operation="unknown-integrity-constraint"):
            failure = IntegrityError(
                "INSERT INTO identity.member",
                {"member_no": "sensitive"},
                PostgreSqlFailureSentinel(
                    sqlstate="23505",
                    constraint_name="some_other_constraint",
                ),
            )
            session = AsyncSessionSpy(flush_failure=failure)
            with self.assertRaises(MemberRepositoryError) as caught:
                await _repository(session).add(member)
            self.assertIs(type(caught.exception), MemberRepositoryError)
            self.assertEqual(
                str(caught.exception),
                "member persistence operation failed",
            )
            self.assertIs(caught.exception.__cause__, failure)
            self.assertNotIn("sensitive", str(caught.exception))

        unavailable_failures = (
            OperationalError(
                "SELECT 1", {}, PostgreSqlFailureSentinel()
            ),
            InterfaceError(
                "SELECT 1", {}, PostgreSqlFailureSentinel()
            ),
            SqlAlchemyTimeoutError("connection pool timed out"),
            DisconnectionError("connection was disconnected"),
            DBAPIError(
                "SELECT 1",
                {},
                PostgreSqlFailureSentinel(),
                connection_invalidated=True,
            ),
        )
        for failure in unavailable_failures:
            with self.subTest(
                operation="persistence-unavailable",
                failure_type=type(failure),
            ):
                session = AsyncSessionSpy(execute_failure=failure)
                with self.assertRaises(
                    MemberPersistenceUnavailableError
                ) as caught:
                    await _repository(session).get_by_id(VALID_MEMBER_ID)
                self.assertEqual(
                    str(caught.exception),
                    "member persistence is unavailable",
                )
                self.assertIs(caught.exception.__cause__, failure)
                self.assertNotIn("SELECT", str(caught.exception))
