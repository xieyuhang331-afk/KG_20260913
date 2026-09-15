from __future__ import annotations

import os
import re

import asyncpg
import pytest
from alembic import command

from tests.integration.conftest import _build_alembic_config, _get_test_database_url

pytestmark = pytest.mark.integration

_SAFE_ROLE = re.compile(r"[a-z][a-z0-9_]{0,62}")
_LEGACY_TABLES = ("health_profile", "health_indicator", "detection_report")
_LEGACY_SEQUENCES = ("health_profile_id_seq", "health_indicator_id_seq")
_FUNCTIONS = (
    "r4_member_health_currentness_v1(bigint)",
    "r4_member_legacy_health_profile_read_v1(bigint,bigint)",
    "r4_member_legacy_health_profile_create_v1(bigint,bigint,character varying,date,numeric,numeric,character varying,jsonb,jsonb,jsonb,character varying,character varying,jsonb,character varying,character varying,timestamp with time zone)",
    "r4_member_legacy_health_profile_update_v1(bigint,bigint,timestamp with time zone,character varying,date,numeric,numeric,character varying,timestamp with time zone)",
    "r4_member_self_health_indicator_history_v1(bigint,character varying,timestamp with time zone,timestamp with time zone,timestamp with time zone,bigint,integer)",
    "r4_member_legacy_health_indicator_create_v1(bigint,bigint,character varying,character varying,numeric,character varying,character varying,timestamp with time zone)",
    "r4_member_legacy_health_indicator_history_v1(bigint,bigint,character varying,timestamp with time zone,timestamp with time zone,integer)",
    "r4_member_legacy_health_indicator_latest_v1(bigint,bigint)",
    "r4_member_self_detection_report_history_v1(bigint,character varying,timestamp with time zone,timestamp with time zone,timestamp with time zone,bigint,integer)",
    "r4_member_self_detection_report_read_v1(bigint,bigint)",
)


def _role(environment_name: str) -> str:
    value = os.environ[environment_name]
    assert _SAFE_ROLE.fullmatch(value)
    return value


def _revoke_fixture_privileges(pg_database) -> str:
    application_role = _role("KG_TEST_APPLICATION_ROLE")
    tables = ", ".join(f'public."{name}"' for name in _LEGACY_TABLES)
    sequences = ", ".join(f'public."{name}"' for name in _LEGACY_SEQUENCES)
    pg_database.execute(
        f'REVOKE ALL PRIVILEGES ON TABLE {tables} FROM "{application_role}"'
    )
    pg_database.execute(
        f'REVOKE ALL PRIVILEGES ON SEQUENCE {sequences} FROM "{application_role}"'
    )
    return application_role


def test_R4_Application只可执行10个受限函数且仍无旧表和Sequence权限(
    pg_database,
    application_database,
    readonly_database,
) -> None:
    application_role = _revoke_fixture_privileges(pg_database)
    readonly_role = _role("KG_TEST_READONLY_ROLE")

    for table_name in _LEGACY_TABLES:
        assert not pg_database.fetch_value(
            "SELECT has_table_privilege("
            f"'{application_role}', 'public.{table_name}', 'SELECT')"
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError) as denied:
            application_database.fetch_value(
                f'SELECT count(*) FROM public."{table_name}"'
            )
        assert denied.value.sqlstate == "42501"

    for sequence_name in _LEGACY_SEQUENCES:
        assert not pg_database.fetch_value(
            "SELECT has_sequence_privilege("
            f"'{application_role}', 'public.{sequence_name}', 'USAGE')"
        )

    for signature in _FUNCTIONS:
        assert pg_database.fetch_value(
            "SELECT has_function_privilege("
            f"'{application_role}', 'public.{signature}', 'EXECUTE')"
        )
        assert not pg_database.fetch_value(
            "SELECT has_function_privilege("
            f"'{readonly_role}', 'public.{signature}', 'EXECUTE')"
        )
        assert not pg_database.fetch_value(
            "SELECT has_function_privilege("
            f"'public', 'public.{signature}', 'EXECUTE')"
        )

    with pytest.raises(asyncpg.InsufficientPrivilegeError) as denied_function:
        readonly_database.fetch_value(
            "SELECT id FROM public.r4_member_health_currentness_v1(1)"
        )
    assert denied_function.value.sqlstate == "42501"


def test_R4_0046_0047空库往返且非空降级在DDL前拒绝(pg_database) -> None:
    config = _build_alembic_config(_get_test_database_url())
    assert pg_database.fetch_value(
        "SELECT version_num FROM alembic_version"
    ) == "20260914_0047"

    command.downgrade(config, "20260914_0046")
    assert pg_database.fetch_value(
        "SELECT version_num FROM alembic_version"
    ) == "20260914_0046"
    assert all(
        not pg_database.fetch_value(
            f"SELECT to_regprocedure('public.{signature}') IS NOT NULL"
        )
        for signature in _FUNCTIONS
    )

    command.upgrade(config, "20260914_0047")
    assert all(
        pg_database.fetch_value(
            f"SELECT to_regprocedure('public.{signature}') IS NOT NULL"
        )
        for signature in _FUNCTIONS
    )

    pg_database.execute(
        'INSERT INTO public."user" '
        "(id,phone,password_hash,role,status,verify_status) VALUES "
        "(9470047,'13900004700','synthetic','member','active','verified')"
    )
    pg_database.execute(
        "INSERT INTO public.health_profile "
        "(user_id,gender,birth_date,updated_at) VALUES "
        "(9470047,'F','1990-01-01','2026-09-14T08:00:00Z')"
    )
    try:
        with pytest.raises(RuntimeError, match="R4 downgrade requires empty legacy tables"):
            command.downgrade(config, "20260914_0046")
        assert pg_database.fetch_value(
            "SELECT version_num FROM alembic_version"
        ) == "20260914_0047"
        assert all(
            pg_database.fetch_value(
                f"SELECT to_regprocedure('public.{signature}') IS NOT NULL"
            )
            for signature in _FUNCTIONS
        )
    finally:
        pg_database.execute("DELETE FROM public.health_profile WHERE user_id=9470047")
        pg_database.execute('DELETE FROM public."user" WHERE id=9470047')
