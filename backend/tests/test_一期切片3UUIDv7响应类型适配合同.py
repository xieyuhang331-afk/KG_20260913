from datetime import UTC, datetime
from enum import Enum
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid1, uuid4

import pytest
from asyncpg.pgproto.pgproto import UUID as AsyncpgUUID
from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError

from app.modules.member_enrollment import api
from app.modules.member_enrollment.schemas import InvitationDTO, UuidV7, _uuid_v7


UUID7 = UUID("01900000-0000-7000-8000-000000000001")


class _ForgedUuid:
    version = 7

    def __str__(self) -> str:
        return str(UUID7)


class _UuidText(Enum):
    VALUE = str(UUID7)


def test_UUIDv7规范化内置与asyncpg类型且返回内置UUID() -> None:
    adapter = TypeAdapter(UuidV7)

    builtin = adapter.validate_python(UUID7)
    database_value = adapter.validate_python(AsyncpgUUID(str(UUID7)))

    assert type(builtin) is UUID
    assert type(database_value) is UUID
    assert builtin == database_value == UUID7
    assert builtin.version == database_value.version == 7


def test_InvitationDTO接受数据库UUIDv7并保持标准字符串输出() -> None:
    value = AsyncpgUUID(str(UUID7))
    dto = InvitationDTO.model_validate(
        {
            "invitation_id": value,
            "tenant_id": value,
            "mode": "SELF",
            "phone_masked": "*******0000",
            "expires_at": datetime(2026, 8, 22, tzinfo=UTC),
            "status": "INVITED",
            "failed_attempts": 0,
            "issued_at": datetime(2026, 8, 21, tzinfo=UTC),
            "version": 1,
        }
    )

    assert type(dto.invitation_id) is UUID
    assert type(dto.tenant_id) is UUID
    assert dto.model_dump(mode="json")["invitation_id"] == str(UUID7)
    assert dto.model_dump(mode="json")["tenant_id"] == str(UUID7)


@pytest.mark.parametrize(
    "value",
    (
        UUID(int=0),
        uuid1(),
        uuid4(),
        AsyncpgUUID(str(uuid4())),
        _ForgedUuid(),
        _UuidText.VALUE,
        object(),
    ),
)
def test_UUIDv7继续拒绝非v7与伪造对象(value: object) -> None:
    with pytest.raises((ValueError, ValidationError), match="UUID_V7_REQUIRED|uuid"):
        _uuid_v7(value)  # type: ignore[arg-type]


class _ReplaySecrets:
    def __init__(self, *, result: object, digest: str, decrypt_error: Exception | None = None):
        self.result = result
        self.digest = digest
        self.decrypt_error = decrypt_error
        self.audit_values: list[object] = []

    def request_digest(self, value: object) -> str:
        return "1" * 64

    def decrypt_replay(self, *args, **kwargs) -> object:
        if self.decrypt_error is not None:
            raise self.decrypt_error
        return self.result

    def audit_digest(self, value: object) -> str:
        self.audit_values.append(value)
        return self.digest


def _replay_context() -> SimpleNamespace:
    return SimpleNamespace(
        actor_scope="user:1:tenant:1",
        idempotency_key="uuidv7-replay-contract",
    )


@pytest.mark.asyncio
async def test_合法回放使用响应后像摘要而非请求摘要(monkeypatch) -> None:
    result = {"invitation_id": str(UUID7), "tenant_id": str(UUID7)}
    secrets = _ReplaySecrets(result=result, digest="2" * 64)
    repository = SimpleNamespace(
        replay_idempotency=AsyncMock(
            return_value={
                "found": True,
                "response_ciphertext": b"ciphertext",
                "response_key_id": "replay-key",
                "postimage_digest": "2" * 64,
            }
        )
    )
    monkeypatch.setattr(api, "MemberEnrollmentRepository", lambda session: repository)

    replay = await api._replay_result(
        object(), _replay_context(), "INVITATION_CREATE", UUID7,
        {"mode": "SELF"}, secrets,
    )

    assert replay == result
    assert secrets.audit_values == [
        {"operation": "INVITATION_CREATE", "result": result}
    ]


@pytest.mark.asyncio
async def test_回放postimage_digest被篡改时固定fail_closed(monkeypatch) -> None:
    result = {"invitation_id": str(UUID7), "tenant_id": str(UUID7)}
    secrets = _ReplaySecrets(result=result, digest="2" * 64)
    repository = SimpleNamespace(
        replay_idempotency=AsyncMock(
            return_value={
                "found": True,
                "response_ciphertext": b"ciphertext",
                "response_key_id": "replay-key",
                "postimage_digest": "3" * 64,
            }
        )
    )
    monkeypatch.setattr(api, "MemberEnrollmentRepository", lambda session: repository)

    with pytest.raises(HTTPException) as error:
        await api._replay_result(
            object(), _replay_context(), "INVITATION_CREATE", UUID7,
            {"mode": "SELF"}, secrets,
        )

    assert error.value.status_code == 503
    assert error.value.detail["code"] == "COMMIT_OUTCOME_UNKNOWN"


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ("ciphertext", "key"))
async def test_回放密文或key异常经现有安全错误边界拒绝(
    monkeypatch, category: str,
) -> None:
    secrets = _ReplaySecrets(
        result={},
        digest="2" * 64,
        decrypt_error=RuntimeError("MEMBER_ENROLLMENT_DEPENDENCY_UNAVAILABLE"),
    )
    repository = SimpleNamespace(
        digest_algorithm_guard=AsyncMock(return_value=True),
        lock_operation=AsyncMock(),
        replay_idempotency=AsyncMock(
            return_value={
                "found": True,
                "response_ciphertext": b"tampered" if category == "ciphertext" else b"ciphertext",
                "response_key_id": "unavailable-key" if category == "key" else "replay-key",
                "postimage_digest": "2" * 64,
            }
        ),
    )
    monkeypatch.setattr(api, "MemberEnrollmentRepository", lambda session: repository)
    monkeypatch.setattr(api, "MemberEnrollmentSecrets", lambda: secrets)

    with pytest.raises(HTTPException) as error:
        await api._begin_mutation(
            SimpleNamespace(info={}), _replay_context(), "INVITATION_CREATE",
            {"mode": "SELF"}, target_id=UUID7,
        )

    assert error.value.status_code == 503
    assert error.value.detail["code"] == "DEPENDENCY_UNAVAILABLE"
