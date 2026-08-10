import pytest


def test_P3机构管理员候选与三态分配尚未实现():
    try:
        from app.modules.organization.domain import normalize_display_name
    except (ImportError, ModuleNotFoundError):
        pytest.fail("P3 Organization administrator assignment is not implemented")

    assert normalize_display_name("  测试甲  ") == "测试甲"
    assert normalize_display_name(None) is None
    assert normalize_display_name("   ") is None
    assert normalize_display_name("测试\n甲") is None


def test_管理员候选display_name是唯一最小PII例外():
    from app.modules.organization.domain import candidate_projection

    user = {
        "id": 7,
        "real_name": " 测试乙 ",
        "role": "province_admin",
        "status": "active",
        "tenant_id": None,
        "phone": "not-readable",
    }
    assert candidate_projection(user, assigned_to_current=False) == {
        "user_id": 7,
        "display_name": "测试乙",
        "role": "province_admin",
        "assignment_status": "unassigned",
    }


def test_admin_candidate_page_scans_past_invalid_display_names():
    import asyncio

    from app.modules.organization.service import _collect_admin_candidate_page

    rows = [
        {"id": 1, "real_name": "   ", "role": "province_admin", "status": "active", "tenant_id": None},
        {"id": 2, "real_name": "bad\nname", "role": "province_admin", "status": "active", "tenant_id": None},
        {"id": 3, "real_name": "Fictional Reviewer", "role": "province_admin", "status": "active", "tenant_id": None},
        {"id": 4, "real_name": "Fictional Reviewer Two", "role": "province_admin", "status": "active", "tenant_id": None},
    ]
    cursors = []

    async def fetch(cursor_id, limit):
        cursors.append(cursor_id)
        return [row for row in rows if cursor_id is None or row["id"] > cursor_id][:limit]

    items, next_cursor_id = asyncio.run(_collect_admin_candidate_page(
        fetch, page_size=1, current_admin_id=None, cursor_id=None
    ))

    assert items == [{
        "user_id": 3,
        "display_name": "Fictional Reviewer",
        "role": "province_admin",
        "assignment_status": "unassigned",
    }]
    assert next_cursor_id == 3
    assert cursors == [None, 2]
