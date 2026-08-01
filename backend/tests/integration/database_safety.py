from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlparse


POSTGRESQL_SCHEMES = {
    "postgresql",
    "postgresql+asyncpg",
    "postgresql+psycopg",
    "postgresql+psycopg2",
}
ALLOWED_TEST_ENVIRONMENTS = {"ci_ephemeral", "local_ephemeral"}
SYSTEM_DATABASE_NAMES = {"postgres", "template0", "template1"}
BLOCKED_DATABASE_NAME_PARTS = {"prod", "production", "stg", "staging", "uat"}
BLOCKED_LOCAL_PORTS = {15432}
RUN_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{5,63}")
SENTINEL_PREFIX = "kg-test-disposable:"


@dataclass(frozen=True)
class DisposableDatabaseTarget:
    database_name: str
    run_id: str
    expected_sentinel: str


def validate_test_database_target(
    database_url: str,
    *,
    integration_enabled: str | None,
    destructive_enabled: str | None,
    environment: str | None,
    run_id: str | None,
) -> DisposableDatabaseTarget:
    if integration_enabled != "1":
        raise RuntimeError("KG_RUN_PG_INTEGRATION must equal 1")
    if destructive_enabled != "1":
        raise RuntimeError("KG_ALLOW_DESTRUCTIVE_TEST_DATABASE must equal 1")
    if environment not in ALLOWED_TEST_ENVIRONMENTS:
        raise RuntimeError("KG_TEST_ENVIRONMENT must identify an ephemeral test environment")
    if run_id is None or RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise RuntimeError("KG_TEST_RUN_ID must be 6-64 lowercase letters, digits, underscores, or hyphens")

    parsed = urlparse(database_url)
    if parsed.scheme not in POSTGRESQL_SCHEMES:
        raise RuntimeError("KG_TEST_DATABASE_URL must use a PostgreSQL driver")
    if parsed.hostname is None or parsed.port is None:
        raise RuntimeError("KG_TEST_DATABASE_URL must include an explicit host and port")

    database_name = unquote(parsed.path.lstrip("/"))
    if not database_name:
        raise RuntimeError("KG_TEST_DATABASE_URL must include a database name")
    if database_name in SYSTEM_DATABASE_NAMES:
        raise RuntimeError("system databases are never disposable test targets")
    lowered_name = database_name.lower()
    if any(part in lowered_name for part in BLOCKED_DATABASE_NAME_PARTS):
        raise RuntimeError("UAT, staging, and production databases are never disposable test targets")
    if parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"} and parsed.port in BLOCKED_LOCAL_PORTS:
        raise RuntimeError("the local UAT database endpoint is never a disposable test target")

    expected_names = {f"kg_it_{run_id}", f"kg_mt_{run_id}"}
    if database_name not in expected_names:
        raise RuntimeError("database name must match the current ephemeral test run")

    return DisposableDatabaseTarget(
        database_name=database_name,
        run_id=run_id,
        expected_sentinel=f"{SENTINEL_PREFIX}{run_id}",
    )


def validate_database_sentinel(actual_sentinel: str | None, target: DisposableDatabaseTarget) -> None:
    if actual_sentinel != target.expected_sentinel:
        raise RuntimeError("database disposable sentinel does not match the current test run")
