import inspect

from sqlalchemy import BigInteger, Numeric, SmallInteger, String

from app.modules.health_fact.models import CanonicalHealthFactOrmModel
from app.modules.health_fact.repository import SqlAlchemyHealthFactRepository
from app.modules.health_fact.unit_of_work import (
    CanonicalHealthFactWriter,
    SqlAlchemyHealthFactUnitOfWork,
)


def test_规范健康事实SQLAlchemy持久化尚未实现():
    table = CanonicalHealthFactOrmModel.__table__
    assert table.name == "canonical_health_fact"
    assert table.schema == "public"
    assert {column.name for column in table.columns} == {
        "id", "subject_user_id", "indicator_code", "catalog_version",
        "subject_member_id", "fact_ref",
        "value_kind", "numeric_value", "unit", "measured_at", "received_at",
        "created_at", "source_type", "source_identity_digest",
        "producer_event_key", "payload_digest", "digest_key_id",
        "supersedes_fact_id", "correction_reason_code", "created_by",
    }


def test_V1_ORM只有NUMERIC且类型长度精确():
    table = CanonicalHealthFactOrmModel.__table__
    assert isinstance(table.c.id.type, BigInteger)
    assert isinstance(table.c.catalog_version.type, SmallInteger)
    assert isinstance(table.c.numeric_value.type, Numeric)
    assert (table.c.numeric_value.type.precision, table.c.numeric_value.type.scale) == (10, 2)
    assert isinstance(table.c.value_kind.type, String)
    assert table.c.value_kind.type.length == 16
    assert "text_value" not in table.c
    assert "composite_value" not in table.c
    assert "source_identity" not in table.c


def test_ORM约束与索引精确存在():
    table = CanonicalHealthFactOrmModel.__table__
    constraints = {constraint.name for constraint in table.constraints}
    assert "uq_canonical_health_fact_source_event" in constraints
    assert "uq_canonical_health_fact_single_successor" in constraints
    assert {
        "ck_canonical_health_fact_catalog_v1_v2",
        "ck_canonical_health_fact_value_kind_v1_numeric",
        "ck_canonical_health_fact_indicator_v1_v2",
        "ck_canonical_health_fact_unit_v1_v2",
        "ck_canonical_health_fact_source_type_v1_v2",
        "ck_canonical_health_fact_source_digest",
        "ck_canonical_health_fact_payload_digest",
        "ck_canonical_health_fact_predecessor_reason_pair",
    } <= constraints
    rendered = " ".join(str(constraint.sqltext) for constraint in table.constraints if hasattr(constraint, "sqltext"))
    assert "catalog_version=1" in rendered and "catalog_version=2" in rendered
    assert "source_type IN ('APP','STORE','DEVICE','REPORT')" in rendered
    assert {index.name for index in table.indexes} == {
        "idx_canonical_health_fact_subject_indicator_time",
        "idx_canonical_health_fact_source_time",
    }


def test_repository没有update_delete或DDL入口():
    public = {
        name for name, value in inspect.getmembers(
            SqlAlchemyHealthFactRepository, inspect.isfunction
        ) if not name.startswith("_")
    }
    assert "update" not in public
    assert "delete" not in public
    assert "create_all" not in inspect.getsource(SqlAlchemyHealthFactRepository)


def test_UoW和Writer不创建Engine或全局SessionFactory():
    source = inspect.getsource(SqlAlchemyHealthFactUnitOfWork) + inspect.getsource(
        CanonicalHealthFactWriter
    )
    assert "create_async_engine" not in source
    assert "async_sessionmaker" not in source
    assert "commit()" in source
    assert "rollback()" in source
    assert "close()" in source


def test_Writer固定semantic_lock全key查询和stored_key验证():
    source = inspect.getsource(CanonicalHealthFactWriter)
    assert "acquire_semantic_lock" in source
    assert "source_identity_digests" in source
    assert "verify_payload" in source
    assert "_confirm_outcome" in source
    assert "fact_replayed" not in source
