from __future__ import annotations

import inspect
from pathlib import Path

from app.modules.member_enrollment.api import _therapist_current
from app.modules.therapist_qualification import service as therapist_service
from app.modules.therapist_qualification.service import (
    require_institution_actor,
    require_reviewer,
    require_therapist,
    translate_error,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "app/migrations/versions/20260912_0043_健管师业务当前性受限读取.py"

FUNCTIONS = (
    "slice2_institution_business_currentness_v1",
    "slice2_therapist_activation_currentness_v1",
    "slice2_therapist_onboarding_currentness_v1",
    "slice3_therapist_service_currentness_v1",
    "slice2_therapist_reviewer_currentness_v1",
    "slice2_therapist_review_target_currentness_v1",
    "slice2_therapist_review_item_currentness_v1",
    "slice2_therapist_self_exit_currentness_v1",
)


def _migration_source() -> str:
    assert MIGRATION.is_file(), "Expected RED: Migration 0043尚未创建"
    return MIGRATION.read_text(encoding="utf-8")


def test_0043线性继承0042且只建立八个固定用途权威() -> None:
    source = _migration_source()
    assert 'revision = "20260912_0043"' in source
    assert 'down_revision = "20260911_0042"' in source
    for name in FUNCTIONS:
        assert f"CREATE FUNCTION public.{name}(" in source
    assert source.count("CREATE FUNCTION public.slice") == len(FUNCTIONS)
    assert "SECURITY DEFINER" in source
    assert "SET search_path=pg_catalog,pg_temp" in source
    assert "dynamic SQL" not in source


def test_0043本人退出权威只允许ReviewWriter及严格成功后像() -> None:
    source = _migration_source()
    body = source.split(
        "CREATE FUNCTION public.slice2_therapist_self_exit_currentness_v1(", 1
    )[1].split("END $$", 1)[0]
    assert "session_user <> '{reviewer}'" in body
    assert "length(p_idempotency_key) BETWEEN 1 AND 128" in body
    assert "p_request_digest !~ '^[0-9a-f]{{64}}$'" in body
    assert "profile_status IN ('APPROVED_ACTIVE','SUSPENDED')" in body
    assert "profile_status='EXITED'" in body
    assert "operation='EXITED'" in body
    assert "idempotency_row.request_digest=p_request_digest" in body
    assert "status_row.expected_profile_version+1=profile_version" in body
    assert "status_row.actor_user_id=p_actor_user_id" in body
    assert "audit_row.action='THERAPIST_EXITED'" in body
    assert "audit_row.postimage_digest=idempotency_row.postimage_digest" in body
    assert "outbox_row.event_type='THERAPIST_EXITED'" in body
    assert "outbox_row.aggregate_id=profile_id" in body
    initial_branch = body.split(
        "IF profile_status IN ('APPROVED_ACTIVE','SUSPENDED') THEN", 1
    )[0]
    replay_branch = body.split("IF profile_status='EXITED'", 1)[1]
    assert "profile_cases<>0" not in initial_branch
    assert "profile_cases<>0" in replay_branch
    service_source = inspect.getsource(therapist_service)
    assert "async def require_therapist_self_exit(" in service_source
    assert "def status_request_digest(" in service_source


def test_0043首次锁模式避免Writer共享锁升级() -> None:
    source = _migration_source()
    assert "SLICE2_THERAPIST_ACTIVATION_CURRENTNESS" in source
    assert "FOR UPDATE OF invitation_row" in source
    assert "SLICE2_THERAPIST_ONBOARDING_CURRENTNESS" in source
    assert "IF session_user IN ('{onboarding}','{reviewer}') THEN" in source
    assert "FOR UPDATE OF profile_row" in source
    assert "FOR SHARE OF profile_row" in source
    assert "SLICE2_THERAPIST_REVIEW_TARGET_CURRENTNESS" in source
    assert "SLICE2_THERAPIST_REVIEW_ITEM_CURRENTNESS" in source
    assert source.count("FOR UPDATE OF profile_row") >= 3
    assert "FOR UPDATE OF review_item_row" in source


def test_0043函数体角色白名单与EXECUTE授权精确一致() -> None:
    source = _migration_source()
    institution_body = source.split(
        "CREATE FUNCTION public.slice2_institution_business_currentness_v1(", 1
    )[1].split("END $$", 1)[0]
    therapist_body = source.split(
        "CREATE FUNCTION public.slice2_therapist_onboarding_currentness_v1(", 1
    )[1].split("END $$", 1)[0]

    assert "session_user NOT IN ('{onboarding}','{reader}')" in institution_body
    assert "'{reviewer}'" not in institution_body
    assert (
        "session_user NOT IN ('{onboarding}','{reviewer}','{reader}')"
        in therapist_body
    )
    assert "session_user IN ('{onboarding}','{reviewer}')" in therapist_body


def test_0043机构批准绑定只使用MVCC以避开反向锁序() -> None:
    source = _migration_source()
    assert "FROM public.institution_application" in source
    assert "FOR SHARE OF institution_application" not in source
    assert "FOR UPDATE OF institution_application" not in source
    assert "WHEN TOO_MANY_ROWS THEN" in source
    assert "USING ERRCODE='21000'" in source


def test_0043只读取User真实存在的当前性列() -> None:
    source = _migration_source()
    assert "actor_user.role='super_admin'" in source
    assert "actor_user.tenant_id IS NULL" in source
    assert "actor_user.org_id" not in source


def test_生产Currentness不再直读基础表或复用Totp登录函数() -> None:
    source = "\n".join(
        inspect.getsource(value)
        for value in (
            require_institution_actor,
            require_therapist,
            require_reviewer,
            therapist_service.require_therapist_self_exit,
            _therapist_current,
        )
    )
    assert 'FROM public."user"' not in source
    assert "FROM public.tenant" not in source
    assert "FROM public.therapist_profile" not in source
    assert "therapist_totp_for_login_v1" not in source
    for name in (
        "slice2_institution_business_currentness_v1",
        "slice2_therapist_onboarding_currentness_v1",
        "slice2_therapist_reviewer_currentness_v1",
        "slice3_therapist_service_currentness_v1",
        "slice2_therapist_self_exit_currentness_v1",
    ):
        assert name in source


def test_未知异常不得伪装成依赖503() -> None:
    translated = translate_error(RuntimeError("internal invariant"))
    assert translated.status_code == 500
    assert translated.detail == "INTERNAL_ERROR"
