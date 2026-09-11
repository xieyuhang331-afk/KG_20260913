from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.integration


FUNCTION_GRANTS = {
    "KG_TEST_SLICE6_INSTITUTION_WRITER_ROLE": {
        "public.slice6_generation_authority_v1(uuid,bigint,character varying)",
        "public.slice6_mutation_replay_v1(bigint,character varying,character varying,bytea)",
        "public.slice6_mutation_expected_v1(jsonb)",
        "public.slice6_mutation_confirm_v1(jsonb)",
        "public.slice6_generation_request_v1(jsonb)",
        "public.slice6_plan_explanation_v1(jsonb)",
        "public.slice6_user_decision_v1(jsonb)",
    },
    "KG_TEST_SLICE6_TEMPLATE_WRITER_ROLE": {
        "public.slice6_mutation_replay_v1(bigint,character varying,character varying,bytea)",
        "public.slice6_mutation_expected_v1(jsonb)",
        "public.slice6_mutation_confirm_v1(jsonb)",
        "public.slice6_template_governance_v1(jsonb)",
    },
    "KG_TEST_SLICE6_REVIEW_WRITER_ROLE": {
        "public.slice6_mutation_replay_v1(bigint,character varying,character varying,bytea)",
        "public.slice6_mutation_expected_v1(jsonb)",
        "public.slice6_mutation_confirm_v1(jsonb)",
        "public.slice6_review_transition_v1(jsonb)",
    },
    "KG_TEST_SLICE6_WORKFLOW_WORKER_ROLE": {
        "public.slice6_generation_worker_v1(jsonb)",
        "public.slice6_generation_input_v1(uuid)",
        "public.slice6_generation_complete_v1(jsonb)",
        "public.slice6_outbox_claim_v1(uuid,bigint)",
        "public.slice6_outbox_consume_v1(jsonb)",
        "public.slice6_outbox_recover_v1(timestamp with time zone)",
    },
    "KG_TEST_SLICE6_CLINICAL_READER_ROLE": {
        "public.slice6_actor_read_authority_v1(bigint,character varying,uuid,uuid,bigint)",
    },
    "KG_TEST_SLICE6_FAMILY_READER_ROLE": {
        "public.slice6_actor_read_authority_v1(bigint,character varying,uuid,uuid,bigint)",
    },
}

ALL_FUNCTIONS = set().union(*FUNCTION_GRANTS.values())
MODULE_TABLES = (
    "health_plan_template_version",
    "health_plan_generation_request",
    "health_plan_version",
    "health_plan_review",
    "health_plan_explanation",
    "health_plan_user_decision",
    "health_plan_receipt",
    "health_plan_audit",
    "health_plan_outbox",
    "health_plan_delivery",
)


def test_PG01_PG10_0031单一Head六身份受限函数与基础表ACL精确闭合(pg_database):
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260912_0043"
    roles = {name: os.environ[name] for name in FUNCTION_GRANTS}
    assert len(set(roles.values())) == 6

    for variable, role in roles.items():
        assert pg_database.fetch_value(
            f"SELECT NOT rolsuper AND NOT rolinherit AND NOT rolcreaterole "
            f"AND NOT rolcreatedb AND NOT rolreplication AND NOT rolbypassrls "
            f"FROM pg_roles WHERE rolname='{role}'"
        )
        for signature in ALL_FUNCTIONS:
            assert pg_database.fetch_value(
                f"SELECT has_function_privilege('{role}','{signature}','EXECUTE')"
            ) is (signature in FUNCTION_GRANTS[variable])
        for table in MODULE_TABLES:
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert not pg_database.fetch_value(
                    f"SELECT has_table_privilege('{role}','public.{table}','{privilege}')"
                )

    for signature in ALL_FUNCTIONS:
        assert not pg_database.fetch_value(
            f"SELECT has_function_privilege('public','{signature}','EXECUTE')"
        )
