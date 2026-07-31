from __future__ import annotations

import re
from typing import Any

from app.core.database import Base, is_sqlalchemy_available
from app.core.model_specs import ColumnSpec, TableSpec, get_core_table_specs
from app.modules.models import get_core_model_classes, import_core_models


POSTGRES_ENUM_TYPES = {
    "TENANT_STATUS": {
        "name": "tenant_status",
        "values": ("pending", "approved", "rejected", "active", "closed"),
    },
    "USER_ROLE": {
        "name": "user_role",
        "values": (
            "super_admin",
            "province_admin",
            "city_admin",
            "sys_admin",
            "expert",
            "org_admin",
            "org_operator",
            "therapist",
            "host",
            "member",
        ),
    },
    "USER_STATUS": {
        "name": "user_status",
        "values": ("active", "disabled", "suspended"),
    },
}


def _require_sqlalchemy() -> None:
    if not is_sqlalchemy_available():
        raise RuntimeError("Install backend dependencies before mapping SQLAlchemy models.")


def _load_sqlalchemy():
    _require_sqlalchemy()
    import sqlalchemy as sa
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.exc import NoInspectionAvailable
    from sqlalchemy.orm.exc import UnmappedClassError

    return sa, JSONB, (NoInspectionAvailable, UnmappedClassError)


def _parse_ddl_type(column: ColumnSpec):
    sa, JSONB, _ = _load_sqlalchemy()
    ddl_type = column.ddl_type.upper()
    postgres_enum = POSTGRES_ENUM_TYPES.get(ddl_type)
    if postgres_enum is not None:
        from sqlalchemy.dialects import postgresql

        return postgresql.ENUM(
            *postgres_enum["values"],
            name=postgres_enum["name"],
            create_type=False,
        )

    varchar_match = re.fullmatch(r"VARCHAR\((\d+)\)", ddl_type)
    if varchar_match:
        return sa.String(int(varchar_match.group(1)))

    decimal_match = re.fullmatch(r"DECIMAL\((\d+),(\d+)\)", ddl_type)
    if decimal_match:
        return sa.Numeric(int(decimal_match.group(1)), int(decimal_match.group(2)))

    if ddl_type == "BIGSERIAL":
        return sa.BigInteger()
    if ddl_type == "BIGINT":
        return sa.BigInteger()
    if ddl_type == "INT":
        return sa.Integer()
    if ddl_type == "TEXT":
        return sa.Text()
    if ddl_type == "DATE":
        return sa.Date()
    if ddl_type == "TIMESTAMPTZ":
        return sa.DateTime(timezone=True)
    if ddl_type == "JSONB":
        return JSONB()
    if ddl_type == "BOOLEAN":
        return sa.Boolean()

    return sa.String()


def _server_default(default: str | None):
    if default is None:
        return None
    sa, _, _ = _load_sqlalchemy()
    if default == "NOW()":
        return sa.text("NOW()")
    if default in {"FALSE", "TRUE", "0"}:
        return sa.text(default)
    return sa.text(f"'{default}'")


def _foreign_key(foreign_key: str | None):
    if foreign_key is None:
        return ()
    sa, _, _ = _load_sqlalchemy()
    return (sa.ForeignKey(foreign_key),)


def _autoincrement(column: ColumnSpec):
    if column.ddl_type.upper() == "BIGSERIAL" and column.primary_key:
        return True
    return "auto"


def build_sqlalchemy_table(spec: TableSpec, metadata: Any | None = None):
    sa, _, _ = _load_sqlalchemy()
    target_metadata = metadata or Base.metadata
    if spec.name in target_metadata.tables:
        return target_metadata.tables[spec.name]

    columns = []
    for column in spec.columns:
        columns.append(
            sa.Column(
                column.name,
                _parse_ddl_type(column),
                *_foreign_key(column.foreign_key),
                primary_key=column.primary_key,
                nullable=column.nullable,
                unique=column.unique,
                autoincrement=_autoincrement(column),
                server_default=_server_default(column.default),
                info={"ddl_type": column.ddl_type},
            )
        )

    table = sa.Table(
        spec.name,
        target_metadata,
        *columns,
        info={
            "module": spec.module,
            "hypertable": spec.hypertable,
            "hypertable_time_column": spec.hypertable_time_column,
        },
    )

    for index_name, index_columns in spec.indexes:
        sa.Index(index_name, *[_index_column(table, item) for item in index_columns])

    return table


def _index_column(table: Any, item: str):
    if item.endswith(" DESC"):
        return table.c[item.removesuffix(" DESC")].desc()
    return table.c[item]


def _is_mapped(model_class: type) -> bool:
    sa, _, inspection_errors = _load_sqlalchemy()
    try:
        sa.inspect(model_class)
        return True
    except inspection_errors:
        return False


def map_core_model_classes() -> dict[str, object]:
    _require_sqlalchemy()
    import_core_models()
    specs = get_core_table_specs()
    classes = get_core_model_classes()
    mapped: dict[str, object] = {}

    for table_name, spec in specs.items():
        table = build_sqlalchemy_table(spec)
        model_class = classes[table_name]
        if not _is_mapped(model_class):
            Base.registry.map_imperatively(model_class, table)
        mapped[table_name] = model_class

    return mapped
