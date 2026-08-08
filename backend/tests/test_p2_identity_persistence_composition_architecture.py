import ast
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
COMPOSITION_DIR = BACKEND_ROOT / "app" / "composition"
COMPOSITION_PATH = COMPOSITION_DIR / "identity_persistence.py"
CONTRACT_PATH = (
    BACKEND_ROOT / "tests" / "test_p2_identity_persistence_composition_contract.py"
)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


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


def _imported_roots(tree: ast.AST) -> set[str]:
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".", 1)[0])
    return roots


def test_composition_at0_target_location_is_exclusive():
    if not COMPOSITION_DIR.exists():
        return

    candidates = {
        path.name
        for path in COMPOSITION_DIR.glob("*identity*persistence*.py")
        if path != COMPOSITION_PATH
    }
    assert not candidates


def test_composition_at0_future_root_does_not_create_engine_or_connect():
    if not COMPOSITION_PATH.exists():
        return

    tree = _parse(COMPOSITION_PATH)
    forbidden_calls = {
        "connect",
        "create_async_engine",
        "create_engine",
        "execute",
        "get_session_factory",
        "get_settings",
    }

    assert {"alembic", "asyncpg", "os"}.isdisjoint(_imported_roots(tree))
    assert forbidden_calls.isdisjoint(_called_names(tree))


def test_composition_at0_future_root_has_no_sync_bridge_or_global_runtime():
    if not COMPOSITION_PATH.exists():
        return

    tree = _parse(COMPOSITION_PATH)
    forbidden_calls = {"run", "run_in_executor", "run_until_complete", "to_thread"}
    assigned_names = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    assert forbidden_calls.isdisjoint(_called_names(tree))
    assert not {
        name
        for name in assigned_names
        if "ENGINE" in name or "SESSION" in name or "UOW" in name
    }


def test_composition_at0_contract_inventory_is_frozen():
    tree = _parse(CONTRACT_PATH)
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "COMPOSITION_SCENARIOS"
            for target in node.targets
        )
    )

    assert set(ast.literal_eval(assignment.value)) == {
        "FACTORY-001",
        "ISOLATION-001",
        "LEAK-001",
        "REPOSITORY-001",
        "UOW-001",
    }


def test_composition_at0_contract_has_no_database_or_bypass():
    tree = _parse(CONTRACT_PATH)
    source = CONTRACT_PATH.read_text(encoding="utf-8")
    class_names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
    }

    assert {"alembic", "asyncpg"}.isdisjoint(_imported_roots(tree))
    assert "FakeIdentityPersistenceComposition" not in class_names
    assert "InMemoryIdentityPersistenceComposition" not in class_names
    assert "sys.modules" not in source
    assert "pytest.mark.skip" not in source
    assert "pytest.mark.xfail" not in source


def test_composition_at0_migration_and_member_roundtrip_remain_closed():
    forbidden = {
        BACKEND_ROOT / "app" / "modules" / "member" / "infrastructure" / "migration.py",
        BACKEND_ROOT / "tests" / "integration" / "test_p2_identity_member_repository_real_db.py",
    }
    assert not {path for path in forbidden if path.exists()}


def test_registration_orchestrator_composition_uses_identity_application_boundary():
    source = COMPOSITION_PATH.read_text(encoding="utf-8")
    assert "create_registration_orchestrator" in source
    assert "RegistrationOutboxWorkerUnitOfWork" not in source
    assert "MIGRATION" not in source
    assert "credential" not in source.lower()
    assert "get_settings" not in source


def test_registration_orchestrator_keeps_three_transaction_boundaries_separate():
    source = COMPOSITION_PATH.read_text(encoding="utf-8")
    assert "member_no_allocation_unit_of_work" in source
    assert "registration_bootstrap_unit_of_work" in source
    assert "RegistrationOutboxWorkerUnitOfWork" not in source
    assert "Combined" not in source
    assert "GlobalRegistrationUnitOfWork" not in source
