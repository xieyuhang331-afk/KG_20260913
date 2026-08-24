from functools import lru_cache
import json
import os
from typing import Any

from pydantic import BaseModel, Field


def require_secret(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Required secret environment variable is missing: {name}")
    return value


class Settings(BaseModel):
    service_name: str = "KG_20260727"
    version: str = "0.1.0"
    environment: str = "local"
    database_driver: str = "postgresql+asyncpg"
    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "kg_20260727"
    database_user: str = "kg_app"
    database_password: str
    verification_writer_database_url: str | None = None
    health_fact_writer_database_url: str | None = None
    health_fact_digest_current_key_id: str | None = None
    health_fact_digest_keyring_json: str | None = None
    organization_mapping_writer_database_url: str | None = None
    health_mapping_writer_database_url: str | None = None
    organization_projection_builder_database_url: str | None = None
    health_projection_builder_database_url: str | None = None
    projection_confirmation_database_url: str | None = None
    organization_projection_shadow_database_url: str | None = None
    health_projection_shadow_database_url: str | None = None
    projection_ready_gate_database_url: str | None = None
    projection_shadow_confirmation_database_url: str | None = None
    organization_projection_reader_database_url: str | None = None
    health_projection_reader_database_url: str | None = None
    organization_projection_reader_role: str | None = None
    health_projection_reader_role: str | None = None
    organization_projection_digest_current_key_id: str | None = None
    organization_projection_digest_keyring_json: str | None = None
    health_projection_digest_current_key_id: str | None = None
    health_projection_digest_keyring_json: str | None = None
    organization_mapping_digest_current_key_id: str | None = None
    organization_mapping_digest_keyring_json: str | None = None
    identity_pii_kek_b64: str | None = None
    identity_pii_hmac_key_b64: str | None = None
    identity_pii_key_id: str | None = None
    identity_review_step_up_secret_key: str | None = None
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 120
    auth_context_map: dict[str, dict[str, Any]] = Field(default_factory=dict)
    async_runtime: str = "Celery + RabbitMQ"
    file_storage_backend: str = "MinIO"
    phase1_pilot_mode: bool = False
    institution_onboarding_writer_database_url: str | None = None
    institution_review_writer_database_url: str | None = None
    private_file_writer_database_url: str | None = None
    institution_onboarding_reader_database_url: str | None = None
    institution_onboarding_writer_role: str | None = None
    institution_review_writer_role: str | None = None
    private_file_writer_role: str | None = None
    institution_onboarding_reader_role: str | None = None
    therapist_onboarding_writer_database_url: str | None = None
    therapist_review_writer_database_url: str | None = None
    therapist_readiness_worker_database_url: str | None = None
    therapist_reader_database_url: str | None = None
    therapist_onboarding_writer_role: str | None = None
    therapist_review_writer_role: str | None = None
    therapist_readiness_worker_role: str | None = None
    therapist_reader_role: str | None = None
    member_enrollment_writer_database_url: str | None = None
    member_identity_review_writer_database_url: str | None = None
    member_case_writer_database_url: str | None = None
    member_workflow_worker_database_url: str | None = None
    member_enrollment_reader_database_url: str | None = None
    member_enrollment_writer_role: str | None = None
    member_identity_review_writer_role: str | None = None
    member_case_writer_role: str | None = None
    member_workflow_worker_role: str | None = None
    member_enrollment_reader_role: str | None = None
    health_record_writer_database_url: str | None = None
    assessment_readiness_writer_database_url: str | None = None
    slice4_workflow_worker_database_url: str | None = None
    slice4_clinical_reader_database_url: str | None = None
    slice4_institution_reader_database_url: str | None = None
    slice4_identity_authority_database_url: str | None = None
    health_record_writer_role: str | None = None
    assessment_readiness_writer_role: str | None = None
    slice4_workflow_worker_role: str | None = None
    slice4_clinical_reader_role: str | None = None
    slice4_institution_reader_role: str | None = None
    slice4_identity_authority_role: str | None = None
    slice4_profile_phi_current_key_id: str | None = None
    slice4_profile_phi_keyring_json: str | None = None
    slice4_assembly_phi_current_key_id: str | None = None
    slice4_assembly_phi_keyring_json: str | None = None
    slice5_assessment_writer_database_url: str | None = None
    slice5_risk_workflow_writer_database_url: str | None = None
    slice5_rule_governance_writer_database_url: str | None = None
    slice5_workflow_worker_database_url: str | None = None
    slice5_clinical_reader_database_url: str | None = None
    slice5_oversight_reader_database_url: str | None = None
    slice5_assessment_writer_role: str | None = None
    slice5_risk_workflow_writer_role: str | None = None
    slice5_rule_governance_writer_role: str | None = None
    slice5_workflow_worker_role: str | None = None
    slice5_clinical_reader_role: str | None = None
    slice5_oversight_reader_role: str | None = None
    slice5_digest_current_key_id: str | None = None
    slice5_digest_keyring_json: str | None = None
    slice5_phi_current_key_id: str | None = None
    slice5_phi_keyring_json: str | None = None
    celery_queues: tuple[str, ...] = (
        "ai",
        "judgment",
        "ocr",
        "report",
        "settlement",
        "notification",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        environment=os.getenv("KG_ENV", "local"),
        database_host=os.getenv("KG_DATABASE_HOST", "localhost"),
        database_port=int(os.getenv("KG_DATABASE_PORT", "5432")),
        database_name=os.getenv("KG_DATABASE_NAME", "kg_20260727"),
        database_user=os.getenv("KG_DATABASE_USER", "kg_app"),
        database_password=require_secret("KG_DATABASE_PASSWORD"),
        verification_writer_database_url=os.getenv(
            "KG_VERIFICATION_WRITER_DATABASE_URL"
        ),
        health_fact_writer_database_url=os.getenv(
            "KG_HEALTH_FACT_WRITER_DATABASE_URL"
        ),
        health_fact_digest_current_key_id=os.getenv(
            "KG_HEALTH_FACT_DIGEST_CURRENT_KEY_ID"
        ),
        health_fact_digest_keyring_json=os.getenv(
            "KG_HEALTH_FACT_DIGEST_KEYRING_JSON"
        ),
        organization_mapping_writer_database_url=os.getenv(
            "KG_ORGANIZATION_MAPPING_WRITER_DATABASE_URL"
        ),
        health_mapping_writer_database_url=os.getenv(
            "KG_HEALTH_MAPPING_WRITER_DATABASE_URL"
        ),
        organization_projection_builder_database_url=os.getenv("KG_ORGANIZATION_PROJECTION_BUILDER_DATABASE_URL"),
        health_projection_builder_database_url=os.getenv("KG_HEALTH_PROJECTION_BUILDER_DATABASE_URL"),
        projection_confirmation_database_url=os.getenv("KG_PROJECTION_CONFIRMATION_DATABASE_URL"),
        organization_projection_shadow_database_url=os.getenv("KG_ORGANIZATION_PROJECTION_SHADOW_DATABASE_URL"),
        health_projection_shadow_database_url=os.getenv("KG_HEALTH_PROJECTION_SHADOW_DATABASE_URL"),
        projection_ready_gate_database_url=os.getenv("KG_PROJECTION_READY_GATE_DATABASE_URL"),
        projection_shadow_confirmation_database_url=os.getenv("KG_PROJECTION_SHADOW_CONFIRMATION_DATABASE_URL"),
        organization_projection_reader_database_url=os.getenv("KG_ORGANIZATION_PROJECTION_READER_DATABASE_URL"),
        health_projection_reader_database_url=os.getenv("KG_HEALTH_PROJECTION_READER_DATABASE_URL"),
        organization_projection_reader_role=os.getenv("KG_ORGANIZATION_PROJECTION_READER_ROLE"),
        health_projection_reader_role=os.getenv("KG_HEALTH_PROJECTION_READER_ROLE"),
        organization_projection_digest_current_key_id=os.getenv("KG_ORGANIZATION_PROJECTION_DIGEST_CURRENT_KEY_ID"),
        organization_projection_digest_keyring_json=os.getenv("KG_ORGANIZATION_PROJECTION_DIGEST_KEYRING_JSON"),
        health_projection_digest_current_key_id=os.getenv("KG_HEALTH_PROJECTION_DIGEST_CURRENT_KEY_ID"),
        health_projection_digest_keyring_json=os.getenv("KG_HEALTH_PROJECTION_DIGEST_KEYRING_JSON"),
        organization_mapping_digest_current_key_id=os.getenv(
            "KG_ORGANIZATION_MAPPING_DIGEST_CURRENT_KEY_ID"
        ),
        organization_mapping_digest_keyring_json=os.getenv(
            "KG_ORGANIZATION_MAPPING_DIGEST_KEYRING_JSON"
        ),
        identity_pii_kek_b64=os.getenv("KG_IDENTITY_PII_KEK_B64"),
        identity_pii_hmac_key_b64=os.getenv("KG_IDENTITY_PII_HMAC_KEY_B64"),
        identity_pii_key_id=os.getenv("KG_IDENTITY_PII_KEY_ID"),
        identity_review_step_up_secret_key=os.getenv(
            "KG_IDENTITY_REVIEW_STEP_UP_SECRET_KEY"
        ),
        jwt_secret_key=require_secret("KG_JWT_SECRET_KEY"),
        jwt_algorithm=os.getenv("KG_JWT_ALGORITHM", "HS256"),
        jwt_access_token_expire_minutes=int(
            os.getenv("KG_JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "120")
        ),
        auth_context_map=json.loads(os.getenv("KG_AUTH_CONTEXT_MAP", "{}")),
        phase1_pilot_mode=os.getenv("KG_PHASE1_PILOT_MODE", "false").lower() == "true",
        institution_onboarding_writer_database_url=os.getenv("KG_INSTITUTION_ONBOARDING_WRITER_DATABASE_URL"),
        institution_review_writer_database_url=os.getenv("KG_INSTITUTION_REVIEW_WRITER_DATABASE_URL"),
        private_file_writer_database_url=os.getenv("KG_PRIVATE_FILE_WRITER_DATABASE_URL"),
        institution_onboarding_reader_database_url=os.getenv("KG_INSTITUTION_ONBOARDING_READER_DATABASE_URL"),
        institution_onboarding_writer_role=os.getenv("KG_INSTITUTION_ONBOARDING_WRITER_ROLE"),
        institution_review_writer_role=os.getenv("KG_INSTITUTION_REVIEW_WRITER_ROLE"),
        private_file_writer_role=os.getenv("KG_PRIVATE_FILE_WRITER_ROLE"),
        institution_onboarding_reader_role=os.getenv("KG_INSTITUTION_ONBOARDING_READER_ROLE"),
        therapist_onboarding_writer_database_url=os.getenv("KG_THERAPIST_ONBOARDING_WRITER_DATABASE_URL"),
        therapist_review_writer_database_url=os.getenv("KG_THERAPIST_REVIEW_WRITER_DATABASE_URL"),
        therapist_readiness_worker_database_url=os.getenv("KG_THERAPIST_READINESS_WORKER_DATABASE_URL"),
        therapist_reader_database_url=os.getenv("KG_THERAPIST_READER_DATABASE_URL"),
        therapist_onboarding_writer_role=os.getenv("KG_THERAPIST_ONBOARDING_WRITER_ROLE"),
        therapist_review_writer_role=os.getenv("KG_THERAPIST_REVIEW_WRITER_ROLE"),
        therapist_readiness_worker_role=os.getenv("KG_THERAPIST_READINESS_WORKER_ROLE"),
        therapist_reader_role=os.getenv("KG_THERAPIST_READER_ROLE"),
        member_enrollment_writer_database_url=os.getenv("KG_MEMBER_ENROLLMENT_WRITER_DATABASE_URL"),
        member_identity_review_writer_database_url=os.getenv("KG_MEMBER_IDENTITY_REVIEW_WRITER_DATABASE_URL"),
        member_case_writer_database_url=os.getenv("KG_MEMBER_CASE_WRITER_DATABASE_URL"),
        member_workflow_worker_database_url=os.getenv("KG_MEMBER_WORKFLOW_WORKER_DATABASE_URL"),
        member_enrollment_reader_database_url=os.getenv("KG_MEMBER_ENROLLMENT_READER_DATABASE_URL"),
        member_enrollment_writer_role=os.getenv("KG_MEMBER_ENROLLMENT_WRITER_ROLE"),
        member_identity_review_writer_role=os.getenv("KG_MEMBER_IDENTITY_REVIEW_WRITER_ROLE"),
        member_case_writer_role=os.getenv("KG_MEMBER_CASE_WRITER_ROLE"),
        member_workflow_worker_role=os.getenv("KG_MEMBER_WORKFLOW_WORKER_ROLE"),
        member_enrollment_reader_role=os.getenv("KG_MEMBER_ENROLLMENT_READER_ROLE"),
        health_record_writer_database_url=os.getenv("KG_HEALTH_RECORD_WRITER_DATABASE_URL"),
        assessment_readiness_writer_database_url=os.getenv("KG_ASSESSMENT_READINESS_WRITER_DATABASE_URL"),
        slice4_workflow_worker_database_url=os.getenv("KG_SLICE4_WORKFLOW_WORKER_DATABASE_URL"),
        slice4_clinical_reader_database_url=os.getenv("KG_SLICE4_CLINICAL_READER_DATABASE_URL"),
        slice4_institution_reader_database_url=os.getenv("KG_SLICE4_INSTITUTION_READER_DATABASE_URL"),
        slice4_identity_authority_database_url=os.getenv("KG_SLICE4_IDENTITY_AUTHORITY_DATABASE_URL"),
        health_record_writer_role=os.getenv("KG_HEALTH_RECORD_WRITER_ROLE"),
        assessment_readiness_writer_role=os.getenv("KG_ASSESSMENT_READINESS_WRITER_ROLE"),
        slice4_workflow_worker_role=os.getenv("KG_SLICE4_WORKFLOW_WORKER_ROLE"),
        slice4_clinical_reader_role=os.getenv("KG_SLICE4_CLINICAL_READER_ROLE"),
        slice4_institution_reader_role=os.getenv("KG_SLICE4_INSTITUTION_READER_ROLE"),
        slice4_identity_authority_role=os.getenv("KG_SLICE4_IDENTITY_AUTHORITY_ROLE"),
        slice4_profile_phi_current_key_id=os.getenv("KG_SLICE4_PROFILE_PHI_CURRENT_KEY_ID"),
        slice4_profile_phi_keyring_json=os.getenv("KG_SLICE4_PROFILE_PHI_KEYRING_JSON"),
        slice4_assembly_phi_current_key_id=os.getenv("KG_SLICE4_ASSEMBLY_PHI_CURRENT_KEY_ID"),
        slice4_assembly_phi_keyring_json=os.getenv("KG_SLICE4_ASSEMBLY_PHI_KEYRING_JSON"),
        slice5_assessment_writer_database_url=os.getenv("KG_SLICE5_ASSESSMENT_WRITER_DATABASE_URL"),
        slice5_risk_workflow_writer_database_url=os.getenv("KG_SLICE5_RISK_WORKFLOW_WRITER_DATABASE_URL"),
        slice5_rule_governance_writer_database_url=os.getenv("KG_SLICE5_RULE_GOVERNANCE_WRITER_DATABASE_URL"),
        slice5_workflow_worker_database_url=os.getenv("KG_SLICE5_WORKFLOW_WORKER_DATABASE_URL"),
        slice5_clinical_reader_database_url=os.getenv("KG_SLICE5_CLINICAL_READER_DATABASE_URL"),
        slice5_oversight_reader_database_url=os.getenv("KG_SLICE5_OVERSIGHT_READER_DATABASE_URL"),
        slice5_assessment_writer_role=os.getenv("KG_SLICE5_ASSESSMENT_WRITER_ROLE"),
        slice5_risk_workflow_writer_role=os.getenv("KG_SLICE5_RISK_WORKFLOW_WRITER_ROLE"),
        slice5_rule_governance_writer_role=os.getenv("KG_SLICE5_RULE_GOVERNANCE_WRITER_ROLE"),
        slice5_workflow_worker_role=os.getenv("KG_SLICE5_WORKFLOW_WORKER_ROLE"),
        slice5_clinical_reader_role=os.getenv("KG_SLICE5_CLINICAL_READER_ROLE"),
        slice5_oversight_reader_role=os.getenv("KG_SLICE5_OVERSIGHT_READER_ROLE"),
        slice5_digest_current_key_id=os.getenv("KG_SLICE5_DIGEST_CURRENT_KEY_ID"),
        slice5_digest_keyring_json=os.getenv("KG_SLICE5_DIGEST_KEYRING_JSON"),
        slice5_phi_current_key_id=os.getenv("KG_SLICE5_PHI_CURRENT_KEY_ID"),
        slice5_phi_keyring_json=os.getenv("KG_SLICE5_PHI_KEYRING_JSON"),
    )
