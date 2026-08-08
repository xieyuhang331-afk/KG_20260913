import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "app" / "composition" / "registration_outbox_runtime.py"


def test_运行时组合禁止创建基础设施或公开入口():
    assert RUNTIME.exists(), (
        "Registration orchestrator runtime composition is not implemented"
    )
    source = RUNTIME.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "app.main" not in source
    assert "app.modules.auth.api" not in source
    assert "app.core.security" not in source
    assert "get_settings" not in imported
    assert not calls.intersection(
        {"create_async_engine", "create_session_factory", "get_session_factory"}
    )
    assert "FastAPI" not in source
    assert "JWT" not in source


def test_运行时组合只依赖既有Identity与Outbox边界():
    source = RUNTIME.read_text(encoding="utf-8")
    assert "IdentityPersistenceComposition" in source
    assert "SqlAlchemyRegistrationOutboxWorkerUnitOfWork" in source
    assert "RegistrationOutboxDispatcher" in source
    assert "RegistrationOutboxReconciler" in source
    assert "identity_session_factory" in source
    assert "worker_session_factory" in source
