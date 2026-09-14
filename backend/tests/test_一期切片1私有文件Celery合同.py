import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from app.tasks import institution_onboarding_tasks as tasks
from app.tasks.celery_app import (
    PRIVATE_FILE_CLEANUP_TASK_NAME,
    PRIVATE_FILE_QUEUE,
    PRIVATE_FILE_RECOVER_TASK_NAME,
    PRIVATE_FILE_SCAN_TASK_NAME,
    celery_app,
    get_declared_queues,
)


class CiDeterministicScanner:
    async def health(self) -> bool:
        return True

    async def scan(self, path, *, mime_type: str) -> str:
        assert os.getenv("KG_TEST_ENVIRONMENT") == "ci_ephemeral"
        assert mime_type in {"application/pdf", "image/jpeg", "image/png"}
        return "REJECTED" if b"CI-REJECT" in path.read_bytes() else "CLEAN"


def create_ci_scanner():
    if os.getenv("KG_TEST_ENVIRONMENT") != "ci_ephemeral":
        raise RuntimeError("CI_SCANNER_FORBIDDEN")
    return CiDeterministicScanner()


def test_私有文件扫描使用独立队列且JSON序列化():
    assert PRIVATE_FILE_QUEUE in get_declared_queues()
    assert celery_app.conf.task_routes[PRIVATE_FILE_SCAN_TASK_NAME] == {"queue": PRIVATE_FILE_QUEUE}
    assert celery_app.conf.task_routes[PRIVATE_FILE_RECOVER_TASK_NAME] == {"queue": PRIVATE_FILE_QUEUE}
    assert celery_app.conf.task_routes[PRIVATE_FILE_CLEANUP_TASK_NAME] == {"queue": PRIVATE_FILE_QUEUE}
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True


def test_生产Worker通过显式Port工厂装配扫描器且缺失配置时fail_closed(
    monkeypatch, tmp_path
):
    class Scanner:
        async def scan(self, path, *, mime_type):
            return "CLEAN"

    module = types.ModuleType("slice1_scanner_contract")
    module.factory = Scanner
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setenv("KG_PRIVATE_FILE_SCANNER_FACTORY", f"{module.__name__}:factory")
    tasks.reset_private_file_scanner_for_test()
    assert type(tasks._scanner_for_worker()) is Scanner
    monkeypatch.delenv("KG_PRIVATE_FILE_SCANNER_FACTORY")
    assert type(tasks._scanner_for_worker()).__name__ == "_UnavailableScanner"

    monkeypatch.setenv(
        "KG_PRIVATE_FILE_SCANNER_FACTORY",
        "app.modules.private_file.clamav_scanner:build_clamav_scanner",
    )
    monkeypatch.setenv("KG_PRIVATE_FILE_CLAMD_HOST", "127.0.0.1")
    monkeypatch.setenv("KG_PRIVATE_FILE_CLAMD_PORT", "3310")
    monkeypatch.setenv("KG_PRIVATE_FILE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("KG_PRIVATE_FILE_SCANNER_ENGINE_VERSION", "1.4.6")
    scanner = tasks._scanner_for_worker()
    assert type(scanner).__name__ == "ClamAVScanner"


def test_S1_真实factory与测试factory在CI合同中明确分离() -> None:
    workflow = (
        Path(__file__).resolve().parents[2] / ".github/workflows/p2-foundation-ci.yml"
    ).read_text(encoding="utf-8")
    s1_setup = workflow.split(
        "- name: Prepare digest-pinned S1 ClamAV scanner", maxsplit=1
    )[1].split("- name: Run real S1 ClamAV scanner contract", maxsplit=1)[0]
    assert (
        "KG_PRIVATE_FILE_SCANNER_FACTORY=app.modules.private_file.clamav_scanner:build_clamav_scanner"
        in workflow
    )
    assert "KG_RUN_S1_CLAMAV_INTEGRATION=1" in workflow
    assert "Run real S1 ClamAV scanner contract" in workflow
    assert "pytest-s1-clamav-report.xml" in workflow
    assert "--health-cmd \"printf 'zPING\\\\0'" in workflow
    assert "S1 scanner container is unhealthy." in workflow
    assert "--entrypoint /usr/bin/clamconf \"$image\" -n" in workflow
    assert "host_hash" in workflow and "container_hash" in workflow
    assert 'readonly"' in s1_setup
    assert "Start independent private-file Celery worker" in workflow
    assert "--worker-kind private_file" in workflow
    assert "pytest-private-file-worker-report.xml" in workflow
    assert "pytest-c23-private-file-readiness-report.xml" in workflow
    assert "KG_TEST_A3_REAL_RABBIT=1" in workflow
    assert "Private-file Celery worker remains after cleanup." in workflow
    assert "--ignore=tests/integration/test_S1私有文件ClamAV扫描适配器Fresh闭环.py" in workflow
    assert s1_setup.index('echo "KG_S1_CLAMD_SENTINEL=$sentinel"') < s1_setup.index(
        "docker network create"
    )
    assert (
        "KG_PRIVATE_FILE_SCANNER_FACTORY: tests.test_一期切片1私有文件Celery合同:create_ci_scanner"
        not in workflow
    )


def test_恢复任务与Outbox投递均注册为周期任务入口():
    source = open(tasks.__file__, encoding="utf-8").read()
    assert "dispatch_institution_outbox_task.s()" in source
    assert "private-file-scan-recovery" in celery_app.conf.beat_schedule
    assert "private-file-orphan-cleanup" in celery_app.conf.beat_schedule
    assert celery_app.conf.beat_schedule["private-file-scan-recovery"]["options"] == {"queue": PRIVATE_FILE_QUEUE}
    assert celery_app.conf.beat_schedule["private-file-orphan-cleanup"]["options"] == {"queue": PRIVATE_FILE_QUEUE}
    assert "@celery_app.on_after_finalize.connect" in source
    assert 'name="phase1.institution.approved"' in source
    assert "_consume_institution_approved_event" in source


def test_独立Worker冷启动完整注册Slice1外键目标模型():
    backend_root = Path(__file__).resolve().parents[1]
    probe = (
        "from app.tasks import institution_onboarding_tasks; "
        "from app.core.database import Base; "
        "[foreign_key.column for table in Base.metadata.tables.values() "
        "for foreign_key in table.foreign_keys]"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=backend_root,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0


def test_Worker任务在成功和失败后均由owner_loop释放数据库runtime(monkeypatch):
    disposed = []

    async def dispose(kind):
        disposed.append(kind)

    async def success():
        return "ok"

    async def failure():
        raise RuntimeError("fixed-test-error")

    monkeypatch.setattr(tasks, "dispose_slice1_runtime", dispose)
    assert tasks._run_worker_uow("file_writer", success) == "ok"
    with pytest.raises(RuntimeError, match="fixed-test-error"):
        tasks._run_worker_uow("review_writer", failure)
    assert disposed == ["file_writer", "review_writer"]
