import sys
import types
import os
import subprocess
from pathlib import Path

import pytest

from app.tasks.celery_app import (
    PRIVATE_FILE_CLEANUP_TASK_NAME,
    PRIVATE_FILE_QUEUE,
    PRIVATE_FILE_RECOVER_TASK_NAME,
    PRIVATE_FILE_SCAN_TASK_NAME,
    celery_app,
    get_declared_queues,
)
from app.tasks import institution_onboarding_tasks as tasks


class CiDeterministicScanner:
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


def test_生产Worker通过显式Port工厂装配扫描器且缺失配置时fail_closed(monkeypatch):
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
