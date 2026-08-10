from pathlib import Path
import pytest


def test_P3机构基础迁移尚未实现():
    path = Path(__file__).parents[1] / "app" / "migrations" / "versions" / "20260810_0014_p3_platform_organization_foundation.py"
    if not path.exists():
        pytest.fail("P3 Organization foundation migration is not implemented")
    text = path.read_text(encoding="utf-8")
    assert 'revision = "20260810_0014"' in text
    assert 'down_revision = "20260809_0013"' in text
    for value in ("admin_id", "version", "created_by", "updated_by"):
        assert value in text
    assert "CASCADE" not in text.upper()
