import importlib.util
from pathlib import Path
from unittest.mock import patch


REVISION_FILENAME = (
    "20260806_0008_p2_identity_member_no_allocation.py"
)
NON_EMPTY_ERROR = (
    "refusing to downgrade: identity.member_no_allocation is not empty"
)


def _load_revision():
    revision_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "migrations"
        / "versions"
        / REVISION_FILENAME
    )
    spec = importlib.util.spec_from_file_location(
        "p2_member_no_allocation_downgrade_revision",
        revision_path,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _FakeResult:
    def __init__(self, has_rows):
        self._has_rows = has_rows

    def scalar_one(self):
        return self._has_rows


class _FakeConnection:
    def __init__(self, operations, has_rows):
        self._operations = operations
        self._has_rows = has_rows

    def execute(self, statement):
        self._operations.append(("check_rows", str(statement)))
        return _FakeResult(self._has_rows)


def _run_downgrade(module, *, has_rows):
    operations = []
    connection = _FakeConnection(operations, has_rows)

    def get_bind():
        operations.append(("get_bind",))
        return connection

    def drop_table(*args, **kwargs):
        operations.append(("drop_table", args, kwargs))

    def execute(statement):
        operations.append(("execute", str(statement)))

    error = None
    with (
        patch.object(module.op, "get_bind", side_effect=get_bind),
        patch.object(module.op, "drop_table", side_effect=drop_table),
        patch.object(module.op, "execute", side_effect=execute),
    ):
        try:
            module.downgrade()
        except RuntimeError as exc:
            error = exc

    return operations, error


def test_MemberNo分配账本降级前验证空表():
    module = _load_revision()

    non_empty_operations, non_empty_error = _run_downgrade(
        module,
        has_rows=True,
    )
    assert (
        non_empty_operations
        and non_empty_operations[0] == ("get_bind",)
    ), "identity.member_no_allocation downgrade must check rows first"
    assert non_empty_operations[1][0] == "check_rows"
    check_statement = non_empty_operations[1][1].lower()
    assert "select" in check_statement
    assert "identity.member_no_allocation" in check_statement
    assert str(non_empty_error) == NON_EMPTY_ERROR
    assert not [
        operation
        for operation in non_empty_operations
        if operation[0] in {"drop_table", "execute"}
    ]

    empty_operations, empty_error = _run_downgrade(
        module,
        has_rows=False,
    )
    assert empty_error is None
    assert [operation[0] for operation in empty_operations] == [
        "get_bind",
        "check_rows",
        "drop_table",
    ]
    assert empty_operations[2] == (
        "drop_table",
        ("member_no_allocation",),
        {"schema": "identity"},
    )
