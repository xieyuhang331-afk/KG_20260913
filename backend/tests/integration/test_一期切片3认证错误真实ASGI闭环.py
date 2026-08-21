import pytest


pytestmark = pytest.mark.integration


def test_未认证机构邀请列表在真实ASGI固定401(real_db_client) -> None:
    response = real_db_client.get("/api/v1/institution/member-invitations")

    assert response.status_code == 401
    assert response.json() == {
        "code": "AUTHENTICATION_REQUIRED",
        "message": "request rejected",
    }
