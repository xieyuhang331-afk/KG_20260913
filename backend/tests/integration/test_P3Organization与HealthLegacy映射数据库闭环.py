import asyncio
import base64
from datetime import datetime, timezone
from decimal import Decimal
import json
import os

import pytest


pytestmark = pytest.mark.integration


def test_0016升级Schema和四角色权限矩阵(
    pg_database,
    organization_mapping_writer_database,
    health_mapping_writer_database,
    mapping_audit_database,
    mapping_shadow_database,
):
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260916_0049"
    assert pg_database.fetch_value("SELECT to_regclass('public.organization_legacy_mapping') IS NOT NULL")
    assert pg_database.fetch_value("SELECT to_regclass('public.health_indicator_legacy_mapping') IS NOT NULL")
    assert pg_database.fetch_value(
        "SELECT is_nullable='NO' FROM information_schema.columns WHERE table_schema='public' "
        "AND table_name='organization_legacy_mapping' AND column_name='disposition'"
    )
    assert {
        row["conname"]
        for row in pg_database.fetch_rows(
            "SELECT conname FROM pg_constraint WHERE connamespace='public'::regnamespace "
            "AND conrelid IN ('public.organization_legacy_mapping'::regclass,"
            "'public.health_indicator_legacy_mapping'::regclass) AND contype='f'"
        )
    } == {
        "fk_organization_legacy_mapping_tenant",
        "fk_organization_legacy_mapping_canonical_org",
        "fk_organization_legacy_mapping_created_by",
        "fk_health_indicator_legacy_mapping_fact",
        "fk_health_indicator_legacy_mapping_created_by",
    }

    org = organization_mapping_writer_database.fetch_rows(
        "SELECT has_table_privilege(current_user,'public.organization_legacy_mapping','SELECT') AS s,"
        "has_table_privilege(current_user,'public.organization_legacy_mapping','INSERT') AS i,"
        "has_table_privilege(current_user,'public.organization_legacy_mapping','UPDATE') AS u,"
        "has_table_privilege(current_user,'public.health_indicator_legacy_mapping','INSERT') AS cross_i"
    )[0]
    health = health_mapping_writer_database.fetch_rows(
        "SELECT has_table_privilege(current_user,'public.health_indicator_legacy_mapping','SELECT') AS s,"
        "has_table_privilege(current_user,'public.health_indicator_legacy_mapping','INSERT') AS i,"
        "has_table_privilege(current_user,'public.canonical_health_fact','INSERT') AS fact_i,"
        "has_table_privilege(current_user,'public.organization_legacy_mapping','INSERT') AS cross_i"
    )[0]
    audit = mapping_audit_database.fetch_rows(
        "SELECT has_table_privilege(current_user,'public.organization_legacy_mapping','SELECT') AS org_s,"
        "has_table_privilege(current_user,'public.health_indicator_legacy_mapping','SELECT') AS health_s,"
        "has_table_privilege(current_user,'public.operation_log','INSERT') AS audit_i"
    )[0]
    shadow = mapping_shadow_database.fetch_rows(
        "SELECT has_column_privilege(current_user,'public.health_indicator','value','SELECT') AS value_s,"
        "has_column_privilege(current_user,'public.tenant','org_id','SELECT') AS tenant_org_s,"
        "has_column_privilege(current_user,'public.tenant','name','SELECT') AS tenant_name_s,"
        "has_column_privilege(current_user,'public.platform_org','parent_id','SELECT') AS parent_s,"
        "has_column_privilege(current_user,'public.platform_org','org_name','SELECT') AS org_name_s,"
        "has_column_privilege(current_user,'public.canonical_health_fact','received_at','SELECT') AS received_s,"
        "has_column_privilege(current_user,'public.canonical_health_fact','payload_digest','SELECT') AS payload_s,"
        "has_table_privilege(current_user,'public.tenant','SELECT') AS tenant_table_s,"
        "has_table_privilege(current_user,'public.platform_org','SELECT') AS org_table_s,"
        "has_table_privilege(current_user,'public.canonical_health_fact','SELECT') AS fact_table_s,"
        "has_table_privilege(current_user,'public.organization_legacy_mapping','INSERT') AS org_i"
    )[0]
    assert org == {"s": True, "i": True, "u": False, "cross_i": False}
    assert health == {"s": True, "i": True, "fact_i": True, "cross_i": False}
    assert audit == {"org_s": True, "health_s": True, "audit_i": False}
    assert shadow == {
        "value_s": True,
        "tenant_org_s": True,
        "tenant_name_s": False,
        "parent_s": True,
        "org_name_s": False,
        "received_s": True,
        "payload_s": False,
        "tenant_table_s": False,
        "org_table_s": False,
        "fact_table_s": False,
        "org_i": False,
    }


def test_Organization与Health映射真实Round_trip且legacy源不变(
    pg_database, organization_mapping_writer_database, health_mapping_writer_database
):
    pg_database.execute(
        "INSERT INTO public.platform_org(id,org_name,org_code,org_type,status,version) "
        "VALUES (910001,'HQ','MAP-HQ','headquarter','active',1),"
        "(910002,'Province','MAP-P','province','active',1) ON CONFLICT (id) DO NOTHING"
    )
    pg_database.execute("UPDATE public.platform_org SET parent_id=910001 WHERE id=910002")
    pg_database.execute(
        "INSERT INTO public.tenant(id,name,tenant_code,type,status,org_id,province,city,created_at,updated_at) "
        "VALUES (910001,'Mapping Tenant','MAP-T','health_store','approved',910002,'P','C',now(),now()) "
        "ON CONFLICT (id) DO NOTHING"
    )
    pg_database.execute(
        "INSERT INTO public.\"user\"(id,phone,password_hash,role,status,verify_status) "
        "VALUES (910001,'10000000001','safe-test-hash','member','active','verified') ON CONFLICT (id) DO NOTHING"
    )
    pg_database.execute(
        "INSERT INTO public.health_indicator(id,user_id,indicator_type,value,unit,source,recorded_at,created_at) "
        "VALUES (910001,910001,'weight',65.20,'kg','APP','2026-08-01T00:00:00Z',now()) ON CONFLICT DO NOTHING"
    )
    before_tenant = pg_database.fetch_rows("SELECT id,org_id,status FROM public.tenant WHERE id=910001")[0]
    before_health = pg_database.fetch_rows(
        "SELECT id,user_id,indicator_type,value::text,unit,source,recorded_at FROM public.health_indicator WHERE id=910001"
    )[0]

    async def run_mapping():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from app.modules.health_fact.domain import HealthFactDigestKeyring
        from app.modules.health_fact.unit_of_work import CanonicalHealthFactWriter
        from app.modules.health_fact_mapping.repository import HealthFactMappingRepository
        from app.modules.health_fact_mapping.service import HealthFactMappingUnitOfWork, HealthLegacyMappingService, HealthShadowValidator
        from app.modules.organization_mapping.domain import OrganizationMappingDigestKeyring
        from app.modules.organization_mapping.repository import OrganizationMappingRepository
        from app.modules.organization_mapping.service import OrganizationMappingUnitOfWork, OrganizationLegacyMappingService, OrganizationShadowValidator

        org_engine=create_async_engine(os.environ["KG_TEST_ORGANIZATION_MAPPING_WRITER_DATABASE_URL"])
        health_engine=create_async_engine(os.environ["KG_TEST_HEALTH_MAPPING_WRITER_DATABASE_URL"])
        shadow_engine=create_async_engine(os.environ["KG_TEST_MAPPING_SHADOW_DATABASE_URL"])
        try:
            org_factory=async_sessionmaker(org_engine,expire_on_commit=False)
            health_factory=async_sessionmaker(health_engine,expire_on_commit=False)
            shadow_factory=async_sessionmaker(shadow_engine,expire_on_commit=False)
            org_keyring=OrganizationMappingDigestKeyring.from_json(
                current_key_id=os.environ["KG_ORGANIZATION_MAPPING_DIGEST_CURRENT_KEY_ID"],
                keyring_json=os.environ["KG_ORGANIZATION_MAPPING_DIGEST_KEYRING_JSON"],
            )
            fact_keyring=HealthFactDigestKeyring.from_base64(
                current_key_id=os.environ["KG_HEALTH_FACT_DIGEST_CURRENT_KEY_ID"],
                encoded_keys=json.loads(os.environ["KG_HEALTH_FACT_DIGEST_KEYRING_JSON"]),
            )
            class Authority:
                async def authorize(self,draft): return draft
            writer=CanonicalHealthFactWriter(uow_factory=None,readonly_uow_factory=None,keyring=fact_keyring,producer_authority=Authority())
            org_service=OrganizationLegacyMappingService(
                uow_factory=lambda:OrganizationMappingUnitOfWork(org_factory),
                readonly_uow_factory=lambda:OrganizationMappingUnitOfWork(org_factory),keyring=org_keyring,
            )
            health_service=HealthLegacyMappingService(
                writer=writer,uow_factory=lambda:HealthFactMappingUnitOfWork(health_factory),
                readonly_uow_factory=lambda:HealthFactMappingUnitOfWork(health_factory),keyring=fact_keyring,
            )
            org_result=await org_service.map_one(legacy_tenant_id=910001,batch_id="00000000-0000-0000-0000-000000000016")
            health_result=await health_service.map_one(
                legacy_indicator_id=910001,legacy_recorded_at=datetime(2026,8,1,tzinfo=timezone.utc),
                batch_id="00000000-0000-0000-0000-000000000016",
            )
            async with shadow_factory() as session:
                org_shadow = await OrganizationShadowValidator(
                    repository=OrganizationMappingRepository(session), keyring=org_keyring
                ).validate({"max_tenant_id": 910001})
            async with shadow_factory() as session:
                health_shadow = await HealthShadowValidator(
                    repository=HealthFactMappingRepository(session), keyring=fact_keyring
                ).validate({
                    "max_recorded_at": "2026-08-01T00:00:00.000000Z",
                    "max_id": 910001,
                })
            return org_result,health_result,org_shadow,health_shadow
        finally:
            await org_engine.dispose(); await health_engine.dispose(); await shadow_engine.dispose()

    org_result,health_result,org_shadow,health_shadow=asyncio.run(run_mapping())
    assert org_result.mapping.disposition == health_result.mapping.disposition == "MAPPED"
    assert pg_database.fetch_value("SELECT count(*) FROM public.organization_legacy_mapping WHERE legacy_tenant_id=910001") == 1
    assert pg_database.fetch_value("SELECT count(*) FROM public.health_indicator_legacy_mapping WHERE legacy_indicator_id=910001") == 1
    assert pg_database.fetch_rows("SELECT id,org_id,status FROM public.tenant WHERE id=910001")[0] == before_tenant
    assert pg_database.fetch_rows(
        "SELECT id,user_id,indicator_type,value::text,unit,source,recorded_at FROM public.health_indicator WHERE id=910001"
    )[0] == before_health
    assert set(org_shadow) == {"blocker", "review_required", "informational", "summary_hash"}
    assert set(health_shadow) == set(org_shadow)
    assert len(org_shadow["summary_hash"]) == len(health_shadow["summary_hash"]) == 64
