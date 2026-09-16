from __future__ import annotations

import asyncio
import hmac
import os
import secrets

import asyncpg
import pytest
from alembic import command

from app.modules.auth.repository import get_reviewer_credential_material
from app.modules.auth.service import hash_password
from app.modules.member_enrollment.repository import MemberEnrollmentRepository
from tests.integration.conftest import _build_alembic_config, _get_test_database_url

pytestmark = pytest.mark.integration

_WRITE_SIGNATURE = (
    "public.slice3_platform_reviewer_write_currentness_v1(bigint)"
)
_CREDENTIAL_SIGNATURE = "public.auth_user_credential_material_v1(bigint)"


@pytest.fixture(scope="module")
def synthetic_reviewers(pg_database):
    phone_suffix = "".join(str(secrets.randbelow(10)) for _ in range(8))
    password_hash = hash_password("Synthetic-Reviewer-Password")
    rows = pg_database.fetch_rows(
        'INSERT INTO public."user"(phone,password_hash,role,status,tenant_id) '
        "VALUES($1,$2,'super_admin','active',NULL),"
        "($3,$2,'member','active',NULL) RETURNING id,role",
        "130" + phone_suffix,
        password_hash,
        "131" + phone_suffix,
    )
    values = {row["role"]: int(row["id"]) for row in rows}
    application_role = os.environ["KG_TEST_APPLICATION_ROLE"]
    writer_role = os.environ["KG_TEST_MEMBER_IDENTITY_REVIEW_WRITER_ROLE"]
    pg_database.execute(
        'REVOKE ALL ON TABLE public."user" FROM '
        f'"{application_role}","{writer_role}"'
    )
    try:
        yield values, password_hash
    finally:
        pg_database.execute(
            'DELETE FROM public."user" WHERE id IN '
            f"({values['super_admin']},{values['member']})"
        )


def _run(awaitable):
    return asyncio.run(awaitable)


def test_两个闭合函数真实返回固定投影且Reviewer失效时无结果(
    application_database,
    member_identity_review_writer_database,
    synthetic_reviewers,
) -> None:
    values, password_hash = synthetic_reviewers
    credential = _run(
        get_reviewer_credential_material(
            _SessionAdapter(application_database), values["super_admin"]
        )
    )
    assert credential is not None
    if not hmac.compare_digest(credential.password_hash, password_hash):
        pytest.fail("G2_REVIEWER_CREDENTIAL_MATERIAL_MISMATCH", pytrace=False)
    assert credential.role == "super_admin"
    assert "password_hash=" not in repr(credential)

    writer = _SessionAdapter(member_identity_review_writer_database)
    current = _run(
        MemberEnrollmentRepository(writer).platform_reviewer_write_currentness(
            values["super_admin"]
        )
    )
    assert current is not None
    assert set(current) == {
        "id", "role", "status", "tenant_id", "exited_at",
        "deletion_requested_at", "updated_at",
    }
    assert _run(
        MemberEnrollmentRepository(writer).platform_reviewer_write_currentness(
            values["member"]
        )
    ) is None
    assert _run(
        get_reviewer_credential_material(
            _SessionAdapter(application_database), values["member"]
        )
    ) is None


@pytest.mark.parametrize("drift_column", ("exited_at", "deletion_requested_at"))
def test_PII凭据读取后退出或删除申请漂移由Writer事务再次拒绝(
    pg_database,
    application_database,
    member_identity_review_writer_database,
    synthetic_reviewers,
    drift_column: str,
) -> None:
    reviewer_id = synthetic_reviewers[0]["super_admin"]
    credential = _run(
        get_reviewer_credential_material(
            _SessionAdapter(application_database), reviewer_id
        )
    )
    assert credential is not None
    try:
        pg_database.execute(
            f'UPDATE public."user" SET {drift_column}=now() WHERE id={reviewer_id}'
        )
        writer_reviewer = _run(
            MemberEnrollmentRepository(
                _SessionAdapter(member_identity_review_writer_database)
            ).platform_reviewer_write_currentness(reviewer_id)
        )
        assert writer_reviewer is None, "G2_REVIEWER_WRITER_DRIFT_NOT_REJECTED"
    finally:
        pg_database.execute(
            f'UPDATE public."user" SET {drift_column}=NULL WHERE id={reviewer_id}'
        )


def test_函数授权与public_user底表权限闭合(
    pg_database,
    application_database,
    member_identity_review_writer_database,
) -> None:
    application_role = os.environ["KG_TEST_APPLICATION_ROLE"]
    writer_role = os.environ["KG_TEST_MEMBER_IDENTITY_REVIEW_WRITER_ROLE"]
    assert application_database.fetch_value("SELECT current_user") == application_role
    assert member_identity_review_writer_database.fetch_value(
        "SELECT current_user"
    ) == writer_role
    assert _run(pg_database._fetch_value(
        "SELECT has_function_privilege($1,$2,'EXECUTE')",
        application_role,
        _CREDENTIAL_SIGNATURE,
    ))
    assert not _run(pg_database._fetch_value(
        "SELECT has_function_privilege($1,$2,'EXECUTE')",
        application_role,
        _WRITE_SIGNATURE,
    ))
    assert _run(pg_database._fetch_value(
        "SELECT has_function_privilege($1,$2,'EXECUTE')",
        writer_role,
        _WRITE_SIGNATURE,
    ))
    assert not _run(pg_database._fetch_value(
        "SELECT has_function_privilege($1,$2,'EXECUTE')",
        writer_role,
        _CREDENTIAL_SIGNATURE,
    ))
    for role in (application_role, writer_role):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert not _run(pg_database._fetch_value(
                'SELECT has_table_privilege($1,\'public."user"\',$2)',
                role,
                privilege,
            ))
    assert not _run(pg_database._fetch_value(
        "SELECT has_function_privilege('public',$1,'EXECUTE')",
        _WRITE_SIGNATURE,
    ))
    assert not _run(pg_database._fetch_value(
        "SELECT has_function_privilege('public',$1,'EXECUTE')",
        _CREDENTIAL_SIGNATURE,
    ))


def test_Writer当前性函数持有同事务FOR_SHARE窗口(
    pg_database, member_identity_review_writer_database, synthetic_reviewers
) -> None:
    reviewer_id = synthetic_reviewers[0]["super_admin"]

    async def verify() -> None:
        writer = await asyncpg.connect(
            member_identity_review_writer_database.database_url
        )
        admin = await asyncpg.connect(pg_database.database_url)
        try:
            transaction = writer.transaction()
            await transaction.start()
            rows = await writer.fetch(
                "SELECT * FROM public."
                "slice3_platform_reviewer_write_currentness_v1($1)",
                reviewer_id,
            )
            assert len(rows) == 1
            await admin.execute("SET lock_timeout='100ms'")
            with pytest.raises(asyncpg.LockNotAvailableError):
                await admin.execute(
                    'UPDATE public."user" SET updated_at=now() WHERE id=$1',
                    reviewer_id,
                )
            await transaction.rollback()
        finally:
            await writer.close()
            await admin.close()

    _run(verify())


def test_0048降级只撤销本模块函数且可重升级(
    pg_database, synthetic_reviewers
) -> None:
    reviewer_id = synthetic_reviewers[0]["super_admin"]
    before = pg_database.fetch_rows(
        'SELECT id,role,status,tenant_id,updated_at FROM public."user" WHERE id=$1',
        reviewer_id,
    )
    config = _build_alembic_config(_get_test_database_url())
    command.downgrade(config, "20260914_0047")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == (
        "20260914_0047"
    )
    for signature in (_WRITE_SIGNATURE, _CREDENTIAL_SIGNATURE):
        assert not pg_database.fetch_value(
            f"SELECT to_regprocedure('{signature}') IS NOT NULL"
        )
    assert pg_database.fetch_rows(
        'SELECT id,role,status,tenant_id,updated_at FROM public."user" WHERE id=$1',
        reviewer_id,
    ) == before
    command.upgrade(config, "head")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == (
        "20260916_0049"
    )
    for signature in (_WRITE_SIGNATURE, _CREDENTIAL_SIGNATURE):
        assert pg_database.fetch_value(
            f"SELECT to_regprocedure('{signature}') IS NOT NULL"
        )


class _MappingResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def one_or_none(self):
        if not self._rows:
            return None
        assert len(self._rows) == 1
        return self._rows[0]


class _SessionAdapter:
    def __init__(self, database):
        self.database = database

    async def execute(self, statement, parameters=None):
        sql = str(statement)
        bound = parameters if parameters is not None else statement.compile().params
        values = tuple(bound.values())
        rows = await self.database._fetch_rows(sql.replace(":user_id", "$1").replace(":reviewer_user_id", "$1"), *values)
        return _MappingResult(rows)
