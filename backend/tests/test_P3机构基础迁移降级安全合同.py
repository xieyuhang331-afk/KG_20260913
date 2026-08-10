from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_revision():
    path = Path(__file__).parents[1] / "app" / "migrations" / "versions" / "20260810_0014_p3_platform_organization_foundation.py"
    spec = spec_from_file_location("p3_org_0014", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_Organization审计事实阻止0014降级():
    module = _load_revision()
    assert callable(module._assert_downgrade_safe)
    assert "operation_log" in module._DOWNGRADE_GUARD_SQL
    assert "organization" in module._DOWNGRADE_GUARD_SQL
    assert "platform_org" in module._DOWNGRADE_GUARD_SQL
