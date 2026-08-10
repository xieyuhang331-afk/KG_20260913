import importlib.util
from pathlib import Path

import sqlalchemy as sa
from unittest.mock import patch


REVISION = "20260811_0015_p3_canonical_health_fact.py"


def _load():
    path = Path(__file__).resolve().parents[1] / "app" / "migrations" / "versions" / REVISION
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_规范健康事实迁移尚未实现():
    module = _load()
    assert module.revision == "20260811_0015"
    assert module.down_revision == "20260810_0014"
    operations = []
    with (
        patch.object(module.op, "create_table", side_effect=lambda *a, **k: operations.append(("table", a, k))),
        patch.object(module.op, "create_index", side_effect=lambda *a, **k: operations.append(("index", a, k))),
        patch.object(module.op, "execute", side_effect=lambda sql: operations.append(("execute", str(sql)))),
    ):
        module.upgrade()
    table_ops = [item for item in operations if item[0] == "table"]
    assert len(table_ops) == 1
    _, args, kwargs = table_ops[0]
    assert args[0] == "canonical_health_fact"
    assert kwargs == {"schema": "public"}
    metadata = sa.MetaData()
    table = sa.Table(args[0], metadata, *args[1:], schema="public")
    assert {column.name for column in table.columns} == {
        "id", "subject_user_id", "indicator_code", "catalog_version", "value_kind",
        "numeric_value", "unit", "measured_at", "received_at", "created_at",
        "source_type", "source_identity_digest", "producer_event_key", "payload_digest",
        "digest_key_id", "supersedes_fact_id", "correction_reason_code", "created_by",
    }
    assert "text_value" not in table.c and "composite_value" not in table.c
    names = {constraint.name for constraint in table.constraints}
    assert "pk_canonical_health_fact" in names
    assert "uq_canonical_health_fact_source_event" in names
    assert "uq_canonical_health_fact_single_successor" in names
    assert sum(item[0] == "index" for item in operations) == 2
    assert operations[-2:] == [
        ("execute", "REVOKE ALL ON TABLE public.canonical_health_fact FROM PUBLIC"),
        ("execute", "REVOKE ALL ON SEQUENCE public.canonical_health_fact_id_seq FROM PUBLIC"),
    ]


def test_0015降级固定锁序且禁止CASCADE和drop_schema():
    module = _load()
    source = Path(module.__file__).read_text(encoding="utf-8")
    first = source.index("LOCK TABLE public.canonical_health_fact IN ACCESS EXCLUSIVE MODE")
    second = source.index("LOCK TABLE public.operation_log IN SHARE ROW EXCLUSIVE MODE")
    check = source.index("SELECT EXISTS")
    assert first < second < check
    assert "CASCADE" not in source
    assert "drop_schema" not in source
    assert "20260728_0006" not in source
