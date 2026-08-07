import importlib.util
from pathlib import Path
from unittest.mock import patch


REVISION_FILENAME = "20260807_0010_p2_identity_self_link_bootstrap.py"
NON_EMPTY_ERROR = (
    "refusing to downgrade: Identity bootstrap tables are not empty"
)


def _load_revision():
    path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "migrations"
        / "versions"
        / REVISION_FILENAME
    )
    spec = importlib.util.spec_from_file_location("identity_bootstrap_0010", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _Connection:
    def __init__(self, operations, values):
        self.operations = operations
        self.values = iter(values)

    def execute(self, statement):
        self.operations.append(("check_rows", str(statement)))
        return _Result(next(self.values))


def _downgrade(values):
    module = _load_revision()
    operations = []
    connection = _Connection(operations, values)
    error = None
    with (
        patch.object(module.op, "get_bind", return_value=connection),
        patch.object(
            module.op,
            "drop_table",
            side_effect=lambda *args, **kwargs: operations.append(
                ("drop_table", args, kwargs)
            ),
        ),
    ):
        try:
            module.downgrade()
        except RuntimeError as caught:
            error = caught
    return operations, error


def test_任一Bootstrap表非空时降级FailClosed且不执行任何Drop():
    for values in ((True,), (False, True)):
        operations, error = _downgrade(values)
        assert str(error) == NON_EMPTY_ERROR
        assert not [item for item in operations if item[0] == "drop_table"]


def test_两表均为空时按依赖逆序删除且不使用Cascade():
    operations, error = _downgrade((False, False))

    assert error is None
    drops = [item for item in operations if item[0] == "drop_table"]
    assert drops == [
        (
            "drop_table",
            ("registration_bootstrap_record",),
            {"schema": "identity"},
        ),
        (
            "drop_table",
            ("user_member_self_link",),
            {"schema": "identity"},
        ),
    ]
