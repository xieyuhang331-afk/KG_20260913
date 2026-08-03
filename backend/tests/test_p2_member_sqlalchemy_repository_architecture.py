import ast
import inspect
from pathlib import Path

from app.modules.member.repository import MemberRepository


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MEMBER_DIR = BACKEND_ROOT / "app" / "modules" / "member"
INFRASTRUCTURE_DIR = MEMBER_DIR / "infrastructure"
CONTRACT_PATH = (
    BACKEND_ROOT
    / "tests"
    / "test_p2_member_sqlalchemy_repository_contract.py"
)
OPERATIONS = {
    "add",
    "get_by_id",
    "get_by_member_no",
    "is_member_no_available",
    "save",
}
FORBIDDEN_IMPORTS = {
    "alembic",
    "asyncpg",
    "fastapi",
    "sqlalchemy",
}
FORBIDDEN_REPOSITORY_CLASSES = {
    "FakeMemberRepository",
    "InMemoryMemberRepository",
    "SqlAlchemyMemberRepository",
}
FORBIDDEN_SYNC_BRIDGES = {
    "run",
    "run_in_executor",
    "run_until_complete",
    "to_thread",
}
REQUIRED_TEST_DOUBLES = {
    "AsyncSessionSpy",
    "FailureSentinel",
    "MapperSpy",
    "PersistenceStateStub",
    "ResultStub",
    "ThirdPartyUuidStub",
}
REQUIRED_SCENARIOS = {
    "ASYNC-001",
    "ERR-GENERIC",
    "ERR-INVALID-ID",
    "ERR-NOT-FOUND",
    "ERR-UNAVAILABLE",
    "ERR-UNIQUE",
    "ERR-VERSION",
    "LEAK-001",
    "MAP-ADD",
    "MAP-READ",
    "MAP-SAVE",
    "TX-COMMAND",
    "TX-QUERY",
    "UUID-001",
}


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


def test_at0_member_repository_contract_remains_async():
    for operation in OPERATIONS:
        assert inspect.iscoroutinefunction(
            getattr(MemberRepository, operation)
        )


def test_at0_missing_production_adapter_does_not_fail_layout_guard():
    if not INFRASTRUCTURE_DIR.exists():
        return

    allowed_files = {
        "__init__.py",
        "mapper.py",
        "models.py",
        "orm_state_mapper.py",
        "sqlalchemy_repository.py",
        "sqlalchemy_unit_of_work.py",
    }
    actual_files = {
        path.name for path in INFRASTRUCTURE_DIR.glob("*.py")
    }
    assert actual_files.issubset(allowed_files)


def test_at0_domain_and_repository_port_have_no_persistence_dependencies():
    imported_roots = set()
    for path in MEMBER_DIR.glob("*.py"):
        imported_roots.update(_imported_roots(_parse(path)))

    assert FORBIDDEN_IMPORTS.isdisjoint(imported_roots)


def test_at0_contract_test_has_required_inventory_and_test_doubles():
    tree = _parse(CONTRACT_PATH)
    class_names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
    }
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "BEHAVIOR_SCENARIOS"
            for target in node.targets
        )
    )
    scenarios = set(ast.literal_eval(assignment.value))

    assert REQUIRED_TEST_DOUBLES.issubset(class_names)
    assert REQUIRED_SCENARIOS.issubset(scenarios)


def test_at0_contract_test_has_no_forbidden_runtime_or_repository():
    tree = _parse(CONTRACT_PATH)
    source = CONTRACT_PATH.read_text(encoding="utf-8")
    class_names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
    }
    base_names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            if isinstance(base, ast.Name):
                base_names.add(base.id)
            elif isinstance(base, ast.Attribute):
                base_names.add(base.attr)

    assert FORBIDDEN_IMPORTS.isdisjoint(_imported_roots(tree))
    assert FORBIDDEN_REPOSITORY_CLASSES.isdisjoint(class_names)
    assert "MemberRepository" not in base_names
    assert FORBIDDEN_SYNC_BRIDGES.isdisjoint(_called_names(tree))
    assert "sys.modules" not in source
    assert "pytest.mark.skip" not in source
    assert "pytest.mark.xfail" not in source


def test_at0_contract_test_asserts_transaction_and_leakage_boundaries():
    tree = _parse(CONTRACT_PATH)
    source = CONTRACT_PATH.read_text(encoding="utf-8")
    required_names = {
        "LEAKAGE_ATTRIBUTES",
        "OPERATIONS",
        "TRANSACTION_FINALIZERS",
    }
    assigned_names = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    assert required_names.issubset(assigned_names)
    assert '"commit"' in source
    assert '"rollback"' in source
    assert '"close"' in source
    assert "assertIs" in source
