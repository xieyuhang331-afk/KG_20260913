from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
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


def _headers(user_id: int, *, role: str = "member") -> dict[str, str]:
    from app.core.security import create_access_token

    token = create_access_token({"sub": str(user_id), "role": role})
    return {"Authorization": f"Bearer {token}"}


async def _set_currentness_async(user_id: int, *, status="active", verify_status="verified") -> None:
    database_url = os.environ["KG_TEST_MIGRATION_DATABASE_URL"].replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    connection = await asyncpg.connect(database_url)
    try:
        await connection.execute(
            'UPDATE public."user" SET status=$2, verify_status=$3 WHERE id=$1',
            user_id,
            status,
            verify_status,
        )
    finally:
        await connection.close()


def _set_currentness(user_id: int, **changes) -> None:
    asyncio.run(_set_currentness_async(user_id, **changes))


def _insert_report(pg_database, *, user_id: int, report_id: int, at: datetime, schema_version=1) -> None:
    payload = json.dumps(
        {"metrics": [{"indicator_code": "systolic_bp", "value": "120", "unit": "mmHg"}]},
        separators=(",", ":"),
    ).replace("'", "''")
    pg_database.execute(
        "INSERT INTO public.detection_report "
        "(id,user_id,report_type,detection_time,view_status,summary,report_schema_version,report_data) "
        f"VALUES ({report_id},{user_id},'store_retest','{at.isoformat()}','unread',NULL,{schema_version},'{payload}'::jsonb)"
    )


def test_本人报告列表详情分页IDOR与currentness闭环(real_db_client, pg_database) -> None:
    user = _register(real_db_client, "13800139601")
    other = _register(real_db_client, "13800139602")
    _set_currentness(user["id"])
    _set_currentness(other["id"])
    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    _insert_report(pg_database, user_id=user["id"], report_id=91001, at=now)
    _insert_report(pg_database, user_id=user["id"], report_id=91002, at=now + timedelta(minutes=1))
    _insert_report(pg_database, user_id=other["id"], report_id=92001, at=now)

    headers = _headers(user["id"])
    first = real_db_client.get(
        "/api/v1/users/me/detection-reports", params={"limit": 1}, headers=headers
    )
    assert first.status_code == 200
    first_data = first.json()["data"]
    assert [item["report_id"] for item in first_data["items"]] == [91002]
    assert first_data["next_cursor"]

    _insert_report(pg_database, user_id=user["id"], report_id=91003, at=now + timedelta(minutes=2))
    second = real_db_client.get(
        "/api/v1/users/me/detection-reports",
        params={"limit": 2, "cursor": first_data["next_cursor"]},
        headers=headers,
    )
    assert second.status_code == 200
    assert [item["report_id"] for item in second.json()["data"]["items"]] == [91001]

    detail = real_db_client.get("/api/v1/users/me/detection-reports/91001", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["data"]["metrics"] == [
        {"indicator_code": "systolic_bp", "value": "120", "unit": "mmHg", "measured_at": None}
    ]
    for report_id in (92001, 99999):
        denied = real_db_client.get(
            f"/api/v1/users/me/detection-reports/{report_id}", headers=headers
        )
        assert denied.status_code == 404
        body = denied.json()
        assert set(body) == {"code", "message", "request_id", "field_errors", "retryable"}
        assert (body["code"], body["message"], body["field_errors"], body["retryable"]) == (
            "DETECTION_REPORT_NOT_FOUND", "request rejected", [], False,
        )
        assert denied.headers["X-Request-ID"] == body["request_id"]
        assert denied.headers["Cache-Control"] == "no-store, private"
        assert denied.headers["Pragma"] == "no-cache"

    assert pg_database.fetch_value(
        f"SELECT COUNT(*) FROM public.detection_report WHERE user_id={user['id']}"
    ) == 3
    original_status = pg_database.fetch_value(
        f'SELECT status FROM public."user" WHERE id={user["id"]}'
    )
    try:
        _set_currentness(user["id"], status="disabled")
        unavailable = real_db_client.get("/api/v1/users/me/detection-reports", headers=headers)
        assert unavailable.status_code == 401
        body = unavailable.json()
        assert set(body) == {"code", "message", "request_id", "field_errors", "retryable"}
        assert (body["code"], body["message"], body["field_errors"], body["retryable"]) == (
            "ACCESS_TOKEN_STALE", "request rejected", [], False,
        )
        assert unavailable.headers["X-Request-ID"] == body["request_id"]
        assert unavailable.headers["WWW-Authenticate"] == "Bearer"
        assert unavailable.headers["Cache-Control"] == "no-store, private"
        assert unavailable.headers["Pragma"] == "no-cache"
        assert pg_database.fetch_value(
            f"SELECT COUNT(*) FROM public.detection_report WHERE user_id={user['id']}"
        ) == 3
    finally:
        _set_currentness(user["id"], status=original_status)


def test_损坏schema_fail_closed且应用与只读角色不能写(real_db_client, pg_database) -> None:
    user = _register(real_db_client, "13800139603")
    _set_currentness(user["id"])
    _insert_report(
        pg_database,
        user_id=user["id"],
        report_id=93001,
        at=datetime.now(timezone.utc),
        schema_version=2,
    )
    response = real_db_client.get(
        "/api/v1/users/me/detection-reports/93001",
        headers=_headers(user["id"]),
    )
    assert response.status_code == 409
    body = response.json()
    assert set(body) == {"code", "message", "request_id", "field_errors", "retryable"}
    assert (body["code"], body["message"], body["field_errors"], body["retryable"]) == (
        "DETECTION_REPORT_CONTENT_INCONSISTENT", "request rejected", [], False,
    )
    assert response.headers["X-Request-ID"] == body["request_id"]
    assert response.headers["Cache-Control"] == "no-store, private"
    assert response.headers["Pragma"] == "no-cache"

    async def assert_read_only(environment_name: str) -> None:
        connection = await asyncpg.connect(
            os.environ[environment_name].replace("postgresql+asyncpg://", "postgresql://", 1)
        )
        try:
            assert await connection.fetchval("SELECT COUNT(*) FROM public.detection_report") >= 1
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await connection.execute(
                    "UPDATE public.detection_report SET view_status='read' WHERE id=93001"
                )
        finally:
            await connection.close()

    asyncio.run(assert_read_only("KG_TEST_DATABASE_URL"))
    asyncio.run(assert_read_only("KG_TEST_READONLY_DATABASE_URL"))
