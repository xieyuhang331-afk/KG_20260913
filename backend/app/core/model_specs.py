from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    ddl_type: str
    nullable: bool = True
    primary_key: bool = False
    unique: bool = False
    foreign_key: str | None = None
    default: str | None = None


@dataclass(frozen=True)
class TableSpec:
    name: str
    module: str
    columns: tuple[ColumnSpec, ...]
    indexes: tuple[tuple[str, tuple[str, ...]], ...] = ()
    hypertable: bool = False
    hypertable_time_column: str | None = None

    def column(self, name: str) -> ColumnSpec:
        for column in self.columns:
            if column.name == name:
                return column
        raise KeyError(f"column not found in {self.name}: {name}")


_CORE_TABLE_SPECS: dict[str, TableSpec] = {}


def register_core_table_specs(*specs: TableSpec) -> None:
    for spec in specs:
        _CORE_TABLE_SPECS[spec.name] = spec


def get_core_table_specs() -> dict[str, TableSpec]:
    return dict(_CORE_TABLE_SPECS)
