import asyncio
import pytest

def test_projection_runtime_requires_running_loop_and_is_per_loop(monkeypatch):
    from app.core import database
    async def invalid():
        with pytest.raises(RuntimeError):
            await database.get_projection_session_factory("unknown")
    asyncio.run(invalid())

def test_projection_modules_have_no_api_or_module_c_entrypoints():
    from pathlib import Path
    root=Path("app/modules")
    for module in ("organization_projection","health_projection"):
        assert not (root/module/"api.py").exists()
        text="\n".join(p.read_text(encoding="utf-8") for p in (root/module).glob("*.py"))
        for forbidden in ("READY","ACTIVE","Shadow","read_cutover"):
            assert forbidden not in text


def test_total_database_dispose_fails_closed_when_orphan_projection_runtime_exists():
    from pathlib import Path
    source = Path("app/core/database.py").read_text(encoding="utf-8")
    body = source.split("async def dispose_database_runtimes", 1)[1].split("def _projection_url", 1)[0]
    assert "invalidate_orphaned_projection_runtimes()" in body
    assert "raise RuntimeError(_PROJECTION_RUNTIME_ERROR)" in body


def test_projection_urls_must_share_exact_database_target():
    from app.core.database import _projection_url
    from app.core.config import Settings

    base = dict(database_password="x", jwt_secret_key="y", database_host="db", database_port=5432, database_name="kg")
    urls = dict(
        organization_projection_builder_database_url="postgresql+asyncpg://org:p@db:5432/kg",
        health_projection_builder_database_url="postgresql+asyncpg://health:p@db:5432/kg",
        projection_confirmation_database_url="postgresql+asyncpg://confirm:p@db:5432/kg",
    )
    settings = Settings(**base, **urls)
    assert _projection_url(settings, "organization") == urls["organization_projection_builder_database_url"]
    for field, bad in (
        ("health_projection_builder_database_url", "postgresql+asyncpg://health:p@other:5432/kg"),
        ("health_projection_builder_database_url", "postgresql+asyncpg://health:p@db:5433/kg"),
        ("projection_confirmation_database_url", "postgresql+asyncpg://confirm:p@db:5432/other"),
    ):
        changed = dict(urls); changed[field] = bad
        with pytest.raises(RuntimeError):
            _projection_url(Settings(**base, **changed), "organization")
