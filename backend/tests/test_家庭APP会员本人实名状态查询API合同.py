from __future__ import annotations

import pytest


def test_会员本人实名状态查询API尚未实现() -> None:
    from app.main import create_app

    operation = create_app().openapi()["paths"].get(
        "/api/v1/users/me/identity-verification", {}
    )
    if "get" not in operation:
        pytest.fail("Member identity verification status API is not implemented")


def test_实名状态响应只允许脱敏身份证号() -> None:
    from app.modules.auth.schemas import IdentityVerificationStatusResponse

    response = IdentityVerificationStatusResponse(
        status="submitted",
        submission_version=1,
        id_card_masked="110105********002X",
        submitted_at="2026-08-09T00:00:00Z",
    )
    public = response.model_dump_json().lower()
    assert "11010519491231002x" not in public
    for forbidden in ("real_name", "id_card_ciphertext", "nonce", "key_id"):
        assert forbidden not in public
