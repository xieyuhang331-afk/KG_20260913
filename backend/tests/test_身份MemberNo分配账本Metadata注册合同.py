import importlib.util
from pathlib import Path

from app.core.database import Base


CORE_TABLES = {
    "detection_report",
    "health_indicator",
    "health_profile",
    "message",
    "operation_log",
    "platform_org",
    "tenant",
    "tenant_attachment",
    "tenant_review_log",
    "user",
}
IDENTITY_TABLES = {
    "identity.member",
    "identity.member_no_allocation",
    "identity.registration_bootstrap_record",
    "identity.user_member_self_link",
    "public.identity_verification_decision",
    "public.identity_verification_submission",
    "public.registration_eligibility_decision",
    "public.registration_verified_outbox",
    "public.user_account_classification_decision",
}
CANONICAL_HEALTH_FACT_TABLES = {"public.canonical_health_fact"}
LEGACY_MAPPING_TABLES = {
    "public.organization_legacy_mapping",
    "public.health_indicator_legacy_mapping",
}
PROJECTION_BUILDER_TABLES = {
    "public.organization_projection_generation",
    "public.organization_projection_checkpoint",
    "public.organization_projection",
    "public.health_projection_generation",
    "public.health_projection_checkpoint",
    "public.health_projection_fact",
    "public.health_projection_window_selection",
}
PROJECTION_SHADOW_TABLES = {
    "public.organization_projection_shadow_run",
    "public.organization_projection_shadow_audit",
    "public.health_projection_shadow_run",
    "public.health_projection_shadow_audit",
}
PHASE1_SLICE2_TABLES = {
    f"public.{name}"
    for name in (
        "therapist_invitation", "therapist_profile", "therapist_profile_revision",
        "therapist_profile_revision_qualification", "therapist_qualification_version",
        "therapist_qualification_attachment", "therapist_review_item",
        "therapist_review_decision", "therapist_status_decision",
        "institution_service_readiness", "readiness_evidence",
        "therapist_workflow_idempotency", "therapist_workflow_audit",
        "therapist_workflow_outbox", "therapist_workflow_delivery",
    )
}
PHASE1_SLICE3_TABLES = {
    "identity.identity_claim_algorithm_state",
    "identity.identity_subject_claim_registry",
    "public.slice3_digest_algorithm_state",
    "public.member_service_invitation",
    "public.service_enrollment",
    "public.controlled_member_bootstrap",
    "public.member_identity_verification",
    "public.member_identity_revision",
    "public.member_identity_review_decision",
    "public.member_identity_pii_access",
    "public.proxy_grant",
    "public.consent_document_version",
    "public.consent_document_rendition",
    "public.consent_record",
    "public.primary_therapist_assignment",
    "public.service_case",
    "public.member_enrollment_idempotency",
    "public.member_enrollment_audit",
    "public.member_enrollment_outbox",
    "public.member_enrollment_delivery",
}
PHASE1_SLICE1_TABLES = {
    "public.institution_invitation", "public.institution_onboarding_account",
    "public.institution_application", "public.institution_application_revision",
    "public.institution_license", "public.private_file",
    "public.institution_onboarding_idempotency", "public.institution_onboarding_audit",
    "public.institution_onboarding_outbox",
    "public.institution_onboarding_delivery",
}


def test_MemberNo分配账本由现有Alembic导入链注册Metadata():
    env_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "migrations"
        / "env.py"
    )
    spec = importlib.util.spec_from_file_location(
        "p2_member_no_allocation_alembic_env",
        env_path,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    assert module.target_metadata is Base.metadata
    registered_tables = set(module.target_metadata.tables)
    assert CORE_TABLES <= registered_tables, (
        "Alembic target_metadata changed the frozen P1 core table set"
    )
    assert "public.registration_verified_outbox" in registered_tables, (
        "Alembic target_metadata is missing approved table:\n"
        "public.registration_verified_outbox"
    )
    assert IDENTITY_TABLES <= registered_tables, (
        "Alembic target_metadata is missing an approved identity table"
    )
    assert registered_tables == (
        CORE_TABLES
        | IDENTITY_TABLES
        | CANONICAL_HEALTH_FACT_TABLES
        | LEGACY_MAPPING_TABLES
        | PROJECTION_BUILDER_TABLES
            | PROJECTION_SHADOW_TABLES
                | PHASE1_SLICE1_TABLES
                | PHASE1_SLICE2_TABLES
                | PHASE1_SLICE3_TABLES
        )
