from __future__ import annotations

import inspect


def test_实名仓储只读取必要User投影并锁定首次提交() -> None:
    from app.modules.auth.identity_submission_repository import SqlAlchemyIdentitySubmissionRepository

    source = inspect.getsource(SqlAlchemyIdentitySubmissionRepository)
    assert "with_for_update" in source
    assert "User.real_name" not in source
    assert "User.id_card" not in source
    assert "select(User)" not in source
    assert "User.verify_status.is_(None)" in source


def test_实名仓储不提供删除或明文写入接口() -> None:
    from app.modules.auth.identity_submission_repository import SqlAlchemyIdentitySubmissionRepository

    names = set(vars(SqlAlchemyIdentitySubmissionRepository))
    assert not names.intersection({"delete", "truncate", "save_plaintext", "update_identity"})
