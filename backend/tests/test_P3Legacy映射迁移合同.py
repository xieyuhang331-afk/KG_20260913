import importlib.util
from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / "app" / "migrations" / "versions" / "20260812_0016_p3_organization_health_legacy_mapping.py"


def _load():
    assert PATH.exists(), "P3 Organization/Health legacy mapping migration is not implemented"
    spec = importlib.util.spec_from_file_location(PATH.stem, PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_Legacy映射0016尚未实现():
    module = _load()
    assert module.revision == "20260812_0016"
    assert module.down_revision == "20260811_0015"


def test_0016只创建两个Mapping表且不修改legacy表():
    source = PATH.read_text(encoding="utf-8")
    assert '"organization_legacy_mapping"' in source
    assert '"health_indicator_legacy_mapping"' in source
    assert "op.add_column" not in source
    assert "op.alter_column" not in source
    assert "CASCADE" not in source.upper()
    assert "drop_schema" not in source
    for name in (
        "uq_organization_legacy_mapping_source_version",
        "uq_health_indicator_legacy_mapping_source_version",
        "uq_health_indicator_legacy_mapping_fact_version",
        "ck_organization_legacy_mapping_target_pair",
        "ck_health_indicator_legacy_mapping_target_pair",
    ):
        assert name in source


def test_0016权限默认关闭且不得在迁移中授予高权限():
    source = PATH.read_text(encoding="utf-8")
    for token in (
        "REVOKE ALL ON TABLE public.organization_legacy_mapping FROM PUBLIC",
        "REVOKE ALL ON TABLE public.health_indicator_legacy_mapping FROM PUBLIC",
        "REVOKE ALL ON SEQUENCE public.organization_legacy_mapping_id_seq FROM PUBLIC",
        "REVOKE ALL ON SEQUENCE public.health_indicator_legacy_mapping_id_seq FROM PUBLIC",
    ):
        assert token in source
    assert "GRANT ALL" not in source.upper()


def test_0016五个FK名称精确符合冻结合同():
    source = PATH.read_text(encoding="utf-8")
    for name in (
        "fk_organization_legacy_mapping_tenant",
        "fk_organization_legacy_mapping_canonical_org",
        "fk_organization_legacy_mapping_created_by",
        "fk_health_indicator_legacy_mapping_fact",
        "fk_health_indicator_legacy_mapping_created_by",
    ):
        assert f'name="{name}"' in source


def test_0016不得向Shadow授予tenant或platform_org表级SELECT():
    source = PATH.read_text(encoding="utf-8")
    assert "GRANT SELECT ON TABLE public.tenant" not in source
    assert "GRANT SELECT ON TABLE public.platform_org" not in source
