from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def fixed_authority(monkeypatch):
    from app.core.config import get_settings

    rows = {
        user_id: SimpleNamespace(
            id=user_id, role=role, tenant_id=None, tenant_org_id=None,
            status="active", exited_at=None, deletion_requested_at=None,
        )
        for user_id, role in ((17, "super_admin"), (18, "province_admin"))
    }
    monkeypatch.setattr("app.core.认证当前性._read_authority", AsyncMock(side_effect=rows.get))
    monkeypatch.setenv("KG_AUTH_CONTEXT_MAP", '{"18":{"province":"ZJ"}}')
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _headers(role="super_admin"):
    from app.core.security import create_access_token
    claims = {"sub": "17", "role": role}
    if role == "province_admin":
        claims.update(sub="18", province="ZJ")
    token = create_access_token(claims)
    return {"Authorization": f"Bearer {token}"}


class _Service:
    def __init__(self): self.calls = []
    async def list_queue(self, **kwargs):
        self.calls.append(("queue", kwargs))
        return [SimpleNamespace(user_ref=42, version=2, id_card_masked="110105********002X", submitted_at=datetime(2026,8,9,tzinfo=timezone.utc))], 1
    async def detail(self, **kwargs):
        self.calls.append(("detail", kwargs))
        model = SimpleNamespace(user_ref=42, version=2, status="submitted", id_card_masked="110105********002X", consent_version="v1", submitted_at=datetime(2026,8,9,tzinfo=timezone.utc))
        return model, "张三", "11010519491231002X"


def _client(service):
    from app.main import create_app
    from app.modules.review.api import (
        get_platform_identity_detail_service,
        get_platform_identity_submission_review_service,
    )
    app = create_app()
    app.dependency_overrides[get_platform_identity_submission_review_service] = lambda: service
    app.dependency_overrides[get_platform_identity_detail_service] = lambda: service
    return TestClient(app)


def test_super_admin审核队列只返回脱敏摘要() -> None:
    response = _client(_Service()).get("/api/v1/reviews/identity?status=submitted", headers=_headers())
    assert response.status_code == 200
    public = response.text
    assert "110105********002X" in public
    assert "11010519491231002X" not in public
    assert "real_name" not in public


def test_审核详情要求用途并仅允许super_admin() -> None:
    service = _Service()
    missing = _client(service).get("/api/v1/reviews/users/42/identity", headers=_headers())
    forbidden = _client(service).get("/api/v1/reviews/users/42/identity?purpose_code=MANUAL_REVIEW", headers=_headers("province_admin"))
    allowed = _client(service).get(
        "/api/v1/reviews/users/42/identity?purpose_code=MANUAL_REVIEW",
        headers={**_headers(), "X-Identity-Review-Step-Up": "test-step-up"},
    )
    assert missing.status_code == 422
    assert forbidden.status_code == 403
    assert allowed.status_code == 200
