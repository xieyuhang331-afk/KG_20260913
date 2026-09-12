import ast
import asyncio
import hashlib
import inspect
import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "app/migrations/versions/20260906_0039_健管师机构身份闭合读取.py"
SIGNATURE = "public.slice2_institution_identity_authority_v1(BIGINT)"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["invitation", "readiness"])
@pytest.mark.parametrize("matched", [False, True])
async def test_E08_有限投影匹配不能唯一确认本次操作(monkeypatch, kind, matched):
    from app.core import database
    from app.modules.therapist_qualification import service

    # Another transaction can produce the same finite version/state projection.
    rows = [("INVITED", 2, 3)] if kind == "invitation" else [(3, "input", "result"), (17, 3, "input", "result")]
    if not matched:
        rows = [None for _ in rows]

    class Confirmation:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, *_args):
            result = MagicMock()
            result.one_or_none.return_value = rows.pop(0)
            return result

        async def rollback(self):
            return None

    outcomes = []

    async def capture_commit(_session, *, confirm):
        outcomes.append(await confirm())

    monkeypatch.setattr(database, "get_slice2_session_factory", lambda _: Confirmation)
    monkeypatch.setattr(service, "_commit", capture_commit)
    if kind == "invitation":
        await service._commit_invitation_failure(object(), invitation_id="synthetic", failed_attempts=2, version=3)
    else:
        await service._commit_readiness(object(), tenant_id=17, trigger_event_id="synthetic", evidence_version=3, input_digest="input", result_digest="result")
    assert outcomes == [service.UNKNOWN], "GATE_FINITE_PROJECTION_IS_NOT_OPERATION_PROOF"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["application", "slice2"])
@pytest.mark.parametrize("body_kind", ["none", "error", "cancel"])
@pytest.mark.parametrize("cleanup_kind", ["error", "cancel"])
async def test_E07_会话拥有者清理不覆盖主异常(monkeypatch, kind, body_kind, cleanup_kind):
    from app.core import database

    primary = {"none": None, "error": RuntimeError("SAFE_BODY"), "cancel": asyncio.CancelledError()}[body_kind]
    cleanup = RuntimeError("SAFE_CLOSE") if cleanup_kind == "error" else asyncio.CancelledError()
    events = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            await self.close()

        async def close(self):
            events.append("close")
            raise cleanup

        async def invalidate(self):
            events.append("invalidate")

    monkeypatch.setattr(database, "get_session_factory", lambda: Session)
    monkeypatch.setattr(database, "get_slice2_session_factory", lambda _: Session)
    context = asynccontextmanager(database.get_db_session)() if kind == "application" else database._slice2_session("reader")
    observed = None
    try:
        async with context:
            if primary is not None:
                raise primary
    except (Exception, asyncio.CancelledError) as error:
        observed = error
    expected = primary if primary is not None else cleanup
    if cleanup_kind == "cancel" and not isinstance(primary, asyncio.CancelledError):
        expected = cleanup
    if primary is None and cleanup_kind == "error":
        assert isinstance(observed, RuntimeError) and str(observed) == "DATABASE_SESSION_CLEANUP_FAILED"
    else:
        assert observed is expected, "GATE_DEPENDENCY_PRIMARY_REPLACED"
    assert events == ["close", "invalidate"], "GATE_DEPENDENCY_CLEANUP_INCOMPLETE"


@pytest.mark.asyncio
@pytest.mark.parametrize("dependency_name", [
    "get_db_session",
    "get_therapist_onboarding_writer_session",
    "get_therapist_review_writer_session",
    "get_therapist_readiness_worker_session",
    "get_therapist_reader_session",
])
async def test_E07_HTTP依赖异常必须传至清理拥有者(monkeypatch, dependency_name):
    from fastapi import Depends, FastAPI, HTTPException

    from app.core import database

    primary = HTTPException(409, "SAFE_BODY_FAILURE")
    observed = []

    class SessionContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, kind, value, traceback):
            observed.append(value)
            return False

        async def close(self):
            return None

    original_close = database._close_owned_session

    async def observed_close(session, error):
        observed.append(error)
        await original_close(session, error)

    monkeypatch.setattr(database, "get_session_factory", lambda: SessionContext)
    monkeypatch.setattr(database, "get_slice2_session_factory", lambda kind: SessionContext)
    monkeypatch.setattr(database, "_close_owned_session", observed_close)
    dependency = getattr(database, dependency_name)
    app = FastAPI()
    session_dependency = Depends(dependency)

    @app.get("/dependency-probe")
    async def probe(session=session_dependency):
        assert isinstance(session, SessionContext), "GATE_DEPENDENCY_SESSION_INVALID"
        raise primary

    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": "/dependency-probe",
        "raw_path": b"/dependency-probe", "query_string": b"", "root_path": "",
        "headers": [], "server": ("test", 80), "client": ("127.0.0.1", 1),
    }
    await app(scope, receive, send)
    assert any(message.get("status") == 409 for message in messages)
    assert len(observed) == 1 and observed[0] is primary, "GATE_DEPENDENCY_PRIMARY_NOT_PROPAGATED"


def test_E01_新增补正闭合函数与两个调用入口():
    source = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE FUNCTION public.slice2_therapist_correction_authority_v1" in source, "GATE_CORRECTION_FUNCTION_MISSING"
    from app.modules.therapist_qualification import service
    for name in ("resubmit", "renewal_resubmit"):
        assert "_correction_authority(" in inspect.getsource(getattr(service, name)), "GATE_CORRECTION_CALL_MISSING"


@pytest.mark.asyncio
async def test_E07_提交取消遇回滚失败仍保留取消主异常():
    from app.modules.therapist_qualification.service import _commit
    cancellation = asyncio.CancelledError()
    session = AsyncMock()
    session.commit.side_effect = cancellation
    session.rollback.side_effect = RuntimeError("SAFE_ROLLBACK_FAILURE")
    observed = None
    try:
        await _commit(session)
    except BaseException as error:
        observed = error
    session.rollback.assert_awaited_once()
    assert observed is cancellation, "GATE_CANCEL_PRIMARY_REPLACED"


@pytest.mark.asyncio
async def test_E08_清理失败不得盲目确认成功():
    from fastapi import HTTPException

    from app.modules.therapist_qualification import service
    session = AsyncMock()
    session.commit.side_effect = RuntimeError("SAFE_COMMIT_UNCERTAIN")
    session.rollback.side_effect = RuntimeError("SAFE_ROLLBACK_FAILURE")
    confirm = AsyncMock(return_value=service.COMMITTED)
    with pytest.raises(HTTPException) as caught:
        await service._commit(session, confirm=confirm)
    assert caught.value.status_code == 503
    assert caught.value.detail == "COMMIT_OUTCOME_UNKNOWN"
    confirm.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["COMMITTED", "NOT_COMMITTED", "UNKNOWN", True, False, None, "OTHER"])
async def test_E08_只有安全释放后精确已提交可接受(outcome):
    from fastapi import HTTPException

    from app.modules.therapist_qualification import service

    events = []

    class Session:
        async def commit(self):
            events.append("commit")
            raise RuntimeError("SAFE_COMMIT_UNCERTAIN")

        async def rollback(self):
            events.append("rollback")

        async def close(self):
            events.append("close")

        async def invalidate(self):
            events.append("invalidate")

    async def confirm():
        assert events == ["commit", "rollback", "close"], "GATE_CONFIRM_BEFORE_RELEASE"
        events.append("confirm")
        return outcome

    if outcome == "COMMITTED":
        await service._commit(Session(), confirm=confirm)
    else:
        with pytest.raises(HTTPException) as caught:
            await service._commit(Session(), confirm=confirm)
        assert caught.value.status_code == 503
        assert caught.value.detail == "COMMIT_OUTCOME_UNKNOWN"
    assert events == ["commit", "rollback", "close", "confirm"], "GATE_CONFIRM_RELEASE_ORDER_INVALID"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["rollback", "close", "invalidate"])
async def test_E07_清理再次取消仍保留提交取消(stage):
    from app.modules.therapist_qualification import service

    primary = asyncio.CancelledError()
    secondary = asyncio.CancelledError()
    events = []

    class Session:
        async def commit(self):
            raise primary

        async def rollback(self):
            events.append("rollback")
            if stage == "rollback":
                raise secondary
            if stage == "invalidate":
                raise RuntimeError("SAFE_ROLLBACK_FAILURE")

        async def close(self):
            events.append("close")
            if stage == "close":
                raise secondary

        async def invalidate(self):
            events.append("invalidate")
            if stage == "invalidate":
                raise secondary

    confirm = AsyncMock()
    with pytest.raises(asyncio.CancelledError) as caught:
        await service._commit(Session(), confirm=confirm)
    assert caught.value is primary, "GATE_CANCEL_PRIMARY_REPLACED"
    assert events == ["rollback", "close", "invalidate"], "GATE_CLEANUP_INCOMPLETE"
    confirm.assert_not_awaited()


def test_G02_唯一新修订与历史Hash():
    assert MIGRATION.exists(), "GATE_0039_MIGRATION_MISSING"
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "20260906_0039"' in source
    assert 'down_revision = "20260904_0038"' in source
    versions = []
    for path in sorted(MIGRATION.parent.glob("*.py")):
        # 0040 through 0042 have their own revision, down-revision, and graph contracts.
        if path.name in {
            "__init__.py",
            MIGRATION.name,
            "20260909_0040_认证主体与当前身份受限读取.py",
            "20260910_0041_注册会员受限写入.py",
            "20260911_0042_机构当前性受限读取.py",
            "20260912_0043_健管师业务当前性受限读取.py",
            "20260913_0044_超级管理员直接开通机构.py",
            "20260913_0044_机构邀请Reviewer锁定当前性.py",
        }:
            continue
        value = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
        versions.append([path.name, hashlib.sha256(value.encode()).hexdigest()])
    assert len(versions) == 38
    digest = hashlib.sha256(json.dumps(versions, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    assert digest == "d030940842b215299aa5db9db9adec26b4bdb2a7e9d280e48bc6359ea63ced83"


def test_G02_0042不得改写或替代0039机构身份权威():
    currentness = (
        MIGRATION.parent / "20260911_0042_机构当前性受限读取.py"
    ).read_text(encoding="utf-8")
    source = MIGRATION.read_text(encoding="utf-8")
    assert SIGNATURE in source
    assert "slice2_institution_identity_authority_v1" not in currentness
    assert "DROP FUNCTION public.slice2_institution_identity_authority_v1" not in currentness


def test_G02_强类型锁与最小权限静态合同():
    assert MIGRATION.exists(), "GATE_0039_MIGRATION_MISSING"
    source = MIGRATION.read_text(encoding="utf-8")
    for token in (
        SIGNATURE, "RETURNS UUID", "SECURITY DEFINER", "VOLATILE",
        "SET search_path=pg_catalog,pg_temp", "session_user",
        "FOR SHARE OF a,t", "a.status='APPROVED'", "t.status='active'",
        "a.tenant_public_id IS NOT NULL", "value_tenant <= 0",
        "INTO STRICT", "WHEN NO_DATA_FOUND", "WHEN TOO_MANY_ROWS",
        "REVOKE ALL ON FUNCTION", "FROM PUBLIC", "GRANT EXECUTE ON FUNCTION",
    ):
        assert token in source
    assert "GRANT SELECT" not in source and "GRANT UPDATE" not in source
    assert "LIMIT 1" not in source
    assert source.count("CREATE FUNCTION") == 2
    assert "CREATE FUNCTION public.slice2_therapist_correction_authority_v1" in source
    tree = ast.parse(source)
    downgrade = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "downgrade")
    text = ast.get_source_segment(source, downgrade)
    assert "DROP FUNCTION" in text
    assert all(token not in text for token in ("DROP TABLE", "UPDATE public.", "DELETE FROM", "INSERT INTO"))


def test_E01_补正函数五入十四出与原锁序():
    source = MIGRATION.read_text(encoding="utf-8")
    expected = (
        "actor_user_id BIGINT", "actor_tenant_id BIGINT", "subject_therapist_id UUID",
        "correction_decision_id UUID", "renewal_review_item_id UUID",
        "decision_therapist_id UUID", "decision_kind VARCHAR", "decision_revision_id UUID",
        "decision_review_item_id UUID", "allow_real_name BOOLEAN", "allow_display_name BOOLEAN",
        "allow_practice_summary BOOLEAN", "allow_service_tags BOOLEAN", "qualification_target UUID",
        "item_therapist_id UUID", "item_review_kind VARCHAR", "item_status VARCHAR",
        "item_version BIGINT", "item_qualification_version_id UUID",
    )
    assert all(value in source for value in expected), "GATE_CORRECTION_TYPED_SIGNATURE_MISSING"
    assert "FOR UPDATE OF ri" in source and "FOR UPDATE OF rd" in source
    assert source.index("FOR UPDATE OF ri") < source.index("FOR UPDATE OF rd")
    assert "KG_THERAPIST_ONBOARDING_WRITER_ROLE" in source
    assert "jsonb_array_length" in source and "jsonb_typeof" in source
    assert "SLICE2_CORRECTION_AUTHORITY_FORBIDDEN" in source


@pytest.mark.asyncio
async def test_G01_helper只用原authority连接闭合函数():
    from app.modules.therapist_qualification.api import _tenant_public_id
    session = AsyncMock()
    value = UUID("01990000-0000-7000-8000-000000000abc")
    result = MagicMock()
    result.one_or_none.return_value = (value,)
    session.execute.return_value = result
    assert await _tenant_public_id(session, 17) == str(value)
    call = session.execute.await_args
    assert str(call.args[0]) == "SELECT public.slice2_institution_identity_authority_v1(:tenant_id)"
    assert call.args[1] == {"tenant_id": 17}
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_G03_空标量保留403且取消透传():
    from fastapi import HTTPException

    from app.modules.therapist_qualification.api import _tenant_public_id
    session = AsyncMock()
    result = MagicMock()
    result.one_or_none.return_value = (None,)
    session.execute.return_value = result
    with pytest.raises(HTTPException) as caught:
        await _tenant_public_id(session, 17)
    assert caught.value.status_code == 403
    assert caught.value.detail == "ACTOR_CURRENTNESS_FORBIDDEN"
    cancellation = asyncio.CancelledError()
    session.execute.side_effect = cancellation
    with pytest.raises(asyncio.CancelledError) as cancelled:
        await _tenant_public_id(session, 17)
    assert cancelled.value is cancellation


@pytest.mark.asyncio
async def test_G06_actor_precommit保留原专用session与身份比较(monkeypatch):
    from fastapi import HTTPException

    from app.core.security import CurrentUser
    from app.modules.therapist_qualification import api
    session = object()
    actor = CurrentUser(id=1, role="org_admin", tenant_id=17, org_id=None)
    authority = AsyncMock()
    monkeypatch.setattr(api, "require_institution_actor", authority)
    authority.return_value = "different"
    check = api._actor_precommit(session, actor, "institution", "expected")
    with pytest.raises(HTTPException) as caught:
        await check()
    assert caught.value.status_code == 403
    authority.assert_awaited_once_with(session, actor)


def test_G05_八入口使用原生业务权威且提交次序保持():
    from app.modules.therapist_qualification import api, service
    expected = {
        "post_invitation": "require_institution_actor(session, actor)",
        "post_activate": "_activation_currentness(session, payload.invitation_id)",
        "get_profile": "require_therapist(session, actor)",
        "put_profile": "require_therapist(session, actor)",
        "post_submit": "require_therapist(session, actor)",
        "post_resubmit": "require_therapist(session, actor)",
        "post_renew": "require_therapist(session, actor)",
        "post_renewal_resubmit": "require_therapist(session, actor)",
    }
    for name, authority_call in expected.items():
        source = inspect.getsource(getattr(api, name))
        assert authority_call in source
        assert "authority_session" not in source
    source = inspect.getsource(service._commit_receipt)
    assert source.index("await precommit_check()") < source.index("await _commit(")
