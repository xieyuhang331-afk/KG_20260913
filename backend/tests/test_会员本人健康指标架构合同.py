from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).parents[1] / "app" / "modules"


def test_本人健康指标只读实现不创建基础设施或临床规则() -> None:
    paths = [
        ROOT / "user_health" / "api.py",
        ROOT / "user_health" / "schemas.py",
        ROOT / "user_health" / "service.py",
        ROOT / "user_health" / "repository.py",
        ROOT / "health_analysis" / "api.py",
        ROOT / "health_analysis" / "schemas.py",
        ROOT / "health_analysis" / "service.py",
        ROOT / "health_analysis" / "repository.py",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    for forbidden in (
        "create_async_engine",
        "create_all",
        "alembic",
        "risk_level",
        "diagnosis_hint",
        "normal_range",
        "unit_conversion",
    ):
        assert forbidden not in combined
    for path in paths:
        ast.parse(path.read_text(encoding="utf-8"))
