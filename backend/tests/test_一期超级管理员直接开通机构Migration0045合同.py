from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "app/migrations/versions/20260913_0045_超级管理员直接开通机构.py"
SERVICE = ROOT / "app/modules/direct_institution_onboarding/service.py"
FUNCTIONS = (
    "institution_tenant_origin_current_v1",
    "institution_controlled_origin_bind_v1",
    "direct_institution_create_v1",
    "direct_activation_regenerate_v1",
    "direct_institution_revoke_v1",
    "direct_institution_activate_v1",
    "direct_activation_authority_v1",
    "admin_handoff_activation_authority_v1",
    "direct_compliance_save_v1",
    "direct_compliance_submit_v1",
    "direct_compliance_decide_v1",
    "institution_admin_handoff_create_v1",
    "institution_admin_handoff_regenerate_v1",
    "institution_admin_handoff_revoke_v1",
    "admin_handoff_activation_v1",
    "direct_review_replay_v1",
    "direct_review_commit_confirm_v1",
    "direct_activation_replay_v1",
    "direct_activation_commit_confirm_v1",
    "direct_compliance_save_replay_v1",
    "direct_compliance_save_commit_confirm_v1",
    "direct_compliance_submit_replay_v1",
    "direct_compliance_submit_commit_confirm_v1",
    "admin_handoff_activation_replay_v1",
    "admin_handoff_activation_commit_confirm_v1",
    "controlled_org_admin_phone_reserve_v1",
    "controlled_org_admin_phone_bind_v1",
    "controlled_org_admin_phone_release_v1",
    "therapist_account_phone_reserve_v1",
    "therapist_account_phone_bind_v1",
    "therapist_account_phone_release_v1",
    "auth_register_member_v2",
    "direct_create_step_up_begin_v1",
    "direct_create_step_up_failure_v1",
    "direct_regenerate_step_up_begin_v1",
    "direct_regenerate_step_up_failure_v1",
    "direct_revoke_step_up_begin_v1",
    "direct_revoke_step_up_failure_v1",
    "direct_compliance_decide_step_up_begin_v1",
    "direct_compliance_decide_step_up_failure_v1",
    "admin_handoff_create_step_up_begin_v1",
    "admin_handoff_create_step_up_failure_v1",
    "admin_handoff_regenerate_step_up_begin_v1",
    "admin_handoff_regenerate_step_up_failure_v1",
    "admin_handoff_revoke_step_up_begin_v1",
    "admin_handoff_revoke_step_up_failure_v1",
    "step_up_failure_commit_confirm_v1",
    "direct_institution_read_v1",
    "direct_compliance_current_v1",
    "direct_org_admin_login_v1",
    "direct_outbox_claim_v1",
    "direct_outbox_consume_v1",
    "direct_outbox_reopen_v1",
    "direct_recovery_claim_v1",
)


def _source() -> str:
    assert MIGRATION.is_file(), "Expected RED: Migration 0045尚未创建"
    return MIGRATION.read_text(encoding="utf-8")


def test_0045线性继承0044且手机号唯一性只约束活跃状态() -> None:
    source = _source()
    assert 'revision = "20260913_0045"' in source
    assert 'down_revision = "20260913_0044"' in source
    assert "claim_id UUID PRIMARY KEY" in source
    assert "uq_identity_phone_claim_active_digest" in source
    assert "WHERE state IN ('PENDING','BOUND')" in source
    assert "phone_digest CHAR(64) PRIMARY KEY" not in source


def test_0045匿名激活由数据库派生User且Runtime无Sequence权限() -> None:
    source = _source()
    assert "direct_institution_activate_v1" in source
    assert "admin_handoff_activation_v1" in source
    assert "nextval(pg_get_serial_sequence('public.user','id'))" in source
    assert "'org_admin'" in source
    assert "GRANT USAGE ON SEQUENCE" not in source
    assert "p_envelope->>'new_user_id'" not in source
    assert "p_envelope - ARRAY[" in source  # exact allow-list rejects new_user_id
    assert "p_envelope->>'phone'" in source


def test_0045匿名激活AAD使用受限强类型Authority且不开放基础表() -> None:
    source = _source()
    body = source.split(
        "CREATE FUNCTION public.direct_activation_authority_v1(", 1
    )[1].split("END $$;", 1)[0]
    compact = re.sub(r"\s+", "", body)
    assert (
        "p_onboarding_id UUID,p_credential_id UUID,p_credential_digests JSONB"
        .replace(" ", "") in compact
    )
    assert (
        "RETURNS TABLE(tenant_public_id UUID,onboarding_id UUID,credential_id UUID,"
        "source_kind VARCHAR,root_version BIGINT,phone_digest_key_id VARCHAR,"
        "credential_digest_key_id VARCHAR)"
        .replace(" ", "") in compact
    )
    assert "session_user<>'{onboarding}'" in body
    assert "root.status='PENDING_ACTIVATION'" in body
    assert "credential.status='ISSUED'" in body
    assert "credential.expires_at>clock_timestamp()" in body
    assert "claim.state='PENDING'" in body
    assert "tenant_row.status='pending'" in body
    assert "'DIRECT_ACTIVATION'::VARCHAR" in body
    for forbidden in (
        "phone_ciphertext",
        "secret_ciphertext",
        "password_hash",
        "response_payload",
    ):
        assert forbidden not in body
    assert "direct_keyed_digest_candidate_v1(" in body
    signature = "direct_activation_authority_v1(UUID,UUID,JSONB)"
    assert f'("{signature}", (onboarding,))' in source
    assert "p_envelope->>'new_phone'" in source


def test_0045直开管理员TOTP绑定唯一凭证来源且登录读取受限() -> None:
    source = _source()
    table = source.split(
        "CREATE TABLE public.direct_institution_admin_account (", 1
    )[1].split(");", 1)[0]
    assert "activation_credential_id UUID NULL" in table
    assert "handoff_credential_id UUID NULL" in table
    assert "activation_credential_id IS NOT NULL AND handoff_credential_id IS NULL" in table
    assert "activation_credential_id IS NULL AND handoff_credential_id IS NOT NULL" in table
    assert "fk_direct_admin_account_activation_credential" in source
    assert "fk_direct_admin_account_handoff_credential" in source
    assert "uq_direct_admin_account_activation_credential" in source
    assert "uq_direct_admin_account_handoff_credential" in source

    login = source.split("CREATE FUNCTION public.direct_org_admin_login_v1(", 1)[1].split(
        "END $$;", 1
    )[0]
    assert "session_user<>'{application}'" in login
    assert "institution_tenant_origin" in login
    assert "DIRECT_PROVISIONING" in login
    assert "activation_credential_id" in login
    assert "handoff_credential_id" in login
    assert "source_kind" in login
    assert '("direct_org_admin_login_v1(BIGINT)", (application,))' in source


def test_0045降级先删除管理员账户再删除其凭证父表() -> None:
    source = _source()
    downgrade = source.split("def downgrade() -> None:", 1)[1]
    drop_tables = downgrade.split("for table in (", 1)[1].split("):", 1)[0]
    assert drop_tables.index('"direct_institution_admin_account"') < drop_tables.index(
        '"institution_admin_handoff_credential"'
    )
    assert drop_tables.index('"direct_institution_admin_account"') < drop_tables.index(
        '"direct_institution_activation_credential"'
    )


def test_0045当前机构管理员合规读取不接受客户端Tenant选址() -> None:
    source = _source()
    body = source.split("CREATE FUNCTION public.direct_compliance_current_v1(", 1)[1].split(
        "END $$;", 1
    )[0]
    assert "p_actor_user_id BIGINT" in source
    assert "session_user<>'{reader}'" in body
    assert "actor_row.role::TEXT<>'org_admin'" in body
    assert "actor_row.tenant_id" in body
    assert "origin_type='DIRECT_PROVISIONING'" in body
    assert "activated_user_id=actor_row.id" in body
    assert "direct_institution_compliance_revision" in body
    assert "direct_institution_license" in body
    assert "p_tenant" not in body
    assert "p_onboarding" not in body
    assert '("direct_compliance_current_v1(BIGINT)", (reader,))' in source


def test_0045直开列表在同一语句返回完整授权集合UUID上界() -> None:
    source = _source()
    body = source.split("CREATE FUNCTION public.direct_institution_read_v1(", 1)[1].split(
        "END $$;", 1
    )[0]
    assert "snapshot_ceiling UUID" in body
    assert "COALESCE(p_ceiling,(SELECT candidate.onboarding_id" in body
    assert "ORDER BY candidate.onboarding_id DESC LIMIT 1" in body
    assert "candidate.status=p_status" in body
    assert "snapshot.snapshot_ceiling" in body
    assert "has_more BOOLEAN" in body
    assert "EXISTS(SELECT 1 FROM public.direct_institution_onboarding following" in body
    assert body.index("ORDER BY candidate.onboarding_id DESC LIMIT 1") < body.index(
        "LIMIT p_limit"
    )


def test_0045合规PUT追加不可变草稿且POST只提交当前草稿() -> None:
    source = _source()
    table = source.split(
        "CREATE TABLE public.direct_institution_compliance_revision (", 1
    )[1].split(");", 1)[0]
    assert "'DRAFT'" in table
    assert "submitted_at TIMESTAMPTZ NULL" in table
    assert "created_operation_id UUID NOT NULL UNIQUE" in table
    assert "compliance_schema_version SMALLINT NOT NULL" in table
    assert "license_count SMALLINT NOT NULL" in table
    assert "license_set_digest_key_id VARCHAR(64) NOT NULL" in table

    save = source.split("CREATE FUNCTION public.direct_compliance_save_v1(", 1)[1].split(
        "END $$;", 1
    )[0]
    assert "'DRAFT'" in save
    assert "DIRECT_COMPLIANCE_SAVE" in save
    assert "DIRECT_COMPLIANCE_SUBMITTED" not in save
    assert "current_revision_id=(p_envelope->>'revision_id')::UUID" in save
    assert "status='COMPLIANCE_UNDER_REVIEW'" not in save

    submit = source.split("CREATE FUNCTION public.direct_compliance_submit_v1(", 1)[1].split(
        "END $$;", 1
    )[0]
    assert "root.current_revision_id" in submit
    assert "revision_row.status<>'DRAFT'" in submit
    assert "compliance_payload_digest" in submit
    submit_envelope = submit.split("OR (p_envelope-ARRAY[", 1)[0]
    assert "'compliance_payload_ciphertext'" not in submit_envelope
    assert "'compliance_payload_key_id'" not in submit_envelope
    assert "unified_social_credit_code_digest" in submit
    assert "UPDATE public.direct_institution_compliance_revision" in submit
    assert "INSERT INTO public.direct_institution_compliance_revision" not in submit
    assert "DIRECT_COMPLIANCE_SUBMITTED" in submit
    assert '("direct_compliance_save_v1(JSONB)", (onboarding,))' in source


def test_0045合规草稿保存使用独立幂等域且提交确认不要求Outbox() -> None:
    source = _source()
    replay = source.split(
        "CREATE FUNCTION public.direct_compliance_save_replay_v1(", 1
    )[1].split("END $$;", 1)[0]
    confirm = source.split(
        "CREATE FUNCTION public.direct_compliance_save_commit_confirm_v1(", 1
    )[1].split("END $$;", 1)[0]
    assert "operation='COMPLIANCE_SAVE'" in replay
    assert "IDEMPOTENCY_CONFLICT" in replay
    assert "direct_onboarding_receipt" in confirm
    assert "direct_onboarding_audit" in confirm
    assert "direct_onboarding_outbox" not in confirm
    assert "operation='COMPLIANCE_SAVE'" in confirm
    assert "p_envelope->>'revision_status'<>'DRAFT'" in confirm
    assert "created_operation_id=(p_envelope->>'operation_id')::UUID" in confirm
    assert "'onboarding_id'" in confirm
    assert "'target_id'" not in confirm
    assert "onboarding_id=(p_envelope->>'onboarding_id')::UUID" in confirm
    assert "root_row.current_revision_id=(p_envelope->>'revision_id')::UUID" not in confirm
    assert "root_row.version>=(p_envelope->>'target_version')::BIGINT" in confirm
    assert "license_set_digest" in confirm
    assert "jsonb_array_elements(p_envelope->'licenses')" in confirm
    assert "COMMITTED" in confirm
    assert "NOT_COMMITTED" in confirm
    assert "UNKNOWN" in confirm
    assert '("direct_compliance_save_replay_v1(BIGINT,VARCHAR,VARCHAR,CHAR,JSONB)", (onboarding,))' in source
    assert '("direct_compliance_save_commit_confirm_v1(JSONB)", (onboarding,))' in source


def test_0045受限当前草稿读取只追加提交所需历史摘要元数据() -> None:
    source = _source()
    body = source.split(
        "CREATE FUNCTION public.direct_compliance_current_v1(", 1
    )[1].split("END $$;", 1)[0]
    for field in (
        "compliance_payload_digest_key_id",
        "compliance_payload_digest",
        "unified_social_credit_code_digest_key_id",
        "unified_social_credit_code_digest",
        "license_set_digest_key_id",
        "license_set_digest",
        "license_no_digest_key_id",
        "license_no_digest",
    ):
        assert field in body
    assert "legal_representative_name" not in body
    assert "contact_phone" not in body
    assert "address" not in body


def test_0045会员注册claim由调用方提供UUIDv7且数据库拒绝其他版本() -> None:
    source = _source()
    assert (
        "auth_register_member_v2(p_claim_id UUID,p_phone VARCHAR,"
        "p_password_hash VARCHAR,p_phone_digest CHAR,p_key_id VARCHAR,p_phone_digest_candidates JSONB)" in source
    )
    assert "get_byte(uuid_send(p_claim_id),6) >> 4" in source
    assert "gen_random_uuid()" not in source
    upgrade = source.split("def upgrade() -> None:", 1)[1].split(
        "def downgrade() -> None:", 1
    )[0]
    downgrade = source.split("def downgrade() -> None:", 1)[1]
    legacy_signature = "auth_register_member_v1(VARCHAR,VARCHAR)"
    assert f"REVOKE EXECUTE ON FUNCTION public.{legacy_signature}" in upgrade
    assert f"GRANT EXECUTE ON FUNCTION public.{legacy_signature}" in downgrade


def test_0045五类手机号占用入口统一使用有界候选与全局确定性锁() -> None:
    source = _source()
    internal = source.split(
        "CREATE FUNCTION public.identity_phone_claim_internal_v1(", 1
    )[1].split("END $$;", 1)[0]
    assert "jsonb_array_length(p_envelope->'phone_digest_candidates') NOT BETWEEN 1 AND 16" in internal
    assert "jsonb_typeof(p_envelope) IS DISTINCT FROM 'object'" in internal
    assert "(jsonb_typeof(candidate->'key_id')='string') IS NOT TRUE" in internal
    assert "(jsonb_typeof(candidate->'digest')='string') IS NOT TRUE" in internal
    assert "current_pair_found IS NOT TRUE" in internal
    assert "(active_claim.phone_digest_key_id=ANY(candidate_keys)) IS NOT TRUE" in internal
    validation = internal.index("FOR candidate IN SELECT value FROM jsonb_array_elements")
    global_lock = internal.index("identity_phone_claim/reserve/v1")
    assert validation < global_lock
    assert "identity_phone_claim/reserve/v1" in internal
    assert "PHONE_CLAIM_KEY_COVERAGE_INCOMPLETE" in internal
    assert global_lock < internal.rindex(
        "FROM jsonb_array_elements(p_envelope->'phone_digest_candidates')"
    )
    for function_name in (
        "direct_institution_create_v1",
        "institution_admin_handoff_create_v1",
        "auth_register_member_v2",
        "controlled_org_admin_phone_reserve_v1",
        "therapist_account_phone_reserve_v1",
    ):
        body = source.split(f"CREATE FUNCTION public.{function_name}(", 1)[1].split(
            "END $$;", 1
        )[0]
        assert "phone_digest_candidates" in body
        assert "identity_phone_claim_internal_v1" in body


def test_0045会员注册函数返回既有受控注册最小字段合同() -> None:
    source = _source()
    body = source.split("CREATE FUNCTION public.auth_register_member_v2", 1)[1].split(
        "CREATE FUNCTION public.direct_review_replay_v1", 1
    )[0]
    assert "RETURNS TABLE (" in body
    for field in (
        "id BIGINT",
        "phone VARCHAR",
        "role public.user_role",
        "status public.user_status",
        "verify_status VARCHAR",
        "tenant_id BIGINT",
        "created_at TIMESTAMPTZ",
    ):
        assert field in body


def test_0045直开机构只接受有效区县并由完整祖先链派生省市() -> None:
    source = _source()
    assert "county_row.org_type<>'county'" in source
    assert "city_row.org_type<>'city'" in source
    assert "province_row.org_type<>'province'" in source
    assert "headquarter_row.org_type<>'headquarter'" in source
    assert "county_row.status<>'active'" in source
    assert "city_row.status<>'active'" in source
    assert "province_row.status<>'active'" in source
    assert "headquarter_row.status<>'active'" in source
    assert "province_row.org_name" in source
    assert "city_row.org_name" in source
    assert "org_path" not in source


def test_0045平台StepUp在业务事务内按数据库窗口原子消费() -> None:
    source = _source()
    assert "accepted_totp_step" in source
    assert "extract(epoch FROM clock_timestamp())/30" in source
    assert "last_accepted_time_step" in source
    assert "accepted_step BETWEEN current_step-1 AND current_step+1" in source
    assert "platform_admin_step_up_operation" not in source
    step_up_bodies = "\n".join(
        source.split(f"CREATE FUNCTION public.{name}(", 1)[1].split("END $$;", 1)[0]
        for name in FUNCTIONS
        if "step_up" in name
    )
    assert "INTERVAL '5 minutes'" not in step_up_bodies


def test_0045九个TOTP步信封在锁与写入前只接受BIGINT范围内的整数JSON数() -> None:
    source = _source()
    functions = {
        "direct_institution_create_v1": "DIRECT_CREATE_ENVELOPE_INVALID",
        "direct_activation_regenerate_v1": "DIRECT_REGENERATE_ENVELOPE_INVALID",
        "direct_institution_revoke_v1": "DIRECT_REVOKE_ENVELOPE_INVALID",
        "institution_admin_handoff_create_v1": "HANDOFF_CREATE_ENVELOPE_INVALID",
        "institution_admin_handoff_regenerate_v1": "HANDOFF_REGENERATE_ENVELOPE_INVALID",
        "institution_admin_handoff_revoke_v1": "HANDOFF_REVOKE_ENVELOPE_INVALID",
        "direct_compliance_decide_v1": "DIRECT_COMPLIANCE_DECIDE_INVALID",
        "direct_institution_activate_v1": "DIRECT_ACTIVATION_ENVELOPE_INVALID",
        "admin_handoff_activation_v1": "ADMIN_HANDOFF_ENVELOPE_INVALID",
    }
    for name, error_code in functions.items():
        body = source.split(f"CREATE FUNCTION public.{name}(", 1)[1].split(
            "END $$;", 1
        )[0]
        type_guard = "jsonb_typeof(p_envelope->'accepted_totp_step')<>'number'"
        integer_guard = (
            "(p_envelope->>'accepted_totp_step')!~'^(0|[1-9][0-9]*)$'"
        )
        range_guard = (
            "(p_envelope->>'accepted_totp_step')::NUMERIC>9223372036854775807"
        )
        assert type_guard in body
        assert integer_guard in body
        assert range_guard in body
        assert f"RAISE EXCEPTION '{error_code}' USING ERRCODE='22023'" in body
        guard_position = body.index(type_guard)
        assert guard_position < body.index("pg_advisory_xact_lock")
        assert guard_position < body.index("accepted_totp_step')::BIGINT")
        for statement in ("INSERT INTO", "UPDATE public"):
            if statement in body:
                assert guard_position < body.index(statement)


def test_0045合规PII使用封闭载荷AEAD与独立版本化摘要() -> None:
    source = _source()
    table = source.split(
        "CREATE TABLE public.direct_institution_compliance_revision (", 1
    )[1].split(");", 1)[0]
    assert "compliance_payload_ciphertext BYTEA NOT NULL" in table
    assert "compliance_payload_key_id VARCHAR(64) NOT NULL" in table
    assert "compliance_payload_digest_key_id VARCHAR(64) NOT NULL" in table
    assert "compliance_payload_digest CHAR(64) NOT NULL" in table
    assert "unified_social_credit_code_digest_key_id VARCHAR(64) NOT NULL" in table
    assert "unified_social_credit_code_digest CHAR(64) NOT NULL" in table
    assert "\n  payload_ciphertext BYTEA NOT NULL" not in table
    assert "\n  payload_key_id VARCHAR(64) NOT NULL" not in table
    assert "\n  payload_digest CHAR(64) NOT NULL" not in table


def test_0045合规PII的AAD由同一权威Revision范围固定构造() -> None:
    source = SERVICE.read_text(encoding="utf-8")
    migration = _source()
    assert "phase1/direct-institution/compliance-pii/v1" in source
    assert "tenant_public_id" in source
    assert "onboarding_id" in source
    assert "revision_id" in source
    assert "revision_no" in source
    assert "compliance_payload_key_id" in migration
    assert "compliance_payload_digest_key_id" in migration


def test_0045合规保存只保存密文摘要并绑定CLEAN私有文件() -> None:
    source = _source()
    body = source.split("CREATE FUNCTION public.direct_compliance_save_v1(", 1)[1].split(
        "END $$;", 1
    )[0]
    envelope = body.split("OR (p_envelope-ARRAY[", 1)[0]
    assert "'event_id'" not in envelope
    assert "direct_onboarding_outbox" not in body
    assert "compliance_payload_ciphertext" in body
    assert "compliance_payload_key_id" in body
    assert "unified_social_credit_code_digest_key_id" in body
    assert "unified_social_credit_code_digest" in body
    assert "unified_social_credit_code'" not in body
    assert "jsonb_array_length(p_envelope->'licenses') BETWEEN 1 AND 10" in body
    assert "AS license_row(license_value)" in body
    assert "ORDER BY (license_value->>'private_file_id')::UUID" in body
    assert "SELECT item FROM jsonb_array_elements" not in body
    assert "file_row.status<>'CLEAN'" in body
    assert "file_row.owner_user_id<>actor_row.id" in body


def test_0045合规决定权威规范化长密文且响应投影读取Root字段() -> None:
    source = _source()
    authority = source.split(
        "CREATE FUNCTION public.direct_compliance_decide_step_up_begin_v1(", 1
    )[1].split("END $$;", 1)[0]
    decision = source.split("CREATE FUNCTION public.direct_compliance_decide_v1(", 1)[
        1
    ].split("END $$;", 1)[0]
    assert (
        "replace(encode(revision_row.compliance_payload_ciphertext,'base64'),E'\\n','')"
        in authority
    )
    assert "revision_row.institution_name" not in decision
    assert "revision_row.institution_type" not in decision
    assert "revision_row.administrative_region_id" not in decision
    assert "revision_row.institution_code" not in decision
    assert "'institution_name',root.institution_name" in decision


def test_0045所有平台TOTP受限前像均输出无换行Base64() -> None:
    migration = _source()
    raw_expression = "encode(profile_row.secret_ciphertext,'base64')"
    normalized_expression = (
        "replace(encode(profile_row.secret_ciphertext,'base64'),E'\\n','')"
    )

    assert migration.count(normalized_expression) == 6
    assert migration.count("'profile_secret_ciphertext'," + raw_expression) == 0


def test_0045合规批准在同一事务写现有Tenant信用代码且不复制唯一真相() -> None:
    source = _source()
    body = source.split("CREATE FUNCTION public.direct_compliance_decide_v1(", 1)[1].split(
        "END $$;", 1
    )[0]
    assert "p_envelope->>'unified_social_credit_code'" in body
    assert "tenant_row.credit_code IS NOT NULL" in body
    assert "tenant_row.credit_code<>p_envelope->>'unified_social_credit_code'" in body
    assert "UPDATE public.tenant SET credit_code=p_envelope->>'unified_social_credit_code'" in body
    assert body.index("UPDATE public.tenant SET credit_code") < body.index(
        "INSERT INTO public.direct_onboarding_audit"
    )
    assert "credit_code_claim" not in source
    receipt_body = source.split("CREATE TABLE public.direct_onboarding_receipt (", 1)[1].split(
        ");", 1
    )[0]
    assert "unified_social_credit_code" not in receipt_body


def test_0045全部PII密文均保存独立AEAD密钥版本() -> None:
    source = _source()
    assert "admin_phone_key_id VARCHAR(64) NOT NULL" in source
    assert "new_phone_key_id VARCHAR(64) NOT NULL" in source
    assert "license_no_key_id VARCHAR(64) NULL" in source
    assert "license_no_digest_key_id VARCHAR(64) NULL" in source
    assert "admin_phone_key_id,phone_digest_key_id" in source
    assert "new_phone_key_id,new_phone_digest_key_id" in source


def test_0045许可证号密文与摘要版本严格执行全空或全非空() -> None:
    source = _source()
    table = source.split("CREATE TABLE public.direct_institution_license (", 1)[1].split(
        ");", 1
    )[0]
    for field in (
        "license_no_ciphertext",
        "license_no_key_id",
        "license_no_digest_key_id",
        "license_no_digest",
    ):
        assert f"{field} IS NULL" in table
        assert f"{field} IS NOT NULL" in table


def test_0045Receipt只保存严格非敏感JSONB结果() -> None:
    source = _source()
    table = source.split("CREATE TABLE public.direct_onboarding_receipt (", 1)[1].split(
        ");", 1
    )[0]
    assert "response_payload JSONB NULL" in table
    assert "response_ciphertext" not in table
    assert "response_key_id" not in table
    assert "ck_direct_onboarding_receipt_response" in table
    assert "digest_key_id VARCHAR(64) NOT NULL" in table
    assert "ck_direct_onboarding_receipt_digest_key" in table
    assert "admin_phone" not in table
    assert "activation_code" not in table


def test_0045Receipt重放精确使用落库摘要Key且缺失旧Key安全失败() -> None:
    source = _source()
    helper = source.split(
        "CREATE FUNCTION public.direct_keyed_digest_candidate_v1(", 1
    )[1].split("END $$;", 1)[0]
    assert "DIGEST_KEY_UNAVAILABLE" in helper
    assert "ERRCODE='55000'" in helper
    assert "(jsonb_typeof(p_candidates)='array') IS NOT TRUE" in helper
    assert "(jsonb_typeof(candidate->'key_id')='string') IS NOT TRUE" in helper
    assert "(jsonb_typeof(candidate->'digest')='string') IS NOT TRUE" in helper
    assert "IS DISTINCT FROM ARRAY['digest','key_id']" in helper
    assert "(candidate->>'key_id'=p_key_id) IS TRUE" in helper
    for function_name in (
        "direct_review_replay_v1",
        "direct_activation_replay_v1",
        "admin_handoff_activation_replay_v1",
        "direct_compliance_save_replay_v1",
        "direct_compliance_submit_replay_v1",
    ):
        body = source.split(f"CREATE FUNCTION public.{function_name}(", 1)[1].split(
            "END $$;", 1
        )[0]
        assert "receipt_row.digest_key_id" in body
        assert "direct_keyed_digest_candidate_v1" in body


def test_0045Receipt允许激活公开DTO但继续拒绝凭据与PII字段() -> None:
    source = _source()
    table = source.split("CREATE TABLE public.direct_onboarding_receipt (", 1)[1].split(
        ");", 1
    )[0]
    for field in (
        "institution_code",
        "institution_name",
        "institution_type",
        "administrative_region_id",
        "compliance_due_at",
    ):
        assert f"'{field}'" in table
    for forbidden in (
        "admin_phone",
        "activation_code",
        "password_hash",
        "totp_ciphertext",
        "totp_secret",
    ):
        assert f"'{forbidden}'" not in table


def test_0045不实现产品待决的逾期状态自动化() -> None:
    source = _source()
    assert "compliance_due_at" in source
    assert "((now_value AT TIME ZONE 'Asia/Shanghai')::DATE+30)::TIMESTAMP AT TIME ZONE 'Asia/Shanghai'" in source
    assert "compliance_due_at=now_value+INTERVAL '30 days'" not in source
    assert "direct_expiry_claim" not in source


def test_canonical机构权威允许任一标识且双标识必须同时匹配() -> None:
    source = _source()
    body = source.split(
        "CREATE FUNCTION public.institution_tenant_origin_current_v1(", 1
    )[1].split("CREATE FUNCTION public.institution_controlled_origin_bind_v1", 1)[0]
    assert "p_tenant_id IS NULL AND p_tenant_public_id IS NULL" in body
    assert "(p_tenant_id IS NULL OR origin.tenant_id=p_tenant_id)" in body
    assert "(p_tenant_public_id IS NULL OR origin.tenant_public_id=p_tenant_public_id)" in body
    assert "DIRECT_COMPLIANCE_OVERDUE" not in source
    task_source = (ROOT / "app/tasks/institution_onboarding_tasks.py").read_text(
        encoding="utf-8"
    )
    celery_source = (ROOT / "app/tasks/celery_app.py").read_text(encoding="utf-8")
    combined = task_source + celery_source
    assert "direct_compliance_overdue" not in combined
    assert "direct_expiry" not in combined


def test_0045受限对象均撤销PUBLIC且没有基础表Runtime授权() -> None:
    source = _source()
    assert "REVOKE ALL ON FUNCTION" in source
    assert "FROM PUBLIC" in source
    assert "GRANT SELECT ON public.identity_phone_claim" not in source
    assert "GRANT INSERT ON public.identity_phone_claim" not in source
    assert "GRANT UPDATE ON public.identity_phone_claim" not in source


def test_0045机构来源权威仅接纳已存在外层受限入口的真实调用角色() -> None:
    source = _source()
    body = source.split(
        "CREATE FUNCTION public.institution_tenant_origin_current_v1(", 1
    )[1].split("CREATE FUNCTION public.institution_controlled_origin_bind_v1", 1)[0]
    for role_key in (
        "health_record_writer",
        "assessment_readiness_writer",
        "slice4_identity_authority",
        "slice5_worker",
        "slice7_milestone_writer",
        "slice7_case_writer",
        "slice7_transfer_writer",
        "slice7_export_worker",
        "slice7_family_reader",
        "slice7_oversight_reader",
    ):
        assert f'roles["{role_key}"]' in source
    for variable in (
        "health_record_writer",
        "assessment_readiness_writer",
        "slice4_identity_authority",
        "slice5_worker",
        "slice7_milestone_writer",
        "slice7_case_writer",
        "slice7_transfer_writer",
        "slice7_export_worker",
        "slice7_family_reader",
        "slice7_oversight_reader",
    ):
        assert "{" + variable + "}" in body
    assert "GRANT EXECUTE ON FUNCTION public.institution_tenant_origin_current_v1" not in source


def test_0045精确兼容重定义Slice2至Slice7的封闭下游对象() -> None:
    source = _source()
    required_objects = (
        "institution_readiness_source_v1",
        "institution_readiness_guard_v1",
        "therapist_totp_for_login_v1",
        "resolve_tenant_admin_delivery_targets_v1",
        "slice3_institution_enrollment_read_v1",
        "slice3_family_enrollment_read_v1",
        "slice3_therapist_assignment_read_v1",
        "slice3_service_case_read_v1",
        "fk_health_profile_revision_tenant",
        "slice4_identity_summary_source_v1",
        "slice4_identity_summary_current_v1",
        "slice4_assessment_assembly_write_v1",
        "slice5_assessment_input_v1",
        "slice7_authority_v1",
        "slice7_mutation_v1",
        "slice2_institution_identity_authority_v1",
        "slice3_institution_currentness_authority_v1",
        "a3_private_file_referenced_v1",
        "batch_b_private_file_access_snapshot_v1",
        "slice2_institution_business_currentness_v1",
        "slice2_therapist_activation_currentness_v1",
        "slice2_therapist_onboarding_currentness_v1",
        "slice2_therapist_self_exit_currentness_v1",
        "slice3_therapist_service_currentness_v1",
        "slice2_therapist_review_target_currentness_v1",
        "slice2_therapist_review_item_currentness_v1",
    )
    missing = [name for name in required_objects if name not in source]
    assert missing == []
    assert "slice7_proxy_plan_decision_guard_v1" not in source


def test_0045操作函数目录封闭且不使用通用action分派() -> None:
    source = _source()
    for name in FUNCTIONS:
        assert f"CREATE FUNCTION public.{name}(" in source
    assert "identity_phone_claim_internal_v1" in source
    assert "direct_expiry_claim" not in source


def test_0045交接凭据重发与撤销保持同一领取权威和手机号声明边界() -> None:
    source = _source()
    regenerate = source.split(
        "CREATE FUNCTION public.institution_admin_handoff_regenerate_v1(", 1
    )[1].split("END $$;", 1)[0]
    revoke = source.split(
        "CREATE FUNCTION public.institution_admin_handoff_revoke_v1(", 1
    )[1].split("END $$;", 1)[0]

    for body in (regenerate, revoke):
        assert "platform_admin_security_profile" in body
        assert "institution_admin_handoff" in body
        assert "identity_phone_claim" in body
        assert "accepted_totp_step" in body
        assert "last_accepted_time_step" in body

    assert "old_credential_id" in regenerate
    assert "new_credential_id" in regenerate
    assert "new_credential_digest_key_id" in regenerate
    assert "new_credential_digest" in regenerate
    assert "p_envelope->>'new_phone" not in regenerate
    assert "SET status='REVOKED'" in regenerate
    assert "ADMIN_HANDOFF_REGENERATED" in regenerate

    assert "active_credential_id" in revoke
    assert "new_phone_digest_key_id" in revoke
    assert "new_phone_digest" in revoke
    assert "state='RELEASED'" in revoke
    assert "ADMIN_HANDOFF_REVOKED" in revoke


def test_0045幂等重放按Runtime和动作封闭且不恢复一次性明文() -> None:
    source = _source()
    review = source.split("CREATE FUNCTION public.direct_review_replay_v1(", 1)[1].split(
        "END $$;", 1
    )[0]
    assert "session_user<>'{review}'" in review
    assert "CREATE','REGENERATE','REVOKE','COMPLIANCE_DECIDE','HANDOFF_CREATE','HANDOFF_REGENERATE','HANDOFF_REVOKE" in review
    assert "receipt_row.digest_key_id" in review
    assert "direct_keyed_digest_candidate_v1(" in review
    assert "request_digest_candidates" in review
    assert "IDEMPOTENCY_CONFLICT" in review
    assert "credential_delivery_state','ALREADY_ISSUED'" in review

    activation = source.split("CREATE FUNCTION public.direct_activation_replay_v1(", 1)[1].split(
        "END $$;", 1
    )[0]
    handoff = source.split("CREATE FUNCTION public.admin_handoff_activation_replay_v1(", 1)[1].split(
        "END $$;", 1
    )[0]
    assert "CREDENTIAL_HOLDER:ACTIVATE:" in activation
    assert "CREDENTIAL_HOLDER:HANDOFF_ACTIVATE:" in handoff
    assert "actor_user_id" not in activation
    assert "actor_user_id" not in handoff


def test_0045激活类Receipt持久化并回放同一非敏感响应() -> None:
    source = _source()
    pairs = (
        (
            "direct_institution_activate_v1",
            "direct_activation_replay_v1",
            "ACTIVATE",
        ),
        (
            "admin_handoff_activation_v1",
            "admin_handoff_activation_replay_v1",
            "HANDOFF_ACTIVATE",
        ),
    )
    for mutation_name, replay_name, operation in pairs:
        mutation = source.split(
            f"CREATE FUNCTION public.{mutation_name}(", 1
        )[1].split("END $$;", 1)[0]
        replay = source.split(
            f"CREATE FUNCTION public.{replay_name}(", 1
        )[1].split("END $$;", 1)[0]
        assert "response_value JSONB" in mutation
        assert "postimage_digest,response_payload" in mutation
        assert "p_envelope->>'expected_postimage_digest',response_value" in mutation
        assert "RETURN response_value" in mutation
        assert f"operation='{operation}'" in replay
        assert "receipt_row.response_payload IS NULL" in replay
        assert "RETURN receipt_row.response_payload" in replay
        for forbidden in (
            "activation_code",
            "password_hash",
            "totp_ciphertext",
            "totp_secret_digest",
        ):
            assert f"'{forbidden}'" not in mutation.split(
                "response_value:=jsonb_build_object", 1
            )[1].split(";", 1)[0]


def test_0045重生成与撤销Authority返回唯一活动凭据且Mutation锁后复核() -> None:
    source = _source()
    begin = source.split(
        "CREATE FUNCTION public.direct_regenerate_step_up_begin_v1", 1
    )[1].split("END $$;", 1)[0]
    regenerate = source.split(
        "CREATE FUNCTION public.direct_activation_regenerate_v1", 1
    )[1].split("END $$;", 1)[0]
    assert "active_credential_id UUID" in begin
    assert "status='ISSUED' FOR UPDATE" in begin
    assert "AND credential_id<>active_credential_id" in begin
    assert "'active_credential_id',active_credential_id" in begin
    assert "old_credential_id" in regenerate
    assert "credential_id=(p_envelope->>'old_credential_id')::UUID" in regenerate
    assert "old_credential.status<>'ISSUED'" in regenerate

    revoke_begin = source.split(
        "CREATE FUNCTION public.direct_revoke_step_up_begin_v1", 1
    )[1].split("END $$;", 1)[0]
    confirmation = source.split(
        "CREATE FUNCTION public.direct_review_commit_confirm_v1", 1
    )[1].split("END $$;", 1)[0]
    assert "active_credential_id UUID" in revoke_begin
    assert "status='ISSUED' FOR UPDATE" in revoke_begin
    assert "AND credential_id<>active_credential_id" in revoke_begin
    assert "'active_credential_id',active_credential_id" in revoke_begin
    assert "p_envelope->>'operation'='REVOKE'" in confirmation
    assert "c.status='REVOKED'" in confirmation
    assert "credential_digest_key_id' IS NULL" in confirmation
    assert "credential_digest' IS NULL" in confirmation


def test_0045列表UUID快照上界使用PostgreSQL原生排序且空集仍返回一行CTE() -> None:
    source = _source()
    body = source.split(
        "CREATE FUNCTION public.direct_institution_read_v1(", 1
    )[1].split("END $$;", 1)[0]
    snapshot = body.split("WITH snapshot AS (", 1)[1].split(")\n    SELECT", 1)[0]
    assert "max(candidate.onboarding_id)" not in snapshot
    assert "COALESCE(p_ceiling,(SELECT candidate.onboarding_id" in snapshot
    assert "ORDER BY candidate.onboarding_id DESC LIMIT 1" in snapshot
    assert "candidate.onboarding_id::text" not in snapshot


def test_0045凭据签发时间由数据库固化且确认不要求调用方预知() -> None:
    source = _source()
    receipt_table = source.split(
        "CREATE TABLE public.direct_onboarding_receipt", 1
    )[1].split("CREATE TABLE", 1)[0]
    assert "'credential_id','credential_issued_at','credential_expires_at'" in receipt_table

    create = source.split(
        "CREATE FUNCTION public.direct_institution_create_v1", 1
    )[1].split("END $$;", 1)[0]
    regenerate = source.split(
        "CREATE FUNCTION public.direct_activation_regenerate_v1", 1
    )[1].split("END $$;", 1)[0]
    handoff_create = source.split(
        "CREATE FUNCTION public.institution_admin_handoff_create_v1", 1
    )[1].split("END $$;", 1)[0]
    handoff_regenerate = source.split(
        "CREATE FUNCTION public.institution_admin_handoff_regenerate_v1", 1
    )[1].split("END $$;", 1)[0]
    for function in (create, regenerate, handoff_create, handoff_regenerate):
        assert "'credential_id'" in function
        assert "'credential_issued_at'" in function
        assert "'credential_expires_at'" in function
        assert "postimage_digest,response_payload" in function
    assert "expires_value:=now_value+INTERVAL '24 hours'" in handoff_create
    assert "expires_value:=now_value+INTERVAL '24 hours'" in handoff_regenerate

    confirmation = source.split(
        "CREATE FUNCTION public.direct_review_commit_confirm_v1", 1
    )[1].split("END $$;", 1)[0]
    required = confirmation.split("OR (p_envelope-ARRAY[", 1)[0]
    assert "'credential_issued_at'" not in required
    assert "'credential_expires_at'" not in required
    assert "r.response_payload->>'credential_issued_at'" in confirmation
    assert "r.response_payload->>'credential_expires_at'" in confirmation


def test_0045Review提交确认同时核验目标与三类不可变证据() -> None:
    source = _source()
    body = source.split("CREATE FUNCTION public.direct_review_commit_confirm_v1(", 1)[1].split(
        "END $$;", 1
    )[0]
    assert "direct_onboarding_receipt" in body
    assert "direct_onboarding_audit" in body
    assert "direct_onboarding_outbox" in body
    assert "direct_institution_onboarding" in body
    assert "institution_admin_handoff" in body
    assert "request_digest" in body
    assert "expected_postimage_digest" in body
    assert "COMMITTED" in body
    assert "NOT_COMMITTED" in body
    assert "UNKNOWN" in body


def test_0045_CREATE确认从Root推导内部Tenant并在同幂等锁域裁决() -> None:
    source = _source()
    body = source.split(
        "CREATE FUNCTION public.direct_review_commit_confirm_v1(", 1
    )[1].split("END $$;", 1)[0]
    required = body.split("OR (p_envelope-ARRAY[", 1)[0]
    assert "'tenant_id'" not in required
    assert "'tenant_public_id'" in body
    assert "pg_advisory_xact_lock(hashtextextended(" in body
    assert "root_row.tenant_id" in body
    assert "origin_type='DIRECT_PROVISIONING'" in body
    assert "p_envelope->>'operation'='CREATE' AND NOT target_present" in body
    assert "NOT receipt_present AND NOT audit_present AND NOT outbox_present" in body


def test_0045各Runtime提交确认不共享登录Actor并核验三类证据() -> None:
    source = _source()
    for function in (
        "direct_activation_commit_confirm_v1",
        "direct_compliance_submit_commit_confirm_v1",
        "admin_handoff_activation_commit_confirm_v1",
    ):
        body = source.split(f"CREATE FUNCTION public.{function}(", 1)[1].split(
            "END $$;", 1
        )[0]
        assert "direct_onboarding_receipt" in body
        assert "direct_onboarding_audit" in body
        assert "direct_onboarding_outbox" in body
        assert "COMMITTED" in body
        assert "NOT_COMMITTED" in body
        assert "UNKNOWN" in body
    activation = source.split(
        "CREATE FUNCTION public.direct_activation_commit_confirm_v1(", 1
    )[1].split("END $$;", 1)[0]
    handoff = source.split(
        "CREATE FUNCTION public.admin_handoff_activation_commit_confirm_v1(", 1
    )[1].split("END $$;", 1)[0]
    assert "actor_user_id" not in activation
    assert "actor_user_id" not in handoff


def test_0045匿名激活确认从Root和凭据后像派生内部标识与数据库时间() -> None:
    source = _source()
    body = source.split(
        "CREATE FUNCTION public.direct_activation_commit_confirm_v1(", 1
    )[1].split("END $$;", 1)[0]
    required = body.split("OR (p_envelope-ARRAY[", 1)[0]
    for caller_unknown in (
        "'credential_issued_at'",
        "'credential_expires_at'",
        "'tenant_id'",
        "'user_id'",
    ):
        assert caller_unknown not in required
    assert "root_row.tenant_id" in body
    assert "root_row.activated_user_id" in body
    assert "c.issued_at IS NOT NULL" in body
    assert "c.expires_at>c.issued_at" in body


def test_0045StepUp失败预算独立持久化且不产生业务副作用() -> None:
    source = _source()
    body = source.split("CREATE FUNCTION public.direct_create_step_up_failure_v1(", 1)[1].split(
        "END $$;", 1
    )[0]
    assert "p_observed_profile_version" in body
    assert "failed_attempts+1" in body
    assert "INTERVAL '15 minutes'" in body
    assert "direct_onboarding_audit" in body
    assert "direct_onboarding_receipt" not in body
    assert "direct_onboarding_outbox" not in body
    assert "p_request_digest" in body
    assert "p_phone_digest_key_id" in body
    assert "p_phone_digest" in body


def test_0045所有JSON字段拼接锁键均显式限定提取运算优先级() -> None:
    source = _source()
    assert re.search(r"p_envelope->>'[a-z_]+'\|\|", source) is None
    assert re.search(r"\|\|p_envelope->>'[a-z_]+'", source) is None
    assert "(p_envelope->>'actor_scope')||E'\\\\000'||(p_envelope->>'idempotency_key')" in source
