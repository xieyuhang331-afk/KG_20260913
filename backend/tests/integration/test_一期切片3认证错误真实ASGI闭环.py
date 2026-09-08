import pytest


pytestmark = pytest.mark.integration


def test_未认证机构邀请列表在真实ASGI固定401(real_db_client) -> None:
    response = real_db_client.get("/api/v1/institution/member-invitations")

    assert response.status_code == 401
    body = response.json()
    assert set(body) == {"code", "message", "request_id", "retryable", "field_errors"}
    assert body["code"] == "AUTHENTICATION_REQUIRED"
    assert body["message"] == "request rejected"
    assert body["request_id"] == response.headers["x-request-id"]
    assert body["retryable"] is False and body["field_errors"] == []
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["www-authenticate"] == "Bearer"
