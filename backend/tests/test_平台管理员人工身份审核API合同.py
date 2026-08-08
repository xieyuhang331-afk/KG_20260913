from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient


ROUTE = "/api/v1/reviews/users/1042/identity/approve"


def _headers(
    role: str = "super_admin",
    *,
    subject: str = "17",
    tenant_id: int | None = None,
    org_id: int | None = None,
) -> dict[str, str]:
    from app.core.security import create_access_token

    claims = {"sub": subject, "role": role}
    if tenant_id is not None:
        claims["tenant_id"] = tenant_id
    if org_id is not None:
        claims["org_id"] = org_id
    token = create_access_token(claims)
    return {"Authorization": f"Bearer {token}"}


def _payload(**changes) -> dict:
    values = {
        "idempotency_key": "manual-review-request-1042-v1",
        "decided_at": "2026-08-08T10:00:00Z",
        "evidence_digest": "a" * 64,
    }
    values.update(changes)
    return values


class _Service:
    def __init__(self, *, result=None, error=None) -> None:
        self.result = result or SimpleNamespace(
            user_ref=1042,
            status="verified",
            verification_decision_ref=(
                "0198a2ef-1234-7abc-8def-0123456789ab"
            ),
            registration_event_id=(
                "0198a2ef-5678-7abc-8def-0123456789ab"
            ),
            authority_decision_key="b" * 64,
            replayed=False,
        )
        self.error = error
        self.calls = []

    async def execute(self, *, current_user, user_ref, request):
        self.calls.append((current_user, user_ref, request))
        if self.error is not None:
            raise self.error
        return self.result


def _client(service: _Service) -> TestClient:
    from app.main import create_app
    from app.modules.review.api import (
        get_platform_admin_manual_identity_review_service,
    )

    app = create_app()
    app.dependency_overrides[
        get_platform_admin_manual_identity_review_service
    ] = lambda: service
    return TestClient(app)


def test_平台后台人工身份审核API尚未实现():
    service = _Service()
    response = _client(service).post(
        ROUTE,
        json=_payload(),
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "ok",
        "data": {
            "user_id": 1042,
            "status": "verified",
            "verification_decision_ref": (
                "0198a2ef-1234-7abc-8def-0123456789ab"
            ),
            "registration_event_id": (
                "0198a2ef-5678-7abc-8def-0123456789ab"
            ),
            "authority_decision_key": "b" * 64,
            "replayed": False,
        },
    }
    current_user, user_ref, request = service.calls[0]
    assert current_user.id == 17
    assert current_user.role == "super_admin"
    assert current_user.tenant_id is None
    assert current_user.org_id is None
    assert user_ref == 1042
    assert request.idempotency_key == "manual-review-request-1042-v1"
    assert request.evidence_digest == "a" * 64


def test_人工身份审核路由遵循既有平台Review路径族():
    response = _client(_Service()).get("/openapi.json")
    path = "/api/v1/reviews/users/{user_id}/identity/approve"
    assert response.status_code == 200
    assert path in response.json()["paths"]
    assert "post" in response.json()["paths"][path]


def test_人工身份审核要求有效JWT():
    service = _Service()
    missing = _client(service).post(ROUTE, json=_payload())
    forged = _client(service).post(
        ROUTE,
        json=_payload(),
        headers={"Authorization": "Bearer forged"},
    )
    assert missing.status_code == 401
    assert forged.status_code == 401
    assert service.calls == []


def test_人工身份审核只允许无TenantOrgScope的super_admin():
    service = _Service()
    for headers in (
        _headers("province_admin"),
        _headers("city_admin"),
        _headers("org_admin"),
        _headers("member"),
        _headers("super_admin", tenant_id=3),
        _headers("super_admin", org_id=9),
    ):
        response = _client(service).post(
            ROUTE, json=_payload(), headers=headers
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Forbidden"
    assert service.calls == []


def test_人工身份审核请求拒绝PII和非规范字段():
    service = _Service()
    for payload in (
        _payload(evidence_digest="not-a-digest"),
        _payload(idempotency_key=" leading"),
        _payload(decided_at="2026-08-08T10:00:00"),
        {**_payload(), "id_card": "forbidden-extra-field"},
    ):
        response = _client(service).post(
            ROUTE, json=payload, headers=_headers()
        )
        assert response.status_code == 422
    assert service.calls == []


def test_人工身份审核固定安全错误映射且不泄漏异常链():
    from app.modules.auth.manual_identity_review_application import (
        PlatformAdminManualIdentityReviewConflict,
        PlatformAdminManualIdentityReviewNotFound,
        PlatformAdminManualIdentityReviewUnavailable,
    )

    cases = (
        (
            PlatformAdminManualIdentityReviewNotFound("secret"),
            404,
            "Identity review subject not found",
        ),
        (
            PlatformAdminManualIdentityReviewConflict("secret"),
            409,
            "Identity review decision conflict",
        ),
        (
            PlatformAdminManualIdentityReviewUnavailable("secret"),
            503,
            "Identity review service unavailable",
        ),
    )
    for error, status_code, detail in cases:
        response = _client(_Service(error=error)).post(
            ROUTE, json=_payload(), headers=_headers()
        )
        assert response.status_code == status_code
        assert response.json() == {"detail": detail}
        assert "secret" not in response.text


def test_人工身份审核响应不包含PII_JWT或数据库信息():
    response = _client(_Service()).post(
        ROUTE, json=_payload(), headers=_headers()
    )
    public = response.text.lower()
    for forbidden in (
        "id_card",
        "real_name",
        "phone",
        "password",
        "authorization",
        "database_url",
        "credential",
    ):
        assert forbidden not in public


def test_WriterRuntime配置缺失固定映射503且不泄漏连接信息(monkeypatch):
    from app.core import database
    from app.main import create_app

    monkeypatch.setattr(database, "get_session_factory", lambda: object())
    monkeypatch.setattr(
        database,
        "get_verification_writer_session_factory",
        lambda: (_ for _ in ()).throw(RuntimeError("secret-database-target")),
    )
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        response = client.post(ROUTE, json=_payload(), headers=_headers())
    assert response.status_code == 503
    assert response.json() == {"detail": "Identity review service unavailable"}
    assert "secret-database-target" not in response.text
