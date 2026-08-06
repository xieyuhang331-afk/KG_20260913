from pathlib import Path

import pytest


REVISION_FILENAME = (
    "20260806_0008_p2_identity_member_no_allocation.py"
)
EXPECTED_RED = "MemberNo allocation migration revision is not implemented"


def test_MemberNo分配账本迁移尚未实现():
    revision_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "migrations"
        / "versions"
        / REVISION_FILENAME
    )
    if not revision_path.is_file():
        pytest.fail(EXPECTED_RED)

    assert revision_path.is_file()
