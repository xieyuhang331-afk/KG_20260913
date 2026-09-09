from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.tasks.readiness import (
    WORKER_SPECS,
    _validate_request,
    worker_readiness,
)
from scripts import check_worker_readiness


def test_C2_3_R06_Worker规格闭合且绑定真实队列与必需任务() -> None:
    assert set(WORKER_SPECS) == {
        "registration",
        "private_file",
        "therapist",
        "slice4",
        "slice5",
        "slice6",
        "slice7",
    }
    private = WORKER_SPECS["private_file"]
    assert private.queue == "private-file"
    assert private.required_tasks == frozenset(
        {
            "phase1.private_file.scan",
            "phase1.private_file.recover_pending",
            "phase1.private_file.cleanup_orphans",
        }
    )
    assert all(spec.queue and spec.required_tasks for spec in WORKER_SPECS.values())


@pytest.mark.parametrize(
    ("kind", "nonce"),
    [
        ("unknown", "0" * 32),
        ("private_file", "short"),
        ("private_file", "g" * 32),
        (None, "0" * 32),
    ],
)
def test_C2_3_R08_inspect请求拒绝未知类型和非法nonce(kind, nonce) -> None:
    with pytest.raises(ValueError, match="WORKER_READINESS_REQUEST_INVALID"):
        _validate_request(kind, nonce)


def test_C2_3_R09_闭合inspect不接收任意SQL任务或JSON参数() -> None:
    signature = inspect.signature(worker_readiness)
    assert tuple(signature.parameters) == ("state", "worker_kind", "nonce")
    source = inspect.getsource(worker_readiness)
    assert "eval(" not in source
    assert "exec(" not in source
    assert "send_task" not in source
    assert "delay(" not in source


def test_C2_3_R06_错误队列Worker不得替代目标且不执行数据库检查(monkeypatch) -> None:
    import app.tasks.readiness as module

    called = False

    async def check(_: str) -> bool:
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(module, "_run_worker_check", check)
    spec = WORKER_SPECS["slice7"]
    state = SimpleNamespace(
        consumer=SimpleNamespace(
            hostname="wrong@worker",
            task_consumer=SimpleNamespace(queues={"slice5-assessment-workflow": object()}),
        ),
        app=SimpleNamespace(tasks={name: object() for name in spec.required_tasks}),
    )
    result = worker_readiness(state, "slice7", "b" * 32)
    assert result["status"] == "NOT_READY"
    assert called is False

    state.consumer.task_consumer.queues = {
        spec.queue: object(),
        "another-queue": object(),
    }
    result = worker_readiness(state, "slice7", "b" * 32)
    assert result["status"] == "NOT_READY"
    assert called is False


def test_C2_3_R06_Worker内部检查有固定子预算(monkeypatch) -> None:
    import app.tasks.readiness as module

    async def blocked(_: str) -> bool:
        await asyncio.Event().wait()
        return True

    monkeypatch.setattr(module, "_run_worker_check", blocked)
    spec = WORKER_SPECS["slice7"]
    state = SimpleNamespace(
        consumer=SimpleNamespace(
            hostname="slice7@worker",
            task_consumer=SimpleNamespace(queues={spec.queue: object()}),
        ),
        app=SimpleNamespace(tasks={name: object() for name in spec.required_tasks}),
    )
    result = worker_readiness(state, "slice7", "c" * 32)
    assert result == {"status": "NOT_READY", "code": "WORKER_NOT_READY"}


def test_C2_3_R07_private_file无scanner_health稳定NOT_READY(monkeypatch) -> None:
    import app.tasks.readiness as module

    monkeypatch.setattr(module, "_run_worker_check", lambda _: asyncio.sleep(0, result=False))
    state = SimpleNamespace(
        consumer=SimpleNamespace(
            hostname="private@worker",
            task_consumer=SimpleNamespace(queues={"private-file": object()}),
        ),
        app=SimpleNamespace(tasks={name: object() for name in WORKER_SPECS["private_file"].required_tasks}),
    )
    result = worker_readiness(state, "private_file", "a" * 32)
    assert result == {
        "status": "NOT_READY",
        "code": "WORKER_NOT_READY",
        "worker_kind": "private_file",
        "hostname": "private@worker",
        "nonce": "a" * 32,
    }


def test_C2_3_R08_CLI未知参数与失败输出不回显输入(monkeypatch, capsys) -> None:
    secret_like = "postgresql://name:credential@host/database"
    code = check_worker_readiness.main(
        ["--worker-kind", "private_file", "--hostname", "private@worker", secret_like]
    )
    captured = capsys.readouterr()
    assert code != 0
    assert secret_like not in captured.out + captured.err
    assert json.loads(captured.out) == {
        "status": "NOT_READY",
        "code": "WORKER_READINESS_ARGUMENT_INVALID",
    }


def test_C2_3_R06_CLI只接受精确目标且不使用全局ping(monkeypatch, capsys) -> None:
    source = inspect.getsource(check_worker_readiness.probe_worker)
    assert ".ping(" not in source
    assert "destination=[hostname]" in source
    monkeypatch.setattr(
        check_worker_readiness,
        "probe_worker",
        lambda **_: {"status": "READY", "worker_kind": "slice7"},
    )
    code = check_worker_readiness.main(
        ["--worker-kind", "slice7", "--hostname", "slice7@worker"]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert json.loads(captured.out) == {"status": "READY", "worker_kind": "slice7"}


def test_C2_3_R06_CLI闭合解析Celery_registered元数据格式() -> None:
    names = check_worker_readiness._registered_names(
        [
            "phase1.slice7.mark_overdue [exchange= routing_key=]",
            "phase1.slice7.generate_export",
        ]
    )
    assert names == {
        "phase1.slice7.mark_overdue",
        "phase1.slice7.generate_export",
    }
    with pytest.raises(RuntimeError, match="WORKER_NOT_READY"):
        check_worker_readiness._registered_names({"unexpected": "shape"})


def test_C2_3_R08_CLI运行失败只输出稳定匿名码(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        check_worker_readiness,
        "probe_worker",
        lambda **_: (_ for _ in ()).throw(
            RuntimeError("postgresql://name:credential@host/database")
        ),
    )
    assert (
        check_worker_readiness.main(
            ["--worker-kind", "slice7", "--hostname", "slice7@worker"]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert "credential" not in captured.out + captured.err
    assert json.loads(captured.out) == {
        "status": "NOT_READY",
        "code": "WORKER_NOT_READY",
    }


@pytest.mark.asyncio
async def test_C2_3_R07_private_file缺少显式scanner_health即失败(monkeypatch) -> None:
    import app.tasks.institution_onboarding_tasks as module

    monkeypatch.setattr(
        module,
        "_scanner_for_worker",
        lambda: SimpleNamespace(scan=lambda *_args, **_kwargs: None),
    )
    assert await module.check_private_file_worker_readiness() is False


def test_C2_3_R10_CI以精确目标运行产品探针并收集JUnit() -> None:
    workflow = (
        Path(__file__).resolve().parents[2] / ".github/workflows/p2-foundation-ci.yml"
    ).read_text(encoding="utf-8")
    assert "--ignore=tests/integration/test_一期后端整改C2_3_Worker就绪RabbitMQ闭环.py" in workflow
    assert workflow.count("python scripts/check_worker_readiness.py") == 2
    assert "--worker-kind slice7" in workflow
    assert "--worker-kind slice5" in workflow
    assert "pytest-c23-slice7-readiness-report.xml" in workflow
    assert "pytest-c23-slice5-readiness-report.xml" in workflow
    assert "backend/pytest-c23-slice7-readiness-report.xml" in workflow
    assert "backend/pytest-c23-slice5-readiness-report.xml" in workflow
