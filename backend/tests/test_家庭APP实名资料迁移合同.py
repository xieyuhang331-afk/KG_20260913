from pathlib import Path

import pytest


REVISION_FILENAME = "20260809_0012_p1_identity_verification_submission.py"


def test_家庭APP实名资料迁移尚未实现() -> None:
    path = Path(__file__).parents[1] / "app/migrations/versions" / REVISION_FILENAME
    if not path.is_file():
        pytest.fail("Family APP identity submission migration revision is not implemented")
    source = path.read_text(encoding="utf-8")
    assert 'revision = "20260809_0012"' in source
    assert 'down_revision = "20260808_0011"' in source
    assert "identity_verification_submission" in source
    for required in (
        "real_name_ciphertext", "real_name_nonce", "id_card_ciphertext",
        "id_card_nonce", "id_card_masked", "encryption_key_id",
        "content_digest", "id_card_digest", "idempotency_key_digest",
    ):
        assert required in source
    for forbidden in ("id_card_front", "id_card_back", "image_url", "minio", "ocr"):
        assert forbidden not in source.lower()
