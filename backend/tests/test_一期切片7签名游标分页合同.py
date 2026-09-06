from pathlib import Path
from uuid import UUID

import pytest

from app.modules.service_fulfillment.service import (
    ServiceFulfillmentError,
    decode_page_cursor,
    encode_page_cursor,
)


CURSOR_ID = UUID("0198f1c0-0000-7000-8000-0000000000d1")
SNAPSHOT_CEILING = UUID("0198f1c0-0000-7000-8000-0000000000f1")
SCOPE_ID = UUID("0198f1c0-0000-7000-8000-0000000000d2")


def test_D_分页游标不透明签名且绑定资源Scope身份与租户(monkeypatch) -> None:
    monkeypatch.setenv("KG_SLICE7_CURSOR_SIGNING_KEY", "slice7-cursor-synthetic-key-0000000000000000")
    cursor = encode_page_cursor(
        CURSOR_ID,
        snapshot_ceiling=SNAPSHOT_CEILING,
        resource="MILESTONE",
        scope_id=SCOPE_ID,
        actor_user_id=91,
        actor_role="therapist",
        actor_tenant_id=7,
        filters={"status": "DUE", "risk": None},
    )
    assert str(CURSOR_ID) not in cursor
    assert cursor.count(".") == 1
    assert decode_page_cursor(
        cursor,
        resource="MILESTONE",
        scope_id=SCOPE_ID,
        actor_user_id=91,
        actor_role="therapist",
        actor_tenant_id=7,
        filters={"status": "DUE", "risk": None},
    ) == (CURSOR_ID, SNAPSHOT_CEILING)

    variants = (
        {"resource": "TRANSFER"},
        {"scope_id": UUID("0198f1c0-0000-7000-8000-0000000000d3")},
        {"actor_user_id": 92},
        {"actor_role": "member"},
        {"actor_tenant_id": 8},
        {"filters": {"status": "COMPLETED", "risk": None}},
        {"filters": {"status": "DUE", "risk": "AT_RISK"}},
    )
    baseline = {
        "resource": "MILESTONE",
        "scope_id": SCOPE_ID,
        "actor_user_id": 91,
        "actor_role": "therapist",
        "actor_tenant_id": 7,
        "filters": {"status": "DUE", "risk": None},
    }
    for variant in variants:
        with pytest.raises(ServiceFulfillmentError, match="^INVALID_REQUEST$"):
            decode_page_cursor(cursor, **{**baseline, **variant})


def test_D_分页游标篡改和非规范编码稳定拒绝(monkeypatch) -> None:
    monkeypatch.setenv("KG_SLICE7_CURSOR_SIGNING_KEY", "slice7-cursor-synthetic-key-0000000000000000")
    cursor = encode_page_cursor(
        CURSOR_ID,
        snapshot_ceiling=SNAPSHOT_CEILING,
        resource="EXPORT",
        scope_id=None,
        actor_user_id=91,
        actor_role="super_admin",
        actor_tenant_id=None,
        filters={"status": "READY"},
    )
    payload, signature = cursor.split(".")
    invalid = (
        cursor[:-1] + ("A" if cursor[-1] != "A" else "B"),
        f"{payload}=.{signature}",
        "not-a-cursor",
        "",
    )
    for value in invalid:
        with pytest.raises(ServiceFulfillmentError, match="^INVALID_REQUEST$"):
            decode_page_cursor(
                value,
                resource="EXPORT",
                scope_id=None,
                actor_user_id=91,
                actor_role="super_admin",
                actor_tenant_id=None,
                filters={"status": "READY"},
            )


def test_D_履约列表状态筛选以ServiceCase为根且包含尚未激活周期的Case() -> None:
    migration = (
        Path(__file__).parents[1]
        / "app/migrations/versions/20260827_0032_phase1_slice7_service_fulfillment_closure_transfer_export.py"
    ).read_text(encoding="utf-8")
    start = migration.index('        "slice7_read_many_v1",')
    function = migration[start : migration.index('        "slice7_worker_claim_v1",', start)]
    assert "FROM public.service_case c CROSS JOIN LATERAL" in function
    assert "eligible AS MATERIALIZED" in function
    assert "e.case_id>value_cursor" in function
    assert "e.case_id<=COALESCE(value_ceiling" in function
    assert "'ceiling',COALESCE(value_ceiling" in function


def test_D_四类列表以UUID原生倒序取得快照上界且不使用max聚合() -> None:
    migration = (
        Path(__file__).parents[1]
        / "app/migrations/versions/20260827_0032_phase1_slice7_service_fulfillment_closure_transfer_export.py"
    ).read_text(encoding="utf-8")
    start = migration.index('        "slice7_read_many_v1",')
    function = migration[start : migration.index('        "slice7_worker_claim_v1",', start)]

    for identifier in ("milestone_id", "transfer_id", "export_id", "case_id"):
        assert f"max(x.{identifier})" not in function
        assert (
            f"(SELECT x.{identifier} FROM eligible x "
            f"ORDER BY x.{identifier} DESC LIMIT 1)"
        ) in function


def test_D_游标快照上界防止并发新增记录进入后续页(monkeypatch) -> None:
    monkeypatch.setenv("KG_SLICE7_CURSOR_SIGNING_KEY", "slice7-cursor-synthetic-key-0000000000000000")
    cursor = encode_page_cursor(
        CURSOR_ID,
        snapshot_ceiling=SNAPSHOT_CEILING,
        resource="TRANSFER",
        scope_id=None,
        actor_user_id=91,
        actor_role="super_admin",
        actor_tenant_id=None,
        filters={"status": None, "risk": None},
    )
    assert str(SNAPSHOT_CEILING) not in cursor
    assert decode_page_cursor(
        cursor,
        resource="TRANSFER",
        scope_id=None,
        actor_user_id=91,
        actor_role="super_admin",
        actor_tenant_id=None,
        filters={"status": None, "risk": None},
    ) == (CURSOR_ID, SNAPSHOT_CEILING)
