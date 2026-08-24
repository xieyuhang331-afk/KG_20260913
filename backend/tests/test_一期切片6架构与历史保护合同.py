from __future__ import annotations

from pathlib import Path

from app.tasks.celery_app import SLICE6_HEALTH_PLAN_QUEUE, get_declared_queues


ROOT = Path(__file__).parents[1]


def test_Slice6生产模块不依赖LLM诊断处方定价支付或Slice7() -> None:
    module = ROOT / "app/modules/health_plan"
    tasks = ROOT / "app/tasks/slice6_health_plan_tasks.py"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (*module.glob("*.py"), tasks)
    ).lower()
    for forbidden in (
        "openai",
        "anthropic",
        "langchain",
        "llm",
        "diagnosis",
        "prescription",
        "medication_adjustment",
        "pricing",
        "payment",
        "slice7",
    ):
        assert forbidden not in source


def test_Slice6独立队列路由和恢复节拍固定() -> None:
    assert SLICE6_HEALTH_PLAN_QUEUE == "slice6-health-plan-workflow"
    assert SLICE6_HEALTH_PLAN_QUEUE in get_declared_queues()
    source = (ROOT / "app/tasks/celery_app.py").read_text(encoding="utf-8")
    for task in (
        "phase1.slice6.generate_plan",
        "phase1.slice6.dispatch_outbox",
        "phase1.slice6.consume_outbox",
        "phase1.slice6.recover_outbox",
    ):
        assert task in source
    assert '"slice6-health-plan-recovery"' in source
    assert '"schedule": 60.0' in source


def test_Slice6不修改0030及更早Migration() -> None:
    migration = ROOT / "app/migrations/versions/20260826_0031_phase1_slice6_health_plan_generation_review.py"
    source = migration.read_text(encoding="utf-8")
    assert 'down_revision = "20260825_0030"' in source
    assert "ALTER TABLE public.service_case" not in source
    assert "ALTER TABLE public.health_assessment" not in source
