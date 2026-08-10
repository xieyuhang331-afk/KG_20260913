from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest


NOW = datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc)


def _current_user():
    return SimpleNamespace(
        id=17,
        role="super_admin",
        tenant_id=None,
        org_id=None,
    )


def _reviewer(**overrides):
    values = {
        "id": 17,
        "role": "super_admin",
        "status": "active",
        "tenant_id": None,
        "org_id": None,
        "verify_status": None,
        "updated_at": NOW,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


INVALID_REVIEWERS = [
    pytest.param(_reviewer(status="disabled"), id="disabled"),
    pytest.param(_reviewer(role="province_admin"), id="demoted"),
    pytest.param(_reviewer(tenant_id=9), id="tenant-bound"),
    pytest.param(_reviewer(org_id=3), id="org-bound"),
]


class _ApplicationRepository:
    def __init__(self, reviewer):
        self.reviewer = reviewer
        self.events: list[str] = []

    async def get_reviewer_state(self, reviewer_id: int):
        self.events.append("reviewer-currentness")
        assert reviewer_id == 17
        return self.reviewer

    async def list_submitted(self, **kwargs):
        self.events.append("queue-read")
        return []

    async def count_submitted(self):
        self.events.append("queue-count")
        return 0

    async def get_submission(self, **kwargs):
        self.events.append("submission-read")
        return SimpleNamespace(
            submission_id="submission-1",
            user_ref=42,
            version=1,
            status="submitted",
            real_name_ciphertext=b"encrypted-name",
            real_name_nonce=b"name-nonce-1",
            id_card_ciphertext=b"encrypted-card",
            id_card_nonce=b"card-nonce-12",
            key_id="v1",
            content_digest="a" * 64,
            submitted_at=NOW,
        )

    async def add_sensitive_read_audit(self, **kwargs):
        self.events.append("sensitive-read-audit")

    async def commit(self):
        self.events.append("application-commit")

    async def rollback(self):
        self.events.append("application-rollback")


class _Crypto:
    key_id = "v1"

    def __init__(self, events):
        self.events = events

    def decrypt(self, encrypted, *, aad):
        self.events.append("pii-decrypt")
        return "sensitive-value"

    def digest(self, *parts):
        self.events.append("reject-digest")
        return "d" * 64

    def aad(self, **kwargs):
        return b"aad"


class _WriterRepository:
    events: list[str] = []

    def __init__(self, session):
        del session

    async def get_submission(self, **kwargs):
        self.events.append("writer-submission-read")
        return SimpleNamespace(
            submission_id="submission-1",
            user_ref=42,
            version=1,
            status="submitted",
        )

    async def mark_rejected(self, **kwargs):
        self.events.append("writer-reject")
        return True

    async def commit(self):
        self.events.append("writer-commit")


def _writer_factory(events):
    @asynccontextmanager
    async def factory():
        events.append("writer-session-open")
        yield object()

    return factory


def _service(reviewer, monkeypatch):
    from app.modules.auth.manual_identity_review_application import (
        PlatformIdentitySubmissionReviewService,
    )
    from app.modules.auth import manual_identity_review_repository

    repository = _ApplicationRepository(reviewer)
    _WriterRepository.events = repository.events
    monkeypatch.setattr(
        manual_identity_review_repository,
        "SqlAlchemyPlatformIdentitySubmissionReviewRepository",
        _WriterRepository,
    )
    return (
        PlatformIdentitySubmissionReviewService(
            application_repository=repository,
            writer_session_factory=_writer_factory(repository.events),
            crypto=_Crypto(repository.events),
        ),
        repository,
    )


@pytest.mark.parametrize("reviewer", INVALID_REVIEWERS)
@pytest.mark.parametrize("operation", ["queue", "detail", "reject"])
def test_旧JWT声明super_admin但数据库reviewer已漂移时全路径fail_closed(
    reviewer, operation, monkeypatch
) -> None:
    from app.modules.auth.manual_identity_review_application import (
        PlatformAdminManualIdentityReviewForbidden,
    )
    from app.modules.review.schemas import IdentityReviewRejectRequest

    service, repository = _service(reviewer, monkeypatch)

    async def execute():
        if operation == "queue":
            return await service.list_queue(
                current_user=_current_user(), page=1, page_size=20
            )
        if operation == "detail":
            return await service.detail(
                current_user=_current_user(),
                user_ref=42,
                purpose_code="MANUAL_REVIEW",
            )
        return await service.reject(
            current_user=_current_user(),
            user_ref=42,
            request=IdentityReviewRejectRequest(
                idempotency_key="reject-request-v1",
                submission_version=1,
                reason_code="OFFLINE_CHECK_FAILED",
                decided_at=NOW,
            ),
        )

    with pytest.raises(PlatformAdminManualIdentityReviewForbidden):
        asyncio.run(execute())

    assert repository.events == ["reviewer-currentness"]


def test_reviewer数据库投影仅包含currentness字段且包含组织绑定() -> None:
    import inspect

    from app.modules.auth import manual_identity_review_repository as repository

    statement_factory = getattr(repository, "_reviewer_projection_statement", None)
    assert statement_factory is not None, "reviewer currentness projection is not implemented"
    source = inspect.getsource(statement_factory)
    for required in (
        "User.id",
        "User.role",
        "User.status",
        "User.tenant_id",
        "User.verify_status",
        "User.updated_at",
        "Tenant.org_id",
    ):
        assert required in source
    for forbidden in (
        "User.phone",
        "User.real_name",
        "User.id_card",
        "User.password_hash",
    ):
        assert forbidden not in source


def test_reviewer_currentness读取取消原样传播且未知异常安全映射() -> None:
    from app.modules.auth.manual_identity_review_application import (
        PlatformAdminManualIdentityReviewUnavailable,
        PlatformIdentitySubmissionReviewService,
    )

    class FailingRepository:
        def __init__(self, error):
            self.error = error

        async def get_reviewer_state(self, reviewer_id):
            del reviewer_id
            raise self.error

    async def execute(error):
        service = PlatformIdentitySubmissionReviewService(
            application_repository=FailingRepository(error),
            writer_session_factory=None,
            crypto=None,
        )
        return await service.list_queue(
            current_user=_current_user(), page=1, page_size=20
        )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(execute(asyncio.CancelledError()))

    vendor_error = RuntimeError("vendor connection target and credential")
    with pytest.raises(PlatformAdminManualIdentityReviewUnavailable) as failure:
        asyncio.run(execute(vendor_error))
    assert failure.value.__cause__ is None
    assert failure.value.__context__ is None
    assert "vendor" not in str(failure.value)
