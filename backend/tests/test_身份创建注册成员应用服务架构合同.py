import ast
from pathlib import Path

from app.modules.member import application
from app.modules.member.application.create_registration_member import (
    CreateRegistrationMemberService,
)


MODULE_PATH = (
    Path(__file__).parents[1]
    / "app"
    / "modules"
    / "member"
    / "application"
    / "create_registration_member.py"
)


def test_应用服务只依赖端口和领域边界():
    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    assert not any("fastapi" in name for name in imported_modules)
    assert not any("sqlalchemy" in name for name in imported_modules)
    assert not any("infrastructure" in name for name in imported_modules)
    assert "AsyncSession" not in source
    assert "create_identity_session_factory" not in source


def test_应用包只导出内部应用合同且没有API路由():
    assert application.CreateRegistrationMemberService is CreateRegistrationMemberService
    assert "CreateRegistrationMemberService" in application.__all__
    assert "router" not in application.__all__
    assert "api" not in application.__all__
