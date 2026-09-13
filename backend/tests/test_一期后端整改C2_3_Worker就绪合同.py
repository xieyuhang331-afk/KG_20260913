from __future__ import annotations

import asyncio
import inspect
import json
import multiprocessing
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from app.tasks.readiness import (
    WORKER_SPECS,
    _validate_request,
    worker_readiness,
)
from scripts import check_worker_readiness


def _blocking_connection_probe(**_: object) -> dict[str, str]:
    time.sleep(5)
    return {"status": "READY", "worker_kind": "slice7"}


def _blocking_publish_probe(**_: object) -> dict[str, str]:
    time.sleep(5)
    return {"status": "READY", "worker_kind": "slice7"}


def _noisy_failing_probe(**_: object) -> dict[str, str]:
    secret = "SYNTHETIC_C23_SECRET_MUST_NOT_LEAK"
    print(secret)
    print(secret, file=sys.stderr)
    raise RuntimeError(secret)


class _ProbePipeEnd:
    def __init__(self, clock: list[float], *, cancel_on_poll: bool = False) -> None:
        self._clock = clock
        self._cancel_on_poll = cancel_on_poll
        self.closed = False

    def poll(self, timeout: float) -> bool:
        if self._cancel_on_poll:
            raise asyncio.CancelledError
        self._clock[0] += timeout
        return False

    def recv(self) -> None:
        raise AssertionError("C23_FAKE_PIPE_RECV_UNEXPECTED")

    def close(self) -> None:
        self.closed = True


class _DelayedReapProcess:
    def __init__(
        self,
        clock: list[float],
        *,
        terminate_reaps: bool = False,
        never_reaps: bool = False,
    ) -> None:
        self._clock = clock
        self._terminate_reaps = terminate_reaps
        self._never_reaps = never_reaps
        self._alive = False
        self._killed = False
        self._post_kill_joins = 0
        self.name = "c23-worker-readiness-probe"
        self.daemon = True
        self.exitcode: int | None = None
        self.calls: list[str] = []

    def start(self) -> None:
        self.calls.append("start")
        self._alive = True

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        self.calls.append("join")
        assert timeout is not None
        if self._killed:
            self._post_kill_joins += 1
            elapsed = min(timeout, 0.01 if self._post_kill_joins == 1 else timeout)
            self._clock[0] += elapsed
            if not self._never_reaps and self._post_kill_joins >= 2:
                self._alive = False
                self.exitcode = -9
            return
        self._clock[0] += timeout
        if self._terminate_reaps and "terminate" in self.calls:
            self._alive = False
            self.exitcode = -15

    def terminate(self) -> None:
        self.calls.append("terminate")

    def kill(self) -> None:
        self.calls.append("kill")
        self._killed = True

    def close(self) -> None:
        assert not self._alive, "C23_PROCESS_CLOSED_BEFORE_REAP"
        self.calls.append("close")


class _ProbeContext:
    def __init__(
        self,
        process: _DelayedReapProcess,
        receiver: _ProbePipeEnd,
        sender: _ProbePipeEnd,
    ) -> None:
        self.process = process
        self.receiver = receiver
        self.sender = sender

    def Pipe(self, *, duplex: bool):
        assert duplex is False
        return self.receiver, self.sender

    def Process(self, **kwargs):
        assert kwargs["name"] == self.process.name
        assert kwargs["daemon"] is True
        return self.process


def _install_probe_context(
    monkeypatch,
    *,
    terminate_reaps: bool = False,
    never_reaps: bool = False,
    cancel_on_poll: bool = False,
) -> _DelayedReapProcess:
    clock = [100.0]
    process = _DelayedReapProcess(
        clock,
        terminate_reaps=terminate_reaps,
        never_reaps=never_reaps,
    )
    receiver = _ProbePipeEnd(clock, cancel_on_poll=cancel_on_poll)
    sender = _ProbePipeEnd(clock)
    context = _ProbeContext(process, receiver, sender)
    monkeypatch.setattr(check_worker_readiness, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        check_worker_readiness.multiprocessing,
        "get_context",
        lambda method: context if method == "spawn" else None,
    )
    return process


def test_C2_3_R06_Worker规格闭合且绑定真实队列与必需任务() -> None:
    assert set(WORKER_SPECS) == {
        "registration",
        "private_file",
        "therapist",
        "member",
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
    member = WORKER_SPECS["member"]
    assert member.queue == "member-enrollment-workflow"
    assert member.required_tasks == frozenset(
        {
            "phase1.member_enrollment.dispatch_outbox",
            "phase1.member_enrollment.consume_outbox",
            "phase1.member_enrollment.recover_outbox",
            "phase1.member_enrollment.expire_invitations",
        }
    )
    assert all(spec.queue and spec.required_tasks for spec in WORKER_SPECS.values())
    assert set(check_worker_readiness._WORKER_KINDS) == set(WORKER_SPECS)


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


@pytest.mark.asyncio
async def test_C2_3_R06_Slice5证明主Worker与跨Slice身份依赖后才READY(
    monkeypatch,
) -> None:
    import app.tasks.readiness as module

    checks: list[tuple[object, object]] = []
    disposed: list[str] = []
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(
            slice5_workflow_worker_role="slice5_worker",
            slice4_identity_authority_role="slice4_identity",
        ),
    )

    async def slice5_factory(_: str) -> str:
        return "slice5_factory"

    async def slice4_factory(_: str) -> str:
        return "slice4_identity_factory"

    async def database_ready(factory: object, role: object) -> bool:
        checks.append((factory, role))
        return True

    async def dispose_slice5(_: str) -> None:
        disposed.append("slice5")

    async def dispose_slice4(_: str) -> None:
        disposed.append("slice4")

    monkeypatch.setattr(module, "get_slice5_session_factory", slice5_factory)
    monkeypatch.setattr(module, "get_slice4_session_factory", slice4_factory)
    monkeypatch.setattr(module, "_database_ready", database_ready)
    monkeypatch.setattr(module, "dispose_slice5_runtime", dispose_slice5)
    monkeypatch.setattr(module, "dispose_slice4_runtime", dispose_slice4)

    assert await module._run_worker_check("slice5") is True
    assert checks == [
        ("slice5_factory", "slice5_worker"),
        ("slice4_identity_factory", "slice4_identity"),
    ]
    assert disposed == ["slice5", "slice4"]


@pytest.mark.asyncio
async def test_C2_3_R06_Member队列证明工作流与邀请过期双角色后才READY(
    monkeypatch,
) -> None:
    import app.tasks.readiness as module

    checks: list[tuple[object, object]] = []
    disposed: list[str] = []
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(
            member_workflow_worker_role="member_workflow_worker",
            member_enrollment_writer_role="member_enrollment_writer",
        ),
    )

    async def slice3_factory(kind: str) -> str:
        return f"slice3_{kind}_factory"

    async def database_ready(factory: object, role: object) -> bool:
        checks.append((factory, role))
        return True

    async def dispose_slice3(kind: str) -> None:
        disposed.append(kind)

    monkeypatch.setattr(module, "get_slice3_session_factory", slice3_factory)
    monkeypatch.setattr(module, "_database_ready", database_ready)
    monkeypatch.setattr(module, "dispose_slice3_runtime", dispose_slice3)

    assert await module._run_worker_check("member") is True
    assert checks == [
        ("slice3_workflow_worker_factory", "member_workflow_worker"),
        ("slice3_enrollment_writer_factory", "member_enrollment_writer"),
    ]
    assert disposed == ["workflow_worker", "enrollment_writer"]


@pytest.mark.asyncio
async def test_C2_3_R06_多身份Worker清理逐项尽力且取消优先() -> None:
    import app.tasks.readiness as module

    called: list[str] = []

    async def ordinary_failure() -> None:
        called.append("ordinary")
        raise RuntimeError("safe-cleanup-failure")

    async def cancellation() -> None:
        called.append("cancel")
        raise asyncio.CancelledError

    async def last_cleanup() -> None:
        called.append("last")

    with pytest.raises(asyncio.CancelledError):
        await module._dispose_all(
            (ordinary_failure, cancellation, last_cleanup)
        )
    assert called == ["ordinary", "cancel", "last"]


def test_C2_3_R06_Slice4五个投影角色配置使用闭合环境映射() -> None:
    from app.core import config

    settings = config.Settings(
        database_password="synthetic-database-secret",
        jwt_secret_key="synthetic-jwt-secret-at-least-thirty-two-bytes",
    )
    for field in (
        "health_projection_builder_role",
        "projection_confirmation_role",
        "health_projection_shadow_role",
        "projection_ready_gate_role",
        "projection_shadow_confirmation_role",
    ):
        assert getattr(settings, field) is None

    source = inspect.getsource(config.get_settings)
    assert 'health_projection_builder_role=os.getenv("KG_HEALTH_PROJECTION_BUILDER_ROLE")' in source
    assert 'projection_confirmation_role=os.getenv("KG_PROJECTION_CONFIRMATION_ROLE")' in source
    assert 'health_projection_shadow_role=os.getenv("KG_HEALTH_PROJECTION_SHADOW_ROLE")' in source
    assert 'projection_ready_gate_role=os.getenv("KG_PROJECTION_READY_GATE_ROLE")' in source
    assert "projection_shadow_confirmation_role=os.getenv(" in source
    assert '"KG_PROJECTION_SHADOW_CONFIRMATION_ROLE"' in source


@pytest.mark.asyncio
async def test_C2_3_R06_Slice4证明两个工作流与五个投影身份后才READY(
    monkeypatch,
) -> None:
    import app.tasks.readiness as module

    checks: list[tuple[object, object]] = []
    disposed: list[str] = []
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(
            slice4_workflow_worker_role="slice4_workflow",
            assessment_readiness_writer_role="assessment_readiness",
            health_projection_builder_role="health_builder",
            projection_confirmation_role="projection_confirmation",
            health_projection_shadow_role="health_shadow",
            projection_ready_gate_role="ready_gate",
            projection_shadow_confirmation_role="shadow_confirmation",
        ),
    )

    async def slice4_factory(kind: str) -> str:
        return f"slice4_{kind}_factory"

    async def projection_factory(kind: str) -> str:
        return f"projection_{kind}_factory"

    async def database_ready(factory: object, role: object) -> bool:
        checks.append((factory, role))
        return True

    async def dispose_slice4(kind: str) -> None:
        disposed.append(f"slice4:{kind}")

    async def dispose_projection(kind: str) -> None:
        disposed.append(f"projection:{kind}")

    monkeypatch.setattr(module, "get_slice4_session_factory", slice4_factory)
    monkeypatch.setattr(module, "get_projection_session_factory", projection_factory)
    monkeypatch.setattr(module, "_database_ready", database_ready)
    monkeypatch.setattr(module, "dispose_slice4_runtime", dispose_slice4)
    monkeypatch.setattr(module, "dispose_projection_runtime", dispose_projection)

    assert await module._run_worker_check("slice4") is True
    assert checks == [
        ("slice4_workflow_worker_factory", "slice4_workflow"),
        ("slice4_assessment_readiness_writer_factory", "assessment_readiness"),
        ("projection_health_factory", "health_builder"),
        ("projection_confirmation_factory", "projection_confirmation"),
        ("projection_health_shadow_factory", "health_shadow"),
        ("projection_ready_gate_factory", "ready_gate"),
        ("projection_shadow_confirmation_factory", "shadow_confirmation"),
    ]
    assert disposed == [
        "slice4:workflow_worker",
        "slice4:assessment_readiness_writer",
        "projection:health",
        "projection:confirmation",
        "projection:health_shadow",
        "projection:ready_gate",
        "projection:shadow_confirmation",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("failing_role", [None, "wrong_role"])
async def test_C2_3_R06_Slice4任一角色缺失或错配稳定NOT_READY(
    monkeypatch, failing_role,
) -> None:
    import app.tasks.readiness as module

    settings = SimpleNamespace(
        slice4_workflow_worker_role="slice4_workflow",
        assessment_readiness_writer_role="assessment_readiness",
        health_projection_builder_role="health_builder",
        projection_confirmation_role="projection_confirmation",
        health_projection_shadow_role="health_shadow",
        projection_ready_gate_role="ready_gate",
        projection_shadow_confirmation_role=failing_role,
    )
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        module,
        "get_slice4_session_factory",
        lambda kind: f"slice4_{kind}_factory",
    )
    monkeypatch.setattr(
        module,
        "get_projection_session_factory",
        lambda kind: f"projection_{kind}_factory",
    )

    async def database_ready(_: object, role: object) -> bool:
        return role not in {None, "wrong_role"}

    async def dispose(_: str) -> None:
        return None

    monkeypatch.setattr(module, "_database_ready", database_ready)
    monkeypatch.setattr(module, "dispose_slice4_runtime", dispose)
    monkeypatch.setattr(module, "dispose_projection_runtime", dispose)

    assert await module._run_worker_check("slice4") is False


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
        "_run_bounded_probe",
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
        "_run_bounded_probe",
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


@pytest.mark.parametrize(
    "probe",
    [_blocking_connection_probe, _blocking_publish_probe],
    ids=["broker-connect-blocked", "broker-publish-blocked"],
)
def test_C2_3_R08_CLI隔离阻塞Broker阶段并在墙钟预算内清理(probe) -> None:
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="WORKER_NOT_READY"):
        check_worker_readiness._run_bounded_probe(
            worker_kind="slice7",
            hostname="slice7@worker",
            probe=probe,
            timeout_seconds=0.3,
        )
    assert time.monotonic() - started < 1.5
    assert not any(
        child.name.startswith("c23-worker-readiness")
        for child in multiprocessing.active_children()
    )


def test_C2_3_R08_真实阻塞探针记录本次进程归属并完成回收(monkeypatch) -> None:
    real_context = multiprocessing.get_context("spawn")
    closed_facts: dict[str, object] = {}
    process_type = type(real_context.Process())
    real_close = process_type.close

    def recording_close(process) -> None:
        if process.name == "c23-worker-readiness-probe":
            closed_facts.update(
                pid=process.pid,
                name=process.name,
                exitcode=process.exitcode,
                alive=process.is_alive(),
            )
        real_close(process)

    before_pids = {child.pid for child in multiprocessing.active_children()}
    monkeypatch.setattr(process_type, "close", recording_close)

    with pytest.raises(RuntimeError, match="WORKER_NOT_READY"):
        check_worker_readiness._run_bounded_probe(
            worker_kind="slice7",
            hostname="slice7@worker",
            probe=_blocking_publish_probe,
            timeout_seconds=0.3,
        )

    assert closed_facts["name"] == "c23-worker-readiness-probe"
    assert closed_facts["pid"] not in before_pids
    assert closed_facts["alive"] is False
    assert closed_facts["exitcode"] is not None


def test_C2_3_R08_kill后短join提前返回仍在硬截止内继续回收(monkeypatch) -> None:
    process = _install_probe_context(monkeypatch)

    with pytest.raises(RuntimeError, match="WORKER_NOT_READY"):
        check_worker_readiness._run_bounded_probe(
            worker_kind="slice7",
            hostname="slice7@worker",
            timeout_seconds=0.3,
        )

    assert process.name == "c23-worker-readiness-probe"
    assert process.calls[:4] == ["start", "join", "terminate", "join"]
    assert process.calls.count("kill") == 1
    assert process.calls.count("join") >= 4
    assert process.calls[-1] == "close"
    assert process.is_alive() is False


def test_C2_3_R08_terminate可回收时不升级kill(monkeypatch) -> None:
    process = _install_probe_context(monkeypatch, terminate_reaps=True)

    with pytest.raises(RuntimeError, match="WORKER_NOT_READY"):
        check_worker_readiness._run_bounded_probe(
            worker_kind="slice7",
            hostname="slice7@worker",
            timeout_seconds=0.3,
        )

    assert "terminate" in process.calls
    assert "kill" not in process.calls
    assert process.calls[-1] == "close"


def test_C2_3_R08_取消在本次子进程完成回收后保持优先(monkeypatch) -> None:
    process = _install_probe_context(monkeypatch, cancel_on_poll=True)

    with pytest.raises(asyncio.CancelledError):
        check_worker_readiness._run_bounded_probe(
            worker_kind="slice7",
            hostname="slice7@worker",
            timeout_seconds=0.3,
        )

    assert process.is_alive() is False
    assert process.calls[-1] == "close"


def test_C2_3_R08_硬截止仍存活时失败且不伪称已回收(monkeypatch) -> None:
    process = _install_probe_context(monkeypatch, never_reaps=True)

    with pytest.raises(RuntimeError, match="WORKER_NOT_READY"):
        check_worker_readiness._run_bounded_probe(
            worker_kind="slice7",
            hostname="slice7@worker",
            timeout_seconds=0.3,
        )

    assert process.is_alive() is True
    assert "kill" in process.calls
    assert "close" not in process.calls


def test_C2_3_R08_CLI子进程输出被丢弃且进程资源已关闭(capfd) -> None:
    with pytest.raises(RuntimeError, match="WORKER_NOT_READY"):
        check_worker_readiness._run_bounded_probe(
            worker_kind="slice7",
            hostname="slice7@worker",
            probe=_noisy_failing_probe,
            timeout_seconds=1.0,
        )
    captured = capfd.readouterr()
    assert "SYNTHETIC_C23_SECRET_MUST_NOT_LEAK" not in captured.out + captured.err
    assert not any(
        child.name.startswith("c23-worker-readiness")
        for child in multiprocessing.active_children()
    )
    source = inspect.getsource(check_worker_readiness._run_bounded_probe)
    assert "process.close()" in source


def test_C2_3_R08_CLI配置导入失败仍无traceback与原始配置() -> None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("KG_")
    }
    result = subprocess.run(
        [
            sys.executable,
            str(Path(check_worker_readiness.__file__).resolve()),
            "--worker-kind",
            "slice7",
            "--hostname",
            "slice7@worker",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=4,
        env=environment,
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 1
    assert "Traceback" not in combined
    assert json.loads(result.stdout) == {
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


def test_C2_3_R10_依赖合并后CI只保留正式Base触发() -> None:
    workflow_path = (
        Path(__file__).resolve().parents[2] / ".github/workflows/p2-foundation-ci.yml"
    )
    workflow = yaml.load(workflow_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

    assert set(workflow["on"]) == {"push", "pull_request", "workflow_dispatch"}
    assert workflow["on"]["push"]["branches"] == ["main", "develop"]
    assert workflow["on"]["pull_request"]["branches"] == ["main", "develop"]
    assert workflow["on"]["workflow_dispatch"] == ""
