from pathlib import Path


def test_Organization基础保持分层和受保护边界():
    root = Path(__file__).parents[1] / "app" / "modules" / "organization"
    assert {path.name for path in root.glob("*.py")} >= {
        "domain.py", "ports.py", "models.py", "repository.py", "service.py", "schemas.py", "api.py"
    }
    combined = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    assert "create_async_engine" not in combined
    assert "create_all(" not in combined
    assert "app.modules.review" not in combined
