import base64
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.modules.health_fact.domain import (
    CanonicalHealthFactDraft,
    HealthFactCommitOutcomeUnknown,
    HealthFactDigestKeyring,
    HealthFactUnavailable,
    HealthFactSourceForbidden,
    prepare_fact,
)
from app.modules.health_fact.repository import SqlAlchemyHealthFactRepository
from app.modules.health_fact.unit_of_work import CanonicalHealthFactWriter


def _keyring() -> HealthFactDigestKeyring:
    return HealthFactDigestKeyring.from_base64(
        current_key_id="k2",
        encoded_keys={
            "k1": base64.b64encode(b"1" * 32).decode("ascii"),
            "k2": base64.b64encode(b"2" * 32).decode("ascii"),
        },
    )


def _draft() -> CanonicalHealthFactDraft:
    return CanonicalHealthFactDraft(
        subject_user_id=7,
        indicator_code="systolic_bp",
        numeric_value=Decimal("120.50"),
        unit="mmHg",
        measured_at=datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc),
        source_type="APP",
        source_identity="fictional-source",
        producer_event_key="event-1",
        created_by=9,
    )


def test_未知数据库异常切断供应商异常链且不公开敏感详情():
    class UnsafeSession:
        async def execute(self, _statement):
            raise RuntimeError(
                "postgresql://credential@target SQL params phone id-card"
            )

    repository = SqlAlchemyHealthFactRepository(UnsafeSession())
    with pytest.raises(HealthFactUnavailable) as captured:
        asyncio.run(repository.acquire_semantic_lock(1))

    public = captured.value
    assert str(public) == "Health fact persistence operation failed"
    assert public.__cause__ is None
    assert public.__context__ is None
    assert "postgresql" not in repr(public).lower()


def test_Cancellation原样传播不被安全异常包装():
    class Cancellation(BaseException):
        pass

    marker = Cancellation()

    class CancelledSession:
        async def execute(self, _statement):
            raise marker

    repository = SqlAlchemyHealthFactRepository(CancelledSession())
    with pytest.raises(Cancellation) as captured:
        asyncio.run(repository.acquire_semantic_lock(1))
    assert captured.value is marker


def test_commit结果未知仅用新鲜只读UoW确认且不二次写入():
    keyring = _keyring()
    draft = _draft()
    stored = replace(prepare_fact(draft, keyring), id=41)
    counters = {"add": 0, "readonly": 0, "audit": 0}

    class WriteRepository:
        async def acquire_semantic_lock(self, _lock_key):
            return None

        async def find_by_semantic_identity(self, **_kwargs):
            return None

        async def get_by_id(self, _fact_id):
            return None

        async def get_successor(self, _fact_id):
            return None

        async def add(self, _fact):
            counters["add"] += 1
            return stored

        async def add_audit(self, **_kwargs):
            counters["audit"] += 1

    class ReadRepository:
        async def find_by_semantic_identity(self, **_kwargs):
            counters["readonly"] += 1
            return stored

        async def has_audit(self, **_kwargs):
            return True

    class Uow:
        def __init__(self, repository, *, commit_unknown=False):
            self.repository = repository
            self._commit_unknown = commit_unknown

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def commit(self):
            if self._commit_unknown:
                raise HealthFactCommitOutcomeUnknown(
                    "Health fact commit outcome is unknown"
                )

    class Authority:
        async def authorize(self, value):
            return value

    writer = CanonicalHealthFactWriter(
        uow_factory=lambda: Uow(WriteRepository(), commit_unknown=True),
        readonly_uow_factory=lambda: Uow(ReadRepository()),
        keyring=keyring,
        producer_authority=Authority(),
    )

    with pytest.raises(HealthFactCommitOutcomeUnknown) as captured:
        asyncio.run(writer.write(draft))

    assert captured.value.__cause__ is None
    assert counters == {"add": 1, "readonly": 1, "audit": 1}


def test_ProducerAuthority拒绝发生在摘要和数据库生命周期之前():
    calls = {"authority": 0, "uow": 0}

    class Authority:
        async def authorize(self, _value):
            calls["authority"] += 1
            raise HealthFactSourceForbidden("Health fact source is forbidden")

    def forbidden_uow():
        calls["uow"] += 1
        raise AssertionError("UoW must not be created")

    writer = CanonicalHealthFactWriter(
        uow_factory=forbidden_uow,
        readonly_uow_factory=forbidden_uow,
        keyring=_keyring(),
        producer_authority=Authority(),
    )

    with pytest.raises(HealthFactSourceForbidden):
        asyncio.run(writer.write(_draft()))
    assert calls == {"authority": 1, "uow": 0}
