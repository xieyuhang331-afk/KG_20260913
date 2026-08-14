import pytest


def test_Module_C状态枚举不包含ACTIVE():
    from app.modules.organization_projection.service import ShadowState
    assert {item.value for item in ShadowState} == {"SHADOW_RUNNING", "SHADOW_PASSED", "SHADOW_FAILED", "READY"}


def test_READY需要连续两轮同Evidence且无阻断():
    from app.modules.organization_projection.service import require_ready_evidence
    require_ready_evidence(first={"run_id":"a","evidence_digest":"0"*64,"blocker_count":0,"review_required_count":0}, second={"run_id":"b","evidence_digest":"0"*64,"blocker_count":0,"review_required_count":0})
    with pytest.raises(Exception):
        require_ready_evidence(first={"run_id":"a","evidence_digest":"0"*64,"blocker_count":0,"review_required_count":0}, second={"run_id":"b","evidence_digest":"1"*64,"blocker_count":0,"review_required_count":0})


def test_Shadow_success_count只计连续完整相同Evidence且饱和为2():
    from app.modules.organization_projection.service import next_shadow_success_count
    current = type("Run", (), {"run_sequence": 2, "evidence_digest": "A" * 64})()
    same = type("Run", (), {"run_sequence": 1, "evidence_digest": "A" * 64})()
    different = type("Run", (), {"run_sequence": 1, "evidence_digest": "B" * 64})()
    assert next_shadow_success_count(passed=False, previous_count=2, previous_run=same, current_run=current) == 0
    assert next_shadow_success_count(passed=True, previous_count=2, previous_run=different, current_run=current) == 1
    assert next_shadow_success_count(passed=True, previous_count=1, previous_run=same, current_run=current) == 2
    assert next_shadow_success_count(passed=True, previous_count=2, previous_run=same, current_run=current) == 2
