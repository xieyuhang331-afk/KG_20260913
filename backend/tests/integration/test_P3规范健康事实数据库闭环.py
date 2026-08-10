from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import importlib
import json
import os

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.modules.health_fact.domain import (
    CanonicalHealthFactDraft,
    HealthFactCommitOutcomeUnknown,
    HealthFactCorrectionConflict,
    HealthFactDigestKeyring,
)
from app.modules.health_fact.unit_of_work import (
    CanonicalHealthFactWriter,
    SqlAlchemyHealthFactUnitOfWork,
)


pytestmark = pytest.mark.integration


def _register(real_db_client, suffix: str) -> int:
    response = real_db_client.post(
        "/api/v1/users/register",
        json={"phone": f"13800139{suffix}", "password": "Secret12345"},
    )
    assert response.status_code == 200
    return response.json()["data"]["id"]


def _keyring(current: str) -> HealthFactDigestKeyring:
    encoded = json.loads(os.environ["KG_HEALTH_FACT_DIGEST_KEYRING_JSON"])
    return HealthFactDigestKeyring.from_base64(
        current_key_id=current,
        encoded_keys=encoded,
    )


def _draft(*, user_id: int, event: str, source: str = "fictional-source", **changes):
    values = dict(
        subject_user_id=user_id,
        indicator_code="systolic_bp",
        numeric_value=Decimal("120.50"),
        unit="mmHg",
        measured_at=datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc),
        source_type="APP",
        source_identity=source,
        producer_event_key=event,
        created_by=user_id,
    )
    values.update(changes)
    return CanonicalHealthFactDraft(**values)


async def _writer(current: str):
    engine = create_async_engine(
        os.environ["KG_TEST_HEALTH_FACT_WRITER_DATABASE_URL"],
        poolclass=NullPool,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    class TestProducerAuthority:
        async def authorize(self, draft):
            return draft

    writer = CanonicalHealthFactWriter(
        uow_factory=lambda: SqlAlchemyHealthFactUnitOfWork(session_factory),
        readonly_uow_factory=lambda: SqlAlchemyHealthFactUnitOfWork(
            session_factory
        ),
        keyring=_keyring(current),
        producer_authority=TestProducerAuthority(),
    )
    return engine, writer


def test_FactWriter权限矩阵与独立身份(
    health_fact_writer_database, application_database, readonly_database
):
    assert health_fact_writer_database.fetch_value("SELECT current_user") == os.environ[
        "KG_TEST_HEALTH_FACT_WRITER_ROLE"
    ]
    checks = health_fact_writer_database.fetch_rows(
        "SELECT "
        "has_table_privilege(current_user,'public.canonical_health_fact','SELECT') AS fact_select,"
        "has_table_privilege(current_user,'public.canonical_health_fact','INSERT') AS fact_insert,"
        "has_table_privilege(current_user,'public.canonical_health_fact','UPDATE') AS fact_update,"
        "has_table_privilege(current_user,'public.canonical_health_fact','DELETE') AS fact_delete,"
        "has_table_privilege(current_user,'public.canonical_health_fact','TRUNCATE') AS fact_truncate,"
        "has_table_privilege(current_user,'public.canonical_health_fact','REFERENCES') AS fact_references,"
        "has_table_privilege(current_user,'public.canonical_health_fact','TRIGGER') AS fact_trigger,"
        "has_table_privilege(current_user,'public.operation_log','SELECT') AS audit_select,"
        "has_table_privilege(current_user,'public.operation_log','INSERT') AS audit_insert,"
        "has_table_privilege(current_user,'public.operation_log','UPDATE') AS audit_update,"
        "has_table_privilege(current_user,'public.operation_log','DELETE') AS audit_delete,"
        "has_table_privilege(current_user,'public.operation_log','TRUNCATE') AS audit_truncate,"
        "has_table_privilege(current_user,'public.operation_log','REFERENCES') AS audit_references,"
        "has_table_privilege(current_user,'public.operation_log','TRIGGER') AS audit_trigger,"
        "has_sequence_privilege(current_user,'public.canonical_health_fact_id_seq','USAGE') AS fact_seq_usage,"
        "has_sequence_privilege(current_user,'public.canonical_health_fact_id_seq','SELECT') AS fact_seq_select,"
        "has_sequence_privilege(current_user,'public.canonical_health_fact_id_seq','UPDATE') AS fact_seq_update,"
        "has_sequence_privilege(current_user,'public.operation_log_id_seq','USAGE') AS audit_seq_usage,"
        "has_sequence_privilege(current_user,'public.operation_log_id_seq','SELECT') AS audit_seq_select,"
        "has_sequence_privilege(current_user,'public.operation_log_id_seq','UPDATE') AS audit_seq_update,"
        "has_schema_privilege(current_user,'public','CREATE') AS schema_create,"
        "has_table_privilege(current_user,'public.alembic_version','SELECT') AS alembic_select"
    )[0]
    assert checks == {
        "fact_select": True,
        "fact_insert": True,
        "fact_update": False,
        "fact_delete": False,
        "fact_truncate": False,
        "fact_references": False,
        "fact_trigger": False,
        "audit_select": True,
        "audit_insert": True,
        "audit_update": False,
        "audit_delete": False,
        "audit_truncate": False,
        "audit_references": False,
        "audit_trigger": False,
        "fact_seq_usage": True,
        "fact_seq_select": True,
        "fact_seq_update": False,
        "audit_seq_usage": True,
        "audit_seq_select": True,
        "audit_seq_update": False,
        "schema_create": False,
        "alembic_select": False,
    }
    for database in (application_database, readonly_database):
        for privilege in (
            "SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE",
            "REFERENCES", "TRIGGER",
        ):
            assert database.fetch_value(
                "SELECT has_table_privilege(current_user,"
                f"'public.canonical_health_fact','{privilege}')"
            ) is False
        for privilege in ("USAGE", "SELECT", "UPDATE"):
            assert database.fetch_value(
                "SELECT has_sequence_privilege(current_user,"
                f"'public.canonical_health_fact_id_seq','{privilege}')"
            ) is False


def test_跨HMAC写入epoch并发单赢家稳定重放与审计(
    real_db_client, health_fact_writer_database
):
    user_id = _register(real_db_client, "071")
    key_ids = tuple(sorted(json.loads(
        os.environ["KG_HEALTH_FACT_DIGEST_KEYRING_JSON"]
    )))
    assert len(key_ids) >= 2
    draft = _draft(user_id=user_id, event="rotation-event")

    async def exercise():
        first_engine, first = await _writer(key_ids[0])
        second_engine, second = await _writer(key_ids[1])
        try:
            return await asyncio.gather(first.write(draft), second.write(draft))
        finally:
            await first_engine.dispose()
            await second_engine.dispose()

    results = asyncio.run(exercise())
    assert sorted(result.outcome for result in results) == ["CREATED", "REPLAYED"]
    assert results[0].fact.id == results[1].fact.id
    assert health_fact_writer_database.fetch_value(
        "SELECT COUNT(*) FROM public.canonical_health_fact "
        "WHERE producer_event_key='rotation-event'"
    ) == 1
    assert health_fact_writer_database.fetch_value(
        "SELECT COUNT(*) FROM public.operation_log "
        "WHERE module='health_fact' AND object_type='canonical_health_fact' "
        "AND action='fact_appended'"
    ) == 1


def test_append_only纠错单后继与数据库禁止变更(
    real_db_client, health_fact_writer_database
):
    user_id = _register(real_db_client, "072")
    current = os.environ["KG_HEALTH_FACT_DIGEST_CURRENT_KEY_ID"]

    async def exercise():
        engine, writer = await _writer(current)
        try:
            original = await writer.write(
                _draft(user_id=user_id, event="original-event")
            )
            correction = _draft(
                user_id=user_id,
                event="correction-event",
                numeric_value=Decimal("121.00"),
                supersedes_fact_id=original.fact.id,
                correction_reason_code="MANUAL_CORRECTION",
            )
            corrected = await writer.write(correction)
            with pytest.raises(HealthFactCorrectionConflict):
                await writer.write(
                    _draft(
                        user_id=user_id,
                        event="second-correction-event",
                        numeric_value=Decimal("122.00"),
                        supersedes_fact_id=original.fact.id,
                        correction_reason_code="MANUAL_CORRECTION",
                    )
                )
            return original, corrected
        finally:
            await engine.dispose()

    original, corrected = asyncio.run(exercise())
    assert original.fact.id != corrected.fact.id
    assert health_fact_writer_database.fetch_value(
        f"SELECT COUNT(*) FROM public.canonical_health_fact WHERE supersedes_fact_id={original.fact.id}"
    ) == 1

    async def forbidden_update():
        connection = await asyncpg.connect(
            os.environ["KG_TEST_HEALTH_FACT_WRITER_DATABASE_URL"].replace(
                "postgresql+asyncpg://", "postgresql://", 1
            )
        )
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await connection.execute(
                    "UPDATE public.canonical_health_fact SET numeric_value=0 WHERE id=$1",
                    original.fact.id,
                )
        finally:
            await connection.close()

    asyncio.run(forbidden_update())


def test_真实事务rollback_Cancellation与commit_outcome_unknown双对象确认(
    real_db_client, health_fact_writer_database
):
    user_id = _register(real_db_client, "073")
    current = os.environ["KG_HEALTH_FACT_DIGEST_CURRENT_KEY_ID"]

    class Authority:
        async def authorize(self, draft):
            return draft

    class ObservedWriter(CanonicalHealthFactWriter):
        confirmed = None

        async def _confirm_outcome(self, draft):
            self.confirmed = await super()._confirm_outcome(draft)
            return self.confirmed

    async def exercise():
        engine = create_async_engine(
            os.environ["KG_TEST_HEALTH_FACT_WRITER_DATABASE_URL"],
            poolclass=NullPool,
        )
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        class UnknownAfterCommit(SqlAlchemyHealthFactUnitOfWork):
            async def commit(self):
                await super().commit()
                raise HealthFactCommitOutcomeUnknown(
                    "Health fact commit outcome is unknown"
                )

        class UnknownBeforeCommit(SqlAlchemyHealthFactUnitOfWork):
            async def commit(self):
                raise HealthFactCommitOutcomeUnknown(
                    "Health fact commit outcome is unknown"
                )

        positive = ObservedWriter(
            uow_factory=lambda: UnknownAfterCommit(sessions),
            readonly_uow_factory=lambda: SqlAlchemyHealthFactUnitOfWork(
                sessions
            ),
            keyring=_keyring(current),
            producer_authority=Authority(),
        )
        negative = ObservedWriter(
            uow_factory=lambda: UnknownBeforeCommit(sessions),
            readonly_uow_factory=lambda: SqlAlchemyHealthFactUnitOfWork(
                sessions
            ),
            keyring=_keyring(current),
            producer_authority=Authority(),
        )
        try:
            with pytest.raises(HealthFactCommitOutcomeUnknown):
                await positive.write(
                    _draft(user_id=user_id, event="unknown-committed")
                )
            with pytest.raises(HealthFactCommitOutcomeUnknown):
                await negative.write(
                    _draft(user_id=user_id, event="unknown-rolled-back")
                )

            blocking = await asyncpg.connect(
                os.environ["KG_TEST_MIGRATION_DATABASE_URL"].replace(
                    "postgresql+asyncpg://", "postgresql://", 1
                )
            )
            transaction = blocking.transaction()
            await transaction.start()
            lock_key = 819274113
            await blocking.execute("SELECT pg_advisory_xact_lock($1)", lock_key)
            cancellable = ObservedWriter(
                uow_factory=lambda: SqlAlchemyHealthFactUnitOfWork(sessions),
                readonly_uow_factory=lambda: SqlAlchemyHealthFactUnitOfWork(
                    sessions
                ),
                keyring=_keyring(current),
                producer_authority=Authority(),
            )
            from app.modules.health_fact import unit_of_work as uow_module

            original_lock_key = uow_module.semantic_lock_key
            uow_module.semantic_lock_key = lambda **_kwargs: lock_key
            try:
                task = asyncio.create_task(
                    cancellable.write(
                        _draft(user_id=user_id, event="cancelled-event")
                    )
                )
                await asyncio.sleep(0.1)
                assert not task.done()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                uow_module.semantic_lock_key = original_lock_key
                await transaction.rollback()
                await blocking.close()
        finally:
            await engine.dispose()
        return positive.confirmed, negative.confirmed

    positive, negative = asyncio.run(exercise())
    assert positive is True
    assert negative is False
    assert health_fact_writer_database.fetch_value(
        "SELECT COUNT(*) FROM public.canonical_health_fact "
        "WHERE producer_event_key='unknown-committed'"
    ) == 1
    assert health_fact_writer_database.fetch_value(
        "SELECT COUNT(*) FROM public.operation_log "
        "WHERE module='health_fact' AND action='fact_appended' "
        "AND object_id=(SELECT id FROM public.canonical_health_fact "
        "WHERE producer_event_key='unknown-committed')"
    ) == 1
    for event in ("unknown-rolled-back", "cancelled-event"):
        assert health_fact_writer_database.fetch_value(
            "SELECT COUNT(*) FROM public.canonical_health_fact "
            f"WHERE producer_event_key='{event}'"
        ) == 0


def test_downgrade锁阻断并发Writer且非空事实zero_DDL(
    real_db_client, pg_database, health_fact_writer_database
):
    user_id = _register(real_db_client, "074")
    current = os.environ["KG_HEALTH_FACT_DIGEST_CURRENT_KEY_ID"]

    async def exercise():
        migration = await asyncpg.connect(
            os.environ["KG_TEST_MIGRATION_DATABASE_URL"].replace(
                "postgresql+asyncpg://", "postgresql://", 1
            )
        )
        transaction = migration.transaction()
        await transaction.start()
        await migration.execute(
            "LOCK TABLE public.canonical_health_fact IN ACCESS EXCLUSIVE MODE"
        )
        engine, writer = await _writer(current)
        try:
            task = asyncio.create_task(
                writer.write(_draft(user_id=user_id, event="locked-writer"))
            )
            await asyncio.sleep(0.1)
            assert not task.done()
            await transaction.rollback()
            result = await task
            return result
        finally:
            await migration.close()
            await engine.dispose()

    result = asyncio.run(exercise())
    assert result.outcome == "CREATED"

    migration = importlib.import_module(
        "app.migrations.versions.20260811_0015_p3_canonical_health_fact"
    )

    async def assert_refusal():
        engine = create_async_engine(
            os.environ["KG_TEST_MIGRATION_DATABASE_URL"], poolclass=NullPool
        )
        try:
            async with engine.begin() as connection:
                with pytest.raises(
                    RuntimeError,
                    match="refusing to downgrade: Canonical Health Fact facts exist",
                ):
                    await connection.run_sync(migration._assert_downgrade_safe)
        finally:
            await engine.dispose()

    asyncio.run(assert_refusal())
    assert pg_database.fetch_value(
        "SELECT to_regclass('public.canonical_health_fact') IS NOT NULL"
    ) is True
    assert pg_database.fetch_value(
        "SELECT COUNT(*) FROM pg_indexes WHERE schemaname='public' "
        "AND tablename='canonical_health_fact' AND indexname IN "
        "('idx_canonical_health_fact_subject_indicator_time',"
        "'idx_canonical_health_fact_source_time')"
    ) == 2
