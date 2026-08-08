from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from functools import wraps
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError


NOW = datetime(2026, 8, 8, 2, 0, tzinfo=timezone.utc)


def _async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


def _user(**overrides):
    values = {
        "id": 1001,
        "role": "member",
        "status": "active",
        "verify_status": "verified",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _profile(**overrides):
    values = {
        "id": 2001,
        "user_id": 1001,
        "gender": "female",
        "birth_date": date(1990, 1, 1),
        "height": Decimal("165.5"),
        "weight": Decimal("55.0"),
        "blood_type": "A",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _payload(*, expected_version=NOW, weight="55.0"):
    from app.modules.user_health.schemas import MemberSelfHealthProfileWriteRequest

    return MemberSelfHealthProfileWriteRequest(
        gender="female",
        birth_date=date(1990, 1, 1),
        height=Decimal("165.5"),
        weight=Decimal(weight),
        blood_type="A",
        expected_version=expected_version,
    )


class _Session:
    def __init__(self, *, commit_error: BaseException | None = None):
        self.commit_error = commit_error
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self):
        self.rollbacks += 1


class _ConfirmationContext:
    def __init__(self, session):
        self.session = session
        self.exited = False

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, traceback):
        self.exited = True


def _provider(session):
    context = _ConfirmationContext(session)
    return lambda: lambda: context


@_async_test
@pytest.mark.parametrize(
    "user",
    [
        None,
        _user(role="org_admin"),
        _user(status="disabled"),
        _user(verify_status="pending"),
    ],
    ids=["missing", "wrong_role", "disabled", "unverified"],
)
async def test_当前用户不是有效已实名member时fail_closed(user) -> None:
    from app.modules.user_health.service import get_member_self_health_profile

    with (
        patch(
            "app.modules.user_health.service.get_member_profile_user_state",
            new=AsyncMock(return_value=user),
        ),
        patch(
            "app.modules.user_health.service.get_health_profile_by_user_id",
            new=AsyncMock(),
        ) as profile_reader,
    ):
        with pytest.raises(HTTPException) as caught:
            await get_member_self_health_profile(_Session(), user_id=1001)

    assert caught.value.status_code == 409
    assert caught.value.detail == "Health profile is unavailable for current member"
    profile_reader.assert_not_awaited()


@_async_test
async def test_GET缺失档案返回NOT_CREATED且零commit() -> None:
    from app.modules.user_health.service import get_member_self_health_profile

    session = _Session()
    with (
        patch(
            "app.modules.user_health.service.get_member_profile_user_state",
            new=AsyncMock(return_value=_user()),
        ),
        patch(
            "app.modules.user_health.service.get_health_profile_by_user_id",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await get_member_self_health_profile(session, user_id=1001)

    assert result.state == "NOT_CREATED"
    assert result.profile is None
    assert result.version is None
    assert session.commits == 0
    assert session.rollbacks == 0


@_async_test
async def test_旧档案缺身高体重时如实返回INCOMPLETE() -> None:
    from app.modules.user_health.service import get_member_self_health_profile

    with (
        patch(
            "app.modules.user_health.service.get_member_profile_user_state",
            new=AsyncMock(return_value=_user()),
        ),
        patch(
            "app.modules.user_health.service.get_health_profile_by_user_id",
            new=AsyncMock(return_value=_profile(height=None, weight=None)),
        ),
    ):
        result = await get_member_self_health_profile(_Session(), user_id=1001)

    assert result.state == "INCOMPLETE"
    assert result.bmi is None


@_async_test
async def test_相同快照稳定重放且零写入零commit() -> None:
    from app.modules.user_health.service import put_member_self_health_profile

    session = _Session()
    update = AsyncMock()
    create = AsyncMock()
    with (
        patch(
            "app.modules.user_health.service.get_member_profile_user_state",
            new=AsyncMock(return_value=_user()),
        ),
        patch(
            "app.modules.user_health.service.get_health_profile_by_user_id",
            new=AsyncMock(return_value=_profile()),
        ),
        patch("app.modules.user_health.service.update_health_profile_record", new=update),
        patch("app.modules.user_health.service.create_health_profile_record", new=create),
    ):
        result = await put_member_self_health_profile(
            session,
            confirmation_session_factory_provider=_provider(_Session()),
            user_id=1001,
            payload=_payload(expected_version=NOW - timedelta(days=1)),
        )

    assert result.outcome == "REPLAYED"
    update.assert_not_awaited()
    create.assert_not_awaited()
    assert session.commits == 0


@_async_test
async def test_stale不同快照拒绝且零写入() -> None:
    from app.modules.user_health.service import put_member_self_health_profile

    session = _Session()
    update = AsyncMock()
    with (
        patch(
            "app.modules.user_health.service.get_member_profile_user_state",
            new=AsyncMock(return_value=_user()),
        ),
        patch(
            "app.modules.user_health.service.get_health_profile_by_user_id",
            new=AsyncMock(return_value=_profile()),
        ),
        patch("app.modules.user_health.service.update_health_profile_record", new=update),
    ):
        with pytest.raises(HTTPException) as caught:
            await put_member_self_health_profile(
                session,
                confirmation_session_factory_provider=_provider(_Session()),
                user_id=1001,
                payload=_payload(
                    expected_version=NOW - timedelta(seconds=1),
                    weight="56.0",
                ),
            )

    assert caught.value.detail == "HEALTH_PROFILE_VERSION_CONFLICT"
    update.assert_not_awaited()
    assert session.commits == 0


@_async_test
async def test_条件更新成功返回UPDATED() -> None:
    from app.modules.user_health.service import put_member_self_health_profile

    session = _Session()
    updated = _profile(weight=Decimal("56.0"), updated_at=NOW + timedelta(seconds=1))
    with (
        patch(
            "app.modules.user_health.service.get_member_profile_user_state",
            new=AsyncMock(return_value=_user()),
        ),
        patch(
            "app.modules.user_health.service.get_health_profile_by_user_id",
            new=AsyncMock(return_value=_profile()),
        ),
        patch(
            "app.modules.user_health.service.update_health_profile_record",
            new=AsyncMock(return_value=updated),
        ) as update,
    ):
        result = await put_member_self_health_profile(
            session,
            confirmation_session_factory_provider=_provider(_Session()),
            user_id=1001,
            payload=_payload(weight="56.0"),
        )

    assert result.outcome == "UPDATED"
    assert result.profile.weight == Decimal("56.0")
    assert session.commits == 1
    assert update.await_args.kwargs["expected_updated_at"] == NOW


class _VendorUniqueViolation(Exception):
    sqlstate = "23505"
    constraint_name = "uq_health_profile_user_id"


@_async_test
async def test_已知创建竞争使用新鲜Session确认canonical重放() -> None:
    from app.modules.user_health.service import put_member_self_health_profile

    error = IntegrityError("INSERT secret-sql", {"phone": "13800000000"}, _VendorUniqueViolation())
    confirmation = _Session()
    with (
        patch(
            "app.modules.user_health.service.get_member_profile_user_state",
            new=AsyncMock(side_effect=[_user(), _user()]),
        ),
        patch(
            "app.modules.user_health.service.get_health_profile_by_user_id",
            new=AsyncMock(side_effect=[None, _profile()]),
        ),
        patch(
            "app.modules.user_health.service.create_health_profile_record",
            new=AsyncMock(side_effect=error),
        ),
    ):
        result = await put_member_self_health_profile(
            _Session(),
            confirmation_session_factory_provider=_provider(confirmation),
            user_id=1001,
            payload=_payload(expected_version=None),
        )

    assert result.outcome == "REPLAYED"


@_async_test
async def test_未知IntegrityError映射generic并切断供应商异常链() -> None:
    from app.modules.user_health.service import put_member_self_health_profile

    error = IntegrityError(
        "INSERT INTO health_profile VALUES (secret)",
        {"password": "secret"},
        Exception("driver database-url phone=13800000000"),
    )
    session = _Session()
    with (
        patch(
            "app.modules.user_health.service.get_member_profile_user_state",
            new=AsyncMock(return_value=_user()),
        ),
        patch(
            "app.modules.user_health.service.get_health_profile_by_user_id",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.modules.user_health.service.create_health_profile_record",
            new=AsyncMock(side_effect=error),
        ),
    ):
        with pytest.raises(HTTPException) as caught:
            await put_member_self_health_profile(
                session,
                confirmation_session_factory_provider=_provider(_Session()),
                user_id=1001,
                payload=_payload(expected_version=None),
            )

    assert caught.value.status_code == 503
    assert caught.value.detail == "Health profile persistence is unavailable"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert session.rollbacks == 1
    serialized = repr(caught.value)
    assert "secret" not in serialized
    assert "13800000000" not in serialized


@_async_test
async def test_commit结果未知使用新鲜只读Session确认且不二次写入() -> None:
    from app.modules.user_health.service import put_member_self_health_profile

    primary = _Session(commit_error=RuntimeError("connection lost secret-url"))
    confirmation = _Session()
    create = AsyncMock(return_value=_profile())
    with (
        patch(
            "app.modules.user_health.service.get_member_profile_user_state",
            new=AsyncMock(side_effect=[_user(), _user()]),
        ),
        patch(
            "app.modules.user_health.service.get_health_profile_by_user_id",
            new=AsyncMock(side_effect=[None, _profile()]),
        ),
        patch("app.modules.user_health.service.create_health_profile_record", new=create),
    ):
        result = await put_member_self_health_profile(
            primary,
            confirmation_session_factory_provider=_provider(confirmation),
            user_id=1001,
            payload=_payload(expected_version=None),
        )

    assert result.outcome == "REPLAYED"
    assert create.await_count == 1
    assert primary.rollbacks == 1
    assert confirmation.commits == 0


@_async_test
async def test_Cancellation原样传播并rollback() -> None:
    from app.modules.user_health.service import put_member_self_health_profile

    session = _Session(commit_error=asyncio.CancelledError())
    with (
        patch(
            "app.modules.user_health.service.get_member_profile_user_state",
            new=AsyncMock(return_value=_user()),
        ),
        patch(
            "app.modules.user_health.service.get_health_profile_by_user_id",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.modules.user_health.service.create_health_profile_record",
            new=AsyncMock(return_value=_profile()),
        ),
    ):
        with pytest.raises(asyncio.CancelledError):
            await put_member_self_health_profile(
                session,
                confirmation_session_factory_provider=_provider(_Session()),
                user_id=1001,
                payload=_payload(expected_version=None),
            )

    assert session.rollbacks == 1
