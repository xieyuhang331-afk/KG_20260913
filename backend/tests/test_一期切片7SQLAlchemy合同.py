from sqlalchemy.dialects.postgresql import UUID

from app.modules.service_fulfillment import models


EXPECTED_TABLES = {
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


def test_Slice7对象目录唯一且所有公共标识使用原生UUID() -> None:
    tables = {
        value.__table__.name: value.__table__
        for value in vars(models).values()
        if isinstance(value, type) and hasattr(value, "__table__")
    }
    assert set(tables) == EXPECTED_TABLES
    for table in tables.values():
        for column in table.columns:
            if column.name.endswith("_id") and column.name not in {
                "tenant_id",
                "actor_user_id",
                "requested_by",
            }:
                assert isinstance(column.type, UUID)
                assert column.type.as_uuid is True


def test_里程碑转机构导出和幂等对象保留数据库约束() -> None:
    assert any(
        item.name == "uq_service_cycle_schedule_current"
        for item in models.ServiceCycleScheduleModel.__table__.indexes
    )
    assert any(
        item.name == "uq_service_transfer_open"
        for item in models.ServiceTransferRequestModel.__table__.indexes
    )
    assert any(
        item.name == "uq_service_fulfillment_receipt"
        for item in models.ServiceFulfillmentReceiptModel.__table__.constraints
    )
    assert models.PersonalDataExportArtifactModel.__table__.c.private_file_id.nullable is False
