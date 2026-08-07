from pathlib import Path

import pytest


REVISION_FILENAME = "20260807_0010_p2_identity_self_link_bootstrap.py"
EXPECTED_RED = "Identity atomic registration bootstrap migration is not implemented"


def test_SelfLink与原子Bootstrap迁移尚未实现():
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
