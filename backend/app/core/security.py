from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
import hmac
import json
from hashlib import sha256
from typing import Any

from fastapi import HTTPException, Request

from app.core.config import get_settings


SUPPORTED_ROLES = {
    "super_admin",
    "province_admin",
    "city_admin",
    "sys_admin",
    "expert",
    "org_admin",
    "org_operator",
    "therapist",
    "host",
    "member",
}


@dataclass(frozen=True)
class CurrentUser:
    id: int
    role: str
    tenant_id: int | None = None
    org_id: int | None = None
    province: str | None = None
    city: str | None = None


def _jwt_error() -> HTTPException:
    return HTTPException(status_code=401, detail="Invalid or expired token")


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _json_dumps(value: dict[str, Any]) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _epoch_seconds(value: datetime | int | float) -> int:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp())
    return int(value)


def _sign_jwt(signing_input: str, secret_key: str) -> str:
    signature = hmac.new(
        secret_key.encode("utf-8"),
        signing_input.encode("ascii"),
        sha256,
    ).digest()
    return _base64url_encode(signature)


def create_access_token(claims: dict[str, Any]) -> str:
    settings = get_settings()
    if settings.jwt_algorithm != "HS256":
        raise ValueError("Only HS256 JWT is supported")

    now = datetime.now(timezone.utc)
    payload = dict(claims)
    payload.setdefault("iss", "kanglin")
    payload.setdefault("typ", "access")
    payload.setdefault("iat", int(now.timestamp()))
    payload.setdefault(
        "exp",
        int((now + timedelta(minutes=settings.jwt_access_token_expire_minutes)).timestamp()),
    )
    payload["iat"] = _epoch_seconds(payload["iat"])
    payload["exp"] = _epoch_seconds(payload["exp"])

    header = {"alg": settings.jwt_algorithm, "typ": "JWT"}
    encoded_header = _base64url_encode(_json_dumps(header).encode("utf-8"))
    encoded_payload = _base64url_encode(_json_dumps(payload).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_payload}"
    signature = _sign_jwt(signing_input, settings.jwt_secret_key)
    return f"{signing_input}.{signature}"


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        encoded_header, encoded_payload, signature = token.split(".", 2)
        header = json.loads(_base64url_decode(encoded_header))
        payload = json.loads(_base64url_decode(encoded_payload))
    except Exception as exc:
        raise _jwt_error() from exc

    if header.get("alg") != settings.jwt_algorithm or header.get("typ") != "JWT":
        raise _jwt_error()

    expected_signature = _sign_jwt(
        f"{encoded_header}.{encoded_payload}",
        settings.jwt_secret_key,
    )
    if not hmac.compare_digest(signature, expected_signature):
        raise _jwt_error()

    try:
        expires_at = int(payload["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise _jwt_error() from exc
    if int(datetime.now(timezone.utc).timestamp()) >= expires_at:
        raise _jwt_error()

    return payload


def _parse_claim_required_int(value: Any) -> int:
    if value is None or value == "":
        raise HTTPException(status_code=401, detail="Invalid token claims")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid token claims") from exc


def _parse_claim_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid token claims") from exc


def build_current_user_from_claims(claims: dict[str, Any]) -> CurrentUser:
    role = claims.get("role")
    if role is None or role == "":
        raise HTTPException(status_code=401, detail="Invalid token claims")
    if role not in SUPPORTED_ROLES:
        raise HTTPException(status_code=403, detail="Unsupported role")

    return CurrentUser(
        id=_parse_claim_required_int(claims.get("sub")),
        role=role,
        tenant_id=_parse_claim_optional_int(claims.get("tenant_id")),
        org_id=_parse_claim_optional_int(claims.get("org_id")),
        province=claims.get("province"),
        city=claims.get("city"),
    )


def build_current_user_from_authorization_header(authorization: str | None) -> CurrentUser:
    if authorization is None or authorization == "":
        raise HTTPException(status_code=401, detail="Authentication required")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token == "":
        raise HTTPException(status_code=401, detail="Authentication required")

    return build_current_user_from_claims(decode_access_token(token))


async def get_current_user_from_jwt(request: Request) -> CurrentUser:
    return build_current_user_from_authorization_header(request.headers.get("authorization"))


def _parse_required_int(value: str | None) -> int:
    if value is None or value == "":
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        return int(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid current user header") from exc


def _parse_optional_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid current user header") from exc


async def get_current_user(request: Request) -> CurrentUser:
    user_id = _parse_required_int(request.headers.get("x-user-id"))
    role = request.headers.get("x-user-role")
    if role is None or role == "":
        raise HTTPException(status_code=401, detail="Authentication required")
    if role not in SUPPORTED_ROLES:
        raise HTTPException(status_code=403, detail="Unsupported role")

    return CurrentUser(
        id=user_id,
        role=role,
        tenant_id=_parse_optional_int(request.headers.get("x-tenant-id")),
        org_id=_parse_optional_int(request.headers.get("x-org-id")),
        province=request.headers.get("x-user-province"),
        city=request.headers.get("x-user-city"),
    )
