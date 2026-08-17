from __future__ import annotations

import hashlib
import secrets

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.permissions import ensure_can_access_own_user_resource
from app.core.security import CurrentUser, create_access_token
from app.modules.auth.repository import (
    create_user_record,
    get_user_for_tenant_binding_update,
    get_user_by_id,
    get_user_by_phone,
    update_user_identity,
    update_user_tenant_binding,
    user_exists_by_phone,
)
from app.modules.auth.schemas import (
    AuthLoginRequest,
    AuthLoginResponse,
    AuthLoginUser,
    TenantBindingRequest,
    TenantBindingResponse,
    UserIdentityRequest,
    UserIdentityResponse,
    UserRegisterRequest,
    UserRegisterResponse,
)
from app.modules.tenant.repository import get_tenant_by_id_for_binding
from app.modules.tenant.models import Tenant


def mask_id_card(id_card: str) -> str:
    if len(id_card) != 18 or not id_card.isdigit():
        raise ValueError("Invalid id_card")
    return f"{id_card[:6]}********{id_card[-4:]}"


def hash_password(password: str) -> str:
    iterations = 200_000
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return f"pbkdf2_sha256${iterations}${salt}${digest}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt, expected_digest = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
    except (AttributeError, ValueError):
        return False

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return secrets.compare_digest(digest, expected_digest)


def get_controlled_auth_context(user) -> dict:
    settings = get_settings()
    return dict(settings.auth_context_map.get(str(user.id), {}))


def _require_context_value(context: dict, key: str):
    value = context.get(key)
    if value is None or value == "":
        raise HTTPException(status_code=403, detail="Login context is not configured")
    return value


async def _tenant_org_id(session, tenant_id: int) -> int | None:
    result = await session.execute(select(Tenant.org_id).where(Tenant.id == tenant_id))
    return result.scalar_one_or_none()


def _build_login_claims(user, *, dynamic_org_id: int | None = None) -> dict:
    context = get_controlled_auth_context(user)
    claims = {
        "sub": str(user.id),
        "role": user.role,
        "tenant_id": user.tenant_id,
    }

    if user.role == "org_admin":
        if user.tenant_id is not None and dynamic_org_id is not None:
            claims["org_id"] = int(dynamic_org_id)
        elif user.tenant_id is not None:
            raise HTTPException(status_code=403, detail="Login context is not configured")
        elif context.get("org_id") not in (None, ""):
            claims["org_id"] = int(context["org_id"])
    elif user.role == "province_admin":
        claims["province"] = _require_context_value(context, "province")
    elif user.role == "city_admin":
        claims["province"] = _require_context_value(context, "province")
        claims["city"] = _require_context_value(context, "city")

    return claims


async def get_onboarding_account_for_login(user_id: int):
    from app.core.database import get_slice1_session_factory
    from app.modules.institution_onboarding.repository import InstitutionOnboardingRepository
    factory = get_slice1_session_factory("reader")
    async with factory() as reader_session:
        return await InstitutionOnboardingRepository(reader_session).account_for_user(user_id)


async def get_therapist_account_for_login(session, user_id: int):
    from sqlalchemy import text
    result = await session.execute(
        text("SELECT * FROM public.therapist_totp_for_login_v1(:user_id)"),
        {"user_id": user_id},
    )
    return result.mappings().one_or_none()


async def login_user(session, payload: AuthLoginRequest) -> AuthLoginResponse:
    user = await get_user_by_phone(session, payload.phone)
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="User is not active")

    account = await get_onboarding_account_for_login(user.id) if user.role == "org_admin" else None
    if account is not None and account.totp_enabled:
        from app.modules.institution_onboarding.domain import verify_totp
        from app.modules.institution_onboarding.service import OnboardingSecrets, utcnow
        if payload.totp_code is None or not verify_totp(
            OnboardingSecrets().decrypt(account.totp_secret_ciphertext), payload.totp_code, at=utcnow()
        ):
            raise HTTPException(status_code=401, detail="TOTP_REQUIRED_OR_INVALID")

    therapist_account = await get_therapist_account_for_login(session, user.id) if user.role == "therapist" else None
    if user.role == "therapist":
        if (
            therapist_account is None
            or therapist_account["therapist_status"] not in {"ACTIVATED", "DRAFT", "SUBMITTED", "UNDER_REVIEW", "NEEDS_CORRECTION", "RESUBMITTED", "APPROVED_ACTIVE", "SUSPENDED"}
            or therapist_account["tenant_id"] != user.tenant_id
        ):
            raise HTTPException(status_code=403, detail="Login context is not configured")
        from app.modules.institution_onboarding.domain import verify_totp
        from app.modules.therapist_qualification.service import TherapistSecrets, utcnow
        secret = TherapistSecrets().decrypt_totp(
            therapist_account["totp_secret_ciphertext"],
            therapist_account["totp_encryption_key_id"],
            therapist_account["tenant_public_id"],
            therapist_account["therapist_id"],
        )
        if payload.totp_code is None or not verify_totp(secret, payload.totp_code, at=utcnow()):
            raise HTTPException(status_code=401, detail="TOTP_REQUIRED_OR_INVALID")

    dynamic_org_id = (
        await _tenant_org_id(session, user.tenant_id)
        if user.role == "org_admin" and user.tenant_id is not None
        else None
    )
    claims = _build_login_claims(user, dynamic_org_id=dynamic_org_id)
    if account is not None:
        claims["amr"] = ["pwd", "totp"]
    if therapist_account is not None:
        claims["amr"] = ["pwd", "totp"]
        claims["therapist_id"] = str(therapist_account["therapist_id"])
    access_token = create_access_token(claims)
    expires_in = get_settings().jwt_access_token_expire_minutes * 60

    return AuthLoginResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=expires_in,
        user=AuthLoginUser(
            id=user.id,
            phone=user.phone,
            role=user.role,
            status=user.status,
            tenant_id=user.tenant_id,
            org_id=claims.get("org_id"),
            province=claims.get("province"),
            city=claims.get("city"),
        ),
    )


async def register_user(session, payload: UserRegisterRequest) -> UserRegisterResponse:
    if await user_exists_by_phone(session, payload.phone):
        raise HTTPException(status_code=409, detail="User already exists")

    try:
        user = await create_user_record(
            session,
            user_data={
                "phone": payload.phone,
                "password_hash": hash_password(payload.password),
                "role": "member",
                "status": "active",
                "tenant_id": None,
            },
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="User already exists") from exc

    return UserRegisterResponse(
        id=user.id,
        phone=user.phone,
        role=user.role,
        status=user.status,
        verify_status=user.verify_status,
        tenant_id=user.tenant_id,
        created_at=user.created_at,
    )


async def submit_user_identity(
    session,
    current_user: CurrentUser,
    user_id: int,
    payload: UserIdentityRequest,
) -> UserIdentityResponse:
    ensure_can_access_own_user_resource(current_user, user_id)

    user = await get_user_by_id(session, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        user = await update_user_identity(
            session,
            user,
            real_name=payload.real_name,
            id_card=payload.id_card,
            verify_status="submitted",
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    return UserIdentityResponse(
        user_id=user.id,
        real_name=user.real_name,
        id_card_masked=mask_id_card(user.id_card),
        verify_status=user.verify_status,
    )


async def bind_user_tenant(
    session,
    current_user: CurrentUser,
    user_id: int,
    payload: TenantBindingRequest,
) -> TenantBindingResponse:
    ensure_can_access_own_user_resource(current_user, user_id)

    user = await get_user_for_tenant_binding_update(session, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role != "member" or user.status != "active":
        raise HTTPException(status_code=403, detail="Forbidden")
    if user.tenant_id is not None:
        raise HTTPException(status_code=409, detail="User already bound to tenant")

    tenant = await get_tenant_by_id_for_binding(session, payload.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if tenant["status"] != "active":
        raise HTTPException(status_code=409, detail="Tenant is not active")

    try:
        user = await update_user_tenant_binding(session, user, tenant_id=payload.tenant_id)
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    return TenantBindingResponse(
        user_id=user.id,
        tenant_id=tenant["tenant_id"],
        tenant_code=tenant["tenant_code"],
        tenant_name=tenant["name"],
        bound_at=user.updated_at,
    )
