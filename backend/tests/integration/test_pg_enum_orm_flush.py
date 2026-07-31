import asyncio
import os

import pytest


pytestmark = pytest.mark.integration


def test_tenant_status_enum_allows_real_orm_flush(pg_database):
    pg_database.execute(
        """
        INSERT INTO platform_org (id, org_name, org_code, org_type)
        VALUES (20, 'Org 20', 'ORG20', 'tenant_org')
        ON CONFLICT (org_code) DO NOTHING
        """
    )

    async def create_tenant():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.core.sqlalchemy_mapping import map_core_model_classes
        from app.modules.tenant.models import Tenant

        map_core_model_classes()
        engine = create_async_engine(os.environ["KG_TEST_DATABASE_URL"], pool_pre_ping=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                tenant = Tenant()
                tenant.org_id = 20
                tenant.tenant_code = "TENUM0001"
                tenant.name = "Enum Flush Store"
                tenant.type = "health_store"
                tenant.credit_code = "91330100MAENUM0001"
                tenant.province = "ZJ"
                tenant.city = "HZ"
                tenant.status = "pending"

                session.add(tenant)
                await session.flush()
                await session.commit()
                return tenant.id
        finally:
            await engine.dispose()

    tenant_id = asyncio.run(create_tenant())
    tenant = pg_database.fetch_rows("SELECT id, status FROM tenant WHERE id = $1", tenant_id)[0]

    assert tenant["status"] == "pending"
