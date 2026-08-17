from pathlib import Path


ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "app/migrations/versions/20260817_0021_phase1_slice2_therapist_qualification_service_ready.py"


def _source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_0021线性对象约束事件目录权限与精确回滚():
    source = _source()
    assert 'revision = "20260817_0021"' in source
    assert 'down_revision = "20260816_0020"' in source
    for table in ("therapist_invitation", "therapist_profile", "therapist_qualification_version", "institution_service_readiness", "readiness_evidence", "therapist_workflow_outbox"):
        assert f'op.create_table("{table}"' in source
        assert f'op.drop_table(table, schema="public")' in source or table in source
    assert "CASCADE" not in source


def test_Application与Slice1例外ACL预检及对称撤销():
    source = _source()
    assert "_RUNTIME_IDENTITIES" in source
    assert "KG_THERAPIST_READER_ROLE" in source
    assert "pg_auth_members" in source
    assert "REVOKE ALL" in source
    assert "therapist_qualification_file_relation_v1" in source
    assert "SECURITY DEFINER SET search_path=pg_catalog,pg_temp" in source
    assert 'KG_PRIVATE_FILE_WRITER_ROLE' in source
    assert 'KG_INSTITUTION_ONBOARDING_READER_ROLE' in source
    assert "REVOKE ALL ON FUNCTION public.therapist_qualification_file_relation_v1(UUID) FROM PUBLIC" in source
    assert "DROP FUNCTION public.therapist_qualification_file_relation_v1(UUID)" in source
    for signature in (
        "lock_therapist_qualification_files_v1(UUID[],BIGINT)",
        "lock_therapist_qualification_review_files_v1(UUID,INTEGER)",
    ):
        assert f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC" in source
        assert f"DROP FUNCTION public.{signature}" in source
    assert 'TO "{onboarding}"' in source
    assert 'TO "{reviewer}"' in source
    assert "tuple(configured.values())" in source
    assert "_membership_is_unsafe(\n        connection, tuple(configured.values())\n    )" in source
    assert (
        '_grant(slice1_onboarding, "SELECT", "institution_license", '
        '("valid_from","valid_until"))'
    ) in source
    downgrade = source[source.index("def downgrade()") :]
    assert (
        "REVOKE SELECT (valid_from,valid_until), INSERT (valid_from,valid_until), "
        "UPDATE (valid_from,valid_until) ON TABLE public.institution_license"
    ) in downgrade
    assert "REVOKE USAGE ON SCHEMA public" in downgrade


def test_Reader公开投影与Evidence列集不含内部源摘要():
    source = _source()
    public_profile = source.split("profile_public =", 1)[1].split("\n", 1)[0]
    public_evidence = source.split("evidence_public =", 1)[1].split("\n", 1)[0]
    assert '"practice_summary"' in public_profile
    assert '"real_name_ciphertext"' not in public_profile
    assert '"totp_secret_ciphertext"' not in public_profile
    for forbidden in (
        "tenant_status", "institution_license_digest", "therapist_set_digest",
        "service_scope_digest", "source_versions", "trigger_event_id",
    ):
        assert forbidden not in public_evidence


def test_复合FK阻断跨therapist资格与审核事实污染():
    source = _source()
    assert "fk_therapist_qualification_revision" in source
    assert '["therapist_id", "profile_revision_id"]' in source
    assert "fk_therapist_revision_qualification_version" in source


def test_延迟完整性触发器不依赖业务身份扩权():
    source = _source()
    for function_name in (
        "enforce_therapist_revision_qualification_v1",
        "enforce_therapist_qualification_attachments_v1",
        "enforce_therapist_review_decision_v1",
    ):
        definition = source.split(
            f"CREATE FUNCTION public.{function_name}()", 1
        )[1].split("END$$", 1)[0]
        assert (
            "RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER "
            "SET search_path=pg_catalog,pg_temp"
        ) in definition


def test_Reviewer只更新同事务SERVICE_READY业务列():
    source = _source()
    statement = (
        '_grant(reviewer, "UPDATE", "institution_service_readiness", '
        'tuple(value for value in readiness if value != "tenant_id"))'
    )
    assert source.count(statement) == 1
    assert '_grant(reviewer, "UPDATE", "institution_service_readiness", readiness)' not in source


def test_Worker过期与人工恢复只使用精确权限边界():
    source = _source()
    assert "is_current_slice2_recovery_actor_v1" in source
    assert (
        '_grant(worker, "UPDATE", "therapist_invitation", '
        '("status","version"))'
    ) in source
    assert '_grant(worker, "INSERT", "therapist_workflow_idempotency", idempotency)' in source
    assert '_grant(worker, "INSERT", "therapist_workflow_outbox", outbox)' in source
    assert "DROP FUNCTION public.is_current_slice2_recovery_actor_v1(BIGINT)" in source
    assert "onboarding, reviewer, worker, _ = roles" in source


def test_0021降级先解除Profile与Qualification命名循环外键():
    source = _source()
    downgrade = source[source.index("def downgrade()") :]
    drop_fk = downgrade.index('"fk_therapist_profile_current_qualification"')
    drop_tables = downgrade.index("for table in reversed(_TABLES):")
    assert drop_fk < drop_tables
    assert "CASCADE" not in source


def test_附件绑定锁后使用新语句复核已提交赢家():
    source = _source()
    function = source.split(
        "CREATE FUNCTION public.lock_therapist_qualification_files_v1", 1
    )[1].split("END$$", 1)[0]
    assert function.count("FROM public.private_file f") == 2
    assert "FOR UPDATE OF f;\n RETURN QUERY" in function
    assert "NOT EXISTS(" in function.split("RETURN QUERY", 1)[1]
