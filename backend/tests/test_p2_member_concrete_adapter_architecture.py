import ast
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MEMBER_DIR = BACKEND_ROOT / "app" / "modules" / "member"
TEST_DIR = BACKEND_ROOT / "tests"


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def test_ct0_persistence_production_artifacts_are_absent():
    forbidden_paths = {
        MEMBER_DIR / "infrastructure" / "mapper.py",
        MEMBER_DIR / "infrastructure" / "models.py",
        MEMBER_DIR / "infrastructure" / "sqlalchemy_repository.py",
        MEMBER_DIR / "infrastructure" / "unit_of_work.py",
    }

    assert not {path for path in forbidden_paths if path.exists()}


def test_ct0_member_domain_has_no_persistence_runtime_dependencies():
    forbidden_roots = {"alembic", "asyncpg", "sqlalchemy"}
    imported_roots = set()

    for path in MEMBER_DIR.glob("*.py"):
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.split(".", 1)[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                imported_roots.add((node.module or "").split(".", 1)[0])

    assert forbidden_roots.isdisjoint(imported_roots)


def test_ct0_test_first_files_contain_no_persistence_implementation():
    test_paths = {
        TEST_DIR / "test_p2_member_concrete_adapter_architecture.py",
        TEST_DIR / "test_p2_member_concrete_adapter_contract.py",
    }
    forbidden_calls = {
        "close",
        "commit",
        "create_async_engine",
        "create_engine",
        "flush",
        "rollback",
    }
    forbidden_classes = {
        "FakeMemberRepository",
        "InMemoryMemberRepository",
        "SqlAlchemyMemberRepository",
    }

    calls = set()
    classes = set()
    for path in test_paths:
        tree = _parse(path)
        classes.update(
            node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        )
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                calls.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                calls.add(node.func.attr)

    assert forbidden_classes.isdisjoint(classes)
    assert forbidden_calls.isdisjoint(calls)


def test_ct0_blocked_decision_declarations_are_absent():
    forbidden_declarations = {
        "Claim",
        "Delegation",
        "FamilyRelationship",
        "HealthDataAuthorization",
        "HealthFactCorrection",
        "SelfMemberLink",
        "ServiceRelationship",
        "Supersession",
        "TherapistAssignment",
        "Withdrawal",
    }
    declarations = set()

    for path in MEMBER_DIR.glob("*.py"):
        declarations.update(
            node.name
            for node in ast.walk(_parse(path))
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        )

    assert forbidden_declarations.isdisjoint(declarations)
