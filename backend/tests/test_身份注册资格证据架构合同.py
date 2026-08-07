import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTH_DIR = ROOT / "app" / "modules" / "auth"
FILES = (
    AUTH_DIR / "eligibility_evidence.py",
    AUTH_DIR / "eligibility_evidence_models.py",
    AUTH_DIR / "eligibility_evidence_repository.py",
)


def test_资格证据边界不依赖API运行时或创建数据库连接():
    forbidden_imports = {"fastapi", "alembic", "asyncpg"}
    forbidden_calls = {
        "create_engine",
        "create_async_engine",
        "async_sessionmaker",
        "connect",
        "commit",
        "rollback",
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


def test_资格证据模块不提供更新删除或公开API入口():
    source = "\n".join(path.read_text(encoding="utf-8") for path in FILES)
    for forbidden in (
        "def update_",
        "def delete_",
        "APIRouter",
        "HTTPException",
        "register_user",
        "submit_user_identity",
    ):
        assert forbidden not in source
