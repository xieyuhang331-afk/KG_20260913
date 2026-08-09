import importlib.util
from pathlib import Path

from app.core.database import Base


CORE_TABLES = {
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
MEMBER_TABLE = "identity.member"
MEMBER_NO_ALLOCATION_TABLE = "identity.member_no_allocation"
REGISTRATION_OUTBOX_TABLE = "public.registration_verified_outbox"
IDENTITY_SUBMISSION_TABLE = "public.identity_verification_submission"
ELIGIBILITY_EVIDENCE_TABLES = {
    "public.identity_verification_decision",
    "public.user_account_classification_decision",
    "public.registration_eligibility_decision",
}
IDENTITY_BOOTSTRAP_TABLES = {
    "identity.user_member_self_link",
    "identity.registration_bootstrap_record",
}


def test_alembic_target_metadata_registers_identity_member():
    env_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "migrations"
        / "env.py"
    )
    spec = importlib.util.spec_from_file_location(
        "p2_identity_member_alembic_env",
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
    assert MEMBER_TABLE in registered_tables, (
        "Alembic target_metadata is missing approved table: "
        "identity.member"
    )
    assert MEMBER_NO_ALLOCATION_TABLE in registered_tables, (
        "Alembic target_metadata is missing approved table: "
        "identity.member_no_allocation"
    )
    assert registered_tables == CORE_TABLES | {
        MEMBER_TABLE,
        MEMBER_NO_ALLOCATION_TABLE,
        REGISTRATION_OUTBOX_TABLE,
        IDENTITY_SUBMISSION_TABLE,
    } | ELIGIBILITY_EVIDENCE_TABLES | IDENTITY_BOOTSTRAP_TABLES
