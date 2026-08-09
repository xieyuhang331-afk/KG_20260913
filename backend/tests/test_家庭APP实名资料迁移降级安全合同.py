from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch


REVISION_FILENAME = "20260809_0012_p1_identity_verification_submission.py"


def _module():
    path = Path(__file__).parents[1] / "app/migrations/versions" / REVISION_FILENAME
    spec = importlib.util.spec_from_file_location("identity_submission_0012", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class _Result:
    def __init__(self, value): self.value = value
    def scalar_one(self): return self.value


class _Connection:
    def __init__(self, has_rows): self.has_rows = has_rows; self.sql = []
    def execute(self, statement): self.sql.append(str(statement)); return _Result(self.has_rows)


def test_非空实名Submission阻止0012降级且零drop() -> None:
    module = _module()
    connection = _Connection(True)
    drops = []
    with (
        patch.object(module.op, "get_bind", return_value=connection),
        patch.object(module.op, "drop_table", side_effect=lambda *a, **k: drops.append((a,k))),
    ):
        try:
            module.downgrade()
        except RuntimeError as error:
            assert str(error) == "refusing to downgrade: identity verification submission is not empty"
        else:
            raise AssertionError("non-empty identity submission must block downgrade")
    assert drops == []
    source = Path(module.__file__).read_text(encoding="utf-8").lower()
    assert "cascade" not in source
    assert "drop schema" not in source


def test_空表0012降级只删除目标表() -> None:
    module = _module()
    connection = _Connection(False)
    drops = []
    with (
        patch.object(module.op, "get_bind", return_value=connection),
        patch.object(module.op, "drop_table", side_effect=lambda *a, **k: drops.append((a,k))),
    ):
        module.downgrade()
    assert drops == [(('identity_verification_submission',), {'schema': 'public'})]
