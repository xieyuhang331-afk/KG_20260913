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
IDENTITY_TABLES = {
    "identity.member",
    "identity.member_no_allocation",
    "identity.registration_bootstrap_record",
    "identity.user_member_self_link",
    "public.identity_verification_decision",
    "public.registration_eligibility_decision",
    "public.user_account_classification_decision",
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
    assert IDENTITY_TABLES <= registered_tables, (
        "Alembic target_metadata is missing an approved identity table"
    )
    assert registered_tables == CORE_TABLES | IDENTITY_TABLES
