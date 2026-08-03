import ast
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MEMBER_DIR = BACKEND_ROOT / "app" / "modules" / "member"
INFRASTRUCTURE_DIR = MEMBER_DIR / "infrastructure"
MAPPER_PATH = INFRASTRUCTURE_DIR / "mapper.py"
ADAPTER_PATH = INFRASTRUCTURE_DIR / "sqlalchemy_repository.py"
CONTRACT_PATH = BACKEND_ROOT / "tests" / "test_p2_member_mapper_contract.py"


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_roots(tree: ast.AST) -> set[str]:
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".", 1)[0])
    return roots


def _called_names(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def test_mapper_at0_domain_does_not_depend_on_infrastructure():
    forbidden_modules = {"infrastructure", "sqlalchemy_repository", "mapper"}
    imported_roots = set()

    for path in MEMBER_DIR.glob("*.py"):
        imported_roots.update(_imported_roots(_parse(path)))

    assert forbidden_modules.isdisjoint(imported_roots)


def test_mapper_at0_repository_uses_injected_mapper_only():
    tree = _parse(ADAPTER_PATH)
    repository_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "SqlAlchemyMemberRepository"
    )
    initializer = next(
        node
        for node in repository_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    parameter_names = {argument.arg for argument in initializer.args.args}

    assert {"mapper", "session"}.issubset(parameter_names)
    assert "MemberMapper" not in _called_names(tree)


def test_mapper_at0_mapper_has_no_runtime_dependencies_when_present():
    if not MAPPER_PATH.exists():
        return

    tree = _parse(MAPPER_PATH)
    forbidden_imports = {
        "alembic",
        "asyncio",
        "asyncpg",
        "fastapi",
        "os",
        "sqlalchemy",
    }
    forbidden_calls = {
        "async_sessionmaker",
        "close",
        "commit",
        "connect",
        "create_async_engine",
        "create_engine",
        "execute",
        "flush",
        "rollback",
        "sessionmaker",
        "uuid1",
        "uuid4",
        "uuid6",
        "uuid7",
    }

    assert forbidden_imports.isdisjoint(_imported_roots(tree))
    assert forbidden_calls.isdisjoint(_called_names(tree))


def test_mapper_at0_unapproved_persistence_artifacts_are_absent():
    forbidden_paths = {
        INFRASTRUCTURE_DIR / "models.py",
        INFRASTRUCTURE_DIR / "sqlalchemy_unit_of_work.py",
        INFRASTRUCTURE_DIR / "unit_of_work.py",
        INFRASTRUCTURE_DIR / "migration.py",
        INFRASTRUCTURE_DIR / "migrations.py",
    }

    assert not {path for path in forbidden_paths if path.exists()}


def test_mapper_at0_contract_inventory_is_complete():
    tree = _parse(CONTRACT_PATH)
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "MAPPER_SCENARIOS"
            for target in node.targets
        )
    )
    scenarios = set(ast.literal_eval(assignment.value))
    required = {
        "ENUM-001",
        "ENUM-002",
        "ERROR-001",
        "IDENTITY-001",
        "LEAK-001",
        "MAP-D2P",
        "MAP-P2D",
        "MEMBER-NO",
        "OPTIONAL-001",
        "PURITY-001",
        "UUID-001",
        "UUID-002",
        "VERSION-001",
        "VERSION-002",
    }

    assert scenarios == required


def test_mapper_at0_tests_have_no_bypass_or_dynamic_implementation():
    forbidden_marks = {"skip", "skipif", "xfail"}
    forbidden_test_doubles = {"FakeMemberMapper", "InMemoryMemberMapper"}
    for path in {Path(__file__), CONTRACT_PATH}:
        tree = _parse(path)
        used_marks = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in forbidden_marks
        }
        declared_classes = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        }

        assert not used_marks
        assert "sys" not in _imported_roots(tree)
        assert "__import__" not in _called_names(tree)
        assert forbidden_test_doubles.isdisjoint(declared_classes)
