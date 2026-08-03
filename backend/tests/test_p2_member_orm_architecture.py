import ast
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MEMBER_DIR = BACKEND_ROOT / "app" / "modules" / "member"
INFRASTRUCTURE_DIR = MEMBER_DIR / "infrastructure"
MODEL_PATH = INFRASTRUCTURE_DIR / "models.py"
MAPPER_PATH = INFRASTRUCTURE_DIR / "mapper.py"
CONTRACT_PATH = BACKEND_ROOT / "tests" / "test_p2_member_orm_contract.py"


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


def test_orm_at0_domain_and_mapper_do_not_depend_on_orm():
    forbidden_roots = {"alembic", "asyncpg", "sqlalchemy"}

    for path in [*MEMBER_DIR.glob("*.py"), MAPPER_PATH]:
        assert forbidden_roots.isdisjoint(_imported_roots(_parse(path)))


def test_orm_at0_model_location_is_exclusive_when_present():
    candidate_model_files = {
        path
        for path in INFRASTRUCTURE_DIR.glob("*model*.py")
        if path.name != "models.py"
    }

    assert not candidate_model_files


def test_orm_at0_model_has_no_runtime_or_transaction_behavior_when_present():
    if not MODEL_PATH.exists():
        return

    tree = _parse(MODEL_PATH)
    forbidden_imports = {"alembic", "asyncpg", "fastapi", "os"}
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
    }

    assert forbidden_imports.isdisjoint(_imported_roots(tree))
    assert forbidden_calls.isdisjoint(_called_names(tree))
    assert "relationship" not in _called_names(tree)


def test_orm_at0_unapproved_persistence_artifacts_remain_absent():
    forbidden_paths = {
        INFRASTRUCTURE_DIR / "migration.py",
        INFRASTRUCTURE_DIR / "migrations.py",
        INFRASTRUCTURE_DIR / "sqlalchemy_unit_of_work.py",
    }

    assert not {path for path in forbidden_paths if path.exists()}


def test_orm_at0_contract_inventory_is_complete():
    tree = _parse(CONTRACT_PATH)
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "ORM_SCENARIOS"
            for target in node.targets
        )
    )
    scenarios = set(ast.literal_eval(assignment.value))
    required = {
        "AUDIT-001",
        "BOUNDARY-001",
        "ENUM-001",
        "INDEX-001",
        "LEAK-001",
        "MEMBER-NO-001",
        "NULL-001",
        "TABLE-001",
        "UUID-001",
        "UUID-002",
        "VERSION-001",
        "VERSION-002",
    }

    assert scenarios == required


def test_orm_at0_tests_have_no_bypass_or_dynamic_model():
    forbidden_marks = {"skip", "skipif", "xfail"}
    forbidden_classes = {"FakeMemberOrmModel", "InMemoryMemberOrmModel"}

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
        assert forbidden_classes.isdisjoint(declared_classes)
