from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.modules.member_enrollment.domain import (
    AssignmentStatus,
    ConsentRecord,
    ConsentStatus,
    MemberEnrollmentConflict,
    PrimaryTherapistAssignment,
)


NOW = datetime(2026, 8, 17, tzinfo=timezone.utc)


def test_同意只能由accepted撤回且版本递增() -> None:
    value = ConsentRecord(
        consent_record_id=UUID("0198b963-38f0-7d7d-8000-000000000011"),
        status=ConsentStatus.ACCEPTED,
        version=1,
    )
    withdrawn = value.withdraw(expected_version=1, now=NOW)
    assert withdrawn.status is ConsentStatus.WITHDRAWN
    assert withdrawn.version == 2


def test_主健管师accept容量达到30时拒绝() -> None:
    value = PrimaryTherapistAssignment(
        assignment_id=UUID("0198b963-38f0-7d7d-8000-000000000012"),
        status=AssignmentStatus.PENDING_ACCEPTANCE,
        version=1,
    )
    with pytest.raises(MemberEnrollmentConflict, match="THERAPIST_CAPACITY_REACHED"):
        value.accept(expected_version=1, active_case_count=30, capacity_limit=30, now=NOW)


def test_新版重签原子supersede旧版() -> None:
    import inspect
    from app.modules.member_enrollment import service

    publish = inspect.getsource(service.MemberEnrollmentService.publish_consent_document)
    record = inspect.getsource(service.MemberEnrollmentService.record_consent)
    assert "CONSENT_DOCUMENT_RETIRED" in publish
    assert "accepted_consents_for_update" in record
    assert 'status="SUPERSEDED"' in record
