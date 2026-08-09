from __future__ import annotations

from pathlib import Path


MIGRATION = (
    Path(__file__).parents[1]
    / "app"
    / "migrations"
    / "versions"
    / "20260809_0013_p2_member_detection_report.py"
)


def test_会员本人检测报告迁移尚未实现() -> None:
    assert MIGRATION.exists(), "Member self detection report migration is not implemented"


def test_检测报告迁移冻结revision与表结构() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "20260809_0013"' in source
    assert 'down_revision = "20260809_0012"' in source
    assert '"detection_report"' in source
    for column in (
        "user_id",
        "store_id",
        "report_type",
        "detection_time",
        "view_status",
        "summary",
        "report_schema_version",
        "report_data",
        "created_at",
    ):
        assert f'"{column}"' in source
    assert "idx_detection_report_user_time" in source
    assert "idx_detection_report_user_type_time" in source
    assert "CASCADE" not in source.upper()
