from __future__ import annotations

import asyncio
from pathlib import Path
import traceback

import pytest


def _runtime(monkeypatch, writer_url: str | None):
    from app.core import config, database

    monkeypatch.setenv("KG_DATABASE_HOST", "127.0.0.1")
    monkeypatch.setenv("KG_DATABASE_PORT", "5432")
    monkeypatch.setenv("KG_DATABASE_NAME", "kg_disposable_contract")
    monkeypatch.setenv("KG_DATABASE_USER", "kg_application_contract")
    monkeypatch.setenv("KG_DATABASE_PASSWORD", "application-secret")
    monkeypatch.setenv("KG_JWT_SECRET_KEY", "jwt-test-secret")
    if writer_url is None:
        monkeypatch.delenv("KG_VERIFICATION_WRITER_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("KG_VERIFICATION_WRITER_DATABASE_URL", writer_url)
    config.get_settings.cache_clear()
    database._VERIFICATION_WRITER_ASYNC_ENGINE = None
    database._VERIFICATION_WRITER_SESSION_FACTORY = None
    return config, database


def test_VerificationWriterRuntimeSessionFactory尚未实现(monkeypatch):
    _, database = _runtime(
        monkeypatch,
        "postgresql+asyncpg://kg_writer_contract:writer-secret@"
        "127.0.0.1:5432/kg_disposable_contract",
    )
    engine = object()
    session_factory = object()
    created = []
    monkeypatch.setattr(
        database,
        "create_async_engine",
        lambda url, **kwargs: created.append((url, kwargs)) or engine,
    )
    monkeypatch.setattr(
        database,
        "create_session_factory",
        lambda value: session_factory if value is engine else None,
    )

    first = database.get_verification_writer_session_factory()
    second = database.get_verification_writer_session_factory()

    assert first is session_factory
    assert second is session_factory
    assert len(created) == 1
    assert created[0][1] == {"pool_pre_ping": True}
    assert database._VERIFICATION_WRITER_ASYNC_ENGINE is engine
    assert database._VERIFICATION_WRITER_SESSION_FACTORY is session_factory


@pytest.mark.parametrize(
    "writer_url",
    [
        None,
        "",
        "sqlite:///not-postgresql",
        "postgresql+asyncpg://kg_application_contract:secret@127.0.0.1:5432/kg_disposable_contract",
        "postgresql+asyncpg://postgres:secret@127.0.0.1:5432/kg_disposable_contract",
        "postgresql+asyncpg://kg_writer_contract@127.0.0.1:5432/kg_disposable_contract",
        "postgresql+asyncpg://kg_writer_contract:secret@127.0.0.1:5433/kg_disposable_contract",
        "postgresql+asyncpg://kg_writer_contract:secret@127.0.0.1:5432/other_database",
    ],
)
def test_WriterURL缺失同角色错误目标或无Credential全部fail_closed(
    monkeypatch, writer_url
):
    _, database = _runtime(monkeypatch, writer_url)
    with pytest.raises(
        RuntimeError,
        match="Verification writer database runtime is unavailable",
    ) as caught:
        database.get_verification_writer_session_factory()
    public = (
        f"{caught.value!s} {caught.value!r} "
        f"{caught.value.__cause__} {caught.value.__context__}"
    )
    for forbidden in (
        "writer-secret",
        "application-secret",
        "kg_disposable_contract",
        "other_database",
        "127.0.0.1",
    ):
        assert forbidden not in public


def test_WriterRuntime与ApplicationSessionFactory严格隔离(monkeypatch):
    _, database = _runtime(
        monkeypatch,
        "postgresql+asyncpg://kg_writer_contract:writer-secret@"
        "127.0.0.1:5432/kg_disposable_contract",
    )
    application_factory = object()
    writer_factory = object()
    database._SESSION_FACTORY = application_factory
    monkeypatch.setattr(database, "create_async_engine", lambda *args, **kwargs: object())
    monkeypatch.setattr(database, "create_session_factory", lambda engine: writer_factory)
    assert database.get_session_factory() is application_factory
    assert database.get_verification_writer_session_factory() is writer_factory
    assert writer_factory is not application_factory


def test_WriterRuntime显式Dispose并清空缓存(monkeypatch):
    _, database = _runtime(
        monkeypatch,
        "postgresql+asyncpg://kg_writer_contract:writer-secret@"
        "127.0.0.1:5432/kg_disposable_contract",
    )

    class Engine:
        def __init__(self):
            self.disposed = 0

        async def dispose(self):
            self.disposed += 1

    engines = []

    def create_engine(*args, **kwargs):
        engine = Engine()
        engines.append(engine)
        return engine

    monkeypatch.setattr(database, "create_async_engine", create_engine)
    monkeypatch.setattr(database, "create_session_factory", lambda engine: object())
    first = database.get_verification_writer_session_factory()
    asyncio.run(database.dispose_verification_writer_runtime())
    assert engines[0].disposed == 1
    assert database._VERIFICATION_WRITER_ASYNC_ENGINE is None
    assert database._VERIFICATION_WRITER_SESSION_FACTORY is None
    second = database.get_verification_writer_session_factory()
    assert second is not first
    assert len(engines) == 2


def test_WriterRuntime错误链和Traceback不泄漏URL(monkeypatch):
    _, database = _runtime(
        monkeypatch,
        "not-a-url-with-credential-marker",
    )
    with pytest.raises(RuntimeError) as caught:
        database.get_verification_writer_session_factory()
    public = " ".join(
        (
            str(caught.value),
            repr(caught.value),
            str(caught.value.__cause__),
            str(caught.value.__context__),
            "".join(
                traceback.format_exception(
                    type(caught.value),
                    caught.value,
                    caught.value.__traceback__,
                )
            ),
        )
    )
    assert "credential-marker" not in public


def test_生产WriterRuntime不读取测试URL或回退通用连接():
    root = Path(__file__).resolve().parents[1]
    config_source = (root / "app/core/config.py").read_text(encoding="utf-8")
    database_source = (root / "app/core/database.py").read_text(encoding="utf-8")
    combined = config_source + database_source
    assert "KG_VERIFICATION_WRITER_DATABASE_URL" in combined
    assert "KG_TEST_VERIFICATION_WRITER_DATABASE_URL" not in combined
    assert "KG_DATABASE_URL" not in combined


def test_API显式取得隔离的Application与VerificationWriterSessionFactory():
    source = (
        Path(__file__).resolve().parents[1]
        / "app/modules/review/api.py"
    ).read_text(encoding="utf-8")
    start = source.index("def get_platform_admin_manual_identity_review_service(")
    end = source.index("\n\ndef ", start + 5)
    dependency = source[start:end]
    compact = "".join(dependency.split())
    assert "get_verification_writer_session_factory" in dependency
    assert "get_session_factory" in dependency
    assert "authority_session_factory=get_session_factory()" in compact
    assert "verification_session_factory=(" in compact
    assert "get_verification_writer_session_factory()" in compact
    assert (
        "verification_session_factory=verification_session_factory"
        in compact
    )
