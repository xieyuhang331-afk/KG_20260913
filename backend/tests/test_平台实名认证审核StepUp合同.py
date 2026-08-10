from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


class _Repository:
    def __init__(self) -> None:
        self.reviewer = SimpleNamespace(
            id=17,
            password_hash="stored-hash",
            role="super_admin",
            status="active",
            tenant_id=None,
            org_id=None,
            updated_at=datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc),
        )
        self.submission = SimpleNamespace(status="submitted", version=3)
        self.failed_count = 0
        self.consumed = None
        self.calls: list[object] = []

    async def acquire_reviewer_lock(self, reviewer_id):
        self.calls.append(("reviewer_lock", reviewer_id))

    async def count_failed_reauth_attempts(self, **kwargs):
        self.calls.append(("failed_count", kwargs))
        return self.failed_count

    async def get_reviewer_auth_state(self, reviewer_id, *, lock):
        self.calls.append(("reviewer", reviewer_id, lock))
        return self.reviewer

    async def get_submission(self, **kwargs):
        self.calls.append(("submission", kwargs))
        return self.submission

    async def add_step_up_audit(self, **kwargs):
        self.calls.append(("audit", kwargs))

    async def commit(self):
        self.calls.append("commit")

    async def rollback(self):
        self.calls.append("rollback")

    async def acquire_nonce_lock(self, nonce_digest):
        self.calls.append(("nonce_lock", nonce_digest))

    async def find_step_up_consumption(
        self, nonce_digest, reviewer_id, subject_user_id, attempt_digest=None
    ):
        self.calls.append(
            ("consumption", nonce_digest, reviewer_id, subject_user_id, attempt_digest)
        )
        return self.consumed

    async def acquire_subject_lock(self, subject_user_id):
        self.calls.append(("subject_lock", subject_user_id))


def _access_token(monkeypatch) -> str:
    monkeypatch.setenv("KG_DATABASE_PASSWORD", "process-only-database-secret")
    monkeypatch.setenv("KG_JWT_SECRET_KEY", "a" * 64)
    monkeypatch.setenv("KG_IDENTITY_REVIEW_STEP_UP_SECRET_KEY", "b" * 64)
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.core.security import create_access_token

    return create_access_token({"sub": "17", "role": "super_admin"})


def _service(repository, *, clock=None, password_verifier=None):
    from app.modules.auth.identity_review_step_up import (
        PlatformIdentityReviewStepUpService,
    )

    kwargs = {
        "repository": repository,
        "secret": "b" * 64,
        "nonce_factory": lambda _: "n" * 43,
        "password_verifier": password_verifier or (lambda password, stored: True),
    }
    if clock is not None:
        kwargs["clock"] = clock
    return PlatformIdentityReviewStepUpService(**kwargs)


def _current_user():
    return SimpleNamespace(id=17, role="super_admin", tenant_id=None, org_id=None)


def test_平台实名认证审核StepUp尚未实现(monkeypatch) -> None:
    repository = _Repository()
    service = _service(repository)
    result = asyncio.run(
        service.issue(
            current_user=_current_user(),
            subject_user_id=42,
            password="current-password",
            access_token=_access_token(monkeypatch),
        )
    )
    assert result.expires_in == 120
    assert result.token.count(".") == 2


def test_StepUpToken与普通AccessToken严格隔离(monkeypatch) -> None:
    from fastapi import HTTPException
    from app.core.security import decode_access_token_for_step_up
    from app.modules.auth.identity_review_step_up import (
        PlatformIdentityReviewStepUpUnauthorized,
    )

    repository = _Repository()
    service = _service(repository)
    access_token = _access_token(monkeypatch)
    step_up_token = asyncio.run(
        service.issue(
            current_user=_current_user(), subject_user_id=42,
            password="current-password", access_token=access_token,
        )
    ).token

    with pytest.raises(HTTPException):
        decode_access_token_for_step_up(step_up_token)
    with pytest.raises(PlatformIdentityReviewStepUpUnauthorized):
        asyncio.run(
            service.begin_consumption(
                current_user=_current_user(), subject_user_id=42,
                purpose="MANUAL_REVIEW", access_token=access_token,
                step_up_token=access_token,
            )
        )


def test_StepUp绑定reviewer_subject_purpose和AccessToken(monkeypatch) -> None:
    from app.modules.auth.identity_review_step_up import (
        PlatformIdentityReviewStepUpUnauthorized,
    )

    repository = _Repository()
    service = _service(repository)
    access_token = _access_token(monkeypatch)
    token = asyncio.run(
        service.issue(
            current_user=_current_user(), subject_user_id=42,
            password="current-password", access_token=access_token,
        )
    ).token
    with pytest.raises(PlatformIdentityReviewStepUpUnauthorized):
        asyncio.run(
            service.begin_consumption(
                current_user=_current_user(), subject_user_id=43,
                purpose="MANUAL_REVIEW", access_token=access_token,
                step_up_token=token,
            )
        )


def test_reviewer_currentness漂移及恢复后旧StepUp仍失效(monkeypatch) -> None:
    from app.modules.auth.identity_review_step_up import (
        PlatformIdentityReviewStepUpForbidden,
    )

    repository = _Repository()
    service = _service(repository)
    access_token = _access_token(monkeypatch)
    token = asyncio.run(
        service.issue(
            current_user=_current_user(), subject_user_id=42,
            password="current-password", access_token=access_token,
        )
    ).token
    repository.reviewer.updated_at += timedelta(seconds=1)
    with pytest.raises(PlatformIdentityReviewStepUpForbidden):
        asyncio.run(
            service.begin_consumption(
                current_user=_current_user(), subject_user_id=42,
                purpose="MANUAL_REVIEW", access_token=access_token,
                step_up_token=token,
            )
        )


def test_相同StepUp并发消费只有一个赢家(monkeypatch) -> None:
    from app.modules.auth.identity_review_step_up import (
        PlatformIdentityReviewStepUpUnauthorized,
    )

    repository = _Repository()
    service = _service(repository)
    access_token = _access_token(monkeypatch)
    token = asyncio.run(
        service.issue(
            current_user=_current_user(), subject_user_id=42,
            password="current-password", access_token=access_token,
        )
    ).token
    first = asyncio.run(
        service.begin_consumption(
            current_user=_current_user(), subject_user_id=42,
            purpose="MANUAL_REVIEW", access_token=access_token,
            step_up_token=token,
        )
    )
    repository.consumed = (1, {"attempt_digest": first.attempt_digest})
    with pytest.raises(PlatformIdentityReviewStepUpUnauthorized):
        asyncio.run(
            service.begin_consumption(
                current_user=_current_user(), subject_user_id=42,
                purpose="MANUAL_REVIEW", access_token=access_token,
                step_up_token=token,
            )
        )


def test_并发密码失败只能消耗剩余频控预算(monkeypatch) -> None:
    from app.modules.auth.identity_review_step_up import (
        PlatformIdentityReviewStepUpRateLimited,
    )

    repository = _Repository()
    repository.failed_count = 5
    verifier_calls: list[str] = []
    service = _service(
        repository,
        password_verifier=lambda password, stored: verifier_calls.append(password) or False,
    )
    with pytest.raises(PlatformIdentityReviewStepUpRateLimited):
        asyncio.run(
            service.issue(
                current_user=_current_user(), subject_user_id=42,
                password="must-not-be-checked", access_token=_access_token(monkeypatch),
            )
        )
    assert verifier_calls == []
    assert repository.calls[0] == ("reviewer_lock", 17)


def test_Cancellation原样传播并回滚(monkeypatch) -> None:
    repository = _Repository()

    async def cancelled(**kwargs):
        raise asyncio.CancelledError

    repository.count_failed_reauth_attempts = cancelled
    service = _service(repository)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            service.issue(
                current_user=_current_user(), subject_user_id=42,
                password="current-password", access_token=_access_token(monkeypatch),
            )
        )
    assert "rollback" in repository.calls


class _DetailRepository:
    def __init__(self, *, status="submitted", commit_error=None) -> None:
        self.reviewer = SimpleNamespace(
            id=17, role="super_admin", status="active",
            tenant_id=None, org_id=None,
        )
        self.model = SimpleNamespace(
            submission_id="0198a2ef-1234-7abc-8def-0123456789ab",
            user_ref=42,
            version=3,
            status=status,
            real_name_ciphertext=b"name",
            real_name_nonce=b"nonce-name",
            id_card_ciphertext=b"card",
            id_card_nonce=b"nonce-card",
            encryption_key_id="v1",
            id_card_masked="110105********002X",
            consent_version="v1",
            submitted_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
        self.commit_error = commit_error
        self.calls = []

    async def get_reviewer_state(self, reviewer_id):
        self.calls.append(("reviewer", reviewer_id))
        return self.reviewer

    async def get_submission(self, **kwargs):
        self.calls.append(("submission", kwargs))
        return self.model

    async def add_sensitive_read_audit(self, **kwargs):
        self.calls.append(("audit", kwargs))

    async def commit(self):
        self.calls.append("commit")
        if self.commit_error:
            raise self.commit_error

    async def rollback(self):
        self.calls.append("rollback")

    async def acquire_subject_lock(self, user_ref):
        self.calls.append(("subject_lock", user_ref))


class _StepUp:
    async def begin_consumption(self, **kwargs):
        return SimpleNamespace(
            nonce_digest="1" * 64,
            attempt_digest="2" * 64,
            reviewer_projection_digest="3" * 64,
            reviewer_id=17,
            subject_user_id=42,
        )


class _Crypto:
    def __init__(self) -> None:
        self.calls = []

    def decrypt(self, encrypted, *, aad):
        self.calls.append((encrypted, aad))
        return "decrypted-name" if len(self.calls) == 1 else "11010519491231002X"

    def aad(self, **kwargs):
        return repr(sorted(kwargs.items())).encode()


def _detail_service(repository, crypto, **kwargs):
    from app.modules.auth.manual_identity_review_application import (
        PlatformIdentitySubmissionReviewService,
    )

    return PlatformIdentitySubmissionReviewService(
        application_repository=repository,
        writer_session_factory=object(),
        crypto=crypto,
        step_up_service=_StepUp(),
        **kwargs,
    )


def test_terminal_submission不得解密完整PII() -> None:
    from app.modules.auth.manual_identity_review_application import (
        PlatformAdminManualIdentityReviewNotFound,
    )

    repository = _DetailRepository(status="verified")
    crypto = _Crypto()
    with pytest.raises(PlatformAdminManualIdentityReviewNotFound):
        asyncio.run(
            _detail_service(repository, crypto).detail(
                current_user=_current_user(), user_ref=42,
                purpose_code="MANUAL_REVIEW", access_token="access",
                step_up_token="step-up",
            )
        )
    assert crypto.calls == []
    assert not any(call[0] == "audit" for call in repository.calls if isinstance(call, tuple))


def test_StepUp消费审计与PII返回同事务() -> None:
    repository = _DetailRepository()
    crypto = _Crypto()
    model, name, card = asyncio.run(
        _detail_service(repository, crypto).detail(
            current_user=_current_user(), user_ref=42,
            purpose_code="MANUAL_REVIEW", access_token="access",
            step_up_token="step-up",
        )
    )
    assert model is repository.model
    assert (name, card) == ("decrypted-name", "11010519491231002X")
    audit = next(call[1] for call in repository.calls if isinstance(call, tuple) and call[0] == "audit")
    assert audit["nonce_digest"] == "1" * 64
    assert audit["attempt_digest"] == "2" * 64
    assert repository.calls[-1] == "commit"


def test_commit_outcome_unknown使用新鲜Session确认且绝不返回PII(monkeypatch) -> None:
    repository = _DetailRepository(commit_error=RuntimeError("vendor-secret"))
    crypto = _Crypto()
    confirmation_calls = []

    class _ConfirmationRepository:
        def __init__(self, session):
            confirmation_calls.append(("repo", session))

        async def acquire_nonce_lock(self, digest):
            confirmation_calls.append(("lock", digest))

        async def find_step_up_consumption(
            self, digest, reviewer_id, subject_user_id, attempt_digest=None
        ):
            confirmation_calls.append(
                ("find", digest, reviewer_id, subject_user_id, attempt_digest)
            )
            return (1, {"attempt_digest": "2" * 64})

    class _SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(
        "app.modules.auth.manual_identity_review_repository.SqlAlchemyPlatformIdentitySubmissionReviewRepository",
        _ConfirmationRepository,
    )
    service = _detail_service(
        repository,
        crypto,
        application_session_factory=lambda: _SessionContext(),
    )
    from app.modules.auth.manual_identity_review_application import (
        PlatformAdminManualIdentityReviewUnavailable,
    )

    with pytest.raises(PlatformAdminManualIdentityReviewUnavailable):
        asyncio.run(
            service.detail(
                current_user=_current_user(), user_ref=42,
                purpose_code="MANUAL_REVIEW", access_token="access",
                step_up_token="step-up",
            )
        )
    assert "rollback" in repository.calls
    assert confirmation_calls[-2:] == [
        ("lock", "1" * 64),
        ("find", "1" * 64, 17, 42, "2" * 64),
    ]


def test_StepUp消费查询同时绑定reviewer和subject() -> None:
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "app/modules/auth/manual_identity_review_repository.py"
    ).read_text(encoding="utf-8")
    compact = "".join(source.split())
    assert "OperationLog.operator_id==reviewer_id" in compact
    assert "OperationLog.object_id==subject_user_id" in compact
