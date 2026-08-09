from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).parents[1] / "app" / "modules" / "user_health"


def test_检测报告读取边界不创建引擎或写入状态() -> None:
    targets = {
        "api.py": {"list_member_self_detection_reports", "get_member_self_detection_report"},
        "service.py": {
            "list_member_self_detection_reports_service",
            "get_member_self_detection_report_service",
        },
        "repository.py": {"list_member_detection_reports", "get_member_detection_report"},
    }
    for filename, names in targets.items():
        tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
        functions = [
            node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
        ]
        assert {node.name for node in functions} == names
        calls = {
            node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            for function in functions
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Attribute, ast.Name))
        }
        assert "create_async_engine" not in calls
        assert not ({"add", "add_all", "flush", "commit"} & calls)


def test_检测报告API没有writer设备AI或外部存储依赖() -> None:
    combined = "\n".join(
        (ROOT / filename).read_text(encoding="utf-8")
        for filename in ("api.py", "service.py", "repository.py")
    )
    for forbidden in ("minio", "ocr", "provider_callback", "report_writer", "risk_rating"):
        assert forbidden not in combined.lower()
