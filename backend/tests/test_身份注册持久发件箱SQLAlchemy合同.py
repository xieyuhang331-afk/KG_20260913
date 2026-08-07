import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from importlib import import_module
import json
import traceback
from types import SimpleNamespace
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, TimeoutError as SqlAlchemyTimeoutError
from sqlalchemy.types import TypeDecorator

from app.modules.auth.registration_outbox import (
    P1RegistrationEligibilityDecision,
    P1RegistrationOutboxRecord,
    P1VerificationDecision,
    P1VerificationTransitionCommitOutcomeUnknown,
    P1VerificationTransitionSnapshot,
    P1VerificationTransitionUnavailable,
)


EXPECTED_RED = (
    "P1 durable registration outbox SQLAlchemy persistence "
    "is not implemented"
)
USER_REF = 73
FACTS_VERSION = 11
DECIDED_AT = datetime(2026, 8, 7, 4, 0, tzinfo=timezone.utc)
VERIFICATION_REF = UUID("01890f3e-7b7d-7cc3-88c8-2f5a12d20001")
EVENT_ID = UUID("01890f3e-7b7d-7cc3-88c8-2f5a12d20002")
ELIGIBILITY_REF = UUID("01890f3e-7b7d-7cc3-88c8-2f5a12d20003")
OUTBOX_ID = UUID("01890f3e-7b7d-7cc3-88c8-2f5a12d20004")
CLASSIFICATION_REF = UUID("01890f3e-7b7d-7cc3-98c8-2f5a12d20005")
AUTHORITY_KEY = "a" * 64
SEMANTIC_KEY = (
    "identity.registration.verification_verified:v1:"
    f"p1_user:{USER_REF}:authority:{AUTHORITY_KEY}"
)


def _persistence_api():
    try:
        models = import_module(
            "app.modules.auth.registration_outbox_models"
        )
        persistence = import_module(
            "app.modules.auth.registration_outbox_repository"
        )
        return (
            models.RegistrationVerifiedOutboxOrmModel,
            persistence.SqlAlchemyP1VerificationTransitionRepository,
            persistence.SqlAlchemyP1VerificationTransitionUnitOfWork,
        )
    except (ImportError, AttributeError):
        pytest.fail(EXPECTED_RED)


def _verification():
    return P1VerificationDecision(
        decision_ref=VERIFICATION_REF,
        user_ref=USER_REF,
        facts_version=FACTS_VERSION,
        verification_epoch=4,
        outcome="verified",
        evidence_digest="verification-evidence-digest-v4",
        actor_type="platform_reviewer",
        actor_ref="reviewer-ref-17",
        decided_at=DECIDED_AT,
        authority_decision_key=AUTHORITY_KEY,
        registration_event_id=EVENT_ID,
        supersedes_ref=None,
    )


def _eligibility():
    return P1RegistrationEligibilityDecision(
        decision_ref=ELIGIBILITY_REF,
        user_ref=USER_REF,
        facts_version=FACTS_VERSION,
        verification_decision_ref=VERIFICATION_REF,
        classification_decision_ref=CLASSIFICATION_REF,
        policy_version="registration-eligibility-v1",
        facts_digest="b" * 64,
        p1_projection_digest="c" * 64,
        decision="eligible",
        reason="eligible",
        decided_at=DECIDED_AT,
    )


def _outbox():
    return P1RegistrationOutboxRecord(
        outbox_record_id=OUTBOX_ID,
        event_id=EVENT_ID,
        semantic_idempotency_key=SEMANTIC_KEY,
        event_type="identity.registration.verification_verified",
        event_schema_version=1,
        source_system="P1_USER",
        source_ref=USER_REF,
        verification_decision_ref=VERIFICATION_REF,
        authority_decision_key=AUTHORITY_KEY,
        facts_version=FACTS_VERSION,
        occurred_at=DECIDED_AT,
    )


def _stored_verification(**changes):
    values = {
        **asdict(_verification()),
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _stored_classification(**changes):
    values = {
        "decision_ref": CLASSIFICATION_REF,
        "user_ref": USER_REF,
        "facts_version": FACTS_VERSION,
        "classification_version": 4,
        "account_class": "natural_person",
        "decision_basis_code": "p1-authority",
        "supersedes_ref": None,
        "decided_at": DECIDED_AT,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _stored_eligibility(**changes):
    values = {**asdict(_eligibility())}
    values.update(changes)
    return SimpleNamespace(**values)


def _stored_outbox(**changes):
    outbox = _outbox()
    canonical = json.dumps(
        {
            "authority_decision_key": outbox.authority_decision_key,
            "event_id": str(outbox.event_id),
            "event_schema_version": outbox.event_schema_version,
            "event_type": outbox.event_type,
            "facts_version": outbox.facts_version,
            "occurred_at": outbox.occurred_at.isoformat(),
            "source_ref": outbox.source_ref,
            "source_system": outbox.source_system,
            "trace_ref": None,
            "verification_decision_ref": str(
                outbox.verification_decision_ref
            ),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    values = {
        **asdict(outbox),
        "payload_digest": hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest(),
        "trace_ref": None,
        "status": "pending",
        "available_at": DECIDED_AT,
        "attempt_count": 0,
        "lease_owner": None,
        "locked_until": None,
        "lease_generation": 0,
        "last_error_category": None,
        "last_error_code": None,
        "last_error_digest": None,
        "delivered_at": None,
        "created_at": DECIDED_AT,
        "updated_at": DECIDED_AT,
    }
    values.update(changes)
    return SimpleNamespace(**values)


class ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def one_or_none(self):
        return self._value


class RepositorySessionSpy:
    def __init__(self, *results, execute_error=None, add_error=None):
        self.results = list(results)
        self.execute_error = execute_error
        self.add_error = add_error
        self.execute_calls = []
        self.add_calls = []

    async def execute(self, statement):
        self.execute_calls.append(statement)
        if self.execute_error is not None:
            raise self.execute_error
        return ScalarResult(self.results.pop(0))

    def add(self, model):
        if self.add_error is not None:
            raise self.add_error
        self.add_calls.append(model)


class UnitOfWorkSessionSpy:
    def __init__(
        self,
        *,
        begin_error=None,
        commit_error=None,
        rollback_error=None,
        close_error=None,
    ):
        self.begin_error = begin_error
        self.commit_error = commit_error
        self.rollback_error = rollback_error
        self.close_error = close_error
        self.calls = {
            "begin": 0,
            "commit": 0,
            "rollback": 0,
            "close": 0,
        }

    async def begin(self):
        self.calls["begin"] += 1
        if self.begin_error is not None:
            raise self.begin_error

    async def commit(self):
        self.calls["commit"] += 1
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self):
        self.calls["rollback"] += 1
        if self.rollback_error is not None:
            raise self.rollback_error

    async def close(self):
        self.calls["close"] += 1
        if self.close_error is not None:
            raise self.close_error


class DriverUniqueViolation(RuntimeError):
    def __init__(self, constraint_name):
        super().__init__("secret sql params phone=13800138000")
        self.sqlstate = "23505"
        self.constraint_name = constraint_name


def test_注册持久发件箱SQLAlchemy适配器尚未实现():
    orm_model, repository_type, unit_of_work_type = _persistence_api()

    assert orm_model is not None
    assert repository_type is not None
    assert unit_of_work_type is not None


def test_OutboxORM字段约束索引与UUID边界():
    orm_model, _, _ = _persistence_api()
    table = sa.inspect(orm_model).local_table
    columns = table.c

    assert table.schema == "public"
    assert table.name == "registration_verified_outbox"
    assert set(columns.keys()) == {
        "outbox_record_id",
        "event_id",
        "semantic_idempotency_key",
        "event_type",
        "event_schema_version",
        "source_system",
        "source_ref",
        "verification_decision_ref",
        "authority_decision_key",
        "facts_version",
        "occurred_at",
        "trace_ref",
        "payload_digest",
        "status",
        "available_at",
        "attempt_count",
        "lease_owner",
        "locked_until",
        "lease_generation",
        "last_error_category",
        "last_error_code",
        "last_error_digest",
        "delivered_at",
        "created_at",
        "updated_at",
    }
    assert columns.outbox_record_id.primary_key
    for name in (
        "outbox_record_id",
        "event_id",
        "verification_decision_ref",
        "trace_ref",
    ):
        uuid_type = columns[name].type
        assert isinstance(uuid_type, TypeDecorator)
        assert isinstance(
            uuid_type.load_dialect_impl(postgresql.dialect()),
            postgresql.UUID,
        )

    unique_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert unique_names == {
        "uq_registration_verified_outbox_event",
        "uq_registration_verified_outbox_semantic_key",
        "uq_registration_verified_outbox_verification",
        "uq_registration_verified_outbox_authority",
        "uq_registration_verified_outbox_source_decision",
    }
    foreign_keys = list(table.foreign_key_constraints)
    assert len(foreign_keys) == 1
    assert foreign_keys[0].name == (
        "fk_registration_verified_outbox_verification_identity"
    )
    assert tuple(column.name for column in foreign_keys[0].columns) == (
        "verification_decision_ref",
        "authority_decision_key",
        "event_id",
        "facts_version",
    )
    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    assert check_names == {
        "ck_registration_verified_outbox_event_type",
        "ck_registration_verified_outbox_schema_version",
        "ck_registration_verified_outbox_source_system",
        "ck_registration_verified_outbox_source_positive",
        "ck_registration_verified_outbox_facts_positive",
        "ck_registration_verified_outbox_authority_sha256",
        "ck_registration_verified_outbox_payload_sha256",
        "ck_registration_verified_outbox_semantic_key",
        "ck_registration_verified_outbox_status_closed",
        "ck_registration_verified_outbox_attempt_budget",
        "ck_registration_verified_outbox_lease_generation",
        "ck_registration_verified_outbox_lease_shape",
        "ck_registration_verified_outbox_delivered_shape",
        "ck_registration_verified_outbox_error_shape",
        "ck_registration_verified_outbox_error_category",
        "ck_registration_verified_outbox_error_code",
        "ck_registration_verified_outbox_error_digest",
        "ck_registration_verified_outbox_timestamps",
    }
    assert {index.name for index in table.indexes} == {
        "ix_registration_verified_outbox_schedule",
        "ix_registration_verified_outbox_expired_lease",
        "ix_registration_verified_outbox_source_verification",
    }
    for name in (
        "authority_decision_key",
        "payload_digest",
        "last_error_digest",
    ):
        assert columns[name].type.compile(
            dialect=postgresql.dialect()
        ) == "CHAR(64)"


def test_VerificationORM加法字段与复合身份约束():
    import app.modules.auth.eligibility_evidence_models as evidence_models

    table = sa.inspect(
        evidence_models.IdentityVerificationEvidenceOrmModel
    ).local_table
    assert "authority_decision_key" in table.c
    assert "registration_event_id" in table.c
    assert table.c.authority_decision_key.nullable
    assert table.c.registration_event_id.nullable
    assert table.c.authority_decision_key.type.compile(
        dialect=postgresql.dialect()
    ) == "CHAR(64)"
    unique_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert "uq_identity_verification_event_identity" in unique_names
    assert {
        index.name for index in table.indexes if index.unique
    } >= {
        "uq_identity_verification_authority_key_present",
        "uq_identity_verification_registration_event_present",
    }


def test_Repository保存映射不可变Envelope且没有隐式commit():
    _, repository_type, _ = _persistence_api()

    async def scenario():
        session = RepositorySessionSpy()
        repository = repository_type(session)
        await repository.add_verification_decision(_verification())
        await repository.add_eligibility_decision(_eligibility())
        await repository.add_outbox(_outbox())

        assert len(session.add_calls) == 3
        verification, eligibility, outbox = session.add_calls
        assert verification.authority_decision_key == AUTHORITY_KEY
        assert verification.registration_event_id == EVENT_ID
        assert eligibility.verification_decision_ref == VERIFICATION_REF
        assert outbox.event_id == EVENT_ID
        assert outbox.semantic_idempotency_key == SEMANTIC_KEY
        assert outbox.payload_digest is not None
        assert len(outbox.payload_digest) == 64
        assert outbox.status == "pending"
        assert outbox.attempt_count == 0
        assert outbox.lease_generation == 0
        for forbidden in ("update", "delete", "recycle", "claim", "ack"):
            assert not hasattr(repository_type, forbidden)

    asyncio.run(scenario())


def test_Repository按权威身份区分全无与缺Outbox的部分持久化():
    _, repository_type, _ = _persistence_api()

    async def scenario():
        user = SimpleNamespace(
            id=USER_REF,
            role="member",
            status="active",
            verify_status="verified",
            updated_at=DECIDED_AT,
        )
        session = RepositorySessionSpy(
            (_stored_verification(), None),
            _stored_eligibility(),
            _stored_classification(),
            user,
        )
        snapshot = await repository_type(
            session
        ).find_by_authority_decision_key(AUTHORITY_KEY)

        assert type(snapshot) is P1VerificationTransitionSnapshot
        assert type(snapshot.verification) is P1VerificationDecision
        assert snapshot.outbox is None
        identity_probe = str(
            session.execute_calls[0].compile(
                dialect=postgresql.dialect()
            )
        ).upper()
        assert "FULL OUTER JOIN" in identity_probe
        assert len(session.execute_calls) == 4

    asyncio.run(scenario())


def test_Repository拒绝重放摘要或Trace不匹配的OutboxEnvelope():
    _, repository_type, _ = _persistence_api()

    async def load(outbox):
        user = SimpleNamespace(
            id=USER_REF,
            role="member",
            status="active",
            verify_status="verified",
            updated_at=DECIDED_AT,
        )
        return await repository_type(
            RepositorySessionSpy(
                (_stored_verification(), outbox),
                _stored_eligibility(),
                _stored_classification(),
                user,
            )
        ).find_by_authority_decision_key(AUTHORITY_KEY)

    async def scenario():
        corrupted_digest = await load(
            _stored_outbox(payload_digest="0" * 64)
        )
        assert type(corrupted_digest) is P1VerificationTransitionSnapshot
        assert type(corrupted_digest.outbox) is not P1RegistrationOutboxRecord

        unexpected_trace = await load(
            _stored_outbox(trace_ref=EVENT_ID)
        )
        assert type(unexpected_trace) is P1VerificationTransitionSnapshot
        assert type(unexpected_trace.outbox) is not P1RegistrationOutboxRecord

    asyncio.run(scenario())


def test_Repository读取完整五对象并双向映射标准UUID():
    _, repository_type, _ = _persistence_api()

    async def scenario():
        user = SimpleNamespace(
            id=USER_REF,
            role="member",
            status="active",
            verify_status="verified",
            updated_at=DECIDED_AT,
        )
        session = RepositorySessionSpy(
            (_stored_verification(), _stored_outbox()),
            _stored_eligibility(),
            _stored_classification(),
            user,
        )
        snapshot = await repository_type(
            session
        ).find_by_authority_decision_key(AUTHORITY_KEY)

        assert type(snapshot) is P1VerificationTransitionSnapshot
        assert type(snapshot.verification) is P1VerificationDecision
        assert type(snapshot.eligibility) is (
            P1RegistrationEligibilityDecision
        )
        assert type(snapshot.outbox) is P1RegistrationOutboxRecord
        assert type(snapshot.result.verification_decision_ref) is UUID
        assert type(snapshot.result.registration_event_id) is UUID
        assert snapshot.user_verify_status == "verified"
        assert snapshot.verification.authority_decision_key == AUTHORITY_KEY
        assert snapshot.outbox.status == "pending"
        assert len(session.execute_calls) == 4
        assert all(not isinstance(call, str) for call in session.execute_calls)

        missing = RepositorySessionSpy(None)
        assert (
            await repository_type(
                missing
            ).find_by_authority_decision_key(AUTHORITY_KEY)
            is None
        )

    asyncio.run(scenario())


def test_Repository锁定User并读取CurrentClassification与Verification():
    _, repository_type, _ = _persistence_api()

    async def scenario():
        user = SimpleNamespace(
            id=USER_REF,
            role="member",
            status="active",
            verify_status="pending",
            updated_at=DECIDED_AT,
        )
        session = RepositorySessionSpy(
            user,
            _stored_classification(),
            _stored_verification(),
        )
        repository = repository_type(session)
        restored_user = await repository.get_user_for_update(USER_REF)
        classification = await repository.get_current_classification(
            USER_REF
        )
        verification = await repository.get_current_verification(USER_REF)

        assert restored_user.id == user.id
        assert restored_user.role == user.role
        assert restored_user.status == user.status
        assert restored_user.verify_status == user.verify_status
        assert restored_user.updated_at == user.updated_at
        assert classification.decision_ref == CLASSIFICATION_REF
        assert verification.decision_ref == VERIFICATION_REF
        user_statement = session.execute_calls[0]
        assert user_statement._for_update_arg is not None

    asyncio.run(scenario())


def test_Repository首次写入与重放只读取User五列且首次写入保留锁():
    _, repository_type, _ = _persistence_api()
    approved_columns = (
        "id",
        "role",
        "status",
        "verify_status",
        "updated_at",
    )

    async def scenario():
        locked_user = SimpleNamespace(
            id=USER_REF,
            role="member",
            status="active",
            verify_status="pending",
            updated_at=DECIDED_AT,
        )
        first_write = RepositorySessionSpy(locked_user, None)
        first_repository = repository_type(first_write)
        restored = await first_repository.get_user_for_update(USER_REF)
        restored.verify_status = "verified"
        await first_repository.flush_locked_user_projection()
        first_statement = first_write.execute_calls[0]
        update_statement = first_write.execute_calls[1]

        replay_user = SimpleNamespace(
            id=USER_REF,
            role="member",
            status="active",
            verify_status="verified",
            updated_at=DECIDED_AT,
        )
        replay = RepositorySessionSpy(
            (_stored_verification(), _stored_outbox()),
            _stored_eligibility(),
            _stored_classification(),
            replay_user,
        )
        await repository_type(replay).find_by_authority_decision_key(
            AUTHORITY_KEY
        )
        replay_statement = replay.execute_calls[3]

        assert tuple(
            column.name for column in first_statement.selected_columns
        ) == approved_columns
        assert tuple(
            column.name for column in replay_statement.selected_columns
        ) == approved_columns
        assert first_statement._for_update_arg is not None
        assert replay_statement._for_update_arg is None
        assert {
            column.name for column in update_statement._values
        } == {"verify_status", "updated_at"}
        for statement in (first_statement, replay_statement):
            rendered = str(
                statement.compile(dialect=postgresql.dialect())
            ).lower()
            assert "phone" not in rendered
            assert "password" not in rendered

    asyncio.run(scenario())


def test_Repository未知异常与Cancellation安全边界():
    _, repository_type, _ = _persistence_api()

    async def scenario():
        secret = RuntimeError(
            "SELECT secret params phone=13800138000 password=hunter2"
        )
        with pytest.raises(P1VerificationTransitionUnavailable) as caught:
            await repository_type(
                RepositorySessionSpy(execute_error=secret)
            ).find_by_authority_decision_key(AUTHORITY_KEY)
        rendered = "".join(
            traceback.format_exception(
                type(caught.value),
                caught.value,
                caught.value.__traceback__,
            )
        )
        assert str(caught.value) == (
            "P1 verification transition persistence is unavailable"
        )
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        assert "13800138000" not in rendered
        assert "hunter2" not in rendered

        cancellation = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError) as cancelled:
            await repository_type(
                RepositorySessionSpy(execute_error=cancellation)
            ).find_by_authority_decision_key(AUTHORITY_KEY)
        assert cancelled.value is cancellation

    asyncio.run(scenario())


def test_专用UoW生命周期Rollback与CommitOutcomeUnknown():
    _, repository_type, unit_of_work_type = _persistence_api()

    async def scenario():
        session = UnitOfWorkSessionSpy()
        async with unit_of_work_type(lambda: session) as unit_of_work:
            assert isinstance(unit_of_work.repository, repository_type)
            await unit_of_work.commit()
        assert session.calls == {
            "begin": 1,
            "commit": 1,
            "rollback": 0,
            "close": 1,
        }

        rollback_session = UnitOfWorkSessionSpy()
        async with unit_of_work_type(lambda: rollback_session):
            pass
        assert rollback_session.calls == {
            "begin": 1,
            "commit": 0,
            "rollback": 1,
            "close": 1,
        }

        timeout = SqlAlchemyTimeoutError(
            "secret database target and password"
        )
        uncertain_session = UnitOfWorkSessionSpy(commit_error=timeout)
        with pytest.raises(
            P1VerificationTransitionCommitOutcomeUnknown
        ) as caught:
            async with unit_of_work_type(
                lambda: uncertain_session
            ) as uncertain:
                await uncertain.commit()
        assert str(caught.value) == (
            "P1 verification transition commit outcome is unknown"
        )
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        assert uncertain_session.calls == {
            "begin": 1,
            "commit": 1,
            "rollback": 1,
            "close": 1,
        }

        driver = DriverUniqueViolation(
            "uq_registration_verified_outbox_authority"
        )
        conflict = IntegrityError("insert", {}, driver)
        conflict_session = UnitOfWorkSessionSpy(commit_error=conflict)
        with pytest.raises(P1VerificationTransitionCommitOutcomeUnknown):
            async with unit_of_work_type(
                lambda: conflict_session
            ) as conflict_uow:
                await conflict_uow.commit()

        unknown = IntegrityError(
            "insert",
            {},
            DriverUniqueViolation("unknown_constraint"),
        )
        unknown_session = UnitOfWorkSessionSpy(commit_error=unknown)
        with pytest.raises(P1VerificationTransitionUnavailable) as generic:
            async with unit_of_work_type(
                lambda: unknown_session
            ) as unknown_uow:
                await unknown_uow.commit()
        assert generic.value.__cause__ is None
        assert generic.value.__context__ is None

    asyncio.run(scenario())


def test_专用UoW直接满足Writer所需端口且共享Repository():
    _, _, unit_of_work_type = _persistence_api()
    required = {
        "find_by_authority_decision_key",
        "get_user_for_update",
        "get_current_classification",
        "get_current_verification",
        "add_verification_decision",
        "add_eligibility_decision",
        "add_outbox",
        "commit",
    }
    assert required <= set(dir(unit_of_work_type))


def test_专用UoW在Begin和Body取消时清理且原样传播Cancellation():
    _, _, unit_of_work_type = _persistence_api()

    async def scenario():
        begin_cancel = asyncio.CancelledError()
        begin_session = UnitOfWorkSessionSpy(begin_error=begin_cancel)
        with pytest.raises(asyncio.CancelledError) as caught_begin:
            async with unit_of_work_type(lambda: begin_session):
                pass
        assert caught_begin.value is begin_cancel
        assert begin_session.calls == {
            "begin": 1,
            "commit": 0,
            "rollback": 1,
            "close": 1,
        }

        body_cancel = asyncio.CancelledError()
        cleanup_cancel = asyncio.CancelledError()
        body_session = UnitOfWorkSessionSpy(rollback_error=cleanup_cancel)
        with pytest.raises(asyncio.CancelledError) as caught_body:
            async with unit_of_work_type(lambda: body_session):
                raise body_cancel
        assert caught_body.value is body_cancel
        assert body_session.calls == {
            "begin": 1,
            "commit": 0,
            "rollback": 1,
            "close": 1,
        }

    asyncio.run(scenario())
