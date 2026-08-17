from pathlib import Path

from app.modules.therapist_qualification.models import EVENT_TYPES
from app.tasks.therapist_qualification_tasks import (
    CONSUME_TASK,
    DISPATCH_TASK,
    INVITATION_EXPIRY_TASK,
    READINESS_SWEEP_TASK,
    RECOVER_TASK,
    reopen_failed_event,
)


def test_claim_retry_failed_recovery与原子Delivery完整postimage():
    assert len(EVENT_TYPES) == 16
    assert len(set(EVENT_TYPES)) == 16
    assert DISPATCH_TASK != CONSUME_TASK != RECOVER_TASK
    assert INVITATION_EXPIRY_TASK != READINESS_SWEEP_TASK
    assert callable(reopen_failed_event)


def test_Consumer按固定顺序await每个目标currentness():
    source = (
        Path(__file__).parents[1]
        / "app/tasks/therapist_qualification_tasks.py"
    ).read_text(encoding="utf-8")
    assert "any(not await" not in source
    assert "for target in targets:" in source
    assert "if not await _target_is_current(session, target):" in source
