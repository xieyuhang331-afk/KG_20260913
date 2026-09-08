from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def fixed_authority(monkeypatch):
    row = SimpleNamespace(
        id=17, role="super_admin", status="active", tenant_id=None,
        tenant_org_id=None, exited_at=None, deletion_requested_at=None,
    )
    monkeypatch.setattr(
        "app.core.认证当前性._read_authority", AsyncMock(side_effect={17: row}.get),
    )


def _headers() -> dict[str, str]:
    from app.core.security import create_access_token

    return {
        "Authorization": "Bearer "
        + create_access_token({"sub": "17", "role": "super_admin"})
    }


def _openapi() -> dict:
    from app.main import create_app

    return TestClient(create_app()).get("/openapi.json").json()


def test_四接口与StepUp成功响应均为精确TypedEnvelope() -> None:
    document = _openapi()
    expected = {
        "/api/v1/reviews/identity": "IdentityReviewQueueEnvelope",
        "/api/v1/reviews/users/{user_id}/identity": "IdentityReviewDetailEnvelope",
        "/api/v1/reviews/users/{user_id}/identity/approve": "PlatformAdminManualIdentityReviewEnvelope",
        "/api/v1/reviews/users/{user_id}/identity/reject": "IdentityReviewRejectEnvelope",
        "/api/v1/reviews/users/{user_id}/identity/step-up": "PlatformIdentityReviewStepUpEnvelope",
    }
    for path, schema_name in expected.items():
        method = "get" if path.endswith("identity") or path.endswith("/identity") and "users" not in path else "post"
        if path == "/api/v1/reviews/users/{user_id}/identity":
            method = "get"
        response_schema = document["paths"][path][method]["responses"]["200"]["content"]["application/json"]["schema"]
        assert response_schema == {"$ref": f"#/components/schemas/{schema_name}"}


def test_四接口OpenAPI声明实际错误状态子集() -> None:
    document = _openapi()["paths"]
    cases = {
        ("/api/v1/reviews/identity", "get"): {"200", "401", "403", "422", "500", "503"},
        ("/api/v1/reviews/users/{user_id}/identity", "get"): {"200", "401", "403", "404", "422", "500", "503"},
        ("/api/v1/reviews/users/{user_id}/identity/approve", "post"): {"200", "401", "403", "404", "409", "422", "500", "503"},
        ("/api/v1/reviews/users/{user_id}/identity/reject", "post"): {"200", "401", "403", "404", "409", "422", "500", "503"},
    }
    for (path, method), expected in cases.items():
        assert set(document[path][method]["responses"]) == expected


def test_approve_reject关闭客户端时间digest和legacy输入() -> None:
    schemas = _openapi()["components"]["schemas"]
    approve = schemas["PlatformAdminManualIdentityReviewRequest"]
    reject = schemas["PlatformIdentityReviewRejectRequest"]
    assert set(approve["properties"]) == {
        "idempotency_key", "submission_version", "decision_basis_code"
    }
    assert set(approve["required"]) == set(approve["properties"])
    assert approve["properties"]["decision_basis_code"]["const"] == "APPROVED_OFFLINE_IDENTITY_CHECK"
    assert set(reject["properties"]) == {
        "submission_version", "idempotency_key", "reason_code"
    }
    assert set(reject["required"]) == set(reject["properties"])
    assert reject["properties"]["reason_code"]["const"] == "OFFLINE_CHECK_FAILED"


def test_detail只接受固定用途且StepUpHeader必填() -> None:
    from app.main import create_app
    from app.modules.review.api import get_platform_identity_detail_service

    class _DetailService:
        async def detail(self, **kwargs):
            model = SimpleNamespace(
                user_ref=42, version=3, status="submitted",
                id_card_masked="110105********002X", consent_version="v1",
                submitted_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
            )
            return model, "name", "11010519491231002X"

    app = create_app()
    app.dependency_overrides[get_platform_identity_detail_service] = lambda: _DetailService()
    client = TestClient(app)
    path = "/api/v1/reviews/users/42/identity"
    missing = client.get(path + "?purpose_code=MANUAL_REVIEW", headers=_headers())
    wrong = client.get(
        path + "?purpose_code=EXPORT",
        headers={**_headers(), "X-Identity-Review-Step-Up": "step-up"},
    )
    allowed = client.get(
        path + "?purpose_code=MANUAL_REVIEW",
        headers={**_headers(), "X-Identity-Review-Step-Up": "step-up"},
    )
    assert missing.status_code == 422
    assert wrong.status_code == 422
    assert allowed.status_code == 200


def test_approve使用服务端权威时间且公开响应不暴露内部引用() -> None:
    from app.main import create_app
    from app.modules.review.api import (
        get_platform_admin_manual_identity_review_service,
        get_platform_identity_submission_review_service,
    )

    class _ReviewService:
        def __init__(self):
            self.request = None

        async def execute(self, **kwargs):
            self.request = kwargs["request"]
            return SimpleNamespace(
                user_ref=42, status="verified",
                verification_decision_ref="0198a2ef-1234-7abc-8def-0123456789ab",
                registration_event_id="0198a2ef-5678-7abc-8def-0123456789ab",
                authority_decision_key="a" * 64,
                replayed=False,
            )

    class _SubmissionService:
        @asynccontextmanager
        async def subject_coordination(self, user_ref):
            yield

        async def approval_evidence_digest(self, **kwargs):
            return "b" * 64

        async def mark_verified(self, **kwargs):
            return None

    review_service = _ReviewService()
    app = create_app()
    app.dependency_overrides[get_platform_admin_manual_identity_review_service] = lambda: review_service
    app.dependency_overrides[get_platform_identity_submission_review_service] = lambda: _SubmissionService()
    before = datetime.now(timezone.utc)
    response = TestClient(app).post(
        "/api/v1/reviews/users/42/identity/approve",
        headers=_headers(),
        json={
            "idempotency_key": "approve-request-v3",
            "submission_version": 3,
            "decision_basis_code": "APPROVED_OFFLINE_IDENTITY_CHECK",
        },
    )
    after = datetime.now(timezone.utc)
    assert response.status_code == 200
    assert before <= review_service.request.decided_at <= after
    assert set(response.json()["data"]) == {
        "user_id", "submission_version", "status", "decision_ref", "replayed"
    }
    assert "registration_event_id" not in response.text
    assert "authority_decision_key" not in response.text
