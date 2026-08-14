from app.modules.organization_projection.models import OrganizationProjectionShadowRun
from app.modules.health_projection.models import HealthProjectionShadowRun
from app.modules.organization_projection.models import OrganizationProjectionGeneration
from app.modules.health_projection.models import HealthProjectionGeneration
from app.modules.organization_projection.models import OrganizationProjectionModel


def test_Shadow_RUN表结果字段允许RUNNING期间NULL():
    for model in (OrganizationProjectionShadowRun, HealthProjectionShadowRun):
        for name in ("source_digest", "mapping_digest", "projection_digest", "coverage_digest", "evidence_digest", "blocker_count", "review_required_count", "informational_count", "category_counts"):
            assert model.__table__.c[name].nullable is True


def test_Shadow表不含原始敏感字段():
    forbidden = {"numeric_value", "org_name", "mobile", "id_card", "database_url", "credential"}
    for model in (OrganizationProjectionShadowRun, HealthProjectionShadowRun):
        assert forbidden.isdisjoint(model.__table__.c.keys())


def test_ORM两领域Generation与ShadowRun约束强度一致():
    def checks(model):
        return {item.name: str(item.sqltext) for item in model.__table__.constraints if hasattr(item, "sqltext")}
    def value(checks, suffix):
        return next(sql for name, sql in checks.items() if name.endswith(suffix))
    org_generation, health_generation = checks(OrganizationProjectionGeneration), checks(HealthProjectionGeneration)
    org_run, health_run = checks(OrganizationProjectionShadowRun), checks(HealthProjectionShadowRun)
    assert value(org_generation, "_state") == value(health_generation, "_state")
    for required in ("builder_id IS NULL", "lease_expires_at IS NULL", "ready_operation_id IS NULL"):
        assert required in value(org_generation, "_state")
    for required in ("category_json", "coverage", "result_digest", "counts", "state", "status", "identity"):
        assert any(name.endswith("_" + required) for name in org_run)
        assert any(name.endswith("_" + required) for name in health_run)


def test_organization_projection_metadata_contains_path_version_baseline():
    table = OrganizationProjectionModel.__table__
    assert table.c.path_versions.nullable is False
    compatibility = next(str(item.sqltext) for item in table.constraints if item.name.endswith("_compatibility"))
    assert "jsonb_array_length(path_versions)=4" in compatibility
    assert "@ % 1 != 0" in compatibility
