import ast
import base64
import json
import secrets
import subprocess
from pathlib import Path

import pytest

from tests.integration import conftest as integration_conftest


EXPECTED_HEAD = "20260912_0043"
STALE_HEAD = "20260816_0020"
REVISION_FAILURE = (
    "integration revision contract must track Alembic head "
    "20260912_0043; found stale revision 20260816_0020"
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
BLOCKED_P2_IDENTIFIERS = (
    "family_delegation",
    "health_fact_correction",
    "health_fact_supersession",
    "therapist_assignment",
    "professional_service_fulfillment",
)
APPROVED_SLICE3_COMPOUND_IDENTIFIERS = (
    "primary_therapist_assignment",
    "slice3_therapist_assignment_read_v1",
    "get_primary_therapist_assignment",
)


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


def test_repository_safety_uses_word_boundaries_without_weakening_blocked_p2_decisions(
    tmp_path,
):
    pattern = "|".join(BLOCKED_P2_IDENTIFIERS)
    command = f"git grep -I -n -w -E '{pattern}' -- backend/app frontend/src"
    step = _workflow_step_block("Enforce blocked P2 decisions")

    assert command in step

    backend = tmp_path / "backend" / "app"
    frontend = tmp_path / "frontend" / "src"
    backend.mkdir(parents=True)
    frontend.mkdir(parents=True)
    (backend / "approved_slice3.py").write_text(
        "\n".join(APPROVED_SLICE3_COMPOUND_IDENTIFIERS), encoding="utf-8"
    )
    (frontend / "blocked_p2.ts").write_text(
        "\n".join(BLOCKED_P2_IDENTIFIERS), encoding="utf-8"
    )
    subprocess.run(
        ["git", "init", "--quiet"], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "add", "--", "backend/app", "frontend/src"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    result = subprocess.run(
        ["git", "grep", "-I", "-n", "-w", "-E", pattern, "--", "backend/app", "frontend/src"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "approved_slice3.py" not in result.stdout
    assert "blocked_p2.ts" in result.stdout
    for identifier in BLOCKED_P2_IDENTIFIERS:
        assert identifier in result.stdout
    for identifier in APPROVED_SLICE3_COMPOUND_IDENTIFIERS:
        assert identifier not in result.stdout


def test_slice3_assignment_detail_uses_an_approved_compound_internal_name():
    api_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "modules"
        / "member_enrollment"
        / "api.py"
    )
    source = api_path.read_text(encoding="utf-8")

    assert "async def get_primary_therapist_assignment(" in source
    assert "async def therapist_assignment(" not in source


def test_registration_runtime_ci_uses_disposable_rabbitmq_and_exact_cleanup():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    start = _workflow_step_block("Start disposable RabbitMQ")
    cleanup = _workflow_step_block("Stop and remove disposable RabbitMQ")
    worker = _workflow_step_block(
        "Run registration Celery worker bootstrap contract"
    )

    assert "secrets.token_urlsafe(48)" in workflow
    assert 'print(f"::add-mask::{value}")' in workflow
    assert 'print(f"::add-mask::{broker_url}")' in start
    assert "KG_CELERY_BROKER_URL" in start
    assert "^kg-reg-[0-9a-f]{12}$" in start
    assert "kg.test.sentinel" in start
    assert "127.0.0.1::5672" in start
    assert "registration Celery worker bootstrap contract" in workflow
    assert "pytest-registration-worker-report.xml" in worker
    assert "        if: always()" in cleanup
    assert 'docker stop "$KG_RABBITMQ_CONTAINER_NAME"' in cleanup
    assert 'docker rm "$KG_RABBITMQ_CONTAINER_NAME"' in cleanup
    assert "actual_network_sentinel" in cleanup
    assert '{{index .Labels "kg.test.sentinel"}}' in cleanup
    assert 'docker network rm "$KG_RABBITMQ_NETWORK_NAME"' in cleanup
    assert "Disposable RabbitMQ cleanup failed." in cleanup
    assert "KG_IDENTITY_APPLICATION_DATABASE_URL" in workflow
    assert "KG_DELIVERY_WORKER_DATABASE_URL" in workflow


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
        "        env:",
        "          PGPASSWORD: ${{ github.token }}",
        "        run: |",
    ]
    shell_lines = _workflow_shell_lines("Drop disposable test database")
    expected_shell_lines = [
        "set -euo pipefail",
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
        monkeypatch.setenv(
            "KG_TEST_VERIFICATION_WRITER_ROLE", "kg_ci_writer_test_run"
        )
        monkeypatch.setenv(
            "KG_TEST_DELIVERY_WORKER_ROLE", "kg_ci_worker_test_run"
        )
        monkeypatch.setenv(
            "KG_TEST_OUTBOX_AUDIT_ROLE", "kg_ci_audit_test_run"
        )
        monkeypatch.setenv(
            "KG_TEST_HEALTH_FACT_WRITER_ROLE",
            "kg_ci_fact_writer_test_run",
        )
        monkeypatch.setenv(
            "KG_TEST_ORGANIZATION_MAPPING_WRITER_ROLE",
            "kg_ci_org_map_test_run",
        )
        monkeypatch.setenv(
            "KG_TEST_HEALTH_MAPPING_WRITER_ROLE",
            "kg_ci_health_map_test_run",
        )
        monkeypatch.setenv(
            "KG_TEST_MAPPING_AUDIT_ROLE",
            "kg_ci_map_audit_test_run",
        )
        monkeypatch.setenv(
            "KG_TEST_MAPPING_SHADOW_ROLE",
            "kg_ci_map_shadow_test_run",
        )
        monkeypatch.setenv(
            "KG_TEST_ORGANIZATION_PROJECTION_BUILDER_ROLE",
            "kg_ci_org_proj_test_run",
        )
        monkeypatch.setenv(
            "KG_TEST_HEALTH_PROJECTION_BUILDER_ROLE",
            "kg_ci_health_proj_test_run",
        )
        monkeypatch.setenv(
            "KG_TEST_PROJECTION_CONFIRMATION_ROLE",
            "kg_ci_proj_confirm_test_run",
        )
        monkeypatch.setenv("KG_TEST_ORGANIZATION_PROJECTION_SHADOW_ROLE", "kg_ci_org_shadow_test_run")
        monkeypatch.setenv("KG_TEST_HEALTH_PROJECTION_SHADOW_ROLE", "kg_ci_health_shadow_test_run")
        monkeypatch.setenv("KG_TEST_PROJECTION_READY_GATE_ROLE", "kg_ci_ready_gate_test_run")
        monkeypatch.setenv("KG_TEST_PROJECTION_SHADOW_CONFIRMATION_ROLE", "kg_ci_shadow_confirm_test_run")
        monkeypatch.setenv("KG_TEST_ORGANIZATION_PROJECTION_READER_ROLE", "kg_ci_org_reader_test_run")
        monkeypatch.setenv("KG_TEST_HEALTH_PROJECTION_READER_ROLE", "kg_ci_health_reader_test_run")
        monkeypatch.setenv("KG_TEST_INSTITUTION_ONBOARDING_WRITER_ROLE", "kg_ci_onboarding_writer_test_run")
        monkeypatch.setenv("KG_TEST_INSTITUTION_REVIEW_WRITER_ROLE", "kg_ci_review_writer_test_run")
        monkeypatch.setenv("KG_TEST_PRIVATE_FILE_WRITER_ROLE", "kg_ci_private_file_writer_test_run")
        monkeypatch.setenv(
            "KG_TEST_PRIVATE_FILE_ACCESS_WRITER_ROLE",
            "kg_ci_private_file_access_test_run",
        )
        monkeypatch.setenv("KG_TEST_INSTITUTION_ONBOARDING_READER_ROLE", "kg_ci_onboarding_reader_test_run")
        monkeypatch.setenv("KG_TEST_THERAPIST_ONBOARDING_WRITER_ROLE", "kg_ci_therapist_onboarding_test_run")
        monkeypatch.setenv("KG_TEST_THERAPIST_REVIEW_WRITER_ROLE", "kg_ci_therapist_review_test_run")
        monkeypatch.setenv("KG_TEST_THERAPIST_READINESS_WORKER_ROLE", "kg_ci_therapist_worker_test_run")
        monkeypatch.setenv("KG_TEST_THERAPIST_READER_ROLE", "kg_ci_therapist_reader_test_run")
        monkeypatch.setenv("KG_TEST_MEMBER_ENROLLMENT_WRITER_ROLE", "kg_ci_member_enrollment_test_run")
        monkeypatch.setenv("KG_TEST_MEMBER_IDENTITY_REVIEW_WRITER_ROLE", "kg_ci_member_review_test_run")
        monkeypatch.setenv("KG_TEST_MEMBER_CASE_WRITER_ROLE", "kg_ci_member_case_test_run")
        monkeypatch.setenv("KG_TEST_MEMBER_WORKFLOW_WORKER_ROLE", "kg_ci_member_worker_test_run")
        monkeypatch.setenv("KG_TEST_MEMBER_ENROLLMENT_READER_ROLE", "kg_ci_member_reader_test_run")
        monkeypatch.setenv("KG_TEST_HEALTH_RECORD_WRITER_ROLE", "kg_ci_health_record_test_run")
        monkeypatch.setenv("KG_TEST_ASSESSMENT_READINESS_WRITER_ROLE", "kg_ci_readiness_test_run")
        monkeypatch.setenv("KG_TEST_SLICE4_WORKFLOW_WORKER_ROLE", "kg_ci_slice4_worker_test_run")
        monkeypatch.setenv("KG_TEST_SLICE4_CLINICAL_READER_ROLE", "kg_ci_clinical_reader_test_run")
        monkeypatch.setenv("KG_TEST_SLICE4_INSTITUTION_READER_ROLE", "kg_ci_institution_reader_test_run")
        monkeypatch.setenv("KG_TEST_SLICE4_IDENTITY_AUTHORITY_ROLE", "kg_ci_identity_authority_test_run")
        monkeypatch.setenv("KG_TEST_SLICE5_ASSESSMENT_WRITER_ROLE", "kg_ci_slice5_assessment_test_run")
        monkeypatch.setenv("KG_TEST_SLICE5_RISK_WORKFLOW_WRITER_ROLE", "kg_ci_slice5_risk_test_run")
        monkeypatch.setenv("KG_TEST_SLICE5_RULE_GOVERNANCE_WRITER_ROLE", "kg_ci_slice5_rule_test_run")
        monkeypatch.setenv("KG_TEST_SLICE5_WORKFLOW_WORKER_ROLE", "kg_ci_slice5_worker_test_run")
        monkeypatch.setenv("KG_TEST_SLICE5_CLINICAL_READER_ROLE", "kg_ci_slice5_clinical_test_run")
        monkeypatch.setenv("KG_TEST_SLICE5_OVERSIGHT_READER_ROLE", "kg_ci_slice5_oversight_test_run")
        monkeypatch.setenv("KG_TEST_SLICE6_INSTITUTION_WRITER_ROLE", "kg_ci_slice6_institution_test_run")
        monkeypatch.setenv("KG_TEST_SLICE6_TEMPLATE_WRITER_ROLE", "kg_ci_slice6_template_test_run")
        monkeypatch.setenv("KG_TEST_SLICE6_REVIEW_WRITER_ROLE", "kg_ci_slice6_review_test_run")
        monkeypatch.setenv("KG_TEST_SLICE6_WORKFLOW_WORKER_ROLE", "kg_ci_slice6_worker_test_run")
        monkeypatch.setenv("KG_TEST_SLICE6_CLINICAL_READER_ROLE", "kg_ci_slice6_clinical_test_run")
        monkeypatch.setenv("KG_TEST_SLICE6_FAMILY_READER_ROLE", "kg_ci_slice6_family_test_run")
        monkeypatch.setenv("KG_TEST_SLICE7_MILESTONE_WRITER_ROLE", "kg_ci_slice7_milestone_test_run")
        monkeypatch.setenv("KG_TEST_SLICE7_CASE_WRITER_ROLE", "kg_ci_slice7_case_test_run")
        monkeypatch.setenv("KG_TEST_SLICE7_TRANSFER_WRITER_ROLE", "kg_ci_slice7_transfer_test_run")
        monkeypatch.setenv("KG_TEST_SLICE7_EXPORT_WORKER_ROLE", "kg_ci_slice7_export_test_run")
        monkeypatch.setenv("KG_TEST_SLICE7_FAMILY_READER_ROLE", "kg_ci_slice7_family_test_run")
        monkeypatch.setenv("KG_TEST_SLICE7_OVERSIGHT_READER_ROLE", "kg_ci_slice7_oversight_test_run")
        monkeypatch.setenv(
            "KG_TEST_A2_IDENTITY_INVENTORY_ROLE", "kg_ci_a2_inventory_test_run"
        )
        monkeypatch.setenv(
            "KG_TEST_A2_IDENTITY_REMEDIATION_WRITER_ROLE",
            "kg_ci_a2_remediation_writer_test_run",
        )
        monkeypatch.setenv(
            "KG_TEST_A2_IDENTITY_REMEDIATION_CONFIRMATION_ROLE",
            "kg_ci_a2_remediation_confirmation_test_run",
        )
        monkeypatch.setenv(
            "KG_TEST_DDL_OWNER_ROLE", "kg_ci_ddl_owner_test_run"
        )
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
        "POSTGRES_PASSWORD: ${{ github.token }}" not in backend_integration_job
        or any(
            step.count("PGPASSWORD: ${{ github.token }}") != 1
            for step in lifecycle_steps
        )
        or any(
            forbidden in integration_step
            for forbidden in ("PGPASSWORD", "GITHUB_ENV", "ci-lifecycle-")
        )
    ):
        violations.append("lifecycle credential reaches pytest")

    if violations:
        pytest.fail(MIGRATION_ROLE_FAILURE, pytrace=False)


def test_backend_integration_runtime_secrets_and_database_targets_are_masked():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    backend_unit_job = _workflow_job_block("backend-unit")
    backend_integration_job = _workflow_job_block("backend-integration")
    violations = []

    for forbidden in (
        "ci-app-${{ github.run_id }}",
        "ci-migration-${GITHUB_RUN_ID}",
        "ci-readonly-${GITHUB_RUN_ID}",
        "ci-jwt-${{ github.run_id }}",
        "KG_TEST_DATABASE_URL: postgresql+asyncpg://",
        "KG_TEST_MIGRATION_DATABASE_URL: postgresql+asyncpg://",
        "KG_TEST_READONLY_DATABASE_URL: postgresql+asyncpg://",
    ):
        if forbidden in backend_integration_job:
            violations.append(forbidden)
    for required in (
        "Prepare masked ephemeral integration configuration",
        "secrets.token_urlsafe",
        "::add-mask::",
        "GITHUB_ENV",
        "KG_TEST_VERIFICATION_WRITER_DATABASE_URL",
        "KG_TEST_DELIVERY_WORKER_DATABASE_URL",
        "KG_TEST_OUTBOX_AUDIT_DATABASE_URL",
        "KG_TEST_DDL_OWNER_ROLE",
        "Close DDL-owner window and grant runtime permissions",
        "REVOKE :\"ddl_owner_role\" FROM :\"migration_role\"",
        "Run Outbox steady-state PostgreSQL contract",
    ):
        if required not in backend_integration_job:
            violations.append(required)
    unit_mask = backend_unit_job.find("::add-mask::")
    unit_export = backend_unit_job.find("GITHUB_ENV")
    if unit_mask < 0 or unit_export < 0 or unit_mask > unit_export:
        violations.append("backend-unit secrets must be masked before export")

    assert violations == []


def test_StepUp独立Secret仅在DisposableIntegration生成并先脱敏():
    backend_unit_job = _workflow_job_block("backend-unit")
    backend_integration_job = _workflow_job_block("backend-integration")
    secret_name = "KG_IDENTITY_REVIEW_STEP_UP_SECRET_KEY"

    assert secret_name not in backend_unit_job
    assert backend_integration_job.count(f'"{secret_name}": secrets.token_urlsafe(64)') == 1
    assert f'os.environ.get("{secret_name}", "")' in backend_integration_job
    assert backend_integration_job.index(f'"{secret_name}": secrets.token_urlsafe(64)') < backend_integration_job.index(
        'print(f"::add-mask::{value}")'
    ) < backend_integration_job.index("GITHUB_ENV")


def test_StepUp消费事实权限在CI精确收紧且进入JUnit泄漏扫描():
    backend_integration_job = _workflow_job_block("backend-integration")
    revoke = (
        "REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLE "
        "public.operation_log FROM :\"application_role\";"
    )

    assert backend_integration_job.count(revoke) == 1
    assert '"step_up_secret": os.environ.get("KG_IDENTITY_REVIEW_STEP_UP_SECRET_KEY", "")' in backend_integration_job
    assert "GRANT UPDATE ON TABLE public.operation_log" not in backend_integration_job
    assert "GRANT DELETE ON TABLE public.operation_log" not in backend_integration_job


def test_VerificationWriterRuntimeURL只在DisposableIntegration注入且先脱敏():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    backend_unit_job = _workflow_job_block("backend-unit")
    backend_integration_job = _workflow_job_block("backend-integration")
    mapping = (
        'values["KG_VERIFICATION_WRITER_DATABASE_URL"] = values['
        '\n              "KG_TEST_VERIFICATION_WRITER_DATABASE_URL"\n          ]'
    )

    assert "KG_VERIFICATION_WRITER_DATABASE_URL" not in backend_unit_job
    assert backend_integration_job.count(mapping) == 1
    assert backend_integration_job.index(mapping) < backend_integration_job.index(
        "for value in ("
    )
    assert "echo $KG_VERIFICATION_WRITER_DATABASE_URL" not in workflow


def test_CI严格执行注册持久发件箱R2数据库合同并上传独立JUnit():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    node = (
        "tests/integration/test_身份注册持久发件箱数据库合同.py::"
        "test_注册持久发件箱R2权限并发恢复与对账真实往返"
    )
    assert workflow.count(node) == 1
    assert workflow.count("pytest-outbox-r2-report.xml") == 2
    assert "echo $KG_TEST_DELIVERY_WORKER_DATABASE_URL" not in workflow
    assert "set -x" not in workflow


def test_projection_migration_runtime_roles_are_explicitly_propagated_from_test_roles():
    backend_integration_job = _workflow_job_block("backend-integration")
    mappings = (
        (
            "KG_ORGANIZATION_PROJECTION_BUILDER_ROLE",
            "KG_TEST_ORGANIZATION_PROJECTION_BUILDER_ROLE",
        ),
        (
            "KG_HEALTH_PROJECTION_BUILDER_ROLE",
            "KG_TEST_HEALTH_PROJECTION_BUILDER_ROLE",
        ),
        ("KG_PROJECTION_CONFIRMATION_ROLE", "KG_TEST_PROJECTION_CONFIRMATION_ROLE"),
        ("KG_ORGANIZATION_PROJECTION_SHADOW_ROLE", "KG_TEST_ORGANIZATION_PROJECTION_SHADOW_ROLE"),
        ("KG_HEALTH_PROJECTION_SHADOW_ROLE", "KG_TEST_HEALTH_PROJECTION_SHADOW_ROLE"),
        ("KG_PROJECTION_READY_GATE_ROLE", "KG_TEST_PROJECTION_READY_GATE_ROLE"),
        ("KG_PROJECTION_SHADOW_CONFIRMATION_ROLE", "KG_TEST_PROJECTION_SHADOW_CONFIRMATION_ROLE"),
        ("KG_ORGANIZATION_PROJECTION_READER_ROLE", "KG_TEST_ORGANIZATION_PROJECTION_READER_ROLE"),
        ("KG_HEALTH_PROJECTION_READER_ROLE", "KG_TEST_HEALTH_PROJECTION_READER_ROLE"),
    )
    export_position = backend_integration_job.index("GITHUB_ENV")

    for runtime_name, test_name in mappings:
        propagation = f'values["{runtime_name}"] = values["{test_name}"]'
        assert backend_integration_job.count(propagation) == 1
        assert backend_integration_job.index(propagation) < export_position


def test_slice1_runtime_roles_and_urls_are_additively_propagated_in_ci():
    backend_integration_job = _workflow_job_block("backend-integration")
    pairs = (
        ("KG_INSTITUTION_ONBOARDING_WRITER", "KG_TEST_INSTITUTION_ONBOARDING_WRITER"),
        ("KG_INSTITUTION_REVIEW_WRITER", "KG_TEST_INSTITUTION_REVIEW_WRITER"),
        ("KG_PRIVATE_FILE_WRITER", "KG_TEST_PRIVATE_FILE_WRITER"),
        ("KG_INSTITUTION_ONBOARDING_READER", "KG_TEST_INSTITUTION_ONBOARDING_READER"),
    )
    export_position = backend_integration_job.index("GITHUB_ENV")
    for runtime_prefix, test_prefix in pairs:
        role_mapping = f'values["{runtime_prefix}_ROLE"] = values["{test_prefix}_ROLE"]'
        url_mapping = f'values["{runtime_prefix}_DATABASE_URL"] = values["{test_prefix}_DATABASE_URL"]'
        assert backend_integration_job.count(
            f'"{test_prefix}_DATABASE_URL": "{test_prefix}_ROLE"'
        ) == 1
        assert backend_integration_job.count(role_mapping) == 1
        assert backend_integration_job.count(url_mapping) == 1
        assert backend_integration_job.index(role_mapping) < export_position
        assert backend_integration_job.index(url_mapping) < export_position


def test_slice1_ephemeral_crypto_material_is_random_masked_and_exported_only_in_integration():
    backend_unit_job = _workflow_job_block("backend-unit")
    backend_integration_job = _workflow_job_block("backend-integration")
    declarations = (
        '"KG_ONBOARDING_PII_KEK_B64": base64.b64encode(',
        '"KG_ONBOARDING_PII_HMAC_KEY_B64": base64.b64encode(',
    )
    mask_position = backend_integration_job.index('print(f"::add-mask::{value}")')
    export_position = backend_integration_job.index("GITHUB_ENV")
    for declaration in declarations:
        assert declaration not in backend_unit_job
        assert backend_integration_job.count(declaration) == 1
        assert backend_integration_job.index(declaration) < mask_position < export_position

    declaration = '"KG_PRIVATE_FILE_ACCESS_SIGNING_KEY": secrets.token_urlsafe(48)'
    for block in (backend_unit_job, backend_integration_job):
        assert block.count(declaration) == 1
        declared = block.index(declaration)
        masked = block.index('print(f"::add-mask::{value}")', declared)
        exported = block.index("GITHUB_ENV", masked)
        assert declared < masked < exported
        assert 'os.environ.get("KG_PRIVATE_FILE_ACCESS_SIGNING_KEY", "")' in block


def test_slice1_database_closure_uses_an_independent_celery_worker_in_ci():
    backend_integration_job = _workflow_job_block("backend-integration")
    declaration = '"KG_RUN_SLICE1_CELERY_WORKER": "1"'
    export_position = backend_integration_job.index("GITHUB_ENV")

    assert backend_integration_job.count(declaration) == 1
    assert backend_integration_job.index(declaration) < export_position


def test_slice1_async_contracts_install_the_pinned_pytest_plugin_in_both_backend_jobs():
    dependency = "pytest-asyncio==1.4.0"
    for job_name in ("backend-unit", "backend-integration"):
        job = _workflow_job_block(job_name)
        install_position = job.index("Install test dependencies")
        pytest_position = job.index("python -m pytest")

        assert job.count(dependency) == 1
        assert install_position < job.index(dependency) < pytest_position


def test_slice2_runtime_roles_urls_and_ephemeral_keyrings_are_propagated_before_export():
    backend_integration_job = _workflow_job_block("backend-integration")
    prefixes = (
        "KG_THERAPIST_ONBOARDING_WRITER",
        "KG_THERAPIST_REVIEW_WRITER",
        "KG_THERAPIST_READINESS_WORKER",
        "KG_THERAPIST_READER",
    )
    export_position = backend_integration_job.index("GITHUB_ENV")
    for prefix in prefixes:
        test_prefix = prefix.replace("KG_", "KG_TEST_", 1)
        role_mapping = f'values["{prefix}_ROLE"] = values["{test_prefix}_ROLE"]'
        url_mapping = f'values["{prefix}_DATABASE_URL"] = values["{test_prefix}_DATABASE_URL"]'
        assert backend_integration_job.count(f'"{test_prefix}_DATABASE_URL": "{test_prefix}_ROLE"') == 1
        assert backend_integration_job.count(role_mapping) == 1
        assert backend_integration_job.count(url_mapping) == 1
        assert backend_integration_job.index(role_mapping) < export_position
        assert backend_integration_job.index(url_mapping) < export_position
    for prefix in (
        "KG_THERAPIST_PII_ENCRYPTION",
        "KG_THERAPIST_PII_DIGEST",
        "KG_THERAPIST_TOTP_ENCRYPTION",
        "KG_THERAPIST_INVITATION_CODE_HMAC",
        "KG_THERAPIST_REPLAY_ENCRYPTION",
        "KG_THERAPIST_READINESS_DIGEST",
    ):
        assert backend_integration_job.count(f'"{prefix}_CURRENT_KEY_ID"') >= 1
        assert f'values[f"{{prefix}}_KEYRING_JSON"]' in backend_integration_job


def test_slice3_runtime_roles_and_urls_are_created_and_propagated_before_export():
    backend_integration_job = _workflow_job_block("backend-integration")
    prefixes = (
        "KG_MEMBER_ENROLLMENT_WRITER",
        "KG_MEMBER_IDENTITY_REVIEW_WRITER",
        "KG_MEMBER_CASE_WRITER",
        "KG_MEMBER_WORKFLOW_WORKER",
        "KG_MEMBER_ENROLLMENT_READER",
    )
    export_position = backend_integration_job.index("GITHUB_ENV")

    for prefix in prefixes:
        test_prefix = prefix.replace("KG_", "KG_TEST_", 1)
        role_mapping = f'values["{prefix}_ROLE"] = values["{test_prefix}_ROLE"]'
        url_mapping = f'values["{prefix}_DATABASE_URL"] = values["{test_prefix}_DATABASE_URL"]'
        role_variable = prefix.removeprefix("KG_").lower() + "_role"

        assert backend_integration_job.count(
            f'"{test_prefix}_DATABASE_URL": "{test_prefix}_ROLE"'
        ) == 1
        assert backend_integration_job.count(role_mapping) == 1
        assert backend_integration_job.count(url_mapping) == 1
        assert backend_integration_job.index(role_mapping) < export_position
        assert backend_integration_job.index(url_mapping) < export_position
        assert backend_integration_job.count(f'CREATE ROLE :"{role_variable}" LOGIN') == 1


def test_slice3_digest_keyrings_are_independent_random_masked_and_exported():
    backend_integration_job = _workflow_job_block("backend-integration")
    prefixes = {
        "KG_MEMBER_ENROLLMENT_PII": "pii",
        "KG_MEMBER_ENROLLMENT_LOOKUP": "lookup",
        "KG_MEMBER_ENROLLMENT_CODE": "code",
        "KG_MEMBER_ENROLLMENT_REPLAY": "replay",
        "KG_MEMBER_ENROLLMENT_COORDINATION": "coordination",
        "KG_MEMBER_ENROLLMENT_REQUEST_DIGEST": "request",
        "KG_MEMBER_ENROLLMENT_AUDIT_DIGEST": "audit",
        "KG_MEMBER_ENROLLMENT_OUTBOX_DIGEST": "outbox",
        "KG_MEMBER_ENROLLMENT_CONSENT_DIGEST": "consent",
        "KG_MEMBER_ENROLLMENT_DELIVERY": "delivery",
    }
    mask_position = backend_integration_job.index('print(f"::add-mask::{value}")')
    export_position = backend_integration_job.index("GITHUB_ENV")

    assert "member_enrollment_key_purposes = {" in backend_integration_job
    assert "member_enrollment_key_materials = {" in backend_integration_job
    assert "base64.b64encode(secrets.token_bytes(32)).decode()" in backend_integration_job
    assert 'values["KG_IDENTITY_PII_HMAC_KEY_B64"]' in backend_integration_job
    assert "Member enrollment key material is not isolated" in backend_integration_job
    assert "*member_enrollment_key_materials.values()," in backend_integration_job
    assert backend_integration_job.count('values[f"{prefix}_CURRENT_KEY_ID"]') == 4
    assert backend_integration_job.count('values[f"{prefix}_KEYRING_JSON"]') == 4
    assert backend_integration_job.count(
        'key_id = f"ci-member-{purpose}-{secrets.token_hex(6)}"'
    ) == 1

    for prefix, purpose in prefixes.items():
        declaration = f'"{prefix}": "{purpose}"'
        assert backend_integration_job.count(declaration) == 1
        assert backend_integration_job.index(declaration) < mask_position < export_position


def test_slice4_runtime_roles_urls_and_keyrings_are_created_masked_and_propagated():
    backend_integration_job = _workflow_job_block("backend-integration")
    runtime_prefixes = (
        "KG_HEALTH_RECORD_WRITER",
        "KG_ASSESSMENT_READINESS_WRITER",
        "KG_SLICE4_WORKFLOW_WORKER",
        "KG_SLICE4_CLINICAL_READER",
        "KG_SLICE4_INSTITUTION_READER",
        "KG_SLICE4_IDENTITY_AUTHORITY",
    )
    export_position = backend_integration_job.index("GITHUB_ENV")
    for prefix in runtime_prefixes:
        test_prefix = prefix.replace("KG_", "KG_TEST_", 1)
        role_variable = prefix.removeprefix("KG_").lower() + "_role"
        role_mapping = f'values[f"{{prefix}}_ROLE"] = values[f"{{test_prefix}}_ROLE"]'
        url_mapping = f'values[f"{{prefix}}_DATABASE_URL"] = values[f"{{test_prefix}}_DATABASE_URL"]'
        assert backend_integration_job.count(
            f'"{test_prefix}_DATABASE_URL": "{test_prefix}_ROLE"'
        ) == 1
        assert backend_integration_job.count(f'CREATE ROLE :"{role_variable}" LOGIN') == 1
        assert role_mapping in backend_integration_job
        assert url_mapping in backend_integration_job
        assert backend_integration_job.index(role_mapping) < export_position
        assert backend_integration_job.index(url_mapping) < export_position

    key_purposes = {
        "KG_SLICE4_PROFILE_PHI": "profile-phi",
        "KG_SLICE4_ASSEMBLY_PHI": "assembly-phi",
        "KG_SLICE4_REQUEST_DIGEST": "request",
        "KG_SLICE4_AUDIT_DIGEST": "audit",
        "KG_SLICE4_OUTBOX_DIGEST": "outbox",
        "KG_SLICE4_DELIVERY": "delivery",
        "KG_SLICE4_CURSOR": "cursor",
        "KG_SLICE4_REPLAY_DIGEST": "replay",
        "KG_SLICE4_COORDINATION": "coordination",
    }
    mask_position = backend_integration_job.index('print(f"::add-mask::{value}")')
    assert "slice4_key_materials = {" in backend_integration_job
    assert "Slice 4 key material is not isolated" in backend_integration_job
    assert "*slice4_key_materials.values()," in backend_integration_job
    assert 'key_id = f"ci-slice4-{purpose}-{secrets.token_hex(6)}"' in backend_integration_job
    for prefix, purpose in key_purposes.items():
        declaration = f'"{prefix}": "{purpose}"'
        assert backend_integration_job.count(declaration) == 1
        assert backend_integration_job.index(declaration) < mask_position < export_position


def test_slice5_runtime_roles_urls_and_keyrings_are_created_masked_and_propagated():
    backend_integration_job = _workflow_job_block("backend-integration")
    runtime_prefixes = {
        "KG_SLICE5_ASSESSMENT_WRITER": "slice5_assessment_role",
        "KG_SLICE5_RISK_WORKFLOW_WRITER": "slice5_risk_role",
        "KG_SLICE5_RULE_GOVERNANCE_WRITER": "slice5_rule_role",
        "KG_SLICE5_WORKFLOW_WORKER": "slice5_worker_role",
        "KG_SLICE5_CLINICAL_READER": "slice5_clinical_role",
        "KG_SLICE5_OVERSIGHT_READER": "slice5_oversight_role",
    }
    export_position = backend_integration_job.index("GITHUB_ENV")
    for prefix, role_variable in runtime_prefixes.items():
        test_prefix = prefix.replace("KG_", "KG_TEST_", 1)
        assert backend_integration_job.count(
            f'"{test_prefix}_DATABASE_URL": "{test_prefix}_ROLE"'
        ) == 1
        assert backend_integration_job.count(f'CREATE ROLE :"{role_variable}" LOGIN') == 1
        assert backend_integration_job.index(f'"{prefix}",') < export_position

    key_purposes = {"KG_SLICE5_DIGEST": "digest", "KG_SLICE5_PHI": "phi"}
    mask_position = backend_integration_job.index('print(f"::add-mask::{value}")')
    assert "slice5_key_materials = {" in backend_integration_job
    assert "Slice 5 key material is not isolated" in backend_integration_job
    for prefix, purpose in key_purposes.items():
        declaration = f'"{prefix}": "{purpose}"'
        assert backend_integration_job.count(declaration) == 1
        assert backend_integration_job.index(declaration) < mask_position < export_position


def test_slice5_ci_uses_disposable_rabbitmq_and_an_independent_worker():
    backend_integration_job = _workflow_job_block("backend-integration")
    worker = _workflow_step_block("Start independent Slice 5 Celery worker")
    contract = _workflow_step_block(
        "Run Slice 5 RabbitMQ and independent worker contract"
    )
    cleanup = _workflow_step_block("Stop independent Slice 5 Celery worker")

    assert "slice5-assessment-workflow" in worker
    assert "--pool=solo" in worker
    assert 'worker_ready="false"' in worker
    assert 'worker_ready="true"' in worker
    assert '[[ "$worker_ready" != "true" ]]' in worker
    assert "KG_TEST_SLICE5_REAL_RABBIT" in contract
    assert "test_一期切片5Outbox与RabbitMQ闭环.py" in contract
    assert "pytest-slice5-rabbit-report.xml" in contract
    assert "        if: always()" in cleanup
    assert 'kill "$worker_pid"' in cleanup
    assert "kg-slice5-worker.log" in cleanup


SLICE3_RUNTIME_KEYRING_PREFIXES = (
    "KG_MEMBER_ENROLLMENT_PII",
    "KG_MEMBER_ENROLLMENT_LOOKUP",
    "KG_MEMBER_ENROLLMENT_CODE",
    "KG_MEMBER_ENROLLMENT_REPLAY",
    "KG_MEMBER_ENROLLMENT_DELIVERY",
    "KG_MEMBER_ENROLLMENT_COORDINATION",
    "KG_MEMBER_ENROLLMENT_REQUEST_DIGEST",
    "KG_MEMBER_ENROLLMENT_AUDIT_DIGEST",
    "KG_MEMBER_ENROLLMENT_OUTBOX_DIGEST",
    "KG_MEMBER_ENROLLMENT_CONSENT_DIGEST",
)


def _install_complete_slice3_runtime_keyrings(monkeypatch: pytest.MonkeyPatch) -> None:
    materials: list[bytes] = []
    while len(materials) != len(SLICE3_RUNTIME_KEYRING_PREFIXES) + 1:
        candidate = secrets.token_bytes(32)
        if candidate not in materials:
            materials.append(candidate)
    monkeypatch.setenv("KG_IDENTITY_PII_KEY_ID", "test-identity-current")
    monkeypatch.setenv(
        "KG_IDENTITY_PII_HMAC_KEY_B64", base64.b64encode(materials[0]).decode()
    )
    for index, prefix in enumerate(SLICE3_RUNTIME_KEYRING_PREFIXES, start=1):
        key_id = f"test-runtime-{index}"
        monkeypatch.setenv(f"{prefix}_CURRENT_KEY_ID", key_id)
        monkeypatch.setenv(
            f"{prefix}_KEYRING_JSON",
            json.dumps(
                {key_id: base64.b64encode(materials[index]).decode()},
                separators=(",", ":"),
                sort_keys=True,
            ),
        )


def test_complete_slice3_runtime_keyrings_construct_member_enrollment_secrets(monkeypatch):
    from app.modules.member_enrollment.service import MemberEnrollmentSecrets

    _install_complete_slice3_runtime_keyrings(monkeypatch)

    secrets_box = MemberEnrollmentSecrets()

    assert secrets_box.pii_key_id == "test-runtime-1"
    assert secrets_box.consent_digest_key_id == "test-runtime-10"


@pytest.mark.parametrize("prefix", SLICE3_RUNTIME_KEYRING_PREFIXES)
@pytest.mark.parametrize("missing_suffix", ("CURRENT_KEY_ID", "KEYRING_JSON"))
def test_member_enrollment_secrets_fail_closed_when_any_runtime_keyring_value_is_missing(
    monkeypatch, prefix: str, missing_suffix: str
):
    from app.modules.member_enrollment.service import MemberEnrollmentSecrets

    _install_complete_slice3_runtime_keyrings(monkeypatch)
    monkeypatch.delenv(f"{prefix}_{missing_suffix}")

    with pytest.raises(RuntimeError, match="^MEMBER_ENROLLMENT_DEPENDENCY_UNAVAILABLE$"):
        MemberEnrollmentSecrets()


def test_A2_2_P匿名盘点专用角色与数据库URL按闭合CI合同传播():
    backend_integration_job = _workflow_job_block("backend-integration")
    assert backend_integration_job.count(
        '"KG_TEST_A2_IDENTITY_INVENTORY_ROLE": f"kg_ci_a2_inventory_{suffix}"'
    ) == 1
    assert backend_integration_job.count(
        '"KG_TEST_A2_IDENTITY_INVENTORY_DATABASE_URL": '
        '"KG_TEST_A2_IDENTITY_INVENTORY_ROLE"'
    ) == 1
    assert backend_integration_job.count(
        '"KG_A2_IDENTITY_INVENTORY_ROLE": "KG_TEST_A2_IDENTITY_INVENTORY_ROLE"'
    ) == 1
    assert backend_integration_job.count(
        '"KG_A2_IDENTITY_INVENTORY_DATABASE_URL": '
        '"KG_TEST_A2_IDENTITY_INVENTORY_DATABASE_URL"'
    ) == 1
    assert backend_integration_job.count(
        'CREATE ROLE :"a2_identity_inventory_role" LOGIN'
    ) == 1
    assert (
        '--set=a2_identity_inventory_role="$KG_TEST_A2_IDENTITY_INVENTORY_ROLE"'
        in backend_integration_job
    )
    assert (
        '--set=a2_identity_inventory_password='
        '"$KG_TEST_A2_IDENTITY_INVENTORY_ROLE_PASSWORD"'
        in backend_integration_job
    )


def test_A2_2_L整改账本双角色与数据库URL按闭合CI合同传播():
    backend_integration_job = _workflow_job_block("backend-integration")
    for role_name, prefix in (
        (
            "KG_TEST_A2_IDENTITY_REMEDIATION_WRITER_ROLE",
            "kg_ci_a2_remediation_writer_",
        ),
        (
            "KG_TEST_A2_IDENTITY_REMEDIATION_CONFIRMATION_ROLE",
            "kg_ci_a2_remediation_confirm_",
        ),
    ):
        assert backend_integration_job.count(
            f'"{role_name}": f"{prefix}{{suffix}}"'
        ) == 1
        url_name = role_name.replace("_ROLE", "_DATABASE_URL")
        assert backend_integration_job.count(
            f'"{url_name}": "{role_name}"'
        ) == 1
    assert backend_integration_job.count(
        '"KG_A2_IDENTITY_REMEDIATION_WRITER_ROLE": '
        '"KG_TEST_A2_IDENTITY_REMEDIATION_WRITER_ROLE"'
    ) == 1
    assert backend_integration_job.count(
        '"KG_A2_IDENTITY_REMEDIATION_CONFIRMATION_ROLE": '
        '"KG_TEST_A2_IDENTITY_REMEDIATION_CONFIRMATION_ROLE"'
    ) == 1
    assert backend_integration_job.count(
        'CREATE ROLE :"a2_identity_remediation_writer_role" LOGIN'
    ) == 1
    assert backend_integration_job.count(
        'CREATE ROLE :"a2_identity_remediation_confirmation_role" LOGIN'
    ) == 1
    for cli_name in (
        "a2_identity_remediation_writer",
        "a2_identity_remediation_confirmation",
    ):
        assert f'--set={cli_name}_role="$KG_TEST_{cli_name.upper()}_ROLE"' in backend_integration_job
        assert (
            f'--set={cli_name}_password="$KG_TEST_{cli_name.upper()}_ROLE_PASSWORD"'
            in backend_integration_job
        )
