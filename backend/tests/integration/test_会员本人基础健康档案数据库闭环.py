from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import os

import asyncpg
import pytest


pytestmark = pytest.mark.integration


def _register(real_db_client, phone: str) -> dict:
    response = real_db_client.post(
        "/api/v1/users/register",
        json={"phone": phone, "password": "Secret12345"},
    )
    assert response.status_code == 200
    return response.json()["data"]


async def _mark_verified_async(user_id: int) -> None:
    database_url = os.environ["KG_TEST_DATABASE_URL"].replace(
        "postgresql+asyncpg://",
        "postgresql://",
        1,
    )
    connection = await asyncpg.connect(database_url)
    try:
        await connection.execute(
            'UPDATE public."user" '
            "SET verify_status = 'verified', status = 'active' "
            "WHERE id = $1",
            user_id,
        )
    finally:
        await connection.close()


def _mark_verified(user_id: int) -> None:
    asyncio.run(_mark_verified_async(user_id))


def _headers(user_id: int, *, role: str = "member") -> dict[str, str]:
    from app.core.security import create_access_token

    token = create_access_token({"sub": str(user_id), "role": role})
    return {"Authorization": f"Bearer {token}"}


def _payload(*, expected_version=None, weight: str = "55.0") -> dict:
    return {
        "gender": "female",
        "birth_date": "1990-01-01",
        "height": "165.5",
        "weight": weight,
        "blood_type": "A",
        "expected_version": expected_version,
    }


def test_本人基础健康档案创建读取更新与稳定重放(real_db_client, pg_database) -> None:
    user = _register(real_db_client, "13800139401")
    _mark_verified(user["id"])
    headers = _headers(user["id"])

    missing = real_db_client.get(
        "/api/v1/users/me/health-profile",
        headers=headers,
    )
    assert missing.status_code == 200
    assert missing.json()["data"] == {
        "state": "NOT_CREATED",
        "version": None,
        "profile": None,
        "bmi": None,
        "outcome": None,
    }

    created = real_db_client.put(
        "/api/v1/users/me/health-profile",
        json=_payload(),
        headers=headers,
    )
    assert created.status_code == 200
    assert created.json()["data"]["outcome"] == "CREATED"
    version = created.json()["data"]["version"]

    fetched = real_db_client.get(
        "/api/v1/users/me/health-profile",
        headers=headers,
    )
    assert fetched.status_code == 200
    assert fetched.json()["data"]["state"] == "COMPLETE"
    assert fetched.json()["data"]["profile"] == {
        "gender": "female",
        "birth_date": "1990-01-01",
        "height": "165.5",
        "weight": "55.0",
        "blood_type": "A",
    }
    assert fetched.json()["data"]["bmi"] == "20.1"

    replayed = real_db_client.put(
        "/api/v1/users/me/health-profile",
        json=_payload(),
        headers=headers,
    )
    assert replayed.status_code == 200
    assert replayed.json()["data"]["outcome"] == "REPLAYED"

    updated = real_db_client.put(
        "/api/v1/users/me/health-profile",
        json=_payload(expected_version=version, weight="56.0"),
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["outcome"] == "UPDATED"
    assert updated.json()["data"]["profile"]["weight"] == "56.0"

    stale = real_db_client.put(
        "/api/v1/users/me/health-profile",
        json=_payload(expected_version=version, weight="57.0"),
        headers=headers,
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "HEALTH_PROFILE_VERSION_CONFLICT"
    assert pg_database.fetch_value(
        "SELECT COUNT(*) FROM health_profile WHERE user_id = " + str(user["id"])
    ) == 1


def test_并发首次创建只有一个canonical档案(real_db_client, pg_database) -> None:
    user = _register(real_db_client, "13800139402")
    _mark_verified(user["id"])
    headers = _headers(user["id"])

    def create_once():
        return real_db_client.put(
            "/api/v1/users/me/health-profile",
            json=_payload(),
            headers=headers,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: create_once(), range(2)))

    assert [response.status_code for response in responses] == [200, 200]
    outcomes = sorted(response.json()["data"]["outcome"] for response in responses)
    assert outcomes == ["CREATED", "REPLAYED"]
    assert pg_database.fetch_value(
        "SELECT COUNT(*) FROM health_profile WHERE user_id = " + str(user["id"])
    ) == 1


def test_权限与本人边界在真实数据库保持fail_closed(real_db_client, pg_database) -> None:
    user_a = _register(real_db_client, "13800139403")
    user_b = _register(real_db_client, "13800139404")
    _mark_verified(user_a["id"])
    _mark_verified(user_b["id"])

    pg_database.execute(
        'INSERT INTO public."user" (id,phone,password_hash,role,status) VALUES '
        "(83941,'13900083941','synthetic','org_admin','active')"
    )

    forbidden = real_db_client.put(
        "/api/v1/users/me/health-profile",
        json=_payload(),
        headers=_headers(83941, role="org_admin"),
    )
    assert forbidden.status_code == 403

    rejected = real_db_client.put(
        "/api/v1/users/me/health-profile",
        json={**_payload(), "user_id": user_b["id"]},
        headers=_headers(user_a["id"]),
    )
    assert rejected.status_code == 422
    assert pg_database.fetch_value(
        "SELECT COUNT(*) FROM health_profile WHERE user_id = " + str(user_a["id"])
    ) == 0
    assert pg_database.fetch_value(
        "SELECT COUNT(*) FROM health_profile WHERE user_id = " + str(user_b["id"])
    ) == 0

    created = real_db_client.put(
        "/api/v1/users/me/health-profile",
        json=_payload(),
        headers=_headers(user_a["id"]),
    )
    assert created.status_code == 200

    serialized = created.text.lower()
    for forbidden_field in (
        "phone",
        "id_card",
        "tenant_id",
        "org_id",
        "verification",
    ):
        assert forbidden_field not in serialized
