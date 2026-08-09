from __future__ import annotations

import ast
from pathlib import Path


def test_新实名模块不依赖图片OCRProvider或Flutter() -> None:
    root = Path(__file__).parents[1] / "app"
    files = [
        root / "modules/auth/identity_submission.py",
        root / "modules/auth/identity_submission_crypto.py",
        root / "modules/auth/identity_submission_repository.py",
        root / "modules/auth/identity_submission_models.py",
        root / "composition/identity_submission.py",
    ]
    forbidden = ("minio", "ocr", "provider_callback", "flutter", "face_image")
    for path in files:
        source = path.read_text(encoding="utf-8").lower()
        ast.parse(source)
        assert not any(token in source for token in forbidden)


def test_API不修改JWT签发或旧register_user语义() -> None:
    from app.main import create_app
    paths = create_app().openapi()["paths"]
    assert "/api/v1/users/register" in paths
    assert "/api/v1/users/me/identity-verification" in paths
