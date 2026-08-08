from __future__ import annotations

import ast
from pathlib import Path


PRODUCTION_FILES = (
    Path("app/modules/user_health/api.py"),
    Path("app/modules/user_health/schemas.py"),
    Path("app/modules/user_health/service.py"),
    Path("app/modules/user_health/repository.py"),
)


def test_本人健康档案实现保持冻结架构边界() -> None:
    forbidden_imports = (
        "app.modules.member",
        "app.modules.auth.registration_outbox",
        "app.modules.review",
        "app.migrations",
    )
    forbidden_calls = {"create_async_engine", "create_all", "drop_all"}

    for path in PRODUCTION_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = []
        calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
            elif isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    calls.append(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    calls.append(node.func.attr)

        assert not any(
            imported.startswith(prefix)
            for imported in imports
            for prefix in forbidden_imports
        ), path
        assert not (set(calls) & forbidden_calls), path


def test_本人健康档案合同禁止skip与xfail() -> None:
    test_paths = (
        Path("tests/test_会员本人基础健康档案API合同.py"),
        Path("tests/test_会员本人基础健康档案服务合同.py"),
        Path("tests/test_会员本人基础健康档案仓储合同.py"),
        Path("tests/test_会员本人基础健康档案架构合同.py"),
    )
    for path in test_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        markers = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if node.attr not in {"skip", "skipif", "xfail"}:
                continue
            if isinstance(node.value, ast.Attribute) and node.value.attr == "mark":
                markers.add(node.attr)
        assert not markers, (path, markers)
