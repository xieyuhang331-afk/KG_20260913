from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from asyncpg import CheckViolationError
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.modules.private_file import service as private_file_service
from app.modules.private_file.ports import PrivateFileScannerUnavailable
from app.modules.private_file.repository import PrivateFileRepository
from app.modules.private_file.service import (
    _finalize_scan_attempt,
    cleanup_orphan_private_file,
    delete_temporary,
    record_scan,
)
from app.tasks.celery_app import PRIVATE_FILE_QUEUE
from app.tasks.institution_onboarding_tasks import (
    cleanup_orphan_private_files,
    recover_pending_scan_tasks,
    scan_private_file_task,
)
from tests.integration.conftest import (
    PgDatabase,
    _build_alembic_config,
    _get_test_database_url,
)

pytestmark = pytest.mark.integration


def _parameterized_value(database: PgDatabase, sql: str, *args):
    return asyncio.run(database._fetch_value(sql, *args))


@pytest.fixture
def private_file_writer_database(pg_database):
    del pg_database
    return PgDatabase(os.environ["KG_TEST_PRIVATE_FILE_WRITER_DATABASE_URL"])


def test_A3_0037字段回填约束与引用authority真实PostgreSQL(pg_database):
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260909_0040"
    columns = set(pg_database.fetch_column(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='private_file'"
    ))
    assert {
        "scan_attempt_count", "scan_last_error_code", "scan_next_retry_at",
        "scan_lease_token", "scan_lease_until", "scan_operation_ref_digest",
        "scan_version",
    } <= columns
    definition = pg_database.fetch_value(
        "SELECT pg_get_functiondef("
        "'public.a3_private_file_referenced_v1(uuid)'::regprocedure)"
    )
    assert "SECURITY DEFINER" in definition
    assert re.search(r"SET search_path TO 'pg_catalog'|SET search_path = pg_catalog", definition)


def test_A3_file_writer只有闭合引用函数和必要列权限(
    pg_database, private_file_writer_database
):
    role = os.environ["KG_TEST_PRIVATE_FILE_WRITER_ROLE"]
    reader_role = os.environ["KG_TEST_INSTITUTION_ONBOARDING_READER_ROLE"]
    assert _parameterized_value(
        pg_database,
        "SELECT has_function_privilege($1, "
        "'public.a3_private_file_referenced_v1(uuid)', 'EXECUTE')",
        role,
    )
    assert private_file_writer_database.fetch_value(
        "SELECT public.a3_private_file_referenced_v1("
        "'00000000-0000-0000-0000-000000000001'::uuid)"
    ) is False
    for table in (
        "institution_license", "therapist_qualification_attachment",
        "detection_report_attachment", "personal_data_export_artifact",
        "personal_data_export_download_access",
    ):
        assert not _parameterized_value(
            pg_database,
            "SELECT has_table_privilege($1,to_regclass($2),'SELECT')",
            role,
            f"public.{table}",
        )
    for column in (
        "scan_attempt_count", "scan_last_error_code", "scan_next_retry_at",
        "scan_lease_token", "scan_lease_until", "scan_operation_ref_digest",
        "scan_version",
    ):
        for privilege in ("SELECT", "INSERT", "UPDATE"):
            assert _parameterized_value(
                pg_database,
                "SELECT has_column_privilege($1,'public.private_file',$2,$3)",
                role,
                column,
                privilege,
            )
    for column in (
        "scan_attempt_count", "scan_last_error_code", "scan_next_retry_at"
    ):
        assert _parameterized_value(
            pg_database,
            "SELECT has_column_privilege($1,'public.private_file',$2,'SELECT')",
            reader_role,
            column,
        )
    for role_env in (
        "KG_TEST_APPLICATION_ROLE",
        "KG_TEST_READONLY_ROLE",
        "KG_TEST_INSTITUTION_ONBOARDING_READER_ROLE",
        "KG_TEST_INSTITUTION_ONBOARDING_WRITER_ROLE",
        "KG_TEST_INSTITUTION_REVIEW_WRITER_ROLE",
        "KG_TEST_DELIVERY_WORKER_ROLE",
    ):
        unrelated_role = os.environ[role_env]
        assert not _parameterized_value(
            pg_database,
            "SELECT has_function_privilege($1, "
            "'public.a3_private_file_referenced_v1(uuid)', 'EXECUTE')",
            unrelated_role,
        )


def test_A3_I2_downgrade非静默期拒绝且任何DDL与授权均不变化(pg_database):
    file_id = str(uuid4())
    owner_user_id = pg_database.fetch_value(
        "INSERT INTO public.\"user\"(phone,password_hash,role,status) "
        "VALUES ('13500000000','a3-downgrade-only','member','active') RETURNING id"
    )
    pg_database.execute(
        "INSERT INTO public.private_file("
        "file_id,purpose,owner_user_id,declared_size,declared_mime_type,"
        "declared_sha256,object_key,status,created_at,expires_at) VALUES ("
        f"'{file_id}','BUSINESS_LICENSE',{owner_user_id},1,'application/pdf',"
        f"'{'0' * 64}','slice1/a3-downgrade/{file_id}','PENDING_SCAN',"
        "clock_timestamp(),clock_timestamp()+interval '15 minutes')"
    )
    before_columns = tuple(pg_database.fetch_column(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='private_file' ORDER BY column_name"
    ))
    before_constraint = pg_database.fetch_value(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname='ck_private_file_scan_state'"
    )
    before_function = pg_database.fetch_value(
        "SELECT pg_get_functiondef("
        "'public.a3_private_file_referenced_v1(uuid)'::regprocedure)"
    )
    config = _build_alembic_config(_get_test_database_url())
    try:
        with pytest.raises(
            RuntimeError, match="^A3_PRIVATE_FILE_DOWNGRADE_NOT_QUIESCENT$"
        ):
            command.downgrade(config, "20260903_0036")
        assert pg_database.fetch_value(
            "SELECT version_num FROM alembic_version"
        ) == "20260909_0040"
        assert tuple(pg_database.fetch_column(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='private_file' "
            "ORDER BY column_name"
        )) == before_columns
        assert pg_database.fetch_value(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname='ck_private_file_scan_state'"
        ) == before_constraint
        assert pg_database.fetch_value(
            "SELECT pg_get_functiondef("
            "'public.a3_private_file_referenced_v1(uuid)'::regprocedure)"
        ) == before_function
    finally:
        pg_database.execute(
            f"DELETE FROM public.private_file WHERE file_id='{file_id}'"
        )
        pg_database.execute(f'DELETE FROM public."user" WHERE id={owner_user_id}')


def test_A3_I3_file_writer直接SQL不能制造非法scan状态(
    pg_database, private_file_writer_database
):
    file_id = str(uuid4())
    owner_user_id = pg_database.fetch_value(
        "INSERT INTO public.\"user\"(phone,password_hash,role,status) "
        "VALUES ('13500000001','a3-constraint-only','member','active') RETURNING id"
    )
    pg_database.execute(
        "INSERT INTO public.private_file("
        "file_id,purpose,owner_user_id,declared_size,declared_mime_type,"
        "declared_sha256,object_key,status,created_at,expires_at) VALUES ("
        f"'{file_id}','BUSINESS_LICENSE',{owner_user_id},1,'application/pdf',"
        f"'{'0' * 64}','slice1/a3-constraint/{file_id}','PENDING_SCAN',"
        "clock_timestamp(),clock_timestamp()+interval '15 minutes')"
    )
    invalid_updates = (
        "scan_attempt_count=4",
        "status='CLEAN',scan_attempt_count=1,scanned_at=NULL",
        "status='SCAN_FAILED',scan_attempt_count=4,"
        "scan_last_error_code='OBJECT_MISSING',scanned_at=NULL",
        "status='SCAN_FAILED',scan_attempt_count=4,"
        "scan_last_error_code=NULL,scanned_at=clock_timestamp()",
        "status='DELETED',scan_operation_ref_digest='"
        + "1" * 64
        + "'",
    )
    try:
        lease_token = str(uuid4())
        private_file_writer_database.execute(
            "UPDATE public.private_file SET scan_attempt_count=4,"
            f"scan_lease_token='{lease_token}',"
            "scan_lease_until=clock_timestamp()+interval '5 minutes',"
            f"scan_operation_ref_digest='{'3' * 64}' "
            f"WHERE file_id='{file_id}'"
        )
        assert pg_database.fetch_value(
            "SELECT status='PENDING_SCAN' AND scan_attempt_count=4 "
            "AND scan_lease_token IS NOT NULL FROM public.private_file "
            f"WHERE file_id='{file_id}'"
        )
        private_file_writer_database.execute(
            "UPDATE public.private_file SET scan_attempt_count=0,"
            "scan_lease_token=NULL,scan_lease_until=NULL,"
            f"scan_operation_ref_digest=NULL WHERE file_id='{file_id}'"
        )
        for values in invalid_updates:
            with pytest.raises(CheckViolationError):
                private_file_writer_database.execute(
                    f"UPDATE public.private_file SET {values} WHERE file_id='{file_id}'"
                )
        assert pg_database.fetch_value(
            "SELECT status='PENDING_SCAN' AND scan_attempt_count=0 "
            f"FROM public.private_file WHERE file_id='{file_id}'"
        )
    finally:
        pg_database.execute(
            f"DELETE FROM public.private_file WHERE file_id='{file_id}'"
        )
        pg_database.execute(f'DELETE FROM public."user" WHERE id={owner_user_id}')


def test_Z_A3_Migration升级降级重升级不访问文件或修改业务行(pg_database, tmp_path):
    before = pg_database.fetch_value("SELECT count(*) FROM public.private_file")
    config = _build_alembic_config(_get_test_database_url())
    owner_user_id: int | None = None
    historical_ids = [str(uuid4()) for _ in range(4)]
    try:
        command.downgrade(config, "20260903_0036")
        assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260903_0036"
        assert pg_database.fetch_value(
            "SELECT to_regprocedure("
            "'public.a3_private_file_referenced_v1(uuid)') IS NULL"
        )
        owner_user_id = pg_database.fetch_value(
            "INSERT INTO public.\"user\"(phone,password_hash,role,status) "
            "VALUES ('13600000000','a3-history-only','member','active') RETURNING id"
        )
        for file_id, status in zip(
            historical_ids,
            ("PENDING_SCAN", "CLEAN", "REJECTED", "SCAN_FAILED"),
            strict=True,
        ):
            pg_database.execute(
                "INSERT INTO public.private_file("
                "file_id,purpose,owner_user_id,declared_size,declared_mime_type,"
                "declared_sha256,object_key,status,created_at,expires_at) VALUES ("
                f"'{file_id}','BUSINESS_LICENSE',{owner_user_id},1,'application/pdf',"
                f"'{'0' * 64}','slice1/a3-history/{file_id}','{status}',"
                "clock_timestamp(),clock_timestamp()+interval '15 minutes')"
            )
    finally:
        command.upgrade(config, "head")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260909_0040"
    rows = pg_database.fetch_rows(
        "SELECT status,scan_attempt_count,scan_last_error_code,"
        "scan_next_retry_at IS NOT NULL AS retry_scheduled "
        "FROM public.private_file WHERE object_key LIKE 'slice1/a3-history/%' "
        "ORDER BY status"
    )
    observed = {
        row["status"]: (
            row["scan_attempt_count"],
            row["scan_last_error_code"],
            row["retry_scheduled"],
        )
        for row in rows
    }
    assert observed == {
        "CLEAN": (1, None, False),
        "PENDING_SCAN": (0, None, True),
        "REJECTED": (1, None, False),
        "SCAN_FAILED": (4, "LEGACY_SCAN_FAILED", False),
    }
    pg_database.execute(
        "DELETE FROM public.private_file WHERE object_key LIKE 'slice1/a3-history/%'"
    )
    if owner_user_id is not None:
        pg_database.execute(f'DELETE FROM public."user" WHERE id={owner_user_id}')
    assert pg_database.fetch_value("SELECT count(*) FROM public.private_file") == before
    assert list(Path(tmp_path).iterdir()) == []


class _UnavailableScanner:
    async def scan(self, path, *, mime_type: str) -> str:
        del path, mime_type
        raise PrivateFileScannerUnavailable("A3_TEST_SCANNER_UNAVAILABLE")


class _CountingCleanScanner:
    def __init__(self) -> None:
        self.calls = 0

    async def scan(self, path, *, mime_type: str) -> str:
        del path, mime_type
        self.calls += 1
        return "CLEAN"


class _BlockingCleanScanner:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def scan(self, path, *, mime_type: str) -> str:
        del path, mime_type
        self.entered.set()
        await self.release.wait()
        return "CLEAN"


class _CommitFailureProxy:
    def __init__(self, session: AsyncSession, *, fail_on: int, cancelled: bool) -> None:
        self._session = session
        self.bind = session.bind
        self.fail_on = fail_on
        self.cancelled = cancelled
        self.commit_calls = 0

    def __getattr__(self, name: str):
        return getattr(self._session, name)

    async def commit(self) -> None:
        self.commit_calls += 1
        if self.commit_calls == self.fail_on:
            if self.cancelled:
                raise asyncio.CancelledError
            raise RuntimeError("A3_SYNTHETIC_COMMIT_OUTCOME_UNKNOWN")
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    async def close(self) -> None:
        await self._session.close()


def create_a3_test_scanner():
    if os.getenv("KG_TEST_ENVIRONMENT") != "ci_ephemeral":
        raise RuntimeError("A3_TEST_SCANNER_FORBIDDEN")
    return _CountingCleanScanner()


@pytest.mark.asyncio
async def test_A3_missing_mismatch_transient_recovery_cleanup真实数据库闭环(
    pg_database, monkeypatch, tmp_path
):
    monkeypatch.setenv("KG_PRIVATE_FILE_STORAGE_ROOT", str(tmp_path))
    engine = create_async_engine(
        os.environ["KG_TEST_PRIVATE_FILE_WRITER_DATABASE_URL"], pool_pre_ping=True
    )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    owner_user_id: int | None = None
    file_ids = [str(uuid4()) for _ in range(5)]
    now = datetime.now(UTC)
    content = b"%PDF-A3-SYNTHETIC"
    content_hash = hashlib.sha256(content).hexdigest()
    fixture_seeded = False
    try:
        owner_user_id = await pg_database._fetch_value(
            "INSERT INTO public.\"user\"(phone,password_hash,role,status) "
            "VALUES ('13900000000','a3-test-only','member','active') RETURNING id"
        )
        for index, file_id in enumerate(file_ids):
            digest = content_hash if index != 1 else "0" * 64
            await pg_database._execute(
                "INSERT INTO public.private_file("
                "file_id,purpose,owner_user_id,declared_size,declared_mime_type,"
                "declared_sha256,actual_size,actual_mime_type,actual_sha256,"
                "object_key,status,created_at,expires_at) VALUES ("
                f"'{file_id}','BUSINESS_LICENSE',{owner_user_id},{len(content)},"
                f"'application/pdf','{digest}',{len(content)},'application/pdf',"
                f"'{digest}','slice1/a3/{file_id}','PENDING_SCAN',"
                f"'{now.isoformat()}','{(now + timedelta(minutes=15)).isoformat()}')"
            )
        fixture_seeded = True
        for index in (1, 2, 3, 4):
            path = tmp_path / "slice1" / "a3" / file_ids[index]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

        async with session_factory() as session:
            missing = await record_scan(session, file_ids[0], scanner=_CountingCleanScanner())
        assert missing["status"] == "SCAN_FAILED"
        assert await pg_database._fetch_value(
            "SELECT scan_last_error_code FROM public.private_file WHERE file_id=$1", file_ids[0]
        ) == "OBJECT_MISSING"

        mismatch_scanner = _CountingCleanScanner()
        async with session_factory() as session:
            mismatch = await record_scan(session, file_ids[1], scanner=mismatch_scanner)
        assert mismatch["status"] == "SCAN_FAILED"
        assert mismatch_scanner.calls == 0
        assert await pg_database._fetch_value(
            "SELECT scan_last_error_code FROM public.private_file WHERE file_id=$1", file_ids[1]
        ) == "EVIDENCE_MISMATCH"

        delays: list[int] = []
        for attempt in range(1, 5):
            async with session_factory() as session:
                result = await record_scan(session, file_ids[2], scanner=_UnavailableScanner())
            if attempt < 4:
                delays.append(int(result["retry_after"]))
                await pg_database._fetch_value(
                    "UPDATE public.private_file "
                    "SET scan_next_retry_at=clock_timestamp()-interval '1 second' "
                    "WHERE file_id=$1 RETURNING scan_attempt_count", file_ids[2]
                )
            else:
                assert result["status"] == "SCAN_FAILED"
        assert delays == [5, 30, 120]
        assert await pg_database._fetch_value(
            "SELECT scan_last_error_code FROM public.private_file WHERE file_id=$1", file_ids[2]
        ) == "SCAN_SERVICE_UNAVAILABLE"

        await pg_database._fetch_value(
            "UPDATE public.private_file SET scan_attempt_count=3,scan_lease_token=$2,"
            "scan_lease_until=clock_timestamp()-interval '1 second',scan_next_retry_at=NULL,"
            "scan_version=scan_version+1 WHERE file_id=$1 RETURNING scan_version",
            file_ids[3], str(uuid4())
        )
        async with session_factory() as session:
            recovered = await PrivateFileRepository(session).recover_pending_scan_ids(
                now=datetime.now(UTC), limit=10
            )
            await session.commit()
        assert recovered.count(file_ids[3]) == 1
        recovered_row = await pg_database._fetch_rows(
            "SELECT status,scan_attempt_count,scan_lease_token,"
            "scan_operation_ref_digest FROM public.private_file WHERE file_id=$1",
            file_ids[3],
        )
        assert len(recovered_row) == 1
        assert recovered_row[0]["status"] == "PENDING_SCAN"
        assert recovered_row[0]["scan_attempt_count"] == 3
        assert recovered_row[0]["scan_lease_token"] is None
        assert recovered_row[0]["scan_operation_ref_digest"] is None

        async with session_factory() as session:
            fourth = await record_scan(
                session, file_ids[3], scanner=_UnavailableScanner()
            )
        assert fourth["status"] == "SCAN_FAILED"
        assert await pg_database._fetch_value(
            "SELECT scan_attempt_count=4 AND scan_last_error_code="
            "'SCAN_SERVICE_UNAVAILABLE' FROM public.private_file WHERE file_id=$1",
            file_ids[3],
        )
        fifth_scanner = _CountingCleanScanner()
        async with session_factory() as session:
            fifth = await record_scan(session, file_ids[3], scanner=fifth_scanner)
        assert fifth == {
            "file_id": file_ids[3],
            "status": "SCAN_FAILED",
            "claimed": False,
        }
        assert fifth_scanner.calls == 0

        await pg_database._fetch_value(
            "UPDATE public.private_file SET expires_at=clock_timestamp()-interval '1 second' "
            "WHERE file_id=$1 RETURNING scan_version", file_ids[4]
        )
        async with session_factory() as session:
            assert await cleanup_orphan_private_file(session, file_ids[4]) is False
            await session.rollback()
        assert await pg_database._fetch_value(
            "SELECT status FROM public.private_file WHERE file_id=$1", file_ids[4]
        ) == "PENDING_SCAN"
    finally:
        try:
            if fixture_seeded:
                await pg_database._execute(
                    "DELETE FROM public.private_file WHERE object_key LIKE 'slice1/a3/%'"
                )
            if owner_user_id is not None:
                await pg_database._execute(f'DELETE FROM public."user" WHERE id={owner_user_id}')
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_A3_R06同一file并发scan仅一个数据库claim(pg_database, monkeypatch, tmp_path):
    monkeypatch.setenv("KG_PRIVATE_FILE_STORAGE_ROOT", str(tmp_path))
    engine = create_async_engine(
        os.environ["KG_TEST_PRIVATE_FILE_WRITER_DATABASE_URL"], pool_pre_ping=True
    )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    owner_user_id: int | None = None
    file_id = str(uuid4())
    object_key = f"slice1/a3-concurrent/{file_id}"
    content = b"%PDF-A3-CONCURRENT"
    digest = hashlib.sha256(content).hexdigest()
    scanner = _BlockingCleanScanner()
    try:
        owner_user_id = await pg_database._fetch_value(
            "INSERT INTO public.\"user\"(phone,password_hash,role,status) "
            "VALUES ('13700000000','a3-concurrent-only','member','active') RETURNING id"
        )
        await pg_database._execute(
            "INSERT INTO public.private_file("
            "file_id,purpose,owner_user_id,declared_size,declared_mime_type,"
            "declared_sha256,actual_size,actual_mime_type,actual_sha256,"
            "object_key,status,created_at,expires_at) VALUES ("
            f"'{file_id}','BUSINESS_LICENSE',{owner_user_id},{len(content)},"
            f"'application/pdf','{digest}',{len(content)},'application/pdf','{digest}',"
            f"'{object_key}','PENDING_SCAN',clock_timestamp(),"
            "clock_timestamp()+interval '15 minutes')"
        )
        path = tmp_path / object_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

        async def _first_scan():
            async with session_factory() as session:
                return await record_scan(session, file_id, scanner=scanner)

        first_task = asyncio.create_task(_first_scan())
        await asyncio.wait_for(scanner.entered.wait(), timeout=10)
        async with session_factory() as competing_session:
            competing = await record_scan(
                competing_session, file_id, scanner=_CountingCleanScanner()
            )
        assert competing == {
            "file_id": file_id,
            "status": "PENDING_SCAN",
            "claimed": False,
        }
        scanner.release.set()
        assert (await first_task)["status"] == "CLEAN"
        assert await pg_database._fetch_value(
            "SELECT scan_attempt_count=1 AND scan_version=5 "
            "AND status='CLEAN' FROM public.private_file WHERE file_id=$1",
            file_id,
        )
    finally:
        scanner.release.set()
        try:
            await pg_database._execute(
                f"DELETE FROM public.private_file WHERE file_id='{file_id}'"
            )
            if owner_user_id is not None:
                await pg_database._execute(
                    f'DELETE FROM public."user" WHERE id={owner_user_id}'
                )
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_A3_I1_claim_finalize未知或取消均隔离且recover零投递(
    pg_database, monkeypatch, tmp_path
):
    monkeypatch.setenv("KG_PRIVATE_FILE_STORAGE_ROOT", str(tmp_path))
    engine = create_async_engine(
        os.environ["KG_TEST_PRIVATE_FILE_WRITER_DATABASE_URL"], pool_pre_ping=True
    )
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    owner_user_id: int | None = None
    file_ids = [str(uuid4()) for _ in range(5)]
    content = b"%PDF-A3-COMMIT-UNKNOWN"
    digest = hashlib.sha256(content).hexdigest()

    async def _force_unknown(bind, file_id, *, preimage, postimage):
        del bind, file_id, preimage, postimage
        return "UNKNOWN"

    monkeypatch.setattr(
        private_file_service, "_confirm_scan_commit_outcome", _force_unknown
    )
    try:
        owner_user_id = await pg_database._fetch_value(
            "INSERT INTO public.\"user\"(phone,password_hash,role,status) "
            "VALUES ('13500000002','a3-unknown-only','member','active') RETURNING id"
        )
        for file_id in file_ids:
            await pg_database._execute(
                "INSERT INTO public.private_file("
                "file_id,purpose,owner_user_id,declared_size,declared_mime_type,"
                "declared_sha256,actual_size,actual_mime_type,actual_sha256,"
                "object_key,status,created_at,expires_at) VALUES ("
                f"'{file_id}','BUSINESS_LICENSE',{owner_user_id},{len(content)},"
                f"'application/pdf','{digest}',{len(content)},'application/pdf',"
                f"'{digest}','slice1/a3-unknown/{file_id}','PENDING_SCAN',"
                "clock_timestamp(),clock_timestamp()+interval '15 minutes')"
            )
            path = tmp_path / "slice1" / "a3-unknown" / file_id
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

        async with session_factory() as raw_session:
            session = _CommitFailureProxy(raw_session, fail_on=1, cancelled=False)
            with pytest.raises(HTTPException) as claim_unknown:
                await record_scan(session, file_ids[0], scanner=_CountingCleanScanner())
        assert claim_unknown.value.detail == "PRIVATE_FILE_SCAN_STATE_UNKNOWN"

        async with session_factory() as raw_session:
            session = _CommitFailureProxy(raw_session, fail_on=1, cancelled=True)
            with pytest.raises(asyncio.CancelledError):
                await record_scan(session, file_ids[1], scanner=_CountingCleanScanner())

        async with session_factory() as raw_session:
            session = _CommitFailureProxy(raw_session, fail_on=4, cancelled=False)
            with pytest.raises(HTTPException) as finalize_unknown:
                await record_scan(session, file_ids[2], scanner=_CountingCleanScanner())
        assert finalize_unknown.value.detail == "PRIVATE_FILE_SCAN_STATE_UNKNOWN"

        async def _isolation_unconfirmed(bind, file_id):
            del bind, file_id
            return False

        monkeypatch.setattr(
            private_file_service,
            "_isolate_scan_commit_unknown",
            _isolation_unconfirmed,
        )
        scanner = _CountingCleanScanner()
        async with session_factory() as raw_session:
            session = _CommitFailureProxy(raw_session, fail_on=2, cancelled=False)
            with pytest.raises(HTTPException) as ack_unknown:
                await record_scan(session, file_ids[3], scanner=scanner)
        assert ack_unknown.value.detail == "PRIVATE_FILE_SCAN_STATE_UNKNOWN"
        assert scanner.calls == 0

        guarded = await pg_database._fetch_rows(
            "SELECT status,scan_last_error_code,scan_lease_token,scan_version "
            f"FROM public.private_file WHERE file_id='{file_ids[3]}'"
        )
        assert len(guarded) == 1
        assert guarded[0]["status"] == "PENDING_SCAN"
        assert guarded[0]["scan_last_error_code"] == "COMMIT_OUTCOME_UNKNOWN"
        assert guarded[0]["scan_lease_token"] is not None
        guarded_version = guarded[0]["scan_version"]

        finalize_scanner = _CountingCleanScanner()
        async with session_factory() as raw_session:
            session = _CommitFailureProxy(raw_session, fail_on=4, cancelled=False)
            with pytest.raises(HTTPException) as fenced_finalize_unknown:
                await record_scan(
                    session, file_ids[4], scanner=finalize_scanner
                )
        assert fenced_finalize_unknown.value.detail == "PRIVATE_FILE_SCAN_STATE_UNKNOWN"
        assert finalize_scanner.calls == 1
        finalize_guarded = await pg_database._fetch_rows(
            "SELECT status,scan_last_error_code,scan_lease_token,scan_version "
            f"FROM public.private_file WHERE file_id='{file_ids[4]}'"
        )
        assert len(finalize_guarded) == 1
        assert finalize_guarded[0]["status"] == "PENDING_SCAN"
        assert (
            finalize_guarded[0]["scan_last_error_code"]
            == "COMMIT_OUTCOME_UNKNOWN"
        )
        assert finalize_guarded[0]["scan_lease_token"] is not None
        finalize_guarded_version = finalize_guarded[0]["scan_version"]

        async with session_factory() as session:
            recovered = await PrivateFileRepository(session).recover_pending_scan_ids(
                now=datetime.now(UTC) + timedelta(minutes=10), limit=10
            )
            await session.commit()
        assert not set(file_ids).intersection(recovered)

        rows = await pg_database._fetch_rows(
            "SELECT file_id,status,scan_last_error_code,scan_next_retry_at,scan_lease_token,"
            "scan_operation_ref_digest,scan_version,scanned_at "
            "FROM public.private_file WHERE object_key LIKE "
            "'slice1/a3-unknown/%' ORDER BY file_id"
        )
        assert len(rows) == 5
        assert all(row["status"] == "SCAN_FAILED" for row in rows)
        assert all(
            row["scan_last_error_code"] == "COMMIT_OUTCOME_UNKNOWN" for row in rows
        )
        assert all(row["scan_next_retry_at"] is None for row in rows)
        assert all(row["scan_lease_token"] is None for row in rows)
        assert all(row["scan_operation_ref_digest"] is None for row in rows)
        assert all(row["scanned_at"] is not None for row in rows)
        fenced = next(row for row in rows if str(row["file_id"]) == file_ids[3])
        assert fenced["scan_version"] == guarded_version + 1
        finalized = next(row for row in rows if str(row["file_id"]) == file_ids[4])
        assert finalized["scan_version"] == finalize_guarded_version + 1
    finally:
        try:
            await pg_database._execute(
                "DELETE FROM public.private_file WHERE object_key LIKE "
                "'slice1/a3-unknown/%'"
            )
            if owner_user_id is not None:
                await pg_database._execute(
                    f'DELETE FROM public."user" WHERE id={owner_user_id}'
                )
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_A3_I4_owner删除清租约递增version并隔离陈旧Worker(
    pg_database, monkeypatch, tmp_path
):
    monkeypatch.setenv("KG_PRIVATE_FILE_STORAGE_ROOT", str(tmp_path))
    engine = create_async_engine(
        os.environ["KG_TEST_PRIVATE_FILE_WRITER_DATABASE_URL"], pool_pre_ping=True
    )
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    owner_user_id: int | None = None
    file_id = str(uuid4())
    lease_token = str(uuid4())
    object_key = f"slice1/a3-delete/{file_id}"
    content = b"%PDF-A3-DELETE"
    digest = hashlib.sha256(content).hexdigest()
    initial_version = 5
    try:
        owner_user_id = await pg_database._fetch_value(
            "INSERT INTO public.\"user\"(phone,password_hash,role,status) "
            "VALUES ('13500000003','a3-delete-only','member','active') RETURNING id"
        )
        await pg_database._execute(
            "INSERT INTO public.private_file("
            "file_id,purpose,owner_user_id,declared_size,declared_mime_type,"
            "declared_sha256,actual_size,actual_mime_type,actual_sha256,object_key,"
            "status,created_at,expires_at,scan_attempt_count,scan_lease_token,"
            "scan_lease_until,scan_operation_ref_digest,scan_version) VALUES ("
            f"'{file_id}','BUSINESS_LICENSE',{owner_user_id},{len(content)},"
            f"'application/pdf','{digest}',{len(content)},'application/pdf','{digest}',"
            f"'{object_key}','PENDING_SCAN',clock_timestamp(),"
            f"clock_timestamp()+interval '15 minutes',1,'{lease_token}',"
            f"clock_timestamp()+interval '5 minutes','{'2' * 64}',{initial_version})"
        )
        path = tmp_path / object_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

        async with session_factory() as session:
            await delete_temporary(session, owner_user_id, file_id)
        deleted_rows = await pg_database._fetch_rows(
            "SELECT status,scan_next_retry_at,scan_lease_token,scan_lease_until,"
            "scan_operation_ref_digest,scan_version,deleted_at "
            "FROM public.private_file WHERE file_id=$1",
            file_id,
        )
        assert len(deleted_rows) == 1
        deleted = deleted_rows[0]
        assert deleted["status"] == "DELETED"
        assert deleted["scan_next_retry_at"] is None
        assert deleted["scan_lease_token"] is None
        assert deleted["scan_lease_until"] is None
        assert deleted["scan_operation_ref_digest"] is None
        assert deleted["scan_version"] == initial_version + 1
        assert deleted["deleted_at"] is not None
        assert not path.exists()

        stale_claim = {
            "file_id": file_id,
            "attempt_count": 1,
            "lease_token": lease_token,
            "scan_version": initial_version,
        }
        async with session_factory() as session:
            stale = await _finalize_scan_attempt(session, stale_claim, status="CLEAN")
        assert stale == {"file_id": file_id, "status": "DELETED", "claimed": False}
        async with session_factory() as session:
            recovered = await PrivateFileRepository(session).recover_pending_scan_ids(
                now=datetime.now(UTC), limit=10
            )
            await session.commit()
        assert file_id not in recovered

        async with session_factory() as session:
            await delete_temporary(session, owner_user_id, file_id)
        assert await pg_database._fetch_value(
            "SELECT scan_version FROM public.private_file WHERE file_id=$1", file_id
        ) == initial_version + 1
    finally:
        try:
            await pg_database._execute(
                f"DELETE FROM public.private_file WHERE file_id='{file_id}'"
            )
            if owner_user_id is not None:
                await pg_database._execute(
                    f'DELETE FROM public."user" WHERE id={owner_user_id}'
                )
        finally:
            await engine.dispose()


def test_A3_scan_recover_cleanup由独立private_file_worker真实消费(pg_database):
    if os.getenv("KG_TEST_A3_REAL_RABBIT") != "1":
        return
    root = Path(os.environ["KG_PRIVATE_FILE_STORAGE_ROOT"])
    owner_user_id = pg_database.fetch_value(
        "INSERT INTO public.\"user\"(phone,password_hash,role,status) "
        "VALUES ('13800000000','a3-worker-only','member','active') RETURNING id"
    )
    scan_id, recover_id, cleanup_id = (str(uuid4()) for _ in range(3))
    content = b"%PDF-A3-WORKER"
    digest = hashlib.sha256(content).hexdigest()
    now = datetime.now(UTC)
    try:
        for file_id, status, expires in (
            (scan_id, "PENDING_SCAN", now + timedelta(minutes=15)),
            (recover_id, "PENDING_SCAN", now + timedelta(minutes=15)),
            (cleanup_id, "UPLOAD_INITIATED", now - timedelta(seconds=1)),
        ):
            actual = (
                f",{len(content)},'application/pdf','{digest}'"
                if status == "PENDING_SCAN"
                else ",NULL,NULL,NULL"
            )
            pg_database.execute(
                "INSERT INTO public.private_file("
                "file_id,purpose,owner_user_id,declared_size,declared_mime_type,"
                "declared_sha256,actual_size,actual_mime_type,actual_sha256,"
                "object_key,status,created_at,expires_at) VALUES ("
                f"'{file_id}','BUSINESS_LICENSE',{owner_user_id},{len(content)},"
                f"'application/pdf','{digest}'{actual},'slice1/a3-worker/{file_id}',"
                f"'{status}','{now.isoformat()}','{expires.isoformat()}')"
            )
            path = root / "slice1" / "a3-worker" / file_id
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

        scan_result = scan_private_file_task.apply_async(
            args=(scan_id,), queue=PRIVATE_FILE_QUEUE
        ).get(timeout=60)
        assert scan_result["status"] == "CLEAN"

        recovered = recover_pending_scan_tasks.apply_async(
            kwargs={"limit": 10}, queue=PRIVATE_FILE_QUEUE
        ).get(timeout=60)
        assert recovered >= 1
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if pg_database.fetch_value(
                f"SELECT status='CLEAN' FROM public.private_file "
                f"WHERE file_id='{recover_id}'"
            ):
                break
            time.sleep(0.5)
        else:
            pytest.fail("A3_RECOVERED_SCAN_NOT_CONSUMED")

        cleaned = cleanup_orphan_private_files.apply_async(
            kwargs={"limit": 10}, queue=PRIVATE_FILE_QUEUE
        ).get(timeout=60)
        assert cleaned >= 1
        assert pg_database.fetch_value(
            f"SELECT status='DELETED' FROM public.private_file "
            f"WHERE file_id='{cleanup_id}'"
        )
        assert not (root / "slice1" / "a3-worker" / cleanup_id).exists()
    finally:
        pg_database.execute(
            "DELETE FROM public.private_file WHERE object_key LIKE 'slice1/a3-worker/%'"
        )
        pg_database.execute(f'DELETE FROM public."user" WHERE id={owner_user_id}')
        for file_id in (scan_id, recover_id, cleanup_id):
            path = root / "slice1" / "a3-worker" / file_id
            if path.exists():
                path.unlink()
