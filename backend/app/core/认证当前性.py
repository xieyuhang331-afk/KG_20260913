from __future__ import annotations

import asyncio

from fastapi import HTTPException
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import get_session_factory
from app.modules.auth.repository import get_user_currentness


def _stale() -> HTTPException:
    return HTTPException(
        status_code=401, detail="ACCESS_TOKEN_STALE",
        headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
    )


async def _read_authority(user_id: int):
    session = get_session_factory()()
    primary = None
    try:
        async with asyncio.timeout(3):
            await session.execute(text("SET TRANSACTION READ ONLY"))
            return await get_user_currentness(session, user_id)
    except BaseException as error:
        primary = error
        raise
    finally:
        cleanup_error = None
        for cleanup in (session.rollback, session.close):
            try:
                await cleanup()
            except BaseException as error:
                if cleanup_error is None or isinstance(error, asyncio.CancelledError):
                    cleanup_error = error
        if (not isinstance(primary, asyncio.CancelledError) and cleanup_error is not None
                and (isinstance(cleanup_error, asyncio.CancelledError) or primary is None)):
            raise cleanup_error


async def verify_current_user(current_user):
    """Release the authority connection before admitting any business dependency."""
    try:
        authority = await _read_authority(current_user.id)
        if (authority is None or authority.status != "active"
                or authority.exited_at is not None or authority.deletion_requested_at is not None
                or authority.id != current_user.id or authority.role != current_user.role
                or authority.tenant_id != current_user.tenant_id):
            raise _stale()
        allowed_scope = {
            "org_admin": {"org_id"}, "province_admin": {"province"},
            "city_admin": {"province", "city"},
        }.get(current_user.role, set())
        if any(
            getattr(current_user, field) not in (None, "")
            for field in ("org_id", "province", "city") if field not in allowed_scope
        ):
            raise _stale()
        context = get_settings().auth_context_map.get(str(current_user.id), {})
        if current_user.role == "org_admin":
            expected_org = authority.tenant_org_id if authority.tenant_id is not None else context.get("org_id")
            expected_org = int(expected_org) if expected_org not in (None, "") else None
            if expected_org != current_user.org_id:
                raise _stale()
        if current_user.role in ("province_admin", "city_admin"):
            if not context.get("province") or context["province"] != current_user.province:
                raise _stale()
            if current_user.role == "city_admin" and (
                not context.get("city") or context["city"] != current_user.city
            ):
                raise _stale()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=503, detail="AUTHORITY_UNAVAILABLE",
            headers={"Cache-Control": "no-store"},
        ) from None
    return current_user
