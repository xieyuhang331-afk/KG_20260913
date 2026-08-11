import inspect

import pytest


def test_两领域Mapper不得共享万能Repository或UoW():
    from app.modules.organization_mapping.repository import OrganizationMappingRepository
    from app.modules.health_fact_mapping.repository import HealthFactMappingRepository
    from app.modules.organization_mapping.service import OrganizationMappingUnitOfWork
    from app.modules.health_fact_mapping.service import HealthFactMappingUnitOfWork

    assert OrganizationMappingRepository is not HealthFactMappingRepository
    assert OrganizationMappingUnitOfWork is not HealthFactMappingUnitOfWork
    sources = "\n".join(
        inspect.getsource(value)
        for value in (
            OrganizationMappingRepository,
            HealthFactMappingRepository,
            OrganizationMappingUnitOfWork,
            HealthFactMappingUnitOfWork,
        )
    )
    assert "UniversalMapping" not in sources


def test_Health_Mapping只能经append_in_uow复用Fact状态机():
    from app.modules.health_fact.unit_of_work import CanonicalHealthFactWriter
    from app.modules.health_fact_mapping.service import HealthLegacyMappingService

    assert hasattr(CanonicalHealthFactWriter, "append_in_uow")
    source = inspect.getsource(HealthLegacyMappingService)
    assert "append_in_uow" in source
    assert "prepare_fact(" not in source


def test_两套Mapping_Writer数据库身份缺失或混用必须fail_closed():
    from app.core.config import Settings
    from app.core.database import _get_mapping_writer_database_url

    base = dict(
        database_host="db",
        database_port=5432,
        database_name="kg",
        database_user="app",
        database_password="safe-placeholder",
        jwt_secret_key="safe-placeholder",
    )
    with pytest.raises(RuntimeError, match="Organization mapping writer database runtime is unavailable"):
        _get_mapping_writer_database_url(Settings(**base), kind="organization")
    shared = "postgresql+asyncpg://mapping:safe-placeholder@db:5432/kg"
    settings = Settings(
        **base,
        organization_mapping_writer_database_url=shared,
        health_mapping_writer_database_url=shared,
    )
    with pytest.raises(RuntimeError, match="Health mapping writer database runtime is unavailable"):
        _get_mapping_writer_database_url(settings, kind="health")


def test_两套Mapping_Runtime均纳入统一dispose生命周期():
    from app.core import database

    source = inspect.getsource(database.dispose_database_runtimes)
    assert "dispose_mapping_writer_runtimes" in source
    assert database.get_organization_mapping_writer_session_factory is not database.get_health_mapping_writer_session_factory
