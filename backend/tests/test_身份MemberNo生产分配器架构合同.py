import ast
from pathlib import Path

from app.modules.member import application
from app.modules.member.application.member_no_allocator import (
    RegistrationMemberNoAllocator,
)


MODULE_PATH = (
    Path(__file__).parents[1]
    / "app"
    / "modules"
    / "member"
    / "application"
    / "member_no_allocator.py"
)


def test_MemberNo分配器只依赖应用端口和值对象边界():
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
    assert "is_member_no_available" not in source
    assert "upsert" not in source.lower()
    assert "delete(" not in source.lower()


def test_应用包导出稳定分配器合同且没有API路由():
    assert (
        application.RegistrationMemberNoAllocator
        is RegistrationMemberNoAllocator
    )
    assert "RegistrationMemberNoAllocator" in application.__all__
    assert "router" not in application.__all__
    assert "api" not in application.__all__


def test_生产分配器只声明三次候选写入预算():
    source = MODULE_PATH.read_text(encoding="utf-8")

    assert "_MAX_CANDIDATE_INSERT_ATTEMPTS = 3" in source
    assert "while True" not in source
    assert "secrets.randbits" in source
