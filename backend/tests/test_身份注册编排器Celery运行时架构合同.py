import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CELERY_APP = ROOT / "app" / "tasks" / "celery_app.py"
TASKS = ROOT / "app" / "tasks" / "registration_outbox_tasks.py"


def test_Celery运行时文件与禁止边界():
    assert CELERY_APP.exists(), (
        "Registration delivery Celery runtime is not implemented"
    )
    assert TASKS.exists(), (
        "Registration delivery Celery runtime is not implemented"
    )
    source = CELERY_APP.read_text(encoding="utf-8") + TASKS.read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    for forbidden in (
        "app.main",
        "FastAPI",
        "lifespan",
        "JWT",
        "register_user",
        "create_async_engine",
        "create_session_factory",
        "get_session_factory",
    ):
        assert forbidden not in source
    assert not calls.intersection(
        {"create_async_engine", "create_session_factory", "get_session_factory"}
    )


def test_Celery运行时不包含Credential或数据库目标():
    source = CELERY_APP.read_text(encoding="utf-8") + TASKS.read_text(
        encoding="utf-8"
    )
    lowered = source.lower()
    assert "postgresql://" not in lowered
    assert "postgresql+asyncpg://" not in lowered
    assert "amqp://" not in lowered
    assert "guest:guest" not in lowered
    assert "password=" not in lowered
