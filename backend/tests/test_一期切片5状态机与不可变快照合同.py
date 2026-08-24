from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.modules.health_assessment.domain import (
    AssessmentInputSnapshot,
    HealthAssessmentState,
    next_assessment_state,
)


UUID7_A = UUID("018f62f6-52c7-7a51-8f75-0c4fe6b15210")
UUID7_B = UUID("018f62f6-52c7-7a51-8f75-0c4fe6b15211")


def test_输入快照不可变并永久绑定Assembly规则和水位():
    snapshot = AssessmentInputSnapshot(
        snapshot_id=UUID7_A,
        assembly_id=UUID7_B,
        assembly_digest="assembly-digest",
        rule_set_code="CN_ADULT_BASELINE_V1",
        rule_set_digest="medical-digest",
        required_max_fact_id=42,
        required_max_status_event_seq=11,
        source_vector_digest="source-digest",
        created_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )
    with pytest.raises(FrozenInstanceError):
        snapshot.required_max_fact_id = 43


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("DRAFT_SNAPSHOT", "RUNNING"),
        ("RUNNING", "COMPLETED"),
        ("RUNNING", "FAILED"),
        ("COMPLETED", "UNDER_REVIEW"),
        ("UNDER_REVIEW", "SUPERSEDED"),
    ],
)
def test_评估只允许冻结状态转换(current, target):
    assert next_assessment_state(HealthAssessmentState(current), HealthAssessmentState(target)).value == target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("DRAFT_SNAPSHOT", "COMPLETED"),
        ("COMPLETED", "RUNNING"),
        ("FAILED", "RUNNING"),
        ("UNDER_REVIEW", "COMPLETED"),
        ("SUPERSEDED", "COMPLETED"),
    ],
)
def test_非法状态跃迁固定拒绝(current, target):
    with pytest.raises(ValueError, match="STATE_CONFLICT"):
        next_assessment_state(HealthAssessmentState(current), HealthAssessmentState(target))


def test_代理争议在权限语义未批准时固定fail_closed():
    from app.modules.health_assessment import api

    source = api.__loader__.get_source(api.__name__)
    route = source.split("async def proxy_dispute", 1)[1].split("def _canonical", 1)[0]
    assert 'raise _error("PROXY_PERMISSION_FORBIDDEN")' in route


def test_Authority十六进制摘要与byte摘要进入同一ExpectedPostimage规范边界():
    from app.modules.health_assessment.service import build_assessment_start_postimage

    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    payload = {
        "assessment_id": UUID7_A,
        "service_case_id": UUID7_B,
        "subject_member_id": UUID7_A,
        "tenant_id": 1,
        "sequence_no": 1,
        "snapshot_id": UUID7_B,
        "rule_set_version_id": UUID7_A,
        "supersedes_assessment_id": None,
        "actor_user_id": 1,
        "created_at": now,
        "assembly_id": UUID7_A,
        "assembly_digest": "11" * 32,
        "source_vector_digest": "22" * 32,
        "profile_revision_id": UUID7_B,
        "consent_version_ids": [],
        "projection_version": 2,
        "projection_rule_version": "slice4-v2",
        "required_max_fact_id": 0,
        "required_max_status_event_seq": 0,
        "source_snapshot": "synthetic",
        "fact_ref_manifest_digest": "33" * 32,
        "snapshot_digest": bytes.fromhex("44" * 32),
        "digest_key_id": "digest-v1",
        "audit_id": UUID7_A,
        "audit_digest": bytes.fromhex("55" * 32),
        "event_id": UUID7_B,
        "outbox_digest": bytes.fromhex("66" * 32),
        "receipt_id": UUID7_A,
        "idempotency_key": "slice5-test-key",
        "request_digest": bytes.fromhex("77" * 32),
    }
    result = build_assessment_start_postimage(payload, response={"status": "DRAFT_SNAPSHOT"})
    assert result["snapshot"]["assembly_digest"] == "11" * 32
    assert result["snapshot"]["snapshot_digest"] == "44" * 32
    with pytest.raises(ValueError, match="DEPENDENCY_UNAVAILABLE"):
        build_assessment_start_postimage(
            {**payload, "assembly_digest": "not-a-digest"},
            response={"status": "DRAFT_SNAPSHOT"},
        )
