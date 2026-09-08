from __future__ import annotations

import base64
import binascii
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

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
    return HTTPException(
        status_code=401, detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
    )


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Invalid token encoding")
    padding = "=" * (-len(value) % 4)
    return base64.b64decode((value + padding).encode("ascii"), altchars=b"-_", validate=True)


def _json_dumps(value: dict[str, Any]) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _epoch_seconds(value: datetime | int | float) -> int:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp())
    if type(value) is not int:
        raise ValueError("Invalid token timestamp")
    return value


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
    payload.setdefault("aud", "kanglin-phase1-api")
    payload.setdefault("iat", int(now.timestamp()))
    payload.setdefault("nbf", payload["iat"])
    payload.setdefault("jti", str(uuid4()))
    payload.setdefault(
        "exp",
        int((now + timedelta(minutes=settings.jwt_access_token_expire_minutes)).timestamp()),
    )
    payload["iat"] = _epoch_seconds(payload["iat"])
    payload["nbf"] = _epoch_seconds(payload["nbf"])
    payload["exp"] = _epoch_seconds(payload["exp"])

    header = {"alg": settings.jwt_algorithm, "typ": "JWT"}
    encoded_header = _base64url_encode(_json_dumps(header).encode("utf-8"))
    encoded_payload = _base64url_encode(_json_dumps(payload).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_payload}"
    signature = _sign_jwt(signing_input, settings.jwt_secret_key)
    return f"{signing_input}.{signature}"


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate token field")
        result[key] = value
    return result


def _validate_access_profile(payload: dict[str, Any], lifetime_minutes: int) -> None:
    if (payload.get("iss") != "kanglin" or payload.get("aud") != "kanglin-phase1-api"
            or payload.get("typ") != "access"):
        raise _jwt_error()
    subject = payload.get("sub")
    role = payload.get("role")
    if (type(subject) is not str or re.fullmatch(r"[1-9][0-9]{0,18}", subject) is None
            or int(subject) > 9223372036854775807
            or type(role) is not str or role not in SUPPORTED_ROLES):
        raise _jwt_error()
    for field in ("iat", "nbf", "exp"):
        if type(payload.get(field)) is not int or payload[field] < 0:
            raise _jwt_error()
    now = int(datetime.now(UTC).timestamp())
    issued, not_before, expires = payload["iat"], payload["nbf"], payload["exp"]
    if (type(lifetime_minutes) is not int or not 15 <= lifetime_minutes <= 120
            or not 0 < expires - issued <= lifetime_minutes * 60
            or not issued <= not_before < expires
            or issued > now + 5 or not_before > now + 5 or expires <= now - 5):
        raise _jwt_error()
    identifier = payload.get("jti")
    try:
        parsed = UUID(identifier) if type(identifier) is str else None
    except (ValueError, AttributeError):
        raise _jwt_error() from None
    if parsed is None or parsed.version != 4 or str(parsed) != identifier:
        raise _jwt_error()


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    if settings.jwt_algorithm != "HS256" or not isinstance(token, str) or len(token) > 8192:
        raise _jwt_error()
    try:
        encoded_header, encoded_payload, signature = token.split(".", 2)
        header = json.loads(_base64url_decode(encoded_header), object_pairs_hook=_unique_json_object)
        payload = json.loads(_base64url_decode(encoded_payload), object_pairs_hook=_unique_json_object)
        _base64url_decode(signature)
    except (ValueError, UnicodeError, binascii.Error, RecursionError):
        raise _jwt_error() from None

    if (not isinstance(header, dict) or not isinstance(payload, dict)
            or header.get("alg") != "HS256" or header.get("typ") != "JWT"):
        raise _jwt_error()

    expected_signature = _sign_jwt(
        f"{encoded_header}.{encoded_payload}",
        settings.jwt_secret_key,
    )
    if not hmac.compare_digest(signature, expected_signature):
        raise _jwt_error()

    _validate_access_profile(payload, settings.jwt_access_token_expire_minutes)

    return payload


def decode_access_token_for_step_up(token: str) -> dict[str, Any]:
    """Validate the stricter access-token profile required by step-up auth."""
    payload = decode_access_token(token)
    now = int(datetime.now(timezone.utc).timestamp())
    if payload.get("iss") != "kanglin" or payload.get("typ") != "access":
        raise _jwt_error()
    if type(payload.get("sub")) is not str or not payload["sub"].isdigit():
        raise _jwt_error()
    if int(payload["sub"]) <= 0:
        raise _jwt_error()
    issued_at = payload.get("iat")
    expires_at = payload.get("exp")
    if type(issued_at) is not int or type(expires_at) is not int:
        raise _jwt_error()
    if issued_at > now + 5 or expires_at <= now or expires_at <= issued_at:
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
        raise HTTPException(status_code=401, detail="Authentication required", headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"})

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token == "":
        raise HTTPException(status_code=401, detail="Authentication required", headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"})

    return build_current_user_from_claims(decode_access_token(token))


_ACCESS_BEARER = Security(HTTPBearer(scheme_name="AccessBearer", bearerFormat="JWT", auto_error=False))


async def get_current_user_from_jwt(
    request: Request, _credential: HTTPAuthorizationCredentials | None = _ACCESS_BEARER,
) -> CurrentUser:
    from app.core.认证当前性 import verify_current_user

    current_user = build_current_user_from_authorization_header(request.headers.get("authorization"))
    return await verify_current_user(current_user)


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
