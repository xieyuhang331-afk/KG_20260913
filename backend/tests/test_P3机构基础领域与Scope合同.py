import pytest


def test_P3机构四级组织领域尚未实现():
    try:
        from app.modules.organization.domain import (
            CANONICAL_LEVELS,
            OrganizationDataCorrupted,
            classify_compatibility,
            validate_parent_type,
        )
    except (ImportError, ModuleNotFoundError):
        pytest.fail("P3 Organization domain and scope are not implemented")

    assert CANONICAL_LEVELS == ("headquarter", "province", "city", "county")
    assert validate_parent_type("headquarter", "province") is None
    assert validate_parent_type("province", "city") is None
    assert classify_compatibility("platform", "active") == "legacy"
    assert classify_compatibility("province", "disabled") == "legacy"
    with pytest.raises(OrganizationDataCorrupted):
        classify_compatibility("unknown", "active")


def test_P3机构祖先链必须完整active_canonical():
    from app.modules.organization.domain import OrganizationCurrentnessInvalid, validate_scope_chain

    valid = [
        {"id": 1, "parent_id": None, "org_type": "headquarter", "status": "active"},
        {"id": 2, "parent_id": 1, "org_type": "province", "status": "active"},
        {"id": 3, "parent_id": 2, "org_type": "city", "status": "active"},
    ]
    validate_scope_chain(valid, bound_type="city")
    for field, value in (("status", "inactive"), ("org_type", "tenant_org")):
        broken = [dict(item) for item in valid]
        broken[1][field] = value
        with pytest.raises(OrganizationCurrentnessInvalid):
            validate_scope_chain(broken, bound_type="city")


def test_tenant_descendant_scope_excludes_legacy_and_archived_and_rejects_corruption():
    from app.modules.organization.domain import OrganizationDataCorrupted
    from app.modules.organization.service import _tenant_scope_ids

    rows = [
        {"id": 1, "parent_id": None, "org_type": "headquarter", "status": "active"},
        {"id": 2, "parent_id": 1, "org_type": "province", "status": "active"},
        {"id": 3, "parent_id": 2, "org_type": "city", "status": "inactive"},
        {"id": 4, "parent_id": 2, "org_type": "tenant_org", "status": "active"},
        {"id": 5, "parent_id": 2, "org_type": "city", "status": "archived"},
    ]

    assert _tenant_scope_ids(rows, root_id=2, super_admin=False) == {2, 3}
    assert _tenant_scope_ids(rows, root_id=2, super_admin=True) == {2, 3, 4, 5}

    rows.append({"id": 6, "parent_id": 2, "org_type": "corrupted", "status": "active"})
    with pytest.raises(OrganizationDataCorrupted):
        _tenant_scope_ids(rows, root_id=2, super_admin=False)
