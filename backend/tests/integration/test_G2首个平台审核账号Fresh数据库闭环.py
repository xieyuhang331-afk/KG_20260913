from __future__ import annotations

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
    "source_location",
    "status",
    "timed_out",
}
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
        "source_location": "UNKNOWN",
        "status": "UNKNOWN",
        "timed_out": "UNKNOWN",
    }


_G2_CONTROLLED_MIGRATION_OPERATION = _unknown_migration_diagnostic("UNKNOWN")


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
    *, controller: Path, action: str, task_database: str, g1_run_id: str
) -> list[str]:
    if action not in {"upgrade-head", "downgrade-0047", "reupgrade-head"}:
        raise ValueError("KG_G2_MIGRATION_ARGUMENT_INVALID")
    if not _SAFE_DATABASE.fullmatch(task_database) or not re.fullmatch(
        r"[a-f0-9]{16}", g1_run_id
    ):
        raise ValueError("KG_G2_MIGRATION_ARGUMENT_INVALID")
    return [
        "pwsh.exe",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(controller),
        "-MigrationAction",
        action,
        "-TaskDatabase",
        task_database,
        "-G1RunId",
        g1_run_id,
    ]


def _run_isolated_migration(action: str, task_database: str) -> None:
    global _G2_CONTROLLED_MIGRATION_OPERATION

    previous = _g2_controlled_diagnostic_stage()
    _G2_CONTROLLED_MIGRATION_OPERATION = _unknown_migration_diagnostic(action)
    try:
        stage = _G2_MIGRATION_STAGES[action]
        controller = Path(os.environ["KG_G2_MIGRATION_CONTROLLER_PATH"])
        g1_run_id = os.environ["KG_G2_MIGRATION_G1_RUN_ID"]
        if not controller.is_file():
            raise ValueError
        arguments = _migration_process_arguments(
            controller=controller,
            action=action,
            task_database=task_database,
            g1_run_id=g1_run_id,
        )
    except (KeyError, TypeError, ValueError):
        pytest.fail("KG_G2_MIGRATION_CONFIGURATION_INVALID", pytrace=False)
    _set_g2_controlled_diagnostic_stage(stage)
    child_environment = {
        name: value for name, value in os.environ.items() if not name.startswith("KG_")
    }
    child_environment["PYTHONDONTWRITEBYTECODE"] = "1"
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
            "source_location",
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
    assert module.EXPECTED_MIGRATION_HEAD == "20260915_0048"
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
    assert "KG_G2_MIGRATION_CONTROLLER_PATH" in helper
    assert "KG_G2_MIGRATION_G1_RUN_ID" in helper
    assert "KG_DATABASE_URL" not in helper
    assert "_DATABASE_URL" not in helper


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
        "source_location": "ALEMBIC_CHILD_PROCESS",
        "status": "FAILED",
        "timed_out": False,
    }
    parsed = _parse_migration_controller_result(
        json.dumps(valid).encode(), action="upgrade-head", returncode=1
    )
    assert parsed == valid
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
    assert 'row["role"] != "super_admin"' in api_source
    assert 'row["status"] != "active"' in api_source
    assert 'row["tenant_id"] is not None' in api_source
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


def test_G2_B_R_M07迁移控制参数不携带URL凭据或自由动作():
    assert "_migration_process_arguments" in globals()
    helper = globals()["_migration_process_arguments"]
    arguments = helper(
        controller=Path("synthetic-controller.ps1"),
        action="upgrade-head",
        task_database="kg_it_0123456789abcdef",
        g1_run_id="fedcba9876543210",
    )
    rendered = " ".join(arguments)
    assert "upgrade-head" in arguments
    assert "kg_it_0123456789abcdef" in arguments
    assert "fedcba9876543210" in arguments
    assert "postgresql" not in rendered
    assert "password" not in rendered.lower()
    with pytest.raises(ValueError):
        helper(
            controller=Path("synthetic-controller.ps1"),
            action="arbitrary-action",
            task_database="kg_it_0123456789abcdef",
            g1_run_id="fedcba9876543210",
        )


def test_G2_B_R_M08短时迁移子进程删除全部父进程KG环境且错误闭合(
    monkeypatch, tmp_path
):
    controller = tmp_path / "controller.ps1"
    controller.write_text("# synthetic", encoding="utf-8")
    monkeypatch.setenv("KG_G2_MIGRATION_CONTROLLER_PATH", str(controller))
    monkeypatch.setenv("KG_G2_MIGRATION_G1_RUN_ID", "fedcba9876543210")
    monkeypatch.setenv("KG_SYNTHETIC_MIGRATION_ONLY_SECRET", "must-not-propagate")
    observed = {}

    def result_bytes(
        *,
        code="KG_G2_MIGRATION_COMPLETED",
        exception_type="None",
        exit_code=0,
        operation_stage="COMPLETED",
        source_location="MIGRATION_CONTROLLER",
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
                "source_location": source_location,
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
    assert all(not name.startswith("KG_") for name in observed["env"])
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
        "source_location": "MIGRATION_VERSION_SOURCE",
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
        "KG_G2_BOOTSTRAP_MIGRATION_HEAD": "20260915_0048",
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
                migration_head="20260915_0048",
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
        _run(verify_migration_state("20260915_0048", objects_present=True))
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
        _run(verify_migration_state("20260915_0048", objects_present=True))
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
