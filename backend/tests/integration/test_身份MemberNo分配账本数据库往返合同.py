import asyncio
import hashlib
import os
from datetime import datetime, timezone
from urllib.parse import urlparse
from uuid import UUID

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.uuid_generator import Uuid7Generator
from app.modules.member.application.member_no_allocator import (
    AllocateRegistrationMemberNoCommand,
    AllocationScope,
    AllocationSourceSystem,
    MemberNoAllocation,
    MemberNoAllocationKey,
    MemberNoAllocationPortError,
    RegistrationMemberNoAllocator,
)
from app.modules.member.infrastructure.member_no_allocation_unit_of_work import (
    SqlAlchemyMemberNoAllocationUnitOfWork,
)
from app.modules.member.infrastructure.sqlalchemy_member_no_allocation_ledger import (
    SqlAlchemyMemberNoAllocationLedger,
)
from app.modules.member.value_objects import MemberNo
from tests.integration.conftest import (
    _get_application_database_url,
    _get_readonly_database_url,
    _get_test_database_target,
    _get_test_database_url,
    _to_asyncpg_dsn,
)
from tests.integration.database_safety import validate_database_sentinel


pytestmark = pytest.mark.integration

EXPECTED_RED = (
    "application role lacks approved identity.member_no_allocation privileges"
)
_FIXED_TIME = datetime(2026, 8, 6, 13, 30, tzinfo=timezone.utc)


class _TrackingAsyncSession(AsyncSession):
    lifecycle: list[tuple[int, str]] = []

    def begin(self):
        self.lifecycle.append((id(self), "begin"))
        return super().begin()

    async def commit(self) -> None:
        self.lifecycle.append((id(self), "commit"))
        await super().commit()

    async def rollback(self) -> None:
        self.lifecycle.append((id(self), "rollback"))
        await super().rollback()

    async def close(self) -> None:
        self.lifecycle.append((id(self), "close"))
        await super().close()


def _same_database_endpoint(*database_urls: str) -> bool:
    endpoints = {
        (
            urlparse(database_url).hostname,
            urlparse(database_url).port,
            urlparse(database_url).path,
        )
        for database_url in database_urls
    }
    return len(endpoints) == 1


async def _read_role_context(database_url: str) -> dict[str, object]:
    connection = await asyncpg.connect(_to_asyncpg_dsn(database_url))
    try:
        row = await connection.fetchrow(
            "SELECT current_database() AS database_name, "
            "current_user AS role_name, "
            "shobj_description(oid, 'pg_database') AS sentinel "
            "FROM pg_database WHERE datname = current_database()"
        )
        return dict(row)
    finally:
        await connection.close()


async def _read_privileges(database_url: str) -> dict[str, object]:
    connection = await asyncpg.connect(_to_asyncpg_dsn(database_url))
    try:
        row = await connection.fetchrow(
            "WITH relation AS ("
            "SELECT c.oid, pg_get_userbyid(c.relowner) AS owner "
            "FROM pg_class AS c "
            "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'identity' "
            "AND c.relname = 'member_no_allocation'"
            "), alembic AS ("
            "SELECT c.oid FROM pg_class AS c "
            "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname = 'alembic_version'"
            ") SELECT "
            "current_user AS role_name, relation.owner AS table_owner, "
            "has_schema_privilege(current_user, 'identity', 'USAGE') "
            "AS schema_usage, "
            "has_schema_privilege(current_user, 'identity', 'CREATE') "
            "AS schema_create, "
            "has_table_privilege(current_user, relation.oid, 'SELECT') "
            "AS allocation_select, "
            "has_table_privilege(current_user, relation.oid, 'INSERT') "
            "AS allocation_insert, "
            "has_table_privilege(current_user, relation.oid, 'UPDATE') "
            "AS allocation_update, "
            "has_table_privilege(current_user, relation.oid, 'DELETE') "
            "AS allocation_delete, "
            "has_table_privilege(current_user, relation.oid, 'TRUNCATE') "
            "AS allocation_truncate, "
            "has_table_privilege(current_user, relation.oid, 'REFERENCES') "
            "AS allocation_references, "
            "has_table_privilege(current_user, relation.oid, 'TRIGGER') "
            "AS allocation_trigger, "
            "has_table_privilege(current_user, alembic.oid, "
            "'INSERT,UPDATE,DELETE') AS alembic_dml "
            "FROM relation CROSS JOIN alembic"
        )
        if row is None:
            raise AssertionError("identity.member_no_allocation is missing")
        return dict(row)
    finally:
        await connection.close()


def _assert_role_context(
    context: dict[str, object],
    *,
    expected_database: str,
    expected_role: str,
    target,
) -> None:
    assert context["database_name"] == expected_database
    assert context["role_name"] == expected_role
    validate_database_sentinel(context["sentinel"], target)


def _assert_application_privileges(privileges: dict[str, object]) -> None:
    approved = (
        privileges["schema_usage"] is True
        and privileges["allocation_select"] is True
        and privileges["allocation_insert"] is True
    )
    if not approved:
        pytest.fail(EXPECTED_RED, pytrace=False)
    assert privileges["schema_create"] is False
    assert privileges["allocation_update"] is False
    assert privileges["allocation_delete"] is False
    assert privileges["allocation_truncate"] is False
    assert privileges["allocation_references"] is False
    assert privileges["allocation_trigger"] is False
    assert privileges["alembic_dml"] is False


def _assert_readonly_privileges(privileges: dict[str, object]) -> None:
    assert privileges["schema_usage"] is True
    assert privileges["allocation_select"] is True
    assert privileges["schema_create"] is False
    assert privileges["allocation_insert"] is False
    assert privileges["allocation_update"] is False
    assert privileges["allocation_delete"] is False
    assert privileges["allocation_truncate"] is False
    assert privileges["allocation_references"] is False
    assert privileges["allocation_trigger"] is False
    assert privileges["alembic_dml"] is False


def _assert_migration_privileges(
    privileges: dict[str, object], migration_role: str
) -> None:
    assert privileges["role_name"] == migration_role
    assert privileges["table_owner"] == migration_role
    assert privileges["schema_usage"] is True
    assert privileges["schema_create"] is True
    assert privileges["allocation_select"] is True
    assert privileges["allocation_insert"] is True
    assert privileges["allocation_update"] is True
    assert privileges["allocation_delete"] is True


def _uow_factory(session_factory):
    return lambda: SqlAlchemyMemberNoAllocationUnitOfWork(
        session_factory,
        lambda session: SqlAlchemyMemberNoAllocationLedger(
            session, lambda: _FIXED_TIME
        ),
    )


def _allocator(session_factory, random_bits):
    return RegistrationMemberNoAllocator(
        unit_of_work_factory=_uow_factory(session_factory),
        uuid_generator=Uuid7Generator(),
        random_bits=random_bits,
    )


def _command(source_ref: int, request_ref: str):
    return AllocateRegistrationMemberNoCommand(
        user_ref=source_ref,
        request_ref=UUID(request_ref),
    )


async def _expect_insufficient_privilege(
    database_url: str, statement: str
) -> None:
    connection = await asyncpg.connect(_to_asyncpg_dsn(database_url))
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.execute(statement)
    finally:
        await connection.close()


async def _fetch_rows(
    readonly_database_url: str, source_refs: tuple[int, ...]
) -> list[dict[str, object]]:
    connection = await asyncpg.connect(_to_asyncpg_dsn(readonly_database_url))
    try:
        rows = await connection.fetch(
            "SELECT allocation_id, allocation_scope, source_system, source_ref, "
            "member_no, state, request_ref, version, created_at, updated_at, "
            "pg_typeof(allocation_id)::text AS allocation_id_type, "
            "pg_typeof(request_ref)::text AS request_ref_type "
            "FROM identity.member_no_allocation "
            "WHERE source_ref = ANY($1::bigint[]) ORDER BY source_ref",
            list(source_refs),
        )
        return [dict(row) for row in rows]
    finally:
        await connection.close()


async def _cleanup_rows(
    migration_database_url: str,
    source_refs: tuple[int, ...],
    *,
    target,
    migration_role: str,
) -> None:
    connection = await asyncpg.connect(_to_asyncpg_dsn(migration_database_url))
    try:
        context = await connection.fetchrow(
            "SELECT current_database() AS database_name, "
            "current_user AS role_name, "
            "shobj_description(oid, 'pg_database') AS sentinel "
            "FROM pg_database WHERE datname = current_database()"
        )
        _assert_role_context(
            dict(context),
            expected_database=target.database_name,
            expected_role=migration_role,
            target=target,
        )
        await connection.execute(
            "DELETE FROM identity.member_no_allocation "
            "WHERE source_ref = ANY($1::bigint[])",
            list(source_refs),
        )
        remaining = await connection.fetchval(
            "SELECT COUNT(*) FROM identity.member_no_allocation "
            "WHERE source_ref = ANY($1::bigint[])",
            list(source_refs),
        )
        assert remaining == 0
    finally:
        await connection.close()


async def _exercise_r2c(
    *,
    application_database_url: str,
    migration_database_url: str,
    readonly_database_url: str,
    target,
) -> None:
    application_role = os.environ["KG_TEST_APPLICATION_ROLE"]
    migration_role = os.environ["KG_TEST_MIGRATION_ROLE"]
    readonly_role = os.environ["KG_TEST_READONLY_ROLE"]
    roles = (application_role, migration_role, readonly_role)
    assert len(set(roles)) == 3
    assert "postgres" not in roles

    contexts = await asyncio.gather(
        _read_role_context(application_database_url),
        _read_role_context(migration_database_url),
        _read_role_context(readonly_database_url),
    )
    for context, role in zip(contexts, roles, strict=True):
        _assert_role_context(
            context,
            expected_database=target.database_name,
            expected_role=role,
            target=target,
        )

    app_privileges, migration_privileges, readonly_privileges = (
        await asyncio.gather(
            _read_privileges(application_database_url),
            _read_privileges(migration_database_url),
            _read_privileges(readonly_database_url),
        )
    )
    _assert_application_privileges(app_privileges)
    _assert_migration_privileges(migration_privileges, migration_role)
    _assert_readonly_privileges(readonly_privileges)

    seed = int.from_bytes(
        hashlib.sha256(target.run_id.encode("ascii")).digest()[:4], "big"
    )
    source_refs = tuple(seed + offset + 1 for offset in range(7))
    _TrackingAsyncSession.lifecycle.clear()
    engine = create_async_engine(application_database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine,
        class_=_TrackingAsyncSession,
        expire_on_commit=False,
    )
    body_error = None
    cleanup_error = None

    try:
        concurrent_command = _command(
            source_refs[0], "01890f5d-6d12-7cc4-98c4-dc0c0c073980"
        )
        first_allocator = _allocator(session_factory, lambda _bits: 1)
        second_allocator = _allocator(session_factory, lambda _bits: 2)
        first, second = await asyncio.gather(
            first_allocator.allocate(concurrent_command),
            second_allocator.allocate(concurrent_command),
        )
        assert first.allocation_id == second.allocation_id
        assert first.member_no == second.member_no
        assert sorted((first.replayed, second.replayed)) == [False, True]

        replay = await _allocator(session_factory, lambda _bits: 30).allocate(
            _command(
                source_refs[0],
                "01890f5d-6d12-7cc4-98c4-dc0c0c073981",
            )
        )
        assert replay.allocation_id == first.allocation_id
        assert replay.member_no == first.member_no
        assert replay.replayed is True

        different = await _allocator(session_factory, lambda _bits: 5).allocate(
            _command(
                source_refs[1],
                "01890f5d-6d12-7cc4-98c4-dc0c0c073982",
            )
        )
        assert different.member_no != first.member_no
        assert different.replayed is False

        seeded = await _allocator(session_factory, lambda _bits: 7).allocate(
            _command(
                source_refs[2],
                "01890f5d-6d12-7cc4-98c4-dc0c0c073983",
            )
        )
        values = iter((7, 8))
        retried = await _allocator(
            session_factory, lambda _bits: next(values)
        ).allocate(
            _command(
                source_refs[3],
                "01890f5d-6d12-7cc4-98c4-dc0c0c073984",
            )
        )
        assert retried.member_no != seeded.member_no
        assert retried.member_no.value.endswith("8")

        key = MemberNoAllocationKey(
            allocation_scope=AllocationScope.REGISTRATION_BOOTSTRAP,
            source_system=AllocationSourceSystem.P1_USER,
            source_ref=source_refs[4],
        )
        duplicate_primary_key = MemberNoAllocation(
            allocation_id=first.allocation_id,
            key=key,
            member_no=MemberNo("M11111111111111111111"),
            request_ref=UUID("01890f5d-6d12-7cc4-98c4-dc0c0c073985"),
        )
        async with _uow_factory(session_factory)() as unit_of_work:
            with pytest.raises(MemberNoAllocationPortError) as raised:
                await unit_of_work.allocations.add(duplicate_primary_key)
            assert type(raised.value) is MemberNoAllocationPortError

        rolled_back = MemberNoAllocation(
            allocation_id=Uuid7Generator().generate(),
            key=MemberNoAllocationKey(
                allocation_scope=AllocationScope.REGISTRATION_BOOTSTRAP,
                source_system=AllocationSourceSystem.P1_USER,
                source_ref=source_refs[5],
            ),
            member_no=MemberNo("M22222222222222222222"),
            request_ref=UUID("01890f5d-6d12-7cc4-98c4-dc0c0c073986"),
        )
        async with _uow_factory(session_factory)() as unit_of_work:
            await unit_of_work.allocations.add(rolled_back)

        def cancel_random(_bits: int) -> int:
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await _allocator(session_factory, cancel_random).allocate(
                _command(
                    source_refs[6],
                    "01890f5d-6d12-7cc4-98c4-dc0c0c073987",
                )
            )

        rows = await _fetch_rows(readonly_database_url, source_refs)
        assert len(rows) == 4
        assert {row["source_ref"] for row in rows} == set(source_refs[:4])
        for row in rows:
            assert isinstance(row["allocation_id"], UUID)
            assert isinstance(row["request_ref"], UUID)
            assert row["allocation_id"].version == 7
            assert row["allocation_scope"] == "registration_bootstrap"
            assert row["source_system"] == "p1_user"
            assert row["member_no"].startswith("M")
            assert len(row["member_no"]) == 21
            assert row["state"] == "allocated"
            assert row["version"] == 1
            assert row["created_at"] == _FIXED_TIME
            assert row["updated_at"] == _FIXED_TIME
            assert row["allocation_id_type"] == "uuid"
            assert row["request_ref_type"] == "uuid"

        async with _uow_factory(session_factory)() as unit_of_work:
            restored = await unit_of_work.allocations.get_by_key(
                MemberNoAllocationKey(
                    allocation_scope=AllocationScope.REGISTRATION_BOOTSTRAP,
                    source_system=AllocationSourceSystem.P1_USER,
                    source_ref=source_refs[0],
                )
            )
        assert restored.allocation_id == first.allocation_id
        assert restored.member_no == first.member_no
        assert type(restored.allocation_id) is UUID
        assert type(restored.request_ref) is UUID
        assert not hasattr(
            SqlAlchemyMemberNoAllocationLedger, "update"
        )
        assert not hasattr(
            SqlAlchemyMemberNoAllocationLedger, "delete"
        )

        allocation_id = first.allocation_id
        await _expect_insufficient_privilege(
            application_database_url,
            "UPDATE identity.member_no_allocation SET state = 'allocated' "
            f"WHERE allocation_id = '{allocation_id}'",
        )
        await _expect_insufficient_privilege(
            application_database_url,
            "DELETE FROM identity.member_no_allocation "
            f"WHERE allocation_id = '{allocation_id}'",
        )
        await _expect_insufficient_privilege(
            application_database_url,
            "CREATE TABLE identity.r2c_forbidden(id bigint)",
        )
        await _expect_insufficient_privilege(
            readonly_database_url,
            "INSERT INTO identity.member_no_allocation DEFAULT VALUES",
        )
        await _expect_insufficient_privilege(
            readonly_database_url,
            "UPDATE identity.member_no_allocation SET state = 'allocated'",
        )
        await _expect_insufficient_privilege(
            readonly_database_url,
            "DELETE FROM identity.member_no_allocation",
        )

        after_denied_writes = await _fetch_rows(
            readonly_database_url, source_refs
        )
        assert after_denied_writes == rows

        lifecycle_by_session: dict[int, list[str]] = {}
        for session_id, event in _TrackingAsyncSession.lifecycle:
            lifecycle_by_session.setdefault(session_id, []).append(event)
        assert lifecycle_by_session
        assert all(events[0] == "begin" for events in lifecycle_by_session.values())
        assert all(events[-1] == "close" for events in lifecycle_by_session.values())
        assert any("commit" in events for events in lifecycle_by_session.values())
        assert any("rollback" in events for events in lifecycle_by_session.values())
    except BaseException as exc:
        body_error = exc
    finally:
        try:
            await _cleanup_rows(
                migration_database_url,
                source_refs,
                target=target,
                migration_role=migration_role,
            )
            assert await _fetch_rows(readonly_database_url, source_refs) == []
        except BaseException as exc:
            cleanup_error = exc
        await engine.dispose()

    if body_error is not None:
        if cleanup_error is not None:
            body_error.add_note(
                "member number allocation cleanup also failed: "
                f"{type(cleanup_error).__name__}"
            )
        raise body_error.with_traceback(body_error.__traceback__)
    if cleanup_error is not None:
        raise cleanup_error


def test_MemberNo分配账本数据库往返合同(pg_database):
    assert os.getenv("KG_RUN_PG_INTEGRATION") == "1"
    assert os.getenv("KG_ALLOW_DESTRUCTIVE_TEST_DATABASE") == "1"
    assert os.getenv("KG_TEST_ENVIRONMENT") in {
        "ci_ephemeral",
        "local_ephemeral",
    }
    assert os.getenv("KG_TEST_ROLE_SEPARATION") == "1"
    assert "KG_TEST_LIFECYCLE_DATABASE_URL" not in os.environ
    assert "KG_TEST_LIFECYCLE_PASSWORD" not in os.environ

    application_database_url = _get_application_database_url()
    migration_database_url = _get_test_database_url()
    readonly_database_url = _get_readonly_database_url()
    _, target = _get_test_database_target()
    assert target.database_name == f"kg_it_{target.run_id}"
    assert _same_database_endpoint(
        application_database_url,
        migration_database_url,
        readonly_database_url,
    )

    asyncio.run(
        _exercise_r2c(
            application_database_url=application_database_url,
            migration_database_url=migration_database_url,
            readonly_database_url=readonly_database_url,
            target=target,
        )
    )
