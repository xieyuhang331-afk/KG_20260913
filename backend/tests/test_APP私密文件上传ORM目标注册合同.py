from __future__ import annotations

import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _isolated_environment(tmp_path: Path) -> dict[str, str]:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("KG_"):
            environment.pop(name)
    environment.update(
        {
            "KG_ENV": "test",
            "KG_DATABASE_HOST": "127.0.0.1",
            "KG_DATABASE_PASSWORD": secrets.token_urlsafe(32),
            "KG_JWT_SECRET_KEY": secrets.token_urlsafe(32),
            "KG_AUTH_RATE_LIMIT_HMAC_KEY": secrets.token_urlsafe(32),
            "KG_SLICE5_CURSOR_SIGNING_KEY": secrets.token_urlsafe(32),
            "KG_SLICE7_CURSOR_SIGNING_KEY": secrets.token_urlsafe(32),
            "KG_SLICE5_PUBLIC_REFERENCE_HMAC_KEY": secrets.token_urlsafe(32),
            "KG_PRIVATE_FILE_ACCESS_SIGNING_KEY": secrets.token_urlsafe(32),
            "KG_FILE_STORAGE_BACKEND": "local_filesystem",
            "KG_PRIVATE_FILE_STORAGE_ROOT": str(tmp_path),
        }
    )
    environment.pop("KG_CELERY_BROKER_URL", None)
    return environment


@pytest.mark.parametrize(
    "imports",
    (
        "from app.modules.private_file.models import PrivateFileModel",
        "from app.modules.institution_onboarding.models import InstitutionApplicationModel\n"
        "from app.modules.private_file.models import PrivateFileModel",
        "from app.main import app\n"
        "from app.modules.private_file.models import PrivateFileModel",
    ),
    ids=("private-file-first", "institution-onboarding-first", "formal-app-startup"),
)
def test_各正式导入顺序均只注册一个权威机构申请目标(
    tmp_path: Path,
    imports: str,
) -> None:
    script = f"""
import sys

from app.core.database import Base
{imports}

foreign_key = next(
    iter(PrivateFileModel.__table__.c.bound_application_id.foreign_keys)
)
assert foreign_key.column.table is Base.metadata.tables[
    "public.institution_application"
]
assert foreign_key.column.name == "application_id"
assert foreign_key.name == "fk_private_file_bound_application"
assert foreign_key.target_fullname == "public.institution_application.application_id"
assert type(PrivateFileModel.__table__.c.bound_application_id.type) is type(
    foreign_key.column.type
)
assert list(Base.metadata.tables).count("public.institution_application") == 1

license_foreign_key = next(
    iter(Base.metadata.tables["public.institution_license"].c.private_file_id.foreign_keys)
)
assert license_foreign_key.column.table is PrivateFileModel.__table__
assert list(Base.metadata.tables).count("public.private_file") == 1
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_ROOT,
        env=_isolated_environment(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, "PRIVATE_FILE_FK_TARGET_NOT_REGISTERED"


def test_目标注册不依赖反射_create_all或伪造影子表() -> None:
    source = (
        BACKEND_ROOT / "app" / "modules" / "private_file" / "models.py"
    ).read_text(encoding="utf-8")

    assert "autoload_with" not in source
    assert "create_all" not in source
    assert "extend_existing" not in source
    assert 'Table("institution_application"' not in source
