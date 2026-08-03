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


def test_ct0_behavioral_expansion_scenario_inventory_is_complete():
    contract_path = TEST_DIR / "test_p2_member_concrete_adapter_contract.py"
    tree = _parse(contract_path)
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "BEHAVIOR_SCENARIOS"
            for target in node.targets
        )
    )
    scenarios = set(ast.literal_eval(assignment.value))
    required = {
        "ASYNC-001",
        "ASYNC-002",
        "ERR-001",
        "ERR-002",
        "ERR-003",
        "ERR-004",
        "ERR-005",
        "ERR-006",
        "LEAK-001",
        "LEAK-002",
        "MAP-001",
        "MAP-002",
        "MAP-003",
        "MAP-004",
        "MAP-005",
        "TX-001",
        "TX-002",
        "TX-003",
        "TX-004",
        "UUID-001",
        "UUID-002",
        "UUID-003",
    }

    assert scenarios == required


def test_ct0_behavioral_test_doubles_do_not_become_repository_implementations():
    contract_path = TEST_DIR / "test_p2_member_concrete_adapter_contract.py"
    tree = _parse(contract_path)
    forbidden_imports = {"alembic", "asyncpg", "fastapi", "sqlalchemy"}
    forbidden_classes = {
        "FakeMemberRepository",
        "InMemoryMemberRepository",
        "SqlAlchemyMemberRepository",
    }
    required_test_doubles = {
        "FailureSentinel",
        "MapperSpy",
        "PersistenceStateStub",
        "ResultStub",
        "SessionSpy",
        "ThirdPartyUuidStub",
    }
    imported_roots = set()
    class_names = set()
    base_names = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(
                alias.name.split(".", 1)[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            imported_roots.add((node.module or "").split(".", 1)[0])
        elif isinstance(node, ast.ClassDef):
            class_names.add(node.name)
            for base in node.bases:
                if isinstance(base, ast.Name):
                    base_names.add(base.id)
                elif isinstance(base, ast.Attribute):
                    base_names.add(base.attr)

    source = contract_path.read_text(encoding="utf-8")

    assert forbidden_imports.isdisjoint(imported_roots)
    assert forbidden_classes.isdisjoint(class_names)
    assert required_test_doubles.issubset(class_names)
    assert "MemberRepository" not in base_names
    assert "sys.modules" not in source
    assert "pytest.mark.skip" not in source
    assert "pytest.mark.xfail" not in source
