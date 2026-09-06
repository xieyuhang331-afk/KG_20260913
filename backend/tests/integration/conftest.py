import asyncio
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

from tests.integration.database_safety import (
    DisposableDatabaseTarget,
    validate_database_sentinel,
    validate_test_database_target,
)


BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"
REQUIRED_HEAD_REVISION = "20260906_0039"

def pytest_configure(config):
    config.addinivalue_line("markers", "integration: tests requiring external PostgreSQL")


def pytest_collection_modifyitems(config, items):
    if os.getenv("KG_RUN_PG_INTEGRATION") == "1":
        return
    skip_pg = pytest.mark.skip(reason="set KG_RUN_PG_INTEGRATION=1 to run PostgreSQL integration tests")
    for item in items:
        if item.get_closest_marker("integration"):
            item.add_marker(skip_pg)


def _get_test_database_target() -> tuple[str, DisposableDatabaseTarget]:
    database_url = os.getenv("KG_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("KG_TEST_DATABASE_URL is required for PostgreSQL integration tests")
    target = validate_test_database_target(
        database_url,
        integration_enabled=os.getenv("KG_RUN_PG_INTEGRATION"),
        destructive_enabled=os.getenv("KG_ALLOW_DESTRUCTIVE_TEST_DATABASE"),
        environment=os.getenv("KG_TEST_ENVIRONMENT"),
        run_id=os.getenv("KG_TEST_RUN_ID"),
    )
    return database_url, target


def _get_test_database_url() -> str:
    database_url, _ = _get_test_database_target()
    if not os.getenv("KG_TEST_MIGRATION_DATABASE_URL"):
        return database_url
    return _get_role_database_url("KG_TEST_MIGRATION_DATABASE_URL")


def _get_application_database_url() -> str:
    database_url, _ = _get_test_database_target()
    return database_url


def _get_readonly_database_url() -> str:
    return _get_role_database_url("KG_TEST_READONLY_DATABASE_URL")


def _get_verification_writer_database_url() -> str:
    return _get_role_database_url("KG_TEST_VERIFICATION_WRITER_DATABASE_URL")


def _get_delivery_worker_database_url() -> str:
    return _get_role_database_url("KG_TEST_DELIVERY_WORKER_DATABASE_URL")


def _get_outbox_audit_database_url() -> str:
    return _get_role_database_url("KG_TEST_OUTBOX_AUDIT_DATABASE_URL")


def _get_health_fact_writer_database_url() -> str:
    return _get_role_database_url("KG_TEST_HEALTH_FACT_WRITER_DATABASE_URL")


def _get_organization_mapping_writer_database_url() -> str:
    return _get_role_database_url("KG_TEST_ORGANIZATION_MAPPING_WRITER_DATABASE_URL")


def _get_health_mapping_writer_database_url() -> str:
    return _get_role_database_url("KG_TEST_HEALTH_MAPPING_WRITER_DATABASE_URL")


def _get_mapping_audit_database_url() -> str:
    return _get_role_database_url("KG_TEST_MAPPING_AUDIT_DATABASE_URL")


def _get_mapping_shadow_database_url() -> str:
    return _get_role_database_url("KG_TEST_MAPPING_SHADOW_DATABASE_URL")


def _get_organization_projection_builder_database_url() -> str:
    return _get_role_database_url("KG_TEST_ORGANIZATION_PROJECTION_BUILDER_DATABASE_URL")


def _get_health_projection_builder_database_url() -> str:
    return _get_role_database_url("KG_TEST_HEALTH_PROJECTION_BUILDER_DATABASE_URL")


def _get_projection_confirmation_database_url() -> str:
    return _get_role_database_url("KG_TEST_PROJECTION_CONFIRMATION_DATABASE_URL")


def _get_organization_projection_shadow_database_url() -> str:
    return _get_role_database_url("KG_TEST_ORGANIZATION_PROJECTION_SHADOW_DATABASE_URL")


def _get_health_projection_shadow_database_url() -> str:
    return _get_role_database_url("KG_TEST_HEALTH_PROJECTION_SHADOW_DATABASE_URL")


def _get_projection_ready_gate_database_url() -> str:
    return _get_role_database_url("KG_TEST_PROJECTION_READY_GATE_DATABASE_URL")


def _get_projection_shadow_confirmation_database_url() -> str:
    return _get_role_database_url("KG_TEST_PROJECTION_SHADOW_CONFIRMATION_DATABASE_URL")


def _get_organization_projection_reader_database_url() -> str:
    return _get_role_database_url("KG_TEST_ORGANIZATION_PROJECTION_READER_DATABASE_URL")


def _get_health_projection_reader_database_url() -> str:
    return _get_role_database_url("KG_TEST_HEALTH_PROJECTION_READER_DATABASE_URL")


def _get_member_enrollment_writer_database_url() -> str:
    return _get_role_database_url("KG_TEST_MEMBER_ENROLLMENT_WRITER_DATABASE_URL")


def _get_member_identity_review_writer_database_url() -> str:
    return _get_role_database_url("KG_TEST_MEMBER_IDENTITY_REVIEW_WRITER_DATABASE_URL")


def _get_member_case_writer_database_url() -> str:
    return _get_role_database_url("KG_TEST_MEMBER_CASE_WRITER_DATABASE_URL")


def _get_member_workflow_worker_database_url() -> str:
    return _get_role_database_url("KG_TEST_MEMBER_WORKFLOW_WORKER_DATABASE_URL")


def _get_member_enrollment_reader_database_url() -> str:
    return _get_role_database_url("KG_TEST_MEMBER_ENROLLMENT_READER_DATABASE_URL")


def _get_health_record_writer_database_url() -> str:
    return _get_role_database_url("KG_TEST_HEALTH_RECORD_WRITER_DATABASE_URL")


def _get_assessment_readiness_writer_database_url() -> str:
    return _get_role_database_url("KG_TEST_ASSESSMENT_READINESS_WRITER_DATABASE_URL")


def _get_slice4_workflow_worker_database_url() -> str:
    return _get_role_database_url("KG_TEST_SLICE4_WORKFLOW_WORKER_DATABASE_URL")


def _get_slice4_clinical_reader_database_url() -> str:
    return _get_role_database_url("KG_TEST_SLICE4_CLINICAL_READER_DATABASE_URL")


def _get_slice4_institution_reader_database_url() -> str:
    return _get_role_database_url("KG_TEST_SLICE4_INSTITUTION_READER_DATABASE_URL")


def _get_slice4_identity_authority_database_url() -> str:
    return _get_role_database_url("KG_TEST_SLICE4_IDENTITY_AUTHORITY_DATABASE_URL")


def _get_slice5_assessment_writer_database_url() -> str:
    return _get_role_database_url("KG_TEST_SLICE5_ASSESSMENT_WRITER_DATABASE_URL")


def _get_slice5_risk_workflow_writer_database_url() -> str:
    return _get_role_database_url("KG_TEST_SLICE5_RISK_WORKFLOW_WRITER_DATABASE_URL")


def _get_slice5_rule_governance_writer_database_url() -> str:
    return _get_role_database_url("KG_TEST_SLICE5_RULE_GOVERNANCE_WRITER_DATABASE_URL")


def _get_slice5_workflow_worker_database_url() -> str:
    return _get_role_database_url("KG_TEST_SLICE5_WORKFLOW_WORKER_DATABASE_URL")


def _get_slice5_clinical_reader_database_url() -> str:
    return _get_role_database_url("KG_TEST_SLICE5_CLINICAL_READER_DATABASE_URL")


def _get_slice5_oversight_reader_database_url() -> str:
    return _get_role_database_url("KG_TEST_SLICE5_OVERSIGHT_READER_DATABASE_URL")


def _get_slice6_institution_writer_database_url() -> str:
    return _get_role_database_url("KG_TEST_SLICE6_INSTITUTION_WRITER_DATABASE_URL")


def _get_slice6_template_writer_database_url() -> str:
    return _get_role_database_url("KG_TEST_SLICE6_TEMPLATE_WRITER_DATABASE_URL")


def _get_slice6_review_writer_database_url() -> str:
    return _get_role_database_url("KG_TEST_SLICE6_REVIEW_WRITER_DATABASE_URL")


def _get_slice6_workflow_worker_database_url() -> str:
    return _get_role_database_url("KG_TEST_SLICE6_WORKFLOW_WORKER_DATABASE_URL")


def _get_slice6_clinical_reader_database_url() -> str:
    return _get_role_database_url("KG_TEST_SLICE6_CLINICAL_READER_DATABASE_URL")


def _get_slice6_family_reader_database_url() -> str:
    return _get_role_database_url("KG_TEST_SLICE6_FAMILY_READER_DATABASE_URL")


def _get_slice7_milestone_writer_database_url() -> str:
    return _get_role_database_url("KG_TEST_SLICE7_MILESTONE_WRITER_DATABASE_URL")


def _get_slice7_case_writer_database_url() -> str:
    return _get_role_database_url("KG_TEST_SLICE7_CASE_WRITER_DATABASE_URL")


def _get_slice7_transfer_writer_database_url() -> str:
    return _get_role_database_url("KG_TEST_SLICE7_TRANSFER_WRITER_DATABASE_URL")


def _get_slice7_export_worker_database_url() -> str:
    return _get_role_database_url("KG_TEST_SLICE7_EXPORT_WORKER_DATABASE_URL")


def _get_slice7_family_reader_database_url() -> str:
    return _get_role_database_url("KG_TEST_SLICE7_FAMILY_READER_DATABASE_URL")


def _get_slice7_oversight_reader_database_url() -> str:
    return _get_role_database_url("KG_TEST_SLICE7_OVERSIGHT_READER_DATABASE_URL")


def _get_a2_identity_inventory_database_url() -> str:
    return _get_role_database_url("KG_TEST_A2_IDENTITY_INVENTORY_DATABASE_URL")


def _get_a2_identity_remediation_writer_database_url() -> str:
    return _get_role_database_url(
        "KG_TEST_A2_IDENTITY_REMEDIATION_WRITER_DATABASE_URL"
    )


def _get_a2_identity_remediation_confirmation_database_url() -> str:
    return _get_role_database_url(
        "KG_TEST_A2_IDENTITY_REMEDIATION_CONFIRMATION_DATABASE_URL"
    )


def _propagate_module_d_role_preflight_environment() -> None:
    aliases = {
        "KG_DATABASE_USER": "KG_TEST_APPLICATION_ROLE",
        "KG_IDENTITY_APPLICATION_DATABASE_URL": "KG_TEST_DATABASE_URL",
        "KG_READONLY_ROLE": "KG_TEST_READONLY_ROLE",
        "KG_READONLY_DATABASE_URL": "KG_TEST_READONLY_DATABASE_URL",
        "KG_VERIFICATION_WRITER_ROLE": "KG_TEST_VERIFICATION_WRITER_ROLE",
        "KG_VERIFICATION_WRITER_DATABASE_URL": "KG_TEST_VERIFICATION_WRITER_DATABASE_URL",
        "KG_DELIVERY_WORKER_ROLE": "KG_TEST_DELIVERY_WORKER_ROLE",
        "KG_DELIVERY_WORKER_DATABASE_URL": "KG_TEST_DELIVERY_WORKER_DATABASE_URL",
        "KG_OUTBOX_AUDIT_ROLE": "KG_TEST_OUTBOX_AUDIT_ROLE",
        "KG_OUTBOX_AUDIT_DATABASE_URL": "KG_TEST_OUTBOX_AUDIT_DATABASE_URL",
        "KG_HEALTH_FACT_WRITER_ROLE": "KG_TEST_HEALTH_FACT_WRITER_ROLE",
        "KG_HEALTH_FACT_WRITER_DATABASE_URL": "KG_TEST_HEALTH_FACT_WRITER_DATABASE_URL",
        "KG_ORGANIZATION_MAPPING_WRITER_ROLE": "KG_TEST_ORGANIZATION_MAPPING_WRITER_ROLE",
        "KG_ORGANIZATION_MAPPING_WRITER_DATABASE_URL": "KG_TEST_ORGANIZATION_MAPPING_WRITER_DATABASE_URL",
        "KG_HEALTH_MAPPING_WRITER_ROLE": "KG_TEST_HEALTH_MAPPING_WRITER_ROLE",
        "KG_HEALTH_MAPPING_WRITER_DATABASE_URL": "KG_TEST_HEALTH_MAPPING_WRITER_DATABASE_URL",
        "KG_MAPPING_AUDIT_ROLE": "KG_TEST_MAPPING_AUDIT_ROLE",
        "KG_MAPPING_AUDIT_DATABASE_URL": "KG_TEST_MAPPING_AUDIT_DATABASE_URL",
        "KG_MAPPING_SHADOW_ROLE": "KG_TEST_MAPPING_SHADOW_ROLE",
        "KG_MAPPING_SHADOW_DATABASE_URL": "KG_TEST_MAPPING_SHADOW_DATABASE_URL",
        "KG_ORGANIZATION_PROJECTION_BUILDER_ROLE": "KG_TEST_ORGANIZATION_PROJECTION_BUILDER_ROLE",
        "KG_ORGANIZATION_PROJECTION_BUILDER_DATABASE_URL": "KG_TEST_ORGANIZATION_PROJECTION_BUILDER_DATABASE_URL",
        "KG_HEALTH_PROJECTION_BUILDER_ROLE": "KG_TEST_HEALTH_PROJECTION_BUILDER_ROLE",
        "KG_HEALTH_PROJECTION_BUILDER_DATABASE_URL": "KG_TEST_HEALTH_PROJECTION_BUILDER_DATABASE_URL",
        "KG_PROJECTION_CONFIRMATION_ROLE": "KG_TEST_PROJECTION_CONFIRMATION_ROLE",
        "KG_PROJECTION_CONFIRMATION_DATABASE_URL": "KG_TEST_PROJECTION_CONFIRMATION_DATABASE_URL",
        "KG_ORGANIZATION_PROJECTION_SHADOW_ROLE": "KG_TEST_ORGANIZATION_PROJECTION_SHADOW_ROLE",
        "KG_ORGANIZATION_PROJECTION_SHADOW_DATABASE_URL": "KG_TEST_ORGANIZATION_PROJECTION_SHADOW_DATABASE_URL",
        "KG_HEALTH_PROJECTION_SHADOW_ROLE": "KG_TEST_HEALTH_PROJECTION_SHADOW_ROLE",
        "KG_HEALTH_PROJECTION_SHADOW_DATABASE_URL": "KG_TEST_HEALTH_PROJECTION_SHADOW_DATABASE_URL",
        "KG_PROJECTION_READY_GATE_ROLE": "KG_TEST_PROJECTION_READY_GATE_ROLE",
        "KG_PROJECTION_READY_GATE_DATABASE_URL": "KG_TEST_PROJECTION_READY_GATE_DATABASE_URL",
        "KG_PROJECTION_SHADOW_CONFIRMATION_ROLE": "KG_TEST_PROJECTION_SHADOW_CONFIRMATION_ROLE",
        "KG_PROJECTION_SHADOW_CONFIRMATION_DATABASE_URL": "KG_TEST_PROJECTION_SHADOW_CONFIRMATION_DATABASE_URL",
        "KG_ORGANIZATION_PROJECTION_READER_ROLE": "KG_TEST_ORGANIZATION_PROJECTION_READER_ROLE",
        "KG_ORGANIZATION_PROJECTION_READER_DATABASE_URL": "KG_TEST_ORGANIZATION_PROJECTION_READER_DATABASE_URL",
        "KG_HEALTH_PROJECTION_READER_ROLE": "KG_TEST_HEALTH_PROJECTION_READER_ROLE",
        "KG_HEALTH_PROJECTION_READER_DATABASE_URL": "KG_TEST_HEALTH_PROJECTION_READER_DATABASE_URL",
        "KG_INSTITUTION_ONBOARDING_WRITER_ROLE": "KG_TEST_INSTITUTION_ONBOARDING_WRITER_ROLE",
        "KG_INSTITUTION_ONBOARDING_WRITER_DATABASE_URL": "KG_TEST_INSTITUTION_ONBOARDING_WRITER_DATABASE_URL",
        "KG_INSTITUTION_REVIEW_WRITER_ROLE": "KG_TEST_INSTITUTION_REVIEW_WRITER_ROLE",
        "KG_INSTITUTION_REVIEW_WRITER_DATABASE_URL": "KG_TEST_INSTITUTION_REVIEW_WRITER_DATABASE_URL",
        "KG_PRIVATE_FILE_WRITER_ROLE": "KG_TEST_PRIVATE_FILE_WRITER_ROLE",
        "KG_PRIVATE_FILE_WRITER_DATABASE_URL": "KG_TEST_PRIVATE_FILE_WRITER_DATABASE_URL",
        "KG_PRIVATE_FILE_ACCESS_WRITER_ROLE": "KG_TEST_PRIVATE_FILE_ACCESS_WRITER_ROLE",
        "KG_PRIVATE_FILE_ACCESS_WRITER_DATABASE_URL": "KG_TEST_PRIVATE_FILE_ACCESS_WRITER_DATABASE_URL",
        "KG_INSTITUTION_ONBOARDING_READER_ROLE": "KG_TEST_INSTITUTION_ONBOARDING_READER_ROLE",
        "KG_INSTITUTION_ONBOARDING_READER_DATABASE_URL": "KG_TEST_INSTITUTION_ONBOARDING_READER_DATABASE_URL",
        "KG_THERAPIST_ONBOARDING_WRITER_ROLE": "KG_TEST_THERAPIST_ONBOARDING_WRITER_ROLE",
        "KG_THERAPIST_ONBOARDING_WRITER_DATABASE_URL": "KG_TEST_THERAPIST_ONBOARDING_WRITER_DATABASE_URL",
        "KG_THERAPIST_REVIEW_WRITER_ROLE": "KG_TEST_THERAPIST_REVIEW_WRITER_ROLE",
        "KG_THERAPIST_REVIEW_WRITER_DATABASE_URL": "KG_TEST_THERAPIST_REVIEW_WRITER_DATABASE_URL",
        "KG_THERAPIST_READINESS_WORKER_ROLE": "KG_TEST_THERAPIST_READINESS_WORKER_ROLE",
        "KG_THERAPIST_READINESS_WORKER_DATABASE_URL": "KG_TEST_THERAPIST_READINESS_WORKER_DATABASE_URL",
        "KG_THERAPIST_READER_ROLE": "KG_TEST_THERAPIST_READER_ROLE",
        "KG_THERAPIST_READER_DATABASE_URL": "KG_TEST_THERAPIST_READER_DATABASE_URL",
        "KG_MEMBER_ENROLLMENT_WRITER_ROLE": "KG_TEST_MEMBER_ENROLLMENT_WRITER_ROLE",
        "KG_MEMBER_ENROLLMENT_WRITER_DATABASE_URL": "KG_TEST_MEMBER_ENROLLMENT_WRITER_DATABASE_URL",
        "KG_MEMBER_IDENTITY_REVIEW_WRITER_ROLE": "KG_TEST_MEMBER_IDENTITY_REVIEW_WRITER_ROLE",
        "KG_MEMBER_IDENTITY_REVIEW_WRITER_DATABASE_URL": "KG_TEST_MEMBER_IDENTITY_REVIEW_WRITER_DATABASE_URL",
        "KG_MEMBER_CASE_WRITER_ROLE": "KG_TEST_MEMBER_CASE_WRITER_ROLE",
        "KG_MEMBER_CASE_WRITER_DATABASE_URL": "KG_TEST_MEMBER_CASE_WRITER_DATABASE_URL",
        "KG_MEMBER_WORKFLOW_WORKER_ROLE": "KG_TEST_MEMBER_WORKFLOW_WORKER_ROLE",
        "KG_MEMBER_WORKFLOW_WORKER_DATABASE_URL": "KG_TEST_MEMBER_WORKFLOW_WORKER_DATABASE_URL",
        "KG_MEMBER_ENROLLMENT_READER_ROLE": "KG_TEST_MEMBER_ENROLLMENT_READER_ROLE",
        "KG_MEMBER_ENROLLMENT_READER_DATABASE_URL": "KG_TEST_MEMBER_ENROLLMENT_READER_DATABASE_URL",
        "KG_HEALTH_RECORD_WRITER_ROLE": "KG_TEST_HEALTH_RECORD_WRITER_ROLE",
        "KG_HEALTH_RECORD_WRITER_DATABASE_URL": "KG_TEST_HEALTH_RECORD_WRITER_DATABASE_URL",
        "KG_ASSESSMENT_READINESS_WRITER_ROLE": "KG_TEST_ASSESSMENT_READINESS_WRITER_ROLE",
        "KG_ASSESSMENT_READINESS_WRITER_DATABASE_URL": "KG_TEST_ASSESSMENT_READINESS_WRITER_DATABASE_URL",
        "KG_SLICE4_WORKFLOW_WORKER_ROLE": "KG_TEST_SLICE4_WORKFLOW_WORKER_ROLE",
        "KG_SLICE4_WORKFLOW_WORKER_DATABASE_URL": "KG_TEST_SLICE4_WORKFLOW_WORKER_DATABASE_URL",
        "KG_SLICE4_CLINICAL_READER_ROLE": "KG_TEST_SLICE4_CLINICAL_READER_ROLE",
        "KG_SLICE4_CLINICAL_READER_DATABASE_URL": "KG_TEST_SLICE4_CLINICAL_READER_DATABASE_URL",
        "KG_SLICE4_INSTITUTION_READER_ROLE": "KG_TEST_SLICE4_INSTITUTION_READER_ROLE",
        "KG_SLICE4_INSTITUTION_READER_DATABASE_URL": "KG_TEST_SLICE4_INSTITUTION_READER_DATABASE_URL",
        "KG_SLICE4_IDENTITY_AUTHORITY_ROLE": "KG_TEST_SLICE4_IDENTITY_AUTHORITY_ROLE",
        "KG_SLICE4_IDENTITY_AUTHORITY_DATABASE_URL": "KG_TEST_SLICE4_IDENTITY_AUTHORITY_DATABASE_URL",
        "KG_SLICE5_ASSESSMENT_WRITER_ROLE": "KG_TEST_SLICE5_ASSESSMENT_WRITER_ROLE",
        "KG_SLICE5_ASSESSMENT_WRITER_DATABASE_URL": "KG_TEST_SLICE5_ASSESSMENT_WRITER_DATABASE_URL",
        "KG_SLICE5_RISK_WORKFLOW_WRITER_ROLE": "KG_TEST_SLICE5_RISK_WORKFLOW_WRITER_ROLE",
        "KG_SLICE5_RISK_WORKFLOW_WRITER_DATABASE_URL": "KG_TEST_SLICE5_RISK_WORKFLOW_WRITER_DATABASE_URL",
        "KG_SLICE5_RULE_GOVERNANCE_WRITER_ROLE": "KG_TEST_SLICE5_RULE_GOVERNANCE_WRITER_ROLE",
        "KG_SLICE5_RULE_GOVERNANCE_WRITER_DATABASE_URL": "KG_TEST_SLICE5_RULE_GOVERNANCE_WRITER_DATABASE_URL",
        "KG_SLICE5_WORKFLOW_WORKER_ROLE": "KG_TEST_SLICE5_WORKFLOW_WORKER_ROLE",
        "KG_SLICE5_WORKFLOW_WORKER_DATABASE_URL": "KG_TEST_SLICE5_WORKFLOW_WORKER_DATABASE_URL",
        "KG_SLICE5_CLINICAL_READER_ROLE": "KG_TEST_SLICE5_CLINICAL_READER_ROLE",
        "KG_SLICE5_CLINICAL_READER_DATABASE_URL": "KG_TEST_SLICE5_CLINICAL_READER_DATABASE_URL",
        "KG_SLICE5_OVERSIGHT_READER_ROLE": "KG_TEST_SLICE5_OVERSIGHT_READER_ROLE",
        "KG_SLICE5_OVERSIGHT_READER_DATABASE_URL": "KG_TEST_SLICE5_OVERSIGHT_READER_DATABASE_URL",
        "KG_SLICE6_INSTITUTION_WRITER_ROLE": "KG_TEST_SLICE6_INSTITUTION_WRITER_ROLE",
        "KG_SLICE6_INSTITUTION_WRITER_DATABASE_URL": "KG_TEST_SLICE6_INSTITUTION_WRITER_DATABASE_URL",
        "KG_SLICE6_TEMPLATE_WRITER_ROLE": "KG_TEST_SLICE6_TEMPLATE_WRITER_ROLE",
        "KG_SLICE6_TEMPLATE_WRITER_DATABASE_URL": "KG_TEST_SLICE6_TEMPLATE_WRITER_DATABASE_URL",
        "KG_SLICE6_REVIEW_WRITER_ROLE": "KG_TEST_SLICE6_REVIEW_WRITER_ROLE",
        "KG_SLICE6_REVIEW_WRITER_DATABASE_URL": "KG_TEST_SLICE6_REVIEW_WRITER_DATABASE_URL",
        "KG_SLICE6_WORKFLOW_WORKER_ROLE": "KG_TEST_SLICE6_WORKFLOW_WORKER_ROLE",
        "KG_SLICE6_WORKFLOW_WORKER_DATABASE_URL": "KG_TEST_SLICE6_WORKFLOW_WORKER_DATABASE_URL",
        "KG_SLICE6_CLINICAL_READER_ROLE": "KG_TEST_SLICE6_CLINICAL_READER_ROLE",
        "KG_SLICE6_CLINICAL_READER_DATABASE_URL": "KG_TEST_SLICE6_CLINICAL_READER_DATABASE_URL",
        "KG_SLICE6_FAMILY_READER_ROLE": "KG_TEST_SLICE6_FAMILY_READER_ROLE",
        "KG_SLICE6_FAMILY_READER_DATABASE_URL": "KG_TEST_SLICE6_FAMILY_READER_DATABASE_URL",
        "KG_SLICE7_MILESTONE_WRITER_ROLE": "KG_TEST_SLICE7_MILESTONE_WRITER_ROLE",
        "KG_SLICE7_MILESTONE_WRITER_DATABASE_URL": "KG_TEST_SLICE7_MILESTONE_WRITER_DATABASE_URL",
        "KG_SLICE7_CASE_WRITER_ROLE": "KG_TEST_SLICE7_CASE_WRITER_ROLE",
        "KG_SLICE7_CASE_WRITER_DATABASE_URL": "KG_TEST_SLICE7_CASE_WRITER_DATABASE_URL",
        "KG_SLICE7_TRANSFER_WRITER_ROLE": "KG_TEST_SLICE7_TRANSFER_WRITER_ROLE",
        "KG_SLICE7_TRANSFER_WRITER_DATABASE_URL": "KG_TEST_SLICE7_TRANSFER_WRITER_DATABASE_URL",
        "KG_SLICE7_EXPORT_WORKER_ROLE": "KG_TEST_SLICE7_EXPORT_WORKER_ROLE",
        "KG_SLICE7_EXPORT_WORKER_DATABASE_URL": "KG_TEST_SLICE7_EXPORT_WORKER_DATABASE_URL",
        "KG_SLICE7_FAMILY_READER_ROLE": "KG_TEST_SLICE7_FAMILY_READER_ROLE",
        "KG_SLICE7_FAMILY_READER_DATABASE_URL": "KG_TEST_SLICE7_FAMILY_READER_DATABASE_URL",
        "KG_SLICE7_OVERSIGHT_READER_ROLE": "KG_TEST_SLICE7_OVERSIGHT_READER_ROLE",
        "KG_SLICE7_OVERSIGHT_READER_DATABASE_URL": "KG_TEST_SLICE7_OVERSIGHT_READER_DATABASE_URL",
        "KG_A2_IDENTITY_INVENTORY_ROLE": "KG_TEST_A2_IDENTITY_INVENTORY_ROLE",
        "KG_A2_IDENTITY_INVENTORY_DATABASE_URL": "KG_TEST_A2_IDENTITY_INVENTORY_DATABASE_URL",
        "KG_A2_IDENTITY_REMEDIATION_WRITER_ROLE": "KG_TEST_A2_IDENTITY_REMEDIATION_WRITER_ROLE",
        "KG_A2_IDENTITY_REMEDIATION_WRITER_DATABASE_URL": "KG_TEST_A2_IDENTITY_REMEDIATION_WRITER_DATABASE_URL",
        "KG_A2_IDENTITY_REMEDIATION_CONFIRMATION_ROLE": "KG_TEST_A2_IDENTITY_REMEDIATION_CONFIRMATION_ROLE",
        "KG_A2_IDENTITY_REMEDIATION_CONFIRMATION_DATABASE_URL": "KG_TEST_A2_IDENTITY_REMEDIATION_CONFIRMATION_DATABASE_URL",
    }
    for target, source in aliases.items():
        os.environ[target] = os.environ[source]


def _get_role_database_url(environment_name: str) -> str:
    primary_url, primary_target = _get_test_database_target()
    database_url = os.getenv(environment_name)
    if not database_url:
        pytest.skip(f"{environment_name} is required for role separation tests")
    target = validate_test_database_target(
        database_url,
        integration_enabled=os.getenv("KG_RUN_PG_INTEGRATION"),
        destructive_enabled=os.getenv("KG_ALLOW_DESTRUCTIVE_TEST_DATABASE"),
        environment=os.getenv("KG_TEST_ENVIRONMENT"),
        run_id=os.getenv("KG_TEST_RUN_ID"),
    )
    primary = urlparse(primary_url)
    candidate = urlparse(database_url)
    if target != primary_target or (candidate.hostname, candidate.port) != (primary.hostname, primary.port):
        raise RuntimeError(f"{environment_name} must target the same ephemeral database endpoint")
    return database_url


def _build_alembic_config(database_url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND_ROOT / "app" / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _to_asyncpg_dsn(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return database_url


class PgDatabase:
    def __init__(self, database_url: str):
        self.database_url = _to_asyncpg_dsn(database_url)

    async def _fetch_value(self, sql: str, *args):
        connection = await asyncpg.connect(self.database_url)
        try:
            return await connection.fetchval(sql, *args)
        finally:
            await connection.close()

    async def _fetch_column(self, sql: str):
        connection = await asyncpg.connect(self.database_url)
        try:
            return [row[0] for row in await connection.fetch(sql)]
        finally:
            await connection.close()

    async def _fetch_rows(self, sql: str, *args):
        connection = await asyncpg.connect(self.database_url)
        try:
            return [dict(row) for row in await connection.fetch(sql, *args)]
        finally:
            await connection.close()

    async def _execute(self, sql: str) -> None:
        connection = await asyncpg.connect(self.database_url)
        try:
            await connection.execute(sql)
        finally:
            await connection.close()

    def fetch_value(self, sql: str):
        return asyncio.run(self._fetch_value(sql))

    def fetch_column(self, sql: str):
        return asyncio.run(self._fetch_column(sql))

    def fetch_rows(self, sql: str, *args):
        return asyncio.run(self._fetch_rows(sql, *args))

    def execute(self, sql: str) -> None:
        asyncio.run(self._execute(sql))


def _validated_role_name(environment_name: str) -> str:
    role_name = os.getenv(environment_name, "")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", role_name):
        raise RuntimeError(f"{environment_name} must contain a safe PostgreSQL role name")
    return role_name


def _grant_test_role_permissions(database: PgDatabase) -> None:
    if os.getenv("KG_TEST_ROLE_SEPARATION") != "1":
        return

    application_role = _validated_role_name("KG_TEST_APPLICATION_ROLE")
    readonly_role = _validated_role_name("KG_TEST_READONLY_ROLE")
    database.execute(f'GRANT USAGE ON SCHEMA public TO "{application_role}"')
    database.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{application_role}"')
    database.execute(f'GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO "{application_role}"')
    database.execute(
        'REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
        f'ON TABLE public.platform_org FROM "{application_role}"'
    )
    database.execute(
        'GRANT UPDATE (org_name, sort_order, status, admin_id, version, updated_at, updated_by) '
        f'ON TABLE public.platform_org TO "{application_role}"'
    )
    database.execute(
        f'REVOKE UPDATE ON SEQUENCE public.platform_org_id_seq FROM "{application_role}"'
    )
    database.execute(
        f'GRANT USAGE, SELECT ON SEQUENCE public.platform_org_id_seq TO "{application_role}"'
    )
    database.execute(
        'REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
        f'ON TABLE public.operation_log FROM "{application_role}"'
    )
    database.execute(
        f'REVOKE UPDATE ON SEQUENCE public.operation_log_id_seq FROM "{application_role}"'
    )
    database.execute(
        f'GRANT USAGE, SELECT ON SEQUENCE public.operation_log_id_seq TO "{application_role}"'
    )
    database.execute(f'REVOKE INSERT, UPDATE, DELETE ON TABLE alembic_version FROM "{application_role}"')
    database.execute(f'REVOKE CREATE ON SCHEMA public FROM "{application_role}"')
    database.execute(f'GRANT USAGE ON SCHEMA public TO "{readonly_role}"')
    database.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{readonly_role}"')
    slice1_tables = (
        "institution_invitation", "institution_onboarding_account", "institution_application",
        "institution_application_revision", "institution_license", "private_file",
        "institution_onboarding_idempotency", "institution_onboarding_audit", "institution_onboarding_outbox",
    )
    for role in (application_role, readonly_role):
        for table_name in slice1_tables:
            database.execute(f'REVOKE ALL ON TABLE public."{table_name}" FROM "{role}"')
    for view in (
        "organization_ready_projection_generation_v1",
        "organization_ready_projection_v1",
        "health_ready_projection_generation_v1",
        "health_ready_projection_fact_v1",
        "health_ready_projection_window_selection_v1",
    ):
        database.execute(f'REVOKE ALL PRIVILEGES ON TABLE public."{view}" FROM "{application_role}", "{readonly_role}"')
    database.execute(
        'REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
        f'ON TABLE public.platform_org, public.operation_log FROM "{readonly_role}"'
    )
    database.execute(
        'REVOKE ALL ON SEQUENCE public.platform_org_id_seq, public.operation_log_id_seq '
        f'FROM "{readonly_role}"'
    )
    database.execute(f'REVOKE CREATE ON SCHEMA public FROM "{readonly_role}"')
    database.execute(f'GRANT SELECT ON TABLE public.detection_report TO "{application_role}", "{readonly_role}"')
    database.execute(
        'REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
        f'ON TABLE public.detection_report FROM "{application_role}", "{readonly_role}"'
    )
    database.execute("REVOKE ALL ON SCHEMA identity FROM PUBLIC")
    database.execute(f'GRANT USAGE ON SCHEMA identity TO "{application_role}"')
    database.execute(f'REVOKE CREATE ON SCHEMA identity FROM "{application_role}"')
    database.execute(f'GRANT SELECT, INSERT, UPDATE ON TABLE identity.member TO "{application_role}"')
    database.execute(
        f'REVOKE DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLE identity.member FROM "{application_role}"'
    )
    database.execute(
        f'GRANT SELECT, INSERT ON TABLE identity.member_no_allocation TO "{application_role}"'
    )
    database.execute(
        'REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
        f'ON TABLE identity.member_no_allocation FROM "{application_role}"'
    )
    database.execute(f'GRANT USAGE ON SCHEMA identity TO "{readonly_role}"')
    database.execute(f'REVOKE CREATE ON SCHEMA identity FROM "{readonly_role}"')
    database.execute(f'GRANT SELECT ON TABLE identity.member TO "{readonly_role}"')
    database.execute(
        f'REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLE identity.member FROM "{readonly_role}"'
    )
    database.execute(
        f'GRANT SELECT ON TABLE identity.member_no_allocation TO "{readonly_role}"'
    )
    database.execute(
        'REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
        f'ON TABLE identity.member_no_allocation FROM "{readonly_role}"'
    )
    bootstrap_tables = (
        "user_member_self_link",
        "registration_bootstrap_record",
    )
    for table_name in bootstrap_tables:
        database.execute(
            f'GRANT SELECT, INSERT ON TABLE identity."{table_name}" '
            f'TO "{application_role}"'
        )
        database.execute(
            'REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
            f'ON TABLE identity."{table_name}" FROM "{application_role}"'
        )
        database.execute(
            f'GRANT SELECT ON TABLE identity."{table_name}" '
            f'TO "{readonly_role}"'
        )
        database.execute(
            'REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
            f'ON TABLE identity."{table_name}" FROM "{readonly_role}"'
        )
    evidence_tables = (
        "identity_verification_decision",
        "user_account_classification_decision",
        "registration_eligibility_decision",
    )
    for table_name in evidence_tables:
        database.execute(
            f'GRANT SELECT, INSERT ON TABLE public."{table_name}" '
            f'TO "{application_role}"'
        )
        database.execute(
            'REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
            f'ON TABLE public."{table_name}" FROM "{application_role}"'
        )
        database.execute(
            f'GRANT SELECT ON TABLE public."{table_name}" '
            f'TO "{readonly_role}"'
        )
        database.execute(
            'REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
            f'ON TABLE public."{table_name}" FROM "{readonly_role}"'
        )

    writer_role = _validated_role_name("KG_TEST_VERIFICATION_WRITER_ROLE")
    worker_role = _validated_role_name("KG_TEST_DELIVERY_WORKER_ROLE")
    audit_role = _validated_role_name("KG_TEST_OUTBOX_AUDIT_ROLE")
    fact_writer_role = _validated_role_name("KG_TEST_HEALTH_FACT_WRITER_ROLE")
    organization_mapping_role = _validated_role_name("KG_TEST_ORGANIZATION_MAPPING_WRITER_ROLE")
    health_mapping_role = _validated_role_name("KG_TEST_HEALTH_MAPPING_WRITER_ROLE")
    mapping_audit_role = _validated_role_name("KG_TEST_MAPPING_AUDIT_ROLE")
    mapping_shadow_role = _validated_role_name("KG_TEST_MAPPING_SHADOW_ROLE")
    organization_projection_role = _validated_role_name("KG_TEST_ORGANIZATION_PROJECTION_BUILDER_ROLE")
    health_projection_role = _validated_role_name("KG_TEST_HEALTH_PROJECTION_BUILDER_ROLE")
    projection_confirmation_role = _validated_role_name("KG_TEST_PROJECTION_CONFIRMATION_ROLE")
    organization_shadow_role = _validated_role_name("KG_TEST_ORGANIZATION_PROJECTION_SHADOW_ROLE")
    health_shadow_role = _validated_role_name("KG_TEST_HEALTH_PROJECTION_SHADOW_ROLE")
    ready_gate_role = _validated_role_name("KG_TEST_PROJECTION_READY_GATE_ROLE")
    shadow_confirmation_role = _validated_role_name("KG_TEST_PROJECTION_SHADOW_CONFIRMATION_ROLE")
    organization_reader_role = _validated_role_name("KG_TEST_ORGANIZATION_PROJECTION_READER_ROLE")
    health_reader_role = _validated_role_name("KG_TEST_HEALTH_PROJECTION_READER_ROLE")
    runtime_roles = (writer_role, worker_role, audit_role)
    for role in runtime_roles:
        database.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        database.execute(f'REVOKE CREATE ON SCHEMA public FROM "{role}"')
        database.execute(
            'GRANT SELECT (id, role, status, verify_status, updated_at) '
            f'ON TABLE public."user" TO "{role}"'
        )
        database.execute(
            f'GRANT SELECT ON TABLE public.identity_verification_decision, '
            'public.user_account_classification_decision, '
            f'public.registration_eligibility_decision TO "{role}"'
        )
        database.execute(
            f'GRANT SELECT ON TABLE public.registration_verified_outbox TO "{role}"'
        )
        database.execute(
            f'REVOKE ALL ON TABLE public.alembic_version FROM "{role}"'
        )
    database.execute(
        'GRANT UPDATE (verify_status, updated_at) ON TABLE public."user" '
        f'TO "{writer_role}"'
    )
    database.execute(
        'GRANT INSERT ON TABLE public.identity_verification_decision, '
        'public.registration_eligibility_decision, '
        f'public.registration_verified_outbox TO "{writer_role}"'
    )
    database.execute(
        f'GRANT INSERT ON TABLE public.registration_verified_outbox TO "{worker_role}"'
    )
    database.execute(
        'GRANT UPDATE (status, available_at, attempt_count, lease_owner, '
        'locked_until, lease_generation, last_error_category, last_error_code, '
        'last_error_digest, delivered_at, updated_at) '
        f'ON TABLE public.registration_verified_outbox TO "{worker_role}"'
    )
    for role in (application_role, readonly_role):
        database.execute(
            'REVOKE SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
            f'ON TABLE public.registration_verified_outbox FROM "{role}"'
        )
    database.execute(
        f'GRANT SELECT, INSERT ON TABLE public.identity_verification_submission TO "{application_role}"'
    )
    database.execute(
        'REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
        f'ON TABLE public.identity_verification_submission FROM "{application_role}"'
    )
    database.execute(
        f'GRANT SELECT ON TABLE public.identity_verification_submission TO "{readonly_role}", "{writer_role}", "{audit_role}"'
    )
    database.execute(
        'GRANT UPDATE (status, decided_at, reviewed_by, decision_basis_code, '
        'evidence_digest, rejection_reason_code) '
        f'ON TABLE public.identity_verification_submission TO "{writer_role}"'
    )
    database.execute(
        'REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
        f'ON TABLE public.identity_verification_submission FROM "{readonly_role}", "{audit_role}"'
    )
    database.execute(f'GRANT USAGE ON SCHEMA public TO "{fact_writer_role}"')
    database.execute(f'REVOKE CREATE ON SCHEMA public FROM "{fact_writer_role}"')
    database.execute(
        'GRANT SELECT, INSERT ON TABLE public.canonical_health_fact, '
        f'public.operation_log TO "{fact_writer_role}"'
    )
    database.execute(
        'REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLE '
        'public.canonical_health_fact, public.operation_log '
        f'FROM "{fact_writer_role}"'
    )
    database.execute(
        'GRANT USAGE, SELECT ON SEQUENCE public.canonical_health_fact_id_seq, '
        f'public.operation_log_id_seq TO "{fact_writer_role}"'
    )
    database.execute(
        'REVOKE UPDATE ON SEQUENCE public.canonical_health_fact_id_seq, '
        f'public.operation_log_id_seq FROM "{fact_writer_role}"'
    )
    database.execute(
        'REVOKE SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
        'ON TABLE public.canonical_health_fact '
        f'FROM "{application_role}", "{readonly_role}"'
    )
    database.execute(
        'REVOKE ALL ON SEQUENCE public.canonical_health_fact_id_seq '
        f'FROM "{application_role}", "{readonly_role}"'
    )
    database.execute(
        f'REVOKE ALL ON TABLE public.alembic_version FROM "{fact_writer_role}"'
    )
    mapping_tables = "public.organization_legacy_mapping, public.health_indicator_legacy_mapping"
    for role in (organization_mapping_role, health_mapping_role, mapping_audit_role, mapping_shadow_role):
        database.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        database.execute(f'REVOKE CREATE ON SCHEMA public FROM "{role}"')
        database.execute(f'REVOKE ALL ON TABLE public.alembic_version FROM "{role}"')
    database.execute(
        'GRANT SELECT (id, org_id, status) ON TABLE public.tenant '
        f'TO "{organization_mapping_role}"'
    )
    database.execute(
        'GRANT SELECT (id, parent_id, org_type, status, version) ON TABLE public.platform_org '
        f'TO "{organization_mapping_role}"'
    )
    database.execute(
        'GRANT SELECT, INSERT ON TABLE public.organization_legacy_mapping, public.operation_log '
        f'TO "{organization_mapping_role}"'
    )
    database.execute(
        'GRANT USAGE, SELECT ON SEQUENCE public.organization_legacy_mapping_id_seq, '
        f'public.operation_log_id_seq TO "{organization_mapping_role}"'
    )
    database.execute(
        'GRANT SELECT (id, user_id, indicator_type, value, unit, source, recorded_at, created_at, batch_id) '
        f'ON TABLE public.health_indicator TO "{health_mapping_role}", "{mapping_shadow_role}"'
    )
    database.execute(
        'GRANT SELECT, INSERT ON TABLE public.health_indicator_legacy_mapping, '
        f'public.canonical_health_fact, public.operation_log TO "{health_mapping_role}"'
    )
    database.execute(
        'GRANT USAGE, SELECT ON SEQUENCE public.health_indicator_legacy_mapping_id_seq, '
        f'public.canonical_health_fact_id_seq, public.operation_log_id_seq TO "{health_mapping_role}"'
    )
    database.execute(
        f'GRANT SELECT ON TABLE {mapping_tables}, public.operation_log TO "{mapping_audit_role}"'
    )
    database.execute(
        f'GRANT SELECT ON TABLE {mapping_tables} TO "{mapping_shadow_role}"'
    )
    database.execute(
        'GRANT SELECT (id, subject_user_id, indicator_code, numeric_value, unit, '
        'measured_at, source_type, producer_event_key, received_at) '
        f'ON TABLE public.canonical_health_fact TO "{mapping_shadow_role}"'
    )
    database.execute(
        'GRANT SELECT (id, org_id, status) ON TABLE public.tenant '
        f'TO "{mapping_shadow_role}"'
    )
    database.execute(
        'GRANT SELECT (id, parent_id, org_type, status, version) ON TABLE public.platform_org '
        f'TO "{mapping_shadow_role}"'
    )
    for role in (organization_mapping_role, health_mapping_role):
        database.execute(
            f'REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLE {mapping_tables}, '
            f'public.canonical_health_fact, public.operation_log FROM "{role}"'
        )
    for role in (mapping_audit_role, mapping_shadow_role):
        database.execute(
            f'REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLE {mapping_tables}, '
            f'public.canonical_health_fact, public.operation_log FROM "{role}"'
        )
        database.execute(
            'REVOKE ALL ON SEQUENCE public.organization_legacy_mapping_id_seq, '
            'public.health_indicator_legacy_mapping_id_seq, public.canonical_health_fact_id_seq, '
            f'public.operation_log_id_seq FROM "{role}"'
        )
    database.execute(
        f'REVOKE SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLE {mapping_tables} '
        f'FROM "{application_role}", "{readonly_role}", "{fact_writer_role}"'
    )
    projection_tables = (
        "public.organization_projection_generation, public.organization_projection_checkpoint, "
        "public.organization_projection, public.health_projection_generation, "
        "public.health_projection_checkpoint, public.health_projection_fact, "
        "public.health_projection_window_selection"
    )
    projection_existing_roles = (
        application_role, readonly_role, writer_role, worker_role, audit_role,
        fact_writer_role, organization_mapping_role, health_mapping_role,
        mapping_audit_role, mapping_shadow_role,
    )
    for role in projection_existing_roles:
        database.execute(
            'REVOKE SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
            f'ON {projection_tables} FROM "{role}"'
        )
        database.execute(
            'REVOKE ALL ON SEQUENCE public.organization_projection_generation_id_seq, '
            f'public.health_projection_generation_id_seq FROM "{role}"'
        )


@pytest.fixture(scope="module")
def pg_database():
    _, target = _get_test_database_target()
    migration_database_url = _get_test_database_url()
    database = PgDatabase(migration_database_url)

    migration_role = None
    if os.getenv("KG_TEST_ROLE_SEPARATION") == "1":
        application_role = _validated_role_name("KG_TEST_APPLICATION_ROLE")
        migration_role = _validated_role_name("KG_TEST_MIGRATION_ROLE")
        readonly_role = _validated_role_name("KG_TEST_READONLY_ROLE")
        ddl_owner_role = _validated_role_name("KG_TEST_DDL_OWNER_ROLE")
        writer_role = _validated_role_name("KG_TEST_VERIFICATION_WRITER_ROLE")
        worker_role = _validated_role_name("KG_TEST_DELIVERY_WORKER_ROLE")
        audit_role = _validated_role_name("KG_TEST_OUTBOX_AUDIT_ROLE")
        fact_writer_role = _validated_role_name("KG_TEST_HEALTH_FACT_WRITER_ROLE")
        organization_mapping_role = _validated_role_name("KG_TEST_ORGANIZATION_MAPPING_WRITER_ROLE")
        health_mapping_role = _validated_role_name("KG_TEST_HEALTH_MAPPING_WRITER_ROLE")
        mapping_audit_role = _validated_role_name("KG_TEST_MAPPING_AUDIT_ROLE")
        mapping_shadow_role = _validated_role_name("KG_TEST_MAPPING_SHADOW_ROLE")
        organization_projection_role = _validated_role_name("KG_TEST_ORGANIZATION_PROJECTION_BUILDER_ROLE")
        health_projection_role = _validated_role_name("KG_TEST_HEALTH_PROJECTION_BUILDER_ROLE")
        projection_confirmation_role = _validated_role_name("KG_TEST_PROJECTION_CONFIRMATION_ROLE")
        organization_shadow_role = _validated_role_name("KG_TEST_ORGANIZATION_PROJECTION_SHADOW_ROLE")
        health_shadow_role = _validated_role_name("KG_TEST_HEALTH_PROJECTION_SHADOW_ROLE")
        ready_gate_role = _validated_role_name("KG_TEST_PROJECTION_READY_GATE_ROLE")
        shadow_confirmation_role = _validated_role_name("KG_TEST_PROJECTION_SHADOW_CONFIRMATION_ROLE")
        organization_reader_role = _validated_role_name("KG_TEST_ORGANIZATION_PROJECTION_READER_ROLE")
        health_reader_role = _validated_role_name("KG_TEST_HEALTH_PROJECTION_READER_ROLE")
        onboarding_writer_role = _validated_role_name("KG_TEST_INSTITUTION_ONBOARDING_WRITER_ROLE")
        institution_review_role = _validated_role_name("KG_TEST_INSTITUTION_REVIEW_WRITER_ROLE")
        private_file_role = _validated_role_name("KG_TEST_PRIVATE_FILE_WRITER_ROLE")
        private_file_access_role = _validated_role_name(
            "KG_TEST_PRIVATE_FILE_ACCESS_WRITER_ROLE"
        )
        onboarding_reader_role = _validated_role_name("KG_TEST_INSTITUTION_ONBOARDING_READER_ROLE")
        therapist_onboarding_role = _validated_role_name("KG_TEST_THERAPIST_ONBOARDING_WRITER_ROLE")
        therapist_review_role = _validated_role_name("KG_TEST_THERAPIST_REVIEW_WRITER_ROLE")
        therapist_worker_role = _validated_role_name("KG_TEST_THERAPIST_READINESS_WORKER_ROLE")
        therapist_reader_role = _validated_role_name("KG_TEST_THERAPIST_READER_ROLE")
        member_enrollment_role = _validated_role_name("KG_TEST_MEMBER_ENROLLMENT_WRITER_ROLE")
        member_review_role = _validated_role_name("KG_TEST_MEMBER_IDENTITY_REVIEW_WRITER_ROLE")
        member_case_role = _validated_role_name("KG_TEST_MEMBER_CASE_WRITER_ROLE")
        member_worker_role = _validated_role_name("KG_TEST_MEMBER_WORKFLOW_WORKER_ROLE")
        member_reader_role = _validated_role_name("KG_TEST_MEMBER_ENROLLMENT_READER_ROLE")
        health_record_role = _validated_role_name("KG_TEST_HEALTH_RECORD_WRITER_ROLE")
        assessment_role = _validated_role_name("KG_TEST_ASSESSMENT_READINESS_WRITER_ROLE")
        slice4_worker_role = _validated_role_name("KG_TEST_SLICE4_WORKFLOW_WORKER_ROLE")
        clinical_reader_role = _validated_role_name("KG_TEST_SLICE4_CLINICAL_READER_ROLE")
        institution_reader_role = _validated_role_name("KG_TEST_SLICE4_INSTITUTION_READER_ROLE")
        identity_authority_role = _validated_role_name("KG_TEST_SLICE4_IDENTITY_AUTHORITY_ROLE")
        slice5_assessment_role = _validated_role_name("KG_TEST_SLICE5_ASSESSMENT_WRITER_ROLE")
        slice5_risk_role = _validated_role_name("KG_TEST_SLICE5_RISK_WORKFLOW_WRITER_ROLE")
        slice5_rule_role = _validated_role_name("KG_TEST_SLICE5_RULE_GOVERNANCE_WRITER_ROLE")
        slice5_worker_role = _validated_role_name("KG_TEST_SLICE5_WORKFLOW_WORKER_ROLE")
        slice5_clinical_role = _validated_role_name("KG_TEST_SLICE5_CLINICAL_READER_ROLE")
        slice5_oversight_role = _validated_role_name("KG_TEST_SLICE5_OVERSIGHT_READER_ROLE")
        slice6_institution_role = _validated_role_name("KG_TEST_SLICE6_INSTITUTION_WRITER_ROLE")
        slice6_template_role = _validated_role_name("KG_TEST_SLICE6_TEMPLATE_WRITER_ROLE")
        slice6_review_role = _validated_role_name("KG_TEST_SLICE6_REVIEW_WRITER_ROLE")
        slice6_worker_role = _validated_role_name("KG_TEST_SLICE6_WORKFLOW_WORKER_ROLE")
        slice6_clinical_role = _validated_role_name("KG_TEST_SLICE6_CLINICAL_READER_ROLE")
        slice6_family_role = _validated_role_name("KG_TEST_SLICE6_FAMILY_READER_ROLE")
        slice7_milestone_role = _validated_role_name("KG_TEST_SLICE7_MILESTONE_WRITER_ROLE")
        slice7_case_role = _validated_role_name("KG_TEST_SLICE7_CASE_WRITER_ROLE")
        slice7_transfer_role = _validated_role_name("KG_TEST_SLICE7_TRANSFER_WRITER_ROLE")
        slice7_export_role = _validated_role_name("KG_TEST_SLICE7_EXPORT_WORKER_ROLE")
        slice7_family_role = _validated_role_name("KG_TEST_SLICE7_FAMILY_READER_ROLE")
        slice7_oversight_role = _validated_role_name("KG_TEST_SLICE7_OVERSIGHT_READER_ROLE")
        a2_identity_inventory_role = _validated_role_name(
            "KG_TEST_A2_IDENTITY_INVENTORY_ROLE"
        )
        a2_identity_remediation_writer_role = _validated_role_name(
            "KG_TEST_A2_IDENTITY_REMEDIATION_WRITER_ROLE"
        )
        a2_identity_remediation_confirmation_role = _validated_role_name(
            "KG_TEST_A2_IDENTITY_REMEDIATION_CONFIRMATION_ROLE"
        )
        roles = (
            application_role,
            migration_role,
            readonly_role,
            ddl_owner_role,
            writer_role,
            worker_role,
            audit_role,
            fact_writer_role,
            organization_mapping_role,
            health_mapping_role,
            mapping_audit_role,
            mapping_shadow_role,
            organization_projection_role,
            health_projection_role,
            projection_confirmation_role,
            organization_shadow_role,
            health_shadow_role,
            ready_gate_role,
            shadow_confirmation_role,
            organization_reader_role,
            health_reader_role,
            onboarding_writer_role,
            institution_review_role,
            private_file_role,
            private_file_access_role,
            onboarding_reader_role,
            therapist_onboarding_role,
            therapist_review_role,
            therapist_worker_role,
            therapist_reader_role,
            member_enrollment_role,
            member_review_role,
            member_case_role,
            member_worker_role,
            member_reader_role,
            health_record_role,
            assessment_role,
            slice4_worker_role,
            clinical_reader_role,
            institution_reader_role,
            identity_authority_role,
            slice5_assessment_role,
            slice5_risk_role,
            slice5_rule_role,
            slice5_worker_role,
            slice5_clinical_role,
            slice5_oversight_role,
            slice6_institution_role,
            slice6_template_role,
            slice6_review_role,
            slice6_worker_role,
            slice6_clinical_role,
            slice6_family_role,
            slice7_milestone_role,
            slice7_case_role,
            slice7_transfer_role,
            slice7_export_role,
            slice7_family_role,
            slice7_oversight_role,
            a2_identity_inventory_role,
            a2_identity_remediation_writer_role,
            a2_identity_remediation_confirmation_role,
        )
        if len(set(roles)) != len(roles):
            raise RuntimeError("database validation roles must be distinct")
        if "postgres" in roles:
            raise RuntimeError("database validation roles must not use postgres")

    sentinel = database.fetch_value(
        "SELECT shobj_description(oid, 'pg_database') "
        "FROM pg_database WHERE datname = current_database()"
    )
    validate_database_sentinel(sentinel, target)

    if migration_role is not None:
        connected_role = database.fetch_value("SELECT current_user")
        if connected_role != migration_role:
            raise RuntimeError(
                "migration database URL role does not match KG_TEST_MIGRATION_ROLE"
            )

    if os.getenv("KG_TEST_SCHEMA_PREPARED") == "1":
        database_owner = database.fetch_value(
            "SELECT pg_get_userbyid(datdba) FROM pg_database "
            "WHERE datname = current_database()"
        )
        schema_owner = database.fetch_value(
            "SELECT schema_owner FROM information_schema.schemata "
            "WHERE schema_name = 'public'"
        )
        has_owner_membership = database.fetch_value(
            f"SELECT pg_has_role(current_user, '{ddl_owner_role}', 'MEMBER')"
        )
        has_business_privilege = database.fetch_value(
            "SELECT has_table_privilege(current_user, "
            "'public.registration_verified_outbox', 'SELECT') OR "
            "has_table_privilege(current_user, "
            "'public.registration_verified_outbox', 'INSERT') OR "
            "has_table_privilege(current_user, "
            "'public.registration_verified_outbox', 'UPDATE') OR "
            "has_table_privilege(current_user, "
            "'public.registration_verified_outbox', 'DELETE')"
        )
        if (
            database_owner != ddl_owner_role
            or schema_owner != ddl_owner_role
            or has_owner_membership
            or has_business_privilege
        ):
            raise RuntimeError(
                "migration steady-state privileges are not isolated"
            )
        current_revision = database.fetch_value(
            "SELECT version_num FROM alembic_version"
        )
        if current_revision != REQUIRED_HEAD_REVISION:
            raise RuntimeError(
                f"expected Alembic head {REQUIRED_HEAD_REVISION}, got {current_revision}"
            )
        yield database
        return

    database.execute("DROP SCHEMA IF EXISTS identity CASCADE")
    database.execute("DROP SCHEMA IF EXISTS public CASCADE")
    database.execute("CREATE SCHEMA public")
    _propagate_module_d_role_preflight_environment()
    command.upgrade(_build_alembic_config(migration_database_url), "head")

    current_revision = database.fetch_value("SELECT version_num FROM alembic_version")
    if current_revision != REQUIRED_HEAD_REVISION:
        raise RuntimeError(f"expected Alembic head {REQUIRED_HEAD_REVISION}, got {current_revision}")
    _grant_test_role_permissions(database)

    yield database


@pytest.fixture(scope="module")
def application_database(pg_database):
    del pg_database
    database = PgDatabase(_get_application_database_url())
    if os.getenv("KG_TEST_ROLE_SEPARATION") == "1":
        connected_role = database.fetch_value("SELECT current_user")
        if connected_role != _validated_role_name("KG_TEST_APPLICATION_ROLE"):
            raise RuntimeError("application database URL role does not match KG_TEST_APPLICATION_ROLE")
    return database


@pytest.fixture(scope="module")
def readonly_database(pg_database):
    del pg_database
    database = PgDatabase(_get_readonly_database_url())
    if os.getenv("KG_TEST_ROLE_SEPARATION") == "1":
        connected_role = database.fetch_value("SELECT current_user")
        if connected_role != _validated_role_name("KG_TEST_READONLY_ROLE"):
            raise RuntimeError("readonly database URL role does not match KG_TEST_READONLY_ROLE")
    return database


@pytest.fixture(scope="module")
def outbox_audit_database(pg_database):
    del pg_database
    database = PgDatabase(_get_outbox_audit_database_url())
    if os.getenv("KG_TEST_ROLE_SEPARATION") == "1":
        connected_role = database.fetch_value("SELECT current_user")
        if connected_role != _validated_role_name("KG_TEST_OUTBOX_AUDIT_ROLE"):
            raise RuntimeError("outbox audit database URL role does not match KG_TEST_OUTBOX_AUDIT_ROLE")
    return database


@pytest.fixture(scope="module")
def health_fact_writer_database(pg_database):
    del pg_database
    database = PgDatabase(_get_health_fact_writer_database_url())
    if os.getenv("KG_TEST_ROLE_SEPARATION") == "1":
        connected_role = database.fetch_value("SELECT current_user")
        if connected_role != _validated_role_name(
            "KG_TEST_HEALTH_FACT_WRITER_ROLE"
        ):
            raise RuntimeError(
                "health fact writer database URL role does not match "
                "KG_TEST_HEALTH_FACT_WRITER_ROLE"
            )
    return database


@pytest.fixture(scope="module")
def organization_mapping_writer_database(pg_database):
    del pg_database
    database = PgDatabase(_get_organization_mapping_writer_database_url())
    if os.getenv("KG_TEST_ROLE_SEPARATION") == "1" and database.fetch_value("SELECT current_user") != _validated_role_name("KG_TEST_ORGANIZATION_MAPPING_WRITER_ROLE"):
        raise RuntimeError("organization mapping writer database role mismatch")
    return database


@pytest.fixture(scope="module")
def health_mapping_writer_database(pg_database):
    del pg_database
    database = PgDatabase(_get_health_mapping_writer_database_url())
    if os.getenv("KG_TEST_ROLE_SEPARATION") == "1" and database.fetch_value("SELECT current_user") != _validated_role_name("KG_TEST_HEALTH_MAPPING_WRITER_ROLE"):
        raise RuntimeError("health mapping writer database role mismatch")
    return database


@pytest.fixture(scope="module")
def mapping_audit_database(pg_database):
    del pg_database
    return PgDatabase(_get_mapping_audit_database_url())


@pytest.fixture(scope="module")
def mapping_shadow_database(pg_database):
    del pg_database
    return PgDatabase(_get_mapping_shadow_database_url())


@pytest.fixture(scope="module")
def member_enrollment_writer_database(pg_database):
    del pg_database
    return PgDatabase(_get_member_enrollment_writer_database_url())


@pytest.fixture(scope="module")
def member_identity_review_writer_database(pg_database):
    del pg_database
    return PgDatabase(_get_member_identity_review_writer_database_url())


@pytest.fixture(scope="module")
def member_case_writer_database(pg_database):
    del pg_database
    return PgDatabase(_get_member_case_writer_database_url())


@pytest.fixture(scope="module")
def member_workflow_worker_database(pg_database):
    del pg_database
    return PgDatabase(_get_member_workflow_worker_database_url())


@pytest.fixture(scope="module")
def member_enrollment_reader_database(pg_database):
    del pg_database
    return PgDatabase(_get_member_enrollment_reader_database_url())


@pytest.fixture(scope="module")
def health_record_writer_database(pg_database):
    del pg_database
    return PgDatabase(_get_health_record_writer_database_url())


@pytest.fixture(scope="module")
def assessment_readiness_writer_database(pg_database):
    del pg_database
    return PgDatabase(_get_assessment_readiness_writer_database_url())


@pytest.fixture(scope="module")
def slice4_workflow_worker_database(pg_database):
    del pg_database
    return PgDatabase(_get_slice4_workflow_worker_database_url())


@pytest.fixture(scope="module")
def slice4_clinical_reader_database(pg_database):
    del pg_database
    return PgDatabase(_get_slice4_clinical_reader_database_url())


@pytest.fixture(scope="module")
def slice4_institution_reader_database(pg_database):
    del pg_database
    return PgDatabase(_get_slice4_institution_reader_database_url())


@pytest.fixture(scope="module")
def slice4_identity_authority_database(pg_database):
    del pg_database
    return PgDatabase(_get_slice4_identity_authority_database_url())


@pytest.fixture(scope="module")
def slice5_assessment_writer_database(pg_database):
    del pg_database
    return PgDatabase(_get_slice5_assessment_writer_database_url())


@pytest.fixture(scope="module")
def slice5_risk_workflow_writer_database(pg_database):
    del pg_database
    return PgDatabase(_get_slice5_risk_workflow_writer_database_url())


@pytest.fixture(scope="module")
def slice5_rule_governance_writer_database(pg_database):
    del pg_database
    return PgDatabase(_get_slice5_rule_governance_writer_database_url())


@pytest.fixture(scope="module")
def slice5_workflow_worker_database(pg_database):
    del pg_database
    return PgDatabase(_get_slice5_workflow_worker_database_url())


@pytest.fixture(scope="module")
def slice5_clinical_reader_database(pg_database):
    del pg_database
    return PgDatabase(_get_slice5_clinical_reader_database_url())


@pytest.fixture(scope="module")
def slice5_oversight_reader_database(pg_database):
    del pg_database
    return PgDatabase(_get_slice5_oversight_reader_database_url())


@pytest.fixture(scope="module")
def slice6_institution_writer_database(pg_database):
    del pg_database
    return PgDatabase(_get_slice6_institution_writer_database_url())


@pytest.fixture(scope="module")
def slice6_template_writer_database(pg_database):
    del pg_database
    return PgDatabase(_get_slice6_template_writer_database_url())


@pytest.fixture(scope="module")
def slice6_review_writer_database(pg_database):
    del pg_database
    return PgDatabase(_get_slice6_review_writer_database_url())


@pytest.fixture(scope="module")
def slice6_workflow_worker_database(pg_database):
    del pg_database
    return PgDatabase(_get_slice6_workflow_worker_database_url())


@pytest.fixture(scope="module")
def slice6_clinical_reader_database(pg_database):
    del pg_database
    return PgDatabase(_get_slice6_clinical_reader_database_url())


@pytest.fixture(scope="module")
def slice6_family_reader_database(pg_database):
    del pg_database
    return PgDatabase(_get_slice6_family_reader_database_url())


@pytest.fixture(scope="module")
def slice7_milestone_writer_database(pg_database):
    del pg_database
    return PgDatabase(_get_slice7_milestone_writer_database_url())


@pytest.fixture(scope="module")
def slice7_case_writer_database(pg_database):
    del pg_database
    return PgDatabase(_get_slice7_case_writer_database_url())


@pytest.fixture(scope="module")
def slice7_transfer_writer_database(pg_database):
    del pg_database
    return PgDatabase(_get_slice7_transfer_writer_database_url())


@pytest.fixture(scope="module")
def slice7_export_worker_database(pg_database):
    del pg_database
    return PgDatabase(_get_slice7_export_worker_database_url())


@pytest.fixture(scope="module")
def slice7_family_reader_database(pg_database):
    del pg_database
    return PgDatabase(_get_slice7_family_reader_database_url())


@pytest.fixture(scope="module")
def slice7_oversight_reader_database(pg_database):
    del pg_database
    return PgDatabase(_get_slice7_oversight_reader_database_url())


@pytest.fixture(scope="module")
def a2_identity_inventory_database(pg_database):
    del pg_database
    database = PgDatabase(_get_a2_identity_inventory_database_url())
    if os.getenv("KG_TEST_ROLE_SEPARATION") == "1":
        connected_role = database.fetch_value("SELECT current_user")
        expected_role = _validated_role_name("KG_TEST_A2_IDENTITY_INVENTORY_ROLE")
        if connected_role != expected_role:
            raise RuntimeError("A2 identity inventory database role mismatch")
    return database


@pytest.fixture(scope="module")
def a2_identity_remediation_writer_database(pg_database):
    del pg_database
    database = PgDatabase(_get_a2_identity_remediation_writer_database_url())
    if os.getenv("KG_TEST_ROLE_SEPARATION") == "1":
        connected_role = database.fetch_value("SELECT current_user")
        expected_role = _validated_role_name(
            "KG_TEST_A2_IDENTITY_REMEDIATION_WRITER_ROLE"
        )
        if connected_role != expected_role:
            raise RuntimeError("A2 identity remediation writer role mismatch")
    return database


@pytest.fixture(scope="module")
def a2_identity_remediation_confirmation_database(pg_database):
    del pg_database
    database = PgDatabase(
        _get_a2_identity_remediation_confirmation_database_url()
    )
    if os.getenv("KG_TEST_ROLE_SEPARATION") == "1":
        connected_role = database.fetch_value("SELECT current_user")
        expected_role = _validated_role_name(
            "KG_TEST_A2_IDENTITY_REMEDIATION_CONFIRMATION_ROLE"
        )
        if connected_role != expected_role:
            raise RuntimeError("A2 identity remediation confirmation role mismatch")
    return database


@pytest.fixture
def real_db_client(pg_database):
    database_url = _get_application_database_url()

    from fastapi.testclient import TestClient
    from sqlalchemy.pool import NullPool
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.database import get_db_session
    from app.main import create_app

    engine = create_async_engine(database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app()

    async def override_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    try:
        with TestClient(app, client=('127.0.0.1', 50000)) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())
