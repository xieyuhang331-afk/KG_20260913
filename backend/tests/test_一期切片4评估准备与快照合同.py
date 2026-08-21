import asyncio
from datetime import datetime, timezone
from uuid import UUID

import pytest


CASE = UUID("00000000-0000-7000-8000-000000000501")
ASSEMBLY = UUID("00000000-0000-7000-8000-000000000502")


def _inputs(**changes):
    from app.modules.assessment_readiness.domain import AssessmentReadinessInputs
    values = {"authorization_complete": True, "profile_current": True, "policy_available": True, "missing_indicator_codes": (), "expired_indicator_codes": (), "disputed_indicator_codes": (), "projection_current": True}
    values.update(changes)
    return AssessmentReadinessInputs(**values)


def test_D20_D22_优先级与所有fail_closed原因固定():
    from app.modules.assessment_readiness.domain import evaluate_readiness
    assert evaluate_readiness(_inputs()).status == "ASSESSMENT_READY"
    assert evaluate_readiness(_inputs(projection_current=False)).status == "DATA_SYNC_PENDING"
    assert evaluate_readiness(_inputs(projection_current=False, missing_indicator_codes=("weight",))).status == "DATA_INSUFFICIENT"
    result = evaluate_readiness(_inputs(projection_current=False, missing_indicator_codes=("weight",), disputed_indicator_codes=("hba1c",)))
    assert result.status == "DISPUTED"
    for changes in ({"authorization_complete": False}, {"profile_current": False}, {"policy_available": False}, {"expired_indicator_codes": ("hba1c",)}):
        assert evaluate_readiness(_inputs(**changes)).status == "DATA_INSUFFICIENT"


def test_D23_D24_D30_D35_assembly后像预先冻结且partial为UNKNOWN():
    from app.modules.assessment_readiness.service import build_assembly_mutation_plan, classify_assembly_confirmation
    kwargs = dict(assembly_id=ASSEMBLY, service_case_id=CASE, source_vector_digest="a" * 64, assembly_digest="b" * 64, status="ASSESSMENT_READY", generated_at=datetime(2026, 8, 20, 1, tzinfo=timezone.utc), preimage={"pointer_version": 0, "current_assembly_id": None})
    plan = build_assembly_mutation_plan(indicator_codes=("weight", "height", "weight"), **kwargs)
    replay = build_assembly_mutation_plan(indicator_codes=("height", "weight"), **kwargs)
    assert plan.expected_postimage == replay.expected_postimage
    assert classify_assembly_confirmation(plan, plan.expected_postimage) == "COMMITTED"
    assert classify_assembly_confirmation(plan, plan.preimage) == "NOT_COMMITTED"
    partial = dict(plan.expected_postimage); partial.pop("outbox")
    assert classify_assembly_confirmation(plan, partial) == "UNKNOWN"


def test_D25_D34_GET无pointer不写并返回重算等待且DTO无内部字段():
    from app.modules.assessment_readiness.schemas import AssessmentReadinessDTO
    from app.modules.assessment_readiness.service import read_current_readiness
    class Repo:
        writes = 0
        async def current_readiness(self, service_case_id): return None
    value = asyncio.run(read_current_readiness(Repo(), service_case_id=CASE))
    dto = AssessmentReadinessDTO.model_validate(value)
    assert dto.status == "DATA_SYNC_PENDING" and "RECOMPUTE_PENDING" in dto.reason_codes
    assert not ({"generation_id", "high_watermark", "source_vector_digest", "fact_id"} & set(AssessmentReadinessDTO.model_fields))


def test_D16_D27_ModuleE只依赖projection_read协议且禁止health_analysis():
    from pathlib import Path
    root = Path(__file__).parents[1] / "app" / "modules" / "assessment_readiness"
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    assert "projection_read.ports" in source
    assert "health_projection.models" not in source and "health_projection.repository" not in source and "health_analysis" not in source
