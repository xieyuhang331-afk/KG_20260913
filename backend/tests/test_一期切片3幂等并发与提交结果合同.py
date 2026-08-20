import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.modules.member_enrollment.service import (
    CommitOutcome,
    commit_with_confirmation,
)


ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "app/modules/member_enrollment/api.py"


MUTATION_FUNCTIONS = {
    "create_invitation", "resend_invitation", "revoke_invitation",
    "institution_identity_check", "create_assignment", "cancel_assignment",
    "accept_enrollment", "identity_submission", "identity_resubmit",
    "consent_record", "withdraw_consent", "revoke_proxy", "claim_review",
    "pii_access", "platform_decision", "create_document", "publish_document",
    "retire_document", "accept_assignment", "decline_assignment",
}


@pytest.mark.asyncio
async def test_commit成功不执行fresh_confirmation() -> None:
    session = AsyncMock()
    confirm = AsyncMock()
    outcome = await commit_with_confirmation(session, confirm=confirm)
    assert outcome is CommitOutcome.COMMITTED
    confirm.assert_not_awaited()


@pytest.mark.asyncio
async def test_commit异常后完整后像可确认才返回committed() -> None:
    session = AsyncMock()
    session.commit.side_effect = ConnectionError("unsafe vendor detail")
    confirm = AsyncMock(return_value=CommitOutcome.COMMITTED)
    outcome = await commit_with_confirmation(session, confirm=confirm)
    assert outcome is CommitOutcome.COMMITTED
    session.rollback.assert_awaited_once()
    confirm.assert_awaited_once()


@pytest.mark.asyncio
async def test_partial或无法确认固定unknown且不二次commit() -> None:
    session = AsyncMock()
    session.commit.side_effect = ConnectionError("unsafe vendor detail")
    confirm = AsyncMock(return_value=CommitOutcome.UNKNOWN)
    with pytest.raises(RuntimeError, match="^COMMIT_OUTCOME_UNKNOWN$"):
        await commit_with_confirmation(session, confirm=confirm)
    assert session.commit.await_count == 1
    assert confirm.await_count == 1


@pytest.mark.asyncio
async def test_confirm明确零写入返回安全不可用且不重写() -> None:
    session = AsyncMock()
    session.commit.side_effect = ConnectionError("unsafe vendor detail")
    confirm = AsyncMock(return_value=CommitOutcome.NOT_COMMITTED)
    with pytest.raises(RuntimeError, match="^MEMBER_ENROLLMENT_DEPENDENCY_UNAVAILABLE$"):
        await commit_with_confirmation(session, confirm=confirm)
    assert session.commit.await_count == 1


@pytest.mark.asyncio
async def test_partial_postimage为UNKNOWN且CancelledError传播() -> None:
    cancelled = AsyncMock()
    cancelled.commit.side_effect = asyncio.CancelledError
    confirm = AsyncMock()
    with pytest.raises(asyncio.CancelledError):
        await commit_with_confirmation(cancelled, confirm=confirm)
    confirm.assert_not_awaited()

    migration = (
        ROOT
        / "app/migrations/versions/20260818_0022_phase1_slice3_member_proxy_consent_service_case.py"
    ).read_text(encoding="utf-8")
    downgrade = migration[migration.index("def downgrade()") :]
    api_source = API.read_text(encoding="utf-8")
    for name in (
        "slice3_enrollment_mutation_confirm_v1",
        "slice3_identity_review_mutation_confirm_v1",
        "slice3_case_mutation_confirm_v1",
    ):
        assert f"FUNCTION public.{name}" in migration
        assert f"public.{name}(VARCHAR,VARCHAR,UUID,VARCHAR,CHAR,JSONB)" in downgrade
    assert "DROP FUNCTION {signature}" in downgrade
    assert "expected_postimage JSONB" in migration
    assert "confirmed_postimage_digest" in migration
    assert "confirm_mutation_outcome" in api_source
    assert "replay is not None" not in api_source[api_source.index("async def confirm()") : api_source.index("async def confirm()") + 800]


def test_全部mutation统一使用加密receipt和fresh_confirmation边界() -> None:
    source = API.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {
        node.name: ast.get_source_segment(source, node) or ""
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert MUTATION_FUNCTIONS <= functions.keys()
    for name in MUTATION_FUNCTIONS:
        body = functions[name]
        assert "_begin_mutation" in body, name
        assert "_finish_mutation" in body, name
        assert ".commit(" not in body, name
    assert "encrypt_replay" in source
    assert "get_slice3_session_factory(kind)" in source


def test_B2_expected_digest不得为空且逐operation目录独立() -> None:
    source = API.read_text(encoding="utf-8")
    migration = (
        ROOT
        / "app/migrations/versions/20260818_0022_phase1_slice3_member_proxy_consent_service_case.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {
        node.name: ast.get_source_segment(source, node) or ""
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert '"expected_confirmed_digest": ""' not in source
    assert "_prewrite_confirmation_postimage" in functions["_begin_mutation"]
    assert 'session.info["slice3-mutation-plan"]' in functions["_begin_mutation"]
    assert "expected_mutation_postimage" in functions["_finish_mutation"]
    assert 'expected_envelope["postimage"]["rows"]' in functions["_finish_mutation"]
    assert 'expected_confirmed_digest="0" * 64' not in functions["_finish_mutation"]
    assert "expected_confirmed_digest=('0000000000000000000000000000000000000000000000000000000000000000')" not in migration
    expected_function = migration[
        migration.index('expected_name = name.replace("_confirm_v1", "_expected_v1")') :
        migration.index("def _safe_interfaces")
    ]
    assert "FROM public.{name}" not in expected_function
    assert "expected_postimage::text" in expected_function
    assert "pg_catalog.sha256" in expected_function
    assert "FROM public.{name}" not in expected_function
    for key in ("preimage", "postimage", "audit", "outbox", "receipt"):
        assert f'"{key}"' in source
