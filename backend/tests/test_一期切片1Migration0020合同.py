from pathlib import Path


MIGRATION = Path(__file__).parents[1] / "app" / "migrations" / "versions" / "20260816_0020_phase1_slice1_controlled_institution_onboarding.py"


def test_0020线性继承0019且只增加切片1对象():
    source = MIGRATION.read_text("utf-8")
    assert 'revision = "20260816_0020"' in source
    assert 'down_revision = "20260815_0019"' in source
    for table in ("institution_invitation", "institution_application", "institution_application_revision", "institution_license", "private_file", "institution_onboarding_idempotency", "institution_onboarding_audit", "institution_onboarding_outbox", "institution_onboarding_delivery"):
        assert f'op.create_table("{table}"' in source
    for forbidden in ("ACTIVE_PROJECTION", "read_cutover", "health_plan", "payment_settlement"):
        assert forbidden not in source


def test_四身份预检最小授权与空表降级门禁():
    source = MIGRATION.read_text("utf-8")
    assert source.count("_roles()") >= 2
    assert "len(set(configured.values())) != len(configured)" in source
    assert "REVOKE ALL ON ALL TABLES" in source
    assert "_grant_columns" in source
    assert "GRANT SELECT, INSERT, UPDATE ON public.institution" not in source
    assert "LOCK TABLE " in source and "ACCESS EXCLUSIVE MODE" in source
    assert "Slice 1 downgrade requires empty tables" in source
    assert "CASCADE" not in source
    assert "make_url" in source
    assert "SELECT current_user" in source
    for identity in (
        "KG_VERIFICATION_WRITER_ROLE",
        "KG_DELIVERY_WORKER_ROLE",
        "KG_ORGANIZATION_PROJECTION_READER_ROLE",
        "KG_HEALTH_PROJECTION_READER_ROLE",
    ):
        assert identity in source


def test_四运行身份在DDL前完成全局双向membership隔离预检():
    source = MIGRATION.read_text("utf-8")
    membership = source.split(
        "def _slice_role_membership_is_unsafe", 1
    )[1].split("def _roles", 1)[0]
    preflight = source.split("def _roles()", 1)[1].split("def _uuid", 1)[0]
    assert "WITH RECURSIVE role_paths" in membership
    assert "source_oid=ANY(:slice_roles) OR target_oid=ANY(:slice_roles)" in membership
    assert "NOT membership.roleid=ANY(role_paths.path)" in membership
    assert "_slice_role_membership_is_unsafe(connection, values)" in preflight
    assert 'row["rolinherit"]' in preflight


def test_幂等表以业务身份为复合主键且不创建代理序列():
    source = MIGRATION.read_text("utf-8")
    assert "institution_onboarding_idempotency_id_seq" not in source
    assert source.count('primary_key=True') >= 3
    assert '("id",)+idempotency_columns' not in source


def test_文件访问签发只授Reader必要完整性列且通知交付目标最小授权():
    source = MIGRATION.read_text("utf-8")
    reader_grant = source.split(
        '_grant_columns(connection, reader, "SELECT", "private_file"', 1
    )[1].split("\n", 1)[0]
    assert "actual_size" in reader_grant
    assert "actual_sha256" in reader_grant
    assert "object_key" not in reader_grant
    assert '_grant_columns(connection, reader, "UPDATE"' not in source
    assert 'institution_onboarding_delivery' in source
    assert 'fk_onboarding_delivery_outbox' in source
    assert "attempts BETWEEN 0 AND 3" in source
    assert "status='PROCESSING' AND processing_at IS NOT NULL" in source
    assert "status='DELIVERED' AND processing_at IS NOT NULL AND delivered_at IS NOT NULL" in source
