import inspect


def test_健康Legacy映射ORM字段约束与索引尚未实现():
    from app.modules.health_fact_mapping.models import HealthIndicatorLegacyMappingOrmModel

    table = HealthIndicatorLegacyMappingOrmModel.__table__
    assert table.name == "health_indicator_legacy_mapping"
    assert table.schema == "public"
    assert {c.name for c in table.columns} == {
        "id", "legacy_indicator_id", "legacy_recorded_at", "canonical_fact_id",
        "mapping_version", "batch_id", "source_fingerprint", "digest_key_id",
        "disposition", "reason_code", "created_at", "created_by",
    }
    names = {c.name for c in table.constraints}
    assert {fk.name for fk in table.foreign_key_constraints} == {
        "fk_health_indicator_legacy_mapping_fact",
        "fk_health_indicator_legacy_mapping_created_by",
    }
    for suffix in ("source_version", "version_v1", "disposition", "reason", "fingerprint_hex", "target_pair"):
        assert any(name and name.endswith(suffix) for name in names)
    assert {i.name for i in table.indexes} == {
        "idx_health_indicator_legacy_mapping_batch_source",
        "idx_health_indicator_legacy_mapping_disposition",
        "uq_health_indicator_legacy_mapping_fact_version",
    }


def test_健康Repository只有append和读取无update_delete_DDL():
    from app.modules.health_fact_mapping.repository import HealthFactMappingRepository

    public = {name for name, fn in inspect.getmembers(HealthFactMappingRepository, inspect.isfunction) if not name.startswith("_")}
    assert {"get_source", "find_existing", "add", "add_audit"} <= public
    assert not {"update", "delete", "create_all"} & public
