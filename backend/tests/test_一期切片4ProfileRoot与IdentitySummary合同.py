from dataclasses import fields
import inspect
from pathlib import Path
import re
from uuid import UUID


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / (
    "app/migrations/versions/"
    "20260821_0024_phase1_slice4_health_record_assessment_readiness.py"
)


def _migration_source() -> str:
    assert MIGRATION.is_file(), "D51: Slice 4 must use the linear 0024 migration"
    return MIGRATION.read_text(encoding="utf-8")


def _candidate_or_catalog_source() -> str:
    if MIGRATION.is_file():
        return MIGRATION.read_text(encoding="utf-8")
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "app/migrations/versions").glob("*.py"))
    )


def _function_block(source: str, name: str) -> str:
    match = re.search(
        rf"CREATE FUNCTION public\.{re.escape(name)}\(.*?\).*?\$\$.*?\$\$;",
        source,
        flags=re.DOTALL,
    )
    assert match is not None, f"missing bounded function: {name}"
    return match.group(0)


def test_D45_profile_root函数冻结完整原子后像() -> None:
    source = _candidate_or_catalog_source()
    signature = "public.slice4_health_profile_root_create_v1"
    for token in (
        signature,
        "requested_profile_public_id UUID",
        "requested_profile_revision_id UUID",
        "identity_revision_ref UUID",
        "identity_source_version BIGINT",
        "expected_postimage_digest BYTEA",
        "SECURITY DEFINER",
        "SET search_path=pg_catalog,pg_temp",
        "SLICE4_HEALTH_PROFILE_ROOT_FORBIDDEN",
    ):
        assert token in source
    function = _function_block(source, "slice4_health_profile_root_create_v1")
    writes = (
        "INSERT INTO public.health_profile_revision",
        "INSERT INTO public.health_profile(",
        "INSERT INTO public.slice4_audit",
        "INSERT INTO public.slice4_outbox",
        "INSERT INTO public.slice4_idempotency",
    )
    positions = [function.index(write) for write in writes]
    assert positions == sorted(positions)
    for write in writes:
        assert function.count(write) == 1
    for column in (
        "profile_public_id",
        "subject_member_id",
        "current_revision_id",
        "identity_revision_ref",
        "identity_source_version",
        "tenant_public_id",
        "request_digest",
        "postimage_digest",
    ):
        assert column in function
    assert "postimage_digest=expected_postimage_digest" in "".join(function.split())
    assert "expected_postimage_digest :=" not in function
    assert "INTO expected_postimage_digest" not in function
    assert "SELECT expected_postimage_digest FROM" not in function


def test_D46_profile_root_Runtime零sequence与零root直接INSERT() -> None:
    source = _candidate_or_catalog_source()
    assert "FUNCTION public.slice4_health_profile_root_create_v1" in source
    assert "GRANT USAGE ON SEQUENCE public.health_profile_id_seq" not in source
    assert "GRANT SELECT ON SEQUENCE public.health_profile_id_seq" not in source
    assert "GRANT UPDATE ON SEQUENCE public.health_profile_id_seq" not in source
    assert "GRANT INSERT ON TABLE public.health_profile" not in source
    grant = next(
        line
        for line in source.splitlines()
        if "GRANT EXECUTE ON FUNCTION public.slice4_health_profile_root_create_v1" in line
    )
    assert 'TO "{writer}"' in grant


def test_D47_v1_v2_truth及PROXY_ELDER无User完整真值表() -> None:
    from app.modules.user_health.models import HEALTH_PROFILE_TABLE, health_profile_truth_v2

    columns = {column.name: column for column in HEALTH_PROFILE_TABLE.columns}
    assert {"profile_public_id", "subject_member_id", "current_revision_id", "version"} <= set(
        columns
    )
    assert columns["user_id"].nullable is True
    assert columns["gender"].nullable is True
    assert columns["birth_date"].nullable is True
    valid = (
        # V1 compatibility row.
        (None, None, 1, "MALE", "2000-01-01", None, None, True),
        # V2 SELF may retain the same subject's User.
        ("member", "revision", 1, None, None, None, None, True),
        # V2 PROXY_ELDER has no User.
        ("member", "revision", None, None, None, None, None, True),
    )
    invalid = (
        (None, None, None, "MALE", "2000-01-01", None, None, False),
        (None, "revision", 1, "MALE", "2000-01-01", None, None, False),
        ("member", None, None, None, None, None, None, False),
        ("member", "revision", None, "MALE", None, None, None, False),
        ("member", "revision", None, None, "2000-01-01", None, None, False),
        ("member", "revision", None, None, None, "175.0", None, False),
        ("member", "revision", None, None, None, None, "70.0", False),
    )
    for row in (*valid, *invalid):
        assert health_profile_truth_v2(*row[:-1]) is row[-1]

    source = _candidate_or_catalog_source()
    exact_check = (
        "((subject_member_id IS NULL AND current_revision_id IS NULL "
        "AND user_id IS NOT NULL AND gender IS NOT NULL AND birth_date IS NOT NULL) OR "
        "(subject_member_id IS NOT NULL AND profile_public_id IS NOT NULL "
        "AND current_revision_id IS NOT NULL AND gender IS NULL AND birth_date IS NULL "
        "AND height IS NULL AND weight IS NULL))"
    )
    assert exact_check in " ".join(source.split())


def test_D48_IdentitySummary六字段且API不暴露内部source_kind() -> None:
    from app.modules.member_enrollment.ports import VerifiedIdentitySummaryV2
    from app.modules.user_health.schemas import HealthIdentitySummaryDTO

    assert [field.name for field in fields(VerifiedIdentitySummaryV2)] == [
        "gender",
        "birth_date",
        "identity_revision_ref",
        "source_version",
        "tenant_public_id",
        "evidence_status",
    ]
    assert set(HealthIdentitySummaryDTO.model_fields) == {
        "gender",
        "birth_date",
        "identity_revision_ref",
        "source_version",
        "tenant_public_id",
        "evidence_status",
    }
    assert "identity_source_kind" not in HealthIdentitySummaryDTO.model_fields


def test_D52_Writer事务内currentness函数冻结全部证据与锁() -> None:
    source = _candidate_or_catalog_source()
    function = _function_block(source, "slice4_identity_summary_current_v1")
    for token in (
        "subject_member_id UUID",
        "service_case_id UUID",
        "identity_revision_ref UUID",
        "identity_source_version BIGINT",
        "tenant_public_id UUID",
        "RETURNS BOOLEAN",
        "FOR SHARE",
        "SECURITY DEFINER",
    ):
        assert token in function
    for token in (
        "service_case",
        "service_enrollment",
        "identity_verification_submission",
        "member_identity_verification",
        "member_identity_revision",
        "member_identity_review_decision",
        "identity_subject_claim_registry",
        "institution_application",
    ):
        assert token in function
    for comparison in (
        "c.case_id=value_service_case_id",
        "e.subject_member_id=value_subject_member_id",
        "a.tenant_public_id=value_tenant_public_id",
        "s.submission_id=value_identity_revision_ref",
        "d.facts_version=value_identity_source_version",
        "r.revision_id=value_identity_revision_ref",
        "g.source_facts_version=value_identity_source_version",
    ):
        assert comparison in "".join(function.split())
    compact = "".join(function.split())
    assert "identity_source_kind='P1'" in compact
    assert "identity_source_kind='SLICE3'" in compact
    validation_positions = [compact.index(value) for value in (
        "c.case_id=value_service_case_id",
        "a.tenant_public_id=value_tenant_public_id",
        "s.submission_id=value_identity_revision_ref",
        "d.facts_version=value_identity_source_version",
        "r.revision_id=value_identity_revision_ref",
        "g.source_facts_version=value_identity_source_version",
    )]
    assert max(validation_positions) < compact.rindex("RETURNTRUE")
    grants = [
        line for line in source.splitlines()
        if "GRANT EXECUTE ON FUNCTION public.slice4_identity_summary_current_v1" in line
    ]
    assert len(grants) == 1
    assert '"{writer}"' in grants[0] and '"{readiness}"' in grants[0]
    assert '"{worker}"' not in grants[0] and '"{reader}"' not in grants[0]


def test_IdentitySummaryPort只读结果必须由Writer复核() -> None:
    from app.modules.member_enrollment import ports

    authority_source = inspect.getsource(ports.HealthIdentitySummaryAuthorityPort)
    currentness_source = inspect.getsource(ports.HealthIdentitySummaryCurrentnessPort)
    assert "VerifiedIdentitySummaryV2" in authority_source
    for name in (
        "subject_member_id",
        "service_case_id",
        "identity_revision_ref",
        "identity_source_version",
        "tenant_public_id",
    ):
        assert name in currentness_source


def test_IdentitySummaryAuthority必须经受限source函数并解析PRC性别() -> None:
    from app.modules.member_enrollment import identity_authority

    source = _candidate_or_catalog_source()
    function = _function_block(source, "slice4_identity_summary_source_v1")
    for token in (
        "RETURNS TABLE",
        "identity_source_kind VARCHAR",
        "identity_revision_ref UUID",
        "source_version BIGINT",
        "tenant_public_id UUID",
        "SECURITY DEFINER",
        "SET search_path=pg_catalog,pg_temp",
        "FOR SHARE",
        "session_user",
    ):
        assert token in function
    grant = next(
        line
        for line in source.splitlines()
        if "GRANT EXECUTE ON FUNCTION public.slice4_identity_summary_source_v1" in line
    )
    assert '"{identity}"' in grant
    assert '"{writer}"' not in grant and '"{readiness}"' not in grant

    assert identity_authority.parse_prc_resident_identity_gender(
        "11010519491231002X"
    ) == "FEMALE"
    assert identity_authority.parse_prc_resident_identity_gender(
        "110105194912310038"
    ) == "MALE"
    authority_source = inspect.getsource(identity_authority.Slice4IdentitySummaryAuthority)
    assert "slice4_identity_summary_source_v1" in authority_source
    assert "MemberEnrollmentSecrets" in authority_source
    assert "IdentitySubmissionCrypto" in authority_source


def test_固定UUID样本均为v7() -> None:
    for value in (
        "0198f1c0-0000-7000-8000-000000000001",
        "0198f1c0-0000-7000-8000-000000000002",
        "0198f1c0-0000-7000-8000-000000000003",
    ):
        assert UUID(value).version == 7
