import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "app/migrations/versions/20260818_0022_phase1_slice3_member_proxy_consent_service_case.py"


def test_0022线性继承且不使用cascade() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assigned = {
        target.id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}
    }
    assert assigned == {
        "revision": "20260818_0022",
        "down_revision": "20260817_0021",
    }
    assert "CASCADE" not in source.upper()


def test_0022包含五身份预检和双向membership闭包() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for value in (
        "KG_MEMBER_ENROLLMENT_WRITER_ROLE",
        "KG_MEMBER_IDENTITY_REVIEW_WRITER_ROLE",
        "KG_MEMBER_CASE_WRITER_ROLE",
        "KG_MEMBER_WORKFLOW_WORKER_ROLE",
        "KG_MEMBER_ENROLLMENT_READER_ROLE",
        "pg_auth_members",
        "source_oid=ANY(:oids) OR target_oid=ANY(:oids)",
        "pg_advisory_xact_lock",
    ):
        assert value in source


def test_0022对象和降级空表门禁完整() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for table in (
        "member_service_invitation", "service_enrollment", "controlled_member_bootstrap",
        "member_identity_verification", "member_identity_revision",
        "member_identity_review_decision", "member_identity_pii_access", "proxy_grant",
        "consent_document_version", "consent_document_rendition", "consent_record",
        "primary_therapist_assignment", "service_case", "member_enrollment_idempotency",
        "member_enrollment_audit", "member_enrollment_outbox", "member_enrollment_delivery",
    ):
        assert f'"{table}"' in source
    downgrade = source[source.index("def downgrade()") :]
    assert "Slice 3 downgrade requires empty module tables" in downgrade
    assert downgrade.index("Slice 3 downgrade requires empty module tables") < downgrade.index("REVOKE")


def test_0022五个safe_view与public撤权() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for name in (
        "slice3_institution_enrollment_read_v1",
        "slice3_family_enrollment_read_v1",
        "slice3_platform_identity_review_read_v1",
        "slice3_therapist_assignment_read_v1",
        "slice3_service_case_read_v1",
    ):
        assert name in source
    assert "REVOKE ALL" in source
    assert "FROM PUBLIC" in source


def test_0022创建规划冻结的全部受限数据库接口() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    required = {
        "public.lock_slice3_identity_fingerprint_v1",
        "public.slice3_assignment_candidate_guard_v1",
        "public.slice3_readiness_guard_v1",
        "identity.claim_identity_subject_v1",
        "public.slice3_verified_adult_authority_v1",
        "public.slice3_reviewer_pii_v1",
        "public.slice3_assignment_subject_v1",
        "public.slice3_idempotency_replay_v1",
        "public.slice3_idempotency_record_v1",
        "public.slice3_outbox_recipient_targets_v1",
        "public.slice3_collection_snapshot_v1",
    }
    missing = sorted(name for name in required if f"FUNCTION {name}" not in source)
    assert missing == []


def test_F1集合快照接口固定表族与最小执行身份() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    signature = "public.slice3_collection_snapshot_v1(VARCHAR,JSONB)"
    assert source.count(signature) >= 3
    assert "RETURNS UUID[]" in source
    assert "SECURITY DEFINER SET search_path=pg_catalog,pg_temp" in source
    assert "SLICE3_COLLECTION_SNAPSHOT_FORBIDDEN" in source
    assert "SLICE3_COLLECTION_SNAPSHOT_INVALID" in source
    for family in (
        "IDENTITY_REVISION",
        "REVIEW_DECISION",
        "PII_ACCESS",
        "IDENTITY_REGISTRY",
        "CONSENT_RENDITION",
        "CONSENT_RECORD_SET",
    ):
        assert family in source
    grant_line = next(
        line for line in source.splitlines()
        if "GRANT EXECUTE ON FUNCTION public.slice3_collection_snapshot_v1" in line
    )
    assert '"{enrollment}","{review}"' in grant_line
    assert '"{case}"' not in grant_line
    assert '"{worker}"' not in grant_line
    assert '"{reader}"' not in grant_line
    assert f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC" in source
    assert "array_agg(revision_id ORDER BY revision_id)" in source
    assert "array_agg(decision_id ORDER BY decision_id)" in source
    assert "array_agg(access_id ORDER BY access_id)" in source
    assert "array_agg(claim_id ORDER BY claim_id)" in source
    assert "array_agg(rendition_id ORDER BY rendition_id)" in source
    assert "array_agg(consent_record_id ORDER BY consent_record_id)" in source
    assert "EXECUTE " not in source[source.index("CREATE FUNCTION public.slice3_collection_snapshot_v1") : source.index("CREATE VIEW public.slice3_institution_enrollment_read_v1")]


def test_0022幂等接口按三个writer冻结operation与scope() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    operations = {
        "INVITATION_CREATE", "INVITATION_RESEND", "INVITATION_REVOKE",
        "ENROLLMENT_ACCEPT", "IDENTITY_SUBMIT", "IDENTITY_RESUBMIT",
        "INSTITUTION_IDENTITY_CHECK", "PROXY_REVOKE", "CONSENT_RECORD",
        "CONSENT_WITHDRAW", "ASSIGNMENT_CREATE", "ASSIGNMENT_CANCEL",
        "IDENTITY_REVIEW_CLAIM", "IDENTITY_PII_ACCESS", "IDENTITY_REVIEW_DECIDE",
        "CONSENT_DOCUMENT_CREATE", "CONSENT_DOCUMENT_PUBLISH",
        "CONSENT_DOCUMENT_RETIRE", "ASSIGNMENT_ACCEPT", "ASSIGNMENT_DECLINE",
    }
    for operation in operations:
        # Each operation is frozen in the idempotency allowlist, its writer
        # confirmation allowlist, and the independent postimage truth table.
        assert source.count(f"'{operation}'") == 3
    assert source.count(r"value_scope LIKE 'user\\:%\\:tenant\\:%'") == 4
    assert source.count(r"value_scope LIKE 'user\\:%\\:platform'") == 2


def test_平台终审仅获得Enrollment状态后像所需列权限() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert '_grant(review,"SELECT","service_enrollment",("enrollment_id","status","version"))' in source
    assert '_grant(review,"UPDATE","service_enrollment",("status","identity_verified_at","updated_at","version"))' in source


def test_机构分配候选仅通过受限Boolean接口验证() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    signature = "public.slice3_assignment_candidate_guard_v1(UUID,BIGINT,JSONB)"
    assert source.count(signature) >= 3
    assert "RETURNS BOOLEAN" in source
    assert "SECURITY DEFINER SET search_path=pg_catalog,pg_temp" in source
    assert "session_user <> '" in source
    assert "FOR SHARE OF p" in source
    assert "qualification_valid_until" in source
    assert "AT TIME ZONE 'Asia/Shanghai'" in source
    assert "REVOKE ALL ON FUNCTION" in source
    assert "GRANT EXECUTE ON FUNCTION" in source


def test_0022冻结P1全局实名事实与十一域算法边界() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    upgrade = source[source.index("def upgrade()") : source.index("def downgrade()")]
    downgrade = source[source.index("def downgrade()") :]
    for value in (
        "identity.identity_claim_algorithm_state",
        "identity.identity_subject_claim_registry",
        "public.slice3_digest_algorithm_state",
        "fingerprint_key_id",
        "identity.sync_p1_identity_claim_registry_v1()",
        "trg_slice3_p1_identity_claim_registry",
        "public.slice3_digest_algorithm_guard_v1(JSONB)",
        "SLICE3_KEY_CHECK_V1\\0",
        "SLICE3_IDENTITY_FINGERPRINT_ALGORITHM_MISMATCH",
    ):
        assert value in source
    assert "_bootstrap_algorithm_state()" in upgrade
    assert "_safe_interfaces(roles)" in upgrade
    assert "CROSS JOIN identity.identity_claim_algorithm_state" in source
    assert "FOR SHARE" in source
    assert "FROM PUBLIC" in source
    assert "DROP TRIGGER trg_slice3_p1_identity_claim_registry" in downgrade
    assert "DROP FUNCTION identity.sync_p1_identity_claim_registry_v1()" in downgrade
    assert "public.slice3_digest_algorithm_guard_v1(JSONB)" in downgrade
    assert "DROP FUNCTION {signature}" in downgrade
    assert 'op.drop_table("slice3_digest_algorithm_state", schema="public")' in downgrade


def test_候选guard同时校验容量() -> None:
    source = MIGRATION.read_text(encoding="utf-8").replace(" ", "")
    assert "p.active_case_count<p.capacity_limit" in source


def test_纯P1派生registry可回滚而Slice3claim拒绝() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    downgrade = source[source.index("def downgrade()") :]
    assert "source_kind<>'P1'" in downgrade.replace(" ", "")
    assert "DELETE FROM identity.identity_subject_claim_registry WHERE source_kind='P1'" in downgrade


def test_A3逐请求credential证明与一次性PII函数最小权限() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "public.slice3_reviewer_step_up_budget_v1" in source
    assert "value_credential_proof_digest CHAR(64)" in source
    assert "pg_catalog.sha256" in source
    assert "pg_catalog.convert_to" in source
    assert "pg_catalog.encode" in source
    assert "pgcrypto" not in source.lower()
    assert "CREATE EXTENSION" not in source.upper()
    assert "value_access UUID" in source
    assert "value_nonce UUID" in source
    assert "value_currentness_digest CHAR(64)" in source
    assert "IDENTITY_PII_STEP_UP_FAILED" in source
    assert "IDENTITY_PII_STEP_UP_RATE_LIMITED" in source


def test_B1复合FK与current_pointer目录精确() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for name in (
        "fk_service_case_identity_revision_scope",
        "fk_service_case_current_inputs_scope",
        "fk_member_identity_review_decision_revision_scope",
        "fk_proxy_grant_authorization_document_version",
        "uq_service_enrollment_current_case_inputs",
    ):
        assert name in source
    assert "member_identity_verification(verification_id,current_revision_id)" in source.replace(" ", "")


def test_B2完整后像expected函数与非空digest() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for name in (
        "slice3_enrollment_mutation_expected_v1",
        "slice3_identity_review_mutation_expected_v1",
        "slice3_case_mutation_expected_v1",
    ):
        assert f"FUNCTION public.{name}" in source
    assert "expected_confirmed_digest IS NULL" not in source
    assert "expected_confirmed_digest=''" not in source.replace(" ", "")


def test_C3五身份AuditSequence仅实际Writer允许() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    audit_grant = next(
        line for line in source.splitlines()
        if "GRANT USAGE,SELECT ON SEQUENCE public.member_enrollment_audit_audit_id_seq" in line
    )
    assert '"{worker}"' not in audit_grant
    assert '"{enrollment}"' in audit_grant
    assert '"{review}"' in audit_grant
    assert '"{case}"' in audit_grant
