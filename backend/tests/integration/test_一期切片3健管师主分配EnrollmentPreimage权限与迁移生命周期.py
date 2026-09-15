from __future__ import annotations

import os

import asyncpg
import pytest
from alembic import command

from tests.integration.conftest import _build_alembic_config, _get_test_database_url


pytestmark = pytest.mark.integration

FUNCTION_SIGNATURE = (
    "public.slice3_case_enrollment_preimage_authority_v1(uuid,uuid,uuid,bigint)"
)


def test_0028到0029升级降级再升级只对称改变受限函数(pg_database) -> None:
    config = _build_alembic_config(_get_test_database_url())
    assert pg_database.fetch_value(
        "SELECT version_num FROM alembic_version"
    ) == "20260915_0048"

    command.downgrade(config, "20260824_0029")
    assert pg_database.fetch_value(
        "SELECT version_num FROM alembic_version"
    ) == "20260824_0029"
    assert pg_database.fetch_value(
        f"SELECT to_regprocedure('{FUNCTION_SIGNATURE}') IS NOT NULL"
    )

    command.downgrade(config, "20260823_0028")
    assert pg_database.fetch_value(
        "SELECT version_num FROM alembic_version"
    ) == "20260823_0028"
    assert pg_database.fetch_value(
        f"SELECT to_regprocedure('{FUNCTION_SIGNATURE}') IS NULL"
    )

    command.upgrade(config, "20260824_0029")
    assert pg_database.fetch_value(
        "SELECT version_num FROM alembic_version"
    ) == "20260824_0029"
    assert pg_database.fetch_value(
        f"SELECT to_regprocedure('{FUNCTION_SIGNATURE}') IS NOT NULL"
    )

    command.upgrade(config, "head")
    assert pg_database.fetch_value(
        "SELECT version_num FROM alembic_version"
    ) == "20260915_0048"
    assert pg_database.fetch_value(
        f"SELECT to_regprocedure('{FUNCTION_SIGNATURE}') IS NOT NULL"
    )


def test_新函数只授权CaseWriter且不扩大Enrollment整表SELECT(pg_database) -> None:
    case = os.environ["KG_TEST_MEMBER_CASE_WRITER_ROLE"]
    denied_roles = (
        os.environ["KG_TEST_MEMBER_ENROLLMENT_WRITER_ROLE"],
        os.environ["KG_TEST_MEMBER_IDENTITY_REVIEW_WRITER_ROLE"],
        os.environ["KG_TEST_MEMBER_ENROLLMENT_READER_ROLE"],
        os.environ["KG_TEST_READONLY_ROLE"],
        os.environ["KG_TEST_APPLICATION_ROLE"],
    )

    assert not pg_database.fetch_value(
        f"SELECT has_table_privilege('{case}',"
        "'public.service_enrollment','SELECT')"
    )
    assert pg_database.fetch_value(
        f"SELECT has_function_privilege('{case}',"
        f"'{FUNCTION_SIGNATURE}','EXECUTE')"
    )
    for role in denied_roles:
        assert not pg_database.fetch_value(
            f"SELECT has_function_privilege('{role}',"
            f"'{FUNCTION_SIGNATURE}','EXECUTE')"
        )
    assert not pg_database.fetch_value(
        f"SELECT has_function_privilege('public','{FUNCTION_SIGNATURE}','EXECUTE')"
    )


def test_即使函数Owner调用也被SessionUser边界拒绝(pg_database) -> None:
    with pytest.raises(
        asyncpg.InsufficientPrivilegeError,
        match="SLICE3_CASE_ENROLLMENT_PREIMAGE_FORBIDDEN",
    ):
        pg_database.fetch_value(
            "SELECT public.slice3_case_enrollment_preimage_authority_v1("
            "NULL,NULL,NULL,NULL)"
        )
