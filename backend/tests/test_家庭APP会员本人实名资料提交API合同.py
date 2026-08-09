from __future__ import annotations

import pytest


def test_会员本人实名资料提交API尚未实现() -> None:
    from app.main import create_app

    operation = create_app().openapi()["paths"].get(
        "/api/v1/users/me/identity-verification", {}
    )
    if "put" not in operation:
        pytest.fail("Member identity verification submission API is not implemented")


def test_实名提交请求不允许客户端指定用户或图片字段() -> None:
    from app.modules.auth.schemas import IdentityVerificationSubmissionRequest

    with pytest.raises(ValueError):
        IdentityVerificationSubmissionRequest.model_validate(
            {
                "real_name": "张三",
                "id_card": "11010519491231002X",
                "idempotency_key": "submit-v1",
                "consent_version": "identity-consent-v1",
                "user_id": 7,
            }
        )
    for forbidden in ("id_card_front", "id_card_back", "face_image"):
        with pytest.raises(ValueError):
            IdentityVerificationSubmissionRequest.model_validate(
                {
                    "real_name": "张三",
                    "id_card": "11010519491231002X",
                    "idempotency_key": "submit-v1",
                    "consent_version": "identity-consent-v1",
                    forbidden: "forbidden",
                }
            )
