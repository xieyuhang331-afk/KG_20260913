import asyncio
import importlib
import inspect
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, TimeoutError as SqlAlchemyTimeoutError
from sqlalchemy.types import TypeDecorator

from app.modules.member.application.member_no_allocator import (
    AllocationScope,
    AllocationSourceSystem,
    MemberNoAllocation,
    MemberNoAllocationCommitOutcomeUnknownError,
    MemberNoAllocationKey,
    MemberNoAllocationKeyConflictError,
    MemberNoAllocationNotFoundError,
    MemberNoAllocationPortError,
    MemberNoAllocationPortUnavailableError,
    MemberNoAllocationState,
    MemberNoAllocationTransactionError,
    MemberNoAllocationTransactionUnavailableError,
    MemberNoValueConflictError,
)
from app.modules.member.value_objects import MemberNo


LEDGER_MODULE = (
    "app.modules.member.infrastructure.sqlalchemy_member_no_allocation_ledger"
)
UOW_MODULE = (
    "app.modules.member.infrastructure.member_no_allocation_unit_of_work"
)
MODEL_MODULE = "app.modules.member.infrastructure.models"
EXPECTED_RED = "MemberNo allocation SQLAlchemy ledger is not implemented"
ALLOCATION_ID = UUID("01890f3e-7b7d-7cc3-98c8-2f5a12d21234")
REQUEST_REF = UUID("12345678-1234-4234-8234-123456789abc")
NOW = datetime(2026, 8, 6, 8, 0, tzinfo=timezone.utc)
MEMBER_NO = "M0123456789ABCDEFGHJK"


def _load_ledger_module():
    try:
        return importlib.import_module(LEDGER_MODULE)
    except ModuleNotFoundError as exc:
        target_is_missing = (
            exc.name == LEDGER_MODULE
            or LEDGER_MODULE.startswith(f"{exc.name}.")
        )
        if target_is_missing:
            pytest.fail(EXPECTED_RED)
        raise


def _key(source_ref=7):
    return MemberNoAllocationKey(
        allocation_scope=AllocationScope.REGISTRATION_BOOTSTRAP,
        source_system=AllocationSourceSystem.P1_USER,
        source_ref=source_ref,
    )


def _allocation():
    return MemberNoAllocation(
        allocation_id=ALLOCATION_ID,
        key=_key(),
        member_no=MemberNo(MEMBER_NO),
        request_ref=REQUEST_REF,
    )


def _stored_model(**changes):
    values = {
        "allocation_id": ALLOCATION_ID,
        "allocation_scope": "registration_bootstrap",
        "source_system": "p1_user",
        "source_ref": 7,
        "member_no": MEMBER_NO,
        "state": "allocated",
        "request_ref": REQUEST_REF,
        "version": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return SimpleNamespace(**values)


class ResultStub:
    def __init__(self, value=None, failure=None):
        self.value = value
        self.failure = failure

    def scalar_one_or_none(self):
        if self.failure is not None:
            raise self.failure
        return self.value


class LedgerSessionSpy:
    def __init__(self, *, value=None, execute_failure=None, flush_failure=None):
        self.value = value
        self.execute_failure = execute_failure
        self.flush_failure = flush_failure
        self.execute_calls = []
        self.add_calls = []
        self.flush_calls = 0

    async def execute(self, statement):
        self.execute_calls.append(statement)
        if self.execute_failure is not None:
            raise self.execute_failure
        return ResultStub(self.value)

    def add(self, model):
        self.add_calls.append(model)

    async def flush(self):
        self.flush_calls += 1
        if self.flush_failure is not None:
            raise self.flush_failure


class DriverUniqueViolation(RuntimeError):
    def __init__(self, constraint_name):
        super().__init__("sensitive driver details")
        self.sqlstate = "23505"
        self.constraint_name = constraint_name


class ForgedCategoryError(RuntimeError):
    def __init__(self):
        super().__init__("sensitive forged details")
        self.category = "unique_conflict"


class UowSessionSpy:
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


def test_MemberNo分配账本SQLAlchemy尚未实现():
    module = _load_ledger_module()
    assert inspect.isclass(
        getattr(module, "SqlAlchemyMemberNoAllocationLedger", None)
    )


def test_MemberNo分配账本ORM元数据合同():
    model_type = getattr(
        importlib.import_module(MODEL_MODULE),
        "MemberNoAllocationOrmModel",
    )
    table = sa.inspect(model_type).local_table
    columns = table.c

    assert table.schema == "identity"
    assert table.name == "member_no_allocation"
    assert set(columns.keys()) == {
        "allocation_id",
        "allocation_scope",
        "source_system",
        "source_ref",
        "member_no",
        "state",
        "request_ref",
        "version",
        "created_at",
        "updated_at",
    }
    assert len(table.foreign_keys) == 0
    assert len(table.indexes) == 0

    assert columns.allocation_id.primary_key
    for name in columns.keys():
        assert not columns[name].nullable
        assert columns[name].server_default is None
        assert columns[name].onupdate is None
        assert columns[name].server_onupdate is None

    for name in ("allocation_id", "request_ref"):
        uuid_type = columns[name].type
        assert isinstance(uuid_type, TypeDecorator)
        dialect_type = uuid_type.load_dialect_impl(postgresql.dialect())
        assert isinstance(dialect_type, postgresql.UUID)
        assert dialect_type.as_uuid

    assert columns.allocation_scope.type.length == 32
    assert columns.source_system.type.length == 32
    assert columns.member_no.type.length == 64
    assert columns.state.type.length == 16
    assert isinstance(columns.source_ref.type, sa.BigInteger)
    assert isinstance(columns.version.type, sa.BigInteger)
    assert columns.created_at.type.timezone
    assert columns.updated_at.type.timezone

    unique_constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert unique_constraints == {
        "uq_member_no_allocation_source": (
            "allocation_scope",
            "source_system",
            "source_ref",
        ),
        "uq_member_no_allocation_member_no": ("member_no",),
    }
    assert set(unique_constraints).isdisjoint({None})

    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    assert check_names == {
        "ck_member_no_allocation_scope_registration_bootstrap",
        "ck_member_no_allocation_source_system_p1_user",
        "ck_member_no_allocation_source_ref_positive",
        "ck_member_no_allocation_member_no_format",
        "ck_member_no_allocation_state_allocated",
        "ck_member_no_allocation_version_one",
        "ck_member_no_allocation_timestamps_immutable",
    }


def test_MemberNo分配账本查询新增与不可变映射合同():
    async def scenario():
        ledger_type = getattr(
            _load_ledger_module(), "SqlAlchemyMemberNoAllocationLedger"
        )
        read_session = LedgerSessionSpy(value=_stored_model())
        restored = await ledger_type(read_session, lambda: NOW).get_by_key(
            _key()
        )

        assert type(restored) is MemberNoAllocation
        assert restored == _allocation()
        assert len(read_session.execute_calls) == 1
        assert not isinstance(read_session.execute_calls[0], str)
        assert read_session.add_calls == []
        assert read_session.flush_calls == 0

        missing_session = LedgerSessionSpy(value=None)
        with pytest.raises(
            MemberNoAllocationNotFoundError,
            match="^member number allocation was not found$",
        ):
            await ledger_type(missing_session, lambda: NOW).get_by_key(_key())

        write_session = LedgerSessionSpy()
        await ledger_type(write_session, lambda: NOW).add(_allocation())
        assert write_session.flush_calls == 1
        assert len(write_session.add_calls) == 1
        model = write_session.add_calls[0]
        assert model.allocation_id == ALLOCATION_ID
        assert model.allocation_scope == "registration_bootstrap"
        assert model.source_system == "p1_user"
        assert model.source_ref == 7
        assert model.member_no == MEMBER_NO
        assert model.state == "allocated"
        assert model.request_ref == REQUEST_REF
        assert model.version == 1
        assert model.created_at is NOW
        assert model.updated_at is NOW
        for forbidden in ("update", "delete", "recycle", "release", "save"):
            assert not hasattr(ledger_type, forbidden)

    asyncio.run(scenario())


def test_MemberNo分配账本异常分类严格且失败关闭():
    async def scenario():
        ledger_type = getattr(
            _load_ledger_module(), "SqlAlchemyMemberNoAllocationLedger"
        )
        cases = (
            (
                "uq_member_no_allocation_source",
                MemberNoAllocationKeyConflictError,
            ),
            (
                "uq_member_no_allocation_member_no",
                MemberNoValueConflictError,
            ),
        )
        for constraint_name, expected_error in cases:
            driver = DriverUniqueViolation(constraint_name)
            failure = IntegrityError("insert", {}, driver)
            session = LedgerSessionSpy(flush_failure=failure)
            with pytest.raises(expected_error) as caught:
                await ledger_type(session, lambda: NOW).add(_allocation())
            assert caught.value.__cause__ is failure
            assert "sensitive" not in str(caught.value)

        unknown_constraint = IntegrityError(
            "insert", {}, DriverUniqueViolation("other_constraint")
        )
        for failure in (unknown_constraint, ForgedCategoryError()):
            session = LedgerSessionSpy(flush_failure=failure)
            with pytest.raises(MemberNoAllocationPortError) as caught:
                await ledger_type(session, lambda: NOW).add(_allocation())
            assert type(caught.value) is MemberNoAllocationPortError
            assert str(caught.value) == (
                "member number allocation persistence operation failed"
            )
            assert caught.value.__cause__ is failure
            assert "sensitive" not in str(caught.value)

        unavailable = SqlAlchemyTimeoutError("sensitive connection details")
        session = LedgerSessionSpy(execute_failure=unavailable)
        with pytest.raises(
            MemberNoAllocationPortUnavailableError,
            match="^member number allocation persistence is unavailable$",
        ) as caught:
            await ledger_type(session, lambda: NOW).get_by_key(_key())
        assert caught.value.__cause__ is unavailable

        invalid_clock_session = LedgerSessionSpy()
        with pytest.raises(
            MemberNoAllocationPortError,
            match="^member number allocation persistence operation failed$",
        ):
            await ledger_type(
                invalid_clock_session,
                lambda: NOW.replace(tzinfo=None),
            ).add(_allocation())
        assert invalid_clock_session.add_calls == []
        assert invalid_clock_session.flush_calls == 0

        for invalid_model in (
            _stored_model(version=2),
            _stored_model(state="released"),
            _stored_model(member_no="M001"),
            _stored_model(updated_at=NOW.replace(hour=9)),
            _stored_model(allocation_id=REQUEST_REF),
        ):
            session = LedgerSessionSpy(value=invalid_model)
            with pytest.raises(
                MemberNoAllocationPortError,
                match="^stored member number allocation is invalid$",
            ):
                await ledger_type(session, lambda: NOW).get_by_key(_key())

    asyncio.run(scenario())


def test_MemberNo分配账本专用UoW生命周期与CommitUnknown合同():
    async def scenario():
        uow_type = getattr(
            importlib.import_module(UOW_MODULE),
            "SqlAlchemyMemberNoAllocationUnitOfWork",
        )

        session = UowSessionSpy()
        ledger = object()
        uow = uow_type(lambda: session, lambda received: ledger)
        async with uow as entered:
            assert entered is uow
            assert uow.allocations is ledger
            await uow.commit()
        assert session.calls == {
            "begin": 1,
            "commit": 1,
            "rollback": 0,
            "close": 1,
        }
        with pytest.raises(
            MemberNoAllocationTransactionError,
            match="^member number allocation transaction state is invalid$",
        ):
            await uow.commit()

        rollback_session = UowSessionSpy()
        async with uow_type(
            lambda: rollback_session, lambda received: object()
        ):
            pass
        assert rollback_session.calls == {
            "begin": 1,
            "commit": 0,
            "rollback": 1,
            "close": 1,
        }

        timeout = SqlAlchemyTimeoutError("commit timed out")
        uncertain_session = UowSessionSpy(commit_failure=timeout)
        with pytest.raises(
            MemberNoAllocationCommitOutcomeUnknownError,
            match="^member number allocation commit outcome is unknown$",
        ) as caught:
            async with uow_type(
                lambda: uncertain_session, lambda received: object()
            ) as uncertain_uow:
                await uncertain_uow.commit()
        assert caught.value.__cause__ is timeout
        assert uncertain_session.calls == {
            "begin": 1,
            "commit": 1,
            "rollback": 1,
            "close": 1,
        }

        begin_timeout = SqlAlchemyTimeoutError("connection details")
        begin_session = UowSessionSpy(begin_failure=begin_timeout)
        with pytest.raises(
            MemberNoAllocationTransactionUnavailableError,
            match="^member number allocation transaction is unavailable$",
        ):
            async with uow_type(
                lambda: begin_session, lambda received: object()
            ):
                pass
        assert begin_session.calls["rollback"] == 1
        assert begin_session.calls["close"] == 1

        primary = RuntimeError("primary application failure")
        cleanup_session = UowSessionSpy(
            rollback_failure=RuntimeError("rollback details"),
            close_failure=RuntimeError("close details"),
        )
        with pytest.raises(RuntimeError) as caught:
            async with uow_type(
                lambda: cleanup_session, lambda received: object()
            ):
                raise primary
        assert caught.value is primary

    asyncio.run(scenario())


def test_MemberNo分配账本R2A架构与数据库隔离合同():
    ledger_module = _load_ledger_module()
    uow_module = importlib.import_module(UOW_MODULE)
    ledger_type = getattr(ledger_module, "SqlAlchemyMemberNoAllocationLedger")
    uow_type = getattr(uow_module, "SqlAlchemyMemberNoAllocationUnitOfWork")

    assert inspect.iscoroutinefunction(ledger_type.get_by_key)
    assert inspect.iscoroutinefunction(ledger_type.add)
    assert inspect.iscoroutinefunction(uow_type.__aenter__)
    assert inspect.iscoroutinefunction(uow_type.__aexit__)
    assert inspect.iscoroutinefunction(uow_type.commit)

    root = Path(__file__).resolve().parents[1]
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            root
            / "app/modules/member/infrastructure/sqlalchemy_member_no_allocation_ledger.py",
            root
            / "app/modules/member/infrastructure/member_no_allocation_unit_of_work.py",
        )
    ).lower()
    for forbidden in (
        "create_engine",
        "create_async_engine",
        "create_all",
        "alembic",
        "memberrepository",
        "sqlalchemymemberrepository",
        "on conflict",
        "upsert",
    ):
        assert forbidden not in sources
