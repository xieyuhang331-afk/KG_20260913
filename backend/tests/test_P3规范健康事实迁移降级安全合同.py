import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest


def _load():
    path = Path(__file__).resolve().parents[1] / "app" / "migrations" / "versions" / "20260811_0015_p3_canonical_health_fact.py"
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Result:
    def __init__(self, exists):
        self._exists = exists

    def scalar_one(self):
        return self._exists


class _Connection:
    def __init__(self, exists):
        self.exists = exists
        self.sql = []

    def execute(self, statement):
        sql = str(statement)
        self.sql.append(sql)
        return _Result(self.exists) if "SELECT EXISTS" in sql else _Result(None)


def test_非空事实或审计阻止0015降级且zero_DDL():
    module = _load()
    connection = _Connection(True)
    with (
        patch.object(module.op, "get_bind", return_value=connection),
        patch.object(module.op, "drop_table") as drop_table,
        patch.object(module.op, "drop_index") as drop_index,
        pytest.raises(RuntimeError, match="Canonical Health Fact facts exist"),
    ):
        module.downgrade()
    assert connection.sql[:2] == [
        "LOCK TABLE public.canonical_health_fact IN ACCESS EXCLUSIVE MODE",
        "LOCK TABLE public.operation_log IN SHARE ROW EXCLUSIVE MODE",
    ]
    drop_table.assert_not_called()
    drop_index.assert_not_called()


def test_全空时按冻结顺序降级且不CASCADE():
    module = _load()
    connection = _Connection(False)
    operations = []
    with (
        patch.object(module.op, "get_bind", return_value=connection),
        patch.object(module.op, "drop_index", side_effect=lambda *a, **k: operations.append(("index", a, k))),
        patch.object(module.op, "drop_table", side_effect=lambda *a, **k: operations.append(("table", a, k))),
    ):
        module.downgrade()
    assert [item[0] for item in operations] == ["index", "index", "table"]
    assert operations[-1][1] == ("canonical_health_fact",)
    assert operations[-1][2] == {"schema": "public"}
