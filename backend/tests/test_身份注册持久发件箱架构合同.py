import ast
import os
from pathlib import Path
import subprocess
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[1]
AUTH_DIR = ROOT / "app" / "modules" / "auth"
FILES = (
    AUTH_DIR / "registration_outbox_models.py",
    AUTH_DIR / "registration_outbox_repository.py",
    AUTH_DIR / "eligibility_evidence_models.py",
)
R2_FILES = (
    AUTH_DIR / "registration_outbox_worker.py",
    AUTH_DIR / "registration_outbox_worker_repository.py",
)
EXPECTED_IMPORT_TOPOLOGY_RED = (
    "Registration outbox model import topology is not registered safely"
)


def _run_fresh_import(script: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["KG_RUN_PG_INTEGRATION"] = "0"
    for name in (
        "KG_TEST_DATABASE_URL",
        "KG_TEST_MIGRATION_DATABASE_URL",
        "KG_TEST_READONLY_DATABASE_URL",
    ):
        environment.pop(name, None)
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_持久发件箱模型新鲜进程导入拓扑安全且Metadata只注册一次():
    direct_models = _run_fresh_import(
        """
        from app.core.database import Base
        from app.modules.auth.registration_outbox_models import (
            RegistrationVerifiedOutboxOrmModel,
        )

        key = "public.registration_verified_outbox"
        assert list(Base.metadata.tables).count(key) == 1
        assert Base.metadata.tables[key] is RegistrationVerifiedOutboxOrmModel.__table__
        """
    )
    assert direct_models.returncode == 0, (
        "direct registration_outbox_models import failed"
    )

    direct_repository = _run_fresh_import(
        """
        from app.core.database import Base
        from app.modules.auth import registration_outbox_repository

        key = "public.registration_verified_outbox"
        assert list(Base.metadata.tables).count(key) == 1
        assert registration_outbox_repository.RegistrationVerifiedOutboxOrmModel.__table__ is Base.metadata.tables[key]
        """
    )
    assert direct_repository.returncode == 0, (
        "direct registration_outbox_repository import failed"
    )

    alembic_import = _run_fresh_import(
        """
        import importlib.util
        from pathlib import Path

        from app.core.database import Base

        env_path = Path("app/migrations/env.py").resolve()
        spec = importlib.util.spec_from_file_location(
            "p1_outbox_import_topology_alembic_env",
            env_path,
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        key = "public.registration_verified_outbox"
        assert module.target_metadata is Base.metadata
        assert list(Base.metadata.tables).count(key) == 1
        """
    )
    assert alembic_import.returncode == 0, EXPECTED_IMPORT_TOPOLOGY_RED


def test_持久发件箱SQLAlchemy边界不创建Engine或执行DDL():
    forbidden_imports = {"fastapi", "alembic", "asyncpg"}
    forbidden_calls = {
        "create_engine",
        "create_async_engine",
        "async_sessionmaker",
        "create_all",
        "drop_all",
        "connect",
    }
    for path in FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {
            (node.module or "").split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        } | {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        } | {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
        }
        assert forbidden_imports.isdisjoint(imports)
        assert forbidden_calls.isdisjoint(calls)


def test_持久发件箱Persistence不提供Worker或公开API能力():
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in FILES
    ).lower()
    for forbidden in (
        "apirouter",
        "httpexception",
        "register_user",
        "dispatcher",
        "reconciliation",
        "def claim",
        "def ack",
        "def delete",
        "def update",
        "on conflict",
        "upsert",
    ):
        assert forbidden not in source


def test_R2调度恢复模块不创建Engine不执行DDL且不暴露API():
    forbidden_imports = {"fastapi", "alembic", "asyncpg"}
    forbidden_calls = {
        "create_engine",
        "create_async_engine",
        "async_sessionmaker",
        "create_all",
        "drop_all",
        "connect",
    }
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in R2_FILES
    ).lower()
    for path in R2_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {
            (node.module or "").split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        } | {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        } | {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
        }
        assert forbidden_imports.isdisjoint(imports)
        assert forbidden_calls.isdisjoint(calls)
    for forbidden in (
        "apirouter",
        "httpexception",
        "register_user",
        "jwt",
        "create_all",
        "drop_all",
    ):
        assert forbidden not in source


def test_R1Writer持久化与R2Worker模块分离():
    r1_source = "\n".join(
        path.read_text(encoding="utf-8") for path in FILES
    ).lower()
    assert "class registrationoutboxdispatcher" not in r1_source
    assert "class registrationoutboxreconciler" not in r1_source
    r2_source = "\n".join(
        path.read_text(encoding="utf-8") for path in R2_FILES
    )
    assert "class RegistrationOutboxDispatcher" in r2_source
    assert "class RegistrationOutboxReconciler" in r2_source
