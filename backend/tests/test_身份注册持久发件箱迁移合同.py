from pathlib import Path

import pytest


REVISION_FILENAME = "20260808_0011_p1_registration_verified_outbox.py"
EXPECTED_RED = (
    "Registration durable outbox migration revision is not implemented"
)


def test_注册持久发件箱迁移尚未实现():
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
