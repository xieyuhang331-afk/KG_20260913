import ast
from pathlib import Path

from app.modules.member.application.registration_bootstrap_ports import (
    RegistrationBootstrapUnitOfWork,
    RegistrationEligibilityProofReader,
    RegistrationMemberNoAllocationProofReader,
)


APPLICATION_DIR = (
    Path(__file__).parents[1]
    / "app"
    / "modules"
    / "member"
    / "application"
)


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }


def test_Bootstrap应用层不依赖SQLAlchemy数据库或API():
    for filename in (
        "registration_bootstrap.py",
        "registration_bootstrap_ports.py",
    ):
        imports = _imports(APPLICATION_DIR / filename)
        assert not any(name.startswith("sqlalchemy") for name in imports)
        assert not any("fastapi" in name for name in imports)
        assert not any("migrations" in name for name in imports)


def test_ProofReader与IdentityUoW保持独立协议边界():
    assert RegistrationEligibilityProofReader is not RegistrationBootstrapUnitOfWork
    assert (
        RegistrationMemberNoAllocationProofReader
        is not RegistrationBootstrapUnitOfWork
    )
    assert "eligibility_reader" not in RegistrationBootstrapUnitOfWork.__annotations__
    assert "allocation_reader" not in RegistrationBootstrapUnitOfWork.__annotations__


def test_Bootstrap生产模块不包含公开路由或认证接线():
    source = (APPLICATION_DIR / "registration_bootstrap.py").read_text(
        encoding="utf-8"
    )
    assert "APIRouter" not in source
    assert "register_user" not in source
    assert "jwt" not in source.lower()
    assert "outbox" not in source.lower()
