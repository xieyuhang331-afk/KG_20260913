import ast
from pathlib import Path

import pytest

from tests.integration import conftest as integration_conftest


EXPECTED_HEAD = "20260807_0009"
STALE_HEAD = "20260728_0006"
REVISION_FAILURE = (
    "integration revision contract must track Alembic head "
    "20260807_0009; found stale revision 20260728_0006"
)
SCHEMA_FAILURE = (
    "pg_database must drop disposable identity schema before public reset "
    "and Alembic upgrade"
)
DROP_SENTINEL_FAILURE = "dropdb 前缺少 Sentinel 复核"
MIGRATION_ROLE_FAILURE = "特权操作前缺少 Migration current_user 校验"
INTEGRATION_ROOT = Path(__file__).resolve().parent / "integration"
WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "p2-foundation-ci.yml"
)
REVISION_CONTRACT_FILES = (
    INTEGRATION_ROOT / "conftest.py",
    INTEGRATION_ROOT / "test_fastapi_f002_e2e_real_db.py",
    INTEGRATION_ROOT / "test_fastapi_f003_health_indicator_query_real_db.py",
    INTEGRATION_ROOT / "test_fastapi_f003_health_indicator_real_db.py",
    INTEGRATION_ROOT / "test_fastapi_f004_ai_contract_real_db.py",
    INTEGRATION_ROOT / "test_fastapi_f004_health_summary_real_db.py",
    INTEGRATION_ROOT / "test_fastapi_f004_health_trend_real_db.py",
    INTEGRATION_ROOT / "test_pg_health_indicator_migration_lifecycle.py",
    INTEGRATION_ROOT / "test_pg_migrations_smoke.py",
)
ALEMBIC_VERSION_QUERY = "SELECT version_num FROM alembic_version"


def _target_contains_name(target, name):
    if isinstance(target, ast.Name):
        return target.id == name
    if isinstance(target, ast.Starred):
        return _target_contains_name(target.value, name)
    if isinstance(target, (ast.Tuple, ast.List)):
        return any(_target_contains_name(item, name) for item in target.elts)
    return False


class _ModuleAssignmentCollector(ast.NodeVisitor):
    def __init__(self, name):
        self.name = name
        self.nodes = []

    def visit_FunctionDef(self, node):
        return None

    def visit_AsyncFunctionDef(self, node):
        return None

    def visit_ClassDef(self, node):
        return None

    def visit_Assign(self, node):
        if any(_target_contains_name(target, self.name) for target in node.targets):
            self.nodes.append(node)

    def visit_AnnAssign(self, node):
        if _target_contains_name(node.target, self.name):
            self.nodes.append(node)

    def visit_AugAssign(self, node):
        if _target_contains_name(node.target, self.name):
            self.nodes.append(node)


def _is_alembic_version_query(node):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "pg_database"
        and node.func.attr == "fetch_value"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == ALEMBIC_VERSION_QUERY
        and not node.keywords
    )


def _has_pg_database_parameter(function):
    positional = function.args.args
    positional_defaults = {
        parameter.arg
        for parameter in positional[len(positional) - len(function.args.defaults) :]
    }
    for parameter in positional:
        if parameter.arg == "pg_database":
            return parameter.arg not in positional_defaults
    for parameter, default in zip(function.args.kwonlyargs, function.args.kw_defaults):
        if parameter.arg == "pg_database":
            return default is None
    return False


class _ScopeBindingCollector(ast.NodeVisitor):
    def __init__(self, name):
        self.name = name
        self.nodes = []

    def _record_target(self, node, target):
        if _target_contains_name(target, self.name):
            self.nodes.append(node)

    def visit_FunctionDef(self, node):
        if node.name == self.name:
            self.nodes.append(node)
        self.visit(node.args)
        for decorator in node.decorator_list:
            self.visit(decorator)
        if node.returns is not None:
            self.visit(node.returns)

    def visit_AsyncFunctionDef(self, node):
        if node.name == self.name:
            self.nodes.append(node)
        self.visit(node.args)
        for decorator in node.decorator_list:
            self.visit(decorator)
        if node.returns is not None:
            self.visit(node.returns)

    def visit_ClassDef(self, node):
        if node.name == self.name:
            self.nodes.append(node)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        for decorator in node.decorator_list:
            self.visit(decorator)

    def visit_Lambda(self, node):
        self.visit(node.args)

    def visit_ListComp(self, node):
        self.generic_visit(node)

    def visit_SetComp(self, node):
        self.generic_visit(node)

    def visit_DictComp(self, node):
        self.generic_visit(node)

    def visit_GeneratorExp(self, node):
        self.generic_visit(node)

    def visit_Assign(self, node):
        for target in node.targets:
            self._record_target(node, target)
        self.visit(node.value)

    def visit_AnnAssign(self, node):
        self._record_target(node, node.target)
        if node.value is not None:
            self.visit(node.value)

    def visit_AugAssign(self, node):
        self._record_target(node, node.target)
        self.visit(node.value)

    def visit_NamedExpr(self, node):
        self._record_target(node, node.target)
        self.visit(node.value)

    def visit_For(self, node):
        self._record_target(node, node.target)
        self.generic_visit(node)

    def visit_AsyncFor(self, node):
        self._record_target(node, node.target)
        self.generic_visit(node)

    def visit_With(self, node):
        for item in node.items:
            if item.optional_vars is not None:
                self._record_target(node, item.optional_vars)
        self.generic_visit(node)

    def visit_AsyncWith(self, node):
        self.visit_With(node)

    def visit_ExceptHandler(self, node):
        if node.name == self.name:
            self.nodes.append(node)
        self.generic_visit(node)

    def visit_Import(self, node):
        for alias in node.names:
            if (alias.asname or alias.name.split(".", 1)[0]) == self.name:
                self.nodes.append(node)

    def visit_ImportFrom(self, node):
        for alias in node.names:
            if alias.name == "*" or (alias.asname or alias.name) == self.name:
                self.nodes.append(node)

    def visit_Delete(self, node):
        for target in node.targets:
            self._record_target(node, target)

    def visit_MatchAs(self, node):
        if node.name == self.name:
            self.nodes.append(node)
        self.generic_visit(node)

    def visit_MatchStar(self, node):
        if node.name == self.name:
            self.nodes.append(node)

    def visit_MatchMapping(self, node):
        if node.rest == self.name:
            self.nodes.append(node)
        self.generic_visit(node)

    def visit_Global(self, node):
        if self.name in node.names:
            self.nodes.append(node)

    def visit_Nonlocal(self, node):
        if self.name in node.names:
            self.nodes.append(node)


def _scope_bindings(statements, name):
    collector = _ScopeBindingCollector(name)
    for statement in statements:
        collector.visit(statement)
    return collector.nodes


def _function_locally_binds_name(function, name):
    parameters = (
        function.args.posonlyargs
        + function.args.args
        + function.args.kwonlyargs
    )
    parameter_names = {parameter.arg for parameter in parameters}
    if function.args.vararg is not None:
        parameter_names.add(function.args.vararg.arg)
    if function.args.kwarg is not None:
        parameter_names.add(function.args.kwarg.arg)
    return name in parameter_names or bool(_scope_bindings(function.body, name))


class _FunctionSuspensionCollector(ast.NodeVisitor):
    def __init__(self):
        self.found = False

    def visit_FunctionDef(self, node):
        return None

    def visit_AsyncFunctionDef(self, node):
        return None

    def visit_Lambda(self, node):
        return None

    def visit_Yield(self, node):
        self.found = True

    def visit_YieldFrom(self, node):
        self.found = True


class _FunctionReturnCollector(ast.NodeVisitor):
    def __init__(self):
        self.found = False

    def visit_FunctionDef(self, node):
        return None

    def visit_AsyncFunctionDef(self, node):
        return None

    def visit_Lambda(self, node):
        return None

    def visit_Return(self, node):
        self.found = True


def _is_synchronous_undecorated_function(function):
    if not isinstance(function, ast.FunctionDef) or function.decorator_list:
        return False
    collector = _FunctionSuspensionCollector()
    for statement in function.body:
        collector.visit(statement)
    return not collector.found


def _returns_before(function, statement):
    collector = _FunctionReturnCollector()
    for candidate in function.body:
        if candidate is statement:
            break
        collector.visit(candidate)
    return collector.found


def _directly_calls_helper(test_function, module, helper_name):
    if (
        not _is_synchronous_undecorated_function(test_function)
        or not _has_pg_database_parameter(test_function)
        or _function_locally_binds_name(test_function, helper_name)
        or _scope_bindings(test_function.body, "pg_database")
    ):
        return False
    module_bindings = _scope_bindings(module.body, test_function.name)
    if len(module_bindings) != 1 or module_bindings[0] is not test_function:
        return False
    if not test_function.body:
        return False
    statement = test_function.body[0]
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Call)
        and isinstance(statement.value.func, ast.Name)
        and statement.value.func.id == helper_name
        and len(statement.value.args) == 1
        and isinstance(statement.value.args[0], ast.Name)
        and statement.value.args[0].id == "pg_database"
        and not statement.value.keywords
    )


def _is_revision_assertion_owner(function, module, module_functions):
    if not _has_pg_database_parameter(function):
        return False
    if not _is_synchronous_undecorated_function(function):
        return False
    if _scope_bindings(function.body, "pg_database"):
        return False
    module_bindings = _scope_bindings(module.body, function.name)
    if len(module_bindings) != 1 or module_bindings[0] is not function:
        return False
    if function.name.startswith("test_"):
        return True
    return any(
        candidate.name.startswith("test_")
        and _directly_calls_helper(candidate, module, function.name)
        for candidate in module_functions
    )


def _is_exact_revision_assertion(function, statement):
    if (
        not isinstance(statement, ast.Assert)
        or not isinstance(statement.test, ast.Compare)
        or len(statement.test.ops) != 1
        or not isinstance(statement.test.ops[0], ast.Eq)
        or len(statement.test.comparators) != 1
        or not isinstance(statement.test.comparators[0], ast.Constant)
        or statement.test.comparators[0].value != EXPECTED_HEAD
    ):
        return False
    if not function.name.startswith("test_") and function.body[0] is not statement:
        return False
    if _returns_before(function, statement):
        return False

    left = statement.test.left
    if _is_alembic_version_query(left):
        return True
    if not isinstance(left, ast.Name) or left.id != "revision":
        return False
    assignments = _scope_bindings(function.body, "revision")
    return (
        len(assignments) == 1
        and isinstance(assignments[0], ast.Assign)
        and len(assignments[0].targets) == 1
        and isinstance(assignments[0].targets[0], ast.Name)
        and assignments[0].targets[0].id == "revision"
        and _is_alembic_version_query(assignments[0].value)
        and assignments[0].lineno < statement.lineno
    )


def test_integration_revision_contract_tracks_identity_member_head():
    violations = []
    for index, path in enumerate(REVISION_CONTRACT_FILES):
        source = path.read_text(encoding="utf-8")
        module = ast.parse(source)
        if STALE_HEAD in source:
            violations.append(path)

        if index == 0:
            assignments = _scope_bindings(module.body, "REQUIRED_HEAD_REVISION")
            valid_assignment = (
                len(assignments) == 1
                and isinstance(assignments[0], ast.Assign)
                and len(assignments[0].targets) == 1
                and isinstance(assignments[0].targets[0], ast.Name)
                and isinstance(assignments[0].value, ast.Constant)
                and assignments[0].value.value == EXPECTED_HEAD
            )
            if not valid_assignment:
                violations.append(path)
            continue

        module_functions = [
            node
            for node in module.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        function_names = [function.name for function in module_functions]
        if len(function_names) != len(set(function_names)):
            violations.append(path)
            continue

        exact_revision_assertions = [
            statement
            for function in module_functions
            if _is_revision_assertion_owner(function, module, module_functions)
            for statement in function.body
            if _is_exact_revision_assertion(function, statement)
        ]
        if len(exact_revision_assertions) != 1:
            violations.append(path)

    assert not violations, REVISION_FAILURE


def test_pg_database_resets_identity_schema_before_upgrade():
    conftest_path = INTEGRATION_ROOT / "conftest.py"
    module = ast.parse(conftest_path.read_text(encoding="utf-8"))
    pg_database = next(
        node
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "pg_database"
    )

    events = []
    for statement in pg_database.body:
        if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
            continue
        node = statement.value
        if isinstance(node.func, ast.Name) and node.func.id == "validate_database_sentinel":
            events.append((node.lineno, "sentinel"))
            continue
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "database"
            and node.func.attr == "execute"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            sql = node.args[0].value
            if sql == "DROP SCHEMA IF EXISTS identity CASCADE":
                events.append((node.lineno, "drop_identity"))
            elif sql == "DROP SCHEMA IF EXISTS public CASCADE":
                events.append((node.lineno, "drop_public"))
            elif sql == "CREATE SCHEMA public":
                events.append((node.lineno, "create_public"))
            continue
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "command"
            and node.func.attr == "upgrade"
            and len(node.args) == 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "head"
        ):
            events.append((node.lineno, "upgrade_head"))

    ordered_events = [event for _, event in sorted(events)]
    assert ordered_events == [
        "sentinel",
        "drop_identity",
        "drop_public",
        "create_public",
        "upgrade_head",
    ], SCHEMA_FAILURE


def _workflow_step_block(step_name):
    lines = WORKFLOW_PATH.read_text(encoding="utf-8").splitlines()
    marker = f"      - name: {step_name}"
    start = lines.index(marker)
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("      - name: ")
            or (
                lines[index].startswith("  ")
                and not lines[index].startswith("    ")
            )
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def _workflow_job_block(job_name):
    lines = WORKFLOW_PATH.read_text(encoding="utf-8").splitlines()
    job_start = lines.index(f"  {job_name}:")
    job_end = next(
        (
            index
            for index in range(job_start + 1, len(lines))
            if lines[index].startswith("  ")
            and not lines[index].startswith("    ")
        ),
        len(lines),
    )
    return "\n".join(lines[job_start:job_end])


def _workflow_shell_lines(step_name):
    block = _workflow_step_block(step_name).splitlines()
    run_start = block.index("        run: |")
    return [
        line.strip()
        for line in block[run_start + 1 :]
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_disposable_database_drop_revalidates_current_run_sentinel():
    step_block = _workflow_step_block("Drop disposable test database")
    step_block_lines = step_block.splitlines()
    run_start = step_block_lines.index("        run: |")
    step_metadata = [
        line
        for line in step_block_lines[: run_start + 1]
        if line.strip() and not line.lstrip().startswith("#")
    ]
    expected_metadata = [
        "      - name: Drop disposable test database",
        "        if: always()",
        "        shell: bash",
        "        run: |",
    ]
    shell_lines = _workflow_shell_lines("Drop disposable test database")
    expected_shell_lines = [
        "set -euo pipefail",
        'export PGPASSWORD="ci-lifecycle-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"',
        'expected_name="kg_it_${KG_TEST_RUN_ID}"',
        'if [[ "$KG_TEST_ENVIRONMENT" != "ci_ephemeral" '
        '|| "$KG_DATABASE_NAME" != "$expected_name" ]]; then',
        "echo 'Refusing to drop a database outside the current ephemeral test run.'",
        "exit 1",
        "fi",
        'expected_sentinel="kg-test-disposable:${KG_TEST_RUN_ID}"',
        'sentinel_matches="$(',
        "psql \\",
        '--host="$KG_DATABASE_HOST" \\',
        '--port="$KG_DATABASE_PORT" \\',
        "--username=postgres \\",
        "--dbname=postgres \\",
        "--tuples-only \\",
        "--no-align \\",
        "--set=ON_ERROR_STOP=1 \\",
        '--set=database_name="$KG_DATABASE_NAME" \\',
        '--set=expected_sentinel="$expected_sentinel" <<\'SQL\'',
        "SELECT CASE",
        "WHEN shobj_description(oid, 'pg_database') = :'expected_sentinel'",
        "THEN 'true'",
        "ELSE 'false'",
        "END",
        "FROM pg_database",
        "WHERE datname = :'database_name';",
        "SQL",
        ')"',
        'if [[ "$sentinel_matches" != "true" ]]; then',
        "echo 'Refusing to drop a database without the current disposable sentinel.'",
        "exit 1",
        "fi",
        "dropdb \\",
        "--if-exists \\",
        "--force \\",
        '--host="$KG_DATABASE_HOST" \\',
        '--port="$KG_DATABASE_PORT" \\',
        "--username=postgres \\",
        '"$KG_DATABASE_NAME"',
    ]

    contract_holds = (
        step_metadata == expected_metadata
        and shell_lines == expected_shell_lines
    )
    if not contract_holds:
        pytest.fail(DROP_SENTINEL_FAILURE, pytrace=False)


def test_migration_fixture_verifies_connected_role_before_privileged_actions(
    monkeypatch,
):
    events = []
    connected_role = {"value": ""}

    class FakeMigrationDatabase:
        def fetch_value(self, sql):
            if "shobj_description" in sql:
                events.append("fetch_sentinel")
                return "kg-test-disposable:gh_test_run"
            if "current_user" in sql:
                events.append("fetch_current_user")
                return connected_role["value"]
            if "version_num" in sql:
                events.append("fetch_revision")
                return EXPECTED_HEAD
            raise AssertionError(f"unexpected query: {sql}")

        def execute(self, sql):
            events.append(f"privileged:{sql}")

    monkeypatch.setenv("KG_TEST_ROLE_SEPARATION", "1")
    monkeypatch.delenv("KG_TEST_LIFECYCLE_DATABASE_URL", raising=False)
    monkeypatch.delenv("KG_TEST_LIFECYCLE_PASSWORD", raising=False)
    monkeypatch.setattr(
        integration_conftest,
        "_get_test_database_target",
        lambda: ("postgresql+asyncpg://test.invalid/kg_it_gh_test_run", object()),
    )
    monkeypatch.setattr(
        integration_conftest,
        "_get_test_database_url",
        lambda: "postgresql+asyncpg://test.invalid/kg_it_gh_test_run",
    )
    monkeypatch.setattr(
        integration_conftest,
        "PgDatabase",
        lambda _database_url: FakeMigrationDatabase(),
    )
    monkeypatch.setattr(
        integration_conftest,
        "validate_database_sentinel",
        lambda _actual, _target: events.append("validate_sentinel"),
    )
    monkeypatch.setattr(
        integration_conftest.command,
        "upgrade",
        lambda *_args: events.append("privileged:upgrade"),
    )
    monkeypatch.setattr(
        integration_conftest,
        "_grant_test_role_permissions",
        lambda _database: events.append("privileged:grant"),
    )

    def exercise(*, application_role, migration_role, readonly_role, connected):
        events.clear()
        connected_role["value"] = connected
        monkeypatch.setenv("KG_TEST_APPLICATION_ROLE", application_role)
        monkeypatch.setenv("KG_TEST_MIGRATION_ROLE", migration_role)
        monkeypatch.setenv("KG_TEST_READONLY_ROLE", readonly_role)
        fixture = integration_conftest.pg_database.__wrapped__()
        failure = None
        try:
            next(fixture)
        except RuntimeError as exc:
            failure = exc
        finally:
            fixture.close()
        return failure, tuple(events)

    mismatched_failure, mismatched_events = exercise(
        application_role="kg_ci_app_test_run",
        migration_role="kg_ci_migration_test_run",
        readonly_role="kg_ci_readonly_test_run",
        connected="unexpected_privileged_role",
    )
    role_guard_cases = (
        (
            "application equals migration",
            "kg_ci_migration_test_run",
            "kg_ci_migration_test_run",
            "kg_ci_readonly_test_run",
            "database validation roles must be distinct",
        ),
        (
            "application equals readonly",
            "kg_ci_readonly_test_run",
            "kg_ci_migration_test_run",
            "kg_ci_readonly_test_run",
            "database validation roles must be distinct",
        ),
        (
            "migration equals readonly",
            "kg_ci_app_test_run",
            "kg_ci_readonly_test_run",
            "kg_ci_readonly_test_run",
            "database validation roles must be distinct",
        ),
        (
            "application is postgres",
            "postgres",
            "kg_ci_migration_test_run",
            "kg_ci_readonly_test_run",
            "database validation roles must not use postgres",
        ),
        (
            "migration is postgres",
            "kg_ci_app_test_run",
            "postgres",
            "kg_ci_readonly_test_run",
            "database validation roles must not use postgres",
        ),
        (
            "readonly is postgres",
            "kg_ci_app_test_run",
            "kg_ci_migration_test_run",
            "postgres",
            "database validation roles must not use postgres",
        ),
        (
            "application role name is unsafe",
            "Unsafe-Application-Role",
            "kg_ci_migration_test_run",
            "kg_ci_readonly_test_run",
            "KG_TEST_APPLICATION_ROLE must contain a safe PostgreSQL role name",
        ),
        (
            "migration role name is unsafe",
            "kg_ci_app_test_run",
            "Unsafe-Migration-Role",
            "kg_ci_readonly_test_run",
            "KG_TEST_MIGRATION_ROLE must contain a safe PostgreSQL role name",
        ),
        (
            "readonly role name is unsafe",
            "kg_ci_app_test_run",
            "kg_ci_migration_test_run",
            "Unsafe-Readonly-Role",
            "KG_TEST_READONLY_ROLE must contain a safe PostgreSQL role name",
        ),
    )
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    integration_step = _workflow_step_block(
        "Run PostgreSQL integration and migration tests"
    )
    backend_integration_job = _workflow_job_block("backend-integration")
    lifecycle_steps = tuple(
        _workflow_step_block(step_name)
        for step_name in (
            "Create disposable test database",
            "Provision isolated validation roles",
            "Drop disposable test database",
        )
    )
    lifecycle_export = (
        'export PGPASSWORD="ci-lifecycle-${GITHUB_RUN_ID}-'
        '${GITHUB_RUN_ATTEMPT}"'
    )

    violations = []
    if not (
        mismatched_failure is not None
        and str(mismatched_failure)
        == "migration database URL role does not match KG_TEST_MIGRATION_ROLE"
        and mismatched_events[:3]
        == ("fetch_sentinel", "validate_sentinel", "fetch_current_user")
        and not any(
            event.startswith("privileged:") for event in mismatched_events
        )
    ):
        violations.append("connected migration role mismatch")
    for (
        case_name,
        application_role,
        migration_role,
        readonly_role,
        expected_error,
    ) in role_guard_cases:
        failure, case_events = exercise(
            application_role=application_role,
            migration_role=migration_role,
            readonly_role=readonly_role,
            connected=migration_role,
        )
        if not (
            failure is not None
            and str(failure) == expected_error
            and not any(
                event.startswith("privileged:") for event in case_events
            )
        ):
            violations.append(case_name)
    if any(
        forbidden in workflow
        for forbidden in (
            "KG_TEST_LIFECYCLE_DATABASE_URL",
            "KG_TEST_LIFECYCLE_PASSWORD",
        )
    ):
        violations.append("lifecycle URL or password environment")
    if (
        "GITHUB_ENV" in backend_integration_job
        or backend_integration_job.count("PGPASSWORD") != 3
        or backend_integration_job.count(lifecycle_export) != 3
        or any(step.count(lifecycle_export) != 1 for step in lifecycle_steps)
        or any(
            forbidden in integration_step
            for forbidden in ("PGPASSWORD", "GITHUB_ENV", "ci-lifecycle-")
        )
    ):
        violations.append("lifecycle credential reaches pytest")

    if violations:
        pytest.fail(MIGRATION_ROLE_FAILURE, pytrace=False)
