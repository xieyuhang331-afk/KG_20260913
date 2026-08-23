from pathlib import Path

import asyncio

from app.core import database


ROOT = Path(__file__).resolve().parents[1]


def test_Module_D无ACTIVE公共API且仅增加Slice4批准的LatestREADY端口():
    module = ROOT / "app/modules/projection_read"
    text = "\n".join(p.read_text("utf-8") for p in module.glob("*.py"))
    assert "ACTIVE" not in text
    assert "APIRouter" not in text
    assert "user_health" not in text
    assert "health_analysis" not in text
    assert "LatestReadyHealthProjectionResolverService" in text
    assert "resolve_latest_ready_generation" in text
    assert "MemberHealthProjectionReadService" in text


def test_Module_D双runtime身份存在且隔离():
    config = (ROOT / "app/core/config.py").read_text("utf-8")
    database = (ROOT / "app/core/database.py").read_text("utf-8")
    for name in ("organization_projection_reader_database_url", "health_projection_reader_database_url"):
        assert name in config and name in database
    assert '"organization_reader"' in database
    assert '"health_reader"' in database


def test_Module_D_CI与IntegrationFixture传播完整生产角色预检配置():
    workflow = (ROOT.parent / ".github/workflows/p2-foundation-ci.yml").read_text(
        "utf-8"
    )
    fixture = (ROOT / "tests/integration/conftest.py").read_text("utf-8")
    aliases = (
        ("KG_READONLY_ROLE", "KG_TEST_READONLY_ROLE"),
        ("KG_VERIFICATION_WRITER_ROLE", "KG_TEST_VERIFICATION_WRITER_ROLE"),
        ("KG_DELIVERY_WORKER_ROLE", "KG_TEST_DELIVERY_WORKER_ROLE"),
        ("KG_OUTBOX_AUDIT_ROLE", "KG_TEST_OUTBOX_AUDIT_ROLE"),
        ("KG_HEALTH_FACT_WRITER_ROLE", "KG_TEST_HEALTH_FACT_WRITER_ROLE"),
        ("KG_ORGANIZATION_MAPPING_WRITER_ROLE", "KG_TEST_ORGANIZATION_MAPPING_WRITER_ROLE"),
        ("KG_HEALTH_MAPPING_WRITER_ROLE", "KG_TEST_HEALTH_MAPPING_WRITER_ROLE"),
        ("KG_MAPPING_AUDIT_ROLE", "KG_TEST_MAPPING_AUDIT_ROLE"),
        ("KG_MAPPING_SHADOW_ROLE", "KG_TEST_MAPPING_SHADOW_ROLE"),
        ("KG_READONLY_DATABASE_URL", "KG_TEST_READONLY_DATABASE_URL"),
        ("KG_OUTBOX_AUDIT_DATABASE_URL", "KG_TEST_OUTBOX_AUDIT_DATABASE_URL"),
        ("KG_MAPPING_AUDIT_DATABASE_URL", "KG_TEST_MAPPING_AUDIT_DATABASE_URL"),
        ("KG_MAPPING_SHADOW_DATABASE_URL", "KG_TEST_MAPPING_SHADOW_DATABASE_URL"),
    )
    for runtime_name, test_name in aliases:
        mapping = f'"{runtime_name}": "{test_name}"'
        assert mapping in workflow
        assert mapping in fixture
    assert "KG_TEST_ROLE_ADMIN_DATABASE_URL" in workflow


def test_Module_D_reader_runtime同loop复用_dispose且跨loop不复用(monkeypatch):
    created = []

    class _SyncEngine:
        def dispose(self, close=False):
            pass

    class _Engine:
        def __init__(self):
            self.disposed = 0
            self.sync_engine = _SyncEngine()

        async def dispose(self):
            self.disposed += 1

    def create_engine(*args, **kwargs):
        engine = _Engine()
        created.append(engine)
        return engine

    monkeypatch.setattr(database, "create_async_engine", create_engine)
    monkeypatch.setattr(database, "create_session_factory", lambda engine: object())
    monkeypatch.setattr(database, "get_settings", lambda: object())
    monkeypatch.setattr(database, "_projection_url", lambda settings, kind: "safe")
    database._PROJECTION_RUNTIMES.clear()
    database._PROJECTION_RUNTIME_LOCKS.clear()

    async def same_loop():
        first = await database.get_projection_session_factory("organization_reader")
        second = await database.get_projection_session_factory("organization_reader")
        assert first is second
        await database.dispose_projection_runtime("organization_reader")

    asyncio.run(same_loop())
    assert len(created) == 1 and created[0].disposed == 1

    factories = []
    async def one_loop():
        factories.append(
            await database.get_projection_session_factory("health_reader")
        )

    asyncio.run(one_loop())
    assert not database.invalidate_orphaned_projection_runtimes()
    asyncio.run(one_loop())
    assert factories[0] is not factories[1]
    assert not database.invalidate_orphaned_projection_runtimes()
    assert not database._PROJECTION_RUNTIMES
