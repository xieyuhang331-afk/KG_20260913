import ast
from pathlib import Path


EXPECTED_HEAD = "20260803_0007"
STALE_HEAD = "20260728_0006"
REVISION_FAILURE = (
    "integration revision contract must track Alembic head "
    "20260803_0007; found stale revision 20260728_0006"
)
SCHEMA_FAILURE = (
    "pg_database must drop disposable identity schema before public reset "
    "and Alembic upgrade"
)
INTEGRATION_ROOT = Path(__file__).resolve().parent / "integration"
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
