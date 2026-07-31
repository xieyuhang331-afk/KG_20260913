from __future__ import annotations

from app.core.model_specs import ColumnSpec, TableSpec, register_core_table_specs


PLATFORM_ORG_TABLE = TableSpec(
    name="platform_org",
    module="system",
    columns=(
        ColumnSpec("id", "BIGSERIAL", nullable=False, primary_key=True),
        ColumnSpec("parent_id", "BIGINT", foreign_key="platform_org.id"),
        ColumnSpec("org_name", "VARCHAR(100)", nullable=False),
        ColumnSpec("org_code", "VARCHAR(50)", nullable=False, unique=True),
        ColumnSpec("org_type", "VARCHAR(20)", nullable=False),
        ColumnSpec("org_path", "VARCHAR(500)"),
        ColumnSpec("sort_order", "INT", default="0"),
        ColumnSpec("contact_name", "VARCHAR(50)"),
        ColumnSpec("contact_phone", "VARCHAR(20)"),
        ColumnSpec("status", "VARCHAR(10)", default="active"),
        ColumnSpec("created_at", "TIMESTAMPTZ", nullable=False, default="NOW()"),
        ColumnSpec("updated_at", "TIMESTAMPTZ", nullable=False, default="NOW()"),
    ),
)


MESSAGE_TABLE = TableSpec(
    name="message",
    module="system",
    columns=(
        ColumnSpec("id", "BIGSERIAL", nullable=False, primary_key=True),
        ColumnSpec("session_id", "BIGINT", nullable=False, foreign_key="interpretation_session.id"),
        ColumnSpec("sender_id", "BIGINT", nullable=False, foreign_key="user.id"),
        ColumnSpec("sender_role", "VARCHAR(10)", nullable=False),
        ColumnSpec("content", "TEXT", nullable=False),
        ColumnSpec("message_type", "VARCHAR(20)", default="text"),
        ColumnSpec("is_read", "BOOLEAN", default="FALSE"),
        ColumnSpec("created_at", "TIMESTAMPTZ", nullable=False, default="NOW()"),
    ),
    indexes=(
        ("idx_msg_session", ("session_id", "created_at")),
        ("idx_msg_sender_read", ("sender_id", "is_read")),
    ),
)

OPERATION_LOG_TABLE = TableSpec(
    name="operation_log",
    module="system",
    columns=(
        ColumnSpec("id", "BIGSERIAL", nullable=False, primary_key=True),
        ColumnSpec("operator_id", "BIGINT", foreign_key="user.id"),
        ColumnSpec("module", "VARCHAR(50)", nullable=False),
        ColumnSpec("object_type", "VARCHAR(50)", nullable=False),
        ColumnSpec("object_id", "BIGINT"),
        ColumnSpec("action", "VARCHAR(50)", nullable=False),
        ColumnSpec("payload", "JSONB"),
        ColumnSpec("created_at", "TIMESTAMPTZ", nullable=False, default="NOW()"),
    ),
    indexes=(
        ("idx_operation_log_operator", ("operator_id",)),
        ("idx_operation_log_module_object", ("module", "object_type", "object_id")),
    ),
)


class PlatformOrg:
    __tablename__ = PLATFORM_ORG_TABLE.name
    __table_spec__ = PLATFORM_ORG_TABLE


class Message:
    __tablename__ = MESSAGE_TABLE.name
    __table_spec__ = MESSAGE_TABLE


class OperationLog:
    __tablename__ = OPERATION_LOG_TABLE.name
    __table_spec__ = OPERATION_LOG_TABLE


register_core_table_specs(PLATFORM_ORG_TABLE, MESSAGE_TABLE, OPERATION_LOG_TABLE)
