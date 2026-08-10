from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "app/modules/auth/identity_review_step_up.py"
SECURITY = ROOT / "app/core/security.py"


def test_StepUp模块不创建数据库运行时或修改业务状态机() -> None:
    source = MODULE.read_text(encoding="utf-8").lower()
    for forbidden in (
        "create_async_engine",
        "create_engine",
        "create_all",
        "alembic",
        "registrationverifiedoutboxormmodel",
        "p1verificationtransitionwriter",
        "register_user",
    ):
        assert forbidden not in source


def test_StepUp严格校验普通AccessToken但不修改签发函数() -> None:
    tree = ast.parse(SECURITY.read_text(encoding="utf-8"))
    functions = {
        node.name: ast.unparse(node)
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    assert "decode_access_token_for_step_up" in functions
    create_source = functions["create_access_token"]
    assert "payload.setdefault('iss', 'kanglin')" in create_source
    assert "payload.setdefault('typ', 'access')" in create_source
