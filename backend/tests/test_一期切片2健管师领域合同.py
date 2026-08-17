from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.modules.therapist_qualification.domain import (
    InvitationStatus,
    ProfileStatus,
    QualificationInput,
    TherapistConflict,
    TherapistInvitation,
    TherapistProfile,
)


def test_邀请激活终态与失败次数合同():
    now = datetime.now(timezone.utc)
    value = TherapistInvitation(UUID(int=1), 7, now + timedelta(minutes=5), now)
    assert value.activate(now=now).status is InvitationStatus.ACTIVATED
    for _ in range(8):
        value = value.record_failed_attempt(now=now)
    assert value.failed_attempts == 5
    with pytest.raises(TherapistConflict, match="ATTEMPTS_EXHAUSTED"):
        value.activate(now=now)
    expirable = TherapistInvitation(UUID(int=3), 7, now, now - timedelta(minutes=5))
    assert expirable.expire(expected_version=1, now=now).status is InvitationStatus.EXPIRED
    with pytest.raises(TherapistConflict, match="NOT_EXPIRABLE"):
        TherapistInvitation(UUID(int=4), 7, now + timedelta(minutes=5), now).expire(
            expected_version=1,
            now=now,
        )


def test_Profile完整状态机与乐观锁合同():
    profile = TherapistProfile(UUID(int=2), 9, 7, ProfileStatus.ACTIVATED)
    draft = profile.save_draft(expected_version=1)
    submitted = draft.submit(expected_version=2, revision_no=1)
    assert submitted.status is ProfileStatus.SUBMITTED
    with pytest.raises(TherapistConflict, match="VERSION_CONFLICT"):
        draft.submit(expected_version=1, revision_no=1)


def test_V1每revision精确一项资质且一至三个附件():
    good = QualificationInput("METABOLIC_HEALTH_PRACTICE", date(2026, 1, 1), date(2027, 1, 1), (UUID(int=1),))
    good.validate()
    with pytest.raises(TherapistConflict, match="ATTACHMENTS_INVALID"):
        QualificationInput("METABOLIC_HEALTH_PRACTICE", date(2026, 1, 1), date(2027, 1, 1), ()).validate()
