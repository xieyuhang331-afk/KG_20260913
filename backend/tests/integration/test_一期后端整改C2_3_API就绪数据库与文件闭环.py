from __future__ import annotations

import os
from pathlib import Path


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
