from importlib import import_module

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint


EXPECTED_RED = "Identity atomic registration bootstrap persistence is not implemented"


def _models():
    try:
        module = import_module("app.modules.member.infrastructure.models")
        return (
            module.UserMemberSelfLinkOrmModel,
            module.RegistrationBootstrapRecordOrmModel,
        )
    except AttributeError:
        pytest.fail(EXPECTED_RED)


def _unique_column_sets(table):
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def test_SelfLink与Bootstrap持久化尚未实现():
    self_link, bootstrap = _models()

    assert self_link.__table__.schema == "identity"
    assert self_link.__tablename__ == "user_member_self_link"
    assert bootstrap.__table__.schema == "identity"
    assert bootstrap.__tablename__ == "registration_bootstrap_record"


def test_SelfLink唯一性覆盖P1User与Member且无跨数据库外键():
    self_link, _ = _models()
    unique_sets = _unique_column_sets(self_link.__table__)

    assert ("user_ref",) in unique_sets
    assert ("member_id",) in unique_sets
    assert not self_link.__table__.foreign_keys
    assert {
        "link_id",
        "user_ref",
        "member_id",
        "source",
        "eligibility_decision_ref",
        "establishment_basis",
        "establishment_record_ref",
        "created_at",
    } == set(self_link.__table__.columns.keys())


def test_BootstrapRecord冻结CanonicalSource与Proof引用且无跨数据库外键():
    _, bootstrap = _models()
    unique_sets = _unique_column_sets(bootstrap.__table__)

    assert ("bootstrap_scope", "source_system", "source_ref") in unique_sets
    assert ("member_id",) in unique_sets
    assert ("self_link_id",) in unique_sets
    assert ("member_no_allocation_ref",) in unique_sets
    assert not bootstrap.__table__.foreign_keys
    assert {
        "record_id",
        "user_ref",
        "member_id",
        "self_link_id",
        "registration_event_id",
        "eligibility_decision_ref",
        "member_no_allocation_ref",
        "member_no",
        "source",
        "bootstrap_scope",
        "source_system",
        "source_ref",
        "decision",
        "policy_version",
        "created_at",
    } == set(bootstrap.__table__.columns.keys())


def test_两表Source均锁定RegistrationVerified且没有可变版本字段():
    self_link, bootstrap = _models()
    for table in (self_link.__table__, bootstrap.__table__):
        checks = {
            str(constraint.sqltext)
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        }
        assert any("REGISTRATION_VERIFIED" in check for check in checks)
        assert "version" not in table.columns
        assert "updated_at" not in table.columns
