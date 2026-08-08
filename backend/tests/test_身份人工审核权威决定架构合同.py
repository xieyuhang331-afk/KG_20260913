import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = (
    ROOT
    / "app"
    / "modules"
    / "auth"
    / "identity_verification_authority.py"
)


def test_人工审核权威决定保持纯领域边界():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    forbidden_imports = {
        "alembic",
        "asyncpg",
        "celery",
        "fastapi",
        "sqlalchemy",
    }
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
    assert forbidden_imports.isdisjoint(imports)


def test_人工审核权威决定不暴露API或业务写入能力():
    source = MODULE.read_text(encoding="utf-8").lower()
    for forbidden in (
        "apirouter",
        "httpexception",
        "register_user",
        "session",
        "commit(",
        "rollback(",
        "insert(",
        "update(",
        "delete(",
        "provider_callback",
    ):
        assert forbidden not in source
