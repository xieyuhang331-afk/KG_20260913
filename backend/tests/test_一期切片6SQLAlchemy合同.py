from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID

from app.modules.health_plan import models


def test_Slice6模型精确登记且所有公开主键使用PostgreSQL_UUID():
    expected = {
        "health_plan_template_version",
        "health_plan_generation_request",
        "health_plan_version",
        "health_plan_review",
        "health_plan_explanation",
        "health_plan_user_decision",
        "health_plan_receipt",
        "health_plan_audit",
        "health_plan_outbox",
        "health_plan_delivery",
    }
    tables = {
        value.__table__.name: value.__table__
        for value in vars(models).values()
        if isinstance(value, type) and hasattr(value, "__table__")
    }
    assert set(tables) == expected
    for table in tables.values():
        for column in table.primary_key.columns:
            assert isinstance(column.type, PostgreSQLUUID)
            assert column.type.as_uuid is True
            assert column.autoincrement is not True


def test_模型保留不可变状态和唯一活动边界():
    template = models.HealthPlanTemplateVersionModel.__table__
    plan = models.HealthPlanVersionModel.__table__
    request = models.HealthPlanGenerationRequestModel.__table__

    assert "content_digest" in template.c
    assert "content_digest" in plan.c
    assert "authority_digest" in request.c
    assert any(index.name == "uq_health_plan_template_published" and index.unique for index in template.indexes)
    assert any(index.name == "uq_health_plan_active_case" and index.unique for index in plan.indexes)
    assert any(index.name == "uq_health_plan_generation_active_case" and index.unique for index in request.indexes)
