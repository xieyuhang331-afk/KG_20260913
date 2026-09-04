from __future__ import annotations

import asyncio
import importlib
import inspect
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.modules.private_file import domain, repository, service
from app.tasks import institution_onboarding_tasks as tasks

celery_module = importlib.import_module("app.tasks.celery_app")


def _source(value: object) -> str:
    return inspect.getsource(value)


def test_A3_R01_OBJECT_MISSING首次立即终态且不由Celery重试():
    source = _source(service.record_scan)
    assert "OBJECT_MISSING" in source, "A3_R01_OBJECT_MISSING_TERMINAL_MISSING"
    assert "except PrivateFileConflict" in source, "A3_R01_OBJECT_MISSING_BOUNDARY_MISSING"


def test_A3_R04_transient总尝试与退避由闭合常量冻结():
    assert domain.PRIVATE_FILE_SCAN_MAX_ATTEMPTS == 4
    assert domain.PRIVATE_FILE_SCAN_RETRY_DELAYS == (5, 30, 120)
    assert timedelta(minutes=5) == domain.PRIVATE_FILE_SCAN_LEASE


def test_A3_R05_EVIDENCE_MISMATCH首次终态且不调用scanner():
    source = _source(service.record_scan)
    mismatch = source.index("EVIDENCE_MISMATCH")
    scanner_call = source.index("scanner.scan")
    assert mismatch < scanner_call
    assert "SCAN_FAILED" in source[max(0, mismatch - 240):scanner_call]


def test_A3_R06_claim以数据库attempt_lease_version为唯一权威():
    source = _source(repository.PrivateFileRepository.claim_scan_attempt)
    for token in (
        "scan_attempt_count",
        "scan_lease_token",
        "scan_lease_until",
        "scan_version",
        "with_for_update",
    ):
        assert token in source, f"A3_R06_CLAIM_MISSING_{token.upper()}"


def test_A3_R09_commit_unknown必须完整前后像三态且不自动扫描():
    source = "\n".join(
        (
            _source(service._commit_scan_state),
            _source(service._confirm_scan_commit_outcome),
        )
    )
    for token in ("COMMITTED", "NOT_COMMITTED", "UNKNOWN"):
        assert token in source, f"A3_R09_{token}_MISSING"
    assert "scanner.scan" not in source


def test_A3_R13_cleanup永不选择或删除PENDING_SCAN():
    repository_source = _source(repository.PrivateFileRepository.expired_orphan_ids)
    service_source = _source(service.cleanup_orphan_private_file)
    assert "PENDING_SCAN" in repository_source
    assert "PENDING_SCAN" in service_source
    assert "status !=" in repository_source or "notin_" in repository_source


def test_A3_R14_cleanup统一使用单UUID引用authority():
    source = _source(repository.PrivateFileRepository.private_file_referenced)
    assert "a3_private_file_referenced_v1" in source
    assert "file_id" in source


def test_A3_R19三任务中央路由与beat全部固定private_file队列():
    routes = celery_module.celery_app.conf.task_routes
    assert routes[tasks.scan_private_file_task.name] == {
        "queue": celery_module.PRIVATE_FILE_QUEUE
    }
    assert routes[tasks.recover_pending_scan_tasks.name] == {
        "queue": celery_module.PRIVATE_FILE_QUEUE
    }
    assert routes[tasks.cleanup_orphan_private_files.name] == {
        "queue": celery_module.PRIVATE_FILE_QUEUE
    }
    schedule = celery_module.celery_app.conf.beat_schedule
    assert schedule["private-file-scan-recovery"]["options"] == {
        "queue": celery_module.PRIVATE_FILE_QUEUE
    }
    assert schedule["private-file-orphan-cleanup"]["options"] == {
        "queue": celery_module.PRIVATE_FILE_QUEUE
    }


def test_A3_Metadata只加安全字段且no_store():
    metadata_source = _source(service.metadata)
    api_module = __import__("app.modules.private_file.api", fromlist=["router"])
    for token in ("failure_code", "retryable", "next_poll_after_seconds"):
        assert token in metadata_source
    assert api_module._PRIVATE_HEADERS["Cache-Control"] == "no-store, private, max-age=0"


def test_A3_SCAN_FAILED一期不暴露重扫入口():
    api_source = inspect.getsource(
        __import__("app.modules.private_file.api", fromlist=["router"])
    )
    assert "rescan" not in api_source.lower()


def _scan_snapshot(*, version: int) -> dict[str, object]:
    return {
        "file_id": "00000000-0000-0000-0000-000000000001",
        "status": "PENDING_SCAN",
        "actual_size": 1,
        "actual_mime_type": "application/pdf",
        "actual_sha256": "0" * 64,
        "scanned_at": None,
        "deleted_at": None,
        "scan_attempt_count": 1,
        "scan_last_error_code": None,
        "scan_next_retry_at": None,
        "scan_lease_token": "00000000-0000-0000-0000-000000000002",
        "scan_lease_until": None,
        "scan_operation_ref_digest": "1" * 64,
        "scan_version": version,
    }


class _CommitFailureSession:
    bind = object()

    def __init__(self, *, cancelled: bool = False) -> None:
        self.cancelled = cancelled
        self.rollback_calls = 0
        self.close_calls = 0

    async def commit(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError
        raise RuntimeError("A3_SYNTHETIC_COMMIT_FAILURE")

    async def rollback(self) -> None:
        self.rollback_calls += 1
        raise RuntimeError("A3_SYNTHETIC_ROLLBACK_FAILURE")

    async def close(self) -> None:
        self.close_calls += 1
        raise RuntimeError("A3_SYNTHETIC_CLOSE_FAILURE")


class _ConfirmationSession:
    def __init__(self, persisted: dict[str, object]) -> None:
        self.persisted = persisted

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback


@pytest.mark.asyncio
async def test_A3_R09_commit失败后cleanup失败不跳过独立三态确认(monkeypatch):
    preimage = _scan_snapshot(version=1)
    postimage = _scan_snapshot(version=2)
    session = _CommitFailureSession()
    confirmed = {"called": False}

    class _Repo:
        def __init__(self, confirmation) -> None:
            confirmed["called"] = confirmation.persisted == postimage

        async def persistence_snapshot(self, file_id: str):
            del file_id
            return postimage

    monkeypatch.setattr(
        service,
        "AsyncSession",
        lambda **kwargs: _ConfirmationSession(postimage),
    )
    monkeypatch.setattr(service, "PrivateFileRepository", _Repo)

    outcome = await service._commit_scan_state(
        session,
        str(preimage["file_id"]),
        preimage=preimage,
        postimage=postimage,
    )

    assert outcome == "COMMITTED"
    assert confirmed == {"called": True}
    assert session.rollback_calls == 1
    assert session.close_calls == 1


@pytest.mark.asyncio
async def test_A3_R09取消优先于rollback与close失败():
    snapshot = _scan_snapshot(version=1)
    session = _CommitFailureSession(cancelled=True)
    with pytest.raises(asyncio.CancelledError):
        await service._commit_scan_state(
            session,
            str(snapshot["file_id"]),
            preimage=snapshot,
            postimage=_scan_snapshot(version=2),
        )
    assert session.rollback_calls == 1
    assert session.close_calls == 1


@pytest.mark.asyncio
async def test_A3_I1_commit取消在释放Writer后仍执行独立确认与隔离(monkeypatch):
    snapshot = _scan_snapshot(version=1)
    session = _CommitFailureSession(cancelled=True)
    calls: list[str] = []

    async def _confirm(bind, file_id, *, preimage, postimage):
        del bind, file_id, preimage, postimage
        assert session.close_calls == 1
        calls.append("confirm")
        return "UNKNOWN"

    async def _isolate(bind, file_id):
        del bind, file_id
        calls.append("isolate")
        return True

    monkeypatch.setattr(service, "_confirm_scan_commit_outcome", _confirm)
    monkeypatch.setattr(service, "_isolate_scan_commit_unknown", _isolate)

    with pytest.raises(asyncio.CancelledError):
        await service._commit_scan_state(
            session,
            str(snapshot["file_id"]),
            preimage=snapshot,
            postimage=_scan_snapshot(version=2),
        )

    assert calls == ["confirm", "isolate"]
    assert session.rollback_calls == 1
    assert session.close_calls == 1


@pytest.mark.asyncio
async def test_A3_I1普通commit失败时cleanup取消不得跳过独立确认(monkeypatch):
    preimage = _scan_snapshot(version=1)
    postimage = _scan_snapshot(version=2)
    session = _CommitFailureSession()
    calls: list[str] = []

    async def _cancelled_cleanup(writer):
        assert writer is session
        calls.append("cleanup")
        raise asyncio.CancelledError

    async def _confirm(bind, file_id, *, preimage, postimage):
        del bind, file_id, preimage, postimage
        calls.append("confirm")
        return "COMMITTED"

    monkeypatch.setattr(service, "_safe_rollback", _cancelled_cleanup)
    monkeypatch.setattr(service, "_confirm_scan_commit_outcome", _confirm)

    with pytest.raises(asyncio.CancelledError):
        await service._commit_scan_state(
            session,
            str(preimage["file_id"]),
            preimage=preimage,
            postimage=postimage,
        )

    assert calls == ["cleanup", "confirm"]


@pytest.mark.asyncio
async def test_A3_I1_commit取消且独立确认取消时必须先UNKNOWN隔离(monkeypatch):
    preimage = _scan_snapshot(version=1)
    postimage = _scan_snapshot(version=2)
    session = _CommitFailureSession(cancelled=True)
    calls: list[str] = []

    async def _confirm(bind, file_id, *, preimage, postimage):
        del bind, file_id, preimage, postimage
        calls.append("confirm")
        raise asyncio.CancelledError

    async def _isolate(bind, file_id):
        del bind, file_id
        calls.append("isolate")
        return True

    monkeypatch.setattr(service, "_confirm_scan_commit_outcome", _confirm)
    monkeypatch.setattr(service, "_isolate_scan_commit_unknown", _isolate)

    with pytest.raises(asyncio.CancelledError):
        await service._commit_scan_state(
            session,
            str(preimage["file_id"]),
            preimage=preimage,
            postimage=postimage,
        )

    assert calls == ["confirm", "isolate"]
    assert session.rollback_calls == 1
    assert session.close_calls == 1


class _ScalarRows:
    def __init__(self, *rows) -> None:
        self.rows = rows

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None

    def scalars(self):
        return self.rows


class _PrivateFileRowsSession:
    def __init__(self, *rows) -> None:
        self.rows = rows
        self.flush_calls = 0

    async def execute(self, statement):
        del statement
        return _ScalarRows(*self.rows)

    async def flush(self) -> None:
        self.flush_calls += 1


@pytest.mark.asyncio
async def test_A3_I1扫描领取必须先持久UNKNOWN围栏且拒绝重复领取():
    now = datetime.now(UTC)
    row = SimpleNamespace(
        status="PENDING_SCAN",
        scan_attempt_count=0,
        scan_last_error_code=None,
        scan_next_retry_at=None,
        scan_lease_token=None,
        scan_lease_until=None,
        scan_operation_ref_digest=None,
        scan_version=1,
    )
    session = _PrivateFileRowsSession(row)
    repo = repository.PrivateFileRepository(session)

    claimed = await repo.claim_scan_attempt(
        "00000000-0000-0000-0000-000000000001",
        lease_token="00000000-0000-0000-0000-000000000002",
        operation_ref_digest="1" * 64,
        now=now,
        lease_until=now + timedelta(minutes=5),
    )

    assert claimed is row
    assert row.scan_attempt_count == 1
    assert row.scan_last_error_code == "COMMIT_OUTCOME_UNKNOWN"

    repeated = await repo.claim_scan_attempt(
        "00000000-0000-0000-0000-000000000001",
        lease_token="00000000-0000-0000-0000-000000000003",
        operation_ref_digest="2" * 64,
        now=now + timedelta(minutes=6),
        lease_until=now + timedelta(minutes=11),
    )
    assert repeated is None


@pytest.mark.asyncio
async def test_A3_I1_UNKNOWN围栏记录不得被recover重新投递():
    now = datetime.now(UTC)
    row = SimpleNamespace(
        file_id="00000000-0000-0000-0000-000000000001",
        status="PENDING_SCAN",
        scan_attempt_count=1,
        scan_last_error_code="COMMIT_OUTCOME_UNKNOWN",
        scan_next_retry_at=None,
        scan_lease_token="00000000-0000-0000-0000-000000000002",
        scan_lease_until=now - timedelta(seconds=1),
        scan_version=2,
    )
    session = _PrivateFileRowsSession(row)

    recovered = await repository.PrivateFileRepository(
        session
    ).recover_pending_scan_ids(now=now)

    assert recovered == ()
    assert row.status == "SCAN_FAILED"
    assert row.scan_last_error_code == "COMMIT_OUTCOME_UNKNOWN"
    assert row.scan_next_retry_at is None
    assert row.scan_lease_token is None
    assert row.scan_lease_until is None
    assert row.scan_operation_ref_digest is None
    assert row.scanned_at == now
    assert row.scan_version == 3
    assert session.flush_calls == 1


@pytest.mark.asyncio
async def test_A3_R06过期租约recover不得重复累加已计入的attempt():
    now = datetime.now(UTC)
    row = SimpleNamespace(
        file_id="00000000-0000-0000-0000-000000000001",
        status="PENDING_SCAN",
        scan_attempt_count=1,
        scan_last_error_code=None,
        scan_next_retry_at=None,
        scan_lease_token="00000000-0000-0000-0000-000000000002",
        scan_lease_until=now - timedelta(seconds=1),
        scan_operation_ref_digest="1" * 64,
        scan_version=2,
    )
    session = _PrivateFileRowsSession(row)

    recovered = await repository.PrivateFileRepository(
        session
    ).recover_pending_scan_ids(now=now)

    assert recovered == (row.file_id,)
    assert row.scan_attempt_count == 1
    assert row.scan_lease_token is None
    assert row.scan_lease_until is None
    assert row.scan_version == 3


@pytest.mark.asyncio
async def test_A3_I1_claim已确认提交后必须CAS清除UNKNOWN围栏(monkeypatch):
    preimage = _scan_snapshot(version=2)
    preimage["scan_last_error_code"] = "COMMIT_OUTCOME_UNKNOWN"
    postimage = dict(preimage)
    postimage["scan_last_error_code"] = None
    postimage["scan_version"] = 3
    row = SimpleNamespace(
        scan_last_error_code="COMMIT_OUTCOME_UNKNOWN",
        scan_version=2,
    )
    snapshots = iter((preimage, postimage))

    class _Repo:
        def __init__(self, session) -> None:
            del session

        async def persistence_snapshot(self, file_id):
            del file_id
            return next(snapshots)

        async def get_claimed_scan(self, file_id, *, lease_token, expected_version):
            del file_id, lease_token
            assert expected_version == 2
            return row

    async def _commit(session, file_id, *, preimage, postimage):
        del session, file_id
        assert preimage["scan_last_error_code"] == "COMMIT_OUTCOME_UNKNOWN"
        assert postimage["scan_last_error_code"] is None
        return "COMMITTED"

    monkeypatch.setattr(service, "PrivateFileRepository", _Repo)
    monkeypatch.setattr(service, "_commit_scan_state", _commit)

    version = await service._acknowledge_scan_claim(
        _PrivateFileRowsSession(),
        {
            "file_id": str(preimage["file_id"]),
            "lease_token": "00000000-0000-0000-0000-000000000002",
            "scan_version": 2,
        },
    )

    assert version == 3
    assert row.scan_last_error_code is None
    assert row.scan_version == 3


@pytest.mark.asyncio
async def test_A3_I1_claim_ACK未确认时scanner必须零调用(monkeypatch):
    preimage = _scan_snapshot(version=1)
    postimage = _scan_snapshot(version=2)
    postimage["scan_last_error_code"] = "COMMIT_OUTCOME_UNKNOWN"
    row = SimpleNamespace(
        object_key="slice1/a3/test",
        actual_size=1,
        actual_mime_type="application/pdf",
        actual_sha256="0" * 64,
        scan_attempt_count=0,
        scan_version=2,
    )
    snapshots = iter((preimage, postimage))

    class _Repo:
        def __init__(self, session) -> None:
            del session

        async def persistence_snapshot(self, file_id):
            del file_id
            return next(snapshots)

        async def claim_scan_attempt(self, *args, **kwargs):
            del args, kwargs
            return row

    async def _commit(*args, **kwargs):
        del args, kwargs
        return "COMMITTED"

    async def _ack(*args, **kwargs):
        del args, kwargs
        return None

    scanner_calls = {"count": 0}

    class _Scanner:
        async def scan(self, *args, **kwargs):
            del args, kwargs
            scanner_calls["count"] += 1
            return "CLEAN"

    class _Store:
        async def stat(self, object_key):
            raise AssertionError(object_key)

        async def materialize_for_scan(self, object_key):
            raise AssertionError(object_key)

    monkeypatch.setattr(service, "PrivateFileRepository", _Repo)
    monkeypatch.setattr(service, "_commit_scan_state", _commit)
    monkeypatch.setattr(service, "_acknowledge_scan_claim", _ack, raising=False)
    monkeypatch.setattr(
        service,
        "_path",
        lambda value: (_ for _ in ()).throw(AssertionError(value)),
    )

    with pytest.raises(service.HTTPException) as exc:
        await service.record_scan(
            object(),
            str(preimage["file_id"]),
            scanner=_Scanner(),
            object_store=_Store(),
        )

    assert exc.value.status_code == 503
    assert exc.value.detail == "PRIVATE_FILE_SCAN_STATE_UNKNOWN"
    assert scanner_calls == {"count": 0}


def test_A3_I1_claim_ACK必须早于文件读取与scanner():
    source = _source(service.record_scan)
    assert source.index("_acknowledge_scan_claim") < source.index("materialize_for_scan")
    assert source.index("_acknowledge_scan_claim") < source.index("scanner.scan")


@pytest.mark.asyncio
async def test_A3_I1_finalize业务变更前必须CAS持久UNKNOWN围栏(monkeypatch):
    preimage = _scan_snapshot(version=3)
    preimage["scan_last_error_code"] = None
    postimage = dict(preimage)
    postimage["scan_last_error_code"] = "COMMIT_OUTCOME_UNKNOWN"
    postimage["scan_version"] = 4
    row = SimpleNamespace(scan_last_error_code=None, scan_version=3)
    snapshots = iter((preimage, postimage))

    class _Repo:
        def __init__(self, session) -> None:
            del session

        async def persistence_snapshot(self, file_id):
            del file_id
            return next(snapshots)

        async def get_claimed_scan(self, file_id, *, lease_token, expected_version):
            del file_id, lease_token
            assert expected_version == 3
            return row

    async def _commit(session, file_id, *, preimage, postimage):
        del session, file_id
        assert preimage["scan_last_error_code"] is None
        assert postimage["scan_last_error_code"] == "COMMIT_OUTCOME_UNKNOWN"
        return "COMMITTED"

    monkeypatch.setattr(service, "PrivateFileRepository", _Repo)
    monkeypatch.setattr(service, "_commit_scan_state", _commit)

    version = await service._guard_scan_finalize(
        _PrivateFileRowsSession(),
        {
            "file_id": str(preimage["file_id"]),
            "lease_token": "00000000-0000-0000-0000-000000000002",
            "scan_version": 3,
        },
    )

    assert version == 4
    assert row.scan_last_error_code == "COMMIT_OUTCOME_UNKNOWN"
    assert row.scan_version == 4


def test_A3_I1_finalize_UNKNOWN围栏必须早于业务终态变更():
    source = _source(service._finalize_scan_attempt)
    assert source.index("_guard_scan_finalize") < source.index("row.status = status")


@pytest.mark.asyncio
async def test_A3_I1_commit未知只隔离一次且不宣称提交成功(monkeypatch):
    preimage = _scan_snapshot(version=1)
    postimage = _scan_snapshot(version=2)
    session = _CommitFailureSession()
    isolated: list[str] = []

    async def _confirm(bind, file_id, *, preimage, postimage):
        del bind, file_id, preimage, postimage
        return "UNKNOWN"

    async def _isolate(bind, file_id):
        del bind
        isolated.append(file_id)
        return True

    monkeypatch.setattr(service, "_confirm_scan_commit_outcome", _confirm)
    monkeypatch.setattr(service, "_isolate_scan_commit_unknown", _isolate)

    assert await service._commit_scan_state(
        session,
        str(preimage["file_id"]),
        preimage=preimage,
        postimage=postimage,
    ) == "UNKNOWN"
    assert isolated == [str(preimage["file_id"])]


def test_A3_I1_commit未知必须隔离为既有SCAN_FAILED且recover不自动重放():
    commit_source = "\n".join(
        (
            _source(service._commit_scan_state),
            _source(service._isolate_scan_commit_unknown),
            _source(repository.PrivateFileRepository.isolate_scan_commit_unknown),
        )
    )
    recovery_source = _source(repository.PrivateFileRepository.recover_pending_scan_ids)
    assert "_isolate_scan_commit_unknown" in commit_source
    assert "COMMIT_OUTCOME_UNKNOWN" in commit_source
    assert "SCAN_FAILED" in commit_source
    assert "PENDING_SCAN" in recovery_source
    assert "COMMIT_OUTCOME_UNKNOWN" in recovery_source


def test_A3_I3_ORM状态约束冻结attempt_lease_retry与终态时间():
    constraint = next(
        item
        for item in service.PrivateFileModel.__table__.constraints
        if getattr(item, "name", None) == "ck_private_file_scan_state"
    )
    sql = str(constraint.sqltext)
    for token in (
        "scan_lease_token IS NOT NULL AND scan_attempt_count BETWEEN 1 AND 4",
        "scan_lease_token IS NULL AND scan_attempt_count<4",
        "scan_lease_until IS NULL",
        "scanned_at IS NOT NULL",
        "COMMIT_OUTCOME_UNKNOWN",
        "scan_operation_ref_digest IS NULL",
    ):
        assert token in sql, f"A3_I3_ORM_CONSTRAINT_MISSING_{token}"


def test_A3_I4_owner删除原子清除调度租约operation并递增version():
    source = _source(service.delete_temporary)
    for token in (
        "scan_next_retry_at = None",
        "scan_lease_token = None",
        "scan_lease_until = None",
        "scan_operation_ref_digest = None",
        "scan_version += 1",
    ):
        assert token in source, f"A3_I4_DELETE_FENCE_MISSING_{token}"
