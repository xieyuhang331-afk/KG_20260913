from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "app/migrations/versions/20260815_0019_p3_ready_projection_internal_read_boundary.py"


def test_0019线性且只创建五个安全视图():
    text = MIGRATION.read_text("utf-8")
    assert 'revision = "20260815_0019"' in text
    assert 'down_revision = "20260814_0018"' in text
    assert text.count("CREATE VIEW public.") == 5
    assert text.count("security_barrier=true") == 5
    assert "SELECT *" not in text.upper()
    assert "CASCADE" not in text.upper()


def test_0019权限与downgrade锁合同():
    text = MIGRATION.read_text("utf-8")
    assert "KG_ORGANIZATION_PROJECTION_READER_ROLE" in text
    assert "KG_HEALTH_PROJECTION_READER_ROLE" in text
    assert "pg_advisory_xact_lock" in text
    assert "pg_advisory_unlock" not in text
    assert "pg_advisory_lock(" not in text
    assert "DROP VIEW public.health_ready_projection_window_selection_v1" in text
    assert "GRANT INSERT" not in text and "GRANT UPDATE" not in text and "GRANT DELETE" not in text


def test_0019真实运行身份同时绑定ROLE与DatabaseURL且不读取TEST变量():
    text = MIGRATION.read_text("utf-8")
    required_pairs = (
        ("KG_DATABASE_USER", "KG_IDENTITY_APPLICATION_DATABASE_URL"),
        ("KG_READONLY_ROLE", "KG_READONLY_DATABASE_URL"),
        ("KG_VERIFICATION_WRITER_ROLE", "KG_VERIFICATION_WRITER_DATABASE_URL"),
        ("KG_DELIVERY_WORKER_ROLE", "KG_DELIVERY_WORKER_DATABASE_URL"),
        ("KG_OUTBOX_AUDIT_ROLE", "KG_OUTBOX_AUDIT_DATABASE_URL"),
        ("KG_HEALTH_FACT_WRITER_ROLE", "KG_HEALTH_FACT_WRITER_DATABASE_URL"),
        ("KG_ORGANIZATION_MAPPING_WRITER_ROLE", "KG_ORGANIZATION_MAPPING_WRITER_DATABASE_URL"),
        ("KG_HEALTH_MAPPING_WRITER_ROLE", "KG_HEALTH_MAPPING_WRITER_DATABASE_URL"),
        ("KG_MAPPING_AUDIT_ROLE", "KG_MAPPING_AUDIT_DATABASE_URL"),
        ("KG_MAPPING_SHADOW_ROLE", "KG_MAPPING_SHADOW_DATABASE_URL"),
        ("KG_ORGANIZATION_PROJECTION_BUILDER_ROLE", "KG_ORGANIZATION_PROJECTION_BUILDER_DATABASE_URL"),
        ("KG_HEALTH_PROJECTION_BUILDER_ROLE", "KG_HEALTH_PROJECTION_BUILDER_DATABASE_URL"),
        ("KG_PROJECTION_CONFIRMATION_ROLE", "KG_PROJECTION_CONFIRMATION_DATABASE_URL"),
        ("KG_ORGANIZATION_PROJECTION_SHADOW_ROLE", "KG_ORGANIZATION_PROJECTION_SHADOW_DATABASE_URL"),
        ("KG_HEALTH_PROJECTION_SHADOW_ROLE", "KG_HEALTH_PROJECTION_SHADOW_DATABASE_URL"),
        ("KG_PROJECTION_READY_GATE_ROLE", "KG_PROJECTION_READY_GATE_DATABASE_URL"),
        ("KG_PROJECTION_SHADOW_CONFIRMATION_ROLE", "KG_PROJECTION_SHADOW_CONFIRMATION_DATABASE_URL"),
        ("KG_ORGANIZATION_PROJECTION_READER_ROLE", "KG_ORGANIZATION_PROJECTION_READER_DATABASE_URL"),
        ("KG_HEALTH_PROJECTION_READER_ROLE", "KG_HEALTH_PROJECTION_READER_DATABASE_URL"),
    )
    for role_name, url_name in required_pairs:
        assert role_name in text
        assert url_name in text
    assert "make_url" in text
    assert "KG_TEST_" not in text


def test_0019在DDL前验证双向递归membership闭包且防环():
    text = MIGRATION.read_text("utf-8")
    assert "WITH RECURSIVE role_paths" in text
    assert "pg_auth_members" in text
    assert "NOT membership.roleid = ANY(role_paths.path)" in text
    assert "source_oid=ANY(:readers) OR target_oid=ANY(:readers)" in text
    assert "target_oid=ANY(:isolated)" not in text
    upgrade = text.index("def upgrade")
    assert text.index("_roles(connection)", upgrade) < text.index(
        "_create_views()", upgrade
    )
