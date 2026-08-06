import ast
from dataclasses import fields
from pathlib import Path

from app.modules.member.application.registration_eligibility import (
    RegistrationEligibilityPolicy,
    TrustedRegistrationEligibilityFacts,
)


MODULE_PATH = (
    Path(__file__).parents[1]
    / "app"
    / "modules"
    / "member"
    / "application"
    / "registration_eligibility.py"
)


def test_资格策略仅依赖标准库纯值合同():
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

    assert imported_modules <= {"dataclasses", "enum", "uuid"}
    assert "fastapi" not in source.lower()
    assert "sqlalchemy" not in source.lower()
    assert "repository" not in source.lower()
    assert "unitofwork" not in source.replace("_", "").lower()
    assert "session" not in source.lower()
    assert "commit(" not in source
    assert "rollback(" not in source


def test_资格策略输入不含PII与持久化能力():
    field_names = {
        field.name for field in fields(TrustedRegistrationEligibilityFacts)
    }
    forbidden = {
        "phone",
        "real_name",
        "id_card",
        "password",
        "jwt",
        "tenant_id",
        "store_id",
    }

    assert field_names.isdisjoint(forbidden)
    assert not hasattr(RegistrationEligibilityPolicy, "commit")
    assert not hasattr(RegistrationEligibilityPolicy, "rollback")
    assert not hasattr(RegistrationEligibilityPolicy, "persist")
