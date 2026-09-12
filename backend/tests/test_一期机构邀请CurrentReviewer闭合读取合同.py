from __future__ import annotations

import asyncio
import inspect

import pytest

from app.modules.institution_onboarding.repository import (
    InstitutionOnboardingRepository,
)


class _Mappings:
    def __init__(self, row):
        self._row = row

    def one_or_none(self):
        return self._row


class _Result:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return _Mappings(self._row)


class _Session:
    def __init__(self, row=None, error: Exception | None = None):
        self.row = row
        self.error = error
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        if self.error is not None:
            raise self.error
        return _Result(self.row)


def test_CurrentReviewer只调用既有闭合当前性函数且不依赖UserORM映射():
    session = _Session(
        {
            "id": 95001,
            "role": "super_admin",
            "tenant_id": None,
            "status": "active",
            "exited_at": None,
            "deletion_requested_at": None,
            "tenant_org_id": None,
        }
    )

    current = asyncio.run(
        InstitutionOnboardingRepository(session).reviewer_currentness(95001)
    )

    assert current == {
        "id": 95001,
        "role": "super_admin",
        "status": "active",
        "tenant_id": None,
    }
    assert len(session.statements) == 1
    rendered = str(session.statements[0])
    assert "public.institution_onboarding_reviewer_currentness_v1" in rendered
    assert "public.auth_user_currentness_v1" not in rendered
    assert "public.user" not in rendered.lower()
    assert session.statements[0].compile().params == {"user_id": 95001}


def test_CurrentReviewer生产方法没有直接User列或mapping逃生通道():
    source = inspect.getsource(InstitutionOnboardingRepository.reviewer_currentness)
    for forbidden in (
        "User.id",
        "User.role",
        "User.status",
        "User.tenant_id",
        "User.password_hash",
        "map_core_model_classes",
    ):
        assert forbidden not in source
    assert "institution_onboarding_reviewer_currentness_v1" in source
    assert "get_user_currentness" not in source


def test_CurrentReviewer依赖故障保持失败不伪造当前用户():
    failure = RuntimeError("synthetic dependency unavailable")
    session = _Session(error=failure)

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(
            InstitutionOnboardingRepository(session).reviewer_currentness(95002)
        )

    assert caught.value is failure
