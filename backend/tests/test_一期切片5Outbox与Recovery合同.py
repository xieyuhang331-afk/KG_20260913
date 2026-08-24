from __future__ import annotations

import importlib
from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID

import pytest

from app.tasks import slice5_assessment_tasks as tasks
from app.modules.health_assessment.service import fail_assessment


celery_config = importlib.import_module("app.tasks.celery_app")


def test_slice5任务目录固定且全部路由到独立队列() -> None:
    expected = {
        "phase1.slice5.run_assessment",
        "phase1.slice5.dispatch_outbox",
        "phase1.slice5.consume_outbox",
        "phase1.slice5.recover_outbox",
    }
    assert tasks.TASK_NAMES == expected
    assert celery_config.SLICE5_ASSESSMENT_QUEUE in celery_config.get_declared_queues()
    app = celery_config.create_celery_app(broker_url="memory://")
    assert all(
        app.conf.task_routes[name] == {"queue": celery_config.SLICE5_ASSESSMENT_QUEUE}
        for name in expected
    )


def test_slice5恢复任务有固定节拍且禁止自动重放mutation() -> None:
    app = celery_config.create_celery_app(broker_url="memory://")
    recovery = app.conf.beat_schedule["slice5-assessment-recovery"]
    dispatch = app.conf.beat_schedule["slice5-assessment-dispatch"]
    assert recovery["task"] == tasks.RECOVER_TASK
    assert recovery["schedule"] == 60.0
    assert dispatch["task"] == tasks.DISPATCH_TASK
    source = tasks.__loader__.get_source(tasks.__name__)
    assert "pytest" not in source
    assert "autoretry_for" not in source
    assert "dispose_slice5_runtime" in source


def test_MQ02_MQ04_MQ05_Worker仅经受限函数且Delivery身份稳定() -> None:
    source = tasks.__loader__.get_source(tasks.__name__)
    repository_source = importlib.import_module(
        "app.modules.health_assessment.repository"
    ).__loader__.get_source("app.modules.health_assessment.repository")
    assert "INSERT INTO public.slice5_delivery" not in source
    assert "UPDATE public.slice5_outbox" not in source
    for function_name in (
        "slice5_outbox_claim_v1",
        "slice5_outbox_consume_v1",
        "slice5_outbox_recover_v1",
        "slice5_outbox_reopen_v1",
    ):
        assert function_name in repository_source
    event_id = UUID("018f62f6-52c7-7a51-8f75-0c4fe6b15279")
    assert tasks._delivery_id(event_id) == tasks._delivery_id(event_id)
    assert tasks._delivery_id(event_id).version == 7


def test_MQ01_评估请求事件精确路由到评估Worker() -> None:
    service_source = importlib.import_module(
        "app.modules.health_assessment.service"
    ).__loader__.get_source("app.modules.health_assessment.service")
    task_source = tasks.__loader__.get_source(tasks.__name__)
    assert '"event_type": "ASSESSMENT_RUN_REQUESTED"' in service_source
    assert 'result.get("event_type") == "ASSESSMENT_RUN_REQUESTED"' in task_source
    assert "run_assessment.apply_async" in task_source


class _Secrets:
    def digest(self, purpose, value):
        return sha256((purpose + repr(value)).encode()).digest(), "slice5-test-k1"


class _Session:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


class _Repository:
    def __init__(self):
        self.session = _Session()
        self.payload = None

    async def fail_assessment(self, payload):
        self.payload = payload
        return payload["response"]


@pytest.mark.asyncio
async def test_MQ03_确定性评估失败原子写入安全后像且不伪造完成() -> None:
    repository = _Repository()
    assessment_id = UUID("018f62f6-52c7-7a51-8f75-0c4fe6b15270")
    result = await fail_assessment(
        repository,
        assessment_id=assessment_id,
        expected_version=2,
        failure_code="RULE_EVALUATION_UNAVAILABLE",
        failed_at=datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc),
        secrets=_Secrets(),
    )
    assert result == {
        "assessment_id": assessment_id,
        "status": "FAILED",
        "failure_code": "RULE_EVALUATION_UNAVAILABLE",
        "version": 3,
    }
    assert repository.payload["postimage_digest"]
    assert repository.payload["audit_digest"]
    assert repository.payload["outbox_digest"]
    assert repository.session.commits == 1


@pytest.mark.asyncio
async def test_MQ03_未知失败类别固定拒绝且零写入() -> None:
    repository = _Repository()
    with pytest.raises(RuntimeError, match="INVALID_REQUEST"):
        await fail_assessment(
            repository,
            assessment_id=UUID("018f62f6-52c7-7a51-8f75-0c4fe6b15271"),
            expected_version=2,
            failure_code="RAW_DATABASE_ERROR",
            failed_at=datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc),
            secrets=_Secrets(),
        )
    assert repository.payload is None
