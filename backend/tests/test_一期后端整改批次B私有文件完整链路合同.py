from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.database import (
    get_db_session,
    get_institution_onboarding_reader_session,
    get_private_file_access_writer_session,
    get_private_file_writer_session,
    get_slice4_clinical_reader_session,
    get_slice4_institution_reader_session,
    get_slice7_transfer_writer_session,
)
from app.core.security import CurrentUser, get_current_user_from_jwt
from app.modules.private_file import api as private_file_api
from app.modules.private_file import service

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
PRIVATE = BACKEND / "app" / "modules" / "private_file"
API = PRIVATE / "api.py"
SERVICE = PRIVATE / "service.py"
STORAGE = PRIVATE / "storage.py"
PORTS = PRIVATE / "ports.py"
MODELS = PRIVATE / "models.py"
REPOSITORY = PRIVATE / "repository.py"
SCHEMAS = PRIVATE / "schemas.py"
CONFIG = BACKEND / "app" / "core" / "config.py"
DATABASE = BACKEND / "app" / "core" / "database.py"
MAIN = BACKEND / "app" / "main.py"
TASKS = BACKEND / "app" / "tasks" / "institution_onboarding_tasks.py"
CELERY = BACKEND / "app" / "tasks" / "celery_app.py"
MIGRATION = (
    BACKEND
    / "app"
    / "migrations"
    / "versions"
    / "20260904_0038_batch_b_private_file_end_to_end.py"
)
FRONTEND_API = ROOT / "frontend" / "src" / "domains" / "platform" / "api.ts"
WORKFLOW = ROOT / ".github" / "workflows" / "p2-foundation-ci.yml"
INTEGRATION_CONFTEST = BACKEND / "tests" / "integration" / "conftest.py"
FRONTEND_PAGE = (
    ROOT
    / "frontend"
    / "src"
    / "domains"
    / "platform"
    / "pages"
    / "InstitutionReviewPage.tsx"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _sources() -> dict[str, str]:
    return {
        "api": _read(API),
        "service": _read(SERVICE),
        "storage": _read(STORAGE) if STORAGE.exists() else "",
        "ports": _read(PORTS),
        "models": _read(MODELS),
        "repository": _read(REPOSITORY),
        "schemas": _read(SCHEMAS),
        "config": _read(CONFIG),
        "database": _read(DATABASE),
        "main": _read(MAIN),
        "tasks": _read(TASKS),
        "celery": _read(CELERY),
        "migration": _read(MIGRATION) if MIGRATION.exists() else "",
        "frontend_api": _read(FRONTEND_API),
        "frontend_page": _read(FRONTEND_PAGE),
    }


def _complete_checks_evidence_before_idempotent_success(source: str) -> bool:
    marker = "async def complete_upload"
    if marker not in source:
        return False
    complete = source.split(marker, 1)[1]
    if "async def record_scan" in complete:
        complete = complete.split("async def record_scan", 1)[0]
    evidence_index = complete.find("payload.size")
    pending_index = complete.find('row.status == "PENDING_SCAN"')
    return 0 <= evidence_index < pending_index


@pytest.mark.parametrize(
    ("contract_id", "predicate"),
    [
        ("B-R01", lambda s: "request.stream()" in s["api"] and "data: bytes = Body" not in s["api"]),
        ("B-R02", lambda s: "write_chunk" in s["ports"] and "received_size" in s["service"]),
        ("B-R03", lambda s: ".part" in s["storage"] and "create_temporary" in s["storage"]),
        ("B-R04", lambda s: "upload_lease_token" in s["models"] and "claim_upload" in s["repository"]),
        ("B-R05", lambda s: "os.replace" in s["storage"] and "fsync" in s["storage"]),
        ("B-R06", lambda s: "cleanup_temporary" in s["storage"] and "active_leases" in s["ports"]),
        ("B-R07", lambda s: all(name in s["celery"] for name in ("PRIVATE_FILE_SCAN_TASK_NAME", "PRIVATE_FILE_RECOVER_TASK_NAME", "PRIVATE_FILE_CLEANUP_TASK_NAME", '"options": {"queue": PRIVATE_FILE_QUEUE}'))),
        ("B-R08", lambda s: 'Literal["local_filesystem", "minio"]' in s["config"] and '= "local_filesystem"' in s["config"] and "KG_FILE_STORAGE_BACKEND" in s["config"]),
        ("B-R09", lambda s: "PRIVATE_FILE_OBJECT_KEY_INVALID" in s["storage"] and "is_relative_to" in s["storage"]),
        ("B-R10", lambda s: "build_private_object_store" in s["main"] and "build_private_object_store" in s["tasks"]),
        ("B-R11", lambda s: "stored_evidence" in s["service"] and "complete_upload" in s["service"]),
        ("B-R12", lambda s: _complete_checks_evidence_before_idempotent_success(s["service"])),
        ("B-R13", lambda s: "PRIVATE_FILE_UPLOAD_IN_PROGRESS" in s["service"]),
        ("B-R14", lambda s: "PRIVATE_FILE_EVIDENCE_MISMATCH" in s["storage"] and "stat(" in s["ports"]),
        ("B-R15", lambda s: "confirm_upload_commit_outcome" in s["service"] and "upload_operation_ref_digest" in s["models"]),
        ("B-R16", lambda s: "content_path" in s["schemas"] and "access_credential" in s["schemas"] and "access_path" not in s["schemas"]),
        ("B-R17", lambda s: "token: str = Query" not in s["api"] and "X-Private-File-Access" in s["api"]),
        ("B-R18", lambda s: "private_file_download_access" in s["migration"] and "consume_access" in s["repository"]),
        ("B-R19", lambda s: "KG_PRIVATE_FILE_ACCESS_WRITER_ROLE" in s["config"] and "batch_b_private_file_access_issue_v1" in s["migration"]),
        ("B-R20", lambda s: "batch_b_private_file_access_confirm_v1" in s["migration"] and "confirm_access" in s["repository"]),
        ("B-R21", lambda s: all(value in s["api"] for value in ("no-store, private, max-age=0", "no-cache", "no-referrer", "nosniff"))),
        ("B-R22", lambda s: "StreamingResponse" in s["api"] and "iter_read" in s["service"]),
        ("B-R23", lambda s: "PRIVATE_FILE_NOT_FOUND" in s["service"] and "PRIVATE_FILE_ACCESS_INVALID" in s["service"]),
        ("B-R24", lambda s: "slice7_export_download_consumer" in s["api"] and "personal_data_export_download_access" not in s["migration"]),
        ("B-R25", lambda s: "response_model" in s["api"] and "PrivateFileMetadata" in s["schemas"]),
        ("B-R26", lambda s: "application/octet-stream" in s["api"] and "413" in s["api"] and "X-Private-File-Access" in s["api"]),
        ("B-R27", lambda s: "access_credential" in s["frontend_api"] and "X-Private-File-Access" in s["frontend_api"] and "access.access_path" not in s["frontend_page"]),
        ("B-R28", lambda s: "?token=" not in s["api"] and "?token=" not in s["frontend_api"] and "?token=" not in s["frontend_page"]),
        ("B-R29", lambda s: "cleanup_temporary" in s["storage"] and "a3_private_file_referenced_v1" in s["repository"]),
        ("B-R30", lambda s: MIGRATION.exists() and STORAGE.exists()),
    ],
    ids=[f"B-R{index:02d}" for index in range(1, 31)],
)
def test_B_R01_R30_完整链路合同先红(contract_id: str, predicate) -> None:
    sources = _sources()
    assert predicate(sources), f"{contract_id}_EXPECTED_CONTRACT_MISSING"


def test_Batch_B只有单一0037_child且禁止历史迁移变化() -> None:
    assert MIGRATION.exists(), "BATCH_B_0038_MIGRATION_MISSING"
    source = _read(MIGRATION)
    assert 'revision = "20260904_0038"' in source
    assert 'down_revision = "20260904_0037"' in source
    assert "20260904_0039" not in source


def test_Batch_B第五function_only身份按正式CI合同闭合传播() -> None:
    workflow = _read(WORKFLOW)
    conftest = _read(INTEGRATION_CONFTEST)
    for token in (
        "KG_TEST_PRIVATE_FILE_ACCESS_WRITER_ROLE",
        "KG_TEST_PRIVATE_FILE_ACCESS_WRITER_DATABASE_URL",
        "KG_PRIVATE_FILE_ACCESS_WRITER_ROLE",
        "KG_PRIVATE_FILE_ACCESS_WRITER_DATABASE_URL",
        "private_file_access_writer_role",
        "private_file_access_writer_password",
    ):
        assert token in workflow, f"BATCH_B_CI_ACCESS_WRITER_MISSING_{token}"
    assert "KG_TEST_PRIVATE_FILE_ACCESS_WRITER_ROLE" in conftest
    assert "KG_TEST_PRIVATE_FILE_ACCESS_WRITER_DATABASE_URL" in conftest
    assert 'REQUIRED_HEAD_REVISION = "20260904_0038"' in conftest


def test_Batch_B正式Integration的private_file_Worker只装配冻结CI扫描器() -> None:
    workflow = _read(WORKFLOW)
    integration_job = workflow.split("  backend-integration:", 1)[1].split(
        "\n  frontend-build:", 1
    )[0]
    assert "KG_TEST_ENVIRONMENT: ci_ephemeral" in integration_job
    assert (
        "KG_PRIVATE_FILE_SCANNER_FACTORY: "
        "tests.test_一期切片1私有文件Celery合同:create_ci_scanner"
    ) in integration_job


def test_Batch_B_BackendUnit私有目录只能在Checkout后由RunnerTemp安全准备() -> None:
    workflow = _read(WORKFLOW)
    unit_job = workflow.split("  backend-unit:", 1)[1].split(
        "\n  backend-integration:", 1
    )[0]
    assert "${{ runner.temp }}" not in unit_job
    checkout = unit_job.index("      - name: Checkout")
    prepare = unit_job.index("      - name: Prepare unit private storage")
    first_python_test = unit_job.index(
        "      - name: Enforce Python lock and Ruff no-new-debt gates"
    )
    assert checkout < prepare < first_python_test
    for token in (
        'storage_root="$RUNNER_TEMP/kg-private-unit-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"',
        '"$RUNNER_TEMP"/kg-private-unit-*',
        'echo "KG_PRIVATE_FILE_STORAGE_ROOT=$storage_root" >>"$GITHUB_ENV"',
        'install -d -m 700 "$storage_root"',
        "if: always()",
        "'') exit 0 ;;",
        'rm -rf -- "$KG_PRIVATE_FILE_STORAGE_ROOT"',
    ):
        assert token in unit_job, f"BATCH_B_UNIT_STORAGE_CONTRACT_MISSING_{token}"


def test_Batch_B_0038闭合函数无JSON且仅授予access_writer执行() -> None:
    source = _read(MIGRATION)
    for name in (
        "batch_b_private_file_access_issue_v1",
        "batch_b_private_file_access_consume_v1",
        "batch_b_private_file_access_confirm_v1",
    ):
        assert name in source
    assert "JSON" not in source.upper()
    assert "REVOKE ALL ON FUNCTION" in source
    assert "GRANT EXECUTE ON FUNCTION" in source
    assert "GRANT SELECT ON TABLE public.private_file_download_access" not in source
    assert "GRANT INSERT ON TABLE public.private_file_download_access" not in source


def test_Batch_B_0038闭合snapshot按角色和authority读取且底表零扩权() -> None:
    migration = _read(MIGRATION)
    repository = _read(REPOSITORY)
    service = _read(SERVICE)
    signature = (
        "public.batch_b_private_file_access_snapshot_v1"
        "(uuid,bigint,varchar)"
    )
    assert signature in migration, "BATCH_B_CLOSED_SNAPSHOT_FUNCTION_MISSING"
    assert "RETURNS TABLE(\n  file_id UUID,purpose VARCHAR,owner_user_id BIGINT,status VARCHAR," in migration
    assert "actual_size BIGINT,actual_mime_type VARCHAR,actual_sha256 VARCHAR," in migration
    assert "object_key VARCHAR,bound_application_id UUID," in migration
    assert "qualification_bound BOOLEAN,reviewer_access BOOLEAN)" in migration
    for token in (
        "KG_INSTITUTION_ONBOARDING_READER_ROLE",
        "KG_SLICE4_CLINICAL_READER_ROLE",
        "KG_SLICE4_INSTITUTION_READER_ROLE",
        "slice4_report_file_authority_v1",
        "OWNER",
        "REVIEWER",
        "FAMILY_AUTHORIZE",
        "THERAPIST_AUTHORIZE",
        "PLATFORM_AUTHORIZE",
        "INSTITUTION_AUTHORIZE",
        "PERSONAL_DATA_EXPORT",
    ):
        assert token in migration, f"BATCH_B_CLOSED_SNAPSHOT_MISSING_{token}"
    assert "closed_access_snapshot" in repository
    assert ".access_snapshot(" not in service
    assert "REVOKE ALL ON FUNCTION {_SNAPSHOT_FUNCTION} FROM PUBLIC" in migration
    assert migration.count("GRANT EXECUTE ON FUNCTION {_SNAPSHOT_FUNCTION}") == 1
    snapshot_body = migration.split(
        "CREATE FUNCTION public.batch_b_private_file_access_snapshot_v1", 1
    )[1].split("$function$", 2)[1]
    assert "JSON" not in snapshot_body.upper()
    assert "[]" not in snapshot_body
    assert "p_context NOT IN" in snapshot_body
    assert "GRANT SELECT (object_key" not in migration
    assert "GRANT SELECT (actual_mime_type" not in migration


class _CancelledCommitSession:
    bind = object()

    def __init__(self) -> None:
        self.primary = asyncio.CancelledError("BATCH_B_PRIMARY_CANCELLATION")
        self.rollback_calls = 0
        self.close_calls = 0

    async def commit(self) -> None:
        raise self.primary

    async def rollback(self) -> None:
        self.rollback_calls += 1

    async def close(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["COMMITTED", "NOT_COMMITTED", "UNKNOWN"])
async def test_Batch_B上传commit取消必须释放Writer并完成独立三态确认(
    monkeypatch, outcome: str
) -> None:
    session = _CancelledCommitSession()
    calls: list[str] = []

    async def confirm(bind, file_id, *, preimage, postimage):
        del bind, file_id, preimage, postimage
        assert session.rollback_calls == 1
        assert session.close_calls == 1
        calls.append("confirm")
        return outcome

    monkeypatch.setattr(service, "confirm_upload_commit_outcome", confirm)
    with pytest.raises(asyncio.CancelledError) as raised:
        await service._commit_upload_state(
            session,
            "00000000-0000-0000-0000-000000000001",
            preimage={"status": "UPLOAD_INITIATED"},
            postimage={"status": "UPLOAD_INITIATED"},
        )

    assert raised.value is session.primary
    assert calls == ["confirm"]


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmation_failure", [asyncio.CancelledError, RuntimeError])
async def test_Batch_B上传commit取消不被独立确认二次失败覆盖(
    monkeypatch, confirmation_failure
) -> None:
    session = _CancelledCommitSession()

    async def confirm(*args, **kwargs):
        del args, kwargs
        raise confirmation_failure("BATCH_B_SECONDARY_CONFIRMATION_FAILURE")

    monkeypatch.setattr(service, "confirm_upload_commit_outcome", confirm)
    with pytest.raises(asyncio.CancelledError) as raised:
        await service._commit_upload_state(
            session,
            "00000000-0000-0000-0000-000000000001",
            preimage={"status": "UPLOAD_INITIATED"},
            postimage={"status": "UPLOAD_INITIATED"},
        )

    assert raised.value is session.primary


class _UploadCancellationRepository:
    row = SimpleNamespace(
        object_key="slice1/batch-b/cancelled.pdf",
        declared_size=1,
        declared_mime_type="application/pdf",
        declared_sha256="0" * 64,
        upload_version=2,
    )

    def __init__(self, session) -> None:
        del session

    async def persistence_snapshot(self, file_id: str):
        return {
            "file_id": file_id,
            "status": "UPLOAD_INITIATED",
            "actual_size": None,
            "actual_mime_type": None,
            "actual_sha256": None,
            "upload_lease_token": None,
            "upload_lease_until": None,
            "upload_operation_ref_digest": None,
            "upload_version": 1,
        }

    async def claim_upload(self, *args, **kwargs):
        del args, kwargs
        return self.row


class _UploadCancellationStore:
    def __init__(self, cleanup_failure) -> None:
        self.cleanup_failure = cleanup_failure
        self.abort_calls = 0
        self.delete_calls = 0

    async def create_temporary(self, object_key: str, lease_token: str) -> None:
        del object_key, lease_token

    async def abort_temporary(self, object_key: str, lease_token: str) -> None:
        del object_key, lease_token
        self.abort_calls += 1
        raise self.cleanup_failure("BATCH_B_SECONDARY_ABORT_FAILURE")


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_failure", [asyncio.CancelledError, RuntimeError])
async def test_Batch_B上传流取消优先于临时清理与claim释放失败(
    monkeypatch, cleanup_failure
) -> None:
    session = SimpleNamespace(bind=object())
    store = _UploadCancellationStore(cleanup_failure)
    primary = asyncio.CancelledError("BATCH_B_PRIMARY_STREAM_CANCELLATION")
    release_calls = 0

    async def committed(*args, **kwargs):
        del args, kwargs
        return "COMMITTED"

    async def release(*args, **kwargs):
        nonlocal release_calls
        del args, kwargs
        release_calls += 1
        raise RuntimeError("BATCH_B_SECONDARY_RELEASE_FAILURE")

    async def chunks():
        raise primary
        yield b""  # pragma: no cover

    monkeypatch.setattr(service, "PrivateFileRepository", _UploadCancellationRepository)
    monkeypatch.setattr(service, "_commit_upload_state", committed)
    monkeypatch.setattr(service, "_release_upload_claim", release)

    with pytest.raises(asyncio.CancelledError) as raised:
        await service.upload_content(
            session,
            1,
            "00000000-0000-0000-0000-000000000001",
            chunks(),
            object_store=store,
        )

    assert raised.value is primary
    assert store.abort_calls == 1
    assert store.delete_calls == 0
    assert release_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("result_code", ["COMMITTED", "NOT_COMMITTED", "UNKNOWN"])
async def test_Batch_B一次性消费commit取消必须独立确认后传播原始取消(
    monkeypatch, result_code: str
) -> None:
    session = _CancelledCommitSession()
    calls: list[str] = []

    class Repo:
        def __init__(self, current) -> None:
            self.current = current

        async def consume_access(self, values):
            del values
            calls.append("consume")
            return {"result_code": "CONSUMED"}

        async def confirm_access(self, values):
            del values
            assert session.close_calls == 1
            calls.append("confirm")
            return {"result_code": result_code}

    class Confirmation:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            del exc_type, exc, traceback

    monkeypatch.setattr(service, "PrivateFileRepository", Repo)
    monkeypatch.setattr(service, "AsyncSession", lambda **kwargs: Confirmation())

    with pytest.raises(asyncio.CancelledError) as raised:
        await service._consume_access(session, {"access_id": "synthetic"})

    assert raised.value is session.primary
    assert calls == ["consume", "confirm"]


def test_Batch_B_0038_downgrade显式撤销access_writer_schema_usage() -> None:
    migration = _read(MIGRATION)
    downgrade = migration.split("def downgrade() -> None:", 1)[1]
    assert 'REVOKE USAGE ON SCHEMA public FROM "{access_writer}"' in downgrade


def test_Batch_B_access函数使用闭合返回码而非异常文本分类() -> None:
    migration = _read(MIGRATION)
    issue = migration.split(
        "CREATE FUNCTION public.batch_b_private_file_access_issue_v1", 1
    )[1].split("$function$", 2)[1]
    consume = migration.split(
        "CREATE FUNCTION public.batch_b_private_file_access_consume_v1", 1
    )[1].split("$function$", 2)[1]

    for result_code in ("ISSUED", "NOT_AVAILABLE", "EVIDENCE_MISMATCH"):
        assert f"'{result_code}'::VARCHAR" in issue
    assert issue.index("SELECT * INTO stored") < issue.index(
        "SELECT * INTO file_row"
    )
    assert "RAISE EXCEPTION 'BATCH_B_PRIVATE_FILE_NOT_AVAILABLE'" not in issue
    for result_code in (
        "CONSUMED",
        "ACCESS_INVALID",
        "NOT_AVAILABLE",
        "EVIDENCE_MISMATCH",
    ):
        assert f"'{result_code}'::VARCHAR" in consume
    assert "RAISE EXCEPTION 'BATCH_B_PRIVATE_FILE_ACCESS_INVALID'" not in consume
    assert "RAISE EXCEPTION 'BATCH_B_PRIVATE_FILE_ACCESS_ALREADY_CONSUMED'" not in consume
    assert "parse" not in _read(SERVICE).lower().split("async def _issue_access", 1)[1].split(
        "async def _closed_access_snapshot", 1
    )[0]


class _AccessResultSession:
    bind = object()

    def __init__(self) -> None:
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

    async def commit(self) -> None:
        self.commit_calls += 1

    async def rollback(self) -> None:
        self.rollback_calls += 1

    async def close(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "result_code", "status_code", "detail"),
    [
        ("issue", "NOT_AVAILABLE", 404, "PRIVATE_FILE_NOT_FOUND"),
        ("issue", "EVIDENCE_MISMATCH", 409, "PRIVATE_FILE_EVIDENCE_MISMATCH"),
        ("consume", "ACCESS_INVALID", 403, "PRIVATE_FILE_ACCESS_INVALID"),
        ("consume", "NOT_AVAILABLE", 404, "PRIVATE_FILE_NOT_FOUND"),
        ("consume", "EVIDENCE_MISMATCH", 409, "PRIVATE_FILE_EVIDENCE_MISMATCH"),
    ],
)
async def test_Batch_B显式access失败码不进入commit确认(
    monkeypatch,
    operation: str,
    result_code: str,
    status_code: int,
    detail: str,
) -> None:
    session = _AccessResultSession()
    confirmation_calls = 0

    class Repo:
        def __init__(self, current) -> None:
            del current

        async def issue_access(self, values):
            del values
            return {"result_code": result_code}

        async def consume_access(self, values):
            del values
            return {"result_code": result_code}

    async def confirm(*args, **kwargs):
        nonlocal confirmation_calls
        del args, kwargs
        confirmation_calls += 1
        return {"result_code": "UNKNOWN"}

    monkeypatch.setattr(service, "PrivateFileRepository", Repo)
    monkeypatch.setattr(service, "_confirm_access_commit_outcome", confirm)

    with pytest.raises(HTTPException) as raised:
        if operation == "issue":
            await service._issue_access(session, {"access_id": "synthetic"})
        else:
            await service._consume_access(session, {"access_id": "synthetic"})

    assert raised.value.status_code == status_code
    assert raised.value.detail == detail
    assert session.commit_calls == 0
    assert session.rollback_calls == 1
    assert session.close_calls == 1
    assert confirmation_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["issue", "consume"])
async def test_Batch_B显式access失败清理取消优先且只执行一次(
    monkeypatch, operation: str
) -> None:
    primary = asyncio.CancelledError("BATCH_B_ACCESS_CLEANUP_CANCELLED")
    confirmation_calls = 0

    class Session(_AccessResultSession):
        async def rollback(self) -> None:
            self.rollback_calls += 1
            raise primary

    class Repo:
        def __init__(self, current) -> None:
            del current

        async def issue_access(self, values):
            del values
            return {"result_code": "NOT_AVAILABLE"}

        async def consume_access(self, values):
            del values
            return {"result_code": "ACCESS_INVALID"}

    async def confirm(*args, **kwargs):
        nonlocal confirmation_calls
        del args, kwargs
        confirmation_calls += 1
        return {"result_code": "UNKNOWN"}

    session = Session()
    monkeypatch.setattr(service, "PrivateFileRepository", Repo)
    monkeypatch.setattr(service, "_confirm_access_commit_outcome", confirm)

    with pytest.raises(asyncio.CancelledError) as raised:
        if operation == "issue":
            await service._issue_access(session, {"access_id": "synthetic"})
        else:
            await service._consume_access(session, {"access_id": "synthetic"})

    assert raised.value is primary
    assert session.commit_calls == 0
    assert session.rollback_calls == 1
    assert session.close_calls == 1
    assert confirmation_calls == 0


def _private_file_test_app(monkeypatch, *, auth_error: HTTPException | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(private_file_api.router)
    app.state.private_object_store = SimpleNamespace()

    async def current_user():
        if auth_error is not None:
            raise auth_error
        return CurrentUser(id=1, role="member")

    async def session():
        return SimpleNamespace()

    app.dependency_overrides[get_current_user_from_jwt] = current_user
    for dependency in (
        get_db_session,
        get_institution_onboarding_reader_session,
        get_private_file_access_writer_session,
        get_private_file_writer_session,
        get_slice4_clinical_reader_session,
        get_slice4_institution_reader_session,
        get_slice7_transfer_writer_session,
    ):
        app.dependency_overrides[dependency] = session
    return app


def _assert_private_error(response, *, status_code: int, detail: str) -> None:
    assert response.status_code == status_code
    assert response.json() == {"detail": detail}
    for name, value in private_file_api._PRIVATE_HEADERS.items():
        assert response.headers[name] == value


def test_Batch_B_private_file路由422删除原始input并返回全部私有头(
    monkeypatch, caplog
) -> None:
    sentinel = "pw://x"
    with TestClient(_private_file_test_app(monkeypatch)) as client:
        response = client.post(
            "/api/v1/private-files/00000000-0000-0000-0000-000000000001/access",
            json={"reason_code": "OWNER_DOWNLOAD", "reauth_password": sentinel},
        )

    _assert_private_error(
        response, status_code=422, detail="PRIVATE_FILE_REQUEST_INVALID"
    )
    assert sentinel not in response.text
    assert sentinel not in caplog.text


@pytest.mark.parametrize(
    ("status_code", "detail"),
    [
        (401, "Invalid or expired token"),
        (403, "PRIVATE_FILE_ACCESS_INVALID"),
        (404, "PRIVATE_FILE_NOT_FOUND"),
        (409, "PRIVATE_FILE_EVIDENCE_MISMATCH"),
        (413, "PRIVATE_FILE_SIZE_LIMIT_EXCEEDED"),
        (503, "PRIVATE_FILE_ACCESS_OUTCOME_UNKNOWN"),
    ],
)
def test_Batch_B_private_file全部HTTP错误保持私有头且不泄漏底层输入(
    monkeypatch, status_code: int, detail: str
) -> None:
    sentinel = "postgresql://synthetic-user:synthetic-secret@localhost/synthetic"
    if status_code == 401:
        app = _private_file_test_app(
            monkeypatch, auth_error=HTTPException(status_code, detail)
        )
        method, path, kwargs = "get", "/api/v1/private-files/synthetic", {}
    elif status_code == 403:
        app = _private_file_test_app(monkeypatch)
        method, path, kwargs = (
            "get",
            "/api/v1/private-files/00000000-0000-0000-0000-000000000001/content",
            {},
        )
    elif status_code == 413:
        app = _private_file_test_app(monkeypatch)
        method, path, kwargs = (
            "put",
            "/api/v1/private-files/uploads/00000000-0000-0000-0000-000000000001/content",
            {"content": b"", "headers": {"Content-Length": str(10 * 1024 * 1024 + 1)}},
        )
    else:
        app = _private_file_test_app(monkeypatch)

        async def fail(*args, **kwargs):
            del args, kwargs
            raise HTTPException(status_code, detail)

        monkeypatch.setattr(private_file_api, "metadata", fail)
        method, path, kwargs = "get", "/api/v1/private-files/synthetic", {}

    with TestClient(app, raise_server_exceptions=False) as client:
        response = getattr(client, method)(path, **kwargs)

    _assert_private_error(response, status_code=status_code, detail=detail)
    assert sentinel not in response.text
