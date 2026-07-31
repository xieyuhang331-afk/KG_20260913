from __future__ import annotations

from sqlalchemy import select

from app.core.sqlalchemy_mapping import map_core_model_classes
from app.modules.auth.models import User


def _ensure_mapped() -> None:
    map_core_model_classes()


async def user_exists_by_phone(session, phone: str) -> bool:
    _ensure_mapped()
    result = await session.execute(select(User.id).where(User.phone == phone).limit(1))
    return result.scalar_one_or_none() is not None


async def get_user_by_phone(session, phone: str):
    _ensure_mapped()
    result = await session.execute(select(User).where(User.phone == phone).limit(1))
    return result.scalar_one_or_none()


async def get_user_by_id(session, user_id: int):
    _ensure_mapped()
    result = await session.execute(select(User).where(User.id == user_id).limit(1))
    return result.scalar_one_or_none()


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
    user.real_name = real_name
    user.id_card = id_card
    user.verify_status = verify_status
    await session.flush()
    return user


async def update_user_tenant_binding(session, user, *, tenant_id: int):
    user.tenant_id = tenant_id
    await session.flush()
    return user
