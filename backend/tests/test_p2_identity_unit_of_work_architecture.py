import ast
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MEMBER_DIR = BACKEND_ROOT / "app" / "modules" / "member"
APPLICATION_PATH = MEMBER_DIR / "application" / "unit_of_work.py"
INFRASTRUCTURE_PATH = MEMBER_DIR / "infrastructure" / "unit_of_work.py"
CONTRACT_PATH = BACKEND_ROOT / "tests" / "test_p2_identity_unit_of_work_contract.py"


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


def test_uow_at0_domain_and_application_ports_have_no_persistence_runtime():
    forbidden = {"alembic", "asyncpg", "fastapi", "sqlalchemy"}
    paths = list(MEMBER_DIR.glob("*.py"))
    if APPLICATION_PATH.exists():
        paths.append(APPLICATION_PATH)

    imported = set()
    for path in paths:
        imported.update(_imported_roots(_parse(path)))

    assert forbidden.isdisjoint(imported)


def test_uow_at0_future_adapter_has_no_engine_or_database_factory():
    if not INFRASTRUCTURE_PATH.exists():
        return

    tree = _parse(INFRASTRUCTURE_PATH)
    forbidden_imports = {"alembic", "asyncpg", "os"}
    forbidden_calls = {
        "async_sessionmaker",
        "connect",
        "create_async_engine",
        "create_engine",
        "execute",
        "sessionmaker",
    }

    assert forbidden_imports.isdisjoint(_imported_roots(tree))
    assert forbidden_calls.isdisjoint(_called_names(tree))


def test_uow_at0_future_adapter_has_no_sync_bridge():
    if not INFRASTRUCTURE_PATH.exists():
        return

    forbidden = {"run", "run_in_executor", "run_until_complete", "to_thread"}
    assert forbidden.isdisjoint(_called_names(_parse(INFRASTRUCTURE_PATH)))


def test_uow_at0_contract_inventory_and_test_doubles_are_frozen():
    tree = _parse(CONTRACT_PATH)
    classes = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
    }
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "UOW_SCENARIOS"
            for target in node.targets
        )
    )
    scenarios = set(ast.literal_eval(assignment.value))

    assert {
        "AsyncSessionFactorySpy",
        "AsyncSessionSpy",
        "FailureSentinel",
        "MemberRepositoryFactorySpy",
    }.issubset(classes)
    assert scenarios == {
        "CANCEL-001",
        "ERROR-001",
        "LIFECYCLE-001",
        "PRIMARY-001",
        "STATE-001",
        "TX-COMMIT",
        "TX-ROLLBACK",
    }


def test_uow_at0_contract_has_no_bypass_or_runtime_dependency():
    tree = _parse(CONTRACT_PATH)
    source = CONTRACT_PATH.read_text(encoding="utf-8")
    forbidden_classes = {
        "FakeIdentityUnitOfWork",
        "InMemoryIdentityUnitOfWork",
        "SqlAlchemyIdentityUnitOfWork",
    }
    class_names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
    }

    assert {"alembic", "asyncpg", "sqlalchemy"}.isdisjoint(
        _imported_roots(tree)
    )
    assert forbidden_classes.isdisjoint(class_names)
    assert "sys.modules" not in source
    assert "pytest.mark.skip" not in source
    assert "pytest.mark.xfail" not in source


def test_uow_at0_migration_and_database_runtime_remain_absent():
    forbidden_paths = {
        MEMBER_DIR / "infrastructure" / "migration.py",
        MEMBER_DIR / "infrastructure" / "migrations.py",
        MEMBER_DIR / "infrastructure" / "sqlalchemy_unit_of_work.py",
    }

    assert not {path for path in forbidden_paths if path.exists()}
