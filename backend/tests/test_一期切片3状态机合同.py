from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.modules.member_enrollment.domain import (
    EnrollmentStatus,
    InvitationStatus,
    MemberEnrollmentConflict,
    MemberServiceInvitation,
    ProxyGrant,
    ProxyGrantStatus,
)


NOW = datetime(2026, 8, 17, tzinfo=timezone.utc)
INVITATION_ID = UUID("0198b963-38f0-7d7d-8000-000000000001")
ENROLLMENT_ID = UUID("0198b963-38f0-7d7d-8000-000000000002")
PRINCIPAL_ID = UUID("0198b963-38f0-7d7d-8000-000000000003")
PROXY_ID = UUID("0198b963-38f0-7d7d-8000-000000000004")


def test_邀请第五次失败后正确码也不能激活() -> None:
    value = MemberServiceInvitation(
        invitation_id=INVITATION_ID,
        tenant_id=1,
        mode="SELF",
        status=InvitationStatus.INVITED,
        expires_at=NOW + timedelta(hours=1),
        version=1,
    )
    for _ in range(5):
        value = value.record_failed_attempt(now=NOW)
    assert value.status is InvitationStatus.EXPIRED
    assert value.failed_attempts == 5
    with pytest.raises(MemberEnrollmentConflict, match="INVITATION_EXHAUSTED"):
        value.accept(expected_version=value.version, now=NOW)


def test_enrollment没有持久invited状态() -> None:
    assert "INVITED" not in {item.value for item in EnrollmentStatus}
    assert EnrollmentStatus.ACCEPTED.value == "ACCEPTED"


def test_proxy必须见证与同意后才能active() -> None:
    grant = ProxyGrant.create_pending(
        grant_id=UUID("0198b963-38f0-7d7d-8000-000000000005"),
        enrollment_id=ENROLLMENT_ID,
        principal_member_id=PRINCIPAL_ID,
        proxy_member_id=PROXY_ID,
        slot_no=1,
    )
    assert grant.status is ProxyGrantStatus.CONSENT_PENDING
    with pytest.raises(MemberEnrollmentConflict, match="PROXY_WITNESS_REQUIRED"):
        grant.activate(
            witness_decision_id=None,
            authorization_document_version_id=UUID(
                "0198b963-38f0-7d7d-8000-000000000006"
            ),
            expected_version=1,
            now=NOW,
        )


@pytest.mark.parametrize("slot_no", [0, 3, True])
def test_proxy槽位仅允许一和二(slot_no) -> None:
    with pytest.raises(MemberEnrollmentConflict, match="PROXY_SLOT_INVALID"):
        ProxyGrant.create_pending(
            grant_id=UUID("0198b963-38f0-7d7d-8000-000000000007"),
            enrollment_id=ENROLLMENT_ID,
            principal_member_id=PRINCIPAL_ID,
            proxy_member_id=PROXY_ID,
            slot_no=slot_no,
        )


def test_邀请到期只转换一次并写audit_outbox() -> None:
    import inspect
    from app.modules.member_enrollment import service
    from app.tasks import member_enrollment_tasks

    service_source = inspect.getsource(service.MemberEnrollmentService.expire_invitations)
    task_source = inspect.getsource(member_enrollment_tasks.expire_member_invitations_task)
    assert "MEMBER_INVITATION_EXPIRED" in service_source
    assert "enrollment_writer" in task_source
    assert "workflow_worker" not in task_source


def test_C1邀请过期任务固定route与60秒beat() -> None:
    from app.tasks.celery_app import MEMBER_ENROLLMENT_QUEUE, create_celery_app
    from app.tasks.member_enrollment_tasks import EXPIRY_TASK

    app = create_celery_app(broker_url="memory://")
    assert app.conf.task_routes[EXPIRY_TASK] == {"queue": MEMBER_ENROLLMENT_QUEUE}
    entries = [
        value
        for value in app.conf.beat_schedule.values()
        if value.get("task") == EXPIRY_TASK
    ]
    assert entries == [
        {
            "task": EXPIRY_TASK,
            "schedule": 60.0,
            "options": {"queue": MEMBER_ENROLLMENT_QUEUE},
        }
    ]
