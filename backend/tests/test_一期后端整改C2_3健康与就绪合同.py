from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.readiness import ReadinessService, probe_private_object_store
from app.main import create_app


class _FakeReadiness:
    def __init__(self, ready: bool) -> None:
        self.ready_result = ready
        self.calls = 0

    async def ready(self) -> bool:
        self.calls += 1
        return self.ready_result

    async def close(self) -> None:
        return None


def _client_with(readiness: _FakeReadiness) -> TestClient:
    app = create_app()
    app.state.readiness_service = readiness
    return TestClient(app)


def test_C2_3_R01_既有health响应完全兼容且live不探测依赖() -> None:
    readiness = _FakeReadiness(False)
    with _client_with(readiness) as client:
        legacy = client.get("/health")
        live = client.get("/health/live")

    assert legacy.status_code == 200
    assert legacy.json() == {
        "code": 0,
        "message": "ok",
        "data": {"service": "KG_20260727", "status": "ok", "version": "0.1.0"},
    }
    assert legacy.headers["cache-control"] == "no-store, private"
    assert legacy.headers["pragma"] == "no-cache"
    assert live.status_code == 200
    assert live.json() == {"code": 0, "message": "ok", "data": {"status": "LIVE"}}
    assert readiness.calls == 0
    for response in (live,):
        assert response.headers["cache-control"] == "no-store, private"
        assert response.headers["pragma"] == "no-cache"


def test_C2_3_R05_ready成功与失败使用闭合且不同的DTO() -> None:
    available = _FakeReadiness(True)
    with _client_with(available) as client:
        success = client.get("/health/ready")
    assert success.status_code == 200
    assert success.json() == {"code": 0, "message": "ok", "data": {"status": "READY"}}
    assert success.headers["cache-control"] == "no-store, private"

    unavailable = _FakeReadiness(False)
    with _client_with(unavailable) as client:
        failure = client.get("/health/ready")
    assert failure.status_code == 503
    assert set(failure.json()) == {"code", "message", "request_id", "retryable", "field_errors"}
    assert failure.json()["code"] == "DEPENDENCY_UNAVAILABLE"
    assert failure.json()["message"] == "request rejected"
    assert failure.json()["retryable"] is True
    assert failure.json()["field_errors"] == []
    assert failure.headers["cache-control"] == "no-store, private"
    assert failure.headers["pragma"] == "no-cache"


def test_C2_3_R05_openapi健康路由公开且schema闭合() -> None:
    with _client_with(_FakeReadiness(True)) as client:
        schema = client.get("/openapi.json").json()
    assert schema["paths"]["/health"]["get"]["deprecated"] is True
    for path in ("/health", "/health/live", "/health/ready"):
        assert schema["paths"][path]["get"]["security"] == []
    ready = schema["paths"]["/health/ready"]["get"]
    assert ready["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/HealthResponseDTO"
    )
    assert ready["responses"]["503"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponseDTO"
    }
    for path in ("/health/live", "/health/ready"):
        headers = schema["paths"][path]["get"]["responses"]["200"]["headers"]
        assert headers["Cache-Control"]["schema"]["const"] == "no-store, private"
        assert headers["Pragma"]["schema"]["const"] == "no-cache"
        assert headers["X-Request-ID"]["schema"]["format"] == "uuid"


class _Rows:
    def __init__(self, mapping: dict[str, object]) -> None:
        self._mapping = mapping

    def mappings(self) -> _Rows:
        return self

    def one(self) -> dict[str, object]:
        return self._mapping


class _Session:
    def __init__(self, mapping: dict[str, object], calls: list[str]) -> None:
        self._mapping = mapping
        self._calls = calls

    async def execute(self, statement: object) -> _Rows:
        self._calls.append(str(statement))
        return _Rows(self._mapping)

    async def close(self) -> None:
        return None

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


def _session_factory(mapping: dict[str, object], calls: list[str]):
    return lambda: _Session(mapping, calls)


@pytest.mark.asyncio
async def test_C2_3_R02_DB探针绑定正式角色并拒绝高权角色(tmp_path: Path) -> None:
    calls: list[str] = []
    service = ReadinessService(
        session_factory=_session_factory(
            {
                "probe": 1,
                "role": "kg_app",
                "high_privilege": False,
            },
            calls,
        ),
        expected_database_role="kg_app",
        object_store=SimpleNamespace(root=tmp_path),
        file_probe=lambda: asyncio.sleep(0, result=True),
    )
    assert await service._probe_database() is True
    assert len(calls) == 1
    assert "current_user" in calls[0]
    assert "pg_roles" in calls[0]

    high_privilege = ReadinessService(
        session_factory=_session_factory(
            {"probe": 1, "role": "kg_app", "high_privilege": True}, []
        ),
        expected_database_role="kg_app",
        object_store=SimpleNamespace(root=tmp_path),
        file_probe=lambda: asyncio.sleep(0, result=True),
    )
    assert await high_privilege._probe_database() is False


@pytest.mark.asyncio
async def test_C2_3_R03_R04_文件探针单飞超时后仍受生命周期追踪(tmp_path: Path) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def slow_probe() -> bool:
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return True

    service = ReadinessService(
        session_factory=_session_factory(
            {"probe": 1, "role": "kg_app", "high_privilege": False}, []
        ),
        expected_database_role="kg_app",
        object_store=SimpleNamespace(root=tmp_path),
        file_probe=slow_probe,
        dependency_timeout_seconds=0.01,
        total_timeout_seconds=0.05,
        cache_seconds=0,
    )
    first, second = await asyncio.gather(service.ready(), service.ready())
    assert (first, second) == (False, False)
    assert entered.is_set()
    assert calls == 1
    assert service.has_pending_probe is True
    release.set()
    await service.close()
    assert service.has_pending_probe is False


@pytest.mark.asyncio
async def test_C2_3_R04_文件探针取消优先于cleanup失败() -> None:
    entered = asyncio.Event()

    class Store:
        async def create_temporary(self, *_: object) -> None:
            return None

        async def write_chunk(self, *_: object) -> None:
            entered.set()
            await asyncio.Event().wait()

        async def abort_temporary(self, *_: object) -> None:
            raise RuntimeError("path and credential must stay hidden")

    task = asyncio.create_task(probe_private_object_store(Store()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_C2_3_R04_应用关闭取消优先于后台探针失败(tmp_path: Path) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def failing_probe() -> bool:
        entered.set()
        await release.wait()
        raise RuntimeError("path and credential must stay hidden")

    service = ReadinessService(
        session_factory=_session_factory(
            {"probe": 1, "role": "kg_app", "high_privilege": False}, []
        ),
        expected_database_role="kg_app",
        object_store=SimpleNamespace(root=tmp_path),
        file_probe=failing_probe,
    )
    service._probe_task = asyncio.create_task(failing_probe())
    close = asyncio.create_task(service.close())
    await entered.wait()
    close.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await close
