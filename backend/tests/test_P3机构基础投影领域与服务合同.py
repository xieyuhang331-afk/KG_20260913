import json

import pytest

from app.modules.organization_projection.domain import (
    OrganizationSourceNode,
    ProjectionSourceInvalid,
    ProjectionUnavailable,
    build_organization_projection_row,
)


PUBLIC_VECTOR_KEY = bytes(range(32))


def _chain(*, leaf_status="active", ancestor_status="active"):
    return (
        OrganizationSourceNode(101, 10, "CN-SH-PD", "Fictional Pudong Org", "county", leaf_status, 7, 3),
        OrganizationSourceNode(10, 2, "PD", "Pudong", "city", ancestor_status, 2, 4),
        OrganizationSourceNode(2, 1, "SH", "Shanghai", "province", "active", 1, 2),
        OrganizationSourceNode(1, None, "HQ", "Headquarter", "headquarter", "active", 1, 1),
    )


def test_完整四级链固定为HQ到leaf且scope可用():
    row = build_organization_projection_row(chain=_chain(), digest_key=PUBLIC_VECTOR_KEY)
    assert row.path_ids == (1, 2, 10, 101)
    assert row.path_codes == ("HQ", "SH", "PD", "CN-SH-PD")
    assert row.scope_eligible is True


@pytest.mark.parametrize("status", ["inactive", "archived"])
def test_inactive或archived节点隔离scope(status):
    row = build_organization_projection_row(
        chain=_chain(ancestor_status=status), digest_key=PUBLIC_VECTOR_KEY
    )
    assert row.scope_eligible is False


@pytest.mark.parametrize(
    "chain",
    [
        (OrganizationSourceNode(1, None, "HQ", "HQ", "headquarter", "active", 1, 1),),
        (OrganizationSourceNode(2, 1, "P", "P", "province", "active", 1, 1), OrganizationSourceNode(1, None, "HQ", "HQ", "headquarter", "active", 1, 1)),
        (OrganizationSourceNode(3, 2, "C", "C", "city", "active", 1, 1), OrganizationSourceNode(2, 1, "P", "P", "province", "active", 1, 1), OrganizationSourceNode(1, None, "HQ", "HQ", "headquarter", "active", 1, 1)),
        (OrganizationSourceNode(2, 99, "P", "P", "province", "active", 1, 1), OrganizationSourceNode(1, None, "HQ", "HQ", "headquarter", "active", 1, 1)),
        (OrganizationSourceNode(1, 1, "HQ", "HQ", "headquarter", "active", 1, 1),),
        (OrganizationSourceNode(1, None, "P", "P", "province", "active", 1, 1),),
    ],
)
def test_短链断链环和非HQ根均拒绝(chain):
    with pytest.raises(ProjectionSourceInvalid):
        build_organization_projection_row(chain=chain, digest_key=PUBLIC_VECTOR_KEY)


def test_机构摘要公开向量逐字节匹配():
    row = build_organization_projection_row(chain=_chain(), digest_key=PUBLIC_VECTOR_KEY)
    payload = {
        "organization_id": 101,
        "parent_id": 10,
        "org_code": "CN-SH-PD",
        "org_name": "Fictional Pudong Org",
        "org_type": "county",
        "status": "active",
        "sort_order": 7,
        "source_version": 3,
        "path_ids": [1, 2, 10, 101],
        "path_codes": ["HQ", "SH", "PD", "CN-SH-PD"],
        "path_versions": [1, 2, 4, 3],
        "compatibility_mode": "canonical",
        "scope_eligible": True,
    }
    assert json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) == (
        '{"compatibility_mode":"canonical","org_code":"CN-SH-PD","org_name":"Fictional Pudong Org",'
        '"org_type":"county","organization_id":101,"parent_id":10,"path_codes":["HQ","SH","PD",'
        '"CN-SH-PD"],"path_ids":[1,2,10,101],"path_versions":[1,2,4,3],"scope_eligible":true,"sort_order":7,"source_version":3,'
        '"status":"active"}'
    )
    assert row.row_digest.upper() == "3622DC57F985CBBEC20A030C0BBDF76ECC33030F9E445EB42883C15458751F62"


@pytest.mark.parametrize("key", [None, "not-bytes", b"short"])
def test_摘要密钥缺失类型错误或过短均fail_closed(key):
    with pytest.raises(ProjectionUnavailable):
        build_organization_projection_row(chain=_chain(), digest_key=key)
