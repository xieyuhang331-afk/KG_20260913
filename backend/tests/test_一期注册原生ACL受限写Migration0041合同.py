from __future__ import annotations

import hashlib
import inspect
from pathlib import Path

from sqlalchemy.exc import IntegrityError, OperationalError

ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "app/migrations/versions/20260910_0041_注册会员受限写入.py"
MIGRATION_0040 = (
    ROOT / "app/migrations/versions/20260909_0040_认证主体与当前身份受限读取.py"
)


def test_0041线性增加唯一注册写边界且0040零漂移() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "20260910_0041"' in source
    assert 'down_revision = "20260909_0040"' in source
    assert source.count("CREATE FUNCTION") == 1
    assert "auth_register_member_v1(" in source
    assert source.count("SECURITY DEFINER") == 1
    assert source.count("SET search_path = pg_catalog") == 1
    assert "session_user" in source
    assert "REVOKE ALL ON FUNCTION" in source
    assert "FROM PUBLIC" in source
    assert "GRANT EXECUTE ON FUNCTION" in source
    for forbidden in (
        "GRANT SELECT",
        "GRANT INSERT",
        "GRANT UPDATE",
        "GRANT DELETE",
        "GRANT USAGE",
        "CREATE ROLE",
        "CREATE TABLE",
        "CREATE SEQUENCE",
    ):
        assert forbidden not in source.upper()
    digest = hashlib.sha256(
        MIGRATION_0040.read_text(encoding="utf-8").encode("utf-8")
    ).hexdigest().upper()
    assert digest == "6190DD1C571885FAA2B36CA8AE0C1222BCA6142E4C9E2C32A52EA935373D63C9"


def test_0041参数和返回字段冻结且数据库固定技术Member状态() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    signature = source.split("CREATE FUNCTION public.auth_register_member_v1", 1)[1]
    arguments, body = signature.split("RETURNS TABLE", 1)
    assert "p_phone VARCHAR(11)" in arguments
    assert "p_password_hash VARCHAR(255)" in arguments
    for forbidden in (
        "p_role",
        "p_status",
        "p_tenant_id",
        "p_verify_status",
        "p_id",
        "p_created_at",
    ):
        assert forbidden not in arguments
    returns = body.split("LANGUAGE plpgsql", 1)[0]
    assert tuple(
        field in returns
        for field in (
            "id BIGINT",
            "phone VARCHAR(11)",
            "role public.user_role",
            "status public.user_status",
            "verify_status VARCHAR(20)",
            "tenant_id BIGINT",
            "created_at TIMESTAMPTZ",
        )
    ) == (True,) * 7
    assert "password_hash" not in returns
    assert "'member'::public.user_role" in body
    assert "'active'::public.user_status" in body
    assert "p_phone !~ '^1[0-9]{{10}}$'" in body
    assert "pbkdf2_sha256[$]200000[$]" in body
    assert "ERRCODE='22023'" in body


def test_0041权限撤销和删除精确对称() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    downgrade = source.split("def downgrade", 1)[1]
    assert "REVOKE EXECUTE ON FUNCTION" in downgrade
    assert "REVOKE ALL ON FUNCTION" in downgrade
    assert "DROP FUNCTION" in downgrade
    assert "auth_login_subject_v1" not in downgrade
    assert "auth_user_currentness_v1" not in downgrade


def test_注册Repository只调用受限函数且不直接使用User或Sequence() -> None:
    from app.modules.auth import repository

    source = inspect.getsource(repository.create_registered_member)
    assert "auth_register_member_v1" in source
    assert "bindparam" in source
    assert "String(11)" in source
    assert "String(255)" in source
    assert "select(User" not in source
    assert "session.add" not in source
    assert "nextval" not in source
    assert "password_hash" not in repr(
        repository.RegisteredMember(
            id=1,
            phone="19900000000",
            role="member",
            status="active",
            verify_status=None,
            tenant_id=None,
            created_at=None,
        )
    )


class _DriverIntegrity(Exception):
    def __init__(self, sqlstate: str, constraint_name: str):
        self.sqlstate = sqlstate
        self.constraint_name = constraint_name


def _integrity(sqlstate: str, constraint_name: str) -> IntegrityError:
    return IntegrityError(
        statement=None,
        params=None,
        orig=_DriverIntegrity(sqlstate, constraint_name),
    )


def test_只有手机号唯一约束三条件同时满足才映射UserExists() -> None:
    from app.modules.auth.service import _phone_unique_conflict

    assert _phone_unique_conflict(_integrity("23505", "uq_user_phone"))
    assert not _phone_unique_conflict(_integrity("23505", "uq_other"))
    assert not _phone_unique_conflict(_integrity("23503", "uq_user_phone"))
    assert not _phone_unique_conflict(_DriverIntegrity("23505", "uq_user_phone"))


def test_手机号冲突不得被旧context或跨节点属性污染() -> None:
    from app.modules.auth.service import _phone_unique_conflict

    current_foreign_key = _integrity("23503", "fk_current")
    current_foreign_key.__context__ = _DriverIntegrity("23505", "uq_user_phone")
    assert not _phone_unique_conflict(current_foreign_key)

    adapter = _DriverIntegrity("23505", "")
    adapter.constraint_name = None
    cause_with_constraint_only = RuntimeError("synthetic current driver cause")
    cause_with_constraint_only.constraint_name = "uq_user_phone"
    adapter.__cause__ = cause_with_constraint_only
    split_identity = IntegrityError(None, None, adapter)
    assert not _phone_unique_conflict(split_identity)


def test_正式asyncpg包装链同一原始错误可识别手机号冲突() -> None:
    from app.modules.auth.service import _phone_unique_conflict

    raw_driver_error = _DriverIntegrity("23505", "uq_user_phone")
    adapter = RuntimeError("synthetic asyncpg adapter")
    adapter.sqlstate = "23505"
    adapter.__cause__ = raw_driver_error
    wrapped = IntegrityError(None, None, adapter)

    assert _phone_unique_conflict(wrapped)


def test_正式包装链出现明确冲突时手机号冲突必须FailClosed() -> None:
    from app.modules.auth.service import _phone_unique_conflict

    raw_unique = _DriverIntegrity("23505", "uq_user_phone")
    adapter_foreign_key = _DriverIntegrity("23503", "fk_current")
    adapter_foreign_key.__cause__ = raw_unique
    wrapped = IntegrityError(None, None, adapter_foreign_key)

    assert not _phone_unique_conflict(wrapped)


def test_依赖不可用不得被旧context或泛OSError污染() -> None:
    from app.modules.auth.service import _registered_dependency_failure

    current_programming_error = RuntimeError("synthetic programming error")
    current_programming_error.__context__ = _DriverIntegrity("08006", "")
    assert not _registered_dependency_failure(current_programming_error)
    assert not _registered_dependency_failure(FileNotFoundError("synthetic missing"))
    assert not _registered_dependency_failure(PermissionError("synthetic denied"))
    assert _registered_dependency_failure(ConnectionError("synthetic disconnected"))
    assert _registered_dependency_failure(
        OperationalError(None, None, ConnectionError("synthetic disconnected"))
    )


def test_CommitUnknown资格只接受受控连接传输证据() -> None:
    from app.modules.auth.service import _commit_outcome_uncertain

    for sqlstate in ("40001", "40P01", "23505", "42601"):
        assert not _commit_outcome_uncertain(
            OperationalError(None, None, _DriverIntegrity(sqlstate, ""))
        )
        assert not _commit_outcome_uncertain(
            OperationalError(
                None,
                None,
                _DriverIntegrity(sqlstate, ""),
                connection_invalidated=True,
            )
        )
    assert _commit_outcome_uncertain(ConnectionError("synthetic disconnected"))
    assert _commit_outcome_uncertain(
        OperationalError(None, None, _DriverIntegrity("08006", ""))
    )
    assert _commit_outcome_uncertain(
        OperationalError(
            None,
            None,
            RuntimeError("synthetic driver failure without sqlstate"),
            connection_invalidated=True,
        )
    )


def test_注册Service没有预查直写和宽泛Integrity映射() -> None:
    from app.modules.auth import service

    source = inspect.getsource(service.register_user)
    assert "user_exists_by_phone" not in source
    assert "create_user_record" not in source
    assert "create_registered_member" in source
    assert "_phone_unique_conflict" in source
    assert "_registered_dependency_failure" in source
    assert "_registration_insert_is_visible" in source
