from __future__ import annotations

import asyncio
import importlib
import os
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from uuid import UUID
from zipfile import ZipFile
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.security import create_access_token
from app.core.uuid_generator import Uuid7Generator
from app.modules.service_fulfillment.domain import SyntheticBusinessClock, SystemBusinessClock
from app.modules.service_fulfillment.repository import ServiceFulfillmentRepository
from app.modules.service_fulfillment.service import (
    ServiceFulfillmentService,
    decode_page_cursor,
    encode_page_cursor,
)
from app.modules.private_file.repository import PrivateFileRepository
from app.modules.private_file.service import (
    authorize_generated_export_access,
    read_authorized_content,
)
from app.modules.private_file.storage import LocalFilesystemAdapter
from app.tasks.slice7_service_fulfillment_tasks import _generate_export, _recover
from tests.integration.conftest import _build_alembic_config, _get_test_database_url
from tests.integration.test_一期切片3会员CurrentnessAuthority真实HTTP合同 import (
    _assert_error_response_dto,
)


pytestmark = pytest.mark.integration


WORKFLOW = (
    Path(__file__).resolve().parents[3]
    / ".github"
    / "workflows"
    / "p2-foundation-ci.yml"
)


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
    "service_transfer_continuation_handoff",
    "proxy_major_authorization",
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
    try:
        command.downgrade(config, "20260826_0031")
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
    finally:
        command.upgrade(config, "head")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260906_0039"


def test_0032单一Head且对象和六身份ACL闭合(pg_database) -> None:
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260906_0039"
    actual = set(pg_database.fetch_column(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' "
        "AND (tablename LIKE ANY(ARRAY['service_%','personal_data_export_%']) "
        "OR tablename='proxy_major_authorization')"
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
        os.environ["KG_TEST_SLICE7_OVERSIGHT_READER_ROLE"]: "slice7_read_many_v1(varchar,uuid,bigint,varchar,uuid,uuid,bigint,varchar,varchar)",
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
        "slice7_export_ready_confirm_v1(jsonb)",
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
    download_functions = {
        "slice7_export_download_consume_v1(jsonb)",
        "slice7_export_download_confirm_v1(jsonb)",
    }
    for download_function in download_functions:
        assert pg_database.fetch_value(
            f"SELECT has_function_privilege('{transfer_role}','public.{download_function}','EXECUTE')"
        )
        for role in roles - {transfer_role} | {"public"}:
            assert not pg_database.fetch_value(
                f"SELECT has_function_privilege('{role}','public.{download_function}','EXECUTE')"
            )


def _seed_active_plan(
    pg_database,
    slice6_institution_writer_database,
    *,
    ordinal: int | None = None,
    accepted_at: datetime | None = None,
) -> dict[str, object]:
    seed_module = importlib.import_module(
        "tests.integration.test_一期切片6方案生成审核确认数据库闭环"
    )
    ordinal = ordinal or int(os.getenv("KG_SLICE7_TEST_ORDINAL", "77"))
    seeded = seed_module._seed_ready_case(pg_database, ordinal=ordinal)
    request_id = f"019c7000-0000-7000-8000-{ordinal:012d}"
    plan_id = f"019c7001-0000-7000-8000-{ordinal:012d}"
    accepted_at_value = (
        accepted_at or datetime(2026, 8, 26, 8, 0, tzinfo=timezone.utc)
    ).isoformat()
    pg_database.execute(
        "BEGIN;"
        "INSERT INTO public.health_plan_generation_request("
        "request_id,service_case_id,subject_member_id,tenant_id,assessment_id,assessment_version,"
        "assembly_id,template_version_id,template_version,status,current_plan_id,failure_code,"
        "authority_digest,initiated_by,initiated_role,created_at,updated_at,version) VALUES ("
        f"'{request_id}','{seeded['case_id']}','{seeded['subject_member_id']}',{seeded['tenant_id']},"
        f"'{seeded['assessment_id']}',3,'{seeded['assembly_id']}','{seeded['template_id']}',1,"
        f"'USER_DECISION_PENDING','{plan_id}',NULL,decode(repeat('ab',32),'hex'),{seeded['org_actor_id']},"
        "'org_admin',now(),now(),1);"
        "INSERT INTO public.health_plan_version("
        "plan_id,request_id,service_case_id,subject_member_id,tenant_id,version_no,template_version_id,"
        "assessment_id,status,content,content_digest,supersedes_plan_id,created_at,updated_at,version) VALUES ("
        f"'{plan_id}','{request_id}','{seeded['case_id']}','{seeded['subject_member_id']}',"
        f"{seeded['tenant_id']},1,'{seeded['template_id']}','{seeded['assessment_id']}',"
        "'USER_DECISION_PENDING','{}'::jsonb,decode(repeat('cd',32),'hex'),NULL,now(),now(),1);"
        "COMMIT;"
    )
    decision_payload = {
        "decision_id": f"019c7002-0000-7000-8000-{ordinal:012d}",
        "plan_id": plan_id,
        "decision": "ACCEPT",
        "expected_version": 1,
        "actor_user_id": seeded["family_actor_id"],
        "actor_role": "member",
        "actor_context": "AUTO",
        "proxy_grant_id": None,
        "idempotency_key": f"slice7-plan-accept-{ordinal}",
        "request_digest": "ce" * 32,
        "audit_id": f"019c7003-0000-7000-8000-{ordinal:012d}",
        "event_id": f"019c7004-0000-7000-8000-{ordinal:012d}",
        "receipt_id": f"019c7005-0000-7000-8000-{ordinal:012d}",
        "occurred_at": accepted_at_value,
        "response": {
            "decision_id": f"019c7002-0000-7000-8000-{ordinal:012d}",
            "plan_id": plan_id,
            "decision": "ACCEPT",
            "status": "ACTIVE",
            "version": 2,
        },
        "postimage_digest": "cf" * 32,
    }
    activated = seed_module._call_json(
        slice6_institution_writer_database,
        "slice6_user_decision_v1",
        decision_payload,
    )
    replayed = seed_module._call_json(
        slice6_institution_writer_database,
        "slice6_user_decision_v1",
        decision_payload,
    )
    assert activated == replayed
    return {**seeded, "plan_id": plan_id}


@pytest.fixture(scope="module")
def slice7_seeded(pg_database, slice6_institution_writer_database) -> dict[str, object]:
    return _seed_active_plan(pg_database, slice6_institution_writer_database)


def test_D_四类列表原生UUID上界与空集合语义在正式Reader下闭合(
    pg_database,
    slice7_seeded,
) -> None:
    oversight_actor_id = 98679
    pg_database.execute(
        "INSERT INTO public.\"user\"(id,phone,password_hash,role,status,tenant_id) "
        f"VALUES ({oversight_actor_id},'00000000000','test-only','super_admin','active',NULL)"
    )

    async def exercise() -> None:
        family_engine = create_async_engine(
            os.environ["KG_TEST_SLICE7_FAMILY_READER_DATABASE_URL"],
            pool_pre_ping=True,
        )
        oversight_engine = create_async_engine(
            os.environ["KG_TEST_SLICE7_OVERSIGHT_READER_DATABASE_URL"],
            pool_pre_ping=True,
        )
        try:
            async with AsyncSession(family_engine, expire_on_commit=False) as session:
                milestones, milestone_ceiling = await ServiceFulfillmentRepository(
                    session
                ).read_many(
                    "MILESTONE",
                    UUID(str(slice7_seeded["case_id"])),
                    int(slice7_seeded["family_actor_id"]),
                    "member",
                    None,
                    None,
                    2,
                    None,
                    None,
                )
            assert milestones and milestone_ceiling is not None
            milestone_cursor = encode_page_cursor(
                UUID(str(milestones[-1]["milestone_id"])),
                snapshot_ceiling=milestone_ceiling,
                resource="MILESTONE",
                scope_id=UUID(str(slice7_seeded["case_id"])),
                actor_user_id=int(slice7_seeded["family_actor_id"]),
                actor_role="member",
                actor_tenant_id=None,
                filters={"risk": None, "status": None},
            )
            assert decode_page_cursor(
                milestone_cursor,
                resource="MILESTONE",
                scope_id=UUID(str(slice7_seeded["case_id"])),
                actor_user_id=int(slice7_seeded["family_actor_id"]),
                actor_role="member",
                actor_tenant_id=None,
                filters={"risk": None, "status": None},
            ) == (UUID(str(milestones[-1]["milestone_id"])), milestone_ceiling)

            async with AsyncSession(oversight_engine, expire_on_commit=False) as session:
                repository = ServiceFulfillmentRepository(session)
                fulfillments, fulfillment_ceiling = await repository.read_many(
                    "FULFILLMENT",
                    None,
                    oversight_actor_id,
                    "super_admin",
                    None,
                    None,
                    2,
                    None,
                    None,
                )
                assert fulfillments and fulfillment_ceiling is not None
                for resource in ("TRANSFER", "EXPORT"):
                    rows, ceiling = await repository.read_many(
                        resource,
                        None,
                        oversight_actor_id,
                        "super_admin",
                        None,
                        None,
                        2,
                        None,
                        None,
                    )
                    assert rows == []
                    assert ceiling is None
        finally:
            await family_engine.dispose()
            await oversight_engine.dispose()

    asyncio.run(exercise())


def test_D03_D05_真实Runtime原子创建五节点并幂等完成D0(
    pg_database,
    slice7_seeded,
) -> None:
    seeded = slice7_seeded

    async def exercise() -> tuple[dict, dict, dict]:
        milestone_engine = create_async_engine(
            os.environ["KG_TEST_SLICE7_MILESTONE_WRITER_DATABASE_URL"],
            pool_pre_ping=True,
        )
        family_engine = create_async_engine(
            os.environ["KG_TEST_SLICE7_FAMILY_READER_DATABASE_URL"],
            pool_pre_ping=True,
        )
        try:
            async with AsyncSession(family_engine, expire_on_commit=False) as session:
                reader = ServiceFulfillmentRepository(session)
                activated = await reader.read_one(
                    "FULFILLMENT",
                    UUID(str(seeded["case_id"])),
                    int(seeded["family_actor_id"]),
                    "member",
                )
                replayed = await reader.read_one(
                    "FULFILLMENT",
                    UUID(str(seeded["case_id"])),
                    int(seeded["family_actor_id"]),
                    "member",
                )
            assert activated is not None
            assert replayed is not None
            async with AsyncSession(milestone_engine, expire_on_commit=False) as session:
                d0 = next(item for item in activated["milestones"] if item["code"] == "D0")
                d0_start = datetime.fromisoformat(
                    f"{d0['window_start']}T12:00:00"
                ).replace(tzinfo=ZoneInfo("Asia/Shanghai"))
                service = ServiceFulfillmentService(
                    ServiceFulfillmentRepository(session),
                    SyntheticBusinessClock(d0_start),
                    Uuid7Generator().generate,
                )
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
            await milestone_engine.dispose()
            await family_engine.dispose()

    activated, replayed, completed = asyncio.run(exercise())
    assert activated == replayed
    assert len(activated["milestones"]) == 5
    assert completed["status"] == "COMPLETED"
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.service_cycle_schedule WHERE service_case_id='{seeded['case_id']}'"
    ) == 1


class _FixedClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


def test_整改A_五节点到总结ACK在正式Runtime事务内自动进入COMPLETED(
    pg_database,
    slice7_seeded,
) -> None:
    seeded = slice7_seeded

    async def exercise() -> dict:
        milestone_engine = create_async_engine(
            os.environ["KG_TEST_SLICE7_MILESTONE_WRITER_DATABASE_URL"],
            pool_pre_ping=True,
        )
        case_engine = create_async_engine(
            os.environ["KG_TEST_SLICE7_CASE_WRITER_DATABASE_URL"],
            pool_pre_ping=True,
        )
        family_engine = create_async_engine(
            os.environ["KG_TEST_SLICE7_FAMILY_READER_DATABASE_URL"],
            pool_pre_ping=True,
        )
        try:
            async with AsyncSession(family_engine, expire_on_commit=False) as session:
                current = await ServiceFulfillmentRepository(session).read_one(
                    "FULFILLMENT",
                    UUID(str(seeded["case_id"])),
                    int(seeded["family_actor_id"]),
                    "member",
                )
            assert current is not None
            for milestone in current["milestones"]:
                if milestone["status"] == "COMPLETED":
                    continue
                window_start = datetime.fromisoformat(
                    f"{milestone['window_start']}T00:00:00+00:00"
                )
                async with AsyncSession(
                    milestone_engine, expire_on_commit=False
                ) as session:
                    service = ServiceFulfillmentService(
                        ServiceFulfillmentRepository(session),
                        _FixedClock(window_start),
                        Uuid7Generator().generate,
                    )
                    await service.complete_milestone(
                        milestone_id=UUID(str(milestone["milestone_id"])),
                        actor_user_id=int(seeded["therapist_actor_id"]),
                        actor_role="therapist",
                        actor_tenant_id=int(seeded["tenant_id"]),
                        idempotency_key=f"slice7-close-{milestone['code']}",
                        expected_version=int(milestone["version"]),
                        record_summary={"EXECUTION_STATUS": "COMPLETED"},
                        evidence_refs=(),
                    )
                    await session.commit()

            transition_clock = _FixedClock(
                datetime(2026, 9, 27, 0, 0, tzinfo=timezone.utc)
            )
            async with AsyncSession(case_engine, expire_on_commit=False) as session:
                service = ServiceFulfillmentService(
                    ServiceFulfillmentRepository(session),
                    transition_clock,
                    Uuid7Generator().generate,
                )
                with pytest.raises(Exception, match="STALE_VERSION"):
                    await service.case_transition(
                        operation="CREATE_CLOSING_ASSESSMENT",
                        service_case_id=UUID(str(seeded["case_id"])),
                        actor_user_id=int(seeded["therapist_actor_id"]),
                        actor_role="therapist",
                        actor_tenant_id=int(seeded["tenant_id"]),
                        idempotency_key="slice7-closing-assessment-stale",
                        request={
                            "expected_version": 1,
                            "assessment_id": UUID(str(seeded["assessment_id"])),
                            "final_retest_evidence": ("REPORT_CLEAN",),
                        },
                    )
                await session.rollback()
                await service.case_transition(
                    operation="CREATE_CLOSING_ASSESSMENT",
                    service_case_id=UUID(str(seeded["case_id"])),
                    actor_user_id=int(seeded["therapist_actor_id"]),
                    actor_role="therapist",
                    actor_tenant_id=int(seeded["tenant_id"]),
                    idempotency_key="slice7-closing-assessment-1",
                    request={
                        "expected_version": 2,
                        "assessment_id": UUID(str(seeded["assessment_id"])),
                        "final_retest_evidence": ("REPORT_CLEAN",),
                    },
                )
                await session.commit()
                with pytest.raises(Exception, match="STALE_VERSION"):
                    await service.case_transition(
                        operation="CREATE_SUMMARY",
                        service_case_id=UUID(str(seeded["case_id"])),
                        actor_user_id=int(seeded["therapist_actor_id"]),
                        actor_role="therapist",
                        actor_tenant_id=int(seeded["tenant_id"]),
                        idempotency_key="slice7-summary-stale",
                        request={
                            "expected_version": 2,
                            "assessment_id": UUID(str(seeded["assessment_id"])),
                            "final_retest_evidence": ("REPORT_CLEAN",),
                            "milestone_outcomes": {"D28": "COMPLETED"},
                            "safety_follow_up": ("FOLLOW_UP_PRIMARY_CARE",),
                            "next_step": ("CONTINUE_MONITORING",),
                        },
                    )
                await session.rollback()
                summary = await service.case_transition(
                    operation="CREATE_SUMMARY",
                    service_case_id=UUID(str(seeded["case_id"])),
                    actor_user_id=int(seeded["therapist_actor_id"]),
                    actor_role="therapist",
                    actor_tenant_id=int(seeded["tenant_id"]),
                    idempotency_key="slice7-summary-1",
                    request={
                        "expected_version": 3,
                        "assessment_id": UUID(str(seeded["assessment_id"])),
                        "final_retest_evidence": ("REPORT_CLEAN",),
                        "milestone_outcomes": {"D28": "COMPLETED"},
                        "safety_follow_up": ("FOLLOW_UP_PRIMARY_CARE",),
                        "next_step": ("CONTINUE_MONITORING",),
                    },
                )
                await session.commit()
                with pytest.raises(Exception, match="STALE_VERSION"):
                    await service.case_transition(
                        operation="ACK_SUMMARY",
                        service_case_id=UUID(str(summary["summary_id"])),
                        actor_user_id=int(seeded["family_actor_id"]),
                        actor_role="member",
                        actor_tenant_id=None,
                        idempotency_key="slice7-summary-ack-stale",
                        request={"expected_version": 2},
                    )
                await session.rollback()
                await service.case_transition(
                    operation="ACK_SUMMARY",
                    service_case_id=UUID(str(summary["summary_id"])),
                    actor_user_id=int(seeded["family_actor_id"]),
                    actor_role="member",
                    actor_tenant_id=None,
                    idempotency_key="slice7-summary-ack-1",
                    request={"expected_version": 1},
                )
                await session.commit()

            async with AsyncSession(family_engine, expire_on_commit=False) as session:
                completed = await ServiceFulfillmentRepository(session).read_one(
                    "FULFILLMENT",
                    UUID(str(seeded["case_id"])),
                    int(seeded["family_actor_id"]),
                    "member",
                )
            assert completed is not None
            return completed
        finally:
            await milestone_engine.dispose()
            await case_engine.dispose()
            await family_engine.dispose()

    completed = asyncio.run(exercise())
    assert completed["lifecycle_status"] == "COMPLETED"
    assert completed["closing_readiness"] == "READY_TO_CLOSE"
    assert pg_database.fetch_value(
        f"SELECT status FROM public.service_case WHERE case_id='{seeded['case_id']}'"
    ) == "PREPARING"
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.service_summary_acknowledgement a JOIN public.service_summary s ON s.summary_id=a.summary_id WHERE s.service_case_id='{seeded['case_id']}'"
    ) == 1




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
    pg_database.execute(
        f'UPDATE public."user" SET status=\'suspended\' '
        f'WHERE id={int(seeded["family_actor_id"])}'
    )

    async def assert_snapshot_blocked() -> None:
        export_engine = create_async_engine(
            os.environ["KG_TEST_SLICE7_EXPORT_WORKER_DATABASE_URL"],
            pool_pre_ping=True,
        )
        try:
            async with AsyncSession(export_engine, expire_on_commit=False) as session:
                with pytest.raises(DBAPIError, match="EXPORT_LEASE_NOT_CURRENT"):
                    await ServiceFulfillmentRepository(session).export_snapshot(export_id)
        finally:
            await export_engine.dispose()

    asyncio.run(assert_snapshot_blocked())
    assert asyncio.run(_recover(recovery_cutoff)) == 0
    assert pg_database.fetch_value(
        f"SELECT status FROM public.personal_data_export_request WHERE export_id='{export_id}'"
    ) == "GENERATING"
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.service_fulfillment_audit "
        f"WHERE target_id='{export_id}' AND action='EXPORT_RECOVERY_REQUESTED'"
    ) == 0
    pg_database.execute(
        f'UPDATE public."user" SET status=\'active\' '
        f'WHERE id={int(seeded["family_actor_id"])}'
    )
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
                repository = ServiceFulfillmentRepository(session)
                authority = await repository.authority(
                    "READ_EXPORT",
                    export_id,
                    int(seeded["family_actor_id"]),
                    "member",
                    None,
                )
                assert authority is not None
                service = ServiceFulfillmentService(
                    repository,
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
                    request={
                        "reason": "PERSONAL_ARCHIVE",
                        "expected_version": int(authority["export_version"]),
                        "step_up_verified": True,
                    },
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

                    stream, mime_type = await read_authorized_content(
                        file_session,
                        None,
                        int(seeded["family_actor_id"]),
                        str(export_id),
                        token,
                        export_access_consumer=consume,
                        object_store=LocalFilesystemAdapter(tmp_path),
                    )
                    content = b"".join([chunk async for chunk in stream])
                    assert mime_type == "application/zip"
                    with pytest.raises(HTTPException) as replay:
                        await read_authorized_content(
                            file_session,
                            None,
                            int(seeded["family_actor_id"]),
                            str(export_id),
                            token,
                            export_access_consumer=consume,
                            object_store=LocalFilesystemAdapter(tmp_path),
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
    ready_evidence = pg_database.fetch_rows(
        "SELECT e.export_id,a.artifact_id,a.private_file_id,encode(a.manifest_digest,'hex') AS manifest_digest,"
        "encode(a.artifact_digest,'hex') AS artifact_digest,f.actual_size AS artifact_size,"
        "au.audit_id,o.event_id,e.ready_at AS created_at,e.expires_at "
        "FROM public.personal_data_export_request e "
        "JOIN public.personal_data_export_artifact a ON a.export_id=e.export_id "
        "JOIN public.private_file f ON f.file_id=a.private_file_id "
        "JOIN public.service_fulfillment_audit au ON au.target_id=e.export_id "
        "AND au.action='EXPORT_READY' "
        "JOIN public.service_fulfillment_outbox o ON o.aggregate_ref=e.export_id "
        "AND o.event_type='EXPORT_READY' "
        f"WHERE e.export_id='{export_id}'"
    )
    assert len(ready_evidence) == 1

    async def confirm_ready(evidence: dict[str, object]) -> tuple[bool, bool]:
        engine = create_async_engine(
            os.environ["KG_TEST_SLICE7_EXPORT_WORKER_DATABASE_URL"],
            pool_pre_ping=True,
        )
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                repository = ServiceFulfillmentRepository(session)
                confirmed = await repository.confirm_export_ready(evidence)
                altered = await repository.confirm_export_ready(
                    {**evidence, "artifact_size": int(evidence["artifact_size"]) + 1}
                )
                return confirmed, altered
        finally:
            await engine.dispose()

    confirmed, altered = asyncio.run(confirm_ready(dict(ready_evidence[0])))
    assert confirmed is True
    assert altered is False
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
    monkeypatch.setattr(
        real_db_client.app.state,
        "private_object_store",
        LocalFilesystemAdapter(tmp_path.resolve()),
    )
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
        "AND idempotency_key='slice7-http-export-create-0001' "
        f"AND target_id='{export_id}'"
    ) == 1
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.service_fulfillment_outbox "
        f"WHERE event_type='CREATE_EXPORT' AND aggregate_ref='{export_id}' "
        f"AND payload_json->>'target_id'='{export_id}'"
    ) == 1
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.service_fulfillment_audit "
        f"WHERE action='CREATE_EXPORT' AND target_id='{export_id}'"
    ) == 1

    assert asyncio.run(_generate_export(UUID(export_id))) == "READY"
    ready = real_db_client.get(
        f"{path}/{export_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ready.status_code == 200, ready.json()
    access = real_db_client.post(
        f"{path}/{export_id}/download-access",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Step-Up-Token": token,
            "Idempotency-Key": "slice7-http-export-access-0001",
        },
        json={
            "reason": "PERSONAL_ARCHIVE",
            "expected_version": ready.json()["version"],
        },
    )
    assert access.status_code == 200, access.json()
    assert access.headers["Cache-Control"] == "no-store"
    assert access.json()["content_type"] == "application/zip"
    content_path = f"/api/v1/private-files/{export_id}/content"
    original_status = pg_database.fetch_value(
        f'SELECT status FROM public."user" WHERE id={actor_id}'
    )
    try:
        pg_database.execute(
            f'UPDATE public."user" SET status=\'suspended\' WHERE id={actor_id}'
        )
        revoked = real_db_client.get(
            content_path,
            headers={
                "Authorization": f"Bearer {token}",
                "X-Private-File-Access": access.json()["access_token"],
            },
        )
        _assert_error_response_dto(
            revoked,
            status_code=401,
            error_code="ACCESS_TOKEN_STALE",
            retryable=False,
        )
        assert revoked.headers["WWW-Authenticate"] == "Bearer"
        assert revoked.headers["Cache-Control"] == "no-store, private, max-age=0"
        assert pg_database.fetch_value(
            "SELECT count(*) FROM public.personal_data_export_download_access "
            f"WHERE export_id='{export_id}' AND consumed_at IS NOT NULL"
        ) == 0
    finally:
        pg_database.execute(
            'UPDATE public."user" SET status=\'' + original_status.replace("'", "''")
            + f'\' WHERE id={actor_id}'
        )
    download = real_db_client.get(
        content_path,
        headers={
            "Authorization": f"Bearer {token}",
            "X-Private-File-Access": access.json()["access_token"],
        },
    )
    assert download.status_code == 200
    assert download.headers["Cache-Control"] == "no-store, private, max-age=0"
    assert download.headers["content-type"].startswith("application/zip")
    with ZipFile(BytesIO(download.content)) as archive:
        assert archive.namelist() == ["manifest.json", "data/assessment.json"]

    replay = real_db_client.get(
        content_path,
        headers={
            "Authorization": f"Bearer {token}",
            "X-Private-File-Access": access.json()["access_token"],
        },
    )
    assert replay.status_code == 403
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.personal_data_export_download_access "
        f"WHERE export_id='{export_id}' AND consumed_at IS NOT NULL"
    ) == 1


def test_D_真实HTTP签名游标稳定分页并拒绝篡改和Scope重绑定(
    real_db_client,
    slice7_seeded,
    pg_database,
) -> None:
    actor_id = int(slice7_seeded["family_actor_id"])
    if not pg_database.fetch_value(
        f'SELECT status=\'active\' FROM public."user" WHERE id={actor_id}'
    ):
        pytest.fail("C1_SHARED_ACTOR_NOT_ACTIVE", pytrace=False)
    token = create_access_token({"sub": str(actor_id), "role": "member"})
    headers = {"Authorization": f"Bearer {token}"}
    case_id = str(slice7_seeded["case_id"])
    path = f"/api/v1/family/service-cases/{case_id}/milestones"

    first = real_db_client.get(path, headers=headers, params={"limit": 2})
    assert first.status_code == 200, first.json()
    assert first.headers["Cache-Control"] == "no-store"
    assert len(first.json()["items"]) == 2
    first_cursor = first.json()["next_cursor"]
    assert first_cursor and case_id not in first_cursor

    second = real_db_client.get(
        path,
        headers=headers,
        params={"limit": 2, "cursor": first_cursor},
    )
    assert second.status_code == 200, second.json()
    assert len(second.json()["items"]) == 2
    assert {
        row["milestone_id"] for row in first.json()["items"]
    }.isdisjoint({row["milestone_id"] for row in second.json()["items"]})

    third = real_db_client.get(
        path,
        headers=headers,
        params={"limit": 2, "cursor": second.json()["next_cursor"]},
    )
    assert third.status_code == 200, third.json()
    assert len(third.json()["items"]) == 1
    assert third.json()["next_cursor"] is None
    all_items = (
        first.json()["items"] + second.json()["items"] + third.json()["items"]
    )

    tampered = first_cursor[:-1] + ("A" if first_cursor[-1] != "A" else "B")
    rejected = real_db_client.get(
        path,
        headers=headers,
        params={"limit": 2, "cursor": tampered},
    )
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "INVALID_REQUEST"
    rebound = real_db_client.get(
        "/api/v1/family/service-cases/0198f1c0-0000-7000-8000-0000000000ee/milestones",
        headers=headers,
        params={"limit": 2, "cursor": first_cursor},
    )
    assert rebound.status_code == 422
    assert rebound.json()["code"] == "INVALID_REQUEST"

    filter_status = max(
        {item["status"] for item in all_items},
        key=lambda value: sum(item["status"] == value for item in all_items),
    )
    assert sum(item["status"] == filter_status for item in all_items) >= 3
    filtered = real_db_client.get(
        path,
        headers=headers,
        params={"limit": 2, "status": filter_status},
    )
    assert filtered.status_code == 200, filtered.json()
    assert len(filtered.json()["items"]) == 2
    assert {item["status"] for item in filtered.json()["items"]} == {filter_status}
    filtered_cursor = filtered.json()["next_cursor"]
    assert filtered_cursor is not None
    rebound_status = "DUE" if filter_status != "DUE" else "PENDING"
    filter_rebound = real_db_client.get(
        path,
        headers=headers,
        params={"limit": 2, "status": rebound_status, "cursor": filtered_cursor},
    )
    assert filter_rebound.status_code == 422
    assert filter_rebound.json()["code"] == "INVALID_REQUEST"


def test_D31_D40_导出超限明确失败且无私有文件或部分写入(
    pg_database,
    slice7_seeded,
) -> None:
    seeded = slice7_seeded
    if not pg_database.fetch_value(
        'SELECT status=\'active\' FROM public."user" WHERE id='
        + str(seeded["family_actor_id"])
    ):
        pytest.fail("C1_SHARED_ACTOR_NOT_ACTIVE", pytrace=False)

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
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260906_0039"
    assert pg_database.fetch_value(
        "SELECT to_regprocedure('public.slice7_export_private_file_register_v1(jsonb)')"
    ) is not None
    assert pg_database.fetch_value(
        "SELECT to_regprocedure('public.slice5_rule_governance_v2(varchar,jsonb)')"
    ) is not None


def test_Slice7真实RabbitMQ合同在CI中使用隔离Worker与共享私有目录() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    rabbit_contract = "tests/integration/test_一期切片7Outbox与RabbitMQ闭环.py"

    assert f"--ignore={rabbit_contract}" in workflow
    assert workflow.count(rabbit_contract) == 2
    assert "KG_TEST_SLICE7_REAL_RABBIT=1" in workflow
    assert "KG_PRIVATE_FILE_STORAGE_ROOT=$storage_root" in workflow
    assert "-Q slice7-service-fulfillment-workflow" in workflow
    assert "python scripts/check_worker_readiness.py" in workflow
    assert "--worker-kind slice7" in workflow
    assert '--hostname "$worker_hostname"' in workflow
    assert '--destination "$worker_hostname"' not in workflow
    readiness_step = workflow.split("- name: Start independent Slice 7 Celery worker", 1)[1].split(
        "- name: Run Slice 7 RabbitMQ and independent worker contract", 1
    )[0]
    assert "if python scripts/check_worker_readiness.py" in readiness_step
    assert "exit 1" in readiness_step
    assert "pytest-slice7-rabbit-report.xml" in workflow
    assert "Stop independent Slice 7 Celery worker" in workflow
    assert "Verify Slice 7 worker and private storage cleanup" in workflow
