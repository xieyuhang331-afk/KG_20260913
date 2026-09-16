from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import secrets
import sys
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx2
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.modules.auth.service import hash_password

BOOTSTRAP_SCHEMA_VERSION = 1
EXPECTED_MIGRATION_HEAD = "20260916_0049"
PERSISTENT_DATABASE_OBJECTS_CREATED: tuple[str, ...] = ()
_RUN_PATTERN = re.compile(r"^[0-9a-f]{16}$")
_SENTINEL_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PHONE_PATTERN = re.compile(r"^1[0-9]{10}$")

FRESH_TABLES = (
    'public."user"',
    "public.tenant",
    "public.platform_org",
    "identity.member",
    "identity.registration_bootstrap_record",
    "public.institution_invitation",
    "public.institution_application",
    "public.institution_application_revision",
    "public.institution_license",
    "public.institution_onboarding_account",
    "public.therapist_invitation",
    "public.therapist_profile",
    "public.therapist_qualification_version",
    "public.identity_verification_submission",
    "public.identity_verification_decision",
    "identity.identity_subject_claim_registry",
    "identity.user_member_self_link",
    "public.member_service_invitation",
    "public.service_enrollment",
    "public.consent_record",
    "public.primary_therapist_assignment",
    "public.service_case",
    "public.private_file",
    "public.health_profile",
    "public.detection_report",
    "public.canonical_health_fact",
    "public.health_assessment",
    "public.assessment_input_assembly",
    "public.health_plan_generation_request",
    "public.health_plan_version",
    "public.service_cycle_schedule",
    "public.service_milestone",
    "public.service_transfer_request",
    "public.personal_data_export_request",
    "public.personal_data_export_artifact",
)


class BootstrapError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class CommitOutcome(Enum):
    COMMITTED = "COMMITTED"
    NOT_COMMITTED = "NOT_COMMITTED"
    UNKNOWN = "UNKNOWN"

    @property
    def retryable(self) -> bool:
        return self is CommitOutcome.NOT_COMMITTED


@dataclass(frozen=True)
class RuntimeScope:
    environment: str
    database_host: str = field(repr=False)
    database_name: str = field(repr=False)
    run_id: str = field(repr=False)
    sentinel: str = field(repr=False)
    migration_head: str

    @classmethod
    def synthetic_valid(cls) -> RuntimeScope:
        run_id = "a" * 16
        return cls(
            environment="local_ephemeral",
            database_host="127.0.0.1",
            database_name=f"kg_it_{run_id}",
            run_id=run_id,
            sentinel="b" * 32,
            migration_head=EXPECTED_MIGRATION_HEAD,
        )

    def with_value(self, name: str, value: str) -> RuntimeScope:
        return replace(self, **{name: value})


@dataclass(frozen=True)
class BootstrapRequest:
    schema_version: int
    run_id: str
    phone: str = field(repr=False)
    password: str = field(repr=False)
    password_hash: str = field(repr=False)
    request_digest: str
    status: str = "PREPARED"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> BootstrapRequest:
        expected = {
            "schema_version",
            "run_id",
            "phone",
            "password",
            "password_hash",
            "request_digest",
            "status",
        }
        if set(value) != expected:
            raise BootstrapError("KG_G2_BOOTSTRAP_COMMIT_OUTCOME_UNKNOWN")
        request = cls(**value)
        if (
            request.schema_version != BOOTSTRAP_SCHEMA_VERSION
            or not _RUN_PATTERN.fullmatch(request.run_id)
            or not _PHONE_PATTERN.fullmatch(request.phone)
            or not 16 <= len(request.password) <= 128
            or not request.password_hash
            or not _DIGEST_PATTERN.fullmatch(request.request_digest)
            or request.request_digest != _request_digest(request)
            or request.status != "PREPARED"
        ):
            raise BootstrapError("KG_G2_BOOTSTRAP_COMMIT_OUTCOME_UNKNOWN")
        return request

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "phone": self.phone,
            "password": self.password,
            "password_hash": self.password_hash,
            "request_digest": self.request_digest,
            "status": self.status,
        }


@dataclass(frozen=True)
class BootstrapResult:
    status: str
    replay: bool
    user_count: int
    credential_present: bool
    login_verified: bool
    currentness_verified: bool
    request_digest: str
    receipt_digest: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": BOOTSTRAP_SCHEMA_VERSION,
            "migration_head": EXPECTED_MIGRATION_HEAD,
            "status": self.status,
            "replay": self.replay,
            "user_count": self.user_count,
            "credential_present": self.credential_present,
            "login_verified": self.login_verified,
            "currentness_verified": self.currentness_verified,
            "request_digest": self.request_digest,
            "receipt_digest": self.receipt_digest,
        }


def safe_error_payload(error: BootstrapError) -> dict[str, str]:
    return {"status": "FAILED", "code": error.code}


def validate_runtime_scope(scope: RuntimeScope) -> None:
    if (
        scope.environment != "local_ephemeral"
        or scope.database_host not in {"127.0.0.1", "::1", "localhost"}
        or not _RUN_PATTERN.fullmatch(scope.run_id)
        or scope.database_name != f"kg_it_{scope.run_id}"
        or len(scope.database_name.encode("ascii")) > 63
        or not _SENTINEL_PATTERN.fullmatch(scope.sentinel)
        or scope.migration_head != EXPECTED_MIGRATION_HEAD
    ):
        raise BootstrapError("KG_G2_BOOTSTRAP_SCOPE_INVALID")


def validate_fresh_manifest(manifest: dict[str, int]) -> None:
    if set(manifest) != set(FRESH_TABLES) or any(
        not isinstance(value, int) or value != 0 for value in manifest.values()
    ):
        raise BootstrapError("KG_G2_BOOTSTRAP_DATABASE_NOT_FRESH")


def validate_committed_manifest(manifest: dict[str, int]) -> None:
    if set(manifest) != set(FRESH_TABLES):
        raise BootstrapError("KG_G2_BOOTSTRAP_POSTIMAGE_INVALID")
    expected = {name: 0 for name in FRESH_TABLES}
    expected['public."user"'] = 1
    if manifest != expected:
        raise BootstrapError("KG_G2_BOOTSTRAP_POSTIMAGE_INVALID")


def require_safe_commit_outcome(outcome: CommitOutcome) -> None:
    if outcome is CommitOutcome.UNKNOWN:
        raise BootstrapError("KG_G2_BOOTSTRAP_COMMIT_OUTCOME_UNKNOWN")


async def confirm_after_writer_exit(
    *, writer_closed: bool, probe: Callable[[], Any]
) -> CommitOutcome:
    if not writer_closed:
        raise BootstrapError("KG_G2_BOOTSTRAP_COMMIT_OUTCOME_UNKNOWN")
    value = probe()
    if inspect.isawaitable(value):
        value = await value
    if not isinstance(value, CommitOutcome):
        raise BootstrapError("KG_G2_BOOTSTRAP_COMMIT_OUTCOME_UNKNOWN")
    return value


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _request_digest(request: BootstrapRequest) -> str:
    return hashlib.sha256(
        _canonical_json(
            {
                "schema_version": request.schema_version,
                "run_id": request.run_id,
                "phone": request.phone,
                "password_hash": request.password_hash,
                "status": request.status,
            }
        )
    ).hexdigest()


def _new_request(run_id: str) -> BootstrapRequest:
    phone = "1" + "".join(secrets.choice("0123456789") for _ in range(10))
    password = secrets.token_urlsafe(24)
    candidate = BootstrapRequest(
        schema_version=BOOTSTRAP_SCHEMA_VERSION,
        run_id=run_id,
        phone=phone,
        password=password,
        password_hash=hash_password(password),
        request_digest="0" * 64,
    )
    return replace(candidate, request_digest=_request_digest(candidate))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BootstrapError("KG_G2_BOOTSTRAP_COMMIT_OUTCOME_UNKNOWN") from error
    if not isinstance(value, dict):
        raise BootstrapError("KG_G2_BOOTSTRAP_COMMIT_OUTCOME_UNKNOWN")
    return value


def _load_or_prepare_request(scope: RuntimeScope) -> tuple[BootstrapRequest, bool]:
    target = Path(os.environ["KG_G2_BOOTSTRAP_PREPARED_PATH"])
    temporary = Path(os.environ["KG_G2_BOOTSTRAP_PREPARED_TEMP_PATH"])
    if target.is_file():
        return BootstrapRequest.from_dict(_read_json(target)), True
    if not temporary.is_file() or temporary.stat().st_size != 0:
        raise BootstrapError("KG_G2_BOOTSTRAP_COMMIT_OUTCOME_UNKNOWN")
    request = _new_request(scope.run_id)
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(request.to_dict(), handle, sort_keys=True, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)
    return request, False


async def _fetch_scope(connection: AsyncConnection) -> RuntimeScope:
    row = (
        await connection.execute(
            text(
                "SELECT current_database() AS database_name, inet_server_addr()::text AS host, "
                "shobj_description(d.oid, 'pg_database') AS comment, "
                "pg_get_userbyid(d.datdba) AS owner, current_user AS actor "
                "FROM pg_database d WHERE d.datname=current_database()"
            )
        )
    ).mappings().one()
    expected_run = os.environ.get("KG_G2_BOOTSTRAP_RUN_ID", "")
    expected_sentinel = os.environ.get("KG_G2_BOOTSTRAP_SENTINEL", "")
    expected_database = os.environ.get("KG_G2_BOOTSTRAP_DATABASE_NAME", "")
    head_rows = (
        await connection.execute(text("SELECT version_num FROM alembic_version"))
    ).scalars().all()
    if (
        row["database_name"] != expected_database
        or row["actor"] != row["owner"]
        or row["comment"] != f"kg-test-disposable:{expected_run}"
        or len(head_rows) != 1
    ):
        raise BootstrapError("KG_G2_BOOTSTRAP_SCOPE_INVALID")
    scope = RuntimeScope(
        environment=os.environ.get("KG_G2_BOOTSTRAP_ENVIRONMENT", ""),
        database_host=os.environ.get("KG_G2_BOOTSTRAP_DATABASE_HOST", ""),
        database_name=row["database_name"],
        run_id=expected_run,
        sentinel=expected_sentinel,
        migration_head=head_rows[0],
    )
    validate_runtime_scope(scope)
    return scope


async def _manifest(connection: AsyncConnection) -> dict[str, int]:
    result: dict[str, int] = {}
    for table_name in FRESH_TABLES:
        result[table_name] = int(
            (await connection.execute(text(f"SELECT count(*) FROM {table_name}"))).scalar_one()
        )
    return result


async def _matching_rows(connection: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        await connection.execute(
            text(
                'SELECT id, phone, password_hash, real_name, id_card, role::text AS role, '
                'user_status, tenant_id, verify_status, status::text AS status, exited_at, '
                'deletion_requested_at FROM public."user" ORDER BY id'
            )
        )
    ).mappings().all()
    return [dict(row) for row in rows]


def _row_matches(row: dict[str, Any], request: BootstrapRequest) -> bool:
    return (
        row["phone"] == request.phone
        and row["password_hash"] == request.password_hash
        and row["role"] == "super_admin"
        and row["status"] == "active"
        and row["tenant_id"] is None
        and row["verify_status"] is None
        and row["real_name"] is None
        and row["id_card"] is None
        and row["exited_at"] is None
        and row["deletion_requested_at"] is None
        and row["user_status"] == "customer"
    )


async def _insert_once(engine: AsyncEngine, request: BootstrapRequest) -> None:
    connection: AsyncConnection | None = None
    transaction = None
    primary_error: BaseException | None = None
    try:
        connection = await engine.connect()
        connection = await connection.execution_options(isolation_level="READ COMMITTED")
        transaction = await connection.begin()
        scope = await _fetch_scope(connection)
        if request.run_id != scope.run_id:
            raise BootstrapError("KG_G2_BOOTSTRAP_SCOPE_INVALID")
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"G2_B_BOOTSTRAP_V1:{request.run_id}"},
        )
        validate_fresh_manifest(await _manifest(connection))
        await connection.execute(
            text(
                'INSERT INTO public."user" '
                "(phone,password_hash,role,status,tenant_id,verify_status,real_name,id_card,"
                "exited_at,deletion_requested_at) VALUES "
                "(:phone,:password_hash,'super_admin','active',NULL,NULL,NULL,NULL,NULL,NULL) "
                "RETURNING id"
            ),
            {"phone": request.phone, "password_hash": request.password_hash},
        )
        rows = await _matching_rows(connection)
        if len(rows) != 1 or not _row_matches(rows[0], request):
            raise BootstrapError("KG_G2_BOOTSTRAP_POSTIMAGE_INVALID")
        validate_committed_manifest(await _manifest(connection))
        await transaction.commit()
    except BaseException as error:
        primary_error = error
        if transaction is not None and transaction.is_active:
            with suppress(BaseException):
                await transaction.rollback()
        raise
    finally:
        if connection is not None:
            try:
                await connection.close()
            except BaseException:
                if primary_error is None:
                    raise


async def _confirm(engine: AsyncEngine, request: BootstrapRequest) -> CommitOutcome:
    async with engine.connect() as connection:
        await _fetch_scope(connection)
        rows = await _matching_rows(connection)
        if len(rows) == 1 and _row_matches(rows[0], request):
            validate_committed_manifest(await _manifest(connection))
            return CommitOutcome.COMMITTED
        if len(rows) == 0:
            validate_fresh_manifest(await _manifest(connection))
            return CommitOutcome.NOT_COMMITTED
        return CommitOutcome.UNKNOWN


async def _require_initial_fresh(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        await _fetch_scope(connection)
        manifest = await _manifest(connection)
        if manifest.get('public."user"') != 0:
            raise BootstrapError("KG_G2_BOOTSTRAP_EXISTING_USER_UNKNOWN")
        validate_fresh_manifest(manifest)


async def _verify_http(request: BootstrapRequest) -> None:
    base_url = os.environ.get("KG_G2_BOOTSTRAP_API_BASE_URL", "")
    parsed = urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise BootstrapError("KG_G2_BOOTSTRAP_SCOPE_INVALID")
    try:
        async with httpx2.AsyncClient(base_url=base_url, timeout=15, trust_env=False) as client:
            login = await client.post(
                "/api/v1/auth/login",
                json={"phone": request.phone, "password": request.password},
            )
            login.raise_for_status()
            login_data = login.json()["data"]
            token = login_data["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            me = await client.get("/api/v1/auth/me", headers=headers)
            me.raise_for_status()
            me_data = me.json()["data"]
            if (
                me_data["id"] != login_data["user"]["id"]
                or me_data["role"] != "super_admin"
                or me_data["tenant_id"] is not None
                or me_data["org_id"] is not None
            ):
                raise BootstrapError("KG_G2_BOOTSTRAP_LOGIN_NOT_CURRENT")
            reviewer = await client.get(
                "/api/v1/platform/member-identity-reviews?limit=1", headers=headers
            )
            reviewer.raise_for_status()
    except asyncio.CancelledError:
        raise
    except BootstrapError:
        raise
    except Exception:
        raise BootstrapError("KG_G2_BOOTSTRAP_LOGIN_NOT_CURRENT") from None


def _write_receipt(request: BootstrapRequest) -> str:
    target = Path(os.environ["KG_G2_BOOTSTRAP_RECEIPT_PATH"])
    temporary = Path(os.environ["KG_G2_BOOTSTRAP_RECEIPT_TEMP_PATH"])
    payload = {
        "schema_version": BOOTSTRAP_SCHEMA_VERSION,
        "status": "COMMITTED",
        "request_digest": request.request_digest,
    }
    encoded = _canonical_json(payload)
    if target.is_file():
        if _read_json(target) != payload:
            raise BootstrapError("KG_G2_BOOTSTRAP_COMMIT_OUTCOME_UNKNOWN")
        return hashlib.sha256(target.read_bytes()).hexdigest()
    if not temporary.is_file() or temporary.stat().st_size != 0:
        raise BootstrapError("KG_G2_BOOTSTRAP_COMMIT_OUTCOME_UNKNOWN")
    with temporary.open("wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)
    return hashlib.sha256(encoded).hexdigest()


async def run_bootstrap() -> BootstrapResult:
    owner_url = os.environ.get("KG_G2_BOOTSTRAP_OWNER_DATABASE_URL", "")
    parsed = urlparse(owner_url)
    scope = RuntimeScope(
        environment=os.environ.get("KG_G2_BOOTSTRAP_ENVIRONMENT", ""),
        database_host=parsed.hostname or "",
        database_name=(parsed.path or "").lstrip("/"),
        run_id=os.environ.get("KG_G2_BOOTSTRAP_RUN_ID", ""),
        sentinel=os.environ.get("KG_G2_BOOTSTRAP_SENTINEL", ""),
        migration_head=os.environ.get("KG_G2_BOOTSTRAP_MIGRATION_HEAD", ""),
    )
    validate_runtime_scope(scope)
    engine = create_async_engine(owner_url, isolation_level="SERIALIZABLE", pool_pre_ping=True)
    primary_error: BaseException | None = None
    try:
        prepared_path = Path(os.environ["KG_G2_BOOTSTRAP_PREPARED_PATH"])
        if not await asyncio.to_thread(prepared_path.is_file):
            await _require_initial_fresh(engine)
        request, replay = _load_or_prepare_request(scope)
        if request.run_id != scope.run_id:
            raise BootstrapError("KG_G2_BOOTSTRAP_COMMIT_OUTCOME_UNKNOWN")
        outcome = await _confirm(engine, request)
        if outcome is CommitOutcome.NOT_COMMITTED:
            try:
                await _insert_once(engine, request)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            outcome = await _confirm(engine, request)
            if outcome is CommitOutcome.NOT_COMMITTED:
                try:
                    await _insert_once(engine, request)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
                outcome = await _confirm(engine, request)
        require_safe_commit_outcome(outcome)
        if outcome is not CommitOutcome.COMMITTED:
            raise BootstrapError("KG_G2_BOOTSTRAP_COMMIT_OUTCOME_UNKNOWN")
        await _verify_http(request)
        receipt_digest = _write_receipt(request)
        return BootstrapResult(
            status="COMMITTED",
            replay=replay,
            user_count=1,
            credential_present=True,
            login_verified=True,
            currentness_verified=True,
            request_digest=request.request_digest,
            receipt_digest=receipt_digest,
        )
    except BaseException as error:
        primary_error = error
        raise
    finally:
        try:
            await engine.dispose()
        except BaseException:
            if primary_error is None:
                raise


def main() -> int:
    if len(sys.argv) != 1:
        print(json.dumps({"status": "FAILED", "code": "KG_G2_BOOTSTRAP_ARGUMENT_INVALID"}))
        return 2
    try:
        result = asyncio.run(run_bootstrap())
    except KeyboardInterrupt:
        raise
    except asyncio.CancelledError:
        raise
    except BootstrapError as error:
        print(json.dumps(safe_error_payload(error), sort_keys=True, separators=(",", ":")))
        return 1
    except Exception:
        print(
            json.dumps(
                {"status": "FAILED", "code": "KG_G2_BOOTSTRAP_COMMIT_OUTCOME_UNKNOWN"},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1
    print(json.dumps(result.to_public_dict(), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
