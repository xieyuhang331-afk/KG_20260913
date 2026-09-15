from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import BigInteger, String, bindparam, select, text

from app.core.sqlalchemy_mapping import map_core_model_classes
from app.modules.auth.models import User


@dataclass(frozen=True, slots=True)
class AuthenticationSubject:
    id: int
    phone: str
    password_hash: str = field(repr=False)
    role: str
    status: str | None
    tenant_id: int | None
    exited_at: datetime | None
    deletion_requested_at: datetime | None
    tenant_org_id: int | None


@dataclass(frozen=True, slots=True)
class UserCurrentness:
    id: int
    role: str
    tenant_id: int | None
    status: str | None
    exited_at: datetime | None
    deletion_requested_at: datetime | None
    tenant_org_id: int | None


@dataclass(frozen=True, slots=True)
class ReviewerCredentialMaterial:
    id: int
    password_hash: str = field(repr=False)
    role: str
    status: str | None
    tenant_id: int | None
    exited_at: datetime | None
    deletion_requested_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RegisteredMember:
    id: int
    phone: str
    role: str
    status: str | None
    verify_status: str | None
    tenant_id: int | None
    created_at: datetime | None


def _ensure_mapped() -> None:
    map_core_model_classes()


async def user_exists_by_phone(session, phone: str) -> bool:
    _ensure_mapped()
    result = await session.execute(select(User.id).where(User.phone == phone).limit(1))
    return result.scalar_one_or_none() is not None


async def create_registered_member(
    session, *, phone: str, password_hash: str
) -> RegisteredMember:
    statement = text(
        "SELECT id,phone,role,status,verify_status,tenant_id,created_at "
        "FROM public.auth_register_member_v1(:phone,:password_hash)"
    ).bindparams(
        bindparam("phone", type_=String(11)),
        bindparam("password_hash", type_=String(255)),
    ).params(phone=phone, password_hash=password_hash)
    result = await session.execute(statement)
    row = result.mappings().one()
    return RegisteredMember(**row)


async def get_user_by_phone(session, phone: str):
    statement = text(
        "SELECT id,phone,password_hash,role,status,tenant_id,exited_at,"
        "deletion_requested_at,tenant_org_id "
        "FROM public.auth_login_subject_v1(:phone)"
    ).bindparams(bindparam("phone", type_=String(11))).params(phone=phone)
    result = await session.execute(statement)
    row = result.mappings().one_or_none()
    return AuthenticationSubject(**row) if row is not None else None


async def get_user_by_id(session, user_id: int):
    _ensure_mapped()
    result = await session.execute(select(User).where(User.id == user_id).limit(1))
    return result.scalar_one_or_none()


async def get_user_currentness(session, user_id: int):
    """Read only the authority fields required to invalidate a stale access token."""
    statement = text(
        "SELECT id,role,tenant_id,status,exited_at,deletion_requested_at,"
        "tenant_org_id FROM public.auth_user_currentness_v1(:user_id)"
    ).bindparams(bindparam("user_id", type_=BigInteger())).params(user_id=user_id)
    result = await session.execute(statement)
    row = result.mappings().one_or_none()
    return UserCurrentness(**row) if row is not None else None


async def get_reviewer_credential_material(session, user_id: int):
    statement = text(
        "SELECT id,password_hash,role,status,tenant_id,exited_at,"
        "deletion_requested_at,updated_at "
        "FROM public.auth_user_credential_material_v1(:user_id)"
    ).bindparams(bindparam("user_id", type_=BigInteger())).params(user_id=user_id)
    result = await session.execute(statement)
    row = result.mappings().one_or_none()
    return ReviewerCredentialMaterial(**row) if row is not None else None


async def get_user_for_tenant_binding_update(session, user_id: int):
    _ensure_mapped()
    result = await session.execute(select(User).where(User.id == user_id).with_for_update())
    return result.scalar_one_or_none()


async def create_user_record(session, *, user_data: dict):
    _ensure_mapped()
    user = User()
    for key, value in user_data.items():
        setattr(user, key, value)

    session.add(user)
    await session.flush()
    return user


async def update_user_identity(
    session,
    user,
    *,
    real_name: str,
    id_card: str,
    verify_status: str,
):
    del session, user, real_name, id_card, verify_status
    raise RuntimeError("LEGACY_IDENTITY_ENDPOINT_RETIRED")


async def update_user_tenant_binding(session, user, *, tenant_id: int):
    del session, user, tenant_id
    raise RuntimeError("LEGACY_MEMBER_TENANT_BINDING_RETIRED")
