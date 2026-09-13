from __future__ import annotations

import asyncio
import hashlib
import secrets

from fastapi import HTTPException
from sqlalchemy.exc import (
    DBAPIError,
    DisconnectionError,
    IntegrityError,
    InterfaceError,
    OperationalError,
)
from sqlalchemy.exc import (
    TimeoutError as SQLAlchemyTimeoutError,
)

from app.core.config import get_settings
from app.core.security import CurrentUser, create_access_token
from app.modules.auth.repository import (
    RegisteredMember,
    create_registered_member,
    get_direct_org_admin_login_account,
    get_user_by_phone,
)
from app.modules.auth.schemas import (
    AuthLoginRequest,
    AuthLoginResponse,
    AuthLoginUser,
    TenantBindingRequest,
    UserIdentityRequest,
    UserRegisterRequest,
    UserRegisterResponse,
)
from app.modules.direct_institution_onboarding.service import (
    direct_org_admin_totp_aad,
    open_totp_secret,
)


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


# Not an account credential: keeps unknown subjects on the same PBKDF2 path.
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))
_CANCELLATIONS = (asyncio.CancelledError, KeyboardInterrupt, SystemExit)
_PHONE_UNIQUE_CONSTRAINTS = {
    "uq_user_phone",
    "uq_identity_phone_claim_active_digest",
}


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


def _driver_error_sources(error: BaseException):
    if not isinstance(error, DBAPIError):
        return
    original = error.orig
    if isinstance(original, BaseException):
        yield original
        driver_cause = original.__cause__
        if isinstance(driver_cause, BaseException) and driver_cause is not original:
            yield driver_cause


def _driver_sqlstate(error: BaseException) -> str | None:
    value = getattr(error, "sqlstate", None) or getattr(error, "pgcode", None)
    return str(value) if value is not None else None


def _driver_constraint(error: BaseException) -> str | None:
    value = getattr(error, "constraint_name", None)
    if value is None:
        value = getattr(getattr(error, "diag", None), "constraint_name", None)
    return str(value) if value is not None else None


def _phone_unique_conflict(error: BaseException) -> bool:
    if not isinstance(error, IntegrityError):
        return False
    identities = [
        (_driver_sqlstate(item), _driver_constraint(item))
        for item in _driver_error_sources(error)
    ]
    sqlstates = {sqlstate for sqlstate, _ in identities if sqlstate is not None}
    constraints = {
        constraint for _, constraint in identities if constraint is not None
    }
    return (
        sqlstates == {"23505"}
        and len(constraints) == 1
        and constraints <= _PHONE_UNIQUE_CONSTRAINTS
        and any(
            ("23505", constraint) in identities
            for constraint in _PHONE_UNIQUE_CONSTRAINTS
        )
    )


def _registered_dependency_failure(error: BaseException) -> bool:
    if (
        isinstance(error, RuntimeError)
        and str(error) == "DIRECT_ONBOARDING_DEPENDENCY_UNAVAILABLE"
    ):
        return True
    if isinstance(
        error,
        (
            asyncio.TimeoutError,
            ConnectionError,
            DisconnectionError,
            InterfaceError,
            OperationalError,
            SQLAlchemyTimeoutError,
        ),
    ):
        return True
    if isinstance(error, DBAPIError) and error.connection_invalidated:
        return True
    return any(
        (sqlstate := _driver_sqlstate(item)) is not None
        and sqlstate.startswith("08")
        for item in _driver_error_sources(error)
    )


def _commit_outcome_uncertain(error: BaseException) -> bool:
    if isinstance(
        error,
        (
            asyncio.TimeoutError,
            ConnectionError,
            DisconnectionError,
            SQLAlchemyTimeoutError,
        ),
    ):
        return True
    if not isinstance(error, DBAPIError):
        return False
    sources = list(_driver_error_sources(error))
    sqlstates = {
        sqlstate
        for item in sources
        if (sqlstate := _driver_sqlstate(item)) is not None
    }
    if sqlstates:
        return all(sqlstate.startswith("08") for sqlstate in sqlstates)
    return error.connection_invalidated or any(
        isinstance(item, ConnectionError) for item in sources
    )


async def _rollback_failed_operation(session, primary: BaseException) -> bool:
    try:
        await session.rollback()
        return True
    except _CANCELLATIONS:
        raise
    except Exception:
        primary.add_note("REGISTRATION_ROLLBACK_FAILED")
        return False


async def _close_uncertain_session(session, primary: BaseException) -> bool:
    clean = await _rollback_failed_operation(session, primary)
    try:
        await session.close()
    except _CANCELLATIONS:
        raise
    except Exception:
        primary.add_note("REGISTRATION_SESSION_CLOSE_FAILED")
        clean = False
    return clean


def _application_session_factory():
    from app.core.database import get_session_factory

    return get_session_factory()


async def _registration_insert_is_visible(
    expected: UserRegisterResponse, password_hash: str
) -> bool:
    try:
        factory = _application_session_factory()
        async with factory() as fresh:
            subject = await get_user_by_phone(fresh, expected.phone)
            matched = bool(
                subject is not None
                and subject.id == expected.id
                and subject.phone == expected.phone
                and subject.role == "member"
                and subject.status == "active"
                and subject.tenant_id is None
                and secrets.compare_digest(subject.password_hash, password_hash)
            )
            await fresh.rollback()
            return matched
    except _CANCELLATIONS:
        raise
    except Exception:
        return False


def _registration_response(user: RegisteredMember) -> UserRegisterResponse:
    return UserRegisterResponse(
        id=user.id,
        phone=user.phone,
        role=user.role,
        status=user.status,
        verify_status=user.verify_status,
        tenant_id=user.tenant_id,
        created_at=user.created_at,
    )


def get_controlled_auth_context(user) -> dict:
    settings = get_settings()
    return dict(settings.auth_context_map.get(str(user.id), {}))


def _require_context_value(context: dict, key: str):
    value = context.get(key)
    if value is None or value == "":
        raise HTTPException(status_code=403, detail="Login context is not configured")
    return value


async def _tenant_org_id(user) -> int | None:
    return user.tenant_org_id


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
    from app.modules.institution_onboarding.repository import (
        InstitutionOnboardingRepository,
    )

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
    password_valid = verify_password(payload.password, user.password_hash if user is not None else _DUMMY_PASSWORD_HASH)
    if user is None or not password_valid:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if getattr(user, "exited_at", None) is not None or getattr(user, "deletion_requested_at", None) is not None:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="User is not active")

    account = None
    direct_account = None
    org_admin_totp_verified = False
    if user.role == "org_admin":
        try:
            account = await get_onboarding_account_for_login(user.id)
            direct_account = await get_direct_org_admin_login_account(session, user.id)
        except Exception:
            raise HTTPException(
                status_code=503,
                detail="Authentication service unavailable",
            ) from None
        if (account is None) == (direct_account is None):
            raise HTTPException(
                status_code=403,
                detail="Login context is not configured",
            )
        selected_account = account if account is not None else direct_account
        if not selected_account.totp_enabled:
            raise HTTPException(
                status_code=403,
                detail="Login context is not configured",
            )
        if payload.totp_code is None:
            raise HTTPException(status_code=401, detail="TOTP_REQUIRED_OR_INVALID")

        from app.modules.institution_onboarding.domain import verify_totp
        from app.modules.institution_onboarding.service import OnboardingSecrets, utcnow

        try:
            if direct_account is None:
                totp_secret = OnboardingSecrets().decrypt(
                    account.totp_secret_ciphertext
                )
            else:
                totp_secret = open_totp_secret(
                    direct_account.totp_secret_ciphertext,
                    key_id=direct_account.totp_key_id,
                    aad=direct_org_admin_totp_aad(
                        direct_account.tenant_public_id,
                        direct_account.onboarding_id,
                        direct_account.source_kind,
                        direct_account.credential_id,
                    ),
                )
            org_admin_totp_verified = verify_totp(
                totp_secret,
                payload.totp_code,
                at=utcnow(),
            )
        except Exception:
            raise HTTPException(
                status_code=503,
                detail="Authentication service unavailable",
            ) from None
        if not org_admin_totp_verified:
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
        await _tenant_org_id(user)
        if user.role == "org_admin" and user.tenant_id is not None
        else None
    )
    claims = _build_login_claims(user, dynamic_org_id=dynamic_org_id)
    if org_admin_totp_verified:
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
    password_hash = hash_password(payload.password)
    try:
        user = await create_registered_member(
            session,
            phone=payload.phone,
            password_hash=password_hash,
        )
    except _CANCELLATIONS:
        raise
    except BaseException as error:
        clean = await _rollback_failed_operation(session, error)
        if clean and _phone_unique_conflict(error):
            raise HTTPException(status_code=409, detail="User already exists") from None
        if clean and _registered_dependency_failure(error):
            raise HTTPException(
                status_code=503, detail="Authentication service unavailable"
            ) from None
        raise

    response = _registration_response(user)
    try:
        await session.commit()
        return response
    except _CANCELLATIONS:
        raise
    except BaseException as error:
        clean = await _close_uncertain_session(session, error)
        if clean and _phone_unique_conflict(error):
            raise HTTPException(status_code=409, detail="User already exists") from None
        if not _registered_dependency_failure(error):
            raise
        if not _commit_outcome_uncertain(error):
            raise HTTPException(
                status_code=503, detail="Authentication service unavailable"
            ) from None
        if not clean:
            raise HTTPException(
                status_code=503, detail="Authentication service unavailable"
            ) from None
        if await _registration_insert_is_visible(response, password_hash):
            return response
        raise HTTPException(
            status_code=503, detail="Authentication service unavailable"
        ) from None


async def submit_user_identity(
    session,
    current_user: CurrentUser,
    user_id: int,
    payload: UserIdentityRequest,
) -> None:
    del session, current_user, user_id, payload
    raise HTTPException(
        status_code=410,
        detail="LEGACY_IDENTITY_ENDPOINT_RETIRED",
    )


async def bind_user_tenant(
    session,
    current_user: CurrentUser,
    user_id: int,
    payload: TenantBindingRequest,
) -> None:
    del session, current_user, user_id, payload
    raise HTTPException(
        status_code=410,
        detail="LEGACY_MEMBER_TENANT_BINDING_RETIRED",
    )
