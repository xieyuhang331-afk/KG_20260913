import importlib.util
import inspect
from pathlib import Path
from unittest.mock import patch


REVISION_FILENAME = "20260808_0011_p1_registration_verified_outbox.py"
OUTBOX_ERROR = (
    "refusing to downgrade: registration durable outbox is not empty"
)
VERIFICATION_IDENTITY_ERROR = (
    "refusing to downgrade: verification event identity is populated"
)
OUTBOX_CONTRACT_MESSAGE = (
    "Registration durable outbox downgrade does not protect non-empty outbox"
)
VERIFICATION_IDENTITY_CONTRACT_MESSAGE = (
    "Registration durable outbox downgrade does not protect verification "
    "event identity"
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
        "p1_registration_verified_outbox_downgrade_revision",
        revision_path,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


class _Connection:
    def __init__(self, operations, *, outbox_has_rows, verification_identity_exists):
        self._operations = operations
        self._outbox_has_rows = outbox_has_rows
        self._verification_identity_exists = verification_identity_exists

    def execute(self, statement):
        sql = str(statement)
        self._operations.append(("sql", sql))
        normalized = " ".join(sql.lower().split())
        if normalized.startswith("select exists"):
            if "registration_verified_outbox" in normalized:
                return _Result(self._outbox_has_rows)
            if "identity_verification_decision" in normalized:
                return _Result(self._verification_identity_exists)
        return _Result(None)


def _run_downgrade(*, outbox_has_rows, verification_identity_exists):
    module = _load_revision()
    operations = []
    connection = _Connection(
        operations,
        outbox_has_rows=outbox_has_rows,
        verification_identity_exists=verification_identity_exists,
    )

    def record(name):
        return lambda *args, **kwargs: operations.append((name, args, kwargs))

    error = None
    with (
        patch.object(module.op, "get_bind", return_value=connection),
        patch.object(module.op, "drop_table", side_effect=record("drop_table")),
        patch.object(module.op, "drop_index", side_effect=record("drop_index")),
        patch.object(
            module.op,
            "drop_constraint",
            side_effect=record("drop_constraint"),
        ),
        patch.object(module.op, "drop_column", side_effect=record("drop_column")),
        patch.object(
            module.op,
            "drop_schema",
            side_effect=record("drop_schema"),
            create=True,
        ),
        patch.object(module.op, "alter_column", side_effect=record("alter_column")),
        patch.object(module.op, "f", side_effect=lambda name: name),
    ):
        try:
            module.downgrade()
        except RuntimeError as caught:
            error = caught

    return module, operations, error


def _mutations(operations):
    return [operation for operation in operations if operation[0] != "sql"]


def _destructive_sql(operations):
    forbidden = {"alter", "create", "drop", "truncate"}
    return [
        operation
        for operation in operations
        if operation[0] == "sql"
        and forbidden.intersection(operation[1].lower().split())
    ]


def _assert_empty_state_downgrade_order():
    module, operations, error = _run_downgrade(
        outbox_has_rows=False,
        verification_identity_exists=False,
    )

    assert error is None
    mutations = _mutations(operations)
    assert mutations == [
        (
            "drop_table",
            ("registration_verified_outbox",),
            {"schema": "public"},
        ),
        (
            "drop_index",
            ("uq_identity_verification_registration_event_present",),
            {
                "table_name": "identity_verification_decision",
                "schema": "public",
            },
        ),
        (
            "drop_index",
            ("uq_identity_verification_authority_key_present",),
            {
                "table_name": "identity_verification_decision",
                "schema": "public",
            },
        ),
        (
            "drop_constraint",
            (
                "uq_identity_verification_event_identity",
                "identity_verification_decision",
            ),
            {"type_": "unique", "schema": "public"},
        ),
        (
            "drop_constraint",
            (
                "ck_identity_verification_decision_"
                "identity_verification_authority_key_sha256",
                "identity_verification_decision",
            ),
            {"type_": "check", "schema": "public"},
        ),
        (
            "drop_constraint",
            (
                "ck_identity_verification_decision_"
                "identity_verification_event_identity_complete",
                "identity_verification_decision",
            ),
            {"type_": "check", "schema": "public"},
        ),
        (
            "drop_column",
            ("identity_verification_decision", "registration_event_id"),
            {"schema": "public"},
        ),
        (
            "drop_column",
            ("identity_verification_decision", "authority_decision_key"),
            {"schema": "public"},
        ),
    ]
    source = inspect.getsource(module.downgrade).lower()
    assert _destructive_sql(operations) == []
    assert "drop schema" not in source
    assert "cascade" not in source


def test_非空Outbox阻止0011降级():
    _, operations, error = _run_downgrade(
        outbox_has_rows=True,
        verification_identity_exists=False,
    )

    assert str(error) == OUTBOX_ERROR, OUTBOX_CONTRACT_MESSAGE
    assert _mutations(operations) == [], OUTBOX_CONTRACT_MESSAGE
    assert _destructive_sql(operations) == [], OUTBOX_CONTRACT_MESSAGE
    queries = [operation[1].lower() for operation in operations if operation[0] == "sql"]
    assert any(
        "select exists" in query and "registration_verified_outbox" in query
        for query in queries
    ), OUTBOX_CONTRACT_MESSAGE
    _assert_empty_state_downgrade_order()


def test_非空Verification审计身份阻止0011降级():
    _, operations, error = _run_downgrade(
        outbox_has_rows=False,
        verification_identity_exists=True,
    )

    assert str(error) == VERIFICATION_IDENTITY_ERROR, (
        VERIFICATION_IDENTITY_CONTRACT_MESSAGE
    )
    assert _mutations(operations) == [], VERIFICATION_IDENTITY_CONTRACT_MESSAGE
    assert _destructive_sql(operations) == [], (
        VERIFICATION_IDENTITY_CONTRACT_MESSAGE
    )
    verification_queries = [
        " ".join(operation[1].lower().split())
        for operation in operations
        if operation[0] == "sql"
        and "identity_verification_decision" in operation[1].lower()
        and "select exists" in operation[1].lower()
    ]
    assert len(verification_queries) == 1, VERIFICATION_IDENTITY_CONTRACT_MESSAGE
    assert (
        "authority_decision_key is not null or "
        "registration_event_id is not null" in verification_queries[0]
    ), VERIFICATION_IDENTITY_CONTRACT_MESSAGE
    _assert_empty_state_downgrade_order()
