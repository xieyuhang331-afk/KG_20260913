from __future__ import annotations

from types import SimpleNamespace

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
            "decided_at": "2026-08-09T09:00:00Z",
        },
    )
    assert response.status_code == 200
    assert response.json()["data"] == {"user_id": 42, "submission_version": 3, "status": "rejected", "replayed": False}
    assert "id_card" not in response.text and "real_name" not in response.text
