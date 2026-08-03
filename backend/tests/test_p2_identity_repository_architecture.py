import ast
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MEMBER_DIR = BACKEND_ROOT / "app" / "modules" / "member"


def _member_trees():
    return {
        path: ast.parse(path.read_text(encoding="utf-8"))
        for path in MEMBER_DIR.glob("*.py")
    }


def test_r1_repository_file_contains_only_abstract_contracts():
    forbidden_files = {
        "api.py",
        "database.py",
        "infrastructure.py",
        "migrations.py",
        "models.py",
        "orm.py",
        "router.py",
        "schemas.py",
    }

    assert forbidden_files.isdisjoint(path.name for path in MEMBER_DIR.glob("*.py"))

    repository_path = MEMBER_DIR / "repository.py"
    if not repository_path.exists():
        return

    tree = ast.parse(repository_path.read_text(encoding="utf-8"))
    assert all(
        isinstance(node, (ast.ClassDef, ast.Import, ast.ImportFrom))
        for node in tree.body
    )

    classes = {
        node.name: node for node in tree.body if isinstance(node, ast.ClassDef)
    }
    assert set(classes) == {
        "MemberNotFoundError",
        "MemberPersistenceUnavailableError",
        "MemberRepository",
        "MemberRepositoryError",
        "MemberUniquenessConflictError",
        "MemberVersionConflictError",
    }

    repository = classes["MemberRepository"]
    assert any(
        isinstance(base, ast.Name) and base.id == "Protocol"
        for base in repository.bases
    )
    async_methods = {
        node.name: node
        for node in repository.body
        if isinstance(node, ast.AsyncFunctionDef)
    }
    assert set(async_methods) == {
        "add",
        "get_by_id",
        "get_by_member_no",
        "is_member_no_available",
        "save",
    }
    assert not {
        node.name
        for node in repository.body
        if isinstance(node, ast.FunctionDef)
    } & set(async_methods)
    for method in (
        node
        for node in repository.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        assert len(method.body) == 1
        assert isinstance(method.body[0], ast.Expr)
        assert isinstance(method.body[0].value, ast.Constant)
        assert method.body[0].value.value is Ellipsis

    assignments = [
        node for node in repository.body if isinstance(node, ast.Assign)
    ]
    assert not assignments


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
