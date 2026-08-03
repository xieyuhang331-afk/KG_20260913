import ast
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MEMBER_DIR = BACKEND_ROOT / "app" / "modules" / "member"
INFRASTRUCTURE_DIR = MEMBER_DIR / "infrastructure"
INTEGRATION_PATH = INFRASTRUCTURE_DIR / "orm_state_mapper.py"
DOMAIN_MAPPER_PATH = INFRASTRUCTURE_DIR / "mapper.py"
ORM_MODEL_PATH = INFRASTRUCTURE_DIR / "models.py"
REPOSITORY_PATH = INFRASTRUCTURE_DIR / "sqlalchemy_repository.py"
CONTRACT_PATH = (
    BACKEND_ROOT / "tests" / "test_p2_member_orm_integration_contract.py"
)


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


def test_integration_at0_existing_boundary_types_are_separate():
    domain_mapper_tree = _parse(DOMAIN_MAPPER_PATH)
    orm_model_tree = _parse(ORM_MODEL_PATH)

    assert "sqlalchemy" not in _imported_roots(domain_mapper_tree)
    assert "Member" not in {
        node.name for node in ast.walk(orm_model_tree) if isinstance(node, ast.ClassDef)
    }


def test_integration_at0_module_location_is_exclusive_when_present():
    candidates = {
        path.name
        for path in INFRASTRUCTURE_DIR.glob("*state*mapper*.py")
        if path != INTEGRATION_PATH
    }

    assert not candidates


def test_integration_at0_future_mapper_has_no_runtime_dependencies_when_present():
    if not INTEGRATION_PATH.exists():
        return

    tree = _parse(INTEGRATION_PATH)
    forbidden_imports = {
        "alembic",
        "asyncio",
        "asyncpg",
        "fastapi",
        "os",
        "sqlalchemy",
    }
    forbidden_calls = {
        "add",
        "async_sessionmaker",
        "close",
        "commit",
        "connect",
        "create_async_engine",
        "create_engine",
        "execute",
        "flush",
        "merge",
        "refresh",
        "rollback",
        "select",
        "sessionmaker",
        "update",
    }

    assert forbidden_imports.isdisjoint(_imported_roots(tree))
    assert forbidden_calls.isdisjoint(_called_names(tree))


def test_integration_at0_repository_is_unchanged_during_test_first():
    tree = _parse(REPOSITORY_PATH)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert not {
        module for module in imported_modules if module.endswith("orm_state_mapper")
    }


def test_integration_at0_contract_inventory_is_complete():
    tree = _parse(CONTRACT_PATH)
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "INTEGRATION_SCENARIOS"
            for target in node.targets
        )
    )
    scenarios = set(ast.literal_eval(assignment.value))
    required = {
        "AUDIT-001",
        "BOUNDARY-001",
        "ERROR-001",
        "LEAK-001",
        "MODEL-TO-STATE",
        "PURITY-001",
        "STATE-TO-MODEL",
        "UUID-001",
        "VERSION-001",
    }

    assert scenarios == required


def test_integration_at0_tests_have_no_bypass_or_dynamic_mapper():
    forbidden_marks = {"skip", "skipif", "xfail"}
    forbidden_classes = {
        "FakeMemberOrmStateMapper",
        "InMemoryMemberOrmStateMapper",
    }

    for path in {Path(__file__), CONTRACT_PATH}:
        tree = _parse(path)
        used_marks = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in forbidden_marks
        }
        class_names = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        }

        assert not used_marks
        assert "sys" not in _imported_roots(tree)
        assert "__import__" not in _called_names(tree)
        assert forbidden_classes.isdisjoint(class_names)
