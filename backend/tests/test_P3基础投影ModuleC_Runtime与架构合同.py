from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_Module_C最高状态仅READY且无公开入口():
    text = "\n".join((ROOT / "app/modules/organization_projection/service.py").read_text("utf-8").splitlines() + (ROOT / "app/modules/health_projection/service.py").read_text("utf-8").splitlines())
    assert "ACTIVE" not in text
    assert "ReadCutover" not in text


def test_Migration0018线性存在且不修改0017():
    migration = ROOT / "app/migrations/versions/20260814_0018_p3_basic_projection_shadow_ready_gate.py"
    assert migration.exists()
    text = migration.read_text("utf-8")
    assert 'down_revision = "20260813_0017"' in text
    assert "CASCADE" not in text.upper()
