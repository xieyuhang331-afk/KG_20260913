from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from time import monotonic
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text


class HealthDataDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["LIVE", "READY"]


class HealthResponseDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal[0] = 0
    message: Literal["ok"] = "ok"
    data: HealthDataDTO


def health_response(status: Literal["LIVE", "READY"]) -> HealthResponseDTO:
    return HealthResponseDTO(data=HealthDataDTO(status=status))


async def _await_storage_operation(operation):
    task = asyncio.create_task(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancellation:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        with suppress(BaseException):
            task.result()
        raise cancellation


async def probe_private_object_store(object_store) -> bool:
    object_key = f".readiness/{uuid4().hex}.probe"
    lease_token = str(uuid4())
    temporary_maybe_owned = False
    final_maybe_owned = False
    healthy = False
    cleanup_ok = True
    primary_cancel: asyncio.CancelledError | None = None
    try:
        temporary_maybe_owned = True
        await _await_storage_operation(
            object_store.create_temporary(object_key, lease_token)
        )
        await _await_storage_operation(
            object_store.write_chunk(object_key, lease_token, b"ready")
        )
        evidence = await _await_storage_operation(
            object_store.flush(
                object_key, lease_token, mime_type="application/octet-stream"
            )
        )
        final_maybe_owned = True
        await _await_storage_operation(object_store.commit(object_key, lease_token))
        actual = await _await_storage_operation(object_store.stat(object_key))
        healthy = (
            evidence.size == 5
            and actual.size == 5
            and evidence.sha256 == actual.sha256
        )
    except asyncio.CancelledError as error:
        primary_cancel = error
    except Exception:
        healthy = False
    finally:
        cleanup_cancel: asyncio.CancelledError | None = None
        if final_maybe_owned:
            try:
                await _await_storage_operation(object_store.delete(object_key))
            except asyncio.CancelledError as error:
                cleanup_cancel = error
            except Exception:
                cleanup_ok = False
        if temporary_maybe_owned:
            try:
                await _await_storage_operation(
                    object_store.abort_temporary(object_key, lease_token)
                )
            except asyncio.CancelledError as error:
                cleanup_cancel = cleanup_cancel or error
            except Exception:
                cleanup_ok = False
        if primary_cancel is not None:
            raise primary_cancel
        if cleanup_cancel is not None:
            raise cleanup_cancel
    return healthy and cleanup_ok


async def probe_database_role(session_factory, expected_role: str | None) -> bool:
    if not expected_role:
        return False
    try:
        async with session_factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT 1 AS probe,
                           current_user AS role,
                           COALESCE((
                               SELECT rolsuper OR rolcreatedb OR rolcreaterole
                                      OR rolreplication OR rolbypassrls
                               FROM pg_catalog.pg_roles
                               WHERE rolname = current_user
                           ), TRUE)
                           OR EXISTS (
                               SELECT 1
                               FROM pg_catalog.pg_roles AS inherited_role
                               WHERE pg_catalog.pg_has_role(
                                   current_user, inherited_role.rolname, 'MEMBER'
                               )
                               AND (
                                   inherited_role.rolsuper
                                   OR inherited_role.rolcreatedb
                                   OR inherited_role.rolcreaterole
                                   OR inherited_role.rolreplication
                                   OR inherited_role.rolbypassrls
                               )
                           )
                           OR EXISTS (
                               SELECT 1
                               FROM pg_catalog.pg_database AS database_role
                               WHERE database_role.datname = current_database()
                                 AND pg_catalog.pg_has_role(
                                     current_user, database_role.datdba, 'MEMBER'
                                 )
                           )
                           OR EXISTS (
                               SELECT 1
                               FROM pg_catalog.pg_namespace AS namespace_role
                               WHERE namespace_role.nspname !~ '^pg_'
                                 AND namespace_role.nspname <> 'information_schema'
                                 AND (
                                     pg_catalog.pg_has_role(
                                         current_user,
                                         namespace_role.nspowner,
                                         'MEMBER'
                                     )
                                     OR pg_catalog.has_schema_privilege(
                                         current_user,
                                         namespace_role.oid,
                                         'CREATE'
                                     )
                                 )
                           )
                           OR EXISTS (
                               SELECT 1
                               FROM pg_catalog.pg_class AS relation_role
                               JOIN pg_catalog.pg_namespace AS relation_namespace
                                 ON relation_namespace.oid = relation_role.relnamespace
                               WHERE relation_namespace.nspname !~ '^pg_'
                                 AND relation_namespace.nspname <> 'information_schema'
                                 AND pg_catalog.pg_has_role(
                                     current_user,
                                     relation_role.relowner,
                                     'MEMBER'
                                 )
                           ) AS high_privilege
                    """
                )
            )
            row = result.mappings().one()
        return (
            row["probe"] == 1
            and row["role"] == expected_role
            and row["high_privilege"] is False
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        return False


class ReadinessService:
    def __init__(
        self,
        *,
        session_factory,
        expected_database_role: str,
        object_store,
        file_probe: Callable[[], Awaitable[bool]] | None = None,
        dependency_timeout_seconds: float = 0.5,
        total_timeout_seconds: float = 2.0,
        cache_seconds: float = 2.0,
    ) -> None:
        self._session_factory = session_factory
        self._expected_database_role = expected_database_role
        self._object_store = object_store
        self._file_probe = file_probe or (
            lambda: probe_private_object_store(self._object_store)
        )
        self._dependency_timeout = dependency_timeout_seconds
        self._total_timeout = total_timeout_seconds
        self._cache_seconds = cache_seconds
        self._lock = asyncio.Lock()
        self._probe_task: asyncio.Task[bool] | None = None
        self._completed_at = 0.0

    @property
    def has_pending_probe(self) -> bool:
        return self._probe_task is not None and not self._probe_task.done()

    async def _probe_database(self) -> bool:
        return await probe_database_role(
            self._session_factory, self._expected_database_role
        )

    async def _run_probe(self) -> bool:
        started = monotonic()
        database = asyncio.create_task(self._probe_database())
        file_system = asyncio.create_task(self._file_probe())
        try:
            database_result = await asyncio.wait_for(
                database, timeout=self._dependency_timeout
            )
            file_result = await file_system
            return (
                database_result
                and file_result
                and monotonic() - started <= self._dependency_timeout
            )
        except asyncio.CancelledError:
            for task in (database, file_system):
                if not task.done():
                    task.cancel()
            await asyncio.gather(database, file_system, return_exceptions=True)
            raise
        except Exception:
            if not database.done():
                database.cancel()
            if not file_system.done():
                await asyncio.gather(file_system, return_exceptions=True)
            return False
        finally:
            self._completed_at = monotonic()

    async def ready(self) -> bool:
        async with self._lock:
            now = monotonic()
            if (
                self._probe_task is None
                or (
                    self._probe_task.done()
                    and now - self._completed_at > self._cache_seconds
                )
            ):
                self._probe_task = asyncio.create_task(self._run_probe())
            task = self._probe_task
        wait_budget = min(self._dependency_timeout, self._total_timeout)
        done, _ = await asyncio.wait({task}, timeout=wait_budget)
        if not done:
            return False
        try:
            return task.result()
        except asyncio.CancelledError:
            raise
        except Exception:
            return False

    async def close(self) -> None:
        task = self._probe_task
        if task is None:
            return
        try:
            done, _ = await asyncio.wait({task}, timeout=self._total_timeout)
        except asyncio.CancelledError:
            raise
        if not done:
            raise RuntimeError("READINESS_CLEANUP_PENDING")
        try:
            task.result()
        except asyncio.CancelledError:
            raise
        except Exception:
            raise RuntimeError("READINESS_CLEANUP_FAILED") from None
