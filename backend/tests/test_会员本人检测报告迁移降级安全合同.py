from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


MIGRATION = (
    Path(__file__).parents[1]
    / "app"
    / "migrations"
    / "versions"
    / "20260809_0013_p2_member_detection_report.py"
)


def _module():
    spec = importlib.util.spec_from_file_location("detection_report_0013", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Connection:
    def __init__(self, has_rows: bool):
        self.has_rows = has_rows
        self.sql = []

    def execute(self, statement):
        self.sql.append(str(statement))
        return SimpleNamespace(scalar_one=lambda: self.has_rows)


def test_非空检测报告阻止0013降级且zero_drop() -> None:
    module = _module()
    connection = _Connection(True)
    drops = []
    module.op = SimpleNamespace(
        get_bind=lambda: connection,
        drop_table=lambda *args, **kwargs: drops.append((args, kwargs)),
    )
    with pytest.raises(RuntimeError, match="detection report is not empty"):
        module.downgrade()
    assert drops == []
    assert any("LOCK TABLE public.detection_report" in sql for sql in connection.sql)


def test_空检测报告允许无CASCADE删除单表() -> None:
    module = _module()
    connection = _Connection(False)
    drops = []
    module.op = SimpleNamespace(
        get_bind=lambda: connection,
        drop_table=lambda *args, **kwargs: drops.append((args, kwargs)),
    )
    module.downgrade()
    assert drops == [(('detection_report',), {'schema': 'public'})]
