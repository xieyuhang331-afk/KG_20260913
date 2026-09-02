from __future__ import annotations

import asyncio
import hashlib
import os
import re
from contextlib import suppress
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.composition.identity_remediation import (
    inspect_read_only_transaction,
    run_identity_inventory,
)
from app.modules.auth.identity_remediation_application import (
    IdentityRemediationApplicationService,
    RemediationRunSummary,
)
from app.modules.auth.identity_remediation_ledger_repository import (
    IdentityRemediationLedgerRepository,
)
from app.modules.auth.identity_remediation_subject_repository import (
    IdentityRemediationSubjectRepository,
)
from app.modules.auth.identity_submission_crypto import IdentitySubmissionCrypto

_DATABASE_NAME = re.compile(r"^kg_(?:it|mt)_[a-z0-9][a-z0-9_]{5,56}$")
_RUN_ID = re.compile(r"^[a-z0-9][a-z0-9_]{5,56}$")
_ROLE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_WRITER_FUNCTIONS = (
    "identity.a2_identity_remediation_ledger_v1(varchar,uuid,uuid,bigint,varchar,smallint,bigint,varchar,varchar,varchar,bigint,bigint,bigint,bigint,bigint,bigint,bigint,bigint,bigint,varchar,varchar,varchar,varchar,varchar,varchar,varchar,uuid,uuid,timestamp with time zone)",
    "identity.a2_identity_remediation_subject_workset_v1(uuid,bigint,varchar,varchar,varchar)",
    "identity.a2_identity_remediation_h3_material_v1(uuid,uuid,bigint,varchar)",
    "identity.a2_identity_remediation_h3_clear_legacy_v1(uuid,uuid,bigint,varchar,varchar,varchar,varchar)",
)
_CONFIRMATION_FUNCTION = (
    "identity.a2_identity_remediation_confirm_v1(varchar,uuid,uuid,bigint,varchar,smallint,bigint,varchar,varchar,varchar,bigint,bigint,bigint,bigint,bigint,bigint,bigint,bigint,bigint,varchar,varchar,varchar,varchar,varchar,varchar,varchar,uuid,uuid,timestamp with time zone,varchar,bigint,varchar)"
)
_BASE_TABLES = (
    ("public", "user"),
    ("public", "identity_verification_submission"),
    ("public", "identity_verification_decision"),
    ("public", "service_enrollment"),
    ("identity", "identity_subject_claim_registry"),
    ("identity", "user_member_self_link"),
    ("identity", "identity_remediation_batch"),
    ("identity", "identity_remediation_item"),
    ("identity", "identity_remediation_audit"),
    ("identity", "identity_remediation_receipt"),
)


class IdentityRemediationRunnerConfigurationError(RuntimeError):
    pass


def _validated_url(name: str) -> str:
    environment = os.getenv("KG_TEST_ENVIRONMENT", "")
    if environment not in {"local_ephemeral", "ci_ephemeral"}:
        raise IdentityRemediationRunnerConfigurationError(
            "A2_REMEDIATION_EPHEMERAL_ENV_REQUIRED"
        )
    run_id = os.getenv("KG_TEST_RUN_ID", "")
    if not _RUN_ID.fullmatch(run_id):
        raise IdentityRemediationRunnerConfigurationError(
            "A2_REMEDIATION_RUN_ID_REQUIRED"
        )
    value = os.getenv(name, "")
    parsed = urlparse(value)
    if parsed.scheme not in {"postgresql", "postgresql+asyncpg"}:
        raise IdentityRemediationRunnerConfigurationError(
            "A2_REMEDIATION_DATABASE_INVALID"
        )
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.port is None:
        raise IdentityRemediationRunnerConfigurationError(
            "A2_REMEDIATION_DATABASE_NOT_LOCAL"
        )
    database_name = parsed.path.removeprefix("/")
    if not _DATABASE_NAME.fullmatch(database_name) or not database_name.endswith(run_id):
        raise IdentityRemediationRunnerConfigurationError(
            "A2_REMEDIATION_DATABASE_SCOPE_INVALID"
        )
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)
    return value


def _validated_role(name: str) -> str:
    role = os.getenv(name, "")
    if not _ROLE_NAME.fullmatch(role):
        raise IdentityRemediationRunnerConfigurationError(
            "A2_REMEDIATION_DATABASE_ROLE_REQUIRED"
        )
    return role


def _actor_scope(run_id: str) -> str:
    digest = hashlib.sha256(run_id.encode()).hexdigest()[:32]
    return f"a2-remediation-{digest}"


async def _assert_sentinel(connection: AsyncConnection) -> None:
    run_id = os.environ["KG_TEST_RUN_ID"]
    description = await connection.scalar(
        text(
            "SELECT shobj_description(oid, 'pg_database') "
            "FROM pg_database WHERE datname=current_database()"
        )
    )
    if description != f"kg-test-disposable:{run_id}":
        raise IdentityRemediationRunnerConfigurationError(
            "A2_REMEDIATION_SENTINEL_MISMATCH"
        )


async def _has_base_authority(connection: AsyncConnection) -> bool:
    for schema_name, table_name in _BASE_TABLES:
        value = await connection.scalar(
            text(
                "SELECT COALESCE(bool_or("
                "has_table_privilege(current_user, relation.oid, 'SELECT') OR "
                "has_table_privilege(current_user, relation.oid, 'INSERT') OR "
                "has_table_privilege(current_user, relation.oid, 'UPDATE') OR "
                "has_table_privilege(current_user, relation.oid, 'DELETE') OR "
                "has_table_privilege(current_user, relation.oid, 'TRUNCATE')),FALSE) "
                "FROM pg_catalog.pg_class relation "
                "JOIN pg_catalog.pg_namespace namespace ON namespace.oid=relation.relnamespace "
                "WHERE namespace.nspname=:schema AND relation.relname=:table"
            ),
            {"schema": schema_name, "table": table_name},
        )
        if value:
            return True
    return False


async def _assert_role(
    connection: AsyncConnection,
    *,
    expected_role: str,
    functions: tuple[str, ...],
) -> None:
    if str(await connection.scalar(text("SELECT current_user"))) != expected_role:
        raise IdentityRemediationRunnerConfigurationError(
            "A2_REMEDIATION_DATABASE_ROLE_MISMATCH"
        )
    await _assert_sentinel(connection)
    if await _has_base_authority(connection):
        raise IdentityRemediationRunnerConfigurationError(
            "A2_REMEDIATION_BASE_TABLE_AUTHORITY_FORBIDDEN"
        )
    for schema_name in ("public", "identity"):
        if await connection.scalar(
            text("SELECT has_schema_privilege(current_user,:schema,'CREATE')"),
            {"schema": schema_name},
        ):
            raise IdentityRemediationRunnerConfigurationError(
                "A2_REMEDIATION_DDL_AUTHORITY_FORBIDDEN"
            )
    for function in functions:
        if not await connection.scalar(
            text(
                "SELECT has_function_privilege(current_user,to_regprocedure(:function),'EXECUTE')"
            ),
            {"function": function},
        ):
            raise IdentityRemediationRunnerConfigurationError(
                "A2_REMEDIATION_FUNCTION_EXECUTE_REQUIRED"
            )


class _WriterUnitOfWork:
    def __init__(self, engine: AsyncEngine, role: str) -> None:
        self._engine = engine
        self._role = role
        self._connection: AsyncConnection | None = None
        self._transaction = None

    async def __aenter__(self) -> _WriterUnitOfWork:
        try:
            self._connection = await self._engine.connect()
            self._transaction = await self._connection.begin()
            await _assert_role(
                self._connection,
                expected_role=self._role,
                functions=_WRITER_FUNCTIONS,
            )
            self.ledger = IdentityRemediationLedgerRepository(self._connection)
            self.subjects = IdentityRemediationSubjectRepository(self._connection)
            return self
        except BaseException:
            if self._transaction is not None and self._transaction.is_active:
                with suppress(BaseException):
                    await self._transaction.rollback()
            if self._connection is not None:
                with suppress(BaseException):
                    await self._connection.close()
            raise

    async def commit(self) -> None:
        assert self._transaction is not None
        await self._transaction.commit()

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        cancellation_active = isinstance(exc, asyncio.CancelledError)
        cleanup_error: BaseException | None = None
        try:
            if self._transaction is not None and self._transaction.is_active:
                try:
                    await self._transaction.rollback()
                except BaseException as error:
                    cleanup_error = error
        finally:
            if self._connection is not None:
                try:
                    await self._connection.close()
                except BaseException as error:
                    cleanup_error = cleanup_error or error
        if not cancellation_active and cleanup_error is not None:
            raise cleanup_error


class _ConfirmationUnitOfWork:
    def __init__(self, engine: AsyncEngine, role: str) -> None:
        self._engine = engine
        self._role = role
        self._connection: AsyncConnection | None = None
        self._transaction = None

    async def __aenter__(self) -> _ConfirmationUnitOfWork:
        try:
            self._connection = await self._engine.connect()
            self._transaction = await self._connection.begin()
            await self._connection.execute(text("SET TRANSACTION READ ONLY"))
            await _assert_role(
                self._connection,
                expected_role=self._role,
                functions=(_CONFIRMATION_FUNCTION,),
            )
            self.ledger = IdentityRemediationLedgerRepository(self._connection)
            return self
        except BaseException:
            if self._transaction is not None and self._transaction.is_active:
                with suppress(BaseException):
                    await self._transaction.rollback()
            if self._connection is not None:
                with suppress(BaseException):
                    await self._connection.close()
            raise

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        cancellation_active = isinstance(exc, asyncio.CancelledError)
        cleanup_error: BaseException | None = None
        try:
            if self._transaction is not None and self._transaction.is_active:
                try:
                    await self._transaction.rollback()
                except BaseException as error:
                    cleanup_error = error
        finally:
            if self._connection is not None:
                try:
                    await self._connection.close()
                except BaseException as error:
                    cleanup_error = cleanup_error or error
        if not cancellation_active and cleanup_error is not None:
            raise cleanup_error


def _engines() -> tuple[AsyncEngine, AsyncEngine]:
    writer_url = _validated_url("KG_A2_IDENTITY_REMEDIATION_WRITER_DATABASE_URL")
    confirmation_url = _validated_url(
        "KG_A2_IDENTITY_REMEDIATION_CONFIRMATION_DATABASE_URL"
    )
    return (
        create_async_engine(writer_url, pool_pre_ping=True),
        create_async_engine(confirmation_url, pool_pre_ping=True),
    )


async def run_identity_remediation() -> RemediationRunSummary:
    writer_role = _validated_role("KG_A2_IDENTITY_REMEDIATION_WRITER_ROLE")
    confirmation_role = _validated_role(
        "KG_A2_IDENTITY_REMEDIATION_CONFIRMATION_ROLE"
    )
    if writer_role == confirmation_role:
        raise IdentityRemediationRunnerConfigurationError(
            "A2_REMEDIATION_DATABASE_ROLE_MISMATCH"
        )
    writer_engine, confirmation_engine = _engines()
    try:
        service = IdentityRemediationApplicationService(
            inventory_provider=run_identity_inventory,
            unit_of_work_factory=lambda: _WriterUnitOfWork(writer_engine, writer_role),
            confirmation_factory=lambda: _ConfirmationUnitOfWork(
                confirmation_engine, confirmation_role
            ),
            crypto=IdentitySubmissionCrypto.from_environment(),
            actor_scope=_actor_scope(os.environ["KG_TEST_RUN_ID"]),
        )
        return await service.run()
    finally:
        await writer_engine.dispose()
        await confirmation_engine.dispose()


async def inspect_remediation_runtime() -> dict[str, str]:
    writer_role = _validated_role("KG_A2_IDENTITY_REMEDIATION_WRITER_ROLE")
    confirmation_role = _validated_role(
        "KG_A2_IDENTITY_REMEDIATION_CONFIRMATION_ROLE"
    )
    inventory_role = _validated_role("KG_A2_IDENTITY_INVENTORY_ROLE")
    if len({writer_role, confirmation_role, inventory_role}) != 3:
        raise IdentityRemediationRunnerConfigurationError(
            "A2_REMEDIATION_DATABASE_ROLE_MISMATCH"
        )
    writer_engine, confirmation_engine = _engines()
    try:
        inventory_inspection = await inspect_read_only_transaction()
        if inventory_inspection != {
            "transaction_read_only": "on",
            "transaction_isolation": "repeatable read",
            "database_role": "inventory_reader_verified",
        }:
            raise IdentityRemediationRunnerConfigurationError(
                "A2_REMEDIATION_INVENTORY_AUTHORITY_INVALID"
            )
        async with writer_engine.connect() as writer_connection:
            await _assert_role(
                writer_connection,
                expected_role=writer_role,
                functions=_WRITER_FUNCTIONS,
            )
        async with confirmation_engine.connect() as confirmation_connection:
            await _assert_role(
                confirmation_connection,
                expected_role=confirmation_role,
                functions=(_CONFIRMATION_FUNCTION,),
            )
        return {
            "environment": "ephemeral_verified",
            "inventory_role": "verified",
            "writer_role": "verified",
            "confirmation_role": "verified",
            "database_sentinel": "verified",
            "direct_table_authority": "denied",
        }
    finally:
        await writer_engine.dispose()
        await confirmation_engine.dispose()
