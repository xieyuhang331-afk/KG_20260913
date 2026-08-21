import importlib.util
import inspect
from pathlib import Path
from unittest.mock import patch

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


REVISION_FILENAME = "20260803_0007_p2_identity_member.py"
EXPECTED_COLUMNS = {
    "member_id",
    "member_no",
    "creation_source",
    "status",
    "version",
    "created_at",
    "updated_at",
}


def _load_revision(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_revision_graph(versions: Path):
    revisions = {}
    for path in sorted(versions.glob("*.py")):
        module = _load_revision(path)
        assert module.revision not in revisions, (
            f"duplicate Alembic revision ID: {module.revision}"
        )
        revisions[module.revision] = module

    revision_ids = set(revisions)
    parent_ids = set()
    for revision_id, module in revisions.items():
        down_revision = module.down_revision
        assert down_revision is None or isinstance(down_revision, str), (
            f"revision {revision_id} must have one parent at most"
        )
        if down_revision is not None:
            assert down_revision in revision_ids, (
                f"revision {revision_id} has missing parent: "
                f"{down_revision}"
            )
            parent_ids.add(down_revision)
    return revisions, revision_ids - parent_ids


def test_approved_identity_member_revision_contract():
    versions = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "migrations"
        / "versions"
    )
    revision_path = versions / REVISION_FILENAME
    assert revision_path.is_file(), (
        "approved migration revision does not exist: "
        f"{REVISION_FILENAME}"
    )

    revisions, heads = _load_revision_graph(versions)
    assert "20260728_0006" in revisions
    assert heads == {"20260821_0024"}

    module = revisions["20260803_0007"]
    assert module.revision == "20260803_0007"
    assert module.down_revision == "20260728_0006"
    assert module.branch_labels is None
    assert module.depends_on is None
    assert callable(module.upgrade)
    assert callable(module.downgrade)

    operations = []

    def record_execute(statement):
        operations.append(("execute", str(statement)))

    def record_create_table(*args, **kwargs):
        operations.append(("create_table", args, kwargs))

    with (
        patch.object(module.op, "f", side_effect=lambda name: name),
        patch.object(module.op, "execute", side_effect=record_execute) as execute,
        patch.object(
            module.op,
            "create_table",
            side_effect=record_create_table,
        ) as create_table,
    ):
        module.upgrade()

    execute.assert_called_once()
    create_table.assert_called_once()
    assert operations[0] == ("execute", "CREATE SCHEMA identity")
    assert operations[1][0] == "create_table"

    _, table_args, table_kwargs = operations[1]
    assert table_args[0] == "member"
    assert table_kwargs == {"schema": "identity"}

    metadata = sa.MetaData()
    table = sa.Table(
        table_args[0],
        metadata,
        *table_args[1:],
        schema=table_kwargs["schema"],
    )
    assert set(table.columns.keys()) == EXPECTED_COLUMNS

    member_id = table.c.member_id
    assert isinstance(member_id.type, postgresql.UUID)
    assert member_id.type.as_uuid is True
    assert member_id.nullable is False

    assert isinstance(table.c.member_no.type, sa.String)
    assert table.c.member_no.type.length == 64
    assert isinstance(table.c.creation_source.type, sa.String)
    assert table.c.creation_source.type.length == 32
    assert isinstance(table.c.status.type, sa.String)
    assert table.c.status.type.length == 32
    assert isinstance(table.c.version.type, sa.BigInteger)
    assert isinstance(table.c.created_at.type, sa.DateTime)
    assert table.c.created_at.type.timezone is True
    assert isinstance(table.c.updated_at.type, sa.DateTime)
    assert table.c.updated_at.type.timezone is True

    for column in table.columns:
        assert column.nullable is False
        assert column.default is None
        assert column.server_default is None
        assert column.onupdate is None
        assert column.server_onupdate is None
        assert column.identity is None
        assert column.computed is None

    assert len(table.constraints) == 2
    assert table.primary_key.name == "pk_member"
    assert [column.name for column in table.primary_key.columns] == [
        "member_id"
    ]
    unique_constraints = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    ]
    assert len(unique_constraints) == 1
    assert unique_constraints[0].name == "uq_member_member_no"
    assert [column.name for column in unique_constraints[0].columns] == [
        "member_no"
    ]
    assert not table.foreign_keys
    assert not [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    ]
    assert not table.indexes

    upgrade_source = inspect.getsource(module.upgrade).lower()
    for forbidden in (
        "if not exists",
        "create_all",
        "server_default",
        "create_index",
        "create_trigger",
        "insert(",
        "update(",
        "copy ",
        '"user"',
        '"tenant"',
        '"platform_org"',
        '"health_profile"',
        '"health_indicator"',
    ):
        assert forbidden not in upgrade_source
