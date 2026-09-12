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
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.modules.private_file.repository import PrivateFileRepository
from app.modules.private_file.schemas import (
    UploadCompleteRequest,
    UploadInitiateRequest,
)
from app.modules.private_file.service import (
    authorize_file_access,
    complete_upload,
    initiate_upload,
    read_authorized_content,
    record_scan,
    upload_content,
)
from app.modules.private_file.storage import LocalFilesystemAdapter
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

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "migrations"
    / "versions"
    / "20260904_0038_batch_b_private_file_end_to_end.py"
)


def _parameterized_value(database: PgDatabase, sql: str, *args):
    return asyncio.run(database._fetch_value(sql, *args))


def _factory(environment_name: str):
    engine = create_async_engine(os.environ[environment_name], pool_pre_ping=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _synthetic_user(pg_database: PgDatabase, prefix: str) -> int:
    suffix = str(uuid4().int)[-8:]
    return int(
        await pg_database._fetch_value(
            'INSERT INTO public."user"(phone,password_hash,role,status) '
            "VALUES ($1,$2,'member','active') RETURNING id",
            f"{prefix}{suffix}",
            "batch-b-synthetic-only",
        )
    )


class _CleanScanner:
    async def scan(self, path: Path, *, mime_type: str) -> str:
        assert await asyncio.to_thread(path.is_file)
        assert mime_type == "application/pdf"
        return "CLEAN"


class _MutatingStatStore:
    def __init__(self, delegate, mutation):
        self._delegate = delegate
        self._mutation = mutation

    async def stat(self, object_key: str):
        evidence = await self._delegate.stat(object_key)
        await self._mutation()
        return evidence

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)


def test_B_R31_0038对象函数与专用角色最小权限真实PostgreSQL(pg_database):
    assert MIGRATION.exists(), "B_R31_0038_MIGRATION_MISSING"
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == (
        "20260913_0044"
    )
    columns = set(
        pg_database.fetch_column(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='private_file'"
        )
    )
    assert {
        "upload_lease_token",
        "upload_lease_until",
        "upload_operation_ref_digest",
        "upload_version",
    } <= columns
    assert pg_database.fetch_value(
        "SELECT to_regclass('public.private_file_download_access') IS NOT NULL"
    )

    access_role = os.environ["KG_TEST_PRIVATE_FILE_ACCESS_WRITER_ROLE"]
    file_writer_role = os.environ["KG_TEST_PRIVATE_FILE_WRITER_ROLE"]
    functions = (
        "public.batch_b_private_file_access_issue_v1"
        "(uuid,uuid,bigint,character varying,character varying,character varying,"
        "character varying,character varying,timestamp with time zone,"
        "timestamp with time zone)",
        "public.batch_b_private_file_access_consume_v1"
        "(uuid,uuid,bigint,character varying,character varying,"
        "timestamp with time zone,bigint)",
        "public.batch_b_private_file_access_confirm_v1"
        "(uuid,uuid,bigint,character varying,character varying,"
        "timestamp with time zone,bigint)",
    )
    for signature in functions:
        definition = _parameterized_value(
            pg_database,
            "SELECT pg_get_functiondef(to_regprocedure($1))",
            signature,
        )
        assert "SECURITY DEFINER" in definition
        assert re.search(
            r"SET search_path TO 'pg_catalog'|SET search_path = pg_catalog",
            definition,
        )
        assert _parameterized_value(
            pg_database,
            "SELECT has_function_privilege($1,to_regprocedure($2),'EXECUTE')",
            access_role,
            signature,
        )
        for role_env in (
            "KG_TEST_APPLICATION_ROLE",
            "KG_TEST_READONLY_ROLE",
            "KG_TEST_PRIVATE_FILE_WRITER_ROLE",
            "KG_TEST_INSTITUTION_ONBOARDING_READER_ROLE",
            "KG_TEST_DELIVERY_WORKER_ROLE",
        ):
            assert not _parameterized_value(
                pg_database,
                "SELECT has_function_privilege($1,to_regprocedure($2),'EXECUTE')",
                os.environ[role_env],
                signature,
            )

    snapshot_signature = (
        "public.batch_b_private_file_access_snapshot_v1"
        "(uuid,bigint,character varying)"
    )
    snapshot_definition = _parameterized_value(
        pg_database,
        "SELECT pg_get_functiondef(to_regprocedure($1))",
        snapshot_signature,
    )
    assert "SECURITY DEFINER" in snapshot_definition
    assert "SET search_path TO 'pg_catalog'" in snapshot_definition
    assert "slice4_report_file_authority_v1" in snapshot_definition
    assert "PERSONAL_DATA_EXPORT" in snapshot_definition
    for allowed_env in (
        "KG_TEST_INSTITUTION_ONBOARDING_READER_ROLE",
        "KG_TEST_SLICE4_CLINICAL_READER_ROLE",
        "KG_TEST_SLICE4_INSTITUTION_READER_ROLE",
    ):
        assert _parameterized_value(
            pg_database,
            "SELECT has_function_privilege($1,to_regprocedure($2),'EXECUTE')",
            os.environ[allowed_env],
            snapshot_signature,
        )
    for denied_env in (
        "KG_TEST_APPLICATION_ROLE",
        "KG_TEST_READONLY_ROLE",
        "KG_TEST_PRIVATE_FILE_WRITER_ROLE",
        "KG_TEST_PRIVATE_FILE_ACCESS_WRITER_ROLE",
        "KG_TEST_DELIVERY_WORKER_ROLE",
    ):
        assert not _parameterized_value(
            pg_database,
            "SELECT has_function_privilege($1,to_regprocedure($2),'EXECUTE')",
            os.environ[denied_env],
            snapshot_signature,
        )
    for reader_env in (
        "KG_TEST_INSTITUTION_ONBOARDING_READER_ROLE",
        "KG_TEST_SLICE4_CLINICAL_READER_ROLE",
        "KG_TEST_SLICE4_INSTITUTION_READER_ROLE",
    ):
        for column in ("actual_mime_type", "object_key"):
            assert not _parameterized_value(
                pg_database,
                "SELECT has_column_privilege($1,'public.private_file',$2,'SELECT')",
                os.environ[reader_env],
                column,
            )

    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
        assert not _parameterized_value(
            pg_database,
            "SELECT has_table_privilege($1,'public.private_file_download_access',$2)",
            access_role,
            privilege,
        )
    assert not _parameterized_value(
        pg_database,
        "SELECT has_table_privilege($1,'public.private_file','SELECT')",
        access_role,
    )
    for column in (
        "upload_lease_token",
        "upload_lease_until",
        "upload_operation_ref_digest",
        "upload_version",
    ):
        for privilege in ("SELECT", "INSERT", "UPDATE"):
            assert _parameterized_value(
                pg_database,
                "SELECT has_column_privilege($1,'public.private_file',$2,$3)",
                file_writer_role,
                column,
                privilege,
            )


@pytest.mark.asyncio
async def test_B_R32_LocalAdapter原子提交限额路径隔离与临时清理(tmp_path):
    root = tmp_path / "private-storage"
    store = LocalFilesystemAdapter(root.resolve())
    object_key = "slice1/batch-b/atomic.pdf"
    lease = str(uuid4())
    content = b"%PDF-1.7\nBATCH-B\n%%EOF"

    await store.create_temporary(object_key, lease)
    await store.write_chunk(object_key, lease, content[:7])
    await store.write_chunk(object_key, lease, content[7:])
    evidence = await store.flush(object_key, lease, mime_type="application/pdf")
    assert evidence.size == len(content)
    assert evidence.sha256 == hashlib.sha256(content).hexdigest()
    assert not (root / object_key).exists()
    await store.commit(object_key, lease)
    assert (root / object_key).read_bytes() == content

    with pytest.raises(Exception, match="PRIVATE_FILE_OBJECT_KEY_INVALID"):
        await store.stat("../escape")

    stale_lease = str(uuid4())
    active_lease = str(uuid4())
    await store.create_temporary("slice1/batch-b/stale.pdf", stale_lease)
    await store.create_temporary("slice1/batch-b/active.pdf", active_lease)
    stale = store._temporary("slice1/batch-b/stale.pdf", stale_lease)
    active = store._temporary("slice1/batch-b/active.pdf", active_lease)
    old = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
    os.utime(stale, (old, old))
    os.utime(active, (old, old))
    assert await store.cleanup_temporary(
        active_leases={active_lease},
        older_than=datetime.now(UTC) - timedelta(hours=1),
    ) == 1
    assert not stale.exists()
    assert active.exists()
    await store.abort_temporary("slice1/batch-b/active.pdf", active_lease)


@pytest.mark.asyncio
async def test_B_R33_流式上传complete幂等与一次性Header凭证真实数据库闭环(
    pg_database, tmp_path
):
    root = (tmp_path / "private-storage").resolve()
    store = LocalFilesystemAdapter(root)
    file_engine, file_sessions = _factory("KG_TEST_PRIVATE_FILE_WRITER_DATABASE_URL")
    reader_engine, reader_sessions = _factory(
        "KG_TEST_INSTITUTION_ONBOARDING_READER_DATABASE_URL"
    )
    access_engine, access_sessions = _factory(
        "KG_TEST_PRIVATE_FILE_ACCESS_WRITER_DATABASE_URL"
    )
    clinical_engine, clinical_sessions = _factory(
        "KG_TEST_SLICE4_CLINICAL_READER_DATABASE_URL"
    )
    user_id = await _synthetic_user(pg_database, "188")
    other_user_id = await _synthetic_user(pg_database, "185")
    reviewer_user_id = int(
        await pg_database._fetch_value(
            'INSERT INTO public."user"(phone,password_hash,role,status) '
            "VALUES ($1,$2,'super_admin','active') RETURNING id",
            f"184{str(uuid4().int)[-8:]}",
            "batch-b-synthetic-reviewer-only",
        )
    )
    file_id = ""
    export_file_id = str(uuid4())
    invitation_id = str(uuid4())
    application_id = str(uuid4())
    administrative_region_id = 800_000_000 + uuid4().int % 100_000_000
    content = b"%PDF-1.7\nBATCH-B-STREAM\n%%EOF"
    digest = hashlib.sha256(content).hexdigest()

    async def chunks():
        yield content[:5]
        yield content[5:17]
        yield content[17:]

    try:
        async with file_sessions() as session:
            initiated = await initiate_upload(
                session,
                user_id,
                UploadInitiateRequest(
                    purpose="BUSINESS_LICENSE",
                    size=len(content),
                    mime_type="application/pdf",
                    sha256=digest,
                ),
            )
        file_id = str(initiated["file_id"])
        async with file_sessions() as session:
            uploaded = await upload_content(
                session, user_id, file_id, chunks(), object_store=store
            )
        assert uploaded == {
            "received_size": len(content),
            "sha256": digest,
            "mime_type": "application/pdf",
        }
        request = UploadCompleteRequest(
            size=len(content), mime_type="application/pdf", sha256=digest
        )
        async with file_sessions() as session:
            first = await complete_upload(
                session, user_id, file_id, request, object_store=store
            )
        async with file_sessions() as session:
            replay = await complete_upload(
                session, user_id, file_id, request, object_store=store
            )
        assert first["status"] == replay["status"] == "PENDING_SCAN"
        assert first["dispatch_required"] is True
        assert replay["dispatch_required"] is False
        async with file_sessions() as session:
            scanned = await record_scan(
                session, file_id, scanner=_CleanScanner(), object_store=store
            )
        assert scanned["status"] == "CLEAN"

        await pg_database._fetch_value(
            "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) "
            "VALUES ($1,NULL,'Batch B synthetic region',$2,'county','active',1) "
            "RETURNING true",
            administrative_region_id,
            f"BATCH-B-{administrative_region_id}",
        )
        await pg_database._fetch_value(
            "INSERT INTO public.institution_invitation("
            "invitation_id,institution_name,institution_type,applicant_phone_ciphertext,"
            "applicant_phone_digest,pilot_batch_code,administrative_region_id,code_digest,"
            "status,failed_attempts,expires_at,issued_by,issued_at,activated_at,version) "
            "VALUES ($1,'Batch B synthetic','HEALTH_STORE',decode('00','hex'),"
            "repeat('1',64),'BATCH-B',$3,repeat('2',64),'ACTIVATED',0,"
            "clock_timestamp()+interval '1 day',$2,clock_timestamp(),clock_timestamp(),1) "
            "RETURNING true",
            invitation_id,
            reviewer_user_id,
            administrative_region_id,
        )
        await pg_database._fetch_value(
            "INSERT INTO public.institution_application("
            "application_id,invitation_id,applicant_user_id,institution_type,status,"
            "draft_payload,correction_fields,current_revision_no,service_ready,created_at,"
            "updated_at,version) VALUES ($1,$2,$3,'HEALTH_STORE','DRAFT','{}'::jsonb,"
            "'[]'::jsonb,0,false,clock_timestamp(),clock_timestamp(),1) RETURNING true",
            application_id,
            invitation_id,
            user_id,
        )
        await pg_database._fetch_value(
            "UPDATE public.private_file SET bound_application_id=$2,bound_at=clock_timestamp() "
            "WHERE file_id=$1 RETURNING true",
            file_id,
            application_id,
        )
        async with reader_sessions() as reader:
            repository = PrivateFileRepository(reader)
            owner = await repository.closed_access_snapshot(
                file_id=file_id, actor_user_id=user_id, context="OWNER"
            )
            assert owner is not None and str(owner["file_id"]) == file_id
            assert await repository.closed_access_snapshot(
                file_id=file_id, actor_user_id=other_user_id, context="OWNER"
            ) is None
            assert await repository.closed_access_snapshot(
                file_id=file_id, actor_user_id=reviewer_user_id, context="REVIEWER"
            ) is not None
            assert await repository.closed_access_snapshot(
                file_id=file_id, actor_user_id=other_user_id, context="REVIEWER"
            ) is None
            assert await repository.closed_access_snapshot(
                file_id=file_id, actor_user_id=user_id, context="EXPORT"
            ) is None
        await pg_database._fetch_value(
            'UPDATE public."user" SET status=\'disabled\' WHERE id=$1 RETURNING true',
            user_id,
        )
        async with reader_sessions() as reader:
            assert await PrivateFileRepository(reader).closed_access_snapshot(
                file_id=file_id, actor_user_id=user_id, context="OWNER"
            ) is None
        await pg_database._fetch_value(
            'UPDATE public."user" SET status=\'active\' WHERE id=$1 RETURNING true',
            user_id,
        )
        async with clinical_sessions() as clinical:
            assert await PrivateFileRepository(clinical).closed_access_snapshot(
                file_id=file_id,
                actor_user_id=reviewer_user_id,
                context="PLATFORM_AUTHORIZE",
            ) is None
        await pg_database._fetch_value(
            "INSERT INTO public.private_file("
            "file_id,purpose,owner_user_id,declared_size,declared_mime_type,declared_sha256,"
            "actual_size,actual_mime_type,actual_sha256,object_key,status,created_at,"
            "expires_at,scanned_at) VALUES ($1,'PERSONAL_DATA_EXPORT',$2,1,"
            "'application/pdf',repeat('3',64),1,'application/pdf',repeat('3',64),"
            "$3,'CLEAN',clock_timestamp(),clock_timestamp()+interval '1 day',clock_timestamp()) "
            "RETURNING true",
            export_file_id,
            user_id,
            f"slice1/batch-b-export/{export_file_id}",
        )
        async with reader_sessions() as reader:
            assert await PrivateFileRepository(reader).closed_access_snapshot(
                file_id=export_file_id, actor_user_id=user_id, context="OWNER"
            ) is None

        expires_at = int(time.time()) + 120

        async def clear_actual_evidence():
            await pg_database._fetch_value(
                "UPDATE public.private_file SET actual_size=NULL,"
                "actual_mime_type=NULL,actual_sha256=NULL "
                "WHERE file_id=$1 RETURNING true",
                file_id,
            )

        async def drift_actual_digest():
            await pg_database._fetch_value(
                "UPDATE public.private_file SET actual_sha256=repeat('f',64) "
                "WHERE file_id=$1 RETURNING true",
                file_id,
            )

        async def restore_actual_evidence():
            await pg_database._fetch_value(
                "UPDATE public.private_file SET actual_size=$2,"
                "actual_mime_type='application/pdf',actual_sha256=$3 "
                "WHERE file_id=$1 RETURNING true",
                file_id,
                len(content),
                digest,
            )

        async with reader_sessions() as reader, access_sessions() as access_writer:
            with pytest.raises(HTTPException) as issue_not_available:
                await authorize_file_access(
                    reader,
                    access_writer,
                    user_id,
                    file_id,
                    "OWNER_DOWNLOAD",
                    expires_at,
                    object_store=_MutatingStatStore(store, clear_actual_evidence),
                )
        assert issue_not_available.value.status_code == 404
        assert issue_not_available.value.detail == "PRIVATE_FILE_NOT_FOUND"
        await restore_actual_evidence()

        async with reader_sessions() as reader, access_sessions() as access_writer:
            with pytest.raises(HTTPException) as issue_evidence_mismatch:
                await authorize_file_access(
                    reader,
                    access_writer,
                    user_id,
                    file_id,
                    "OWNER_DOWNLOAD",
                    expires_at,
                    object_store=_MutatingStatStore(store, drift_actual_digest),
                )
        assert issue_evidence_mismatch.value.status_code == 409
        assert (
            issue_evidence_mismatch.value.detail
            == "PRIVATE_FILE_EVIDENCE_MISMATCH"
        )
        await restore_actual_evidence()
        assert await pg_database._fetch_value(
            "SELECT count(*)=0 FROM public.private_file_download_access "
            "WHERE private_file_id=$1",
            file_id,
        )

        async with reader_sessions() as reader, access_sessions() as access_writer:
            credential = await authorize_file_access(
                reader,
                access_writer,
                user_id,
                file_id,
                "OWNER_DOWNLOAD",
                expires_at,
                object_store=store,
            )
        assert credential.count(".") == 2
        assert file_id not in credential

        replay_access_id = str(uuid4())
        replay_issued_at = datetime.now(UTC)
        replay_values = {
            "access_id": replay_access_id,
            "private_file_id": file_id,
            "actor_user_id": user_id,
            "access_scope": "OWNER",
            "reason_code": "OWNER_DOWNLOAD",
            "credential_digest": "1" * 64,
            "authority_digest": "2" * 64,
            "content_evidence_digest": hashlib.sha256(
                (
                    f"BATCH_B_PRIVATE_FILE_CONTENT_V1;{file_id};{len(content)};"
                    f"application/pdf;{digest}"
                ).encode()
            ).hexdigest(),
            "issued_at": replay_issued_at,
            "expires_at": replay_issued_at + timedelta(seconds=120),
        }
        async with access_sessions() as access_writer:
            first_issue = await PrivateFileRepository(access_writer).issue_access(
                replay_values
            )
            await access_writer.commit()
        await clear_actual_evidence()
        async with access_sessions() as access_writer:
            replay_issue = await PrivateFileRepository(access_writer).issue_access(
                replay_values
            )
            await access_writer.commit()
        assert first_issue["result_code"] == replay_issue["result_code"] == "ISSUED"
        assert first_issue["result_digest"] == replay_issue["result_digest"]
        await restore_actual_evidence()

        async with reader_sessions() as reader, access_sessions() as access_writer:
            unavailable_credential = await authorize_file_access(
                reader,
                access_writer,
                user_id,
                file_id,
                "OWNER_DOWNLOAD",
                expires_at,
                object_store=store,
            )
        async with reader_sessions() as reader, access_sessions() as access_writer:
            with pytest.raises(HTTPException) as consume_not_available:
                await read_authorized_content(
                    reader,
                    access_writer,
                    user_id,
                    file_id,
                    unavailable_credential,
                    object_store=_MutatingStatStore(store, clear_actual_evidence),
                )
        assert consume_not_available.value.status_code == 404
        assert consume_not_available.value.detail == "PRIVATE_FILE_NOT_FOUND"
        unavailable_access_id = unavailable_credential.split(".", 1)[0]
        assert await pg_database._fetch_value(
            "SELECT consumed_at IS NULL AND version=1 "
            "FROM public.private_file_download_access WHERE access_id=$1",
            unavailable_access_id,
        )
        await restore_actual_evidence()

        async with reader_sessions() as reader, access_sessions() as access_writer:
            mismatch_credential = await authorize_file_access(
                reader,
                access_writer,
                user_id,
                file_id,
                "OWNER_DOWNLOAD",
                expires_at,
                object_store=store,
            )
        async with reader_sessions() as reader, access_sessions() as access_writer:
            with pytest.raises(HTTPException) as consume_evidence_mismatch:
                await read_authorized_content(
                    reader,
                    access_writer,
                    user_id,
                    file_id,
                    mismatch_credential,
                    object_store=_MutatingStatStore(store, drift_actual_digest),
                )
        assert consume_evidence_mismatch.value.status_code == 409
        assert (
            consume_evidence_mismatch.value.detail
            == "PRIVATE_FILE_EVIDENCE_MISMATCH"
        )
        mismatch_access_id = mismatch_credential.split(".", 1)[0]
        assert await pg_database._fetch_value(
            "SELECT consumed_at IS NULL AND version=1 "
            "FROM public.private_file_download_access WHERE access_id=$1",
            mismatch_access_id,
        )
        await restore_actual_evidence()

        access_id, access_version, access_secret = credential.split(".", 2)
        forged_secret = ("A" if access_secret[0] != "A" else "B") + access_secret[1:]
        forged_credential = f"{access_id}.{access_version}.{forged_secret}"
        async with reader_sessions() as reader, access_sessions() as access_writer:
            with pytest.raises(HTTPException) as forged_error:
                await read_authorized_content(
                    reader,
                    access_writer,
                    user_id,
                    file_id,
                    forged_credential,
                    object_store=store,
                )
        assert forged_error.value.status_code == 403
        assert forged_error.value.detail == "PRIVATE_FILE_ACCESS_INVALID"

        async with reader_sessions() as reader, access_sessions() as access_writer:
            expired_credential = await authorize_file_access(
                reader,
                access_writer,
                user_id,
                file_id,
                "OWNER_DOWNLOAD",
                int(time.time()) + 120,
                object_store=store,
            )
        expired_access_id = expired_credential.split(".", 1)[0]
        await pg_database._fetch_value(
            "UPDATE public.private_file_download_access "
            "SET issued_at=clock_timestamp()-interval '2 minutes',"
            "expires_at=clock_timestamp()-interval '1 minute' "
            "WHERE access_id=$1 RETURNING true",
            expired_access_id,
        )
        async with reader_sessions() as reader, access_sessions() as access_writer:
            with pytest.raises(HTTPException) as expired_error:
                await read_authorized_content(
                    reader,
                    access_writer,
                    user_id,
                    file_id,
                    expired_credential,
                    object_store=store,
                )
        assert expired_error.value.status_code == 403
        assert expired_error.value.detail == "PRIVATE_FILE_ACCESS_INVALID"

        async with reader_sessions() as reader, access_sessions() as access_writer:
            with pytest.raises(HTTPException) as cross_subject_error:
                await read_authorized_content(
                    reader,
                    access_writer,
                    other_user_id,
                    file_id,
                    credential,
                    object_store=store,
                )
        assert cross_subject_error.value.status_code == 404
        assert cross_subject_error.value.detail == "PRIVATE_FILE_NOT_FOUND"

        async with reader_sessions() as reader, access_sessions() as access_writer:
            stream, mime_type = await read_authorized_content(
                reader,
                access_writer,
                user_id,
                file_id,
                credential,
                object_store=store,
            )
            loaded = b"".join([chunk async for chunk in stream])
        assert loaded == content
        assert mime_type == "application/pdf"
        async with reader_sessions() as reader, access_sessions() as access_writer:
            with pytest.raises(HTTPException) as replay_error:
                await read_authorized_content(
                    reader,
                    access_writer,
                    user_id,
                    file_id,
                    credential,
                    object_store=store,
                )
        assert replay_error.value.status_code == 403
        assert replay_error.value.detail == "PRIVATE_FILE_ACCESS_INVALID"
        assert await pg_database._fetch_value(
            "SELECT count(*)=5 AND count(*) FILTER (WHERE consumed_at IS NOT NULL)=1 "
            "FROM public.private_file_download_access WHERE private_file_id=$1",
            file_id,
        )
    finally:
        if file_id:
            await pg_database._execute(
                "DELETE FROM public.private_file_download_access "
                f"WHERE private_file_id='{file_id}'"
            )
            await pg_database._execute(
                f"DELETE FROM public.private_file WHERE file_id='{file_id}'"
            )
        await pg_database._fetch_value(
            "DELETE FROM public.private_file WHERE file_id=$1 RETURNING true",
            export_file_id,
        )
        await pg_database._fetch_value(
            "DELETE FROM public.institution_application WHERE application_id=$1 RETURNING true",
            application_id,
        )
        await pg_database._fetch_value(
            "DELETE FROM public.institution_invitation WHERE invitation_id=$1 RETURNING true",
            invitation_id,
        )
        await pg_database._fetch_value(
            "DELETE FROM public.platform_org WHERE id=$1 RETURNING true",
            administrative_region_id,
        )
        for cleanup_user_id in (user_id, other_user_id, reviewer_user_id):
            await pg_database._fetch_value(
                'DELETE FROM public."user" WHERE id=$1 RETURNING true',
                cleanup_user_id,
            )
        await file_engine.dispose()
        await reader_engine.dispose()
        await access_engine.dispose()
        await clinical_engine.dispose()


@pytest.mark.asyncio
async def test_B_R34_并发上传仅一方成功且超限不读取后续分块(pg_database, tmp_path):
    store = LocalFilesystemAdapter((tmp_path / "private-storage").resolve())
    engine, sessions = _factory("KG_TEST_PRIVATE_FILE_WRITER_DATABASE_URL")
    user_id = await _synthetic_user(pg_database, "189")
    created: list[str] = []
    content = b"%PDF-1.7\nBATCH-B-CONCURRENT\n%%EOF"
    digest = hashlib.sha256(content).hexdigest()
    try:
        async with sessions() as session:
            initiated = await initiate_upload(
                session,
                user_id,
                UploadInitiateRequest(
                    purpose="BUSINESS_LICENSE",
                    size=len(content),
                    mime_type="application/pdf",
                    sha256=digest,
                ),
            )
        concurrent_id = str(initiated["file_id"])
        created.append(concurrent_id)

        async def upload_once():
            async with sessions() as session:
                return await upload_content(
                    session, user_id, concurrent_id, content, object_store=store
                )

        outcomes = await asyncio.gather(upload_once(), upload_once(), return_exceptions=True)
        assert sum(isinstance(value, dict) for value in outcomes) == 1
        conflicts = [value for value in outcomes if isinstance(value, HTTPException)]
        assert len(conflicts) == 1
        assert conflicts[0].status_code == 409
        assert conflicts[0].detail == "PRIVATE_FILE_UPLOAD_IN_PROGRESS"

        declared_size = 10 * 1024 * 1024
        async with sessions() as session:
            initiated = await initiate_upload(
                session,
                user_id,
                UploadInitiateRequest(
                    purpose="BUSINESS_LICENSE",
                    size=declared_size,
                    mime_type="application/pdf",
                    sha256="0" * 64,
                ),
            )
        limited_id = str(initiated["file_id"])
        created.append(limited_id)
        produced = 0

        async def too_large():
            nonlocal produced
            for _ in range(20):
                produced += 1
                yield b"x" * (1024 * 1024)

        async with sessions() as session:
            with pytest.raises(HTTPException) as exceeded:
                await upload_content(
                    session, user_id, limited_id, too_large(), object_store=store
                )
        assert exceeded.value.status_code == 413
        assert exceeded.value.detail == "PRIVATE_FILE_SIZE_LIMIT_EXCEEDED"
        assert produced == 11
        row = await pg_database._fetch_rows(
            "SELECT actual_size,upload_lease_token,upload_lease_until "
            "FROM public.private_file WHERE file_id=$1",
            limited_id,
        )
        assert row == [
            {
                "actual_size": None,
                "upload_lease_token": None,
                "upload_lease_until": None,
            }
        ]
        assert not list(store.root.rglob(".*.part"))
    finally:
        for file_id in created:
            await pg_database._execute(
                f"DELETE FROM public.private_file WHERE file_id='{file_id}'"
            )
        await pg_database._execute(f'DELETE FROM public."user" WHERE id={user_id}')
        await engine.dispose()


def test_B_R35_scan_recover_cleanup固定private_file队列由独立Worker消费(pg_database):
    if os.getenv("KG_TEST_A3_REAL_RABBIT") != "1":
        return
    root = Path(os.environ["KG_PRIVATE_FILE_STORAGE_ROOT"])
    user_id = pg_database.fetch_value(
        'INSERT INTO public."user"(phone,password_hash,role,status) '
        "VALUES ('18700000000','batch-b-worker','member','active') RETURNING id"
    )
    scan_id, recover_id, cleanup_id = (str(uuid4()) for _ in range(3))
    content = b"%PDF-BATCH-B-WORKER"
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
                f"'{file_id}','BUSINESS_LICENSE',{user_id},{len(content)},"
                f"'application/pdf','{digest}'{actual},'slice1/batch-b-worker/{file_id}',"
                f"'{status}','{now.isoformat()}','{expires.isoformat()}')"
            )
            path = root / "slice1" / "batch-b-worker" / file_id
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

        scanned = scan_private_file_task.apply_async(
            args=(scan_id,), queue=PRIVATE_FILE_QUEUE
        ).get(timeout=60)
        assert scanned["status"] == "CLEAN"
        assert recover_pending_scan_tasks.apply_async(
            kwargs={"limit": 10}, queue=PRIVATE_FILE_QUEUE
        ).get(timeout=60) >= 1
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if pg_database.fetch_value(
                f"SELECT status='CLEAN' FROM public.private_file "
                f"WHERE file_id='{recover_id}'"
            ):
                break
            time.sleep(0.5)
        else:
            pytest.fail("BATCH_B_RECOVERED_SCAN_NOT_CONSUMED")
        assert cleanup_orphan_private_files.apply_async(
            kwargs={"limit": 10}, queue=PRIVATE_FILE_QUEUE
        ).get(timeout=60) >= 1
        assert pg_database.fetch_value(
            f"SELECT status='DELETED' FROM public.private_file WHERE file_id='{cleanup_id}'"
        )
        assert not (root / "slice1" / "batch-b-worker" / cleanup_id).exists()
    finally:
        pg_database.execute(
            "DELETE FROM public.private_file WHERE object_key LIKE "
            "'slice1/batch-b-worker/%'"
        )
        pg_database.execute(f'DELETE FROM public."user" WHERE id={user_id}')
        for file_id in (scan_id, recover_id, cleanup_id):
            path = root / "slice1" / "batch-b-worker" / file_id
            if path.exists():
                path.unlink()


def test_Z_B_R36_downgrade活动上传或凭证拒绝且静默期可往返(pg_database):
    user_id = pg_database.fetch_value(
        'INSERT INTO public."user"(phone,password_hash,role,status) '
        "VALUES ('18600000000','batch-b-downgrade','member','active') RETURNING id"
    )
    file_id = str(uuid4())
    lease_token = str(uuid4())
    digest = "0" * 64
    pg_database.execute(
        "INSERT INTO public.private_file("
        "file_id,purpose,owner_user_id,declared_size,declared_mime_type,declared_sha256,"
        "object_key,status,created_at,expires_at,upload_lease_token,upload_lease_until,"
        "upload_operation_ref_digest,upload_version) VALUES ("
        f"'{file_id}','BUSINESS_LICENSE',{user_id},1,'application/pdf','{digest}',"
        f"'slice1/batch-b-downgrade/{file_id}','UPLOAD_INITIATED',clock_timestamp(),"
        f"clock_timestamp()+interval '15 minutes','{lease_token}',"
        f"clock_timestamp()+interval '5 minutes','{digest}',2)"
    )
    config = _build_alembic_config(_get_test_database_url())
    snapshot_signature = (
        "public.batch_b_private_file_access_snapshot_v1"
        "(uuid,bigint,character varying)"
    )
    allowed_snapshot_roles = tuple(
        os.environ[name]
        for name in (
            "KG_TEST_INSTITUTION_ONBOARDING_READER_ROLE",
            "KG_TEST_SLICE4_CLINICAL_READER_ROLE",
            "KG_TEST_SLICE4_INSTITUTION_READER_ROLE",
        )
    )
    access_writer_role = os.environ["KG_TEST_PRIVATE_FILE_ACCESS_WRITER_ROLE"]
    assert _parameterized_value(
        pg_database,
        "SELECT has_schema_privilege($1,'public','USAGE')",
        access_writer_role,
    )
    try:
        with pytest.raises(RuntimeError, match="BATCH_B_PRIVATE_FILE_DOWNGRADE_NOT_QUIESCENT"):
            command.downgrade(config, "20260904_0037")
        assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == (
            "20260913_0044"
        )
        assert pg_database.fetch_value(
            "SELECT to_regclass('public.private_file_download_access') IS NOT NULL"
        )
        assert _parameterized_value(
            pg_database,
            "SELECT to_regprocedure($1) IS NOT NULL",
            snapshot_signature,
        )
        pg_database.execute(
            "UPDATE public.private_file SET upload_lease_token=NULL,upload_lease_until=NULL,"
            "upload_operation_ref_digest=NULL,upload_version=upload_version+1 "
            f"WHERE file_id='{file_id}'"
        )
        pg_database.execute(f"DELETE FROM public.private_file WHERE file_id='{file_id}'")
        command.downgrade(config, "20260904_0037")
        assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == (
            "20260904_0037"
        )
        assert pg_database.fetch_value(
            "SELECT to_regclass('public.private_file_download_access') IS NULL"
        )
        assert not _parameterized_value(
            pg_database,
            "SELECT to_regprocedure($1) IS NOT NULL",
            snapshot_signature,
        )
        assert not _parameterized_value(
            pg_database,
            "SELECT has_schema_privilege($1,'public','USAGE')",
            access_writer_role,
        )
        command.upgrade(config, "head")
        assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == (
            "20260913_0044"
        )
        assert _parameterized_value(
            pg_database,
            "SELECT to_regprocedure($1) IS NOT NULL",
            snapshot_signature,
        )
        assert _parameterized_value(
            pg_database,
            "SELECT has_schema_privilege($1,'public','USAGE')",
            access_writer_role,
        )
        for role in allowed_snapshot_roles:
            assert _parameterized_value(
                pg_database,
                "SELECT has_function_privilege($1,to_regprocedure($2),'EXECUTE')",
                role,
                snapshot_signature,
            )
            for column in ("actual_mime_type", "object_key"):
                assert not _parameterized_value(
                    pg_database,
                    "SELECT has_column_privilege($1,'public.private_file',$2,'SELECT')",
                    role,
                    column,
                )
        assert not _parameterized_value(
            pg_database,
            "SELECT has_function_privilege('public',to_regprocedure($1),'EXECUTE')",
            snapshot_signature,
        )
    finally:
        if pg_database.fetch_value("SELECT version_num FROM alembic_version") != (
            "20260913_0044"
        ):
            command.upgrade(config, "head")
            pg_database.execute(
                f"DELETE FROM public.private_file WHERE file_id='{file_id}'"
            )
            pg_database.execute(
                "DELETE FROM public.identity_phone_claim "
                f"WHERE user_id={user_id}"
            )
            pg_database.execute(f'DELETE FROM public."user" WHERE id={user_id}')
