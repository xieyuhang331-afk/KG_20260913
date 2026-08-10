import pytest


def test_P3机构SQLAlchemy持久化尚未实现():
    try:
        from app.modules.organization.repository import OrganizationRepository
        from app.core.sqlalchemy_mapping import map_core_model_classes
    except (ImportError, ModuleNotFoundError):
        pytest.fail("P3 Organization SQLAlchemy persistence is not implemented")

    mapped = map_core_model_classes()
    table = mapped["platform_org"].__table__
    assert {"admin_id", "version", "created_by", "updated_by"} <= set(table.c.keys())
    assert OrganizationRepository is not None


def test_管理员候选只投影批准字段():
    from app.modules.organization.repository import ADMIN_CANDIDATE_USER_COLUMNS

    assert ADMIN_CANDIDATE_USER_COLUMNS == (
        "id",
        "real_name",
        "role",
        "status",
        "tenant_id",
        "updated_at",
    )
