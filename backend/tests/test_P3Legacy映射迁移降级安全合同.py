import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest


PATH = Path(__file__).resolve().parents[1] / "app" / "migrations" / "versions" / "20260812_0016_p3_organization_health_legacy_mapping.py"


def _load():
    spec = importlib.util.spec_from_file_location(PATH.stem, PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class Result:
    def __init__(self, value): self.value=value
    def scalar_one(self): return self.value


class Connection:
    def __init__(self, exists): self.exists=exists; self.sql=[]
    def execute(self, statement):
        sql=str(statement); self.sql.append(sql)
        return Result(self.exists if "SELECT EXISTS" in sql else None)


def test_非空Mapping或审计阻止0016降级且zero_DDL():
    module=_load(); connection=Connection(True)
    with patch.object(module.op,"get_bind",return_value=connection), patch.object(module.op,"drop_table") as drop_table, patch.object(module.op,"drop_index") as drop_index, pytest.raises(RuntimeError,match="legacy mapping facts exist"):
        module.downgrade()
    assert connection.sql[:3] == [
        "LOCK TABLE public.organization_legacy_mapping IN ACCESS EXCLUSIVE MODE",
        "LOCK TABLE public.health_indicator_legacy_mapping IN ACCESS EXCLUSIVE MODE",
        "LOCK TABLE public.operation_log IN SHARE ROW EXCLUSIVE MODE",
    ]
    drop_table.assert_not_called(); drop_index.assert_not_called()


def test_全空时才按冻结顺序降级():
    module=_load(); connection=Connection(False); operations=[]
    with patch.object(module.op,"get_bind",return_value=connection), patch.object(module.op,"drop_index",side_effect=lambda *a,**k:operations.append(("index",a,k))), patch.object(module.op,"drop_table",side_effect=lambda *a,**k:operations.append(("table",a,k))):
        module.downgrade()
    assert [item[0] for item in operations][-2:] == ["table","table"]
    assert {item[1][0] for item in operations[-2:]} == {"organization_legacy_mapping","health_indicator_legacy_mapping"}
