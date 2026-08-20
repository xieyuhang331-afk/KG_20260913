import ast
import asyncio
from datetime import date, datetime, timezone
from enum import Enum
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from app.modules.member_enrollment.service import (
    CommitOutcome,
    commit_with_confirmation,
)
from app.modules.member_enrollment.repository import MemberEnrollmentRepository


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


@pytest.mark.parametrize(
    ("microsecond", "expected"),
    (
        (0, "2026-08-20T12:34:56+00:00"),
        (100000, "2026-08-20T12:34:56.1+00:00"),
        (120000, "2026-08-20T12:34:56.12+00:00"),
        (123000, "2026-08-20T12:34:56.123+00:00"),
        (123456, "2026-08-20T12:34:56.123456+00:00"),
    ),
)
def test_PostgreSQL_timestamp后像固定样本规范化(microsecond: int, expected: str) -> None:
    value = datetime(2026, 8, 20, 12, 34, 56, microsecond, tzinfo=timezone.utc)
    assert MemberEnrollmentRepository._json_value(value) == expected


def test_timestamp嵌套规范与date_UUID_bytes_Enum保持兼容() -> None:
    class Sample(Enum):
        VALUE = "value"

    timestamp = datetime(2026, 8, 20, 12, 34, 56, 120000, tzinfo=timezone.utc)
    identifier = UUID("018f7e2a-4f5c-7a91-8c21-123456789abc")
    value = {
        "nested": [timestamp, {"again": timestamp}],
        "date": date(2026, 8, 20),
        "uuid": identifier,
        "bytes": b"\x00\xff",
        "enum": Sample.VALUE,
    }
    assert MemberEnrollmentRepository._json_value(value) == {
        "nested": [
            "2026-08-20T12:34:56.12+00:00",
            {"again": "2026-08-20T12:34:56.12+00:00"},
        ],
        "date": "2026-08-20",
        "uuid": str(identifier),
        "bytes": "\\x00ff",
        "enum": "value",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method_name", ("expected_mutation_postimage", "confirm_mutation_outcome")
)
async def test_ExpectedEnvelope两个数据库边界统一规范receipt时间(
    method_name: str,
) -> None:
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one.return_value = "a" * 64
    result.mappings.return_value.one.return_value = {
        "outcome": "COMMITTED",
        "confirmed_postimage_digest": "a" * 64,
    }
    session.execute.return_value = result
    repository = MemberEnrollmentRepository(session)
    target_id = UUID("018f7e2a-4f5c-7a91-8c21-123456789abc")
    expected = {
        "receipt": {
            "created_at": datetime(
                2026, 8, 20, 12, 34, 56, 120000, tzinfo=timezone.utc
            )
        }
    }
    await getattr(repository, method_name)(
        "enrollment_writer",
        actor_scope="user:1",
        operation="INVITATION_CREATE",
        target_id=target_id,
        idempotency_key="timestamp-contract",
        request_digest="b" * 64,
        expected_postimage=expected,
    )
    parameters = session.execute.await_args.args[1]
    serialized = json.loads(parameters["expected_postimage"])
    assert serialized["receipt"]["created_at"] == "2026-08-20T12:34:56.12+00:00"


def test_API_receipt保留原始datetime并交由Repository规范化() -> None:
    source = API.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_finish_mutation"
    )
    body = ast.get_source_segment(source, function) or ""
    assert '"created_at": receipt_created_at,' in body
    assert "receipt_created_at.isoformat()" not in body


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
