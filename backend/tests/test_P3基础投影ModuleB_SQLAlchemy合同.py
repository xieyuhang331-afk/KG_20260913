from pathlib import Path

import pytest


def test_module_b_models_register_exact_seven_tables_and_stored_key_constraints():
    from app.modules.models import import_core_models

    modules = import_core_models()
    assert "organization_projection" in modules
    assert "health_projection" in modules
    from app.core.database import Base

    expected = {
        "organization_projection_generation",
        "organization_projection_checkpoint",
        "organization_projection",
        "health_projection_generation",
        "health_projection_checkpoint",
        "health_projection_fact",
        "health_projection_window_selection",
    }
    tables = {name.split(".")[-1] for name in Base.metadata.tables}
    assert expected <= tables
    org_fk = {c.name for c in Base.metadata.tables["public.organization_projection"].constraints}
    health_fk = {c.name for c in Base.metadata.tables["public.health_projection_fact"].constraints}
    assert "fk_organization_projection_generation_key" in org_fk
    assert "fk_health_projection_fact_generation_key" in health_fk


def test_module_b_migration_is_linear_and_excludes_module_c_tokens():
    path = Path("app/migrations/versions/20260813_0017_p3_basic_projection_builder_foundation.py")
    source = path.read_text(encoding="utf-8")
    assert 'revision = "20260813_0017"' in source
    assert 'down_revision = "20260812_0016"' in source
    for forbidden in ("READY", "ACTIVE", "shadow", "read_cutover"):
        assert forbidden not in source
    for table in (
        "organization_projection_generation",
        "organization_projection_checkpoint",
        "organization_projection",
        "health_projection_generation",
        "health_projection_checkpoint",
        "health_projection_fact",
        "health_projection_window_selection",
    ):
        assert f'"{table}"' in source


def test_generation_state_contract_has_only_four_states():
    from app.modules.organization_projection.models import PROJECTION_BUILD_STATUSES

    assert PROJECTION_BUILD_STATUSES == {
        "BUILDING",
        "BUILD_COMPLETE",
        "FAILED",
        "SUPERSEDED",
    }
    assert "READY" not in PROJECTION_BUILD_STATUSES


@pytest.mark.parametrize(
    "repository_path",
    (
        "app/modules/organization_projection/repository.py",
        "app/modules/health_projection/repository.py",
    ),
)
def test_projection_audit_insert_is_inline_and_never_requires_returning_select(repository_path):
    source = Path(repository_path).read_text(encoding="utf-8")
    add_audit = source.split("async def add_audit", 1)[1].split("async def ", 1)[0]
    assert "insert(OperationLog).inline()" in add_audit
    assert "session.add(audit)" not in add_audit
    assert "session.flush()" not in add_audit


@pytest.mark.parametrize(
    ("repository_path", "generation_model", "checkpoint_model"),
    (
        (
            "app/modules/organization_projection/repository.py",
            "OrganizationProjectionGeneration",
            "OrganizationProjectionCheckpoint",
        ),
        (
            "app/modules/health_projection/repository.py",
            "HealthProjectionGeneration",
            "HealthProjectionCheckpoint",
        ),
    ),
)
def test_generation_and_checkpoint_persistence_use_only_inline_minimum_columns(
    repository_path, generation_model, checkpoint_model
):
    source = Path(repository_path).read_text(encoding="utf-8")
    add_generation = source.split("async def add_generation", 1)[1].split("async def ", 1)[0]
    assert "nextval(" not in add_generation
    assert f"insert({generation_model}).values(" in add_generation
    assert f".returning({generation_model}.id)" in add_generation
    assert "id=generation.id" not in add_generation
    assert "generation.id = int(result.scalar_one())" in add_generation
    assert "checkpoint.generation_id = generation.id" in add_generation
    assert f"insert({checkpoint_model}).inline()" in add_generation
    assert "session.add(generation)" not in add_generation
    assert "session.add(checkpoint)" not in add_generation
    assert "session.flush()" not in add_generation


def test_health_snapshot_visibility_casts_text_bind_to_txid_snapshot_for_source_and_successor():
    source = Path("app/modules/health_projection/repository.py").read_text(encoding="utf-8")
    visibility = "CAST(:source_snapshot AS txid_snapshot)"

    assert '"health_projection_source_visibility_v1",' in source
    assert "self.visibility = table(" in source
    assert 'schema="public"' in source
    assert f"txid_visible_in_snapshot(inserting_xid, {visibility})" in source
    assert f"txid_visible_in_snapshot(successor.inserting_xid, {visibility})" in source
    assert source.count(visibility) == 3
    snapshot_section = source.split("async def max_source_id", 1)[1].split("async def load_facts", 1)[0]
    assert "self.visibility" in snapshot_section
    assert "self.source.c.numeric_value" not in snapshot_section
