from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _startup_environment(tmp_path: Path) -> dict[str, str]:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("KG_"):
            environment.pop(name)
    environment.update(
        {
            "KG_ENV": "test",
            "KG_DATABASE_HOST": "127.0.0.1",
            "KG_DATABASE_PASSWORD": "orm-fk-db-material-0000000000000001",
            "KG_JWT_SECRET_KEY": "orm-fk-jwt-material-0000000000000002",
            "KG_AUTH_RATE_LIMIT_HMAC_KEY": "orm-fk-rate-material-000000000000003",
            "KG_SLICE5_CURSOR_SIGNING_KEY": "orm-fk-s5-cursor-material-000000000004",
            "KG_SLICE7_CURSOR_SIGNING_KEY": "orm-fk-s7-cursor-material-000000000005",
            "KG_SLICE5_PUBLIC_REFERENCE_HMAC_KEY": "orm-fk-s5-ref-material-000000000000006",
            "KG_PRIVATE_FILE_ACCESS_SIGNING_KEY": "orm-fk-file-material-0000000000000007",
            "KG_FILE_STORAGE_BACKEND": "local_filesystem",
            "KG_PRIVATE_FILE_STORAGE_ROOT": str(tmp_path),
        }
    )
    environment.pop("KG_CELERY_BROKER_URL", None)
    return environment


def test_正式App独立进程启动即注册机构邀请外键目标(tmp_path: Path) -> None:
    script = """
from app.main import app
from app.core.database import Base
from app.modules.institution_onboarding.models import InstitutionInvitationModel

assert app is not None
assert "platform_org" in Base.metadata.tables
foreign_key = next(iter(InstitutionInvitationModel.__table__.c.administrative_region_id.foreign_keys))
assert foreign_key.column.table is Base.metadata.tables["platform_org"]
assert foreign_key.column.name == "id"
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_ROOT,
        env=_startup_environment(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, "INSTITUTION_INVITATION_FK_TARGET_NOT_REGISTERED"


def test_模型只复用既有TableSpec桥接且不创建反射或伪目标() -> None:
    source = (
        BACKEND_ROOT / "app" / "modules" / "institution_onboarding" / "models.py"
    ).read_text(encoding="utf-8")

    assert "PLATFORM_ORG_TABLE" in source
    assert "build_sqlalchemy_table(PLATFORM_ORG_TABLE)" in source
    assert "autoload_with" not in source
    assert "extend_existing" not in source
    assert "Table(\"platform_org\"" not in source
