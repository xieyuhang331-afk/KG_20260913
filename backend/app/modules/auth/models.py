from __future__ import annotations

from app.core.model_specs import ColumnSpec, TableSpec, register_core_table_specs


USER_TABLE = TableSpec(
    name="user",
    module="auth",
    columns=(
        ColumnSpec("id", "BIGSERIAL", nullable=False, primary_key=True),
        ColumnSpec("phone", "VARCHAR(11)", nullable=False, unique=True),
        ColumnSpec("password_hash", "VARCHAR(255)", nullable=False),
        ColumnSpec("real_name", "VARCHAR(50)"),
        ColumnSpec("id_card", "VARCHAR(18)"),
        ColumnSpec("role", "user_role", nullable=False),
        ColumnSpec("user_status", "VARCHAR(20)", default="customer"),
        ColumnSpec("tenant_id", "BIGINT", foreign_key="tenant.id"),
        ColumnSpec("gender", "VARCHAR(5)"),
        ColumnSpec("birth_date", "DATE"),
        ColumnSpec("avatar_url", "VARCHAR(500)"),
        ColumnSpec("verify_status", "VARCHAR(20)"),
        ColumnSpec("therapist_level", "VARCHAR(10)"),
        ColumnSpec("specialties", "JSONB"),
        ColumnSpec("violation_count", "INT", default="0"),
        ColumnSpec("status", "user_status", default="active"),
        ColumnSpec("therapist_status", "VARCHAR(20)", default="available"),
        ColumnSpec("exited_at", "TIMESTAMPTZ"),
        ColumnSpec("exit_reason", "VARCHAR(50)"),
        ColumnSpec("deletion_requested_at", "TIMESTAMPTZ"),
        ColumnSpec("last_login_at", "TIMESTAMPTZ"),
        ColumnSpec("created_at", "TIMESTAMPTZ", nullable=False, default="NOW()"),
        ColumnSpec("updated_at", "TIMESTAMPTZ", nullable=False, default="NOW()"),
    ),
    indexes=(
        ("idx_user_tenant", ("tenant_id",)),
        ("idx_user_role", ("role",)),
        ("idx_user_status", ("user_status",)),
    ),
)


class User:
    __tablename__ = USER_TABLE.name
    __table_spec__ = USER_TABLE


register_core_table_specs(USER_TABLE)
