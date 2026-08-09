from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace


class _Repository:
    def __init__(self, model): self.model = model
    async def get_submission(self, **kwargs): return self.model


def test_approve证据由当前Submission版本与内容摘要服务端派生() -> None:
    from app.modules.auth.identity_submission_crypto import IdentitySubmissionCrypto
    from app.modules.auth.manual_identity_review_application import PlatformIdentitySubmissionReviewService
    model = SimpleNamespace(
        submission_id="0198a2ef-1234-7abc-8def-0123456789ab",
        user_ref=42, version=2, status="submitted", content_digest="a" * 64,
    )
    service = PlatformIdentitySubmissionReviewService(
        application_repository=_Repository(model), writer_session_factory=object(),
        crypto=IdentitySubmissionCrypto(encryption_key=b"E"*32, hmac_key=b"H"*32, key_id="v1"),
    )
    request = SimpleNamespace(
        submission_version=2, decision_basis_code="APPROVED_OFFLINE_IDENTITY_CHECK",
        evidence_digest=None,
    )
    reviewer = SimpleNamespace(id=17, role="super_admin", tenant_id=None, org_id=None)
    actual = asyncio.run(service.approval_evidence_digest(current_user=reviewer, user_ref=42, request=request))
    expected = hashlib.sha256(
        f"identity-submission-review:v1:{model.submission_id}:2:{'a'*64}:APPROVED_OFFLINE_IDENTITY_CHECK".encode()
    ).hexdigest()
    assert actual == expected


def test_approve拒绝陈旧已拒绝或已替换Submission() -> None:
    import pytest
    from app.modules.auth.identity_submission_crypto import IdentitySubmissionCrypto
    from app.modules.auth.manual_identity_review_application import (
        PlatformAdminManualIdentityReviewConflict, PlatformIdentitySubmissionReviewService,
    )
    reviewer = SimpleNamespace(id=17, role="super_admin", tenant_id=None, org_id=None)
    request = SimpleNamespace(submission_version=1, decision_basis_code="APPROVED_OFFLINE_IDENTITY_CHECK", evidence_digest=None)
    for status in ("rejected", "verified"):
        model = SimpleNamespace(user_ref=42, version=1, status=status)
        service = PlatformIdentitySubmissionReviewService(
            application_repository=_Repository(model), writer_session_factory=object(),
            crypto=IdentitySubmissionCrypto(encryption_key=b"E"*32, hmac_key=b"H"*32, key_id="v1"),
        )
        with pytest.raises(PlatformAdminManualIdentityReviewConflict):
            asyncio.run(service.approval_evidence_digest(current_user=reviewer, user_ref=42, request=request))
