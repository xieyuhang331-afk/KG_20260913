import asyncio
from datetime import datetime, timedelta, timezone
import traceback
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import TimeoutError as SqlAlchemyTimeoutError

from app.modules.auth.registration_outbox_worker import (
    RegistrationOutboxCommitOutcomeUnknown,
    RegistrationOutboxUnavailable,
)
from app.modules.auth.registration_outbox_worker_repository import (
    SqlAlchemyRegistrationOutboxWorkerRepository,
    SqlAlchemyRegistrationOutboxWorkerUnitOfWork,
)


NOW = datetime(2026, 8, 7, 8, 0, tzinfo=timezone.utc)
EVENT_ID = UUID("01890f3e-7b7d-7cc3-88c8-2f5a12d29101")
OUTBOX_ID = UUID("01890f3e-7b7d-7cc3-88c8-2f5a12d29102")
VERIFICATION_ID = UUID("01890f3e-7b7d-7cc3-88c8-2f5a12d29103")


def _row(**changes):
    values = {
        "outbox_record_id": OUTBOX_ID,
        "event_id": EVENT_ID,
        "semantic_idempotency_key": "semantic-key",
        "event_type": "identity.registration.verification_verified",
        "event_schema_version": 1,
        "source_system": "P1_USER",
        "source_ref": 73,
        "verification_decision_ref": VERIFICATION_ID,
        "authority_decision_key": "a" * 64,
        "facts_version": 11,
        "occurred_at": NOW,
        "trace_ref": None,
        "payload_digest": "b" * 64,
        "status": "pending",
        "available_at": NOW,
        "attempt_count": 0,
        "lease_owner": None,
        "locked_until": None,
        "lease_generation": 0,
        "last_error_category": None,
        "last_error_code": None,
        "last_error_digest": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return SimpleNamespace(**values)


class ScalarRows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class SessionSpy:
    def __init__(self, *, rows=(), error=None):
        self.rows = list(rows)
        self.error = error
        self.statements = []
        self.calls = []

    async def scalar(self, statement):
        self.statements.append(statement)
        if self.error:
            raise self.error
        return NOW

    async def scalars(self, statement):
        self.statements.append(statement)
        if self.error:
            raise self.error
        return ScalarRows(self.rows)

    async def execute(self, statement):
        self.statements.append(statement)
        if self.error:
            raise self.error
        return SimpleNamespace(rowcount=1)

    async def flush(self):
        self.calls.append("flush")

    async def begin(self):
        self.calls.append("begin")

    async def commit(self):
        self.calls.append("commit")
        if self.error:
            raise self.error

    async def rollback(self):
        self.calls.append("rollback")

    async def close(self):
        self.calls.append("close")


def test_Claim使用数据库时间SKIPLOCKED固定顺序与租约世代():
    row = _row()
    session = SessionSpy(rows=(row,))
    items = asyncio.run(
        SqlAlchemyRegistrationOutboxWorkerRepository(session).claim(
            lease_owner="worker-1", limit=50, lease_seconds=90
        )
    )
    assert len(items) == 1
    assert items[0].attempt_count == 1
    assert items[0].lease_generation == 1
    assert row.status == "processing"
    statement = session.statements[1]
    assert statement._for_update_arg is not None
    assert statement._for_update_arg.skip_locked
    rendered = str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in rendered.upper()
    assert "attempt_count" in rendered
    assert session.calls == ["flush"]


def test_过期第八次Processing只确认接管且不增加Attempt():
    row = _row(
        status="processing",
        attempt_count=8,
        lease_owner="dead-worker",
        locked_until=NOW - timedelta(seconds=1),
        lease_generation=7,
    )
    item = asyncio.run(
        SqlAlchemyRegistrationOutboxWorkerRepository(
            SessionSpy(rows=(row,))
        ).claim(lease_owner="worker-2", limit=1, lease_seconds=90)
    )[0]
    assert item.confirmation_only
    assert item.attempt_count == 8
    assert item.lease_generation == 8


def test_过期未耗尽Processing接管消耗下一次Attempt预算():
    row = _row(
        status="processing",
        attempt_count=7,
        lease_owner="crashed-worker",
        locked_until=NOW - timedelta(seconds=1),
        lease_generation=7,
    )
    item = asyncio.run(
        SqlAlchemyRegistrationOutboxWorkerRepository(
            SessionSpy(rows=(row,))
        ).claim(lease_owner="worker-2", limit=1, lease_seconds=90)
    )[0]
    assert not item.confirmation_only
    assert item.attempt_count == 8
    assert item.lease_generation == 8


def test_所有技术Transition使用OwnerGenerationStatusFencing():
    session = SessionSpy()
    changed = asyncio.run(
        SqlAlchemyRegistrationOutboxWorkerRepository(session).transition(
            event_id=EVENT_ID,
            lease_owner="worker-1",
            lease_generation=3,
            target_status="delivered",
            error_category=None,
            error_code=None,
            error_digest=None,
            retry_delay_seconds=None,
        )
    )
    assert changed
    statement = session.statements[1]
    rendered = str(statement.compile(dialect=postgresql.dialect()))
    for token in ("event_id", "lease_owner", "lease_generation", "status"):
        assert token in rendered
    assert "locked_until" in rendered
    assert "payload_digest" not in statement._values
    assert "semantic_idempotency_key" not in statement._values


def test_专用UoW生命周期RollbackClose与CommitUnknown安全链():
    async def successful():
        session = SessionSpy()
        async with SqlAlchemyRegistrationOutboxWorkerUnitOfWork(
            lambda: session
        ) as unit_of_work:
            await unit_of_work.commit()
        assert session.calls == ["begin", "commit", "close"]

    asyncio.run(successful())

    secret = SqlAlchemyTimeoutError(
        "postgresql://secret password phone=13800138000"
    )

    async def uncertain():
        session = SessionSpy(error=secret)
        with pytest.raises(
            RegistrationOutboxCommitOutcomeUnknown
        ) as caught:
            async with SqlAlchemyRegistrationOutboxWorkerUnitOfWork(
                lambda: session
            ) as unit_of_work:
                await unit_of_work.commit()
        rendered = "".join(
            traceback.format_exception(
                type(caught.value),
                caught.value,
                caught.value.__traceback__,
            )
        )
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        assert "13800138000" not in rendered
        assert "postgresql://" not in rendered

    asyncio.run(uncertain())


def test_Repository未知异常安全隔离且Cancellation原样传播():
    secret = RuntimeError("SELECT phone=13800138000 password=hunter2")

    async def unavailable():
        with pytest.raises(RegistrationOutboxUnavailable) as caught:
            await SqlAlchemyRegistrationOutboxWorkerRepository(
                SessionSpy(error=secret)
            ).claim(lease_owner="worker-1", limit=1, lease_seconds=90)
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        assert "13800138000" not in str(caught.value)

    asyncio.run(unavailable())

    cancellation = asyncio.CancelledError()

    class CancelSession(SessionSpy):
        async def scalar(self, statement):
            raise cancellation

    async def cancelled():
        with pytest.raises(asyncio.CancelledError) as caught:
            await SqlAlchemyRegistrationOutboxWorkerRepository(
                CancelSession()
            ).claim(lease_owner="worker-1", limit=1, lease_seconds=90)
        assert caught.value is cancellation

    asyncio.run(cancelled())


def test_Reconciliation其他Policy检查按同一用户与FactsVersion相关():
    class ReconciliationSession(SessionSpy):
        async def execute(self, statement):
            self.statements.append(statement)
            return ScalarRows(())

    session = ReconciliationSession()
    assert (
        asyncio.run(
            SqlAlchemyRegistrationOutboxWorkerRepository(
                session
            ).find_reconciliation_candidates(10)
        )
        == ()
    )
    rendered = str(
        session.statements[0].compile(dialect=postgresql.dialect())
    )
    assert (
        "registration_eligibility_decision_1.user_ref = "
        "public.registration_eligibility_decision.user_ref"
    ) in rendered
    assert (
        "registration_eligibility_decision_1.facts_version = "
        "public.registration_eligibility_decision.facts_version"
    ) in rendered
    assert "user_ref = registration_eligibility_decision_1.user_ref" not in rendered
