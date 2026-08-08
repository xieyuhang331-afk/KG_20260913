import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FILES = (
    ROOT / "app/modules/auth/manual_identity_review_adapter.py",
    ROOT / "app/composition/p1_verified_transition.py",
)


def test_人工审核适配器和组合保持内部边界且不创建Engine():
    forbidden_imports = {"alembic", "asyncpg", "celery", "fastapi"}
    forbidden_calls = {
        "create_async_engine",
        "create_engine",
        "create_all",
        "drop_all",
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
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
        }
        assert forbidden_imports.isdisjoint(imports)
        assert forbidden_calls.isdisjoint(calls)


def test_人工审核接线不开放API_JWT_register_user或provider_callback():
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in FILES
    ).lower()
    for forbidden in (
        "apirouter",
        "httpexception",
        "jwt",
        "register_user",
        "provider_callback",
        "identity_card",
        "id_card",
    ):
        assert forbidden not in source
