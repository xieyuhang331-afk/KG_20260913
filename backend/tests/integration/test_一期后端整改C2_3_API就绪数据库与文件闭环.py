from __future__ import annotations

import asyncio
import os
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.readiness import probe_database_role
from app.tasks.readiness import _run_worker_check


def test_C2_3_API使用正式应用角色与真实本地文件根完成就绪闭环(
    real_db_client,
) -> None:
    response = real_db_client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "ok",
        "data": {"status": "READY"},
    }
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["pragma"] == "no-cache"

    root = Path(os.environ["KG_PRIVATE_FILE_STORAGE_ROOT"])
    readiness_root = root / ".readiness"
    assert not readiness_root.exists() or not any(readiness_root.rglob("*"))


def test_C2_3_liveness不依赖数据库文件Broker或Worker(real_db_client) -> None:
    response = real_db_client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "ok",
        "data": {"status": "LIVE"},
    }


def test_C2_3_迁移Owner即使无super标志也不得作为API就绪角色() -> None:
    database_url = os.environ["KG_TEST_MIGRATION_DATABASE_URL"]
    expected_role = make_url(database_url).username
    assert expected_role

    async def verify() -> None:
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            assert await probe_database_role(factory, expected_role) is False
        finally:
            await engine.dispose()

    asyncio.run(verify())


def test_C2_3_Slice4七个正式身份全部通过数据库权威就绪检查() -> None:
    assert asyncio.run(_run_worker_check("slice4")) is True


def test_C2_3_Member双正式身份全部通过数据库权威就绪检查() -> None:
    assert asyncio.run(_run_worker_check("member")) is True
