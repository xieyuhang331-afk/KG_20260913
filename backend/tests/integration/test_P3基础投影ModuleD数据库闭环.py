import asyncio
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest
from alembic import command

from app.core.config import get_settings
from app.core.database import dispose_projection_runtime
from app.modules.projection_read.domain import (
    HealthReadGrant, OrganizationReadGrant, ProjectionAccessDenied,
    ProjectionGenerationUnavailable, ProjectionIndicatorDenied,
    ProjectionReadPrincipal, ProjectionScopeDenied,
)
from app.modules.projection_read.service import (
    HealthProjectionReadService, OrganizationProjectionReadService,
)
from tests.integration.conftest import (
    PgDatabase, _build_alembic_config, _get_role_database_url,
    _get_test_database_url,
)


pytestmark = pytest.mark.integration
VIEWS = (
    "organization_ready_projection_generation_v1", "organization_ready_projection_v1",
    "health_ready_projection_generation_v1", "health_ready_projection_fact_v1",
    "health_ready_projection_window_selection_v1",
)
CONFIGURATION_ERROR = "projection reader runtime role configuration is invalid"
SLICE2_CONFIGURATION_ERROR = "Slice 2 database role configuration is invalid"
SLICE4_CONFIGURATION_ERROR = "Slice 4 database role configuration is invalid"


class _Policy:
    def __init__(
        self, *, roots=(1,), tenant_id=7, subject_user_id=9,
        indicators=("heart_rate",),
    ):
        self.roots = roots
        self.tenant_id = tenant_id
        self.subject_user_id = subject_user_id
        self.indicators = indicators

    async def authorize_organization_read(self, **kwargs):
        return OrganizationReadGrant(
            tenant_id=self.tenant_id,
            allowed_root_organization_ids=self.roots,
        )

    async def authorize_health_read(self, **kwargs):
        return HealthReadGrant(
            tenant_id=self.tenant_id, subject_user_id=self.subject_user_id,
            allowed_indicator_codes=self.indicators, authorization_basis="SELF",
        )


async def _seed_ready_projection(connection):
    digest = "a" * 64
    result_digest = "A" * 64
    organization_generation = await connection.fetchval(
        "INSERT INTO public.organization_projection_generation "
        "(projection_version,generation_no,status,high_watermark,digest_key_id,input_digest,start_operation_id,builder_id,lease_epoch,lease_expires_at,completed_at,version) "
        "VALUES (1,7001,'BUILD_COMPLETE','{\"max_organization_id\":6}'::jsonb,'k1',$1,$2,NULL,0,NULL,now(),1) RETURNING id",
        digest, uuid4(),
    )
    health_generation = await connection.fetchval(
        "INSERT INTO public.health_projection_generation "
        "(projection_version,generation_no,status,high_watermark,digest_key_id,input_digest,start_operation_id,builder_id,lease_epoch,lease_expires_at,completed_at,version) "
        "VALUES (1,7001,'BUILD_COMPLETE','{\"max_fact_id\":3,\"source_snapshot\":\"1:1:\"}'::jsonb,'k1',$1,$2,NULL,0,NULL,now(),1) RETURNING id",
        digest, uuid4(),
    )
    nonready_generation = await connection.fetchval(
        "INSERT INTO public.organization_projection_generation "
        "(projection_version,generation_no,status,high_watermark,digest_key_id,input_digest,start_operation_id,builder_id,lease_epoch,lease_expires_at,completed_at,version) "
        "VALUES (1,7002,'BUILD_COMPLETE','{\"max_organization_id\":0}'::jsonb,'k1',$1,$2,NULL,0,NULL,now(),1) RETURNING id",
        digest, uuid4(),
    )
    organization_run = uuid4()
    health_run = uuid4()
    await connection.execute(
        "INSERT INTO public.organization_projection_shadow_run "
        "(run_id,generation_id,run_sequence,status,projection_version,rule_version,high_watermark,high_watermark_digest,digest_key_id,generation_input_digest,source_digest,mapping_digest,projection_digest,coverage_digest,evidence_digest,blocker_count,review_required_count,informational_count,category_counts,source_count,eligible_count,projection_count,coverage_numerator,coverage_denominator,start_operation_id,complete_operation_id,validator_id,lease_epoch,lease_expires_at,completed_at,version) "
        "VALUES ($1,$2,2,'PASSED',1,'organization-shadow-v1','{\"max_organization_id\":6}'::jsonb,$3,'k1',$4,$3,$3,$3,$3,$3,0,0,0,'{}'::jsonb,3,3,3,3,3,$5,$6,$7,0,NULL,now(),1)",
        organization_run, organization_generation, result_digest, digest,
        uuid4(), uuid4(), uuid4(),
    )
    await connection.execute(
        "INSERT INTO public.health_projection_shadow_run "
        "(run_id,generation_id,run_sequence,status,projection_version,rule_version,high_watermark,high_watermark_digest,digest_key_id,generation_input_digest,source_digest,mapping_digest,projection_digest,coverage_digest,evidence_digest,currentness_digest,selection_digest,blocker_count,review_required_count,informational_count,category_counts,source_count,current_fact_count,projection_fact_count,expected_selection_count,actual_selection_count,fact_coverage_numerator,fact_coverage_denominator,selection_coverage_numerator,selection_coverage_denominator,start_operation_id,complete_operation_id,validator_id,lease_epoch,lease_expires_at,completed_at,version) "
        "VALUES ($1,$2,2,'PASSED',1,'health-shadow-v1','{\"max_fact_id\":3,\"source_snapshot\":\"1:1:\"}'::jsonb,$3,'k1',$4,$3,$3,$3,$3,$3,$3,$3,0,0,0,'{}'::jsonb,3,3,3,3,3,3,3,3,3,$5,$6,$7,0,NULL,now(),1)",
        health_run, health_generation, result_digest, digest,
        uuid4(), uuid4(), uuid4(),
    )
    await connection.execute(
        "UPDATE public.organization_projection_generation SET status='READY',current_shadow_run_id=$1,shadow_success_count=2,ready_at=now(),ready_operation_id=$2,version=2 WHERE id=$3",
        organization_run, uuid4(), organization_generation,
    )
    await connection.execute(
        "UPDATE public.health_projection_generation SET status='READY',current_shadow_run_id=$1,shadow_success_count=2,ready_at=now(),ready_operation_id=$2,version=2 WHERE id=$3",
        health_run, uuid4(), health_generation,
    )
    for organization_id in (4, 5, 6):
        await connection.execute(
            "INSERT INTO public.organization_projection "
            "(generation_id,organization_id,parent_id,org_code,org_name,org_type,status,sort_order,source_version,path_ids,path_codes,path_versions,compatibility_mode,scope_eligible,row_digest,digest_key_id) "
            "VALUES ($1,$2,3,$3,$4,'county','active',$8,1,$5::jsonb,$6::jsonb,'[1,1,1,1]'::jsonb,'canonical',true,$7,'k1')",
            organization_generation, organization_id, f"C-{organization_id}",
            f"county-{organization_id}", f"[1,2,3,{organization_id}]",
            f'["HQ","P","CITY","C-{organization_id}"]', digest, organization_id,
        )
    await connection.execute(
        "INSERT INTO public.organization_projection "
        "(generation_id,organization_id,parent_id,org_code,org_name,org_type,status,sort_order,source_version,path_ids,path_codes,path_versions,compatibility_mode,scope_eligible,row_digest,digest_key_id) "
        "VALUES ($1,7,3,'C-7','inactive-county','county','inactive',7,1,'[1,2,3,7]'::jsonb,'[\"HQ\",\"P\",\"CITY\",\"C-7\"]'::jsonb,'[1,1,1,1]'::jsonb,'canonical',false,$2,'k1')",
        organization_generation, digest,
    )
    measured = (
        datetime(2026, 8, 12, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 13, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 14, 1, tzinfo=timezone.utc),
    )
    for fact_id, measured_at in enumerate(measured, 1):
        business_day = measured_at.date()
        await connection.execute(
            "INSERT INTO public.health_projection_fact "
            "(generation_id,fact_id,subject_user_id,indicator_code,numeric_value,unit,measured_at,received_at,source_type,business_day,window_start_utc,window_end_utc,row_digest,digest_key_id) "
            "VALUES ($1,$2,9,'heart_rate',$3,'bpm',$4,$4,'DEVICE',$5,$6,$7,$8,'k1')",
            health_generation, fact_id, Decimal(60 + fact_id), measured_at,
            business_day,
            datetime.combine(business_day, datetime.min.time(), timezone.utc) - timedelta(hours=8),
            datetime.combine(business_day + timedelta(days=1), datetime.min.time(), timezone.utc) - timedelta(hours=8),
            digest,
        )
        await connection.execute(
            "INSERT INTO public.health_projection_window_selection "
            "(generation_id,subject_user_id,indicator_code,business_day,winner_fact_id,rule_version,selection_digest,digest_key_id) "
            "VALUES ($1,9,'heart_rate',$2,$3,'health-daily-selection-v1',$4,'k1')",
            health_generation, business_day, fact_id, digest,
        )
    return (
        organization_generation, health_generation, organization_run, health_run,
        nonready_generation,
    )


async def _remove_ready_projection(connection, values):
    (
        organization_generation, health_generation, organization_run, health_run,
        nonready_generation,
    ) = values
    await connection.execute(
        "UPDATE public.organization_projection_generation SET status='BUILD_COMPLETE',current_shadow_run_id=NULL,shadow_success_count=0,ready_at=NULL,ready_operation_id=NULL,version=3 WHERE id=$1",
        organization_generation,
    )
    await connection.execute(
        "UPDATE public.health_projection_generation SET status='BUILD_COMPLETE',current_shadow_run_id=NULL,shadow_success_count=0,ready_at=NULL,ready_operation_id=NULL,version=3 WHERE id=$1",
        health_generation,
    )
    await connection.execute("DELETE FROM public.organization_projection_shadow_run WHERE run_id=$1", organization_run)
    await connection.execute("DELETE FROM public.health_projection_shadow_run WHERE run_id=$1", health_run)
    await connection.execute(
        "DELETE FROM public.health_projection_window_selection WHERE generation_id=$1",
        health_generation,
    )
    await connection.execute("DELETE FROM public.organization_projection_generation WHERE id=$1", organization_generation)
    await connection.execute("DELETE FROM public.health_projection_generation WHERE id=$1", health_generation)
    await connection.execute(
        "DELETE FROM public.organization_projection_generation WHERE id=$1",
        nonready_generation,
    )


def test_Module_D五个READY视图与最小权限闭环(pg_database):
    assert pg_database.fetch_value("SELECT version_num='20260915_0048' FROM alembic_version")
    assert pg_database.fetch_column(
        "SELECT relname FROM pg_class WHERE relnamespace='public'::regnamespace "
        "AND relname IN ("
        "'organization_ready_projection_generation_v1',"
        "'organization_ready_projection_v1',"
        "'health_ready_projection_generation_v1',"
        "'health_ready_projection_fact_v1',"
        "'health_ready_projection_window_selection_v1'"
        ") ORDER BY relname"
    ) == sorted(VIEWS)
    org = os.environ["KG_TEST_ORGANIZATION_PROJECTION_READER_ROLE"]
    health = os.environ["KG_TEST_HEALTH_PROJECTION_READER_ROLE"]
    app = os.environ["KG_TEST_APPLICATION_ROLE"]
    readonly = os.environ["KG_TEST_READONLY_ROLE"]
    for view in VIEWS:
        expected = org if view.startswith("organization_") else health
        other = health if expected == org else org
        assert pg_database.fetch_value(f"SELECT has_any_column_privilege('{expected}','public.{view}','SELECT')")
        assert not pg_database.fetch_value(f"SELECT has_any_column_privilege('{other}','public.{view}','SELECT')")
        assert not pg_database.fetch_value(f"SELECT has_any_column_privilege('{app}','public.{view}','SELECT')")
        assert not pg_database.fetch_value(f"SELECT has_any_column_privilege('{readonly}','public.{view}','SELECT')")
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            assert not pg_database.fetch_value(f"SELECT has_table_privilege('{expected}','public.{view}','{privilege}')")
    for role in (org, health):
        for relation in ("organization_projection", "health_projection_fact", "canonical_health_fact", "operation_log", "organization_projection_shadow_audit"):
            assert not pg_database.fetch_value(f"SELECT has_any_column_privilege('{role}','public.{relation}','SELECT')")


def test_Module_D_reader身份只能读取本领域READY表面(pg_database):
    async def verify():
        org = await asyncpg.connect(PgDatabase(_get_role_database_url("KG_TEST_ORGANIZATION_PROJECTION_READER_DATABASE_URL")).database_url)
        health = await asyncpg.connect(PgDatabase(_get_role_database_url("KG_TEST_HEALTH_PROJECTION_READER_DATABASE_URL")).database_url)
        try:
            assert await org.fetchval("SELECT count(*) FROM public.organization_ready_projection_generation_v1") >= 0
            assert await health.fetchval("SELECT count(*) FROM public.health_ready_projection_generation_v1") >= 0
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await org.fetchval("SELECT count(*) FROM public.health_ready_projection_fact_v1")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await health.fetchval("SELECT count(*) FROM public.organization_ready_projection_v1")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await org.fetchval("SELECT count(*) FROM public.organization_projection")
            with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.ObjectNotInPrerequisiteStateError)):
                await health.execute("DELETE FROM public.health_ready_projection_fact_v1")
        finally:
            await org.close(); await health.close()

    asyncio.run(verify())


def test_Module_D真实ServiceRepositoryRuntime_READY授权与三类分页闭环(pg_database, monkeypatch):
    monkeypatch.setenv(
        "KG_ORGANIZATION_PROJECTION_READER_DATABASE_URL",
        _get_role_database_url("KG_TEST_ORGANIZATION_PROJECTION_READER_DATABASE_URL"),
    )
    monkeypatch.setenv(
        "KG_HEALTH_PROJECTION_READER_DATABASE_URL",
        _get_role_database_url("KG_TEST_HEALTH_PROJECTION_READER_DATABASE_URL"),
    )
    monkeypatch.setenv(
        "KG_ORGANIZATION_PROJECTION_READER_ROLE",
        os.environ["KG_TEST_ORGANIZATION_PROJECTION_READER_ROLE"],
    )
    monkeypatch.setenv(
        "KG_HEALTH_PROJECTION_READER_ROLE",
        os.environ["KG_TEST_HEALTH_PROJECTION_READER_ROLE"],
    )
    get_settings.cache_clear()

    async def verify():
        owner = await asyncpg.connect(pg_database.database_url)
        values = None
        try:
            values = await _seed_ready_projection(owner)
            organization_generation, health_generation, _, _, nonready_generation = values
            principal = ProjectionReadPrincipal(1, 7, "platform", "request")
            organization = OrganizationProjectionReadService(_Policy())
            health = HealthProjectionReadService(_Policy())

            node = await organization.get_node(
                generation_id=organization_generation, organization_id=4,
                principal=principal,
            )
            assert node.organization_id == 4 and node.scope_eligible
            first = await organization.list_children(
                generation_id=organization_generation, parent_id=3,
                cursor=None, limit=2, principal=principal,
            )
            second = await organization.list_children(
                generation_id=organization_generation, parent_id=3,
                cursor=first.next_cursor, limit=2, principal=principal,
            )
            assert [item.organization_id for item in first.items + second.items] == [4, 5, 6]

            facts1 = await health.list_current_facts(
                generation_id=health_generation, subject_user_id=9,
                indicator_codes=("heart_rate",),
                measured_from=datetime(2026, 8, 1, tzinfo=timezone.utc),
                measured_to=datetime(2026, 8, 20, tzinfo=timezone.utc),
                cursor=None, limit=2, principal=principal,
            )
            facts2 = await health.list_current_facts(
                generation_id=health_generation, subject_user_id=9,
                indicator_codes=("heart_rate",),
                measured_from=datetime(2026, 8, 1, tzinfo=timezone.utc),
                measured_to=datetime(2026, 8, 20, tzinfo=timezone.utc),
                cursor=facts1.next_cursor, limit=2, principal=principal,
            )
            assert [item.fact_id for item in facts1.items + facts2.items] == [1, 2, 3]

            selections1 = await health.list_daily_selections(
                generation_id=health_generation, subject_user_id=9,
                indicator_codes=("heart_rate",),
                business_day_from=date(2026, 8, 1), business_day_to=date(2026, 8, 20),
                cursor=None, limit=2, principal=principal,
            )
            selections2 = await health.list_daily_selections(
                generation_id=health_generation, subject_user_id=9,
                indicator_codes=("heart_rate",),
                business_day_from=date(2026, 8, 1), business_day_to=date(2026, 8, 20),
                cursor=selections1.next_cursor, limit=2, principal=principal,
            )
            assert [item.winner_fact_id for item in selections1.items + selections2.items] == [1, 2, 3]

            with pytest.raises(ProjectionGenerationUnavailable):
                await organization.get_node(
                    generation_id=organization_generation + 100000,
                    organization_id=4, principal=principal,
                )
            with pytest.raises(ProjectionGenerationUnavailable):
                await organization.get_node(
                    generation_id=nonready_generation,
                    organization_id=4, principal=principal,
                )
            with pytest.raises(ProjectionScopeDenied):
                await organization.get_node(
                    generation_id=organization_generation,
                    organization_id=999, principal=principal,
                )
            with pytest.raises(ProjectionScopeDenied):
                await organization.get_node(
                    generation_id=organization_generation,
                    organization_id=7, principal=principal,
                )
            with pytest.raises(ProjectionScopeDenied):
                await OrganizationProjectionReadService(
                    _Policy(roots=(999,))
                ).get_node(
                    generation_id=organization_generation,
                    organization_id=4, principal=principal,
                )
            with pytest.raises(ProjectionAccessDenied):
                await HealthProjectionReadService(
                    _Policy(subject_user_id=10)
                ).list_current_facts(
                    generation_id=health_generation, subject_user_id=9,
                    indicator_codes=("heart_rate",),
                    measured_from=datetime(2026, 8, 1, tzinfo=timezone.utc),
                    measured_to=datetime(2026, 8, 20, tzinfo=timezone.utc),
                    cursor=None, limit=2, principal=principal,
                )
            with pytest.raises(ProjectionIndicatorDenied):
                await HealthProjectionReadService(_Policy()).list_current_facts(
                    generation_id=health_generation, subject_user_id=9,
                    indicator_codes=("spo2",),
                    measured_from=datetime(2026, 8, 1, tzinfo=timezone.utc),
                    measured_to=datetime(2026, 8, 20, tzinfo=timezone.utc),
                    cursor=None, limit=2, principal=principal,
                )
        finally:
            await dispose_projection_runtime("organization_reader")
            await dispose_projection_runtime("health_reader")
            if values is not None:
                await _remove_ready_projection(owner, values)
            await owner.close()
            get_settings.cache_clear()

    asyncio.run(verify())


def test_Module_D_downgrade事务锁覆盖commit_rollback与cancellation(pg_database):
    lock_key = 4341875042858344256
    config = _build_alembic_config(_get_test_database_url())

    async def wait_for_waiter(connection, mode, operation=None):
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        deadline = started_at + 30.0
        while loop.time() < deadline:
            if operation is not None and operation.done():
                await operation
            count = await connection.fetchval(
                "SELECT count(*) FROM pg_locks WHERE locktype='advisory' "
                "AND NOT granted AND mode=$1",
                mode,
            )
            if count:
                return
            await asyncio.sleep(0.025)
        if operation is not None and operation.done():
            await operation
        waited_seconds = loop.time() - started_at
        raise AssertionError(
            "advisory lock waiter was not observed: "
            f"mode={mode}, operation_done="
            f"{operation.done() if operation is not None else None}, "
            f"waited_seconds={waited_seconds:.3f}"
        )

    async def verify():
        organization_url = PgDatabase(
            _get_role_database_url("KG_TEST_ORGANIZATION_PROJECTION_READER_DATABASE_URL")
        ).database_url
        health_url = PgDatabase(
            _get_role_database_url("KG_TEST_HEALTH_PROJECTION_READER_DATABASE_URL")
        ).database_url
        old_reader = await asyncpg.connect(organization_url)
        waiting_reader = await asyncpg.connect(health_url)
        monitor = await asyncpg.connect(pg_database.database_url)
        old_transaction = old_reader.transaction()
        waiting_transaction = waiting_reader.transaction()
        await old_transaction.start()
        await waiting_transaction.start()
        downgraded = False
        try:
            await old_reader.fetchval("SELECT pg_advisory_xact_lock_shared($1)", lock_key)
            downgrade = asyncio.create_task(
                asyncio.to_thread(command.downgrade, config, "20260814_0018")
            )
            await wait_for_waiter(monitor, "ExclusiveLock", downgrade)
            waiting_lock = asyncio.create_task(
                waiting_reader.fetchval("SELECT pg_advisory_xact_lock_shared($1)", lock_key)
            )
            await wait_for_waiter(monitor, "ShareLock")
            await old_transaction.commit()
            await downgrade
            downgraded = True
            await waiting_lock
            with pytest.raises(
                (asyncpg.UndefinedTableError, asyncpg.InsufficientPrivilegeError)
            ):
                await waiting_reader.fetchval(
                    "SELECT count(*) FROM public.health_ready_projection_generation_v1"
                )
            await waiting_transaction.rollback()
            await asyncio.to_thread(command.upgrade, config, "head")
            downgraded = False

            migration = await asyncpg.connect(pg_database.database_url)
            migration_transaction = migration.transaction()
            await migration_transaction.start()
            try:
                await migration.fetchval("SELECT pg_advisory_xact_lock($1)", lock_key)
                cancelled_read = asyncio.create_task(
                    OrganizationProjectionReadService(_Policy()).get_node(
                        generation_id=999999, organization_id=4,
                        principal=ProjectionReadPrincipal(
                            1, 7, "platform", "cancelled-request"
                        ),
                    )
                )
                await wait_for_waiter(monitor, "ShareLock")
                cancelled_read.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await cancelled_read
                await migration_transaction.rollback()
            finally:
                if migration.is_in_transaction():
                    await migration_transaction.rollback()
                await migration.close()
                await dispose_projection_runtime("organization_reader")

            migration = await asyncpg.connect(pg_database.database_url)
            reader_after_rollback = await asyncpg.connect(organization_url)
            migration_transaction = migration.transaction()
            reader_transaction = reader_after_rollback.transaction()
            await migration_transaction.start()
            await reader_transaction.start()
            try:
                await migration.fetchval("SELECT pg_advisory_xact_lock($1)", lock_key)
                await migration.execute(
                    "DROP VIEW public.organization_ready_projection_generation_v1"
                )
                reader_lock = asyncio.create_task(
                    reader_after_rollback.fetchval(
                        "SELECT pg_advisory_xact_lock_shared($1)", lock_key
                    )
                )
                await wait_for_waiter(monitor, "ShareLock")
                await migration_transaction.rollback()
                await reader_lock
                assert await reader_after_rollback.fetchval(
                    "SELECT count(*) FROM public.organization_ready_projection_generation_v1"
                ) >= 0
                await reader_transaction.rollback()
            finally:
                if migration.is_in_transaction():
                    await migration_transaction.rollback()
                if reader_after_rollback.is_in_transaction():
                    await reader_transaction.rollback()
                await migration.close()
                await reader_after_rollback.close()

            migration = await asyncpg.connect(pg_database.database_url)
            reader_after_cancel = await asyncpg.connect(organization_url)
            entered = asyncio.Event()

            async def cancelled_migration():
                transaction = migration.transaction()
                await transaction.start()
                try:
                    await migration.fetchval("SELECT pg_advisory_xact_lock($1)", lock_key)
                    await migration.execute(
                        "DROP VIEW public.organization_ready_projection_generation_v1"
                    )
                    entered.set()
                    await asyncio.Future()
                finally:
                    await transaction.rollback()

            cancellation = asyncio.create_task(cancelled_migration())
            await entered.wait()
            reader_transaction = reader_after_cancel.transaction()
            await reader_transaction.start()
            reader_lock = asyncio.create_task(
                reader_after_cancel.fetchval(
                    "SELECT pg_advisory_xact_lock_shared($1)", lock_key
                )
            )
            await wait_for_waiter(monitor, "ShareLock")
            cancellation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancellation
            await reader_lock
            assert await reader_after_cancel.fetchval(
                "SELECT count(*) FROM public.organization_ready_projection_generation_v1"
            ) >= 0
            await reader_transaction.rollback()
            await migration.close()
            await reader_after_cancel.close()
        finally:
            if old_reader.is_in_transaction():
                await old_transaction.rollback()
            if waiting_reader.is_in_transaction():
                await waiting_transaction.rollback()
            await old_reader.close()
            await waiting_reader.close()
            await monitor.close()
            if downgraded:
                await asyncio.to_thread(command.upgrade, config, "head")

    asyncio.run(verify())


def test_Module_D真实运行身份与membership预检失败保持零DDL(pg_database, monkeypatch):
    config = _build_alembic_config(_get_test_database_url())
    admin_url = PgDatabase(os.environ["KG_TEST_ROLE_ADMIN_DATABASE_URL"]).database_url
    organization_reader = os.environ["KG_TEST_ORGANIZATION_PROJECTION_READER_ROLE"]
    health_reader = os.environ["KG_TEST_HEALTH_PROJECTION_READER_ROLE"]
    writer = os.environ["KG_TEST_VERIFICATION_WRITER_ROLE"]
    writer_url = os.environ["KG_TEST_VERIFICATION_WRITER_DATABASE_URL"]
    reader_url = os.environ["KG_TEST_ORGANIZATION_PROJECTION_READER_DATABASE_URL"]
    intermediate = f"kg_role_path_{os.environ['KG_TEST_RUN_ID']}"
    intermediate_two = f"kg_role_path_two_{os.environ['KG_TEST_RUN_ID']}"
    external = f"kg_external_role_{os.environ['KG_TEST_RUN_ID']}"

    def assert_zero_ddl():
        assert pg_database.fetch_value(
            "SELECT version_num='20260814_0018' FROM alembic_version"
        )
        assert not pg_database.fetch_value(
            "SELECT EXISTS (SELECT 1 FROM pg_class WHERE relnamespace='public'::regnamespace "
            "AND relname LIKE '%ready_projection%_v1')"
        )

    def rejected_upgrade():
        with pytest.raises(RuntimeError) as error:
            command.upgrade(config, "head")
        assert str(error.value) == CONFIGURATION_ERROR
        assert error.value.__cause__ is None
        assert_zero_ddl()

    async def admin_execute(sql):
        connection = await asyncpg.connect(admin_url)
        try:
            await connection.execute(sql)
        finally:
            await connection.close()

    command.downgrade(config, "20260814_0018")
    try:
        monkeypatch.setenv("KG_VERIFICATION_WRITER_ROLE", organization_reader)
        rejected_upgrade()
        monkeypatch.setenv("KG_VERIFICATION_WRITER_DATABASE_URL", reader_url)
        rejected_upgrade()
        monkeypatch.setenv("KG_VERIFICATION_WRITER_ROLE", writer)
        monkeypatch.delenv("KG_VERIFICATION_WRITER_DATABASE_URL")
        rejected_upgrade()
        monkeypatch.setenv("KG_VERIFICATION_WRITER_DATABASE_URL", "not-a-database-url")
        rejected_upgrade()
        monkeypatch.setenv(
            "KG_VERIFICATION_WRITER_DATABASE_URL",
            "postgresql" + "+asyncpg://localhost/database",
        )
        rejected_upgrade()
        monkeypatch.setenv("KG_VERIFICATION_WRITER_DATABASE_URL", writer_url)

        asyncio.run(admin_execute(f'CREATE ROLE "{external}" NOLOGIN NOINHERIT'))
        asyncio.run(admin_execute(f'CREATE ROLE "{intermediate}" NOLOGIN NOINHERIT'))
        try:
            asyncio.run(admin_execute(
                f'GRANT SELECT ON TABLE public.organization_projection TO "{external}"'
            ))
            asyncio.run(admin_execute(f'GRANT "{external}" TO "{organization_reader}"'))
            rejected_upgrade()
            asyncio.run(admin_execute(f'REVOKE "{external}" FROM "{organization_reader}"'))

            asyncio.run(admin_execute(f'GRANT "{organization_reader}" TO "{external}"'))
            rejected_upgrade()
            asyncio.run(admin_execute(f'REVOKE "{organization_reader}" FROM "{external}"'))

            asyncio.run(admin_execute(f'GRANT "{intermediate}" TO "{organization_reader}"'))
            asyncio.run(admin_execute(f'GRANT "{external}" TO "{intermediate}"'))
            rejected_upgrade()
            asyncio.run(admin_execute(f'REVOKE "{intermediate}" FROM "{organization_reader}"'))
            asyncio.run(admin_execute(f'REVOKE "{external}" FROM "{intermediate}"'))

            asyncio.run(admin_execute(f'GRANT "{intermediate}" TO "{external}"'))
            asyncio.run(admin_execute(f'GRANT "{organization_reader}" TO "{intermediate}"'))
            rejected_upgrade()
        finally:
            asyncio.run(admin_execute(
                f'REVOKE ALL PRIVILEGES ON TABLE public.organization_projection FROM "{external}"'
            ))
            asyncio.run(admin_execute(f'DROP ROLE "{intermediate}"'))
            asyncio.run(admin_execute(f'DROP ROLE "{external}"'))

        asyncio.run(admin_execute(f'GRANT "{writer}" TO "{organization_reader}"'))
        try:
            rejected_upgrade()
        finally:
            asyncio.run(admin_execute(f'REVOKE "{writer}" FROM "{organization_reader}"'))

        asyncio.run(admin_execute(f'GRANT "{organization_reader}" TO "{writer}"'))
        try:
            rejected_upgrade()
        finally:
            asyncio.run(admin_execute(f'REVOKE "{organization_reader}" FROM "{writer}"'))

        asyncio.run(admin_execute(f'CREATE ROLE "{intermediate}" NOLOGIN NOINHERIT'))
        asyncio.run(admin_execute(f'CREATE ROLE "{intermediate_two}" NOLOGIN NOINHERIT'))
        try:
            asyncio.run(admin_execute(f'GRANT "{intermediate}" TO "{organization_reader}"'))
            asyncio.run(admin_execute(f'GRANT "{writer}" TO "{intermediate}"'))
            rejected_upgrade()
            asyncio.run(admin_execute(f'REVOKE "{writer}" FROM "{intermediate}"'))
            asyncio.run(admin_execute(f'GRANT "{intermediate_two}" TO "{intermediate}"'))
            asyncio.run(admin_execute(f'GRANT "{writer}" TO "{intermediate_two}"'))
            rejected_upgrade()
            asyncio.run(admin_execute(f'REVOKE "{intermediate}" FROM "{organization_reader}"'))
            asyncio.run(admin_execute(f'REVOKE "{intermediate_two}" FROM "{intermediate}"'))
            asyncio.run(admin_execute(f'REVOKE "{writer}" FROM "{intermediate_two}"'))
            asyncio.run(admin_execute(f'GRANT "{intermediate}" TO "{writer}"'))
            asyncio.run(admin_execute(f'GRANT "{organization_reader}" TO "{intermediate}"'))
            rejected_upgrade()
            asyncio.run(admin_execute(f'REVOKE "{intermediate}" FROM "{writer}"'))
            asyncio.run(admin_execute(f'REVOKE "{organization_reader}" FROM "{intermediate}"'))
            asyncio.run(admin_execute(f'GRANT "{intermediate}" TO "{organization_reader}"'))
            asyncio.run(admin_execute(f'GRANT "{health_reader}" TO "{intermediate}"'))
            rejected_upgrade()
        finally:
            asyncio.run(admin_execute(f'DROP ROLE "{intermediate_two}"'))
            asyncio.run(admin_execute(f'DROP ROLE "{intermediate}"'))

        asyncio.run(admin_execute(f'GRANT "{health_reader}" TO "{organization_reader}"'))
        try:
            rejected_upgrade()
            async def assert_postgresql_rejects_cycle():
                connection = await asyncpg.connect(admin_url)
                try:
                    with pytest.raises(asyncpg.InvalidGrantOperationError):
                        await connection.execute(
                            f'GRANT "{organization_reader}" TO "{health_reader}"'
                        )
                finally:
                    await connection.close()

            asyncio.run(assert_postgresql_rejects_cycle())
        finally:
            asyncio.run(admin_execute(f'REVOKE "{health_reader}" FROM "{organization_reader}"'))

        command.upgrade(config, "head")
        assert pg_database.fetch_value(
            "SELECT version_num='20260915_0048' FROM alembic_version"
        )
        asyncio.run(admin_execute(f'GRANT "{writer}" TO "{organization_reader}"'))
        try:
            with pytest.raises(RuntimeError) as error:
                command.downgrade(config, "20260814_0018")
            assert str(error.value) == SLICE4_CONFIGURATION_ERROR
            assert pg_database.fetch_value(
                "SELECT version_num='20260915_0048' FROM alembic_version"
            )
            assert pg_database.fetch_column(
                "SELECT relname FROM pg_class WHERE relnamespace='public'::regnamespace "
                "AND relname IN ("
                "'organization_ready_projection_generation_v1',"
                "'organization_ready_projection_v1',"
                "'health_ready_projection_generation_v1',"
                "'health_ready_projection_fact_v1',"
                "'health_ready_projection_window_selection_v1'"
                ") ORDER BY relname"
            ) == sorted(VIEWS)
        finally:
            asyncio.run(admin_execute(f'REVOKE "{writer}" FROM "{organization_reader}"'))
    finally:
        if pg_database.fetch_value("SELECT version_num FROM alembic_version") != "20260915_0048":
            command.upgrade(config, "head")
