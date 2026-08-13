import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.integration.conftest import (
    _get_health_projection_builder_database_url,
    _get_organization_projection_builder_database_url,
    _get_projection_confirmation_database_url,
)


pytestmark = pytest.mark.integration


def _run(coro):
    return asyncio.run(coro)


def _stage(value):
    target = os.getenv("KG_SAFE_PROGRESS_FILE")
    if target:
        with open(target, "a", encoding="ascii") as stream:
            stream.write(f"STAGE_{value}\n")


async def _attributed(stage, awaitable):
    _stage(stage)
    try:
        return await awaitable
    except BaseException as exc:
        target = os.getenv("KG_SAFE_PROGRESS_FILE")
        if target:
            with open(target, "a", encoding="ascii") as stream:
                stream.write(f"PRIMARY_EXCEPTION={type(exc).__module__}.{type(exc).__name__}\n")
        raise


async def _dispose_engines(engines, primary):
    cleanup_error = None
    for engine in engines:
        try:
            await engine.dispose()
        except BaseException as exc:
            cleanup_error = cleanup_error or exc
    if primary is None and cleanup_error is not None:
        raise cleanup_error


async def _autocommit_lock_connection(engine):
    connection = await engine.connect()
    return await connection.execution_options(isolation_level="AUTOCOMMIT")


async def _seed_sources(url):
    engine = create_async_engine(url)
    primary = None
    try:
        async with engine.begin() as connection:
            await connection.execute(text("INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,sort_order,version) VALUES (9101,NULL,'HQ','HQ-B','headquarter','active',0,1),(9102,9101,'Province','P-B','province','active',0,1),(9103,9102,'City','C-B','city','active',0,1),(9104,9103,'County A','K-B-A','county','active',0,1),(9105,9103,'County B','K-B-B','county','active',1,1)"))
            await connection.execute(text("INSERT INTO public.\"user\"(id,phone,password_hash,role,status,created_at,updated_at) VALUES (9201,'18800000001','fixture','member','active',now(),now())"))
            await connection.execute(text("INSERT INTO public.canonical_health_fact(id,subject_user_id,indicator_code,catalog_version,value_kind,numeric_value,unit,measured_at,received_at,created_at,source_type,source_identity_digest,producer_event_key,payload_digest,digest_key_id) VALUES (9301,9201,'weight',1,'NUMERIC',60.00,'kg','2026-08-12 01:00:00+00','2026-08-12 01:01:00+00',now(),'APP',repeat('a',64),'evt-b-1',repeat('b',64),'fact-k'),(9302,9201,'weight',1,'NUMERIC',61.00,'kg','2026-08-12 02:00:00+00','2026-08-12 02:01:00+00',now(),'DEVICE',repeat('c',64),'evt-b-2',repeat('d',64),'fact-k')"))
    except BaseException as exc:
        primary = exc
        raise
    finally:
        await _dispose_engines((engine,), primary)


async def _exercise_builders(org_url, health_url, confirmation_url):
    from app.modules.organization_projection.repository import OrganizationProjectionRepository
    from app.modules.organization_projection.service import (
        ConfirmationResult, OperationPostimage, OrganizationProjectionBuilder,
        ProjectionDigestKeyring, ProjectionLeaseConflict, ProjectionSessionLock,
        ProjectionUnitOfWork, _builder_lock_key,
    )
    from app.modules.health_projection.repository import HealthProjectionRepository
    from app.modules.health_projection.service import HealthProjectionBuilder

    class AttributedOrganizationRepository(OrganizationProjectionRepository):
        async def audit_record(self, operation_id): _stage("B2_AUDIT_VIEW_REPLAY"); return await super().audit_record(operation_id)
        async def max_source_id(self): _stage("B3_SOURCE_MAX_ID"); return await super().max_source_id()
        async def count_sources(self, max_id): _stage("B4_SOURCE_COUNT"); return await super().count_sources(max_id)
        async def add_generation(self, generation, checkpoint):
            _stage("B5_GENERATION_PERSIST")
            return await super().add_generation(generation, checkpoint)
        async def add_audit(self, **kwargs): return await super().add_audit(**kwargs)

    class AttributedHealthRepository(HealthProjectionRepository):
        async def audit_record(self, operation_id): _stage("H2_AUDIT_REPLAY"); return await super().audit_record(operation_id)
        async def export_source_snapshot(self): _stage("H3_SNAPSHOT_EXPORT"); return await super().export_source_snapshot()
        async def max_source_id(self, *, source_snapshot=None): _stage("H4_SOURCE_MAX_VISIBLE_ID"); return await super().max_source_id(source_snapshot=source_snapshot)
        async def count_sources(self, max_id, *, source_snapshot=None): _stage("H5_SOURCE_VISIBLE_COUNT_AND_SUCCESSOR"); return await super().count_sources(max_id, source_snapshot=source_snapshot)
        async def add_generation(self, generation, checkpoint): _stage("H6_GENERATION_AND_CHECKPOINT"); return await super().add_generation(generation, checkpoint)
        async def add_audit(self, **kwargs): _stage("H7_AUDIT_AND_COMMIT"); return await super().add_audit(**kwargs)

    class AttributedProjectionUnitOfWork(ProjectionUnitOfWork):
        async def commit(self):
            _stage("B7_TRANSACTION_COMMIT")
            return await super().commit()

    org_engine = create_async_engine(org_url); health_engine = create_async_engine(health_url); confirmation_engine = create_async_engine(confirmation_url)
    primary = None
    try:
        org_factory = async_sessionmaker(org_engine, expire_on_commit=False); health_factory = async_sessionmaker(health_engine, expire_on_commit=False); confirmation_factory = async_sessionmaker(confirmation_engine, expire_on_commit=False)
        @event.listens_for(org_engine.sync_engine, "before_cursor_execute")
        def _mark_checkpoint_boundary(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("INSERT INTO ORGANIZATION_PROJECTION_CHECKPOINT"):
                _stage("B6_CHECKPOINT_PERSIST")
        ring = ProjectionDigestKeyring(current_key_id="k1", keys={"k1": b"k" * 32})
        async def _attributed_lock_connection():
            _stage("B1_SESSION_LOCK")
            return await _autocommit_lock_connection(org_engine)
        org = OrganizationProjectionBuilder(lambda: AttributedProjectionUnitOfWork(org_factory, AttributedOrganizationRepository), lambda: ProjectionUnitOfWork(confirmation_factory, OrganizationProjectionRepository, isolation_level="READ COMMITTED"), ring, _attributed_lock_connection)
        async def _attributed_health_lock_connection():
            _stage("H1_SESSION_LOCK")
            return await _autocommit_lock_connection(health_engine)
        health = HealthProjectionBuilder(lambda: ProjectionUnitOfWork(health_factory, AttributedHealthRepository), lambda: ProjectionUnitOfWork(confirmation_factory, HealthProjectionRepository, isolation_level="READ COMMITTED"), ring, _attributed_health_lock_connection)
        org_id = await _attributed("ORGANIZATION_START", org.start(generation_no=1, builder_id="00000000-0000-0000-0000-000000000001", operation_id="00000000-0000-0000-0000-000000000101"))
        async with confirmation_factory() as session:
            org_repo = OrganizationProjectionRepository(session); org_checkpoint = await org_repo.get_checkpoint(org_id)
            org_checkpoint_digest = org_checkpoint.checkpoint_digest
        _stage("ORGANIZATION_BUILD")
        state = await _attributed("ORGANIZATION_BUILD", org.build_page(generation_id=org_id, builder_id="00000000-0000-0000-0000-000000000001", lease_epoch=0, expected_checkpoint_digest=org_checkpoint_digest, operation_id="00000000-0000-0000-0000-000000000102", heartbeat_operation_id="00000000-0000-0000-0000-000000000104", page_size=1))
        assert state.remaining_count == 1
        async with confirmation_factory() as session:
            org_repo = OrganizationProjectionRepository(session); org_checkpoint = await org_repo.get_checkpoint(org_id)
            org_checkpoint_digest = org_checkpoint.checkpoint_digest
        state = await _attributed("ORGANIZATION_BUILD", org.build_page(generation_id=org_id, builder_id="00000000-0000-0000-0000-000000000001", lease_epoch=0, expected_checkpoint_digest=org_checkpoint_digest, operation_id="00000000-0000-0000-0000-000000000105", heartbeat_operation_id="00000000-0000-0000-0000-000000000106", page_size=1))
        assert state.remaining_count == 0
        _stage("ORGANIZATION_COMPLETE")
        async with confirmation_factory() as session:
            org_repo = OrganizationProjectionRepository(session); org_generation = await org_repo.get_generation(org_id); org_checkpoint = await org_repo.get_checkpoint(org_id)
        await _attributed("ORGANIZATION_COMPLETE", org.complete(generation_id=org_id, builder_id="00000000-0000-0000-0000-000000000001", lease_epoch=0, expected_generation_version=org_generation.version, expected_checkpoint_digest=org_checkpoint.checkpoint_digest, operation_id="00000000-0000-0000-0000-000000000103"))
        _stage("HEALTH_START")
        health_id = await _attributed("HEALTH_START", health.start(generation_no=1, builder_id="00000000-0000-0000-0000-000000000002", operation_id="00000000-0000-0000-0000-000000000201"))
        async with confirmation_factory() as session:
            health_repo = HealthProjectionRepository(session); health_checkpoint = await health_repo.get_checkpoint(health_id)
        _stage("HEALTH_BUILD")
        with pytest.raises(ProjectionLeaseConflict):
            await health.takeover(generation_id=health_id, builder_id="00000000-0000-0000-0000-000000000003", expected_lease_epoch=0, expected_checkpoint_digest=health_checkpoint.checkpoint_digest, operation_id="00000000-0000-0000-0000-000000000205")
        async with health_factory() as session:
            await session.execute(update(__import__('app.modules.health_projection.models', fromlist=['HealthProjectionGeneration']).HealthProjectionGeneration).where(__import__('app.modules.health_projection.models', fromlist=['HealthProjectionGeneration']).HealthProjectionGeneration.id == health_id).values(lease_expires_at=datetime.now(UTC)-timedelta(seconds=1)))
            await session.commit()
        epoch = await health.takeover(generation_id=health_id, builder_id="00000000-0000-0000-0000-000000000003", expected_lease_epoch=0, expected_checkpoint_digest=health_checkpoint.checkpoint_digest, operation_id="00000000-0000-0000-0000-000000000206")
        assert epoch == 1
        state = await _attributed("HEALTH_BUILD", health.build_page(generation_id=health_id, builder_id="00000000-0000-0000-0000-000000000003", lease_epoch=1, expected_checkpoint_digest=health_checkpoint.checkpoint_digest, operation_id="00000000-0000-0000-0000-000000000202", heartbeat_operation_id="00000000-0000-0000-0000-000000000204", page_size=1))
        assert state.remaining_count == 1
        async with confirmation_factory() as session:
            health_repo = HealthProjectionRepository(session); health_checkpoint = await health_repo.get_checkpoint(health_id)
        state = await _attributed("HEALTH_BUILD", health.build_page(generation_id=health_id, builder_id="00000000-0000-0000-0000-000000000003", lease_epoch=1, expected_checkpoint_digest=health_checkpoint.checkpoint_digest, operation_id="00000000-0000-0000-0000-000000000207", heartbeat_operation_id="00000000-0000-0000-0000-000000000208", page_size=1))
        assert state.remaining_count == 0
        _stage("HEALTH_COMPLETE")
        complete_preimage = {"operation": "complete", "generation_id": health_id, "builder_id": "00000000-0000-0000-0000-000000000003", "lease_epoch": 1, "checkpoint_digest": None, "generation_version": None}
        async with confirmation_factory() as session:
            before_repo = HealthProjectionRepository(session); before_generation = await before_repo.get_generation(health_id); before_checkpoint = await before_repo.get_checkpoint(health_id)
            complete_preimage["checkpoint_digest"] = before_checkpoint.checkpoint_digest; complete_preimage["generation_version"] = before_generation.version
        await _attributed("HEALTH_COMPLETE", health.complete(generation_id=health_id, builder_id="00000000-0000-0000-0000-000000000003", lease_epoch=1, expected_generation_version=complete_preimage["generation_version"], expected_checkpoint_digest=complete_preimage["checkpoint_digest"], operation_id="00000000-0000-0000-0000-000000000203"))
        async with confirmation_factory() as session:
            repo = HealthProjectionRepository(session)
            generation = await repo.get_generation(health_id); checkpoint = await repo.get_checkpoint(health_id); audit = await repo.audit_payload("00000000-0000-0000-0000-000000000203")
            postimage = await repo.operation_postimage(generation, checkpoint, audit)
        assert await health.confirm_operation(generation_id=health_id, operation_id="00000000-0000-0000-0000-000000000203", expected_preimage=complete_preimage, expected_postimage=postimage) == ConfirmationResult.COMMITTED
        missing_preimage = {"operation": "start", "generation_no": 999999, "builder_id": "00000000-0000-0000-0000-000000000003", "projection_version": 1}
        assert await health.confirm_operation(generation_id=999999, operation_id="00000000-0000-0000-0000-000000000299", expected_preimage=missing_preimage, expected_postimage=postimage) == ConfirmationResult.ROLLED_BACK
        wrong = OperationPostimage(**{**postimage.__dict__, "row_count": 999}) if hasattr(postimage, "__dict__") else None
        if wrong is None:
            from dataclasses import replace
            wrong = replace(postimage, row_count=999)
        assert await health.confirm_operation(generation_id=health_id, operation_id="00000000-0000-0000-0000-000000000203", expected_preimage=complete_preimage, expected_postimage=wrong) == ConfirmationResult.UNKNOWN
        async with ProjectionSessionLock(lambda: _autocommit_lock_connection(org_engine), _builder_lock_key("organization")):
            with pytest.raises(ProjectionLeaseConflict):
                async with ProjectionSessionLock(lambda: _autocommit_lock_connection(org_engine), _builder_lock_key("organization")):
                    pass
        async with ProjectionSessionLock(lambda: _autocommit_lock_connection(org_engine), _builder_lock_key("organization")):
            pass
    except BaseException as exc:
        primary = exc
        raise
    finally:
        await _dispose_engines((org_engine, health_engine, confirmation_engine), primary)


def test_Module_B真实Builder与最小权限闭环(pg_database):
    _stage("SEED")
    migration_url = os.environ["KG_TEST_MIGRATION_DATABASE_URL"]
    _run(_seed_sources(migration_url))
    org_url = _get_organization_projection_builder_database_url()
    health_url = _get_health_projection_builder_database_url()
    confirmation_url = _get_projection_confirmation_database_url()
    _run(_exercise_builders(org_url, health_url, confirmation_url))
    _stage("PERMISSION")
    assert pg_database.fetch_value("SELECT status='BUILD_COMPLETE' FROM public.organization_projection_generation WHERE generation_no=1")
    assert pg_database.fetch_value("SELECT status='BUILD_COMPLETE' FROM public.health_projection_generation WHERE generation_no=1")
    assert pg_database.fetch_value("SELECT count(*)=2 FROM public.organization_projection")
    assert pg_database.fetch_value("SELECT count(*)=2 FROM public.health_projection_fact")
    assert pg_database.fetch_value("SELECT count(*)=1 FROM public.health_projection_window_selection")
    assert pg_database.fetch_value("SELECT winner_fact_id=9302 FROM public.health_projection_window_selection")
    for role_env in ("KG_TEST_APPLICATION_ROLE", "KG_TEST_READONLY_ROLE"):
        role = os.environ[role_env]
        assert not pg_database.fetch_value(f"SELECT has_table_privilege('{role}','public.organization_projection','SELECT')")
        assert not pg_database.fetch_value(f"SELECT has_table_privilege('{role}','public.health_projection_fact','SELECT')")
    health_role = os.environ["KG_TEST_HEALTH_PROJECTION_BUILDER_ROLE"]
    confirmation_role = os.environ["KG_TEST_PROJECTION_CONFIRMATION_ROLE"]
    assert not pg_database.fetch_value(f"SELECT has_table_privilege('{health_role}','public.health_projection_window_selection','UPDATE')")
    assert pg_database.fetch_value(f"SELECT has_column_privilege('{health_role}','public.health_projection_window_selection','winner_fact_id','INSERT')")
    assert not pg_database.fetch_value(f"SELECT has_table_privilege('{confirmation_role}','public.health_projection_fact','SELECT')")
    assert pg_database.fetch_value(f"SELECT has_column_privilege('{confirmation_role}','public.health_projection_fact','fact_id','SELECT')")
    assert not pg_database.fetch_value(f"SELECT has_table_privilege('{confirmation_role}','public.health_projection_fact','INSERT')")
