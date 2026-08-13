import asyncio
from datetime import UTC, datetime, timedelta

import pytest


def test_generation_state_has_no_module_c_states():
    from app.modules.organization_projection.service import BuildState
    assert {s.value for s in BuildState} == {"BUILDING", "BUILD_COMPLETE", "FAILED", "SUPERSEDED"}


def test_checkpoint_rejects_non_contiguous_progress():
    from app.modules.organization_projection.service import BuildCheckpoint, ProjectionCheckpointConflict
    checkpoint = BuildCheckpoint(last_source_id=4, processed_count=4, projected_count=4, skipped_count=0, remaining_count=2)
    with pytest.raises(ProjectionCheckpointConflict):
        checkpoint.advance(source_ids=(4,), projected=1)


def test_checkpoint_empty_page_requires_zero_remaining():
    from app.modules.organization_projection.service import BuildCheckpoint, ProjectionCheckpointConflict
    checkpoint = BuildCheckpoint(last_source_id=4, processed_count=4, projected_count=4, skipped_count=0, remaining_count=1)
    with pytest.raises(ProjectionCheckpointConflict):
        checkpoint.complete()


def test_completion_contract_requires_source_exhaustion_and_identity_closure():
    from pathlib import Path
    for path in (
        Path("app/modules/organization_projection/service.py"),
        Path("app/modules/health_projection/service.py"),
    ):
        body = path.read_text(encoding="utf-8").split("async def complete", 1)[1].split("\n    async def ", 1)[0]
        assert "has_remaining_sources" in body
        assert "completion_evidence" in body
        assert "validate_completion" in body


def test_confirmation_uses_explicit_full_postimage():
    from pathlib import Path
    for path in (
        Path("app/modules/organization_projection/service.py"),
        Path("app/modules/health_projection/service.py"),
    ):
        body = path.read_text(encoding="utf-8").split("async def confirm_operation", 1)[1]
        assert "expected_postimage" in body.splitlines()[0]
        assert "operation_postimage" in body


def test_checkpoint_validation_rejects_tampered_counts_and_digest():
    from app.modules.organization_projection.service import ProjectionCheckpointConflict, _validate_checkpoint
    generation = type("G", (), {})()
    bad_counts = type("C", (), dict(last_source_id=2, processed_count=2, projected_count=1, skipped_count=0, remaining_count=0, checkpoint_digest="x", version=1))()
    with pytest.raises(ProjectionCheckpointConflict):
        _validate_checkpoint(generation, bad_counts)
    bad_digest = type("C", (), dict(last_source_id=2, processed_count=2, projected_count=2, skipped_count=0, remaining_count=0, checkpoint_digest="0" * 64, version=1))()
    with pytest.raises(ProjectionCheckpointConflict):
        _validate_checkpoint(generation, bad_digest)
