import asyncio
import os
from datetime import datetime, timezone
from urllib.parse import urlparse
from uuid import RFC_4122, UUID

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.composition.identity_persistence import (
    IdentityPersistenceComposition,
    create_identity_session_factory,
)
from app.modules.member.entities import Member
from app.modules.member.repository import MemberVersionConflictError
from app.modules.member.value_objects import CreationSource, MemberNo
from tests.integration.conftest import (
    _get_application_database_url,
    _get_readonly_database_url,
    _get_test_database_target,
    _get_test_database_url,
    _to_asyncpg_dsn,
)
from tests.integration.database_safety import validate_database_sentinel


pytestmark = pytest.mark.integration

EXPECTED_RED = "application role lacks approved identity.member privileges"
MEMBER_ID = UUID("01890f5d-6d12-7cc4-98c4-dc0c0c07398f")


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


async def _read_privileges(database_url: str) -> dict[str, bool]:
    connection = await asyncpg.connect(_to_asyncpg_dsn(database_url))
    try:
        row = await connection.fetchrow(
            "SELECT "
            "has_schema_privilege(current_user, 'identity', 'USAGE') "
            "AS schema_usage, "
            "has_schema_privilege(current_user, 'identity', 'CREATE') "
            "AS schema_create, "
            "has_table_privilege(current_user, 'identity.member', 'SELECT') "
            "AS member_select, "
            "has_table_privilege(current_user, 'identity.member', 'INSERT') "
            "AS member_insert, "
            "has_table_privilege(current_user, 'identity.member', 'UPDATE') "
            "AS member_update, "
            "has_table_privilege(current_user, 'identity.member', 'DELETE') "
            "AS member_delete, "
            "has_table_privilege(current_user, 'identity.member', 'TRUNCATE') "
            "AS member_truncate, "
            "has_table_privilege(current_user, 'identity.member', 'REFERENCES') "
            "AS member_references, "
            "has_table_privilege(current_user, 'identity.member', 'TRIGGER') "
            "AS member_trigger, "
            "has_table_privilege(current_user, 'public.alembic_version', "
            "'INSERT,UPDATE,DELETE') AS alembic_dml"
        )
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
    if context["database_name"] != expected_database:
        raise AssertionError("role connection targets the wrong disposable database")
    if context["role_name"] != expected_role:
        raise AssertionError("role connection does not use the approved test role")
    validate_database_sentinel(context["sentinel"], target)


def _assert_application_privileges(privileges: dict[str, bool]) -> None:
    approved = (
        privileges["schema_usage"]
        and privileges["member_select"]
        and privileges["member_insert"]
        and privileges["member_update"]
    )
    if not approved:
        pytest.fail(EXPECTED_RED, pytrace=False)
    assert privileges["schema_create"] is False
    assert privileges["member_delete"] is False
    assert privileges["member_truncate"] is False
    assert privileges["member_references"] is False
    assert privileges["member_trigger"] is False
    assert privileges["alembic_dml"] is False


def _assert_readonly_privileges(privileges: dict[str, bool]) -> None:
    assert privileges["schema_usage"] is True
    assert privileges["member_select"] is True
    assert privileges["schema_create"] is False
    assert privileges["member_insert"] is False
    assert privileges["member_update"] is False
    assert privileges["member_delete"] is False
    assert privileges["member_truncate"] is False
    assert privileges["member_references"] is False
    assert privileges["member_trigger"] is False
    assert privileges["alembic_dml"] is False


async def _fetch_member_row(
    readonly_database_url: str, member_id: UUID
) -> dict[str, object]:
    connection = await asyncpg.connect(_to_asyncpg_dsn(readonly_database_url))
    try:
        row = await connection.fetchrow(
            "SELECT member_id, member_no, creation_source, status, version, "
            "created_at, updated_at, pg_typeof(member_id)::text AS member_id_type "
            "FROM identity.member WHERE member_id = $1",
            member_id,
        )
        if row is None:
            raise AssertionError("member row is missing")
        return dict(row)
    finally:
        await connection.close()


async def _assert_member_absent(
    readonly_database_url: str, member_id: UUID
) -> None:
    connection = await asyncpg.connect(_to_asyncpg_dsn(readonly_database_url))
    try:
        count = await connection.fetchval(
            "SELECT COUNT(*) FROM identity.member WHERE member_id = $1",
            member_id,
        )
        assert count == 0
    finally:
        await connection.close()


async def _delete_member_with_migration_role(
    migration_database_url: str,
    *,
    member_id: UUID,
    expected_database: str,
    expected_role: str,
    target,
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
            expected_database=expected_database,
            expected_role=expected_role,
            target=target,
        )
        result = await connection.execute(
            "DELETE FROM identity.member WHERE member_id = $1", member_id
        )
        assert result == "DELETE 1"
    finally:
        await connection.close()


async def _exercise_repository_round_trip(
    *,
    application_database_url: str,
    migration_database_url: str,
    readonly_database_url: str,
    target,
    run_id: str,
) -> None:
    application_role = os.environ["KG_TEST_APPLICATION_ROLE"]
    migration_role = os.environ["KG_TEST_MIGRATION_ROLE"]
    readonly_role = os.environ["KG_TEST_READONLY_ROLE"]
    roles = (application_role, migration_role, readonly_role)
    assert len(set(roles)) == 3
    assert "postgres" not in roles

    application_context, migration_context, readonly_context = await asyncio.gather(
        _read_role_context(application_database_url),
        _read_role_context(migration_database_url),
        _read_role_context(readonly_database_url),
    )
    _assert_role_context(
        application_context,
        expected_database=target.database_name,
        expected_role=application_role,
        target=target,
    )
    _assert_role_context(
        migration_context,
        expected_database=target.database_name,
        expected_role=migration_role,
        target=target,
    )
    _assert_role_context(
        readonly_context,
        expected_database=target.database_name,
        expected_role=readonly_role,
        target=target,
    )

    application_privileges, readonly_privileges = await asyncio.gather(
        _read_privileges(application_database_url),
        _read_privileges(readonly_database_url),
    )
    _assert_application_privileges(application_privileges)
    _assert_readonly_privileges(readonly_privileges)

    run_fragment = run_id[-20:]
    initial_member_no = f"P2-RT-{run_fragment}-A"
    updated_member_no = f"P2-RT-{run_fragment}-B"
    conflict_member_no = f"P2-RT-{run_fragment}-C"
    assert max(map(len, (initial_member_no, updated_member_no, conflict_member_no))) <= 64

    created_at = datetime(2026, 8, 5, 2, 30, tzinfo=timezone.utc)
    updated_at = datetime(2026, 8, 5, 2, 31, tzinfo=timezone.utc)
    clock_values = iter((created_at, updated_at))
    engine = create_async_engine(application_database_url, poolclass=NullPool)
    composition = IdentityPersistenceComposition(
        session_factory=create_identity_session_factory(engine),
        clock=lambda: next(clock_values),
    )
    member_committed = False
    body_error = None
    cleanup_error = None

    try:
        member = Member.create(
            member_id=MEMBER_ID,
            member_no=MemberNo(initial_member_no),
            creation_source=CreationSource.REGISTRATION,
        )
        async with composition.unit_of_work() as uow:
            await uow.members.add(member)
            await uow.commit()
        member_committed = True

        async with composition.unit_of_work() as uow:
            assert await uow.members.get_by_id(MEMBER_ID) == member
            assert await uow.members.get_by_member_no(MemberNo(initial_member_no)) == member
            assert await uow.members.is_member_no_available(MemberNo(initial_member_no)) is False

        initial_row = await _fetch_member_row(readonly_database_url, MEMBER_ID)
        stored_member_id = initial_row["member_id"]
        assert type(stored_member_id) is UUID
        assert stored_member_id == MEMBER_ID
        assert stored_member_id.bytes == MEMBER_ID.bytes
        assert stored_member_id.int == MEMBER_ID.int
        assert stored_member_id.version == 7
        assert stored_member_id.variant == RFC_4122
        assert initial_row["member_id_type"] == "uuid"
        assert initial_row["member_no"] == initial_member_no
        assert initial_row["creation_source"] == "registration"
        assert initial_row["status"] == "created"
        assert initial_row["version"] == 1
        assert initial_row["created_at"] == created_at
        assert initial_row["updated_at"] == created_at

        updated_member = Member(
            member_id=MEMBER_ID,
            member_no=MemberNo(updated_member_no),
            creation_source=CreationSource.REGISTRATION,
            status=member.status,
        )
        async with composition.unit_of_work() as uow:
            await uow.members.save(updated_member, expected_version=1)
            await uow.commit()

        async with composition.unit_of_work() as uow:
            assert await uow.members.get_by_id(MEMBER_ID) == updated_member
            assert (
                await uow.members.get_by_member_no(MemberNo(updated_member_no))
                == updated_member
            )
            assert await uow.members.is_member_no_available(MemberNo(initial_member_no)) is True
            assert await uow.members.is_member_no_available(MemberNo(updated_member_no)) is False

        updated_row = await _fetch_member_row(readonly_database_url, MEMBER_ID)
        assert updated_row["member_id"] == MEMBER_ID
        assert updated_row["member_no"] == updated_member_no
        assert updated_row["creation_source"] == "registration"
        assert updated_row["status"] == "created"
        assert updated_row["version"] == 2
        assert updated_row["created_at"] == created_at
        assert updated_row["updated_at"] == updated_at
        assert updated_row["updated_at"] > updated_row["created_at"]

        conflicting_member = Member(
            member_id=MEMBER_ID,
            member_no=MemberNo(conflict_member_no),
            creation_source=CreationSource.REGISTRATION,
            status=member.status,
        )
        async with composition.unit_of_work() as uow:
            with pytest.raises(
                MemberVersionConflictError, match="^member version conflict$"
            ):
                await uow.members.save(conflicting_member, expected_version=1)

        conflict_row = await _fetch_member_row(readonly_database_url, MEMBER_ID)
        assert conflict_row["member_no"] == updated_member_no
        assert conflict_row["version"] == 2
        assert conflict_row["updated_at"] == updated_at
    except BaseException as exc:
        body_error = exc
    finally:
        if member_committed:
            try:
                await _delete_member_with_migration_role(
                    migration_database_url,
                    member_id=MEMBER_ID,
                    expected_database=target.database_name,
                    expected_role=migration_role,
                    target=target,
                )
                await _assert_member_absent(readonly_database_url, MEMBER_ID)
            except BaseException as exc:
                cleanup_error = exc
        await engine.dispose()

    if body_error is not None:
        if cleanup_error is not None:
            body_error.add_note(
                "member row cleanup also failed: "
                f"{type(cleanup_error).__name__}"
            )
        raise body_error.with_traceback(body_error.__traceback__)
    if cleanup_error is not None:
        raise cleanup_error


def test_member_repository_round_trip_in_disposable_postgresql(pg_database):
    assert os.getenv("KG_RUN_PG_INTEGRATION") == "1"
    assert os.getenv("KG_ALLOW_DESTRUCTIVE_TEST_DATABASE") == "1"
    assert os.getenv("KG_TEST_ENVIRONMENT") == "ci_ephemeral"
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
        _exercise_repository_round_trip(
            application_database_url=application_database_url,
            migration_database_url=migration_database_url,
            readonly_database_url=readonly_database_url,
            target=target,
            run_id=target.run_id,
        )
    )
