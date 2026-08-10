from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


NOW = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)


class _Repository:
    def __init__(self, latest=None, replay=None, count=0):
        self.latest = latest
        self.replay = replay
        self.count = count
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    async def lock_user(self, user_ref):
        return SimpleNamespace(id=user_ref, role="member", status="active", tenant_id=None, verify_status="submitted")

    get_user = lock_user

    async def find_by_idempotency(self, user_ref, digest): return self.replay
    async def find_latest(self, user_ref): return self.latest
    async def count_since(self, user_ref, since): return self.count
    async def add(self, **values):
        model = SimpleNamespace(
            status="submitted", version=values["version"],
            id_card_masked=values["id_card_masked"], submitted_at=values["submitted_at"],
            decided_at=None, rejection_reason_code=None,
        )
        self.added.append(values)
        return model
    async def mark_user_submitted(self, user_ref): return None
    async def commit(self): self.commits += 1
    async def rollback(self): self.rollbacks += 1


def _request(key="submission-key-v1", card="11010519491231002X"):
    return SimpleNamespace(real_name="张三", id_card=card, idempotency_key=key, consent_version="v1")


def _user(): return SimpleNamespace(id=42, role="member", tenant_id=None, org_id=None)


def _service(repository):
    from app.modules.auth.identity_submission import IdentitySubmissionService
    from app.modules.auth.identity_submission_crypto import IdentitySubmissionCrypto

    return IdentitySubmissionService(
        repository=repository,
        crypto=IdentitySubmissionCrypto(encryption_key=b"E" * 32, hmac_key=b"H" * 32, key_id="v1"),
        clock=lambda: NOW,
    )


def test_首次提交只持久化密文摘要和脱敏值() -> None:
    repository = _Repository()
    result = asyncio.run(
        _service(repository).submit(current_user=_user(), request=_request())
    )
    assert result.outcome == "CREATED"
    assert repository.commits == 1
    stored = repository.added[0]
    serialized = repr(stored)
    assert "张三" not in serialized
    assert "11010519491231002X" not in serialized
    assert stored["id_card_masked"] == "110105********002X"


def test_相同幂等键同内容稳定重放不同内容冲突() -> None:
    base = _Repository()
    asyncio.run(_service(base).submit(current_user=_user(), request=_request()))
    values = base.added[0]
    existing = SimpleNamespace(
        status="submitted", version=1, id_card_masked=values["id_card_masked"],
        submitted_at=NOW, decided_at=None, rejection_reason_code=None,
        content_digest=values["content_digest"],
    )
    replay_repo = _Repository(replay=existing)
    replay = asyncio.run(
        _service(replay_repo).submit(current_user=_user(), request=_request())
    )
    assert replay.outcome == "REPLAYED"
    assert replay_repo.added == [] and replay_repo.commits == 0

    from app.modules.auth.identity_submission import IdentitySubmissionConflict
    with pytest.raises(IdentitySubmissionConflict):
        asyncio.run(
            _service(_Repository(replay=existing)).submit(
                current_user=_user(), request=_request(card="110105194912310011")
            )
        )


def test_拒绝冷却和三次每24小时限制() -> None:
    from app.modules.auth.identity_submission import IdentitySubmissionRateLimited

    rejected = SimpleNamespace(status="rejected", version=1, decided_at=NOW - timedelta(hours=1))
    with pytest.raises(IdentitySubmissionRateLimited):
        asyncio.run(
            _service(_Repository(latest=rejected)).submit(
                current_user=_user(), request=_request()
            )
        )
    with pytest.raises(IdentitySubmissionRateLimited):
        asyncio.run(
            _service(_Repository(count=3)).submit(
                current_user=_user(), request=_request()
            )
        )


def test_verified会员仍可查询本人实名认证终态但不得再次提交() -> None:
    from app.modules.auth.identity_submission import IdentitySubmissionForbidden

    class VerifiedRepository(_Repository):
        async def get_user(self, user_ref):
            return SimpleNamespace(
                id=user_ref,
                role="member",
                status="active",
                tenant_id=None,
                verify_status="verified",
            )

        async def lock_user(self, user_ref):
            return await self.get_user(user_ref)

    latest = SimpleNamespace(
        status="verified",
        version=1,
        id_card_masked="110105********002X",
        submitted_at=NOW,
        decided_at=NOW,
        rejection_reason_code=None,
    )
    repository = VerifiedRepository(latest=latest)

    result = asyncio.run(_service(repository).status(current_user=_user()))

    assert result.status == "verified"
    assert result.submission_version == 1
    assert result.id_card_masked == "110105********002X"
    with pytest.raises(IdentitySubmissionForbidden):
        asyncio.run(
            _service(repository).submit(current_user=_user(), request=_request())
        )
