import base64
import json

import pytest


def _key(value: int) -> str:
    return base64.b64encode(bytes([value]) * 32).decode("ascii")


def test_机构Legacy映射领域尚未实现():
    from app.modules.organization_mapping.domain import (
        OrganizationMappingDigestKeyring,
        OrganizationSourceSnapshot,
        build_organization_mapping,
    )

    keyring = OrganizationMappingDigestKeyring.from_json(
        current_key_id="k1",
        keyring_json=json.dumps({"k1": _key(1)}),
    )
    result = build_organization_mapping(
        source=OrganizationSourceSnapshot(
            legacy_tenant_id=7,
            legacy_org_id=9,
            target={
                "id": 9,
                "parent_id": 3,
                "org_type": "city",
                "status": "active",
                "version": 2,
            },
            ancestors=(
                {"id": 3, "parent_id": 1, "org_type": "province", "status": "active", "version": 1},
                {"id": 1, "parent_id": None, "org_type": "headquarter", "status": "active", "version": 1},
            ),
        ),
        mapping_version=1,
        batch_id="00000000-0000-0000-0000-000000000001",
        keyring=keyring,
    )
    assert result.disposition == "MAPPED"
    assert result.reason_code == "MAPPED_EXACT"
    assert result.canonical_organization_id == 9
    assert result.digest_key_id == "k1"
    assert len(result.source_fingerprint) == 64


def test_orphan_org_id可形成确定UNMAPPED且不伪造target():
    from app.modules.organization_mapping.domain import (
        OrganizationMappingDigestKeyring,
        OrganizationSourceSnapshot,
        build_organization_mapping,
    )

    keyring = OrganizationMappingDigestKeyring.from_json(
        current_key_id="k1", keyring_json=json.dumps({"k1": _key(1)})
    )
    result = build_organization_mapping(
        source=OrganizationSourceSnapshot(legacy_tenant_id=7, legacy_org_id=999, target=None),
        mapping_version=1,
        batch_id="00000000-0000-0000-0000-000000000001",
        keyring=keyring,
    )
    assert (result.disposition, result.reason_code) == ("UNMAPPED", "TARGET_NOT_FOUND")
    assert result.legacy_org_id == 999
    assert result.canonical_organization_id is None


def test_机构keyring重复或未知key必须fail_closed():
    from app.modules.organization_mapping.domain import (
        OrganizationMappingUnavailable,
        OrganizationMappingDigestKeyring,
    )

    with pytest.raises(OrganizationMappingUnavailable):
        OrganizationMappingDigestKeyring.from_json(
            current_key_id="k1",
            keyring_json='{"k1":"%s","k1":"%s"}' % (_key(1), _key(2)),
        )
    with pytest.raises(OrganizationMappingUnavailable):
        OrganizationMappingDigestKeyring.from_json(
            current_key_id="missing", keyring_json=json.dumps({"k1": _key(1)})
        )


@pytest.mark.parametrize(
    ("ancestors", "expected"),
    [
        ((None,), ("BLOCKED", "TARGET_CHAIN_INVALID")),
        (({"id": 9, "parent_id": 3, "org_type": "city", "status": "active", "version": 1},), ("BLOCKED", "TARGET_CHAIN_INVALID")),
        (({"id": 3, "parent_id": None, "org_type": "platform", "status": "active", "version": 1},), ("REVIEW_REQUIRED", "TARGET_LEGACY")),
        (({"id": 3, "parent_id": None, "org_type": "province", "status": "archived", "version": 1},), ("REVIEW_REQUIRED", "TARGET_ARCHIVED")),
        (({"id": 3, "parent_id": None, "org_type": "unknown", "status": "active", "version": 1},), ("BLOCKED", "SOURCE_CORRUPTED")),
        (({"id": 3, "parent_id": None, "org_type": "province", "status": "active", "version": 1},), ("BLOCKED", "TARGET_CHAIN_INVALID")),
        (({"id": 3, "parent_id": 1, "org_type": "province", "status": "active", "version": 1}, {"id": 1, "parent_id": 2, "org_type": "headquarter", "status": "active", "version": 1}), ("BLOCKED", "TARGET_CHAIN_INVALID")),
    ],
)
def test_机构祖先链异常按冻结reason_fail_closed(ancestors, expected):
    from app.modules.organization_mapping.domain import (
        OrganizationMappingDigestKeyring,
        OrganizationSourceSnapshot,
        build_organization_mapping,
    )

    keyring = OrganizationMappingDigestKeyring.from_json(
        current_key_id="k1", keyring_json=json.dumps({"k1": _key(1)})
    )
    result = build_organization_mapping(
        source=OrganizationSourceSnapshot(
            7,
            9,
            {"id": 9, "parent_id": 3, "org_type": "city", "status": "active", "version": 2},
            ancestors=ancestors,
        ),
        mapping_version=1,
        batch_id="00000000-0000-0000-0000-000000000001",
        keyring=keyring,
    )
    assert (result.disposition, result.reason_code) == expected
