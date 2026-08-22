from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from asyncpg.pgproto.pgproto import UUID as AsyncpgUUID

from app.modules.member_enrollment.service import MemberEnrollmentSecrets


ROOT = Path(__file__).resolve().parents[1]
UUID7 = UUID("01900000-0000-7000-8000-000000000025")
MIGRATION = (
    ROOT
    / "app"
    / "migrations"
    / "versions"
    / "20260822_0025_phase1_slice3_member_identity_revision_bounded_read.py"
)


def test_0025线性迁移与两个受限读接口已冻结() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "20260822_0025"' in source
    assert 'down_revision = "20260821_0024"' in source
    assert "slice3_identity_revision_summary_v1" in source
    assert "slice3_identity_revision_correction_v1" in source
    assert "SECURITY DEFINER" in source
    assert "SET search_path=pg_catalog,pg_temp" in source
    assert "session_user" in source
    assert "REVOKE ALL ON FUNCTION" in source
    assert "FROM PUBLIC" in source
    assert "GRANT EXECUTE ON FUNCTION" in source
    assert "GRANT SELECT" not in source
    assert source.count(
        "GRANT UPDATE (platform_decision_id,platform_decided_at)"
    ) == 1
    assert source.count(
        "REVOKE UPDATE (platform_decision_id,platform_decided_at)"
    ) == 1
    assert "GRANT UPDATE ON TABLE" not in source
    assert "EXECUTE IMMEDIATE" not in source


def test_Repository不再直接读取会员实名Revision基础表() -> None:
    source = (
        ROOT / "app" / "modules" / "member_enrollment" / "repository.py"
    ).read_text(encoding="utf-8")

    assert "slice3_identity_revision_summary_v1" in source
    assert "slice3_identity_revision_correction_v1" in source


def test_回放AAD仅规范化UUID子类且拒绝伪造对象() -> None:
    builtin = MemberEnrollmentSecrets._replay_aad(
        "tenant:member", "INSTITUTION_IDENTITY_CHECK", UUID7, "key", "kid"
    )
    database_value = MemberEnrollmentSecrets._replay_aad(
        "tenant:member",
        "INSTITUTION_IDENTITY_CHECK",
        AsyncpgUUID(str(UUID7)),
        "key",
        "kid",
    )

    assert database_value == builtin
    with pytest.raises(RuntimeError):
        MemberEnrollmentSecrets._replay_aad(
            "tenant:member", "INSTITUTION_IDENTITY_CHECK", object(), "key", "kid"
        )
