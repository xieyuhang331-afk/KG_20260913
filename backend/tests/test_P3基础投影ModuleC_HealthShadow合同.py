import pytest
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal


def test_Health_Shadow摘要冻结向量():
    from app.modules.health_projection.service import shadow_component_digest
    payload = {"generation_id":41,"high_watermark_digest":"1"*64,"item_count":1,"items":["0"*64],"projection_version":1,"rule_version":"health-daily-selection-v1"}
    assert shadow_component_digest(domain="health", component="source", payload=payload, key=bytes(range(32))) == "CED69079FC9D74E8B9FEF31908B1A2E53E8161DE145DA2DCC2A48EC568BC2332"


def test_Health_Shadow拒绝非规范payload():
    from app.modules.health_projection.service import shadow_component_digest
    with pytest.raises(Exception):
        shadow_component_digest(domain="health", component="source", payload={"value": float("nan")}, key=bytes(32))


def test_health_shadow_recomputes_winner_and_coverage():
    from app.modules.health_projection.domain import HealthCurrentFact, build_health_projection_rows
    from app.modules.health_projection.service import build_health_shadow_evidence
    key = bytes(range(32))
    facts = (
        HealthCurrentFact(1, 9, "heart_rate", Decimal("70.00"), "bpm", datetime(2026, 8, 11, 1, tzinfo=UTC), datetime(2026, 8, 11, 1, 1, tzinfo=UTC), "APP"),
        HealthCurrentFact(2, 9, "heart_rate", Decimal("71.00"), "bpm", datetime(2026, 8, 11, 2, tzinfo=UTC), datetime(2026, 8, 11, 2, 1, tzinfo=UTC), "DEVICE"),
    )
    rows, selections = build_health_projection_rows(facts=facts, digest_key=key)
    visibility = tuple(type("V", (), {"fact_id": fact.id, "supersedes_fact_id": None, "is_current": True, "inserting_xid": fact.id})() for fact in facts)
    arguments = dict(generation_id=41, projection_version=1, high_watermark={"max_fact_id": 2, "source_snapshot": "1:3:"}, digest_key_id="k1", generation_input_digest="2" * 64, current_facts=facts, mappings=(), projected_rows=rows, visibility_rows=visibility, digest_key=key)
    evidence = build_health_shadow_evidence(selections=selections, **arguments)
    assert evidence.blocker_count == 0
    failed = build_health_shadow_evidence(selections=(replace(selections[0], winner_fact_id=1),), **arguments)
    assert failed.category_counts == {"HEALTH_WINNER_MISMATCH": 1}
