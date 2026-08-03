from datetime import datetime, timezone
from unittest import IsolatedAsyncioTestCase
from uuid import UUID

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
from app.modules.member.repository import MemberVersionConflictError


VALID_MEMBER_ID = UUID("01890f5d-6d12-7cc4-98c4-dc0c0c07398f")
NOW = datetime(2026, 8, 3, 6, 0, tzinfo=timezone.utc)


class ResultStub:
    def __init__(self, *, value=None, rowcount=1):
        self.value = value
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return self.value


class AsyncSessionSpy:
    def __init__(self, *results):
        self._results = list(results)
        self.execute_calls = []
        self.add_calls = []
        self.flush_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0

    async def execute(self, statement):
        self.execute_calls.append(statement)
        return self._results.pop(0)

    def add(self, value):
        self.add_calls.append(value)

    async def flush(self):
        self.flush_count += 1


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
