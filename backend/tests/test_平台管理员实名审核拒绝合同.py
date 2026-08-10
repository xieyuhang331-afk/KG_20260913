from __future__ import annotations

from types import SimpleNamespace
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi.testclient import TestClient


class _Service:
    async def reject(self, **kwargs):
        return SimpleNamespace(version=3), False


def _headers():
    from app.core.security import create_access_token
    return {"Authorization": "Bearer " + create_access_token({"sub": "17", "role": "super_admin"})}


def test_平台管理员可拒绝当前Submission且响应无PII() -> None:
    from app.main import create_app
    from app.modules.review.api import get_platform_identity_submission_review_service
    app = create_app()
    app.dependency_overrides[get_platform_identity_submission_review_service] = lambda: _Service()
    response = TestClient(app).post(
        "/api/v1/reviews/users/42/identity/reject",
        headers=_headers(),
        json={
            "submission_version": 3,
            "idempotency_key": "reject-request-v3",
            "reason_code": "OFFLINE_CHECK_FAILED",
        },
    )
    assert response.status_code == 200
    assert response.json()["data"] == {"user_id": 42, "submission_version": 3, "status": "rejected", "replayed": False}
    assert "id_card" not in response.text and "real_name" not in response.text


class _Crypto:
    def digest(self, namespace, value):
        return f"{namespace}:{value}"


class _Repository:
    def __init__(self, model, *, commit_error=None, on_commit=None):
        self.model = model
        self.commit_error = commit_error
        self.on_commit = on_commit
        self.calls = []

    async def get_submission(self, **kwargs):
        self.calls.append(("get", kwargs))
        return self.model

    async def mark_rejected(self, **kwargs):
        self.calls.append(("mark", kwargs))
        self.model.status = "rejected"
        self.model.reviewed_by = kwargs["reviewer_id"]
        self.model.evidence_digest = kwargs["evidence_digest"]
        return True

    async def commit(self):
        self.calls.append("commit")
        if self.on_commit is not None:
            self.on_commit()
        if self.commit_error:
            raise self.commit_error


class _ApplicationRepository:
    async def get_reviewer_state(self, reviewer_id):
        return SimpleNamespace(
            id=reviewer_id, role="super_admin", status="active",
            tenant_id=None, org_id=None,
        )

    async def acquire_subject_lock(self, user_ref):
        return None

    async def rollback(self):
        return None


def _session_factory(repository):
    @asynccontextmanager
    async def factory():
        yield object()
    factory.repository = repository
    return factory


def test_拒绝幂等摘要绑定reviewer且提交结果未知使用新鲜Session确认(monkeypatch) -> None:
    from app.modules.auth.manual_identity_review_application import (
        PlatformIdentitySubmissionReviewService,
    )

    written = SimpleNamespace(status="submitted", version=3, evidence_digest=None, reviewed_by=None)
    confirmed = SimpleNamespace(status="rejected", version=3, evidence_digest=None, reviewed_by=17)
    second = _Repository(confirmed)
    first = _Repository(
        written,
        commit_error=RuntimeError("unknown"),
        on_commit=lambda: setattr(confirmed, "evidence_digest", written.evidence_digest),
    )
    repositories = iter((first, second))

    class _SqlRepository:
        def __new__(cls, session):
            return next(repositories)

    monkeypatch.setattr(
        "app.modules.auth.manual_identity_review_repository.SqlAlchemyPlatformIdentitySubmissionReviewRepository",
        _SqlRepository,
    )
    writer_factory = _session_factory(first)
    service = PlatformIdentitySubmissionReviewService(
        application_repository=_ApplicationRepository(),
        writer_session_factory=writer_factory,
        crypto=_Crypto(),
        clock=lambda: datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    request = SimpleNamespace(
        submission_version=3,
        idempotency_key="reject-v3",
        reason_code="OFFLINE_CHECK_FAILED",
    )

    model, replayed = asyncio.run(
        service.reject(
            current_user=SimpleNamespace(id=17, role="super_admin", tenant_id=None, org_id=None),
            user_ref=42,
            request=request,
        )
    )
    assert model is confirmed
    assert replayed is True
    mark = next(call[1] for call in first.calls if isinstance(call, tuple) and call[0] == "mark")
    assert ":17:" in mark["evidence_digest"]
