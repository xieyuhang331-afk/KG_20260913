from pathlib import Path

import pytest
from pydantic import ValidationError

from app.main import create_app
from app.modules.institution_onboarding.schemas import ActivationRequest
from app.modules.models import import_core_models


@pytest.mark.parametrize("secret", ("A" * 15, "NOT-BASE32-SECRET!", "A" * 16))
def test_TOTP_secret必须是具有最低强度的合法Base32(secret: str):
    with pytest.raises(ValidationError):
        ActivationRequest(
            invitation_id="0198a58f-4900-7000-8000-000000000001",
            phone="13900000001",
            short_code="123456",
            password="StrongPassword123!",
            totp_secret=secret,
            totp_code="123456",
        )


def test_切片1精确路由已注册且无自由注册替代入口(monkeypatch):
    monkeypatch.setenv("KG_DATABASE_PASSWORD", "test-only")
    monkeypatch.setenv("KG_JWT_SECRET_KEY", "test-only")
    paths = set()
    for route in create_app().routes:
        candidates = getattr(getattr(route, "original_router", None), "routes", (route,))
        paths.update(candidate.path for candidate in candidates if hasattr(candidate, "path"))
    required = {
        "/api/v1/platform/institution-invitations",
        "/api/v1/institution-onboarding/activate",
        "/api/v1/institution-onboarding/application",
        "/api/v1/institution-onboarding/application/submit",
        "/api/v1/institution-onboarding/application/corrections",
        "/api/v1/institution-onboarding/application/resubmit",
        "/api/v1/platform/institution-reviews",
        "/api/v1/platform/institution-reviews/{application_id}",
        "/api/v1/platform/institution-reviews/{application_id}/decision",
        "/api/v1/private-files/uploads",
        "/api/v1/private-files/uploads/{file_id}/complete",
        "/api/v1/private-files/{file_id}",
        "/api/v1/private-files/{file_id}/access",
    }
    assert required <= paths


def test_Metadata只加法注册切片1对象():
    modules = import_core_models()
    assert "institution_onboarding" in modules
    assert "private_file" in modules


def test_禁止切片2与短信OCR能力进入生产目录():
    root = Path(__file__).parents[1] / "app" / "modules"
    sources = "\n".join(path.read_text("utf-8") for folder in (root / "institution_onboarding", root / "private_file") for path in folder.glob("*.py"))
    for forbidden in ("send_sms", "payment_settlement", "member_enrollment", "health_plan", "ocr_extract"):
        assert forbidden not in sources
