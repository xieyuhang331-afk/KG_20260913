from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import UTC, date, datetime
from decimal import Decimal

import asyncpg
import pytest

pytestmark = pytest.mark.integration

_SAFE_ROLE = re.compile(r"[a-z][a-z0-9_]{0,62}")
_LEGACY_TABLES = ("health_profile", "health_indicator", "detection_report")
_LEGACY_SEQUENCES = ("health_profile_id_seq", "health_indicator_id_seq")
_FORBIDDEN_RESPONSE_MARKERS = (
    "postgresql://",
    "postgresql+asyncpg://",
    "select ",
    "insert ",
    "update ",
    "password",
    "credential",
    "traceback",
)


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


async def _mark_verified(user_ids: tuple[int, ...]) -> None:
    database_url = os.environ["KG_TEST_MIGRATION_DATABASE_URL"].replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    connection = await asyncpg.connect(database_url)
    try:
        await connection.execute(
            'UPDATE public."user" SET verify_status=$1,status=$2 '
            "WHERE id=ANY($3::bigint[])",
            "verified",
            "active",
            list(user_ids),
        )
    finally:
        await connection.close()


async def _create_profile_via_r4(connection, user_id: int):
    return await connection.fetchrow(
        "SELECT * FROM public.r4_member_legacy_health_profile_create_v1("
        "$1::bigint,$2::bigint,$3::varchar,$4::date,$5::numeric,$6::numeric,"
        "$7::varchar,$8::jsonb,$9::jsonb,$10::jsonb,$11::varchar,$12::varchar,"
        "$13::jsonb,$14::varchar,$15::varchar,$16::timestamptz)",
        user_id,
        user_id,
        "F",
        date(1990, 1, 1),
        Decimal("165.5"),
        Decimal("55.0"),
        "A",
        "[]",
        None,
        None,
        "never",
        "none",
        "[]",
        "normal",
        "normal",
        datetime(2026, 9, 14, 8, tzinfo=UTC),
    )


def _revoke_fixture_privileges(pg_database) -> None:
    role = os.environ["KG_TEST_APPLICATION_ROLE"]
    assert _SAFE_ROLE.fullmatch(role)
    tables = ", ".join(f'public."{name}"' for name in _LEGACY_TABLES)
    sequences = ", ".join(f'public."{name}"' for name in _LEGACY_SEQUENCES)
    pg_database.execute(f'REVOKE ALL PRIVILEGES ON TABLE {tables} FROM "{role}"')
    pg_database.execute(
        f'REVOKE ALL PRIVILEGES ON SEQUENCE {sequences} FROM "{role}"'
    )


def _member_profile_payload() -> dict:
    return {
        "gender": "female",
        "birth_date": "1990-01-01",
        "height": "165.5",
        "weight": "55.0",
        "blood_type": "A",
        "expected_version": None,
    }


def _legacy_profile_payload() -> dict:
    return {
        "gender": "M",
        "birth_date": "1988-02-03",
        "height": "175.0",
        "weight": "70.0",
        "blood_type": "O",
        "medical_history": [],
        "allergy_history": None,
        "family_history": None,
        "smoking": "never",
        "drinking": "none",
        "symptoms": [],
        "sleep_quality": "normal",
        "bowel_urination": "normal",
    }


def _indicator_payload() -> dict:
    return {
        "indicators": [
            {
                "indicator_type": "systolic_bp",
                "value": "120.00",
                "unit": "mmHg",
                "source": "APP",
                "recorded_at": "2026-09-14T08:00:00Z",
            }
        ]
    }


def _insert_report(pg_database, *, user_id: int, report_id: int) -> None:
    data = json.dumps(
        {
            "metrics": [
                {
                    "indicator_code": "systolic_bp",
                    "value": "120",
                    "unit": "mmHg",
                }
            ]
        },
        separators=(",", ":"),
    ).replace("'", "''")
    pg_database.execute(
        "INSERT INTO public.detection_report "
        "(id,user_id,report_type,detection_time,view_status,summary,"
        "report_schema_version,report_data) VALUES ("
        f"{report_id},{user_id},'store_retest','2026-09-14T09:00:00Z',"
        f"'unread',NULL,1,'{data}'::jsonb)"
    )


def _assert_safe_error(response, status: int, code: str) -> None:
    assert response.status_code == status
    assert response.json()["code"] == code
    assert all(marker not in response.text.lower() for marker in _FORBIDDEN_RESPONSE_MARKERS)


def test_R4_11个旧路由在原生Application_ACL下完成正向闭环(
    real_db_client,
    pg_database,
) -> None:
    self_member = _register(real_db_client, "13800139571")
    explicit_member = _register(real_db_client, "13800139572")
    asyncio.run(_mark_verified((self_member["id"], explicit_member["id"])))
    _revoke_fixture_privileges(pg_database)

    self_headers = _headers(self_member["id"])
    explicit_headers = _headers(explicit_member["id"])

    missing_self = real_db_client.get(
        "/api/v1/users/me/health-profile", headers=self_headers
    )
    assert missing_self.status_code == 200
    assert missing_self.json()["data"]["state"] == "NOT_CREATED"

    self_created = real_db_client.put(
        "/api/v1/users/me/health-profile",
        headers=self_headers,
        json=_member_profile_payload(),
    )
    assert self_created.status_code == 200
    assert self_created.json()["data"]["outcome"] == "CREATED"

    explicit_created = real_db_client.post(
        f"/api/v1/users/{explicit_member['id']}/health-profile",
        headers=explicit_headers,
        json=_legacy_profile_payload(),
    )
    assert explicit_created.status_code == 201
    explicit_read = real_db_client.get(
        f"/api/v1/users/{explicit_member['id']}/health-profile",
        headers=explicit_headers,
    )
    assert explicit_read.status_code == 200

    indicator_created = real_db_client.post(
        f"/api/v1/users/{self_member['id']}/health-indicators",
        headers=self_headers,
        json=_indicator_payload(),
    )
    assert indicator_created.status_code == 201
    for path in (
        f"/api/v1/users/{self_member['id']}/health-indicators",
        f"/api/v1/users/{self_member['id']}/health-indicators/latest",
        "/api/v1/users/me/health-indicators",
        "/api/v1/users/me/health-indicators/latest",
    ):
        response = real_db_client.get(path, headers=self_headers)
        assert response.status_code == 200
        assert response.json()["data"]

    _insert_report(pg_database, user_id=self_member["id"], report_id=94701)
    report_list = real_db_client.get(
        "/api/v1/users/me/detection-reports", headers=self_headers
    )
    assert report_list.status_code == 200
    assert report_list.json()["data"]["items"][0]["report_id"] == 94701
    report_detail = real_db_client.get(
        "/api/v1/users/me/detection-reports/94701", headers=self_headers
    )
    assert report_detail.status_code == 200


def test_R4鉴权越权currentness与已知依赖错误保持冻结合同(
    real_db_client,
    pg_database,
) -> None:
    member = _register(real_db_client, "13800139573")
    other = _register(real_db_client, "13800139574")
    asyncio.run(_mark_verified((member["id"], other["id"])))
    _revoke_fixture_privileges(pg_database)

    _assert_safe_error(
        real_db_client.get("/api/v1/users/me/health-profile"),
        401,
        "AUTHENTICATION_REQUIRED",
    )
    _assert_safe_error(
        real_db_client.get(
            "/api/v1/users/me/health-profile",
            headers={"Authorization": "Bearer malformed"},
        ),
        401,
        "ACCESS_TOKEN_INVALID",
    )
    _assert_safe_error(
        real_db_client.get(
            f"/api/v1/users/{other['id']}/health-profile",
            headers=_headers(member["id"]),
        ),
        403,
        "FORBIDDEN",
    )

    pg_database.execute(
        'UPDATE public."user" SET status=\'disabled\' WHERE id='
        + str(member["id"])
    )
    _assert_safe_error(
        real_db_client.get(
            "/api/v1/users/me/health-profile", headers=_headers(member["id"])
        ),
        401,
        "ACCESS_TOKEN_STALE",
    )


def test_R4函数输入边界在数据库内fail_closed(application_database) -> None:
    with pytest.raises(asyncpg.PostgresError) as invalid_limit:
        application_database.fetch_value(
            "SELECT count(*) FROM public.r4_member_self_health_indicator_history_v1("
            "1,NULL,NULL,NULL,NULL,NULL,202)"
        )
    assert invalid_limit.value.sqlstate == "22023"

    with pytest.raises(asyncpg.InsufficientPrivilegeError) as cross_user:
        application_database.fetch_value(
            "SELECT count(*) FROM public.r4_member_legacy_health_indicator_latest_v1(1,2)"
        )
    assert cross_user.value.sqlstate == "42501"


def test_R4档案锁按用户隔离且等待后重新校验currentness(
    real_db_client,
    pg_database,
) -> None:
    user_a = _register(real_db_client, "13800139575")
    user_b = _register(real_db_client, "13800139576")
    asyncio.run(_mark_verified((user_a["id"], user_b["id"])))

    async def scenario() -> None:
        admin_url = os.environ["KG_TEST_MIGRATION_DATABASE_URL"].replace(
            "postgresql+asyncpg://", "postgresql://", 1
        )
        application_url = os.environ["KG_TEST_DATABASE_URL"].replace(
            "postgresql+asyncpg://", "postgresql://", 1
        )
        holder = await asyncpg.connect(admin_url)
        observer = await asyncpg.connect(admin_url)
        waiter = await asyncpg.connect(
            application_url,
            server_settings={"application_name": "r4_profile_lock_waiter"},
        )
        other = await asyncpg.connect(application_url)
        holder_transaction = holder.transaction()
        waiting_create = None
        try:
            await holder_transaction.start()
            await holder.execute(
                "SELECT pg_catalog.pg_advisory_xact_lock("
                "pg_catalog.hashtextextended('r4-legacy-health-profile:' || "
                "$1::bigint::text,47))",
                user_a["id"],
            )
            waiting_create = asyncio.create_task(
                _create_profile_via_r4(waiter, user_a["id"])
            )

            for _ in range(100):
                if waiting_create.done():
                    await waiting_create
                    raise AssertionError("R4 profile writer bypassed advisory lock")
                is_blocked = await observer.fetchval(
                    "SELECT COALESCE(bool_or(cardinality("
                    "pg_catalog.pg_blocking_pids(pid)) > 0),false) "
                    "FROM pg_catalog.pg_stat_activity "
                    "WHERE application_name='r4_profile_lock_waiter'"
                )
                if is_blocked:
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("R4 profile writer did not reach advisory wait")

            other_result = await asyncio.wait_for(
                _create_profile_via_r4(other, user_b["id"]), timeout=5
            )
            assert other_result is not None

            await observer.execute(
                'UPDATE public."user" SET status=$1 WHERE id=$2',
                "disabled",
                user_a["id"],
            )
            await holder_transaction.rollback()
            with pytest.raises(asyncpg.RaiseError) as stale_actor:
                await waiting_create
            assert stale_actor.value.sqlstate == "P0001"
            assert "R4_MEMBER_HEALTH_CURRENTNESS_INVALID" in str(stale_actor.value)

            assert await observer.fetchval(
                "SELECT count(*) FROM public.health_profile WHERE user_id=$1",
                user_a["id"],
            ) == 0
            assert await observer.fetchval(
                "SELECT count(*) FROM public.health_profile WHERE user_id=$1",
                user_b["id"],
            ) == 1

            await observer.execute(
                'UPDATE public."user" SET status=$1 WHERE id=$2',
                "active",
                user_a["id"],
            )
            assert await asyncio.wait_for(
                _create_profile_via_r4(waiter, user_a["id"]), timeout=5
            ) is not None
        finally:
            if waiting_create is not None and not waiting_create.done():
                waiting_create.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await waiting_create
            if holder.is_in_transaction():
                await holder_transaction.rollback()
            await other.close()
            await waiter.close()
            await observer.close()
            await holder.close()

    asyncio.run(scenario())
    assert pg_database.fetch_value(
        "SELECT COUNT(*) FROM health_profile WHERE user_id = " + str(user_a["id"])
    ) == 1
