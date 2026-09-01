from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration
VALID_ID_UPPER = "11010519491231002X"


def _headers(user_id: int) -> dict[str, str]:
    from app.core.security import create_access_token

    token = create_access_token({"sub": str(user_id), "role": "member"})
    return {"Authorization": f"Bearer {token}"}


def _register(real_db_client, phone: str) -> int:
    response = real_db_client.post(
        "/api/v1/users/register",
        json={"phone": phone, "password": "SyntheticPassword123"},
    )
    assert response.status_code == 200
    return response.json()["data"]["id"]


def test_A2_R05_R10_R11_formal提交规范化并保持legacy明文为空(
    real_db_client, application_database
) -> None:
    user_id = _register(real_db_client, "13800139681")
    headers = _headers(user_id)
    lower = real_db_client.put(
        "/api/v1/users/me/identity-verification",
        headers=headers,
        json={
            "real_name": "合成会员",
            "id_card": " 11010519491231002x ",
            "idempotency_key": "a2-canonical-replay-v1",
            "consent_version": "identity-consent-v1",
        },
    )
    upper = real_db_client.put(
        "/api/v1/users/me/identity-verification",
        headers=headers,
        json={
            "real_name": "合成会员",
            "id_card": VALID_ID_UPPER,
            "idempotency_key": "a2-canonical-replay-v1",
            "consent_version": "identity-consent-v1",
        },
    )

    assert lower.status_code == upper.status_code == 200
    assert lower.headers["cache-control"] == "no-store"
    assert upper.headers["cache-control"] == "no-store"
    assert lower.json()["data"]["id_card_masked"] == "110105********002X"
    assert lower.json()["data"]["outcome"] == "CREATED"
    assert upper.json()["data"]["outcome"] == "REPLAYED"
    projection = application_database.fetch_rows(
        'SELECT real_name,id_card,verify_status,tenant_id FROM public."user" '
        f"WHERE id={user_id}"
    )[0]
    assert projection == {
        "real_name": None,
        "id_card": None,
        "verify_status": "submitted",
        "tenant_id": None,
    }
    assert application_database.fetch_value(
        "SELECT count(*) FROM public.identity_verification_submission "
        f"WHERE user_ref={user_id}"
    ) == 1


@pytest.mark.parametrize(
    ("path", "payload", "code"),
    [
        (
            "/api/v1/users/{user_id}/identity",
            {"real_name": "synthetic", "id_card": "110101199001011234"},
            "LEGACY_IDENTITY_ENDPOINT_RETIRED",
        ),
        (
            "/api/v1/users/{user_id}/tenant-binding",
            {"tenant_id": 1},
            "LEGACY_MEMBER_TENANT_BINDING_RETIRED",
        ),
    ],
)
def test_A2_R03_R04_R18_R19_R22_legacy入口401_410且数据库零副作用(
    real_db_client,
    application_database,
    path: str,
    payload: dict[str, object],
    code: str,
) -> None:
    user_id = _register(
        real_db_client,
        "13800139682" if path.endswith("identity") else "13800139683",
    )
    resolved_path = path.format(user_id=user_id)

    unauthenticated = real_db_client.post(resolved_path, json=payload)
    retired = real_db_client.post(
        resolved_path, json=payload, headers=_headers(user_id)
    )

    assert unauthenticated.status_code == 401
    assert retired.status_code == 410
    assert retired.headers["cache-control"] == "no-store"
    assert retired.json() == {"code": code, "message": "request rejected"}
    assert str(user_id) not in retired.text
    projection = application_database.fetch_rows(
        'SELECT real_name,id_card,verify_status,tenant_id FROM public."user" '
        f"WHERE id={user_id}"
    )[0]
    assert projection == {
        "real_name": None,
        "id_card": None,
        "verify_status": None,
        "tenant_id": None,
    }
