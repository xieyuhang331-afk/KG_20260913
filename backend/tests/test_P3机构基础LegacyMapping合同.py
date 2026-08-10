import pytest


def test_P1租户组织映射兼容尚未实现():
    try:
        from app.modules.organization.domain import classify_compatibility
    except (ImportError, ModuleNotFoundError):
        pytest.fail("P1 tenant organization mapping compatibility is not implemented")

    assert classify_compatibility("platform", "active") == "legacy"
    assert classify_compatibility("tenant_org", "disabled") == "legacy"
    assert classify_compatibility("city", "active") == "canonical"
