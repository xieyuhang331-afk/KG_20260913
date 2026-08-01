import ast
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MEMBER_DIR = BACKEND_ROOT / "app" / "modules" / "member"


def _member_trees():
    return {
        path: ast.parse(path.read_text(encoding="utf-8"))
        for path in MEMBER_DIR.glob("*.py")
    }


def test_r0_contains_no_repository_or_infrastructure_production_files():
    forbidden_files = {
        "api.py",
        "database.py",
        "infrastructure.py",
        "migrations.py",
        "models.py",
        "orm.py",
        "repository.py",
        "router.py",
        "schemas.py",
    }

    assert forbidden_files.isdisjoint(path.name for path in MEMBER_DIR.glob("*.py"))


def test_member_module_has_no_persistence_or_api_dependencies():
    forbidden_roots = {
        "alembic",
        "asyncpg",
        "fastapi",
        "frontend",
        "sqlalchemy",
    }
    imported_roots = set()

    for tree in _member_trees().values():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.split(".", 1)[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                imported_roots.add((node.module or "").split(".", 1)[0])

    assert forbidden_roots.isdisjoint(imported_roots)


def test_member_module_has_no_uuid_generation_responsibility():
    forbidden_calls = {
        "Uuid7Generator",
        "uuid1",
        "uuid3",
        "uuid4",
        "uuid5",
        "uuid7",
    }
    called_names = set()

    for tree in _member_trees().values():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                called_names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called_names.add(node.func.attr)

    assert forbidden_calls.isdisjoint(called_names)


def test_member_module_has_no_blocked_relationship_or_mapping_writes():
    forbidden_declarations = {
        "Claim",
        "Delegation",
        "FamilyRelationship",
        "HealthDataAuthorization",
        "HealthFactCorrection",
        "LegacyIdentityMapping",
        "LegacyMappingWriter",
        "MemberClaim",
        "SelfMemberLink",
        "ServiceRelationship",
        "Supersession",
        "TherapistAssignment",
        "Withdrawal",
    }
    declarations = set()

    for tree in _member_trees().values():
        declarations.update(
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        )

    assert forbidden_declarations.isdisjoint(declarations)
