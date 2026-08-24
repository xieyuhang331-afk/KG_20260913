from __future__ import annotations

import os

import pytest
from alembic import command

from tests.integration.conftest import _build_alembic_config, _get_test_database_url


pytestmark = pytest.mark.integration


BOUNDARY_FUNCTIONS = (
    "public.slice4_subject_authority_v1(uuid,uuid,bigint,character varying)",
    "public.slice4_readiness_currentness_v1(uuid,bigint)",
    "public.slice4_projection_coverage_v2(uuid,jsonb)",
    "public.slice4_report_file_authority_v1(uuid,uuid,bigint,character varying)",
    "public.slice4_clinical_profile_read_v1(bigint,character varying,uuid,uuid,uuid)",
    "public.slice4_clinical_report_read_v1(bigint,character varying,uuid,uuid,uuid,jsonb)",
    "public.slice4_clinical_fact_read_v1(bigint,character varying,uuid,uuid,uuid,jsonb)",
    "public.slice4_institution_health_read_v1(bigint,uuid,character varying,jsonb)",
    "public.health_projection_builder_source_v2(bigint,jsonb,character varying)",
    "public.health_projection_subject_evidence_verify_v2(bigint)",
    "public.slice4_profile_confirm_v1(uuid,uuid,uuid)",
    "public.slice4_report_confirm_v1(uuid,uuid,uuid)",
    "public.slice4_health_fact_confirm_v1(uuid,uuid,uuid)",
    "public.slice4_assembly_confirm_v1(uuid,uuid,uuid)",
)

BOUNDARY_VIEWS = (
    "slice4_health_profile_clinical_read_v1",
    "slice4_institution_health_record_read_v1",
    "slice4_detection_report_read_v1",
    "slice4_health_fact_status_read_v1",
    "slice4_assessment_readiness_read_v1",
    "slice4_recompute_candidate_v1",
    "health_ready_projection_resolution_v2",
    "health_projection_source_visibility_v2",
    "health_projection_status_visibility_v2",
    "slice4_projection_coverage_source_v2",
    "health_ready_subject_indicator_evidence_v2",
    "health_ready_projection_fact_v2",
)


def _hotfix_acl_present(pg_database) -> bool:
    writer = os.environ["KG_TEST_MEMBER_ENROLLMENT_WRITER_ROLE"]
    return all(
        pg_database.fetch_value(
            f"SELECT has_column_privilege('{writer}',"
            f"'public.member_service_invitation','{column}','UPDATE')"
        )
        for column in ("code_digest", "code_key_id", "expires_at", "issued_at")
    )


def test_PG34_空库0027_0028往返对象ACL与Hotfix权限精确对称(pg_database) -> None:
    config = _build_alembic_config(_get_test_database_url())
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260826_0031"
    assert _hotfix_acl_present(pg_database)
    for signature in BOUNDARY_FUNCTIONS:
        assert pg_database.fetch_value(f"SELECT to_regprocedure('{signature}') IS NOT NULL")
    for view in BOUNDARY_VIEWS:
        assert pg_database.fetch_value(f"SELECT to_regclass('public.{view}') IS NOT NULL")

    command.downgrade(config, "20260822_0027")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260822_0027"
    assert _hotfix_acl_present(pg_database)
    for signature in BOUNDARY_FUNCTIONS:
        assert pg_database.fetch_value(f"SELECT to_regprocedure('{signature}') IS NULL")
    for view in BOUNDARY_VIEWS:
        assert pg_database.fetch_value(f"SELECT to_regclass('public.{view}') IS NULL")

    command.upgrade(config, "20260823_0028")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260823_0028"
    assert _hotfix_acl_present(pg_database)
    for signature in BOUNDARY_FUNCTIONS:
        assert pg_database.fetch_value(f"SELECT to_regprocedure('{signature}') IS NOT NULL")
    for view in BOUNDARY_VIEWS:
        assert pg_database.fetch_value(f"SELECT to_regclass('public.{view}') IS NOT NULL")
    command.upgrade(config, "head")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260826_0031"


def test_PG35_非空降级在任何对象或ACL变化前fail_closed(pg_database) -> None:
    policy_id = "00000000-0000-7000-8000-000000000024"
    pg_database.execute(
        "INSERT INTO public.assessment_readiness_policy_version("
        "policy_version_id,version_no,status,required_profile_sections,required_indicators,"
        "allowed_states,projection_version,rule_version,professionally_approved,approved_by,"
        "approved_at,effective_from,retired_at,policy_digest,digest_key_id,created_at) VALUES ("
        f"'{policy_id}',24,'DRAFT','[]'::jsonb,'[]'::jsonb,'[]'::jsonb,2,'slice4-pg35',"
        "false,NULL,NULL,now(),NULL,decode(repeat('a',64),'hex'),'policy-k1',now())"
    )
    config = _build_alembic_config(_get_test_database_url())
    function_snapshot = tuple(
        pg_database.fetch_value(f"SELECT to_regprocedure('{signature}') IS NOT NULL")
        for signature in BOUNDARY_FUNCTIONS
    )
    view_snapshot = tuple(
        pg_database.fetch_value(f"SELECT to_regclass('public.{view}') IS NOT NULL")
        for view in BOUNDARY_VIEWS
    )
    acl_snapshot = _hotfix_acl_present(pg_database)
    with pytest.raises(RuntimeError, match="Slice 4 downgrade requires empty module tables"):
        command.downgrade(config, "20260822_0027")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260826_0031"
    assert function_snapshot == tuple(
        pg_database.fetch_value(f"SELECT to_regprocedure('{signature}') IS NOT NULL")
        for signature in BOUNDARY_FUNCTIONS
    )
    assert view_snapshot == tuple(
        pg_database.fetch_value(f"SELECT to_regclass('public.{view}') IS NOT NULL")
        for view in BOUNDARY_VIEWS
    )
    assert _hotfix_acl_present(pg_database) is acl_snapshot
    pg_database.execute(
        f"DELETE FROM public.assessment_readiness_policy_version WHERE policy_version_id='{policy_id}'"
    )
