from __future__ import annotations

import ast
import asyncio
import hashlib
import importlib.util
import inspect
import json
import os
import re
import secrets
import stat
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.integration.conftest import _to_asyncpg_dsn

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PYTHON_RUNNER = BACKEND_ROOT / "scripts" / "初始化G2首个平台审核账号.py"
_SAFE_DATABASE = re.compile(r"^kg_it_[a-f0-9]{16}$")
_SAFE_ROLE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
RUNNER_HTTP_ACCEPTANCE = "PENDING_G1_LIVE_REHEARSAL"
_FRESH_URL_ROLES = {
    "KG_VERIFICATION_WRITER_DATABASE_URL": "KG_TEST_VERIFICATION_WRITER_ROLE",
    "KG_MEMBER_ENROLLMENT_WRITER_DATABASE_URL": "KG_TEST_MEMBER_ENROLLMENT_WRITER_ROLE",
    "KG_MEMBER_IDENTITY_REVIEW_WRITER_DATABASE_URL": "KG_TEST_MEMBER_IDENTITY_REVIEW_WRITER_ROLE",
    "KG_MEMBER_CASE_WRITER_DATABASE_URL": "KG_TEST_MEMBER_CASE_WRITER_ROLE",
    "KG_MEMBER_WORKFLOW_WORKER_DATABASE_URL": "KG_TEST_MEMBER_WORKFLOW_WORKER_ROLE",
    "KG_MEMBER_ENROLLMENT_READER_DATABASE_URL": "KG_TEST_MEMBER_ENROLLMENT_READER_ROLE",
}
_G2_PREPARE_STAGES = frozenset(
    {
        "PREPARE_ADMIN_CONNECT",
        "PREPARE_CREATE_DATABASE",
        "PREPARE_COMMENT_DATABASE",
        "PREPARE_ADMIN_CLOSE",
        "PREPARE_TASK_ADMIN_CONNECT",
        "PREPARE_CREATE_EXTENSION",
        "PREPARE_PUBLIC_SCHEMA_OWNER",
        "PREPARE_PUBLIC_SCHEMA_REVOKE",
        "PREPARE_TASK_ADMIN_CLOSE",
    }
)
_G2_MIGRATION_STAGES = {
    "upgrade-head": "MIGRATION_UPGRADE",
    "downgrade-0047": "MIGRATION_DOWNGRADE",
    "reupgrade-head": "MIGRATION_REUPGRADE",
}
_G2_CONTROLLED_STAGES = _G2_PREPARE_STAGES | {
    "PREPARE_ASYNCIO_RUN",
    *_G2_MIGRATION_STAGES.values(),
}
_G2_CONTROLLED_STAGE = "UNKNOWN"
_G2_MIGRATION_CODES = {
    "KG_G2_MIGRATION_ALEMBIC_INTERNAL_FAILED",
    "KG_G2_MIGRATION_ARGUMENT_INVALID",
    "KG_G2_MIGRATION_COMPLETED",
    "KG_G2_MIGRATION_EXECUTION_FAILED",
    "KG_G2_MIGRATION_MANIFEST_INVALID",
    "KG_G2_MIGRATION_OWNERSHIP_INVALID",
    "KG_G2_MIGRATION_PRIVATE_CLEANUP_FAILED",
    "KG_G2_MIGRATION_PROCESS_START_FAILED",
    "KG_G2_MIGRATION_RUNTIME_INVALID",
    "KG_G2_MIGRATION_TIMEOUT",
    "KG_G2_MIGRATION_URL_BINDING_INVALID",
}
_G2_MIGRATION_OPERATION_STAGES = {
    "ALEMBIC_LAUNCH",
    "ALEMBIC_RUN",
    "ARGUMENT_VALIDATION",
    "COMPLETED",
    "MANIFEST_VALIDATION",
    "OWNERSHIP_VALIDATION",
    "PRIVATE_CLEANUP",
    "RUNTIME_VALIDATION",
    "URL_REBIND",
    "UNKNOWN",
}
_G2_MIGRATION_SOURCES = {
    "ALEMBIC_ENV_SOURCE",
    "ALEMBIC_LIBRARY",
    "ALEMBIC_CHILD_PROCESS",
    "APPLICATION_IMPORT_SOURCE",
    "INTEGRATION_SUBPROCESS",
    "MIGRATION_VERSION_SOURCE",
    "MIGRATION_CONTROLLER",
    "UNKNOWN",
}
_G2_MIGRATION_EXCEPTION_TYPES = {
    "ChildProcessFailure",
    "IntegrityError",
    "KeyError",
    "None",
    "OSError",
    "OperationalError",
    "PowerShellRuntimeException",
    "ProgrammingError",
    "RuntimeError",
    "TimeoutError",
    "ValueError",
    "UNKNOWN",
}
_G2_MIGRATION_DIAGNOSTIC_MATRIX = {
    ("KG_G2_MIGRATION_ARGUMENT_INVALID", "ARGUMENT_VALIDATION"),
    ("KG_G2_MIGRATION_MANIFEST_INVALID", "MANIFEST_VALIDATION"),
    ("KG_G2_MIGRATION_RUNTIME_INVALID", "RUNTIME_VALIDATION"),
    ("KG_G2_MIGRATION_OWNERSHIP_INVALID", "OWNERSHIP_VALIDATION"),
    ("KG_G2_MIGRATION_URL_BINDING_INVALID", "URL_REBIND"),
    ("KG_G2_MIGRATION_PROCESS_START_FAILED", "ALEMBIC_LAUNCH"),
    ("KG_G2_MIGRATION_TIMEOUT", "ALEMBIC_RUN"),
    ("KG_G2_MIGRATION_EXECUTION_FAILED", "ALEMBIC_RUN"),
    ("KG_G2_MIGRATION_ALEMBIC_INTERNAL_FAILED", "ALEMBIC_RUN"),
    ("KG_G2_MIGRATION_PRIVATE_CLEANUP_FAILED", "PRIVATE_CLEANUP"),
    ("KG_G2_MIGRATION_COMPLETED", "COMPLETED"),
}
_G2_MIGRATION_RESULT_KEYS = {
    "action",
    "cleanup_status",
    "code",
    "exception_type",
    "exit_code",
    "operation_stage",
    "schema_version",
    "source_code",
    "source_line",
    "source_location",
    "source_revision",
    "source_sha256",
    "status",
    "timed_out",
}
_G2_MIGRATION_PURPOSE_HASH = (
    "ED4F6B801530A715E2673733C5E7EEA2C8C327CDBD4F767E97FA8717011FA012"
)
_G2_MIGRATION_PURPOSE_COUNTS = (62, 60, 12)
_G2_CHILD_SYSTEM_ENVIRONMENT = (
    "LANG",
    "LC_ALL",
    "PATH",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "WINDIR",
)


def _normalized_runtime_path(value: str | Path, *, platform_name: str) -> str:
    normalized = str(value).replace("\\", "/").rstrip("/")
    return normalized.casefold() if platform_name.startswith("win") else normalized


def _python_runtime_identity_is_approved(
    *,
    executable_path: str | Path,
    resolved_executable_path: str | Path,
    version: tuple[int, int, int],
    app_origin: str | Path,
    test_origin: str | Path,
    backend_root: str | Path,
    environment_prefix: str | Path,
    platform_name: str,
) -> bool:
    def normalize(value: str | Path) -> str:
        return _normalized_runtime_path(value, platform_name=platform_name)

    backend = normalize(backend_root)
    prefix = normalize(environment_prefix)
    executable = normalize(executable_path)
    resolved_name = normalize(resolved_executable_path).rsplit("/", 1)[-1]
    approved_entrypoints = {
        f"{prefix}/bin/python",
        f"{prefix}/scripts/python.exe",
    }
    approved_resolved_names = {"python", "python.exe", "python3.11"}
    return (
        version == (3, 11, 16)
        and prefix.rsplit("/", 1)[-1] == ".venv"
        and executable in approved_entrypoints
        and resolved_name in approved_resolved_names
        and normalize(app_origin).startswith(f"{backend}/app/")
        and normalize(test_origin).startswith(f"{backend}/tests/")
    )


def _migration_role_aliases_are_approved(role_values: dict[str, str]) -> bool:
    approved_alias_groups = (
        frozenset({"KG_DATABASE_USER", "KG_TEST_APPLICATION_ROLE"}),
        frozenset({"KG_READONLY_ROLE", "KG_TEST_READONLY_ROLE"}),
    )
    if any(not group <= role_values.keys() for group in approved_alias_groups):
        return False
    if any(
        len({role_values[name] for name in group}) != 1
        for group in approved_alias_groups
    ):
        return False
    semantic_values: dict[frozenset[str], str] = {}
    for name, value in role_values.items():
        group = next(
            (candidate for candidate in approved_alias_groups if name in candidate),
            frozenset({name}),
        )
        semantic_values[group] = value
    return all(_SAFE_ROLE.fullmatch(value) for value in role_values.values()) and len(
        set(semantic_values.values())
    ) == len(semantic_values)


_G2_MIGRATION_DIAGNOSTIC_SUPPORT = r"""
import ast
import hashlib
import re
from pathlib import Path


def migration_source_registry(backend_root):
    versions_root = (backend_root / "app" / "migrations" / "versions").resolve(strict=True)
    registry = {}
    for source_path in sorted(versions_root.glob("*.py")):
        resolved = source_path.resolve(strict=True)
        if not resolved.is_relative_to(versions_root) or resolved.is_symlink():
            raise RuntimeError("KG_G2_MIGRATION_SOURCE_REGISTRY_INVALID")
        source_bytes = resolved.read_bytes()
        source_text = source_bytes.decode("utf-8")
        tree = ast.parse(source_text, filename=resolved.name)
        revision = None
        stable_codes = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                if any(isinstance(target, ast.Name) and target.id == "revision" for target in node.targets):
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        revision = node.value.value
            if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
                continue
            call = node.exc
            if not isinstance(call.func, ast.Name) or call.func.id != "RuntimeError":
                continue
            if len(call.args) != 1 or not isinstance(call.args[0], ast.Constant):
                continue
            candidate = call.args[0].value
            if isinstance(candidate, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{2,127}", candidate):
                stable_codes[node.lineno] = candidate
        if revision is None or not re.fullmatch(r"[0-9]{8}_[0-9]{4}", revision):
            raise RuntimeError("KG_G2_MIGRATION_SOURCE_REGISTRY_INVALID")
        registry[resolved] = {
            "revision": revision,
            "sha256": hashlib.sha256(source_bytes).hexdigest().upper(),
            "stable_codes": stable_codes,
        }
    if not registry:
        raise RuntimeError("KG_G2_MIGRATION_SOURCE_REGISTRY_INVALID")
    return registry


def migration_error_diagnostic(error, backend_root):
    unknown = {
        "source_code": "UNKNOWN",
        "source_line": "UNKNOWN",
        "source_revision": "UNKNOWN",
        "source_sha256": "UNKNOWN",
    }
    try:
        registry = migration_source_registry(backend_root)
        visited = set()
        current = error
        selected = None
        while current is not None and id(current) not in visited:
            visited.add(id(current))
            trace = current.__traceback__
            while trace is not None:
                try:
                    candidate = Path(trace.tb_frame.f_code.co_filename).resolve(
                        strict=True
                    )
                except (OSError, RuntimeError):
                    trace = trace.tb_next
                    continue
                approved = registry.get(candidate)
                if approved is not None:
                    selected = (approved, trace.tb_lineno)
                trace = trace.tb_next
            current = current.__cause__ or current.__context__
        if selected is None:
            return unknown
        approved, source_line = selected
        return {
            "source_code": approved["stable_codes"].get(source_line, "UNKNOWN"),
            "source_line": source_line,
            "source_revision": approved["revision"],
            "source_sha256": approved["sha256"],
        }
    except BaseException:
        return unknown
"""
_G2_MIGRATION_CHILD_PROGRAM = _G2_MIGRATION_DIAGNOSTIC_SUPPORT + r"""
import asyncio
import json
import os
import sys
import traceback
from importlib.util import find_spec
from pathlib import Path

import asyncpg
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url


def emit(
    *, status, code, exception_type, exit_code, operation_stage, source_location,
    source_code="UNKNOWN", source_line="UNKNOWN", source_revision="UNKNOWN",
    source_sha256="UNKNOWN",
):
    print(json.dumps({
        "action": os.environ["KG_G2_CHILD_ACTION"],
        "cleanup_status": "PASSED",
        "code": code,
        "exception_type": exception_type,
        "exit_code": exit_code,
        "operation_stage": operation_stage,
        "schema_version": 1,
        "source_code": source_code,
        "source_line": source_line,
        "source_location": source_location,
        "source_revision": source_revision,
        "source_sha256": source_sha256,
        "status": status,
        "timed_out": False,
    }, separators=(",", ":")))


def bind_backend_root():
    backend_root = Path.cwd().resolve(strict=True)
    if (
        backend_root.name != "backend"
        or not (backend_root / "alembic.ini").is_file()
        or not (backend_root / "app" / "migrations").is_dir()
        or (backend_root / "app").is_symlink()
    ):
        raise RuntimeError("KG_G2_MIGRATION_IMPORT_BOUNDARY_INVALID")
    sys.path.insert(0, str(backend_root))
    spec = find_spec("app")
    if spec is None or spec.origin is None:
        raise RuntimeError("KG_G2_MIGRATION_IMPORT_BOUNDARY_INVALID")
    if not Path(spec.origin).resolve(strict=True).is_relative_to(backend_root):
        raise RuntimeError("KG_G2_MIGRATION_IMPORT_BOUNDARY_INVALID")
    return backend_root


async def verify_target():
    url = make_url(os.environ["KG_DATABASE_URL"])
    connection = await asyncpg.connect(
        host=url.host,
        port=url.port,
        user=url.username,
        password=url.password,
        database=url.database,
    )
    try:
        row = await connection.fetchrow(
            "SELECT current_database() AS database_name, current_user AS role_name, "
            "pg_get_userbyid(datdba) AS owner_name, "
            "shobj_description(oid,'pg_database') AS sentinel "
            "FROM pg_database WHERE datname=current_database()"
        )
        if (
            row is None
            or row["database_name"] != os.environ["KG_G2_CHILD_TASK_DATABASE"]
            or row["role_name"] != os.environ["KG_TEST_MIGRATION_ROLE"]
            or row["owner_name"] != os.environ["KG_TEST_MIGRATION_ROLE"]
            or row["sentinel"]
            != "kg-test-disposable:" + os.environ["KG_G2_CHILD_RUN_ID"]
        ):
            raise RuntimeError("KG_G2_MIGRATION_OWNERSHIP_INVALID")
    finally:
        await connection.close()


def verify_offline_environment(backend_root):
    names = sorted({
        match.group(0)
        for source_path in (backend_root / "app" / "migrations" / "versions").glob("*.py")
        for match in re.finditer(r"KG_[A-Z0-9_]+", source_path.read_text(encoding="utf-8"))
    })
    canonical = "\n".join(names).encode()
    roles = tuple(name for name in names if name.endswith("_ROLE") or name == "KG_DATABASE_USER")
    urls = tuple(name for name in names if name.endswith("_DATABASE_URL"))
    keys = tuple(name for name in names if name not in roles and name not in urls)
    if (
        (len(roles), len(urls), len(keys)) != (62, 60, 12)
        or hashlib.sha256(canonical).hexdigest().upper()
        != "ED4F6B801530A715E2673733C5E7EEA2C8C327CDBD4F767E97FA8717011FA012"
        or any(name not in os.environ for name in (*roles, *urls, *keys))
    ):
        raise RuntimeError("KG_G2_MIGRATION_MANIFEST_INVALID")
    role_values = {name: os.environ[name] for name in roles}
    role_values["KG_TEST_APPLICATION_ROLE"] = os.environ["KG_TEST_APPLICATION_ROLE"]
    owner_role = role_values["KG_TEST_MIGRATION_ROLE"]
    approved_alias_groups = (
        frozenset({"KG_DATABASE_USER", "KG_TEST_APPLICATION_ROLE"}),
        frozenset({"KG_READONLY_ROLE", "KG_TEST_READONLY_ROLE"}),
    )
    semantic_role_values = {}
    for name, value in role_values.items():
        group = next(
            (candidate for candidate in approved_alias_groups if name in candidate),
            frozenset({name}),
        )
        semantic_role_values[group] = value
    if (
        role_values["KG_DATABASE_USER"] != role_values["KG_TEST_APPLICATION_ROLE"]
        or role_values["KG_READONLY_ROLE"] != role_values["KG_TEST_READONLY_ROLE"]
        or len(set(semantic_role_values.values())) != len(semantic_role_values)
        or role_values["KG_DATABASE_USER"] == owner_role
    ):
        raise RuntimeError("KG_G2_MIGRATION_RUNTIME_INVALID")
    migration_url = make_url(os.environ["KG_DATABASE_URL"])
    task_database = os.environ["KG_G2_CHILD_TASK_DATABASE"]
    if migration_url.username != owner_role or migration_url.database != task_database:
        raise RuntimeError("KG_G2_MIGRATION_URL_BINDING_INVALID")
    for name in urls:
        role_name = (
            "KG_DATABASE_USER"
            if name == "KG_IDENTITY_APPLICATION_DATABASE_URL"
            else "KG_READONLY_ROLE"
            if name == "KG_READONLY_DATABASE_URL"
            else name.removesuffix("_DATABASE_URL") + "_ROLE"
        )
        parsed = make_url(os.environ[name])
        if (
            parsed.username != role_values[role_name]
            or parsed.host != migration_url.host
            or parsed.port != migration_url.port
            or parsed.database != task_database
        ):
            raise RuntimeError("KG_G2_MIGRATION_URL_BINDING_INVALID")
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "app" / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    if scripts.get_heads() != ["20260916_0049"]:
        raise RuntimeError("KG_G2_MIGRATION_REVISION_GRAPH_INVALID")


try:
    backend_root = bind_backend_root()
    action = os.environ["KG_G2_CHILD_ACTION"]
    if action == "offline-preflight":
        verify_offline_environment(backend_root)
    else:
        asyncio.run(verify_target())
        target = "20260914_0047" if action == "downgrade-0047" else "head"
        config = Config("alembic.ini")
        config.set_main_option("script_location", "app/migrations")
        config.set_main_option("sqlalchemy.url", os.environ["KG_DATABASE_URL"])
        if action == "downgrade-0047":
            command.downgrade(config, target)
        else:
            command.upgrade(config, target)
except BaseException as error:
    allowed = {
        "IntegrityError", "KeyError", "OperationalError", "ProgrammingError",
        "RuntimeError", "ValueError",
    }
    exception_type = type(error).__name__
    if exception_type not in allowed:
        exception_type = "UNKNOWN"
    source = migration_error_diagnostic(error, backend_root) if "backend_root" in locals() else {
        "source_code": "UNKNOWN",
        "source_line": "UNKNOWN",
        "source_revision": "UNKNOWN",
        "source_sha256": "UNKNOWN",
    }
    filenames = []
    for entry in traceback.extract_tb(error.__traceback__):
        try:
            filenames.append(Path(entry.filename).resolve(strict=True))
        except (OSError, RuntimeError):
            continue
    if isinstance(error, RuntimeError) and error.args == ("KG_G2_MIGRATION_IMPORT_BOUNDARY_INVALID",):
        source_location = "APPLICATION_IMPORT_SOURCE"
    elif source["source_revision"] != "UNKNOWN":
        source_location = "MIGRATION_VERSION_SOURCE"
    elif "backend_root" in locals() and (backend_root / "app" / "migrations" / "env.py").resolve(strict=True) in filenames:
        source_location = "ALEMBIC_ENV_SOURCE"
    elif "backend_root" in locals() and any(name.is_relative_to(backend_root / "app") for name in filenames):
        source_location = "APPLICATION_IMPORT_SOURCE"
    else:
        source_location = "ALEMBIC_LIBRARY"
    failure_code = "KG_G2_MIGRATION_ALEMBIC_INTERNAL_FAILED"
    failure_stage = "ALEMBIC_RUN"
    stable_configuration_failures = {
        "KG_G2_MIGRATION_MANIFEST_INVALID": "MANIFEST_VALIDATION",
        "KG_G2_MIGRATION_RUNTIME_INVALID": "RUNTIME_VALIDATION",
        "KG_G2_MIGRATION_URL_BINDING_INVALID": "URL_REBIND",
    }
    if isinstance(error, RuntimeError) and error.args:
        candidate_code = error.args[0]
        if candidate_code in stable_configuration_failures:
            failure_code = candidate_code
            failure_stage = stable_configuration_failures[candidate_code]
    emit(
        status="FAILED",
        code=failure_code,
        exception_type=exception_type,
        exit_code=86,
        operation_stage=failure_stage,
        source_location=source_location,
        **source,
    )
    sys.exit(86)
emit(
    status="PASSED",
    code="KG_G2_MIGRATION_COMPLETED",
    exception_type="None",
    exit_code=0,
    operation_stage="COMPLETED",
    source_location="ALEMBIC_CHILD_PROCESS",
)
"""
_G2_MIGRATION_OFFLINE_PREFLIGHT_PROGRAM = r"""
import json
import sys
from importlib.util import find_spec
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def emit(payload):
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))


try:
    backend_root = Path.cwd().resolve(strict=True)
    if (
        backend_root.name != "backend"
        or not (backend_root / "alembic.ini").is_file()
        or not (backend_root / "app" / "migrations").is_dir()
        or (backend_root / "app").is_symlink()
    ):
        raise RuntimeError("KG_G2_MIGRATION_IMPORT_BOUNDARY_INVALID")
    sys.path.insert(0, str(backend_root))
    spec = find_spec("app")
    if spec is None or spec.origin is None:
        raise RuntimeError("KG_G2_MIGRATION_IMPORT_BOUNDARY_INVALID")
    if not Path(spec.origin).resolve(strict=True).is_relative_to(backend_root):
        raise RuntimeError("KG_G2_MIGRATION_IMPORT_BOUNDARY_INVALID")
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "app" / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    heads = scripts.get_heads()
    target = scripts.get_revision("20260914_0047")
    revisions = tuple(scripts.walk_revisions())
    if heads != ["20260916_0049"] or target is None or not revisions:
        raise RuntimeError("KG_G2_MIGRATION_REVISION_GRAPH_INVALID")
except BaseException as error:
    exception_type = type(error).__name__
    if exception_type not in {"ImportError", "ModuleNotFoundError", "RuntimeError", "ValueError"}:
        exception_type = "UNKNOWN"
    code = "KG_G2_MIGRATION_OFFLINE_PREFLIGHT_FAILED"
    stage = "REVISION_GRAPH"
    if isinstance(error, RuntimeError) and error.args == ("KG_G2_MIGRATION_IMPORT_BOUNDARY_INVALID",):
        stage = "APPLICATION_IMPORT"
    emit({"code": code, "exception_type": exception_type, "stage": stage, "status": "FAILED"})
    sys.exit(87)
emit({
    "code": "KG_G2_MIGRATION_OFFLINE_PREFLIGHT_PASSED",
    "head": "20260916_0049",
    "status": "PASSED",
    "target": "20260914_0047",
})
"""
_G2_CONCURRENCY_CODES = {
    "KG_G2_CONCURRENCY_DOUBLE_REJECTION",
    "KG_G2_CONCURRENCY_DOUBLE_SUCCESS",
    "KG_G2_CONCURRENCY_EXPECTED",
    "KG_G2_CONCURRENCY_UNEXPECTED_EXCEPTION",
    "UNKNOWN",
}
_G2_CONCURRENCY_EXCEPTION_TYPES = {
    "IntegrityError",
    "OperationalError",
    "ProgrammingError",
    "RuntimeError",
    "UNKNOWN",
}
_G2_CONCURRENCY_KEYS = {
    "bootstrap_count",
    "code",
    "other_count",
    "other_type",
    "success_count",
}
_G2_HTTP_STAGES = (
    "AUTH_LOGIN",
    "AUTH_ME",
    "PLATFORM_IDENTITY_REVIEWS",
    "AUTH_ME_AFTER_DISABLE",
)
_G2_HTTP_EXCEPTION_STAGES = {
    "DEPENDENCY_FACTORY",
    "SESSION_CONTEXT",
    "REVIEWER_CURRENTNESS",
    "VIEW_QUERY",
    "DTO_MAPPING",
    "RESPONSE_SERIALIZATION",
}
_G2_HTTP_EXCEPTION_TYPES = {
    "AssertionError",
    "DBAPIError",
    "IntegrityError",
    "KeyError",
    "OperationalError",
    "ProgrammingError",
    "ResponseValidationError",
    "RuntimeError",
    "ValueError",
}


def _unknown_concurrency_diagnostic() -> dict[str, object]:
    return {
        "bootstrap_count": "UNKNOWN",
        "code": "UNKNOWN",
        "other_count": "UNKNOWN",
        "other_type": "UNKNOWN",
        "success_count": "UNKNOWN",
    }


_G2_CONTROLLED_CONCURRENCY = _unknown_concurrency_diagnostic()


def _summarize_concurrency_results(
    results: list[object], bootstrap_error_type: type[BaseException]
) -> dict[str, object]:
    if len(results) != 2:
        return _unknown_concurrency_diagnostic()
    success_count = sum(result is None for result in results)
    bootstrap_count = sum(
        isinstance(result, bootstrap_error_type) for result in results
    )
    others = [
        result
        for result in results
        if result is not None and not isinstance(result, bootstrap_error_type)
    ]
    other_count = len(others)
    if success_count + bootstrap_count + other_count != 2:
        return _unknown_concurrency_diagnostic()
    other_types = {
        type(result).__name__
        for result in others
        if type(result).__name__ in _G2_CONCURRENCY_EXCEPTION_TYPES
    }
    other_type = next(iter(other_types)) if len(other_types) == 1 else "UNKNOWN"
    if success_count == 1 and bootstrap_count == 1 and other_count == 0:
        code = "KG_G2_CONCURRENCY_EXPECTED"
    elif success_count == 2:
        code = "KG_G2_CONCURRENCY_DOUBLE_SUCCESS"
    elif bootstrap_count == 2:
        code = "KG_G2_CONCURRENCY_DOUBLE_REJECTION"
    else:
        code = "KG_G2_CONCURRENCY_UNEXPECTED_EXCEPTION"
    return {
        "bootstrap_count": bootstrap_count,
        "code": code,
        "other_count": other_count,
        "other_type": other_type,
        "success_count": success_count,
    }


def _set_g2_controlled_concurrency_results(
    results: list[object], bootstrap_error_type: type[BaseException]
) -> None:
    global _G2_CONTROLLED_CONCURRENCY
    _G2_CONTROLLED_CONCURRENCY = _summarize_concurrency_results(
        results, bootstrap_error_type
    )


def _reset_g2_controlled_concurrency_results() -> None:
    global _G2_CONTROLLED_CONCURRENCY
    _G2_CONTROLLED_CONCURRENCY = _unknown_concurrency_diagnostic()


def _g2_controlled_diagnostic_concurrency() -> dict[str, object]:
    return {key: _G2_CONTROLLED_CONCURRENCY[key] for key in _G2_CONCURRENCY_KEYS}


def _unknown_http_diagnostic() -> dict[str, object]:
    return {stage: "UNKNOWN" for stage in _G2_HTTP_STAGES}


_G2_CONTROLLED_HTTP = _unknown_http_diagnostic()


def _reset_g2_controlled_http_statuses() -> None:
    global _G2_CONTROLLED_HTTP
    _G2_CONTROLLED_HTTP = _unknown_http_diagnostic()


def _record_g2_controlled_http_status(stage: str, status: object) -> None:
    if stage not in _G2_HTTP_STAGES:
        return
    _G2_CONTROLLED_HTTP[stage] = (
        status
        if isinstance(status, int)
        and not isinstance(status, bool)
        and 100 <= status <= 599
        else "UNKNOWN"
    )


def _g2_controlled_diagnostic_http() -> dict[str, object]:
    return {stage: _G2_CONTROLLED_HTTP[stage] for stage in _G2_HTTP_STAGES}


def _unknown_http_exception_diagnostic() -> dict[str, object]:
    return {
        "exception_type": "UNKNOWN",
        "source_code": "UNKNOWN",
        "source_line": "UNKNOWN",
        "source_sha256": "UNKNOWN",
        "sqlstate": "UNKNOWN",
        "stage": "UNKNOWN",
    }


_G2_CONTROLLED_HTTP_EXCEPTION = _unknown_http_exception_diagnostic()


def _reset_g2_controlled_http_exception() -> None:
    global _G2_CONTROLLED_HTTP_EXCEPTION
    _G2_CONTROLLED_HTTP_EXCEPTION = _unknown_http_exception_diagnostic()


def _approved_http_exception_sources() -> dict[Path, str]:
    return {
        Path(__file__).resolve(): "G2_FRESH_INTEGRATION_SOURCE",
        (BACKEND_ROOT / "app" / "modules" / "member_enrollment" / "api.py").resolve(): (
            "MEMBER_ENROLLMENT_API_SOURCE"
        ),
        (BACKEND_ROOT / "app" / "core" / "database.py").resolve(): "DATABASE_SOURCE",
        (
            BACKEND_ROOT / "app" / "modules" / "member_enrollment" / "repository.py"
        ).resolve(): "MEMBER_ENROLLMENT_REPOSITORY_SOURCE",
    }


def _record_g2_controlled_http_exception(stage: str, error: BaseException) -> None:
    global _G2_CONTROLLED_HTTP_EXCEPTION
    if stage not in _G2_HTTP_EXCEPTION_STAGES:
        _G2_CONTROLLED_HTTP_EXCEPTION = _unknown_http_exception_diagnostic()
        return
    exception_type = "UNKNOWN"
    for base in type(error).__mro__:
        if base.__name__ in _G2_HTTP_EXCEPTION_TYPES:
            exception_type = base.__name__
            break
    sqlstate = getattr(error, "sqlstate", None)
    if sqlstate is None:
        sqlstate = getattr(getattr(error, "orig", None), "sqlstate", None)
    if not isinstance(sqlstate, str) or re.fullmatch(r"[0-9A-Z]{5}", sqlstate) is None:
        sqlstate = "UNKNOWN"
    source_code: object = "UNKNOWN"
    source_line: object = "UNKNOWN"
    source_sha256: object = "UNKNOWN"
    approved = _approved_http_exception_sources()
    trace = error.__traceback__
    while trace is not None:
        candidate = Path(trace.tb_frame.f_code.co_filename).resolve()
        if candidate in approved and candidate.is_file():
            source_code = approved[candidate]
            source_line = trace.tb_lineno
            source_sha256 = hashlib.sha256(candidate.read_bytes()).hexdigest().upper()
        trace = trace.tb_next
    _G2_CONTROLLED_HTTP_EXCEPTION = {
        "exception_type": exception_type,
        "source_code": source_code,
        "source_line": source_line,
        "source_sha256": source_sha256,
        "sqlstate": sqlstate,
        "stage": stage,
    }


async def _observe_g2_http_exception(stage: str, awaitable):
    try:
        return await awaitable
    except BaseException as error:
        try:
            _record_g2_controlled_http_exception(stage, error)
        except BaseException:
            _reset_g2_controlled_http_exception()
        raise


def _observe_g2_http_exception_sync(stage: str, operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except BaseException as error:
        try:
            _record_g2_controlled_http_exception(stage, error)
        except BaseException:
            _reset_g2_controlled_http_exception()
        raise


def _g2_controlled_diagnostic_http_exception() -> dict[str, object]:
    return dict(_G2_CONTROLLED_HTTP_EXCEPTION)


def _unknown_migration_diagnostic(action: str) -> dict[str, object]:
    return {
        "action": action if action in _G2_MIGRATION_STAGES else "UNKNOWN",
        "cleanup_status": "UNKNOWN",
        "code": "UNKNOWN",
        "exception_type": "UNKNOWN",
        "exit_code": "UNKNOWN",
        "operation_stage": "UNKNOWN",
        "schema_version": 1,
        "source_code": "UNKNOWN",
        "source_line": "UNKNOWN",
        "source_location": "UNKNOWN",
        "source_revision": "UNKNOWN",
        "source_sha256": "UNKNOWN",
        "status": "UNKNOWN",
        "timed_out": "UNKNOWN",
    }


_G2_CONTROLLED_MIGRATION_OPERATION = _unknown_migration_diagnostic("UNKNOWN")


def _migration_diagnostic_source_is_approved(payload: dict[str, object]) -> bool:
    revision = payload["source_revision"]
    if revision == "UNKNOWN":
        return (
            payload["source_line"] == "UNKNOWN"
            and payload["source_sha256"] == "UNKNOWN"
            and payload["source_code"] == "UNKNOWN"
        )
    versions_root = BACKEND_ROOT / "app" / "migrations" / "versions"
    matches = []
    for source_path in versions_root.glob("*.py"):
        source_bytes = source_path.read_bytes()
        source_text = source_bytes.decode("utf-8")
        tree = ast.parse(source_text, filename=source_path.name)
        source_revision = None
        stable_codes = {}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "revision"
                    for target in node.targets
                )
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                source_revision = node.value.value
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                call = node.exc
                if (
                    isinstance(call.func, ast.Name)
                    and call.func.id == "RuntimeError"
                    and len(call.args) == 1
                    and isinstance(call.args[0], ast.Constant)
                    and isinstance(call.args[0].value, str)
                    and re.fullmatch(r"[A-Z][A-Z0-9_]{2,127}", call.args[0].value)
                ):
                    stable_codes[node.lineno] = call.args[0].value
        if source_revision == revision:
            matches.append((source_text, source_bytes, stable_codes))
    if len(matches) != 1:
        return False
    source_text, source_bytes, stable_codes = matches[0]
    source_line = payload["source_line"]
    return (
        isinstance(source_line, int)
        and source_line <= len(source_text.splitlines())
        and payload["source_sha256"]
        == hashlib.sha256(source_bytes).hexdigest().upper()
        and payload["source_code"] == stable_codes.get(source_line, "UNKNOWN")
        and payload["source_location"] == "MIGRATION_VERSION_SOURCE"
    )


def _parse_migration_controller_result(
    raw: bytes, *, action: str, returncode: int
) -> dict[str, object]:
    unknown = _unknown_migration_diagnostic(action)
    if len(raw) > 4096 or returncode < 0 or returncode > 255:
        return unknown
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return unknown
    if not isinstance(payload, dict) or set(payload) != _G2_MIGRATION_RESULT_KEYS:
        return unknown
    if (
        payload["schema_version"] != 1
        or payload["action"] != action
        or payload["status"] not in {"FAILED", "PASSED"}
        or payload["cleanup_status"] not in {"FAILED", "PASSED"}
        or payload["code"] not in _G2_MIGRATION_CODES
        or payload["operation_stage"] not in _G2_MIGRATION_OPERATION_STAGES
        or payload["source_location"] not in _G2_MIGRATION_SOURCES
        or (
            payload["source_revision"] != "UNKNOWN"
            and re.fullmatch(r"[0-9]{8}_[0-9]{4}", payload["source_revision"])
            is None
        )
        or (
            payload["source_sha256"] != "UNKNOWN"
            and re.fullmatch(r"[A-F0-9]{64}", payload["source_sha256"]) is None
        )
        or (
            payload["source_code"] != "UNKNOWN"
            and re.fullmatch(r"[A-Z][A-Z0-9_]{2,127}", payload["source_code"])
            is None
        )
        or (
            payload["source_line"] != "UNKNOWN"
            and (
                isinstance(payload["source_line"], bool)
                or not isinstance(payload["source_line"], int)
                or payload["source_line"] < 1
                or payload["source_line"] > 10000
            )
        )
        or ((payload["source_revision"] == "UNKNOWN") != (payload["source_line"] == "UNKNOWN"))
        or ((payload["source_revision"] == "UNKNOWN") != (payload["source_sha256"] == "UNKNOWN"))
        or (payload["source_code"] != "UNKNOWN" and payload["source_revision"] == "UNKNOWN")
        or not _migration_diagnostic_source_is_approved(payload)
        or payload["exception_type"] not in _G2_MIGRATION_EXCEPTION_TYPES
        or isinstance(payload["exit_code"], bool)
        or not isinstance(payload["exit_code"], int)
        or payload["exit_code"] < -1
        or payload["exit_code"] > 255
        or not isinstance(payload["timed_out"], bool)
        or (payload["status"] == "PASSED") != (returncode == 0)
        or (payload["status"] == "PASSED")
        != (payload["code"] == "KG_G2_MIGRATION_COMPLETED")
        or (payload["code"], payload["operation_stage"])
        not in _G2_MIGRATION_DIAGNOSTIC_MATRIX
    ):
        return unknown
    return payload


def _load_runner():
    spec = importlib.util.spec_from_file_location("g2_bootstrap_integration", PYTHON_RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _database_url_for_name(database_url: str, database_name: str) -> str:
    return make_url(database_url).set(database=database_name).render_as_string(
        hide_password=False
    )


def _migration_process_arguments(
    *, action: str, task_database: str, g1_run_id: str
) -> list[str]:
    if action not in {"upgrade-head", "downgrade-0047", "reupgrade-head"}:
        raise ValueError("KG_G2_MIGRATION_ARGUMENT_INVALID")
    if not _SAFE_DATABASE.fullmatch(task_database) or not re.fullmatch(
        r"[a-f0-9]{16}", g1_run_id
    ):
        raise ValueError("KG_G2_MIGRATION_ARGUMENT_INVALID")
    return [sys.executable, "-I", "-c", _G2_MIGRATION_CHILD_PROGRAM]


def _run_migration_offline_preflight() -> dict[str, str]:
    environment = {
        name: value
        for name in _G2_CHILD_SYSTEM_ENVIRONMENT
        if (value := os.environ.get(name))
    }
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-I", "-c", _G2_MIGRATION_OFFLINE_PREFLIGHT_PROGRAM],
        cwd=BACKEND_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        timeout=60,
    )
    if completed.returncode != 0 or completed.stderr:
        pytest.fail("KG_G2_MIGRATION_OFFLINE_PREFLIGHT_FAILED", pytrace=False)
    try:
        payload = json.loads(completed.stdout)
    except (TypeError, ValueError):
        pytest.fail("KG_G2_MIGRATION_OFFLINE_PREFLIGHT_FAILED", pytrace=False)
    expected = {
        "code": "KG_G2_MIGRATION_OFFLINE_PREFLIGHT_PASSED",
        "head": "20260916_0049",
        "status": "PASSED",
        "target": "20260914_0047",
    }
    if payload != expected:
        pytest.fail("KG_G2_MIGRATION_OFFLINE_PREFLIGHT_FAILED", pytrace=False)
    return payload


def _execute_child_environment_offline_preflight(
    child_environment: dict[str, str], *, task_database: str, g1_run_id: str
) -> tuple[int, dict[str, object]]:
    completed = subprocess.run(
        _migration_process_arguments(
            action="upgrade-head",
            task_database=task_database,
            g1_run_id=g1_run_id,
        ),
        cwd=BACKEND_ROOT,
        env=child_environment,
        check=False,
        capture_output=True,
        timeout=60,
    )
    if completed.stderr:
        pytest.fail("KG_G2_MIGRATION_FINAL_ENVIRONMENT_PREFLIGHT_FAILED", pytrace=False)
    try:
        payload = json.loads(completed.stdout)
    except (TypeError, ValueError):
        pytest.fail("KG_G2_MIGRATION_FINAL_ENVIRONMENT_PREFLIGHT_FAILED", pytrace=False)
    if not isinstance(payload, dict) or set(payload) != _G2_MIGRATION_RESULT_KEYS:
        pytest.fail("KG_G2_MIGRATION_FINAL_ENVIRONMENT_PREFLIGHT_FAILED", pytrace=False)
    return completed.returncode, payload


def _run_final_child_environment_offline_preflight(
    *, task_database: str, g1_run_id: str
) -> dict[str, object]:
    child_environment = _migration_child_environment(
        action="upgrade-head",
        task_database=task_database,
        g1_run_id=g1_run_id,
    )
    child_environment["KG_G2_CHILD_ACTION"] = "offline-preflight"
    returncode, payload = _execute_child_environment_offline_preflight(
        child_environment,
        task_database=task_database,
        g1_run_id=g1_run_id,
    )
    expected = {
        "action": "offline-preflight",
        "cleanup_status": "PASSED",
        "code": "KG_G2_MIGRATION_COMPLETED",
        "exception_type": "None",
        "exit_code": 0,
        "operation_stage": "COMPLETED",
        "schema_version": 1,
        "source_code": "UNKNOWN",
        "source_line": "UNKNOWN",
        "source_location": "ALEMBIC_CHILD_PROCESS",
        "source_revision": "UNKNOWN",
        "source_sha256": "UNKNOWN",
        "status": "PASSED",
        "timed_out": False,
    }
    if returncode != 0 or payload != expected:
        pytest.fail("KG_G2_MIGRATION_FINAL_ENVIRONMENT_PREFLIGHT_FAILED", pytrace=False)
    return payload


def _migration_purpose_names() -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    names = sorted(
        {
            match.group(0)
            for path in (BACKEND_ROOT / "app" / "migrations" / "versions").glob("*.py")
            for match in re.finditer(r"KG_[A-Z0-9_]+", path.read_text(encoding="utf-8"))
        }
    )
    canonical = "\n".join(names).encode()
    roles = tuple(
        name for name in names if name.endswith("_ROLE") or name == "KG_DATABASE_USER"
    )
    urls = tuple(name for name in names if name.endswith("_DATABASE_URL"))
    keys = tuple(name for name in names if name not in roles and name not in urls)
    if (
        (len(roles), len(urls), len(keys)) != _G2_MIGRATION_PURPOSE_COUNTS
        or hashlib.sha256(canonical).hexdigest().upper() != _G2_MIGRATION_PURPOSE_HASH
        or any(
            not re.search(
                r"(HMAC|KEYRING_JSON|CURRENT_KEY_ID|_KEY_ID$|_KEY_B64$|KEK_B64)",
                name,
            )
            for name in keys
        )
    ):
        raise ValueError("KG_G2_MIGRATION_MANIFEST_INVALID")
    return roles, urls, keys


def _migration_runtime_error_catalog() -> tuple[dict[str, object], ...]:
    versions_root = BACKEND_ROOT / "app" / "migrations" / "versions"
    known_environment_names = set().union(*_migration_purpose_names())
    catalog: list[dict[str, object]] = []
    for source_path in sorted(versions_root.glob("*.py")):
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=source_path.name)
        revision = None
        environment_names = sorted(
            {
                node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in known_environment_names
            }
        )
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "revision"
                    for target in node.targets
                )
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                revision = node.value.value
        if revision is None:
            raise ValueError("KG_G2_MIGRATION_STATIC_CATALOG_INVALID")
        source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest().upper()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
                continue
            call = node.exc
            if not isinstance(call.func, ast.Name) or call.func.id != "RuntimeError":
                continue
            stable_code = "UNKNOWN"
            if len(call.args) == 1 and isinstance(call.args[0], ast.Constant):
                candidate = call.args[0].value
                if isinstance(candidate, str) and re.fullmatch(
                    r"[A-Z][A-Z0-9_]{2,127}", candidate
                ):
                    stable_code = candidate
            catalog.append(
                {
                    "environment_names": tuple(environment_names),
                    "revision": revision,
                    "source_code": stable_code,
                    "source_line": node.lineno,
                    "source_sha256": source_hash,
                }
            )
    return tuple(catalog)


def _migration_child_semantic_report(
    child: dict[str, str], *, task_database: str
) -> dict[str, object]:
    roles, urls, keys = _migration_purpose_names()
    migration_role = child.get("KG_TEST_MIGRATION_ROLE")
    application_role = os.environ.get("KG_DATABASE_USER")
    url_usernames = {}
    parsed_urls = {}
    for name in urls:
        try:
            parsed_urls[name] = make_url(child[name])
            url_usernames[name] = parsed_urls[name].username
        except (KeyError, TypeError, ValueError):
            parsed_urls[name] = None
            url_usernames[name] = None
    try:
        migration_url = make_url(child["KG_DATABASE_URL"])
    except (KeyError, TypeError, ValueError):
        migration_url = None
    referenced_environment = {
        name
        for entry in _migration_runtime_error_catalog()
        for name in entry["environment_names"]
    }
    return {
        "application_role_preserved": bool(application_role)
        and child.get("KG_DATABASE_USER") == application_role,
        "business_roles_distinct_from_owner": bool(migration_role)
        and all(
            child.get(name) != migration_role
            for name in roles
            if name not in {"KG_TEST_MIGRATION_ROLE"}
        ),
        "manifest_counts_match": (len(roles), len(urls), len(keys))
        == _G2_MIGRATION_PURPOSE_COUNTS
        and set((*roles, *urls, *keys)) <= child.keys(),
        "migration_connection_owner_bound": migration_url is not None
        and migration_url.username == migration_role
        and migration_url.database == task_database,
        "referenced_environment_present": referenced_environment <= child.keys(),
        "target_scope_match": migration_url is not None
        and all(
            parsed is not None
            and parsed.host == migration_url.host
            and parsed.port == migration_url.port
            and parsed.database == task_database
            for parsed in parsed_urls.values()
        ),
        "url_role_bindings_match": all(
            url_usernames[name] == child.get(_migration_role_name_for_url(name))
            for name in urls
        ),
    }


def _migration_role_name_for_url(url_name: str) -> str:
    if url_name == "KG_IDENTITY_APPLICATION_DATABASE_URL":
        return "KG_DATABASE_USER"
    if url_name == "KG_READONLY_DATABASE_URL":
        return "KG_READONLY_ROLE"
    return url_name.removesuffix("_DATABASE_URL") + "_ROLE"


def _migration_child_environment(
    *, action: str, task_database: str, g1_run_id: str
) -> dict[str, str]:
    _migration_process_arguments(
        action=action, task_database=task_database, g1_run_id=g1_run_id
    )
    roles, urls, keys = _migration_purpose_names()
    role_values: dict[str, str] = {}
    for name in roles:
        value = os.environ[name]
        if not _SAFE_ROLE.fullmatch(value):
            raise ValueError("KG_G2_MIGRATION_RUNTIME_INVALID")
        role_values[name] = value
    role_values["KG_TEST_APPLICATION_ROLE"] = os.environ["KG_TEST_APPLICATION_ROLE"]
    if not _migration_role_aliases_are_approved(role_values):
        raise ValueError("KG_G2_MIGRATION_RUNTIME_INVALID")

    migration_target = make_url(os.environ["KG_TEST_MIGRATION_DATABASE_URL"])
    if (
        migration_target.drivername != "postgresql+asyncpg"
        or migration_target.username != role_values["KG_TEST_MIGRATION_ROLE"]
        or migration_target.database != task_database
        or migration_target.host not in {"127.0.0.1", "localhost", "::1"}
        or not migration_target.port
        or not migration_target.password
    ):
        raise ValueError("KG_G2_MIGRATION_URL_BINDING_INVALID")

    child = {
        name: os.environ[name]
        for name in _G2_CHILD_SYSTEM_ENVIRONMENT
        if os.environ.get(name)
    }
    child.update(role_values)
    for name in urls:
        role_name = _migration_role_name_for_url(name)
        if role_name not in role_values:
            raise ValueError("KG_G2_MIGRATION_MANIFEST_INVALID")
        source = make_url(os.environ[name])
        if (
            source.drivername != "postgresql+asyncpg"
            or source.username != role_values[role_name]
            or source.host != migration_target.host
            or source.port != migration_target.port
            or not source.password
        ):
            raise ValueError("KG_G2_MIGRATION_URL_BINDING_INVALID")
        child[name] = source.set(database=task_database).render_as_string(
            hide_password=False
        )
    for name in keys:
        value = os.environ[name]
        if not value:
            raise ValueError("KG_G2_MIGRATION_RUNTIME_INVALID")
        child[name] = value
    child.update(
        {
            "KG_DATABASE_URL": migration_target.render_as_string(hide_password=False),
            "KG_DATABASE_HOST": str(migration_target.host),
            "KG_DATABASE_PORT": str(migration_target.port),
            "KG_DATABASE_NAME": task_database,
            "KG_DATABASE_PASSWORD": str(migration_target.password),
            "KG_G2_CHILD_ACTION": action,
            "KG_G2_CHILD_RUN_ID": g1_run_id,
            "KG_G2_CHILD_TASK_DATABASE": task_database,
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return child


def _run_isolated_migration(action: str, task_database: str) -> None:
    global _G2_CONTROLLED_MIGRATION_OPERATION

    previous = _g2_controlled_diagnostic_stage()
    _G2_CONTROLLED_MIGRATION_OPERATION = _unknown_migration_diagnostic(action)
    try:
        stage = _G2_MIGRATION_STAGES[action]
        g1_run_id = task_database.removeprefix("kg_it_")
        arguments = _migration_process_arguments(
            action=action,
            task_database=task_database,
            g1_run_id=g1_run_id,
        )
        child_environment = _migration_child_environment(
            action=action,
            task_database=task_database,
            g1_run_id=g1_run_id,
        )
    except (KeyError, TypeError, ValueError):
        pytest.fail("KG_G2_MIGRATION_CONFIGURATION_INVALID", pytrace=False)
    _set_g2_controlled_diagnostic_stage(stage)
    try:
        completed = subprocess.run(
            arguments,
            cwd=BACKEND_ROOT,
            env=child_environment,
            check=False,
            capture_output=True,
            timeout=620,
        )
    except OSError:
        _G2_CONTROLLED_MIGRATION_OPERATION = {
            **_unknown_migration_diagnostic(action),
            "cleanup_status": "PASSED",
            "code": "KG_G2_MIGRATION_PROCESS_START_FAILED",
            "exception_type": "OSError",
            "exit_code": -1,
            "operation_stage": "ALEMBIC_LAUNCH",
            "source_location": "INTEGRATION_SUBPROCESS",
            "status": "FAILED",
            "timed_out": False,
        }
        pytest.fail("KG_G2_MIGRATION_EXECUTION_FAILED", pytrace=False)
    except subprocess.TimeoutExpired:
        _G2_CONTROLLED_MIGRATION_OPERATION = {
            **_unknown_migration_diagnostic(action),
            "cleanup_status": "PASSED",
            "code": "KG_G2_MIGRATION_TIMEOUT",
            "exception_type": "TimeoutError",
            "exit_code": -1,
            "operation_stage": "ALEMBIC_RUN",
            "source_location": "INTEGRATION_SUBPROCESS",
            "status": "FAILED",
            "timed_out": True,
        }
        pytest.fail("KG_G2_MIGRATION_EXECUTION_FAILED", pytrace=False)
    parsed = _parse_migration_controller_result(
        completed.stdout, action=action, returncode=completed.returncode
    )
    _G2_CONTROLLED_MIGRATION_OPERATION = parsed
    if completed.returncode != 0:
        pytest.fail("KG_G2_MIGRATION_EXECUTION_FAILED", pytrace=False)
    if parsed["status"] != "PASSED":
        pytest.fail("KG_G2_MIGRATION_EXECUTION_FAILED", pytrace=False)
    _G2_CONTROLLED_MIGRATION_OPERATION = _unknown_migration_diagnostic("UNKNOWN")
    _set_g2_controlled_diagnostic_stage(previous)


def _set_g2_controlled_diagnostic_stage(stage: str) -> None:
    global _G2_CONTROLLED_STAGE
    _G2_CONTROLLED_STAGE = stage if stage in _G2_CONTROLLED_STAGES else "UNKNOWN"


def _g2_controlled_diagnostic_stage() -> str:
    return _G2_CONTROLLED_STAGE


def _g2_controlled_diagnostic_operation() -> dict[str, object]:
    return {
        key: _G2_CONTROLLED_MIGRATION_OPERATION[key]
        for key in (
            "code",
            "exception_type",
            "exit_code",
            "operation_stage",
            "source_code",
            "source_line",
            "source_location",
            "source_revision",
            "source_sha256",
            "timed_out",
        )
    }


def _complete_g2_prepare_diagnostic_stage() -> None:
    _set_g2_controlled_diagnostic_stage("UNKNOWN")


async def _diagnostic_call(stage, operation, *args, restore_on_success=False):
    previous = _g2_controlled_diagnostic_stage()
    _set_g2_controlled_diagnostic_stage(stage)
    result = await operation(*args)
    if restore_on_success:
        _set_g2_controlled_diagnostic_stage(previous)
    return result


def _run(awaitable, *, entry_stage=None):
    if entry_stage is not None:
        _set_g2_controlled_diagnostic_stage(entry_stage)
    return asyncio.run(awaitable)


def _validated_source_urls() -> dict[str, str]:
    parsed = {}
    try:
        required = {
            "KG_TEST_ROLE_ADMIN_DATABASE_URL": "postgres",
            "KG_TEST_MIGRATION_DATABASE_URL": os.environ["KG_TEST_MIGRATION_ROLE"],
            "KG_TEST_DATABASE_URL": os.environ["KG_TEST_APPLICATION_ROLE"],
            **{
                name: os.environ[role_name]
                for name, role_name in _FRESH_URL_ROLES.items()
            },
        }
        for name, expected_role in required.items():
            value = os.environ[name]
            url = make_url(value)
            if (
                url.drivername != "postgresql+asyncpg"
                or url.username != expected_role
                or not url.password
                or url.host not in {"127.0.0.1", "localhost", "::1"}
                or not url.port
                or not url.database
            ):
                raise ValueError
            parsed[name] = url
        targets = {(url.host, url.port, url.database) for url in parsed.values()}
        if len(targets) != 1 or next(iter(targets))[2] != os.environ["KG_DATABASE_NAME"]:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        pytest.fail("KG_G2_BOOTSTRAP_TEST_SOURCE_SCOPE_INVALID", pytrace=False)
    return {name: url.render_as_string(hide_password=False) for name, url in parsed.items()}


def _assert_task_database_bindings(
    source_urls: dict[str, str], rebound_urls: dict[str, str], task_database: str
) -> None:
    if set(rebound_urls) != set(_FRESH_URL_ROLES):
        pytest.fail("KG_G2_BOOTSTRAP_TEST_DATABASE_BINDING_INVALID", pytrace=False)
    for name in _FRESH_URL_ROLES:
        source = make_url(source_urls[name])
        rebound = make_url(rebound_urls[name])
        if source.database == task_database or rebound.database != task_database:
            pytest.fail("KG_G2_BOOTSTRAP_TEST_DATABASE_BINDING_INVALID", pytrace=False)


def _prepare_private_credential_files(directory: Path, files: tuple[Path, ...]) -> None:
    if os.name == "nt":
        common = BACKEND_ROOT.parent / "本机联调交付" / "本机联调公共.ps1"
        command_text = (
            "& { . $env:KG_G2_TEST_COMMON; "
            "$acl=[Security.AccessControl.DirectorySecurity]::new(); "
            "$acl.SetAccessRuleProtection($true,$false); "
            "$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User; "
            "$inherit=[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'; "
            "$rule=[Security.AccessControl.FileSystemAccessRule]::new($sid,'Modify',$inherit,'None','Allow'); "
            "$acl.AddAccessRule($rule); "
            "New-Item -ItemType Directory -Path $env:KG_G2_TEST_DIRECTORY|Out-Null; "
            "Set-Acl -LiteralPath $env:KG_G2_TEST_DIRECTORY -AclObject $acl; "
            "Assert-KgNoReparsePath $env:KG_G2_TEST_DIRECTORY; "
            "Assert-KgPrivateAcl $env:KG_G2_TEST_DIRECTORY; "
            "Initialize-KgPrivateFile $env:KG_G2_TEST_PREPARED; "
            "Initialize-KgPrivateFile $env:KG_G2_TEST_RECEIPT }"
        )
        child_environment = os.environ.copy()
        child_environment.update(
            {
                "KG_G2_TEST_COMMON": str(common),
                "KG_G2_TEST_DIRECTORY": str(directory),
                "KG_G2_TEST_PREPARED": str(files[0]),
                "KG_G2_TEST_RECEIPT": str(files[1]),
            }
        )
        completed = subprocess.run(
            ["pwsh.exe", "-NoProfile", "-Command", command_text],
            check=False,
            capture_output=True,
            env=child_environment,
        )
        if completed.returncode != 0:
            pytest.fail("KG_G2_BOOTSTRAP_TEST_PRIVATE_ACL_INVALID", pytrace=False)
        return
    directory.mkdir(mode=0o700)
    if directory.is_symlink() or stat.S_IMODE(directory.stat().st_mode) != 0o700:
        pytest.fail("KG_G2_BOOTSTRAP_TEST_PRIVATE_ACL_INVALID", pytrace=False)
    for path in files:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != 0o600:
            pytest.fail("KG_G2_BOOTSTRAP_TEST_PRIVATE_ACL_INVALID", pytrace=False)


def test_G2_B_Fresh数据库闭环入口存在且不创建持久对象():
    module = _load_runner()
    assert module.EXPECTED_MIGRATION_HEAD == "20260916_0049"
    assert module.BOOTSTRAP_SCHEMA_VERSION == 1
    assert module.PERSISTENT_DATABASE_OBJECTS_CREATED == ()


def test_G2_B_Migration生命周期目标是当前Head直接父且正式图不含0045():
    versions = BACKEND_ROOT / "app" / "migrations" / "versions"
    current = (versions / "20260915_0048_平台实名审核Reviewer当前性受限读取.py").read_text(
        encoding="utf-8"
    )
    revisions = "\n".join(
        path.read_text(encoding="utf-8") for path in versions.glob("*.py")
    )
    assert 'revision = "20260915_0048"' in current
    assert 'down_revision = "20260914_0047"' in current
    assert 'revision = "20260913_0045"' not in revisions


@pytest.mark.asyncio
async def test_G2_B_COMMITTED_NOT_COMMITTED_UNKNOWN三态使用独立确认连接():
    module = _load_runner()
    events: list[str] = []
    outcome = await module.confirm_after_writer_exit(
        writer_closed=True,
        probe=lambda: events.append("confirmation") or module.CommitOutcome.UNKNOWN,
    )
    assert events == ["confirmation"]
    assert outcome is module.CommitOutcome.UNKNOWN
    with pytest.raises(module.BootstrapError) as caught:
        module.require_safe_commit_outcome(outcome)
    assert caught.value.code == "KG_G2_BOOTSTRAP_COMMIT_OUTCOME_UNKNOWN"


def test_G2_B_公开结果不含凭据行级标识或连接信息():
    module = _load_runner()
    result = module.BootstrapResult(
        status="COMMITTED",
        replay=False,
        user_count=1,
        credential_present=True,
        login_verified=True,
        currentness_verified=True,
        request_digest="a" * 64,
        receipt_digest="b" * 64,
    ).to_public_dict()
    assert set(result) == {
        "schema_version", "migration_head", "status", "replay", "user_count",
        "credential_present", "login_verified", "currentness_verified",
        "request_digest", "receipt_digest",
    }
    assert all(name not in result for name in ("phone", "password", "user_id", "url", "sql"))


def test_G2_B_交付脚本不存在用户状态漂移或删除入口():
    source = PYTHON_RUNNER.read_text(encoding="utf-8").lower()
    assert "delete from public.user" not in source
    assert "update public.user" not in source
    assert "exited_at =" not in source
    assert "deletion_requested_at =" not in source
    assert "tenant_id =" not in source


def test_G2_B_runner真实HTTP边界明确留给G1实启验收():
    source = PYTHON_RUNNER.read_text(encoding="utf-8")
    wrapper = (BACKEND_ROOT.parent / "本机联调交付" / "初始化G2首个平台审核账号.ps1").read_text(
        encoding="utf-8"
    )
    assert RUNNER_HTTP_ACCEPTANCE == "PENDING_G1_LIVE_REHEARSAL"
    assert "async with httpx2.AsyncClient" in source
    assert "await _verify_http(request)" in source
    assert "KG_G2_BOOTSTRAP_API_BASE_URL" in wrapper


@pytest.mark.parametrize(
    "stage",
    (
        "PREPARE_ADMIN_CONNECT",
        "PREPARE_CREATE_DATABASE",
        "PREPARE_COMMENT_DATABASE",
        "PREPARE_ADMIN_CLOSE",
        "PREPARE_TASK_ADMIN_CONNECT",
        "PREPARE_CREATE_EXTENSION",
        "PREPARE_PUBLIC_SCHEMA_OWNER",
        "PREPARE_PUBLIC_SCHEMA_REVOKE",
        "PREPARE_TASK_ADMIN_CLOSE",
    ),
)
def test_G2_B_受控准备阶段标记保留异常且仅返回闭合枚举(stage):
    sentinel = RuntimeError("synthetic-sensitive-value")

    async def fail():
        raise sentinel

    with pytest.raises(RuntimeError) as caught:
        _run(_diagnostic_call(stage, fail), entry_stage="PREPARE_ASYNCIO_RUN")
    assert caught.value is sentinel
    assert _g2_controlled_diagnostic_stage() == stage


def test_G2_B_准备成功后后续异常不继承prepare阶段():
    async def succeed():
        return None

    _run(
        _diagnostic_call("PREPARE_PUBLIC_SCHEMA_REVOKE", succeed),
        entry_stage="PREPARE_ASYNCIO_RUN",
    )
    _complete_g2_prepare_diagnostic_stage()
    with pytest.raises(RuntimeError):
        raise RuntimeError("synthetic-later-failure")
    assert _g2_controlled_diagnostic_stage() == "UNKNOWN"


def test_G2_B_prepare_database源码绑定全部固定阶段且无异常正文通道():
    source = inspect.getsource(
        test_G2_B_Fresh真实数据库并发重放当前性权限与清理闭环
    )
    for stage in _G2_PREPARE_STAGES:
        assert re.search(rf'_diagnostic_call\(\s*"{stage}"', source)
    assert "except RuntimeError" not in source
    assert "str(error)" not in source


def test_G2_B_R_M05_Migration隔离入口存在且应用进程不承载用途全集():
    assert "_run_isolated_migration" in globals()
    helper = inspect.getsource(globals()["_run_isolated_migration"])
    assert "_migration_child_environment" in helper
    assert "KG_G2_MIGRATION_CONTROLLER_PATH" not in helper
    assert "KG_G2_MIGRATION_G1_RUN_ID" not in helper
    assert "KG_DATABASE_URL" not in helper
    assert "_DATABASE_URL" not in helper


def test_G2_B_Migration子进程必须由冻结Python跨平台自包含且不依赖仓库外controller():
    arguments = inspect.getsource(_migration_process_arguments)
    helper = inspect.getsource(_run_isolated_migration)
    assert "sys.executable" in arguments
    assert "pwsh.exe" not in arguments
    assert "KG_G2_MIGRATION_CONTROLLER_PATH" not in helper
    assert "KG_G2_MIGRATION_G1_RUN_ID" not in helper
    assert "os.environ.copy()" not in helper
    assert "capture_output=True" in helper
    assert "timeout=620" in helper


def test_G2_B_Migration隔离子进程必须锁定当前backend导入根而非editable旧检出():
    program = _G2_MIGRATION_CHILD_PROGRAM
    assert "from pathlib import Path" in program
    assert "backend_root = Path.cwd().resolve(strict=True)" in program
    assert "sys.path.insert(0, str(backend_root))" in program
    assert "find_spec(\"app\")" in program
    assert "is_relative_to(backend_root)" in program
    assert "KG_G2_MIGRATION_IMPORT_BOUNDARY_INVALID" in program


def test_G2_B_pytest主进程app与测试模块必须来自当前批准worktree():
    app_spec = importlib.util.find_spec("app")
    assert app_spec is not None
    assert app_spec.origin is not None
    assert _python_runtime_identity_is_approved(
        executable_path=sys.executable,
        resolved_executable_path=Path(sys.executable).resolve(strict=True),
        version=sys.version_info[:3],
        app_origin=Path(app_spec.origin).resolve(strict=True),
        test_origin=Path(__file__).resolve(strict=True),
        backend_root=BACKEND_ROOT,
        environment_prefix=sys.prefix,
        platform_name=sys.platform,
    )


def test_G2_B_Python身份合同兼容正式Linux与Windows且拒绝错误解释器和checkout():
    backend = "C:/approved/backend"
    common = {
        "version": (3, 11, 16),
        "app_origin": f"{backend}/app/__init__.py",
        "test_origin": f"{backend}/tests/integration/test_g2.py",
        "backend_root": backend,
        "environment_prefix": f"{backend}/.venv",
        "platform_name": "linux",
    }
    assert _python_runtime_identity_is_approved(
        executable_path=f"{backend}/.venv/bin/python",
        resolved_executable_path="/opt/hostedtoolcache/Python/3.11.16/x64/bin/python3.11",
        **common,
    )
    assert _python_runtime_identity_is_approved(
        executable_path=f"{backend}\\.venv\\Scripts\\python.exe",
        resolved_executable_path="C:/Python311/python.exe",
        **{
            **common,
            "app_origin": "C:/Approved/backend/app/__init__.py",
            "platform_name": "win32",
        },
    )
    assert not _python_runtime_identity_is_approved(
        executable_path="C:/outside/python.exe",
        resolved_executable_path="C:/Python311/python.exe",
        **common,
    )
    assert not _python_runtime_identity_is_approved(
        executable_path=f"{backend}/.venv/bin/python",
        resolved_executable_path="/opt/python3.12",
        **common,
    )
    assert not _python_runtime_identity_is_approved(
        executable_path=f"{backend}/.venv/bin/python",
        resolved_executable_path="/opt/python3.11",
        **{**common, "app_origin": "C:/wrong/app/__init__.py"},
    )
    assert not _python_runtime_identity_is_approved(
        executable_path=f"{backend}/.venv/bin/python",
        resolved_executable_path="/opt/python3.11",
        **{**common, "app_origin": "C:/Approved/backend/app/__init__.py"},
    )


def test_G2_B_Migration离线预检必须加载配置Revision图且不执行env_migrations(tmp_path):
    result = _run_migration_offline_preflight()
    assert result == {
        "code": "KG_G2_MIGRATION_OFFLINE_PREFLIGHT_PASSED",
        "head": "20260916_0049",
        "status": "PASSED",
        "target": "20260914_0047",
    }
    assert "command.upgrade" not in _G2_MIGRATION_OFFLINE_PREFLIGHT_PROGRAM
    assert "command.downgrade" not in _G2_MIGRATION_OFFLINE_PREFLIGHT_PROGRAM
    assert "asyncpg" not in _G2_MIGRATION_OFFLINE_PREFLIGHT_PROGRAM
    rejected = subprocess.run(
        [sys.executable, "-I", "-c", _G2_MIGRATION_OFFLINE_PREFLIGHT_PROGRAM],
        cwd=tmp_path,
        env={
            name: value
            for name in _G2_CHILD_SYSTEM_ENVIRONMENT
            if (value := os.environ.get(name))
        },
        check=False,
        capture_output=True,
        timeout=60,
    )
    assert rejected.returncode == 87
    assert rejected.stderr == b""
    assert json.loads(rejected.stdout) == {
        "code": "KG_G2_MIGRATION_OFFLINE_PREFLIGHT_FAILED",
        "exception_type": "RuntimeError",
        "stage": "APPLICATION_IMPORT",
        "status": "FAILED",
    }


def test_G2_B_Migration诊断只映射实际版本源码且错误链不泄漏(tmp_path):
    namespace: dict[str, object] = {}
    exec(_G2_MIGRATION_DIAGNOSTIC_SUPPORT, namespace)
    backend_root = tmp_path / "backend"
    versions = backend_root / "app" / "migrations" / "versions"
    versions.mkdir(parents=True)
    approved = versions / "20260915_0048_safe.py"
    approved.write_text(
        'revision = "20260915_0048"\n'
        'def fail():\n'
        '    raise RuntimeError("PLATFORM_REVIEWER_BOUNDED_READ_CONFIGURATION_INVALID")\n',
        encoding="utf-8",
    )
    module_namespace: dict[str, object] = {}
    exec(compile(approved.read_text(encoding="utf-8"), str(approved), "exec"), module_namespace)
    try:
        module_namespace["fail"]()
    except RuntimeError as inner:
        try:
            raise ValueError("synthetic-credentialed-url") from inner
        except ValueError as outer:
            observed = namespace["migration_error_diagnostic"](outer, backend_root)
    assert observed == {
        "source_code": "PLATFORM_REVIEWER_BOUNDED_READ_CONFIGURATION_INVALID",
        "source_line": 3,
        "source_revision": "20260915_0048",
        "source_sha256": hashlib.sha256(approved.read_bytes()).hexdigest().upper(),
    }
    assert "synthetic" not in json.dumps(observed)

    string_namespace: dict[str, object] = {}
    exec(
        compile(
            "def propagate(callback):\n    callback()\n",
            "<string>",
            "exec",
        ),
        string_namespace,
    )
    try:
        string_namespace["propagate"](module_namespace["fail"])
    except RuntimeError as inner:
        try:
            raise ValueError("synthetic-credentialed-url") from inner
        except ValueError as outer:
            observed_with_string_frame = namespace["migration_error_diagnostic"](
                outer, backend_root
            )
    assert observed_with_string_frame == observed

    forged = tmp_path / "forged" / approved.name
    forged.parent.mkdir()
    forged.write_text(approved.read_text(encoding="utf-8"), encoding="utf-8")
    forged_namespace: dict[str, object] = {}
    exec(compile(forged.read_text(encoding="utf-8"), str(forged), "exec"), forged_namespace)
    try:
        forged_namespace["fail"]()
    except RuntimeError as error:
        assert namespace["migration_error_diagnostic"](error, backend_root) == {
            "source_code": "UNKNOWN",
            "source_line": "UNKNOWN",
            "source_revision": "UNKNOWN",
            "source_sha256": "UNKNOWN",
        }
    assert namespace["migration_error_diagnostic"](
        RuntimeError("synthetic-private-value"), backend_root
    ) == {
        "source_code": "UNKNOWN",
        "source_line": "UNKNOWN",
        "source_revision": "UNKNOWN",
        "source_sha256": "UNKNOWN",
    }


def test_G2_B_Migration静态RuntimeError目录闭合且不导入执行Revision():
    catalog = _migration_runtime_error_catalog()
    assert catalog
    assert all(
        set(entry)
        == {
            "environment_names",
            "revision",
            "source_code",
            "source_line",
            "source_sha256",
        }
        for entry in catalog
    )
    reviewer = [entry for entry in catalog if entry["revision"] == "20260915_0048"]
    assert reviewer == [
        {
            "environment_names": (
                "KG_DATABASE_USER",
                "KG_IDENTITY_APPLICATION_DATABASE_URL",
                "KG_MEMBER_IDENTITY_REVIEW_WRITER_DATABASE_URL",
                "KG_MEMBER_IDENTITY_REVIEW_WRITER_ROLE",
            ),
            "revision": "20260915_0048",
            "source_code": "PLATFORM_REVIEWER_BOUNDED_READ_CONFIGURATION_INVALID",
            "source_line": 22,
            "source_sha256": hashlib.sha256(
                (
                    BACKEND_ROOT
                    / "app"
                    / "migrations"
                    / "versions"
                    / "20260915_0048_平台实名审核Reviewer当前性受限读取.py"
                ).read_bytes()
            ).hexdigest().upper(),
        }
    ]
    helper = inspect.getsource(_migration_runtime_error_catalog)
    assert "import_module" not in helper
    assert "exec(" not in helper
    assert "upgrade(" not in helper


def test_G2_B_Alembic连接owner与41Revision业务授予角色必须分离():
    versions_root = BACKEND_ROOT / "app" / "migrations" / "versions"
    revision_sources = {
        path.name: path.read_text(encoding="utf-8")
        for path in versions_root.glob("*.py")
    }
    env_source = (BACKEND_ROOT / "app" / "migrations" / "env.py").read_text(
        encoding="utf-8"
    )
    assert 'context.config.get_main_option("sqlalchemy.url")' in env_source
    assert "KG_DATABASE_USER" not in env_source
    assert all("KG_DATABASE_URL" not in source for source in revision_sources.values())
    application_role_revisions = {
        name for name, source in revision_sources.items() if "KG_DATABASE_USER" in source
    }
    assert application_role_revisions == {
        "20260814_0018_p3_basic_projection_shadow_ready_gate.py",
        "20260815_0019_p3_ready_projection_internal_read_boundary.py",
        "20260816_0020_phase1_slice1_controlled_institution_onboarding.py",
        "20260817_0021_phase1_slice2_therapist_qualification_service_ready.py",
        "20260818_0022_phase1_slice3_member_proxy_consent_service_case.py",
        "20260821_0024_phase1_slice3_member_currentness_authority.py",
        "20260823_0028_phase1_slice4_health_record_assessment_readiness.py",
        "20260906_0039_健管师机构身份闭合读取.py",
        "20260909_0040_认证主体与当前身份受限读取.py",
        "20260910_0041_注册会员受限写入.py",
        "20260911_0042_机构当前性受限读取.py",
        "20260913_0044_机构邀请Reviewer锁定当前性.py",
        "20260914_0047_R4旧健康接口受限兼容边界.py",
        "20260915_0048_平台实名审核Reviewer当前性受限读取.py",
    }
    assert all(
        "KG_IDENTITY_APPLICATION_DATABASE_URL" in revision_sources[name]
        for name in application_role_revisions
        if not name.startswith("20260814_0018_")
    )


def test_G2_B_Migration正式Workflow应用角色别名精确闭合且其他职责保持隔离():
    workflow = (
        BACKEND_ROOT.parent / ".github" / "workflows" / "p2-foundation-ci.yml"
    ).read_text(encoding="utf-8")
    assert '"KG_DATABASE_USER": roles["KG_TEST_APPLICATION_ROLE"]' in workflow
    roles = {
        "KG_DATABASE_USER": "kg_ci_app_synthetic",
        "KG_TEST_APPLICATION_ROLE": "kg_ci_app_synthetic",
        "KG_TEST_MIGRATION_ROLE": "kg_ci_migration_synthetic",
        "KG_READONLY_ROLE": "kg_ci_readonly_synthetic",
        "KG_TEST_READONLY_ROLE": "kg_ci_readonly_synthetic",
        "KG_TEST_VERIFICATION_WRITER_ROLE": "kg_ci_writer_synthetic",
        "KG_TEST_MEMBER_IDENTITY_REVIEW_WRITER_ROLE": "kg_ci_review_synthetic",
    }
    assert _migration_role_aliases_are_approved(roles)
    assert not _migration_role_aliases_are_approved(
        {**roles, "KG_DATABASE_USER": "kg_ci_other_synthetic"}
    )
    assert not _migration_role_aliases_are_approved(
        {**roles, "KG_TEST_MIGRATION_ROLE": roles["KG_DATABASE_USER"]}
    )
    assert not _migration_role_aliases_are_approved(
        {
            **roles,
            "KG_TEST_MEMBER_IDENTITY_REVIEW_WRITER_ROLE": roles[
                "KG_TEST_VERIFICATION_WRITER_ROLE"
            ],
        }
    )


def test_G2_B_Migration最终子环境必须区分owner覆盖与业务manifest角色(monkeypatch):
    roles, urls, keys = _migration_purpose_names()
    role_values = {
        name: f"r{index:02d}_{hashlib.sha256(name.encode()).hexdigest()[:12]}"
        for index, name in enumerate(roles)
    }
    application_role = "kg_ci_app_synthetic"
    role_values["KG_DATABASE_USER"] = application_role
    role_values["KG_READONLY_ROLE"] = role_values["KG_TEST_READONLY_ROLE"]
    role_values["KG_TEST_MIGRATION_ROLE"] = "postgres"
    for name, value in role_values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("KG_TEST_APPLICATION_ROLE", application_role)
    task_database = "kg_it_0123456789abcdef"
    for name in urls:
        username = role_values[_migration_role_name_for_url(name)]
        monkeypatch.setenv(
            name,
            f"postgresql+asyncpg://{username}:synthetic@127.0.0.1:5432/source_db",
        )
    monkeypatch.setenv(
        "KG_TEST_MIGRATION_DATABASE_URL",
        f"postgresql+asyncpg://postgres:synthetic@127.0.0.1:5432/{task_database}",
    )
    for name in keys:
        monkeypatch.setenv(name, "synthetic-non-secret-test-value")
    child = _migration_child_environment(
        action="upgrade-head",
        task_database=task_database,
        g1_run_id="0123456789abcdef",
    )
    report = _migration_child_semantic_report(child, task_database=task_database)
    assert report == {
        "application_role_preserved": True,
        "business_roles_distinct_from_owner": True,
        "manifest_counts_match": True,
        "migration_connection_owner_bound": True,
        "referenced_environment_present": True,
        "target_scope_match": True,
        "url_role_bindings_match": True,
    }
    assert child["KG_TEST_MIGRATION_ROLE"] == "postgres"
    assert os.environ["KG_DATABASE_USER"] != "postgres"
    assert child["KG_DATABASE_USER"] == os.environ["KG_DATABASE_USER"]
    offline = _run_final_child_environment_offline_preflight(
        task_database=task_database,
        g1_run_id="0123456789abcdef",
    )
    assert offline["status"] == "PASSED"
    assert 'if action == "offline-preflight"' in _G2_MIGRATION_CHILD_PROGRAM
    assert _G2_MIGRATION_CHILD_PROGRAM.index(
        'if action == "offline-preflight"'
    ) < _G2_MIGRATION_CHILD_PROGRAM.index("asyncio.run(verify_target())")

    def assert_child_rejects(
        candidate: dict[str, str], expected_code: str, expected_stage: str
    ) -> None:
        candidate["KG_G2_CHILD_ACTION"] = "offline-preflight"
        returncode, payload = _execute_child_environment_offline_preflight(
            candidate,
            task_database=task_database,
            g1_run_id="0123456789abcdef",
        )
        assert returncode == 86
        assert payload["status"] == "FAILED"
        assert payload["code"] == expected_code
        assert payload["operation_stage"] == expected_stage
        assert payload["source_code"] == "UNKNOWN"
        assert "synthetic" not in json.dumps(payload).lower()

    wrong_order = {**child, "KG_DATABASE_USER": "postgres"}
    wrong_order_report = _migration_child_semantic_report(
        wrong_order, task_database=task_database
    )
    assert wrong_order_report["application_role_preserved"] is False
    assert wrong_order_report["business_roles_distinct_from_owner"] is False
    assert wrong_order_report["url_role_bindings_match"] is False
    assert_child_rejects(
        wrong_order, "KG_G2_MIGRATION_RUNTIME_INVALID", "RUNTIME_VALIDATION"
    )

    owner_mixed = {
        **child,
        "KG_TEST_MIGRATION_ROLE": child["KG_DATABASE_USER"],
    }
    owner_mixed_report = _migration_child_semantic_report(
        owner_mixed, task_database=task_database
    )
    assert owner_mixed_report["business_roles_distinct_from_owner"] is False
    assert owner_mixed_report["migration_connection_owner_bound"] is False
    assert_child_rejects(
        owner_mixed, "KG_G2_MIGRATION_RUNTIME_INVALID", "RUNTIME_VALIDATION"
    )

    wrong_url = dict(child)
    identity_url = make_url(wrong_url["KG_IDENTITY_APPLICATION_DATABASE_URL"])
    wrong_url["KG_IDENTITY_APPLICATION_DATABASE_URL"] = identity_url.set(
        username="postgres"
    ).render_as_string(hide_password=False)
    assert _migration_child_semantic_report(
        wrong_url, task_database=task_database
    )["url_role_bindings_match"] is False
    assert_child_rejects(
        wrong_url, "KG_G2_MIGRATION_URL_BINDING_INVALID", "URL_REBIND"
    )

    missing_field = dict(child)
    del missing_field["KG_MEMBER_IDENTITY_REVIEW_WRITER_ROLE"]
    missing_report = _migration_child_semantic_report(
        missing_field, task_database=task_database
    )
    assert missing_report["manifest_counts_match"] is False
    assert missing_report["referenced_environment_present"] is False
    assert_child_rejects(
        missing_field, "KG_G2_MIGRATION_MANIFEST_INVALID", "MANIFEST_VALIDATION"
    )

    wrong_target = dict(child)
    writer_url = make_url(wrong_target["KG_MEMBER_IDENTITY_REVIEW_WRITER_DATABASE_URL"])
    wrong_target["KG_MEMBER_IDENTITY_REVIEW_WRITER_DATABASE_URL"] = writer_url.set(
        database="wrong_target"
    ).render_as_string(hide_password=False)
    assert _migration_child_semantic_report(
        wrong_target, task_database=task_database
    )["target_scope_match"] is False
    assert_child_rejects(
        wrong_target, "KG_G2_MIGRATION_URL_BINDING_INVALID", "URL_REBIND"
    )


def test_G2_B_R_M06_upgrade_downgrade_reupgrade统一短时隔离入口():
    source = inspect.getsource(
        test_G2_B_Fresh真实数据库并发重放当前性权限与清理闭环
    )
    assert source.count("_run_isolated_migration(") == 3
    assert '"upgrade-head"' in source
    assert '"downgrade-0047"' in source
    assert '"reupgrade-head"' in source
    assert "command.upgrade" not in source
    assert "command.downgrade" not in source


def test_G2_B_Migration三阶段闭合且成功清除失败保留():
    expected = {
        "upgrade-head": "MIGRATION_UPGRADE",
        "downgrade-0047": "MIGRATION_DOWNGRADE",
        "reupgrade-head": "MIGRATION_REUPGRADE",
    }
    assert expected == _G2_MIGRATION_STAGES
    assert set(expected.values()) <= _G2_CONTROLLED_STAGES
    helper = inspect.getsource(_run_isolated_migration)
    assert "stage = _G2_MIGRATION_STAGES[action]" in helper
    assert helper.index("_set_g2_controlled_diagnostic_stage(stage)") < helper.index(
        "subprocess.run("
    )
    assert "_set_g2_controlled_diagnostic_stage(previous)" in helper


def test_G2_B_Migration子进程诊断只接受闭合JSON与固定枚举():
    valid = {
        "action": "upgrade-head",
        "cleanup_status": "PASSED",
        "code": "KG_G2_MIGRATION_EXECUTION_FAILED",
        "exception_type": "ChildProcessFailure",
        "exit_code": 1,
        "operation_stage": "ALEMBIC_RUN",
        "schema_version": 1,
        "source_code": "UNKNOWN",
        "source_line": "UNKNOWN",
        "source_location": "ALEMBIC_CHILD_PROCESS",
        "source_revision": "UNKNOWN",
        "source_sha256": "UNKNOWN",
        "status": "FAILED",
        "timed_out": False,
    }
    parsed = _parse_migration_controller_result(
        json.dumps(valid).encode(), action="upgrade-head", returncode=1
    )
    assert parsed == valid
    reviewer_source = (
        BACKEND_ROOT
        / "app"
        / "migrations"
        / "versions"
        / "20260915_0048_平台实名审核Reviewer当前性受限读取.py"
    )
    approved_source = {
        **valid,
        "source_code": "PLATFORM_REVIEWER_BOUNDED_READ_CONFIGURATION_INVALID",
        "source_line": 22,
        "source_location": "MIGRATION_VERSION_SOURCE",
        "source_revision": "20260915_0048",
        "source_sha256": hashlib.sha256(reviewer_source.read_bytes())
        .hexdigest()
        .upper(),
    }
    assert _parse_migration_controller_result(
        json.dumps(approved_source).encode(), action="upgrade-head", returncode=1
    ) == approved_source
    forged_source = {**approved_source, "source_line": 23}
    assert _parse_migration_controller_result(
        json.dumps(forged_source).encode(), action="upgrade-head", returncode=1
    )["code"] == "UNKNOWN"
    invalid_payloads = []
    for key, value in (
        ("code", "KG_G2_MIGRATION_ARBITRARY"),
        ("operation_stage", "FREE_TEXT"),
        ("source_location", "app/migrations/secret.py:123"),
        ("exception_type", "VendorSecretError"),
        ("exit_code", 999999),
    ):
        candidate = dict(valid)
        candidate[key] = value
        invalid_payloads.append(json.dumps(candidate).encode())
    extra = dict(valid)
    extra["detail"] = "forbidden"
    invalid_payloads.extend(
        (
            json.dumps(extra).encode(),
            b"not-json",
            (json.dumps(valid) + json.dumps(valid)).encode(),
            b"x" * 4097,
        )
    )
    for payload in invalid_payloads:
        assert _parse_migration_controller_result(
            payload, action="upgrade-head", returncode=1
        )["code"] == "UNKNOWN"


def test_G2_B_Migration子进程启动超时非零与清理矩阵不丢主失败():
    matrix = (
        ("KG_G2_MIGRATION_ARGUMENT_INVALID", "ARGUMENT_VALIDATION"),
        ("KG_G2_MIGRATION_MANIFEST_INVALID", "MANIFEST_VALIDATION"),
        ("KG_G2_MIGRATION_RUNTIME_INVALID", "RUNTIME_VALIDATION"),
        ("KG_G2_MIGRATION_OWNERSHIP_INVALID", "OWNERSHIP_VALIDATION"),
        ("KG_G2_MIGRATION_URL_BINDING_INVALID", "URL_REBIND"),
        ("KG_G2_MIGRATION_PROCESS_START_FAILED", "ALEMBIC_LAUNCH"),
        ("KG_G2_MIGRATION_TIMEOUT", "ALEMBIC_RUN"),
        ("KG_G2_MIGRATION_EXECUTION_FAILED", "ALEMBIC_RUN"),
        ("KG_G2_MIGRATION_PRIVATE_CLEANUP_FAILED", "PRIVATE_CLEANUP"),
    )
    assert set(matrix) <= _G2_MIGRATION_DIAGNOSTIC_MATRIX
    source = inspect.getsource(_run_isolated_migration)
    assert "capture_output=True" in source
    assert "completed.stderr" not in source
    assert "_parse_migration_controller_result" in source


def test_G2_B_并发结果摘要固定分类且不携带异常正文():
    class SyntheticBootstrapError(RuntimeError):
        pass

    cases = (
        (
            [None, None],
            "KG_G2_CONCURRENCY_DOUBLE_SUCCESS",
            (2, 0, 0, "UNKNOWN"),
        ),
        (
            [None, SyntheticBootstrapError("synthetic-secret")],
            "KG_G2_CONCURRENCY_EXPECTED",
            (1, 1, 0, "UNKNOWN"),
        ),
        (
            [SyntheticBootstrapError("first"), SyntheticBootstrapError("second")],
            "KG_G2_CONCURRENCY_DOUBLE_REJECTION",
            (0, 2, 0, "UNKNOWN"),
        ),
        (
            [None, RuntimeError("synthetic-credentialed-url")],
            "KG_G2_CONCURRENCY_UNEXPECTED_EXCEPTION",
            (1, 0, 1, "RuntimeError"),
        ),
        (
            [None, ConnectionError("synthetic-vendor-secret")],
            "KG_G2_CONCURRENCY_UNEXPECTED_EXCEPTION",
            (1, 0, 1, "UNKNOWN"),
        ),
    )
    for results, code, counts in cases:
        summary = _summarize_concurrency_results(results, SyntheticBootstrapError)
        assert set(summary) == _G2_CONCURRENCY_KEYS
        assert summary["code"] == code
        assert (
            summary["success_count"],
            summary["bootstrap_count"],
            summary["other_count"],
            summary["other_type"],
        ) == counts
        serialized = json.dumps(summary, sort_keys=True)
        assert "synthetic" not in serialized
    assert _summarize_concurrency_results(
        [None], SyntheticBootstrapError
    ) == _unknown_concurrency_diagnostic()


def test_G2_B_Fresh并发与连接池隔离恢复必须由真实旅程证明():
    source = inspect.getsource(
        test_G2_B_Fresh真实数据库并发重放当前性权限与清理闭环
    )
    for required in (
        "overlap_barrier",
        "overlapped_scope",
        'summary["code"] == "KG_G2_CONCURRENCY_EXPECTED"',
        'summary["success_count"] == 1',
        'summary["bootstrap_count"] == 1',
        'summary["other_count"] == 0',
        'SHOW transaction_isolation',
        'observed_confirmation_isolation == ["serializable"]',
    ):
        assert required in source
    owner_engine_source = source[source.index("owner_engine =") :]
    assert "poolclass=NullPool" not in owner_engine_source.split("try:", 1)[0]


def test_G2_B_HTTP诊断默认值与状态值闭合且不接收响应对象():
    getter = globals()["_g2_controlled_diagnostic_http"]
    reset = globals()["_reset_g2_controlled_http_statuses"]
    record = globals()["_record_g2_controlled_http_status"]
    reset()
    assert getter() == {
        "AUTH_LOGIN": "UNKNOWN",
        "AUTH_ME": "UNKNOWN",
        "PLATFORM_IDENTITY_REVIEWS": "UNKNOWN",
        "AUTH_ME_AFTER_DISABLE": "UNKNOWN",
    }
    record("AUTH_LOGIN", 200)
    record("AUTH_ME", 599)
    record("PLATFORM_IDENTITY_REVIEWS", 99)
    record("AUTH_ME_AFTER_DISABLE", True)
    assert getter() == {
        "AUTH_LOGIN": 200,
        "AUTH_ME": 599,
        "PLATFORM_IDENTITY_REVIEWS": "UNKNOWN",
        "AUTH_ME_AFTER_DISABLE": "UNKNOWN",
    }
    assert "response" not in inspect.signature(record).parameters


def test_G2_B_HTTP旅程全部断言先写固定阶段整数状态():
    source = inspect.getsource(
        test_G2_B_Fresh真实数据库并发重放当前性权限与清理闭环
    )
    for stage, response_name in (
        ("AUTH_LOGIN", "login"),
        ("AUTH_ME", "me"),
        ("PLATFORM_IDENTITY_REVIEWS", "reviewer"),
        ("AUTH_ME_AFTER_DISABLE", "stale"),
    ):
        record = re.search(
            rf'_record_g2_controlled_http_status\(\s*"{stage}",\s*'
            rf"{response_name}\.status_code\s*\)",
            source,
        )
        assertion = f"assert {response_name}.status_code =="
        assert record is not None
        assert record.start() < source.index(assertion)


def test_G2_B_HTTP诊断公开结构只含固定阶段与整数或UNKNOWN():
    source = inspect.getsource(globals()["_g2_controlled_diagnostic_http"])
    assert "body" not in source
    assert "headers" not in source
    assert "token" not in source
    assert "query" not in source
    assert "response" not in source


def test_G2_B_平台审核列表正式依赖角色URL与基线权限边界保持闭合():
    api_source = (
        BACKEND_ROOT / "app" / "modules" / "member_enrollment" / "api.py"
    ).read_text(encoding="utf-8")
    database_source = (BACKEND_ROOT / "app" / "core" / "database.py").read_text(
        encoding="utf-8"
    )
    config_source = (BACKEND_ROOT / "app" / "core" / "config.py").read_text(
        encoding="utf-8"
    )
    migration_source = (
        BACKEND_ROOT
        / "app"
        / "migrations"
        / "versions"
        / "20260818_0022_phase1_slice3_member_proxy_consent_service_case.py"
    ).read_text(encoding="utf-8")
    assert '@platform_router.get("/member-identity-reviews"' in api_source
    assert "Depends(get_current_user_from_jwt)" in api_source
    assert 'get_slice3_session_factory("identity_review_writer")' in api_source
    assert 'actor.role != "super_admin"' in api_source
    assert "actor.tenant_id is not None" in api_source
    assert "actor.org_id is not None" in api_source
    assert (
        '"identity_review_writer": settings.member_identity_review_writer_database_url'
        in database_source
    )
    assert (
        '"identity_review_writer": settings.member_identity_review_writer_role'
        in database_source
    )
    assert "KG_MEMBER_IDENTITY_REVIEW_WRITER_DATABASE_URL" in config_source
    assert "KG_MEMBER_IDENTITY_REVIEW_WRITER_ROLE" in config_source
    assert "slice3_platform_identity_review_read_v1" in migration_source
    assert "GRANT SELECT ON TABLE public.slice3_platform_identity_review_read_v1" in migration_source


def test_G2_B_平台审核当前性断言绑定PR17后真实闭合边界而非旧row源码形状():
    from app.modules.member_enrollment import api as member_enrollment_api

    require_source = inspect.getsource(member_enrollment_api._require_platform)
    endpoint_source = inspect.getsource(member_enrollment_api.identity_reviews)
    assert 'actor.role != "super_admin"' in require_source
    assert "actor.tenant_id is not None" in require_source
    assert "actor.org_id is not None" in require_source
    assert "_require_platform(actor)" in endpoint_source
    assert 'get_slice3_session_factory("identity_review_writer")' in endpoint_source
    assert "slice3_platform_identity_review_read_v1" in endpoint_source


def test_G2_B_HTTP未知异常观察默认值与载荷闭合():
    reset = globals()["_reset_g2_controlled_http_exception"]
    getter = globals()["_g2_controlled_diagnostic_http_exception"]
    reset()
    assert getter() == {
        "exception_type": "UNKNOWN",
        "source_code": "UNKNOWN",
        "source_line": "UNKNOWN",
        "source_sha256": "UNKNOWN",
        "sqlstate": "UNKNOWN",
        "stage": "UNKNOWN",
    }
    source = inspect.getsource(getter)
    for forbidden in ("message", "args", "locals", "sql", "url", "body", "headers", "token"):
        assert forbidden not in source.lower()


def test_G2_B_HTTP未知异常观察仅允许固定阶段类型SQLSTATE与批准源码():
    reset = globals()["_reset_g2_controlled_http_exception"]
    record = globals()["_record_g2_controlled_http_exception"]
    getter = globals()["_g2_controlled_diagnostic_http_exception"]

    class SyntheticDatabaseError(RuntimeError):
        sqlstate = "42501"

    reset()
    try:
        raise SyntheticDatabaseError("safe synthetic")
    except SyntheticDatabaseError as error:
        record("VIEW_QUERY", error)
    observed = getter()
    assert observed["stage"] == "VIEW_QUERY"
    assert observed["exception_type"] == "RuntimeError"
    assert observed["sqlstate"] == "42501"
    assert observed["source_code"] == "G2_FRESH_INTEGRATION_SOURCE"
    assert isinstance(observed["source_line"], int)
    assert observed["source_sha256"] == hashlib.sha256(
        Path(__file__).read_bytes()
    ).hexdigest().upper()

    reset()
    try:
        raise SyntheticDatabaseError("safe synthetic")
    except SyntheticDatabaseError as error:
        error.sqlstate = "unsafe-free-text"
        record("UNAPPROVED_STAGE", error)
    assert getter() == {
        "exception_type": "UNKNOWN",
        "source_code": "UNKNOWN",
        "source_line": "UNKNOWN",
        "source_sha256": "UNKNOWN",
        "sqlstate": "UNKNOWN",
        "stage": "UNKNOWN",
    }


def test_G2_B_HTTP未知异常六层观察保持原异常身份且不改变返回分类():
    observe = globals()["_observe_g2_http_exception"]
    stages = (
        "REVIEWER_CURRENTNESS",
        "DEPENDENCY_FACTORY",
        "SESSION_CONTEXT",
        "VIEW_QUERY",
        "DTO_MAPPING",
        "RESPONSE_SERIALIZATION",
    )

    async def fail(error):
        raise error

    for stage in stages:
        marker = RuntimeError("safe synthetic")
        with pytest.raises(RuntimeError) as caught:
            _run(observe(stage, fail(marker)))
        assert caught.value is marker
        assert _g2_controlled_diagnostic_http_exception()["stage"] == stage


def test_G2_B_HTTP未知异常六层合成注入仍由真实路由包装器返回500(monkeypatch):
    import fastapi.routing as fastapi_routing
    from fastapi import APIRouter, HTTPException
    from pydantic import BaseModel

    from app.main import create_app
    from app.modules.member_enrollment import api as member_enrollment_api

    class SyntheticOutput(BaseModel):
        value: int

    original_serialize_response = fastapi_routing.serialize_response

    async def observed_serialize_response(*args, **kwargs):
        return await _observe_g2_http_exception(
            "RESPONSE_SERIALIZATION",
            original_serialize_response(*args, **kwargs),
        )

    monkeypatch.setattr(
        fastapi_routing, "serialize_response", observed_serialize_response
    )
    route_path = "/synthetic-g2-http-observer"
    monkeypatch.setitem(
        member_enrollment_api.SLICE3_ROUTE_ERROR_CODES,
        ("GET", route_path),
        {
            401: ("AUTHENTICATION_REQUIRED",),
            403: ("REVIEWER_CURRENTNESS_FORBIDDEN",),
            503: ("DEPENDENCY_UNAVAILABLE",),
        },
    )
    router = APIRouter(route_class=member_enrollment_api.MemberEnrollmentRoute)

    async def fail():
        raise RuntimeError("safe synthetic")

    @router.get(route_path, response_model=SyntheticOutput)
    async def synthetic(stage: str = "SUCCESS"):
        if stage in _G2_HTTP_EXCEPTION_STAGES - {"RESPONSE_SERIALIZATION"}:
            if stage == "DTO_MAPPING":
                _observe_g2_http_exception_sync(
                    stage, lambda: (_ for _ in ()).throw(RuntimeError("safe synthetic"))
                )
            await _observe_g2_http_exception(stage, fail())
        if stage == "RESPONSE_SERIALIZATION":
            return {"value": "invalid"}
        if stage == "AUTH_401":
            raise HTTPException(401, {"code": "AUTHENTICATION_REQUIRED"})
        if stage == "CURRENTNESS_403":
            raise HTTPException(403, {"code": "REVIEWER_CURRENTNESS_FORBIDDEN"})
        if stage == "DEPENDENCY_503":
            raise HTTPException(503, {"code": "DEPENDENCY_UNAVAILABLE"})
        return {"value": 1}

    app = create_app()
    app.include_router(router)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get(route_path).status_code == 200
        for stage in _G2_HTTP_EXCEPTION_STAGES:
            _reset_g2_controlled_http_exception()
            assert client.get(route_path, params={"stage": stage}).status_code == 500
            assert _g2_controlled_diagnostic_http_exception()["stage"] == stage
        assert client.get(route_path, params={"stage": "AUTH_401"}).status_code == 401
        assert client.get(route_path, params={"stage": "CURRENTNESS_403"}).status_code == 403
        assert client.get(route_path, params={"stage": "DEPENDENCY_503"}).status_code == 503


def test_G2_B_R_M07迁移控制参数不携带URL凭据或自由动作(monkeypatch):
    assert "_migration_process_arguments" in globals()
    monkeypatch.setenv("KG_DATABASE_PASSWORD", "synthetic-command-line-secret")
    helper = globals()["_migration_process_arguments"]
    arguments = helper(
        action="upgrade-head",
        task_database="kg_it_0123456789abcdef",
        g1_run_id="fedcba9876543210",
    )
    rendered = " ".join(arguments)
    assert arguments[:3] == [sys.executable, "-I", "-c"]
    assert "upgrade-head" not in rendered
    assert "kg_it_0123456789abcdef" not in rendered
    assert "fedcba9876543210" not in rendered
    assert "postgresql" not in rendered
    assert "synthetic-command-line-secret" not in rendered
    with pytest.raises(ValueError):
        helper(
            action="arbitrary-action",
            task_database="kg_it_0123456789abcdef",
            g1_run_id="fedcba9876543210",
        )


def test_G2_B_R_M08短时迁移子进程删除全部父进程KG环境且错误闭合(monkeypatch):
    monkeypatch.setenv("KG_SYNTHETIC_MIGRATION_ONLY_SECRET", "must-not-propagate")
    observed = {}
    safe_child_environment = {
        "KG_G2_CHILD_ACTION": "upgrade-head",
        "KG_G2_CHILD_RUN_ID": "0123456789abcdef",
        "KG_G2_CHILD_TASK_DATABASE": "kg_it_0123456789abcdef",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    monkeypatch.setattr(
        sys.modules[__name__],
        "_migration_child_environment",
        lambda **_kwargs: dict(safe_child_environment),
    )

    def result_bytes(
        *,
        code="KG_G2_MIGRATION_COMPLETED",
        exception_type="None",
        exit_code=0,
        operation_stage="COMPLETED",
        source_location="ALEMBIC_CHILD_PROCESS",
        status="PASSED",
    ):
        return json.dumps(
            {
                "action": "upgrade-head",
                "cleanup_status": "PASSED",
                "code": code,
                "exception_type": exception_type,
                "exit_code": exit_code,
                "operation_stage": operation_stage,
                "schema_version": 1,
                "source_code": "UNKNOWN",
                "source_line": "UNKNOWN",
                "source_location": source_location,
                "source_revision": "UNKNOWN",
                "source_sha256": "UNKNOWN",
                "status": status,
                "timed_out": False,
            }
        ).encode()

    def completed(arguments, **kwargs):
        observed["arguments"] = arguments
        observed.update(kwargs)
        return subprocess.CompletedProcess(arguments, 0, result_bytes(), b"")

    monkeypatch.setattr(subprocess, "run", completed)
    _run_isolated_migration("upgrade-head", "kg_it_0123456789abcdef")
    assert observed["cwd"] == BACKEND_ROOT
    assert observed["timeout"] == 620
    assert observed["capture_output"] is True
    assert observed["check"] is False
    assert observed["env"] == safe_child_environment
    assert "KG_SYNTHETIC_MIGRATION_ONLY_SECRET" not in observed["env"]
    assert all("must-not-propagate" not in value for value in observed["arguments"])

    def failed(arguments, **kwargs):
        return subprocess.CompletedProcess(
            arguments,
            1,
            result_bytes(
                code="KG_G2_MIGRATION_ALEMBIC_INTERNAL_FAILED",
                exception_type="OperationalError",
                exit_code=86,
                operation_stage="ALEMBIC_RUN",
                source_location="MIGRATION_VERSION_SOURCE",
                status="FAILED",
            ),
            b"synthetic-credentialed-url",
        )

    monkeypatch.setattr(subprocess, "run", failed)
    with pytest.raises(pytest.fail.Exception) as caught:
        _run_isolated_migration("upgrade-head", "kg_it_0123456789abcdef")
    assert str(caught.value) == "KG_G2_MIGRATION_EXECUTION_FAILED"
    assert "synthetic" not in str(caught.value)
    assert _g2_controlled_diagnostic_operation() == {
        "code": "KG_G2_MIGRATION_ALEMBIC_INTERNAL_FAILED",
        "exception_type": "OperationalError",
        "exit_code": 86,
        "operation_stage": "ALEMBIC_RUN",
        "source_code": "UNKNOWN",
        "source_line": "UNKNOWN",
        "source_location": "MIGRATION_VERSION_SOURCE",
        "source_revision": "UNKNOWN",
        "source_sha256": "UNKNOWN",
        "timed_out": False,
    }

    def cannot_start(arguments, **kwargs):
        raise OSError("synthetic-credentialed-url")

    monkeypatch.setattr(subprocess, "run", cannot_start)
    with pytest.raises(pytest.fail.Exception) as caught:
        _run_isolated_migration("upgrade-head", "kg_it_0123456789abcdef")
    assert str(caught.value) == "KG_G2_MIGRATION_EXECUTION_FAILED"
    assert _g2_controlled_diagnostic_operation()["code"] == (
        "KG_G2_MIGRATION_PROCESS_START_FAILED"
    )

    def times_out(arguments, **kwargs):
        raise subprocess.TimeoutExpired(arguments, 620)

    monkeypatch.setattr(subprocess, "run", times_out)
    with pytest.raises(pytest.fail.Exception) as caught:
        _run_isolated_migration("upgrade-head", "kg_it_0123456789abcdef")
    assert str(caught.value) == "KG_G2_MIGRATION_EXECUTION_FAILED"
    assert _g2_controlled_diagnostic_operation()["code"] == "KG_G2_MIGRATION_TIMEOUT"


def test_G2_B_Fresh真实数据库并发重放当前性权限与清理闭环(
    monkeypatch, tmp_path
) -> None:
    _set_g2_controlled_diagnostic_stage("UNKNOWN")
    _reset_g2_controlled_concurrency_results()
    _reset_g2_controlled_http_statuses()
    _reset_g2_controlled_http_exception()
    module = _load_runner()
    import fastapi.routing as fastapi_routing

    from app.core import database, 认证当前性
    from app.core.config import get_settings
    from app.core.database import get_db_session
    from app.main import create_app
    from app.modules.member_enrollment import api as member_enrollment_api
    from app.modules.member_enrollment.repository import MemberEnrollmentRepository

    run_id = secrets.token_hex(8)
    task_database = f"kg_it_{run_id}"
    migration_role = os.environ["KG_TEST_MIGRATION_ROLE"]
    application_role = os.environ["KG_TEST_APPLICATION_ROLE"]
    if (
        not _SAFE_DATABASE.fullmatch(task_database)
        or not _SAFE_ROLE.fullmatch(migration_role)
        or not _SAFE_ROLE.fullmatch(application_role)
        or migration_role == application_role
    ):
        pytest.fail("KG_G2_BOOTSTRAP_TEST_SCOPE_INVALID", pytrace=False)

    source_urls = _validated_source_urls()
    admin_url = _database_url_for_name(
        source_urls["KG_TEST_ROLE_ADMIN_DATABASE_URL"], "postgres"
    )
    migration_url = _database_url_for_name(
        source_urls["KG_TEST_MIGRATION_DATABASE_URL"], task_database
    )
    application_url = _database_url_for_name(
        source_urls["KG_TEST_DATABASE_URL"], task_database
    )
    task_admin_url = _database_url_for_name(
        source_urls["KG_TEST_ROLE_ADMIN_DATABASE_URL"], task_database
    )
    rebound_urls = {}
    for name in _FRESH_URL_ROLES:
        value = source_urls[name]
        rebound_urls[name] = _database_url_for_name(value, task_database)
        monkeypatch.setenv(name, rebound_urls[name])
    _assert_task_database_bindings(source_urls, rebound_urls, task_database)
    application_target = make_url(application_url)
    for name, value in {
        "KG_TEST_RUN_ID": run_id,
        "KG_TEST_DATABASE_URL": application_url,
        "KG_TEST_MIGRATION_DATABASE_URL": migration_url,
        "KG_TEST_ROLE_ADMIN_DATABASE_URL": task_admin_url,
        "KG_DATABASE_HOST": str(application_target.host),
        "KG_DATABASE_PORT": str(application_target.port),
        "KG_DATABASE_NAME": task_database,
        "KG_DATABASE_USER": str(application_target.username),
        "KG_DATABASE_PASSWORD": str(application_target.password),
        "KG_G2_BOOTSTRAP_ENVIRONMENT": "local_ephemeral",
        "KG_G2_BOOTSTRAP_DATABASE_HOST": "127.0.0.1",
        "KG_G2_BOOTSTRAP_DATABASE_NAME": task_database,
        "KG_G2_BOOTSTRAP_RUN_ID": run_id,
        "KG_G2_BOOTSTRAP_SENTINEL": "a" * 32,
        "KG_G2_BOOTSTRAP_MIGRATION_HEAD": "20260916_0049",
        "KG_G2_BOOTSTRAP_OWNER_DATABASE_URL": migration_url,
        "KG_PRIVATE_FILE_STORAGE_ROOT": str(tmp_path / "private"),
    }.items():
        monkeypatch.setenv(name, value)
    credential_directory = tmp_path / "g2-private"
    prepared_path = credential_directory / "prepared.json"
    prepared_temp_path = credential_directory / "prepared.tmp"
    receipt_path = credential_directory / "receipt.json"
    receipt_temp_path = credential_directory / "receipt.tmp"
    for name, path in {
        "KG_G2_BOOTSTRAP_PREPARED_PATH": prepared_path,
        "KG_G2_BOOTSTRAP_PREPARED_TEMP_PATH": prepared_temp_path,
        "KG_G2_BOOTSTRAP_RECEIPT_PATH": receipt_path,
        "KG_G2_BOOTSTRAP_RECEIPT_TEMP_PATH": receipt_temp_path,
    }.items():
        monkeypatch.setenv(name, str(path))
    get_settings.cache_clear()
    created = False

    async def prepare_database() -> None:
        nonlocal created
        admin = await _diagnostic_call(
            "PREPARE_ADMIN_CONNECT", asyncpg.connect, _to_asyncpg_dsn(admin_url)
        )
        try:
            await _diagnostic_call(
                "PREPARE_CREATE_DATABASE",
                admin.execute,
                f'CREATE DATABASE "{task_database}" OWNER "{migration_role}"',
            )
            created = True
            await _diagnostic_call(
                "PREPARE_COMMENT_DATABASE",
                admin.execute,
                f'COMMENT ON DATABASE "{task_database}" '
                f"IS 'kg-test-disposable:{run_id}'",
            )
        finally:
            await _diagnostic_call(
                "PREPARE_ADMIN_CLOSE", admin.close, restore_on_success=True
            )
        task_admin = await _diagnostic_call(
            "PREPARE_TASK_ADMIN_CONNECT",
            asyncpg.connect,
            _to_asyncpg_dsn(task_admin_url),
        )
        try:
            await _diagnostic_call(
                "PREPARE_CREATE_EXTENSION",
                task_admin.execute,
                "CREATE EXTENSION IF NOT EXISTS timescaledb",
            )
            await _diagnostic_call(
                "PREPARE_PUBLIC_SCHEMA_OWNER",
                task_admin.execute,
                f'ALTER SCHEMA public OWNER TO "{migration_role}"',
            )
            await _diagnostic_call(
                "PREPARE_PUBLIC_SCHEMA_REVOKE",
                task_admin.execute,
                "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
            )
        finally:
            await _diagnostic_call(
                "PREPARE_TASK_ADMIN_CLOSE", task_admin.close, restore_on_success=True
            )
        _complete_g2_prepare_diagnostic_stage()

    async def drop_database() -> None:
        admin = await asyncpg.connect(_to_asyncpg_dsn(admin_url))
        try:
            ownership = await admin.fetchrow(
                "SELECT pg_get_userbyid(datdba) AS owner, "
                "shobj_description(oid,'pg_database') AS sentinel "
                "FROM pg_database WHERE datname=$1",
                task_database,
            )
            if ownership is None:
                return
            if (
                ownership["owner"] != migration_role
                or ownership["sentinel"] != f"kg-test-disposable:{run_id}"
            ):
                raise RuntimeError("KG_G2_BOOTSTRAP_TEST_CLEANUP_OWNERSHIP_UNKNOWN")
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=$1 AND pid<>pg_backend_pid()", task_database,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{task_database}"')
            assert not await admin.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=$1)",
                task_database,
            )
        finally:
            await admin.close()

    async def verify_database_contract():
        owner_engine = create_async_engine(
            migration_url, isolation_level="SERIALIZABLE"
        )
        try:
            await module._require_initial_fresh(owner_engine)
            owner = await asyncpg.connect(_to_asyncpg_dsn(migration_url))
            try:
                await owner.execute(
                    'INSERT INTO public."user"(phone,password_hash,role,status) '
                    "VALUES('19900000001','synthetic','member','active')"
                )
            finally:
                await owner.close()
            with pytest.raises(module.BootstrapError) as unknown_existing:
                await module._require_initial_fresh(owner_engine)
            assert unknown_existing.value.code == "KG_G2_BOOTSTRAP_EXISTING_USER_UNKNOWN"
            owner = await asyncpg.connect(_to_asyncpg_dsn(migration_url))
            try:
                await owner.execute('DELETE FROM public."user" WHERE phone=$1', "19900000001")
            finally:
                await owner.close()

            scope = module.RuntimeScope(
                environment="local_ephemeral", database_host="127.0.0.1",
                database_name=task_database, run_id=run_id, sentinel="a" * 32,
                migration_head="20260916_0049",
            )
            request, replay = module._load_or_prepare_request(scope)
            assert replay is False
            connection = await owner_engine.connect()
            transaction = await connection.begin()
            try:
                await connection.execute(
                    text(
                        'INSERT INTO public."user"(phone,password_hash,role,status) '
                        "VALUES(:phone,:password_hash,'super_admin','active')"
                    ),
                    {"phone": request.phone, "password_hash": request.password_hash},
                )
                await transaction.rollback()
            finally:
                await connection.close()
            assert await module._confirm(owner_engine, request) is module.CommitOutcome.NOT_COMMITTED

            original_fetch_scope = module._fetch_scope
            overlap_barrier = asyncio.Event()
            overlap_count = 0

            async def overlapped_scope(connection):
                nonlocal overlap_count
                observed_scope = await original_fetch_scope(connection)
                overlap_count += 1
                if overlap_count == 2:
                    overlap_barrier.set()
                await asyncio.wait_for(overlap_barrier.wait(), timeout=10)
                return observed_scope

            monkeypatch.setattr(module, "_fetch_scope", overlapped_scope)
            try:
                results = await asyncio.gather(
                    module._insert_once(owner_engine, request),
                    module._insert_once(owner_engine, request),
                    return_exceptions=True,
                )
            finally:
                monkeypatch.setattr(module, "_fetch_scope", original_fetch_scope)
            assert overlap_count == 2
            _set_g2_controlled_concurrency_results(results, module.BootstrapError)
            summary = _g2_controlled_diagnostic_concurrency()
            assert summary["code"] == "KG_G2_CONCURRENCY_EXPECTED"
            assert summary["success_count"] == 1
            assert summary["bootstrap_count"] == 1
            assert summary["other_count"] == 0
            assert sum(result is None for result in results) == 1
            failures = [result for result in results if isinstance(result, module.BootstrapError)]
            assert len(failures) == 1
            assert failures[0].code == "KG_G2_BOOTSTRAP_DATABASE_NOT_FRESH"

            async with owner_engine.connect() as pooled_connection:
                isolation = (
                    await pooled_connection.execute(text("SHOW transaction_isolation"))
                ).scalar_one()
                assert isolation == "serializable"
                module.validate_committed_manifest(
                    await module._manifest(pooled_connection)
                )

            observed_confirmation_isolation = []

            async def confirming_scope(connection):
                observed_confirmation_isolation.append(
                    (
                        await connection.execute(text("SHOW transaction_isolation"))
                    ).scalar_one()
                )
                return await original_fetch_scope(connection)

            monkeypatch.setattr(module, "_fetch_scope", confirming_scope)
            try:
                assert (
                    await module._confirm(owner_engine, request)
                    is module.CommitOutcome.COMMITTED
                )
            finally:
                monkeypatch.setattr(module, "_fetch_scope", original_fetch_scope)
            assert observed_confirmation_isolation == ["serializable"]
            owner = await asyncpg.connect(_to_asyncpg_dsn(migration_url))
            try:
                user_id = await owner.fetchval(
                    'SELECT id FROM public."user" WHERE phone=$1', request.phone
                )
                assert await owner.fetchval('SELECT count(*) FROM public."user"') == 1
            finally:
                await owner.close()
            return request, int(user_id)
        finally:
            await owner_engine.dispose()

    async def verify_role_isolation() -> None:
        application = await asyncpg.connect(_to_asyncpg_dsn(application_url))
        try:
            assert await application.fetchval("SELECT current_user") == application_role
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert not await application.fetchval(
                    "SELECT has_table_privilege(current_user,'public.\"user\"',$1)",
                    privilege,
                )
            assert not await application.fetchval(
                "SELECT has_schema_privilege(current_user,'public','CREATE')"
            )
        finally:
            await application.close()

    async def verify_verification_writer_binding() -> None:
        connection = await asyncpg.connect(
            _to_asyncpg_dsn(rebound_urls["KG_VERIFICATION_WRITER_DATABASE_URL"])
        )
        try:
            actual = await connection.fetchrow(
                "SELECT current_database() AS database_name, current_user AS role_name"
            )
            assert actual["database_name"] == task_database
            assert actual["role_name"] == os.environ["KG_TEST_VERIFICATION_WRITER_ROLE"]
        finally:
            await connection.close()

    async def user_count() -> int:
        owner = await asyncpg.connect(_to_asyncpg_dsn(migration_url))
        try:
            return int(await owner.fetchval('SELECT count(*) FROM public."user"'))
        finally:
            await owner.close()

    async def verify_migration_state(expected_head: str, *, objects_present: bool) -> None:
        owner = await asyncpg.connect(_to_asyncpg_dsn(migration_url))
        try:
            actual_head = await owner.fetchval("SELECT version_num FROM alembic_version")
            write_function = await owner.fetchval(
                "SELECT to_regprocedure("
                "'public.slice3_platform_reviewer_write_currentness_v1(bigint)')"
            )
            credential_function = await owner.fetchval(
                "SELECT to_regprocedure("
                "'public.auth_user_credential_material_v1(bigint)')"
            )
            assert actual_head == expected_head
            assert (write_function is not None) is objects_present
            assert (credential_function is not None) is objects_present
        finally:
            await owner.close()

    async def verify_http(request, user_id: int) -> None:
        engine = create_async_engine(application_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        original_get_slice3_factory = member_enrollment_api.get_slice3_session_factory
        original_safe_view_rows = MemberEnrollmentRepository.safe_view_rows
        original_review = member_enrollment_api._review
        original_serialize_response = fastapi_routing.serialize_response

        class ObservedSessionContext:
            def __init__(self, context):
                self._context = context

            async def __aenter__(self):
                return await _observe_g2_http_exception(
                    "SESSION_CONTEXT", self._context.__aenter__()
                )

            async def __aexit__(self, *args):
                return await _observe_g2_http_exception(
                    "SESSION_CONTEXT", self._context.__aexit__(*args)
                )

        async def observed_get_slice3_factory(kind):
            observed_factory = await _observe_g2_http_exception(
                "DEPENDENCY_FACTORY", original_get_slice3_factory(kind)
            )

            def session_factory(*args, **kwargs):
                return ObservedSessionContext(observed_factory(*args, **kwargs))

            return session_factory

        async def observed_safe_view_rows(repository, *args, **kwargs):
            return await _observe_g2_http_exception(
                "VIEW_QUERY", original_safe_view_rows(repository, *args, **kwargs)
            )

        def observed_review(*args, **kwargs):
            return _observe_g2_http_exception_sync(
                "DTO_MAPPING", original_review, *args, **kwargs
            )

        async def observed_serialize_response(*args, **kwargs):
            return await _observe_g2_http_exception(
                "RESPONSE_SERIALIZATION",
                original_serialize_response(*args, **kwargs),
            )

        monkeypatch.setattr(
            member_enrollment_api,
            "get_slice3_session_factory",
            observed_get_slice3_factory,
        )
        monkeypatch.setattr(
            MemberEnrollmentRepository, "safe_view_rows", observed_safe_view_rows
        )
        monkeypatch.setattr(member_enrollment_api, "_review", observed_review)
        monkeypatch.setattr(
            fastapi_routing, "serialize_response", observed_serialize_response
        )
        app = create_app()

        async def override_session():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_session
        monkeypatch.setattr(认证当前性, "get_session_factory", lambda: factory)
        try:
            with TestClient(
                app, raise_server_exceptions=False, client=("127.0.0.1", 50000)
            ) as client:
                login = client.post(
                    "/api/v1/auth/login",
                    json={"phone": request.phone, "password": request.password},
                )
                _record_g2_controlled_http_status("AUTH_LOGIN", login.status_code)
                assert login.status_code == 200
                token = login.json()["data"]["access_token"]
                headers = {"Authorization": f"Bearer {token}"}
                me = client.get("/api/v1/auth/me", headers=headers)
                _record_g2_controlled_http_status("AUTH_ME", me.status_code)
                assert me.status_code == 200
                me_data = me.json()["data"]
                assert (me_data["id"], me_data["role"]) == (user_id, "super_admin")
                assert me_data["tenant_id"] is None and me_data["org_id"] is None
                reviewer = client.get(
                    "/api/v1/platform/member-identity-reviews?limit=1", headers=headers
                )
                _record_g2_controlled_http_status(
                    "PLATFORM_IDENTITY_REVIEWS", reviewer.status_code
                )
                assert reviewer.status_code == 200
                owner = await asyncpg.connect(_to_asyncpg_dsn(migration_url))
                try:
                    await owner.execute(
                        'UPDATE public."user" SET status=\'disabled\' WHERE id=$1', user_id
                    )
                finally:
                    await owner.close()
                stale = client.get("/api/v1/auth/me", headers=headers)
                _record_g2_controlled_http_status(
                    "AUTH_ME_AFTER_DISABLE", stale.status_code
                )
                assert stale.status_code == 401
                if request.phone in stale.text or request.password in stale.text:
                    pytest.fail("KG_G2_BOOTSTRAP_HTTP_OUTPUT_UNSAFE", pytrace=False)
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    primary: BaseException | None = None
    try:
        _prepare_private_credential_files(
            credential_directory, (prepared_temp_path, receipt_temp_path)
        )
        _run(prepare_database(), entry_stage="PREPARE_ASYNCIO_RUN")
        _run_isolated_migration("upgrade-head", task_database)
        _run(verify_migration_state("20260916_0049", objects_present=True))
        _run(verify_verification_writer_binding())
        request, user_id = _run(verify_database_contract())

        async def verified_without_network(candidate) -> None:
            assert candidate.request_digest == request.request_digest

        monkeypatch.setattr(module, "_verify_http", verified_without_network)
        first = _run(module.run_bootstrap())
        second = _run(module.run_bootstrap())
        assert first.status == second.status == "COMMITTED"
        assert first.replay is second.replay is True
        assert first.request_digest == second.request_digest == request.request_digest
        assert _run(user_count()) == 1
        _run(verify_role_isolation())
        _run(verify_http(request, user_id))
        _run(database.dispose_database_runtimes())
        _run_isolated_migration("downgrade-0047", task_database)
        _run(verify_migration_state("20260914_0047", objects_present=False))
        _run_isolated_migration("reupgrade-head", task_database)
        _run(verify_migration_state("20260916_0049", objects_present=True))
    except BaseException as error:
        primary = error
    finally:
        get_settings.cache_clear()
        cleanup_failures: list[str] = []
        if created:
            try:
                _run(drop_database())
            except BaseException:
                cleanup_failures.append("KG_G2_BOOTSTRAP_TEST_DATABASE_CLEANUP_FAILED")
        for path in (prepared_path, prepared_temp_path, receipt_path, receipt_temp_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                cleanup_failures.append("KG_G2_BOOTSTRAP_TEST_CREDENTIAL_CLEANUP_FAILED")
        if credential_directory.exists():
            try:
                credential_directory.rmdir()
            except OSError:
                cleanup_failures.append("KG_G2_BOOTSTRAP_TEST_CREDENTIAL_CLEANUP_FAILED")
        if primary is not None:
            for code in sorted(set(cleanup_failures)):
                primary.add_note(code)
        elif cleanup_failures:
            pytest.fail(sorted(set(cleanup_failures))[0], pytrace=False)
    if primary is not None:
        raise primary.with_traceback(primary.__traceback__)
