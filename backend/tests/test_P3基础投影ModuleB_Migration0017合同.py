from pathlib import Path


def test_0017_has_operation_identity_and_downgrade_revokes_every_added_surface():
    source = Path(
        "app/migrations/versions/20260813_0017_p3_basic_projection_builder_foundation.py"
    ).read_text(encoding="utf-8")
    assert "uq_operation_log_basic_projection_builder_operation_id" in source
    assert "start_operation_id" in source
    assert "digest_key_id" in source
    assert "REVOKE" in source
    assert "ACCESS EXCLUSIVE" in source
    assert "basic_projection_builder" in source
    assert "jsonb_object_length" not in source
    assert "jsonb_build_object" in source
    assert "CASCADE" not in source.split("def downgrade", 1)[1]


def test_integration_fixture_revokes_existing_roles_from_projection_tables():
    source = Path("tests/integration/conftest.py").read_text(encoding="utf-8")
    assert "projection_existing_roles" in source
    assert "REVOKE SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER" in source


def test_0017_owns_projection_role_permissions_and_fixture_does_not_grant_them():
    migration = Path("app/migrations/versions/20260813_0017_p3_basic_projection_builder_foundation.py").read_text(encoding="utf-8")
    fixture = Path("tests/integration/conftest.py").read_text(encoding="utf-8")
    for token in (
        "organization_projection_builder_role", "health_projection_builder_role",
        "projection_confirmation_role", "GRANT SELECT (", "GRANT INSERT (",
        "REVOKE ALL PRIVILEGES", "pg_roles",
    ):
        assert token in migration
    projection_section = fixture.split("projection_tables =", 1)[1].split("@pytest.fixture", 1)[0]
    assert "GRANT SELECT,INSERT,UPDATE ON public.organization_projection_generation" not in projection_section
    assert "GRANT SELECT,INSERT,UPDATE ON public.health_projection_generation" not in projection_section
    assert "GRANT SELECT ON {projection_tables}" not in projection_section


def test_0017_downgrade_revokes_projection_roles_from_existing_objects():
    source = Path("app/migrations/versions/20260813_0017_p3_basic_projection_builder_foundation.py").read_text(encoding="utf-8")
    downgrade = source.split("def downgrade", 1)[1]
    revoke_helper = source.split("def _revoke_runtime_permissions", 1)[1].split("def ", 1)[0]
    assert "_revoke_runtime_permissions(op.get_bind())" in downgrade
    for object_name in ("platform_org", "canonical_health_fact", "operation_log", "operation_log_id_seq"):
        assert object_name in revoke_helper


def test_0017_has_no_table_level_select_for_runtime_roles():
    source = Path("app/migrations/versions/20260813_0017_p3_basic_projection_builder_foundation.py").read_text(encoding="utf-8")
    grants = source.split("def _grant_runtime_permissions", 1)[1].split("def _revoke_runtime_permissions", 1)[0]
    assert "GRANT SELECT ON TABLE public.organization_projection TO" not in grants
    assert "GRANT SELECT ON TABLE public.health_projection TO" not in grants
    assert "GRANT SELECT ON TABLE public.operation_log" not in grants
    assert grants.count("GRANT SELECT (") >= 12


def test_0017_generation_permissions_and_audit_rows_are_minimal():
    source = Path("app/migrations/versions/20260813_0017_p3_basic_projection_builder_foundation.py").read_text(encoding="utf-8")
    grants = source.split("def _grant_runtime_permissions", 1)[1].split("def _revoke_runtime_permissions", 1)[0]
    assert "INSERT (projection_version,generation_no,status,high_watermark,digest_key_id,input_digest,start_operation_id,builder_id,lease_epoch,lease_expires_at)" in grants
    assert "UPDATE (status,builder_id,lease_epoch,lease_expires_at,updated_at,completed_at,failure_code,version)" in grants
    assert "GRANT SELECT (id,module,object_type,object_id,action,payload,created_at) ON TABLE public.operation_log" not in grants
    assert "organization_projection_operation_audit" in source
    assert "health_projection_operation_audit" in source


def test_health_hwm_persists_fixed_postgresql_snapshot():
    migration = Path("app/migrations/versions/20260813_0017_p3_basic_projection_builder_foundation.py").read_text(encoding="utf-8")
    repository = Path("app/modules/health_projection/repository.py").read_text(encoding="utf-8")
    assert "source_snapshot" in migration
    assert "txid_visible_in_snapshot" in repository
    assert "load_all_source_facts" in repository


def test_0017_high_watermark_id_regex_literals_are_sql_closed():
    source = Path(
        "app/migrations/versions/20260813_0017_p3_basic_projection_builder_foundation.py"
    ).read_text(encoding="utf-8")
    assert "(high_watermark->>'{hwm}') ~ '^(0|[1-9][0-9]*)$'" in source
    assert "(high_watermark->>'max_fact_id') ~ '^(0|[1-9][0-9]*)$'" in source


def test_0017_health_snapshot_visibility_view_is_minimal_and_downgrade_symmetric():
    source = Path("app/migrations/versions/20260813_0017_p3_basic_projection_builder_foundation.py").read_text(encoding="utf-8")
    grants = source.split("def _grant_runtime_permissions", 1)[1].split("def _revoke_runtime_permissions", 1)[0]
    downgrade = source.split("def downgrade", 1)[1]

    assert "CREATE VIEW public.health_projection_source_visibility_v1 WITH (security_barrier=true, security_invoker=false)" in source
    assert "SELECT id,supersedes_fact_id,(xmin::text)::bigint AS inserting_xid FROM public.canonical_health_fact" in source
    assert "REVOKE ALL ON TABLE public.health_projection_source_visibility_v1 FROM PUBLIC" in source
    assert "GRANT SELECT ON TABLE public.health_projection_source_visibility_v1 TO {health}" in grants
    assert "health_projection_source_visibility_v1 FROM {org}, {confirmation}" in grants
    assert "REVOKE ALL PRIVILEGES ON TABLE public.health_projection_source_visibility_v1" in source
    assert "DROP VIEW public.health_projection_source_visibility_v1" in downgrade
    assert "DROP VIEW public.health_projection_source_visibility_v1 CASCADE" not in downgrade
