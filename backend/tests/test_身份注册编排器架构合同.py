import ast
from pathlib import Path

from app.modules.member import application


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "app/modules/member/application/registration_orchestrator.py"


def test_注册编排器保持内部应用层边界():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    roots = {
        (node.module or "").split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    roots.update(
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert {"sqlalchemy", "alembic", "fastapi", "asyncpg"}.isdisjoint(roots)
    assert application.RegistrationOrchestrator.__module__.endswith(
        "registration_orchestrator"
    )


def test_注册编排器不承担API或Dispatcher职责():
    source = MODULE.read_text(encoding="utf-8")
    assert "APIRouter" not in source
    assert "RegistrationOutboxDispatcher(" not in source
    assert "create_async_engine" not in source
    assert "sessionmaker" not in source
    assert "pytest.mark.skip" not in source
    assert "pytest.mark.xfail" not in source
