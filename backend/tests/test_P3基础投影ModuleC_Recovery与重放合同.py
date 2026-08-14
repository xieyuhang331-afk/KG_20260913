import pytest

from app.modules.organization_projection.service import (
    ProjectionCheckpointConflict,
    ShadowOperationLedger,
    shadow_operation_preimage,
    verify_shadow_replay,
)


def test_Shadow_operation相同preimage稳定重放():
    ledger = ShadowOperationLedger()
    assert ledger.record("op", {"generation_id": 1}, {"status": "RUNNING"}) == {"status": "RUNNING"}
    assert ledger.record("op", {"generation_id": 1}, {"status": "different"}) == {"status": "RUNNING"}
    with pytest.raises(ProjectionCheckpointConflict):
        ledger.record("op", {"generation_id": 2}, {"status": "RUNNING"})


def test_Shadow_READY不可回退():
    ledger = ShadowOperationLedger(status="READY")
    with pytest.raises(ProjectionCheckpointConflict):
        ledger.transition("SHADOW_RUNNING")


def test_Shadow六类operation使用canonical_preimage且不同请求冲突():
    for action, values in (
        ("SHADOW_START", {"generation_id": 1, "run_id": "r1", "validator_id": "v1"}),
        ("SHADOW_HEARTBEAT", {"generation_id": 1, "run_id": "r1", "validator_id": "v1", "lease_epoch": 0}),
        ("SHADOW_TAKEOVER", {"generation_id": 1, "run_id": "r1", "validator_id": "v2", "lease_epoch": 0}),
        ("SHADOW_COMPLETE", {"generation_id": 1, "run_id": "r1"}),
        ("SHADOW_FAIL", {"generation_id": 1, "run_id": "r1"}),
        ("GENERATION_READY", {"generation_id": 1, "expected_version": 4}),
    ):
        preimage = shadow_operation_preimage(action, **values)
        audit = type("Audit", (), {
            "action": action,
            "preimage_digest": __import__("app.modules.organization_projection.service", fromlist=["x"])._shadow_digest(preimage),
            "postimage_digest": __import__("app.modules.organization_projection.service", fromlist=["x"])._shadow_digest({"status": "ok"}),
        })()
        verify_shadow_replay(audit=audit, action=action, preimage=preimage, postimage={"status": "ok"})
        with pytest.raises(ProjectionCheckpointConflict):
            verify_shadow_replay(audit=audit, action=action, preimage={**preimage, "generation_id": 2}, postimage={"status": "ok"})


def test_历史operation重放不依赖后续可变状态重建postimage():
    from app.modules.organization_projection.service import _shadow_digest

    preimage = shadow_operation_preimage(
        "SHADOW_HEARTBEAT", generation_id=1, run_id="r1",
        validator_id="v1", lease_epoch=0,
    )
    audit = type("Audit", (), {
        "action": "SHADOW_HEARTBEAT",
        "preimage_digest": _shadow_digest(preimage),
        "postimage_digest": _shadow_digest({"lease_epoch": 0, "run_version": 2}),
    })()
    # Later takeover changes current lease/version; replay validates the persisted
    # historical audit rather than rebuilding that old postimage from live state.
    verify_shadow_replay(audit=audit, action="SHADOW_HEARTBEAT", preimage=preimage)


def test_Shadow完整postimage覆盖generation_run及全部Evidence字段():
    from app.modules.organization_projection.service import _shadow_postimage

    generation = type("Generation", (), {
        "id": 1, "projection_version": 1, "generation_no": 2,
        "status": "SHADOW_PASSED", "high_watermark": {"max_organization_id": 4},
        "digest_key_id": "k1", "input_digest": "a" * 64,
        "start_operation_id": "g-start", "builder_id": None, "lease_epoch": 3,
        "lease_expires_at": None, "created_at": None, "updated_at": None,
        "completed_at": None, "failure_code": None, "version": 7,
        "current_shadow_run_id": "r1", "shadow_success_count": 1,
        "ready_at": None, "ready_operation_id": None,
    })()
    run = type("Run", (), {
        "run_id": "r1", "generation_id": 1, "run_sequence": 2,
        "status": "PASSED", "projection_version": 1,
        "rule_version": "organization-projection-v1",
        "high_watermark": {"max_organization_id": 4},
        "high_watermark_digest": "B" * 64, "digest_key_id": "k1",
        "generation_input_digest": "a" * 64, "source_digest": "C" * 64,
        "mapping_digest": "D" * 64, "projection_digest": "E" * 64,
        "coverage_digest": "F" * 64, "evidence_digest": "0" * 64,
        "blocker_count": 0, "review_required_count": 0,
        "informational_count": 0, "category_counts": {}, "source_count": 1,
        "eligible_count": 1, "projection_count": 1,
        "coverage_numerator": 1, "coverage_denominator": 1,
        "start_operation_id": "s", "complete_operation_id": "c",
        "validator_id": "v", "lease_epoch": 0, "lease_expires_at": None,
        "started_at": None, "completed_at": None, "version": 2,
    })()
    postimage = _shadow_postimage(generation=generation, run=run)
    for field in (
        "generation_high_watermark", "generation_projection_version",
        "generation_digest_key_id", "generation_input_digest", "run_sequence",
        "rule_version", "high_watermark", "source_digest", "mapping_digest",
        "projection_digest", "coverage_digest", "category_counts",
        "blocker_count", "review_required_count", "informational_count",
        "coverage_numerator", "coverage_denominator", "start_operation_id",
    ):
        assert field in postimage


def test_canonical_postimage_generation_fields_match_explicit_projection():
    from pathlib import Path
    from app.modules.organization_projection.service import _SHADOW_GENERATION_POSTIMAGE_FIELDS

    assert _SHADOW_GENERATION_POSTIMAGE_FIELDS == (
        "id", "projection_version", "status", "high_watermark",
        "digest_key_id", "input_digest", "updated_at", "completed_at",
        "version", "current_shadow_run_id", "shadow_success_count",
        "ready_at", "ready_operation_id",
    )
    for relative in (
        "app/modules/organization_projection/repository.py",
        "app/modules/health_projection/repository.py",
    ):
        text = (Path(__file__).resolve().parents[1] / relative).read_text("utf-8")
        shadow = text.split("async def get_shadow_generation", 1)[1].split("async def get_checkpoint", 1)[0]
        for field in _SHADOW_GENERATION_POSTIMAGE_FIELDS:
            assert f'"{field}"' in shadow
        for forbidden in ("generation_no", "start_operation_id", "builder_id", "lease_epoch", "lease_expires_at", "created_at", "failure_code"):
            assert f'"{forbidden}"' not in shadow
