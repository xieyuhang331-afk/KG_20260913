import ast
import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / (
    "app/migrations/versions/"
    "20260823_0028_phase1_slice4_health_record_assessment_readiness.py"
)
MIGRATION_0027 = ROOT / (
    "app/migrations/versions/"
    "20260822_0027_phase1_slice3_review_enrollment_preimage_authority.py"
)
MIGRATION_0023 = ROOT / (
    "app/migrations/versions/"
    "20260821_0023_phase1_slice3_invitation_resend_runtime_acl.py"
)


def _source() -> str:
    assert MIGRATION.is_file()
    return MIGRATION.read_text(encoding="utf-8")


def test_D51_0028线性继承已合并Hotfix0027且历史不改写() -> None:
    source = _source()
    tree = ast.parse(source)
    assigned = {
        target.id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}
    }
    assert assigned == {
        "revision": "20260823_0028",
        "down_revision": "20260822_0027",
    }
    assert MIGRATION_0027.is_file()
    migration_0027_lf = MIGRATION_0027.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(migration_0027_lf).hexdigest().upper() == (
        "F23AE222731FAD857C4A72A8F647C1C7A6D775D880677ABC52E9C3CB175D803D"
    )
    assert "CASCADE" not in source.upper()


def test_0028四个owner边界及零RuntimeSequence授权() -> None:
    source = _source()
    for name in (
        "slice4_health_profile_root_create_v1",
        "slice4_detection_report_create_v1",
        "slice4_health_fact_state_transition_v1",
        "slice4_assessment_assembly_write_v1",
        "slice4_identity_summary_current_v1",
    ):
        assert f"FUNCTION public.{name}" in source
        assert "REVOKE ALL ON FUNCTION public." + name in source
    for sequence in (
        "health_profile_id_seq",
        "detection_report_id_seq",
        "health_fact_status_event_status_event_seq_seq",
        "assessment_input_assembly_assembly_seq_seq",
    ):
        for privilege in ("USAGE", "SELECT", "UPDATE"):
            assert f"GRANT {privilege} ON SEQUENCE public.{sequence}" not in source


def test_0028保持P3_v1_shadow规则版本兼容且只冻结v2规则() -> None:
    source = _source()
    assert "AND (projection_version=1 " in source
    assert "OR (projection_version=2 AND rule_version='health-daily-selection-v2'))" in source
    assert "projection_version=1 AND rule_version='health-daily-selection-v1'" not in source


def test_0028非空downgrade在REVOKE_DROP_ALTER前fail_closed() -> None:
    source = _source()
    downgrade = source[source.index("def downgrade()") :]
    marker = "Slice 4 downgrade requires empty module tables"
    assert marker in downgrade
    guard = downgrade.index(marker)
    ddl_positions = [
        position
        for token in ("REVOKE", "DROP", "op.drop_", "op.alter_column")
        if (position := downgrade.find(token)) >= 0
    ]
    assert ddl_positions and guard < min(ddl_positions)


def test_0028保留PR53四列权限且不修改历史Migration() -> None:
    source = _source()
    hotfix = MIGRATION_0023.read_text(encoding="utf-8")
    exact_columns = ("code_digest", "code_key_id", "expires_at", "issued_at")
    for column in exact_columns:
        assert column in hotfix
    assert "GRANT UPDATE" in hotfix and "REVOKE UPDATE" in hotfix
    assert "member_service_invitation" in hotfix
    for column in exact_columns:
        assert f"has_column_privilege" in source
        assert column in source
    downgrade = source[source.index("def downgrade()") :]
    assert "member_service_invitation" not in downgrade or "has_column_privilege" in downgrade


def test_D11_D17_D23_三个owner函数使用冻结签名且不是JSON回显占位() -> None:
    source = _source()
    assert "(request JSONB) RETURNS JSONB" not in source
    signatures = {
        "slice4_detection_report_create_v1": (
            "value_actor_user_id BIGINT",
            "value_actor_context VARCHAR",
            "value_subject_member_id UUID",
            "value_service_case_id UUID",
            "value_enrollment_id UUID",
            "value_requested_report_id UUID",
            "value_private_file_ids UUID[]",
            "RETURNS TABLE(report_id UUID,version BIGINT,received_at TIMESTAMPTZ,report_status VARCHAR,attachment_count SMALLINT)",
            "INSERT INTO public.detection_report(",
            "INSERT INTO public.detection_report_attachment",
        ),
        "slice4_health_fact_state_transition_v1": (
            "value_fact_ref UUID",
            "value_target_state VARCHAR",
            "value_expected_state VARCHAR",
            "value_expected_version BIGINT",
            "value_service_case_id UUID",
            "RETURNS TABLE(fact_ref UUID,state VARCHAR,event_no BIGINT,status_event_seq BIGINT)",
            "INSERT INTO public.health_fact_status_event",
        ),
        "slice4_assessment_assembly_write_v1": (
            "value_service_case_id UUID",
            "value_requested_assembly_id UUID",
            "value_subject_member_id UUID",
            "value_tenant_public_id UUID",
            "value_source_vector JSONB",
            "value_encrypted_fact_rows JSONB",
            "RETURNS TABLE(assembly_id UUID,service_case_id UUID,readiness_status VARCHAR,pointer_version BIGINT,generated_at TIMESTAMPTZ)",
            "INSERT INTO public.assessment_input_assembly(",
            "INSERT INTO public.assessment_input_assembly_fact",
            "INSERT INTO public.assessment_readiness_case_pointer",
        ),
    }
    compact = "".join(source.split())
    for name, tokens in signatures.items():
        assert f"CREATEFUNCTIONpublic.{name}(" in compact
        for token in tokens:
            assert "".join(token.split()) in compact


def test_D40_D44_PG16_受限读取投影确认边界与权限目录完整() -> None:
    source = _source()
    views = (
        "slice4_health_profile_clinical_read_v1",
        "slice4_institution_health_record_read_v1",
        "slice4_detection_report_read_v1",
        "slice4_health_fact_status_read_v1",
        "slice4_assessment_readiness_read_v1",
        "slice4_recompute_candidate_v1",
        "health_ready_projection_resolution_v2",
        "health_projection_source_visibility_v2",
        "health_projection_status_visibility_v2",
        "slice4_projection_coverage_source_v2",
        "health_ready_subject_indicator_evidence_v2",
        "health_ready_projection_fact_v2",
    )
    functions = (
        ("slice4_subject_authority_v1", "UUID,UUID,BIGINT,VARCHAR"),
        ("slice4_readiness_currentness_v1", "UUID,BIGINT"),
        ("slice4_projection_coverage_v2", "UUID,JSONB"),
        ("slice4_report_file_authority_v1", "UUID,UUID,BIGINT,VARCHAR"),
        ("slice4_clinical_profile_read_v1", "BIGINT,VARCHAR,UUID,UUID,UUID"),
        ("slice4_clinical_report_read_v1", "BIGINT,VARCHAR,UUID,UUID,UUID,JSONB"),
        ("slice4_clinical_fact_read_v1", "BIGINT,VARCHAR,UUID,UUID,UUID,JSONB"),
        ("slice4_institution_health_read_v1", "BIGINT,UUID,VARCHAR,JSONB"),
        ("health_projection_builder_source_v2", "BIGINT,JSONB,VARCHAR"),
        ("health_projection_subject_evidence_verify_v2", "BIGINT"),
        ("slice4_profile_confirm_v1", "UUID,UUID,UUID"),
        ("slice4_report_confirm_v1", "UUID,UUID,UUID"),
        ("slice4_health_fact_confirm_v1", "UUID,UUID,UUID"),
        ("slice4_assembly_confirm_v1", "UUID,UUID,UUID"),
    )
    tree = ast.parse(source)
    assigned = {
        target.id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
        and target.id in {"_BOUNDARY_VIEWS", "_EXTENDED_FUNCTIONS"}
    }
    assert assigned["_BOUNDARY_VIEWS"] == views
    assert assigned["_EXTENDED_FUNCTIONS"] == functions
    assert 'CREATE VIEW public.{name} WITH (security_barrier=true)' in source
    assert 'REVOKE ALL ON TABLE public.{name} FROM PUBLIC' in source
    assert 'DROP VIEW public.{view}' in source
    assert 'CREATE FUNCTION public.{name}({arguments}) RETURNS {returns}' in source
    assert 'REVOKE ALL ON FUNCTION public.{name}({arguments}) FROM PUBLIC' in source
    assert 'DROP FUNCTION public.{name}({signature})' in source
    assert "SECURITY DEFINER SET search_path=pg_catalog,pg_temp" in source
    assert "health_projection_builder_source_v2" in source
    assert "health_projection_subject_evidence_verify_v2" in source
    assert "GRANT SELECT ON TABLE public.health_projection_source_visibility_v2" not in source
    assert "GRANT SELECT ON TABLE public.health_projection_status_visibility_v2" not in source
