import hashlib
import hmac
import json

import pytest
from dataclasses import replace


def _chain():
    from app.modules.organization_projection.domain import OrganizationSourceNode
    return (
        OrganizationSourceNode(4, 3, "C-4", "county", "county", "active", 0, 1),
        OrganizationSourceNode(3, 2, "C-3", "city", "city", "active", 0, 1),
        OrganizationSourceNode(2, 1, "C-2", "province", "province", "active", 0, 1),
        OrganizationSourceNode(1, None, "C-1", "hq", "headquarter", "active", 0, 1),
    )


def test_Organization_Shadow摘要使用冻结字节合同():
    from app.modules.organization_projection.service import shadow_component_digest

    payload = {
        "generation_id": 41,
        "high_watermark_digest": "1" * 64,
        "item_count": 1,
        "items": ["0" * 64],
        "projection_version": 1,
        "rule_version": "organization-projection-v1",
    }
    key = bytes(range(32))
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    expected = hmac.new(key, b"kg:projection:shadow:organization:source:v1\0" + encoded, hashlib.sha256).hexdigest().upper()
    assert expected == "962FCD1E02CE2ABD3A7E85682B5720C0B7B3EC905BEB1744624B2960EF0726AF"
    assert shadow_component_digest(domain="organization", component="source", payload=payload, key=key) == expected


@pytest.mark.parametrize("component", ["unknown", "selection", "currentness"])
def test_Organization_Shadow拒绝未知或跨领域摘要(component):
    from app.modules.organization_projection.service import shadow_component_digest
    with pytest.raises(Exception):
        shadow_component_digest(domain="organization", component=component, payload={}, key=bytes(32))


def test_organization_shadow_recomputes_and_detects_tamper():
    from app.modules.organization_projection.domain import build_organization_projection_row
    from app.modules.organization_projection.service import build_organization_shadow_evidence
    key = bytes(range(32))
    row = build_organization_projection_row(chain=_chain(), digest_key=key)
    evidence = build_organization_shadow_evidence(generation_id=41, projection_version=1, high_watermark={"max_organization_id": 4}, digest_key_id="k1", generation_input_digest="2" * 64, source_chains=(_chain(),), mappings=(), projected_rows=(row,), digest_key=key)
    assert evidence.blocker_count == 0
    failed = build_organization_shadow_evidence(generation_id=41, projection_version=1, high_watermark={"max_organization_id": 4}, digest_key_id="k1", generation_input_digest="2" * 64, source_chains=(_chain(),), mappings=(), projected_rows=(replace(row, org_name="tampered"),), digest_key=key)
    assert failed.category_counts == {"ORG_ROW_MISMATCH": 1}


def test_organization_path_versions_are_immutable_build_baseline():
    from app.modules.organization_projection.domain import build_organization_projection_row
    row = build_organization_projection_row(chain=_chain(), digest_key=bytes(range(32)))
    assert row.path_versions == (1, 1, 1, 1)


def test_organization_post_hwm合法版本推进只产生informational():
    from app.modules.organization_projection.domain import build_organization_projection_row
    from app.modules.organization_projection.service import build_organization_shadow_evidence
    key = bytes(range(32))
    old = build_organization_projection_row(chain=_chain(), digest_key=key)
    live = tuple(replace(node, version=2, org_name="new") if index == 0 else node for index, node in enumerate(_chain()))
    evidence = build_organization_shadow_evidence(
        generation_id=41, projection_version=1,
        high_watermark={"max_organization_id": 4}, digest_key_id="k1",
        generation_input_digest="2" * 64, source_chains=(live,), mappings=(),
        projected_rows=(old,),
        post_hwm_sources=(type("Changed", (), {
            "id": live[0].id, "version": live[0].version,
            "updated_at": __import__("datetime").datetime.now(__import__("datetime").UTC),
        })(),), digest_key=key,
    )
    assert evidence.blocker_count == 0
    assert evidence.category_counts == {"ORG_POST_BUILD_VALID_VERSION_ADVANCE": 1}


@pytest.mark.parametrize("live_version", [1, 0])
def test_organization_ancestor_same_version_drift_or_downgrade_is_blocker(live_version):
    from app.modules.organization_projection.domain import build_organization_projection_row
    from app.modules.organization_projection.service import build_organization_shadow_evidence
    key = bytes(range(32))
    old = build_organization_projection_row(chain=_chain(), digest_key=key)
    live = tuple(replace(node, version=live_version, org_name="drift") if index == 2 else node for index, node in enumerate(_chain()))
    changed = live[2]
    evidence = build_organization_shadow_evidence(
        generation_id=41, projection_version=1,
        high_watermark={"max_organization_id": 4}, digest_key_id="k1",
        generation_input_digest="2" * 64, source_chains=(live,), mappings=(),
        projected_rows=(old,), post_hwm_sources=(type("Changed", (), {
            "id": changed.id, "version": changed.version,
            "updated_at": __import__("datetime").datetime.now(__import__("datetime").UTC),
        })(),), digest_key=key,
    )
    assert evidence.blocker_count > 0
    expected = "ORG_UNPROVEN_SOURCE_DRIFT" if live_version >= 1 else "ORG_CHAIN_INVALID"
    assert expected in evidence.category_counts


def test_organization_path_ids_and_path_versions_must_align():
    from app.modules.organization_projection.domain import build_organization_projection_row
    from app.modules.organization_projection.service import build_organization_shadow_evidence
    row = build_organization_projection_row(chain=_chain(), digest_key=bytes(range(32)))
    malformed = replace(row, path_versions=(1, 1, 1))
    evidence = build_organization_shadow_evidence(
        generation_id=41, projection_version=1,
        high_watermark={"max_organization_id": 4}, digest_key_id="k1",
        generation_input_digest="2" * 64, source_chains=(_chain(),), mappings=(),
        projected_rows=(malformed,), digest_key=bytes(range(32)),
    )
    assert evidence.blocker_count > 0


@pytest.mark.parametrize(
    "mutation",
    (
        lambda node: replace(node, version=node.version + 1, org_type="county"),
        lambda node: replace(node, version=node.version + 1, status="corrupted"),
        lambda node: replace(node, version=node.version + 1, parent_id=999),
    ),
)
def test_organization_ancestor_version_advance_with_invalid_chain_is_blocker(mutation):
    from app.modules.organization_projection.domain import build_organization_projection_row
    from app.modules.organization_projection.service import build_organization_shadow_evidence
    key = bytes(range(32))
    old_chain = _chain()
    old = build_organization_projection_row(chain=old_chain, digest_key=key)
    live = tuple(mutation(node) if index == 2 else node for index, node in enumerate(old_chain))
    changed = live[2]
    evidence = build_organization_shadow_evidence(
        generation_id=41, projection_version=1,
        high_watermark={"max_organization_id": 4}, digest_key_id="k1",
        generation_input_digest="2" * 64, source_chains=(live,), mappings=(),
        projected_rows=(old,), post_hwm_sources=(type("Changed", (), {
            "id": changed.id, "version": changed.version,
            "updated_at": __import__("datetime").datetime.now(__import__("datetime").UTC),
        })(),), digest_key=key,
    )
    assert evidence.blocker_count > 0
    assert "ORG_CHAIN_INVALID" in evidence.category_counts
    assert "ORG_POST_BUILD_VALID_VERSION_ADVANCE" not in evidence.category_counts


@pytest.mark.parametrize("ancestor_index", [1, 2, 3])
def test_organization_ancestor_post_hwm合法推进不产生ROW_MISMATCH(ancestor_index):
    from app.modules.organization_projection.domain import build_organization_projection_row
    from app.modules.organization_projection.service import build_organization_shadow_evidence
    key = bytes(range(32))
    old_chain = _chain()
    old = build_organization_projection_row(chain=old_chain, digest_key=key)
    live = tuple(
        replace(node, version=node.version + 1, org_name=f"advanced-{node.id}")
        if index == ancestor_index else node
        for index, node in enumerate(old_chain)
    )
    changed = live[ancestor_index]
    evidence = build_organization_shadow_evidence(
        generation_id=41, projection_version=1,
        high_watermark={"max_organization_id": 4}, digest_key_id="k1",
        generation_input_digest="2" * 64, source_chains=(live,), mappings=(),
        projected_rows=(old,), post_hwm_sources=(type("Changed", (), {
            "id": changed.id, "version": changed.version,
            "updated_at": __import__("datetime").datetime.now(__import__("datetime").UTC),
        })(),), digest_key=key,
    )
    assert evidence.blocker_count == 0
    assert evidence.category_counts == {"ORG_POST_BUILD_VALID_VERSION_ADVANCE": 1}
