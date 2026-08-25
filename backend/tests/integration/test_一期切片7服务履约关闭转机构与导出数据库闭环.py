from __future__ import annotations

import asyncio
import importlib
import os
from datetime import datetime, timedelta, timezone
from io import BytesIO
from uuid import UUID
from zipfile import ZipFile

import pytest
from alembic import command
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.security import create_access_token
from app.core.uuid_generator import Uuid7Generator
from app.modules.service_fulfillment.domain import SystemBusinessClock
from app.modules.service_fulfillment.repository import ServiceFulfillmentRepository
from app.modules.service_fulfillment.service import ServiceFulfillmentService
from app.modules.private_file.repository import PrivateFileRepository
from app.modules.private_file.service import (
    authorize_generated_export_access,
    read_authorized_content,
)
from app.tasks.slice7_service_fulfillment_tasks import _generate_export, _recover
from tests.integration.conftest import _build_alembic_config, _get_test_database_url


pytestmark = pytest.mark.integration


TABLES = {
    "service_cycle_schedule",
    "service_milestone",
    "service_milestone_revision",
    "service_case_lifecycle_event",
    "service_closing_assessment",
    "service_summary",
    "service_summary_acknowledgement",
    "service_transfer_request",
    "service_transfer_scope_revision",
    "personal_data_export_request",
    "personal_data_export_artifact",
    "personal_data_export_download_access",
    "service_fulfillment_receipt",
    "service_fulfillment_audit",
    "service_fulfillment_outbox",
    "service_fulfillment_delivery",
}


def _slice7_roles() -> set[str]:
    return {
        os.environ["KG_TEST_SLICE7_MILESTONE_WRITER_ROLE"],
        os.environ["KG_TEST_SLICE7_CASE_WRITER_ROLE"],
        os.environ["KG_TEST_SLICE7_TRANSFER_WRITER_ROLE"],
        os.environ["KG_TEST_SLICE7_EXPORT_WORKER_ROLE"],
        os.environ["KG_TEST_SLICE7_FAMILY_READER_ROLE"],
        os.environ["KG_TEST_SLICE7_OVERSIGHT_READER_ROLE"],
    }


def test_0031到0032到0031到0032生命周期保持单一Head(pg_database) -> None:
    config = _build_alembic_config(_get_test_database_url())
    command.downgrade(config, "20260826_0031")
    try:
        assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260826_0031"
        assert pg_database.fetch_value(
            "SELECT to_regprocedure('public.slice7_export_private_file_register_v1(jsonb)')"
        ) is None
        definitions = pg_database.fetch_value(
            "SELECT string_agg(pg_get_constraintdef(c.oid),' ') FROM pg_constraint c "
            "JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace "
            "WHERE n.nspname='public' AND t.relname='private_file' AND c.contype='c'"
        ) or ""
        assert "application/zip" not in definitions
    finally:
        command.upgrade(config, "20260827_0032")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260827_0032"
    assert pg_database.fetch_value(
        "SELECT to_regprocedure('public.slice7_export_private_file_register_v1(jsonb)')"
    ) is not None
    definitions = pg_database.fetch_value(
        "SELECT string_agg(pg_get_constraintdef(c.oid),' ') FROM pg_constraint c "
        "JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace "
        "WHERE n.nspname='public' AND t.relname='private_file' AND c.contype='c'"
    ) or ""
    assert "application/zip" in definitions


def test_0032单一Head且对象和六身份ACL闭合(pg_database) -> None:
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260827_0032"
    actual = set(pg_database.fetch_column(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE ANY(ARRAY['service_%','personal_data_export_%'])"
    ))
    assert TABLES <= actual
    roles = _slice7_roles()
    for role in roles:
        for table in TABLES:
            assert not pg_database.fetch_value(
                f"SELECT has_table_privilege('{role}','public.{table}','SELECT') OR "
                f"has_table_privilege('{role}','public.{table}','INSERT') OR "
                f"has_table_privilege('{role}','public.{table}','UPDATE') OR "
                f"has_table_privilege('{role}','public.{table}','DELETE')"
            )
        assert not pg_database.fetch_value(
            f"SELECT has_table_privilege('{role}','public.private_file','SELECT') OR "
            f"has_table_privilege('{role}','public.private_file','INSERT') OR "
            f"has_table_privilege('{role}','public.private_file','UPDATE') OR "
            f"has_table_privilege('{role}','public.private_file','DELETE')"
        )
    assert not pg_database.fetch_value(
        "SELECT has_function_privilege('public','public.slice7_authority_v1(varchar,uuid,bigint,varchar,bigint)','EXECUTE')"
    )


def test_必要Runtime仅通过固定受限接口工作(pg_database) -> None:
    roles = _slice7_roles()
    expected = {
        os.environ["KG_TEST_SLICE7_MILESTONE_WRITER_ROLE"]: "slice7_authority_v1(varchar,uuid,bigint,varchar,bigint)",
        os.environ["KG_TEST_SLICE7_CASE_WRITER_ROLE"]: "slice7_mutation_v1(jsonb)",
        os.environ["KG_TEST_SLICE7_TRANSFER_WRITER_ROLE"]: "slice7_mutation_replay_v1(bigint,varchar,varchar,bytea)",
        os.environ["KG_TEST_SLICE7_EXPORT_WORKER_ROLE"]: "slice7_worker_claim_v1(varchar,varchar,bigint)",
        os.environ["KG_TEST_SLICE7_FAMILY_READER_ROLE"]: "slice7_read_one_v1(varchar,uuid,bigint,varchar)",
        os.environ["KG_TEST_SLICE7_OVERSIGHT_READER_ROLE"]: "slice7_read_many_v1(varchar,uuid,bigint,varchar,uuid,bigint)",
    }
    for role, function in expected.items():
        assert pg_database.fetch_value(
            f"SELECT has_function_privilege('{role}','public.{function}','EXECUTE')"
        )

    export_role = os.environ["KG_TEST_SLICE7_EXPORT_WORKER_ROLE"]
    transfer_role = os.environ["KG_TEST_SLICE7_TRANSFER_WRITER_ROLE"]
    internal_export_functions = {
        "slice7_export_claim_v1(uuid,varchar)",
        "slice7_export_snapshot_v1(uuid)",
        "slice7_export_source_file_v1(uuid,uuid)",
        "slice7_export_private_file_register_v1(jsonb)",
        "slice7_export_private_file_snapshot_v1(uuid,uuid)",
        "slice7_export_artifact_bind_v1(jsonb)",
        "slice7_export_fail_v1(jsonb)",
        "slice7_export_recover_v1(jsonb)",
        "slice7_export_cleanup_claim_v1(timestamptz,bigint)",
        "slice7_export_cleanup_complete_v1(jsonb)",
    }
    for function in internal_export_functions:
        assert pg_database.fetch_value(
            f"SELECT has_function_privilege('{export_role}','public.{function}','EXECUTE')"
        )
        for role in roles - {export_role} | {"public"}:
            assert not pg_database.fetch_value(
                f"SELECT has_function_privilege('{role}','public.{function}','EXECUTE')"
            )
    download_function = "slice7_export_download_consume_v1(jsonb)"
    assert pg_database.fetch_value(
        f"SELECT has_function_privilege('{transfer_role}','public.{download_function}','EXECUTE')"
    )
    for role in roles - {transfer_role} | {"public"}:
        assert not pg_database.fetch_value(
            f"SELECT has_function_privilege('{role}','public.{download_function}','EXECUTE')"
        )


def _seed_active_plan(pg_database) -> dict[str, object]:
    seed_module = importlib.import_module(
        "tests.integration.test_一期切片6方案生成审核确认数据库闭环"
    )
    ordinal = int(os.getenv("KG_SLICE7_TEST_ORDINAL", "77"))
    seeded = seed_module._seed_ready_case(pg_database, ordinal=ordinal)
    request_id = f"019c7000-0000-7000-8000-{ordinal:012d}"
    plan_id = f"019c7001-0000-7000-8000-{ordinal:012d}"
    pg_database.execute(
        "BEGIN;"
        "INSERT INTO public.health_plan_generation_request("
        "request_id,service_case_id,subject_member_id,tenant_id,assessment_id,assessment_version,"
        "assembly_id,template_version_id,template_version,status,current_plan_id,failure_code,"
        "authority_digest,initiated_by,initiated_role,created_at,updated_at,version) VALUES ("
        f"'{request_id}','{seeded['case_id']}','{seeded['subject_member_id']}',{seeded['tenant_id']},"
        f"'{seeded['assessment_id']}',3,'{seeded['assembly_id']}','{seeded['template_id']}',1,"
        f"'ACTIVE','{plan_id}',NULL,decode(repeat('ab',32),'hex'),{seeded['org_actor_id']},"
        "'org_admin',now(),now(),1);"
        "INSERT INTO public.health_plan_version("
        "plan_id,request_id,service_case_id,subject_member_id,tenant_id,version_no,template_version_id,"
        "assessment_id,status,content,content_digest,supersedes_plan_id,created_at,updated_at,version) VALUES ("
        f"'{plan_id}','{request_id}','{seeded['case_id']}','{seeded['subject_member_id']}',"
        f"{seeded['tenant_id']},1,'{seeded['template_id']}','{seeded['assessment_id']}',"
        "'ACTIVE','{}'::jsonb,decode(repeat('cd',32),'hex'),NULL,now(),now(),1);"
        "COMMIT;"
    )
    return {**seeded, "plan_id": plan_id}


@pytest.fixture(scope="module")
def slice7_seeded(pg_database) -> dict[str, object]:
    return _seed_active_plan(pg_database)


def test_D03_D05_真实Runtime原子创建五节点并幂等完成D0(
    pg_database,
    slice7_seeded,
) -> None:
    seeded = slice7_seeded

    async def exercise() -> tuple[dict, dict, dict]:
        engine = create_async_engine(
            os.environ["KG_TEST_SLICE7_MILESTONE_WRITER_DATABASE_URL"],
            pool_pre_ping=True,
        )
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                service = ServiceFulfillmentService(
                    ServiceFulfillmentRepository(session),
                    SystemBusinessClock(),
                    Uuid7Generator().generate,
                )
                activated = await service.activate_cycle(
                    service_case_id=UUID(str(seeded["case_id"])),
                    actor_user_id=int(seeded["therapist_actor_id"]),
                    actor_role="therapist",
                    actor_tenant_id=int(seeded["tenant_id"]),
                    idempotency_key="slice7-db-activate-0001",
                    expected_version=1,
                )
                await session.commit()
                replayed = await service.activate_cycle(
                    service_case_id=UUID(str(seeded["case_id"])),
                    actor_user_id=int(seeded["therapist_actor_id"]),
                    actor_role="therapist",
                    actor_tenant_id=int(seeded["tenant_id"]),
                    idempotency_key="slice7-db-activate-0001",
                    expected_version=1,
                )
                await session.commit()
                d0 = next(item for item in activated["milestones"] if item["code"] == "D0")
                completed = await service.complete_milestone(
                    milestone_id=UUID(str(d0["milestone_id"])),
                    actor_user_id=int(seeded["therapist_actor_id"]),
                    actor_role="therapist",
                    actor_tenant_id=int(seeded["tenant_id"]),
                    idempotency_key="slice7-db-complete-d0-0001",
                    expected_version=1,
                    record_summary={"EXECUTION_STATUS": "COMPLETED"},
                    evidence_refs=(),
                )
                await session.commit()
                return activated, replayed, completed
        finally:
            await engine.dispose()

    activated, replayed, completed = asyncio.run(exercise())
    assert activated == replayed
    assert len(activated["milestones"]) == 5
    assert completed["status"] == "COMPLETED"
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.service_cycle_schedule WHERE service_case_id='{seeded['case_id']}'"
    ) == 1
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.service_milestone WHERE service_case_id='{seeded['case_id']}'"
    ) == 5
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.service_milestone_revision r JOIN public.service_milestone m ON m.milestone_id=r.milestone_id WHERE m.service_case_id='{seeded['case_id']}'"
    ) == 1
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.service_fulfillment_receipt WHERE target_id='{seeded['case_id']}' OR target_id IN (SELECT milestone_id FROM public.service_milestone WHERE service_case_id='{seeded['case_id']}')"
    ) == 2


def test_D37_D40_Worker崩溃后租约恢复且不重复事件(
    pg_database,
    slice7_seeded,
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("KG_PRIVATE_FILE_STORAGE_ROOT", str(tmp_path))
    seeded = slice7_seeded

    async def request_and_claim() -> UUID:
        transfer_engine = create_async_engine(
            os.environ["KG_TEST_SLICE7_TRANSFER_WRITER_DATABASE_URL"],
            pool_pre_ping=True,
        )
        export_engine = create_async_engine(
            os.environ["KG_TEST_SLICE7_EXPORT_WORKER_DATABASE_URL"],
            pool_pre_ping=True,
        )
        try:
            async with AsyncSession(transfer_engine, expire_on_commit=False) as session:
                service = ServiceFulfillmentService(
                    ServiceFulfillmentRepository(session),
                    SystemBusinessClock(),
                    Uuid7Generator().generate,
                )
                requested = await service.export_transition(
                    operation="CREATE_EXPORT",
                    export_or_member_id=UUID(str(seeded["subject_member_id"])),
                    actor_user_id=int(seeded["family_actor_id"]),
                    actor_role="member",
                    actor_tenant_id=None,
                    idempotency_key="slice7-db-export-recovery-0001",
                    request={
                        "requested_scope": ("ASSESSMENT",),
                        "reason": "PERSONAL_ARCHIVE",
                    },
                )
                await session.commit()
            export_id = UUID(str(requested["export_id"]))
            async with AsyncSession(export_engine, expire_on_commit=False) as session:
                claimed = await ServiceFulfillmentRepository(session).claim_export(
                    export_id, "crashed-export-worker"
                )
                await session.commit()
                assert claimed is not None
            return export_id
        finally:
            await transfer_engine.dispose()
            await export_engine.dispose()

    export_id = asyncio.run(request_and_claim())
    recovery_cutoff = datetime.now(timezone.utc) + timedelta(minutes=10)
    assert asyncio.run(_recover(recovery_cutoff)) == 1
    assert pg_database.fetch_value(
        f"SELECT status FROM public.personal_data_export_request WHERE export_id='{export_id}'"
    ) == "REQUESTED"
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.service_fulfillment_audit "
        f"WHERE target_id='{export_id}' AND action='EXPORT_RECOVERY_REQUESTED'"
    ) == 1
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.service_fulfillment_outbox "
        f"WHERE aggregate_ref='{export_id}' AND event_type='EXPORT_RETRY_REQUESTED'"
    ) == 1
    assert asyncio.run(_recover(recovery_cutoff)) == 0
    assert asyncio.run(_generate_export(export_id)) == "READY"
    assert asyncio.run(_recover(datetime.now(timezone.utc) + timedelta(days=2))) >= 1
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.service_fulfillment_audit "
        f"WHERE target_id='{export_id}' AND action='EXPORT_RECOVERY_REQUESTED'"
    ) == 1


def test_D31_D40_内部Worker生成ZIP并一次性下载且无基础表扩权(
    pg_database,
    slice7_seeded,
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("KG_PRIVATE_FILE_STORAGE_ROOT", str(tmp_path))
    seeded = slice7_seeded

    async def exercise() -> tuple[dict, bytes]:
        transfer_engine = create_async_engine(
            os.environ["KG_TEST_SLICE7_TRANSFER_WRITER_DATABASE_URL"],
            pool_pre_ping=True,
        )
        file_engine = create_async_engine(
            os.environ["KG_TEST_INSTITUTION_ONBOARDING_READER_DATABASE_URL"],
            pool_pre_ping=True,
        )
        try:
            async with AsyncSession(transfer_engine, expire_on_commit=False) as session:
                service = ServiceFulfillmentService(
                    ServiceFulfillmentRepository(session),
                    SystemBusinessClock(),
                    Uuid7Generator().generate,
                )
                requested = await service.export_transition(
                    operation="CREATE_EXPORT",
                    export_or_member_id=UUID(str(seeded["subject_member_id"])),
                    actor_user_id=int(seeded["family_actor_id"]),
                    actor_role="member",
                    actor_tenant_id=None,
                    idempotency_key="slice7-db-export-create-0001",
                    request={
                        "requested_scope": ("ASSESSMENT",),
                        "reason": "PERSONAL_ARCHIVE",
                    },
                )
                await session.commit()

            export_id = UUID(str(requested["export_id"]))
            assert await _generate_export(export_id) == "READY"

            async with AsyncSession(transfer_engine, expire_on_commit=False) as session:
                service = ServiceFulfillmentService(
                    ServiceFulfillmentRepository(session),
                    SystemBusinessClock(),
                    Uuid7Generator().generate,
                )
                access = await service.export_transition(
                    operation="EXPORT_DOWNLOAD_ACCESS",
                    export_or_member_id=export_id,
                    actor_user_id=int(seeded["family_actor_id"]),
                    actor_role="member",
                    actor_tenant_id=None,
                    idempotency_key="slice7-db-export-access-0001",
                    request={"reason": "PERSONAL_ARCHIVE", "step_up_verified": True},
                )
                async with AsyncSession(file_engine, expire_on_commit=False) as file_session:
                    token = await authorize_generated_export_access(
                        file_session,
                        user_id=int(seeded["family_actor_id"]),
                        file_id=str(export_id),
                        reason_code="PERSONAL_ARCHIVE",
                        expires_at=int(
                            datetime.fromisoformat(
                                str(access["expires_at"])
                            ).timestamp()
                        ),
                        token_id=str(access["access_id"]),
                    )
                    await session.commit()

                    async def consume(values: dict) -> bool:
                        consumed = await PrivateFileRepository(
                            session
                        ).consume_export_download_access(
                            {
                                "access_id": values["token_id"],
                                "private_file_id": str(export_id),
                                "actor_user_id": int(seeded["family_actor_id"]),
                                "evidence_digest": values["evidence_digest"],
                                "consumed_at": datetime.now(timezone.utc),
                            }
                        )
                        if consumed:
                            await session.commit()
                        else:
                            await session.rollback()
                        return consumed

                    content, mime_type = await read_authorized_content(
                        file_session,
                        int(seeded["family_actor_id"]),
                        str(export_id),
                        token,
                        export_access_consumer=consume,
                    )
                    assert mime_type == "application/zip"
                    with pytest.raises(HTTPException) as replay:
                        await read_authorized_content(
                            file_session,
                            int(seeded["family_actor_id"]),
                            str(export_id),
                            token,
                            export_access_consumer=consume,
                        )
                    assert replay.value.status_code == 403
                    return requested, content
        finally:
            await transfer_engine.dispose()
            await file_engine.dispose()

    requested, content = asyncio.run(exercise())
    with ZipFile(BytesIO(content)) as archive:
        assert archive.namelist() == ["manifest.json", "data/assessment.json"]

    export_id = requested["export_id"]
    assert pg_database.fetch_value(
        f"SELECT status FROM public.personal_data_export_request WHERE export_id='{export_id}'"
    ) == "DOWNLOADED"
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.private_file WHERE file_id='{export_id}' "
        "AND purpose='PERSONAL_DATA_EXPORT' AND actual_mime_type='application/zip' "
        "AND actual_size=declared_size AND actual_sha256=declared_sha256"
    ) == 1
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.personal_data_export_download_access "
        f"WHERE export_id='{export_id}' AND consumed_at IS NOT NULL"
    ) == 1
    export_role = os.environ["KG_TEST_SLICE7_EXPORT_WORKER_ROLE"]
    assert not pg_database.fetch_value(
        f"SELECT has_table_privilege('{export_role}','public.private_file','SELECT') "
        f"OR has_table_privilege('{export_role}','public.private_file','INSERT') "
        f"OR has_table_privilege('{export_role}','public.private_file','UPDATE')"
    )
    assert len(list(tmp_path.rglob(str(export_id)))) == 1

    recovered = asyncio.run(_recover(datetime.now(timezone.utc) + timedelta(days=2)))
    assert recovered >= 1
    assert pg_database.fetch_value(
        f"SELECT status FROM public.personal_data_export_request WHERE export_id='{export_id}'"
    ) == "EXPIRED"
    assert pg_database.fetch_value(
        f"SELECT status FROM public.private_file WHERE file_id='{export_id}'"
    ) == "DELETED"
    assert not list(tmp_path.rglob(str(export_id)))
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.service_fulfillment_audit "
        f"WHERE target_id='{export_id}' AND action='EXPORT_FILE_CLEANED'"
    ) == 1
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.service_fulfillment_outbox "
        f"WHERE aggregate_ref='{export_id}' AND event_type='EXPORT_FILE_CLEANED'"
    ) == 1
    assert asyncio.run(_recover(datetime.now(timezone.utc) + timedelta(days=2))) == 0


def test_D31_D40_真实ASGI要求StepUp并完成幂等导出与一次性下载(
    pg_database,
    real_db_client,
    slice7_seeded,
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("KG_PRIVATE_FILE_STORAGE_ROOT", str(tmp_path))
    actor_id = int(slice7_seeded["family_actor_id"])
    token = create_access_token({"sub": str(actor_id), "role": "member"})
    path = "/api/v1/family/data-exports"
    payload = {
        "requested_scope": ["ASSESSMENT"],
        "reason": "PERSONAL_ARCHIVE",
    }
    base_headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": "slice7-http-export-create-0001",
    }

    missing_step_up = real_db_client.post(path, headers=base_headers, json=payload)
    assert missing_step_up.status_code == 422
    invalid_step_up = real_db_client.post(
        path,
        headers={**base_headers, "X-Step-Up-Token": "not-a-token"},
        json=payload,
    )
    assert invalid_step_up.status_code == 403
    assert invalid_step_up.json()["code"] == "STEP_UP_FORBIDDEN"
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.personal_data_export_request "
        f"WHERE requested_by={actor_id} AND status='REQUESTED'"
    ) == 0

    headers = {**base_headers, "X-Step-Up-Token": token}
    created = real_db_client.post(path, headers=headers, json=payload)
    assert created.status_code == 202, created.json()
    replayed = real_db_client.post(path, headers=headers, json=payload)
    assert replayed.status_code == 202
    assert replayed.json() == created.json()
    export_id = created.json()["export_id"]
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.personal_data_export_request "
        f"WHERE export_id='{export_id}'"
    ) == 1
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.service_fulfillment_receipt "
        f"WHERE actor_scope='{actor_id}' AND operation='CREATE_EXPORT' "
        "AND idempotency_key='slice7-http-export-create-0001'"
    ) == 1

    assert asyncio.run(_generate_export(UUID(export_id))) == "READY"
    access = real_db_client.post(
        f"{path}/{export_id}/download-access",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Step-Up-Token": token,
            "Idempotency-Key": "slice7-http-export-access-0001",
        },
        json={"reason": "PERSONAL_ARCHIVE"},
    )
    assert access.status_code == 200, access.json()
    assert access.headers["Cache-Control"] == "no-store"
    assert access.json()["content_type"] == "application/zip"
    content_path = f"/api/v1/private-files/{export_id}/content"
    download = real_db_client.get(
        content_path,
        headers={"Authorization": f"Bearer {token}"},
        params={"token": access.json()["access_token"]},
    )
    assert download.status_code == 200
    assert download.headers["Cache-Control"] == "no-store"
    assert download.headers["content-type"].startswith("application/zip")
    with ZipFile(BytesIO(download.content)) as archive:
        assert archive.namelist() == ["manifest.json", "data/assessment.json"]

    replay = real_db_client.get(
        content_path,
        headers={"Authorization": f"Bearer {token}"},
        params={"token": access.json()["access_token"]},
    )
    assert replay.status_code == 403
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.personal_data_export_download_access "
        f"WHERE export_id='{export_id}' AND consumed_at IS NOT NULL"
    ) == 1


def test_D31_D40_导出超限明确失败且无私有文件或部分写入(
    pg_database,
    slice7_seeded,
) -> None:
    seeded = slice7_seeded

    async def exercise() -> UUID:
        transfer_engine = create_async_engine(
            os.environ["KG_TEST_SLICE7_TRANSFER_WRITER_DATABASE_URL"],
            pool_pre_ping=True,
        )
        export_engine = create_async_engine(
            os.environ["KG_TEST_SLICE7_EXPORT_WORKER_DATABASE_URL"],
            pool_pre_ping=True,
        )
        try:
            async with AsyncSession(transfer_engine, expire_on_commit=False) as session:
                requested = await ServiceFulfillmentService(
                    ServiceFulfillmentRepository(session),
                    SystemBusinessClock(),
                    Uuid7Generator().generate,
                ).export_transition(
                    operation="CREATE_EXPORT",
                    export_or_member_id=UUID(str(seeded["subject_member_id"])),
                    actor_user_id=int(seeded["family_actor_id"]),
                    actor_role="member",
                    actor_tenant_id=None,
                    idempotency_key="slice7-db-export-too-large-0001",
                    request={
                        "requested_scope": ("ASSESSMENT",),
                        "reason": "PERSONAL_ARCHIVE",
                    },
                )
                await session.commit()
            export_id = UUID(str(requested["export_id"]))
            worker_id = "bounded-export-worker"
            async with AsyncSession(export_engine, expire_on_commit=False) as session:
                repository = ServiceFulfillmentRepository(session)
                claimed = await repository.claim_export(export_id, worker_id)
                assert claimed is not None
                failed = await repository.fail_export(
                    {
                        "export_id": export_id,
                        "worker_id": worker_id,
                        "failure_code": "EXPORT_ARCHIVE_TOO_LARGE",
                        "evidence_digest": "0" * 64,
                        "audit_id": Uuid7Generator().generate(),
                        "event_id": Uuid7Generator().generate(),
                        "occurred_at": datetime.now(timezone.utc),
                    }
                )
                await session.commit()
                assert failed["status"] == "FAILED"
            return export_id
        finally:
            await transfer_engine.dispose()
            await export_engine.dispose()

    export_id = asyncio.run(exercise())
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.private_file WHERE file_id='{export_id}'"
    ) == 0
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.personal_data_export_artifact "
        f"WHERE export_id='{export_id}'"
    ) == 0
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.service_fulfillment_audit "
        f"WHERE target_id='{export_id}' AND action='EXPORT_FAILED'"
    ) == 1
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.service_fulfillment_outbox "
        f"WHERE aggregate_ref='{export_id}' AND event_type='EXPORT_FAILED'"
    ) == 1


def test_0032非空降级在任何破坏性DDL前fail_closed(pg_database) -> None:
    config = _build_alembic_config(_get_test_database_url())
    with pytest.raises(RuntimeError, match="Slice 7 downgrade requires empty module tables"):
        command.downgrade(config, "20260826_0031")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260827_0032"
    assert pg_database.fetch_value(
        "SELECT to_regprocedure('public.slice7_export_private_file_register_v1(jsonb)')"
    ) is not None
