import inspect


def test_机构Legacy映射ORM字段约束与索引尚未实现():
    from app.modules.organization_mapping.models import OrganizationLegacyMappingOrmModel

    table = OrganizationLegacyMappingOrmModel.__table__
    assert table.name == "organization_legacy_mapping"
    assert table.schema == "public"
    assert {c.name for c in table.columns} == {
        "id", "legacy_tenant_id", "legacy_org_id", "canonical_organization_id",
        "mapping_version", "batch_id", "source_fingerprint", "digest_key_id",
        "disposition", "reason_code", "created_at", "created_by",
    }
    assert not table.c.disposition.nullable
    assert not table.c.legacy_org_id.foreign_keys
    names = {c.name for c in table.constraints}
    assert {fk.name for fk in table.foreign_key_constraints} == {
        "fk_organization_legacy_mapping_tenant",
        "fk_organization_legacy_mapping_canonical_org",
        "fk_organization_legacy_mapping_created_by",
    }
    assert all(name is None or len(name) <= 63 for name in names)
    for suffix in ("source_version", "version_v1", "disposition", "reason", "fingerprint_hex", "target_pair"):
        assert any(name and name.endswith(suffix) for name in names)
    assert {i.name for i in table.indexes} == {
        "idx_organization_legacy_mapping_batch_source",
        "idx_organization_legacy_mapping_disposition",
        "idx_organization_legacy_mapping_target",
    }


def test_机构Repository只有append和读取无update_delete_DDL():
    from app.modules.organization_mapping.repository import OrganizationMappingRepository

    public = {name for name, fn in inspect.getmembers(OrganizationMappingRepository, inspect.isfunction) if not name.startswith("_")}
    assert {"get_source", "find_existing", "add", "add_audit"} <= public
    assert not {"update", "delete", "create_all"} & public
