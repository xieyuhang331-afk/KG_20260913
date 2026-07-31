from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException


def test_create_and_decode_access_token_returns_claims():
    from app.core.security import create_access_token, decode_access_token

    token = create_access_token({"sub": "101", "role": "member", "tenant_id": 202})

    claims = decode_access_token(token)

    assert claims["iss"] == "kanglin"
    assert claims["typ"] == "access"
    assert claims["sub"] == "101"
    assert claims["role"] == "member"
    assert claims["tenant_id"] == 202
    assert "iat" in claims
    assert "exp" in claims


def test_decode_access_token_rejects_expired_token():
    from app.core.security import create_access_token, decode_access_token

    token = create_access_token(
        {
            "sub": "101",
            "role": "member",
            "exp": datetime.now(timezone.utc) - timedelta(seconds=1),
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(token)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid or expired token"


@pytest.mark.parametrize(
    "claims",
    [
        {"role": "member"},
        {"sub": "101"},
        {"sub": "not-an-int", "role": "member"},
        {"sub": "101", "role": "SA"},
        {"sub": "101", "role": "member", "tenant_id": "not-an-int"},
        {"sub": "101", "role": "member", "org_id": "not-an-int"},
    ],
)
def test_build_current_user_from_claims_validates_claims(claims):
    from app.core.security import build_current_user_from_claims

    with pytest.raises(HTTPException):
        build_current_user_from_claims(claims)


def test_build_current_user_from_claims_returns_current_user():
    from app.core.security import build_current_user_from_claims

    current_user = build_current_user_from_claims(
        {
            "sub": "101",
            "role": "org_admin",
            "tenant_id": 202,
            "org_id": 303,
            "province": "浙江省",
            "city": "杭州市",
        }
    )

    assert current_user.id == 101
    assert current_user.role == "org_admin"
    assert current_user.tenant_id == 202
    assert current_user.org_id == 303
    assert current_user.province == "浙江省"
    assert current_user.city == "杭州市"


def test_verify_password_accepts_registered_password_hash():
    from app.modules.auth.service import hash_password, verify_password

    password_hash = hash_password("Password123")

    assert verify_password("Password123", password_hash) is True
    assert verify_password("WrongPassword123", password_hash) is False


@pytest.mark.parametrize(
    "password_hash",
    [
        "",
        "plain-text",
        "pbkdf2_sha256$bad-iterations$salt$digest",
        "pbkdf2_sha256$200000$salt",
        "sha256$200000$salt$digest",
    ],
)
def test_verify_password_rejects_invalid_hash_formats(password_hash):
    from app.modules.auth.service import verify_password

    assert verify_password("Password123", password_hash) is False
