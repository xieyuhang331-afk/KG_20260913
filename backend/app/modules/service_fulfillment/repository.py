from __future__ import annotations

import json
from datetime import datetime
from typing import Mapping
from uuid import UUID

from sqlalchemy import BigInteger, bindparam, text
from sqlalchemy.dialects.postgresql import UUID as UUIDType
from sqlalchemy.ext.asyncio import AsyncSession

from .service import _json_value, digest_hex


_UUID = UUIDType(as_uuid=True)


class ServiceFulfillmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def authority(
        self,
        operation: str,
        target_id: UUID,
        actor_user_id: int,
        actor_role: str,
        actor_tenant_id: int | None,
    ) -> dict | None:
        statement = text(
            "SELECT public.slice7_authority_v1(:operation,:target_id,:actor_user_id,:actor_role,:actor_tenant_id) AS value"
        ).bindparams(
            bindparam("target_id", type_=_UUID),
            bindparam("actor_user_id", type_=BigInteger()),
            bindparam("actor_tenant_id", type_=BigInteger()),
        )
        return (await self.session.execute(statement, {
            "operation": operation,
            "target_id": target_id,
            "actor_user_id": actor_user_id,
            "actor_role": actor_role,
            "actor_tenant_id": actor_tenant_id,
        })).scalar_one_or_none()

    async def replay(
        self,
        actor_user_id: int,
        operation: str,
        idempotency_key: str,
        request_digest: str,
    ) -> dict | None:
        statement = text(
            "SELECT public.slice7_mutation_replay_v1(:actor,:operation,:key,decode(:digest,'hex')) AS value"
        ).bindparams(bindparam("actor", type_=BigInteger()))
        return (await self.session.execute(statement, {
            "actor": actor_user_id,
            "operation": operation,
            "key": idempotency_key,
            "digest": request_digest,
        })).scalar_one_or_none()

    async def mutate(self, operation: str, payload: Mapping[str, object]) -> dict:
        value = json.dumps(
            {**_json_value(dict(payload)), "operation": operation},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        result = (await self.session.execute(
            text("SELECT public.slice7_mutation_v1(CAST(:value AS jsonb)) AS value"),
            {"value": value},
        )).scalar_one()
        return dict(result)

    async def confirm_mutation_outcome(self, expected: Mapping[str, object]) -> dict:
        value = json.dumps(
            _json_value(dict(expected)),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        result = (await self.session.execute(
            text("SELECT public.slice7_mutation_confirm_v1(CAST(:value AS jsonb)) AS value"),
            {"value": value},
        )).scalar_one()
        return dict(result)

    async def read_one(
        self, resource: str, target_id: UUID, actor_user_id: int, actor_role: str
    ) -> dict | None:
        statement = text(
            "SELECT public.slice7_read_one_v1(:resource,:target,:actor,:role) AS value"
        ).bindparams(
            bindparam("target", type_=_UUID), bindparam("actor", type_=BigInteger())
        )
        return (await self.session.execute(statement, {
            "resource": resource,
            "target": target_id,
            "actor": actor_user_id,
            "role": actor_role,
        })).scalar_one_or_none()

    async def read_many(
        self,
        resource: str,
        scope_id: UUID | None,
        actor_user_id: int,
        actor_role: str,
        cursor_id: UUID | None,
        limit: int,
    ) -> list[dict]:
        statement = text(
            "SELECT public.slice7_read_many_v1(:resource,:scope,:actor,:role,:cursor,:limit) AS value"
        ).bindparams(
            bindparam("scope", type_=_UUID),
            bindparam("cursor", type_=_UUID),
            bindparam("actor", type_=BigInteger()),
            bindparam("limit", type_=BigInteger()),
        )
        value = (await self.session.execute(statement, {
            "resource": resource,
            "scope": scope_id,
            "actor": actor_user_id,
            "role": actor_role,
            "cursor": cursor_id,
            "limit": limit,
        })).scalar_one()
        return [dict(row) for row in value]

    async def claim_work(self, kind: str, worker_id: str, limit: int) -> list[dict]:
        statement = text(
            "SELECT public.slice7_worker_claim_v1(:kind,:worker,:limit) AS value"
        ).bindparams(bindparam("limit", type_=BigInteger()))
        value = (await self.session.execute(statement, {
            "kind": kind,
            "worker": worker_id,
            "limit": limit,
        })).scalar_one()
        return [dict(row) for row in value]

    async def claim_export(self, export_id: UUID, worker_id: str) -> dict | None:
        statement = text(
            "SELECT public.slice7_export_claim_v1(:export_id,:worker) AS value"
        ).bindparams(bindparam("export_id", type_=_UUID))
        value = (
            await self.session.execute(
                statement, {"export_id": export_id, "worker": worker_id}
            )
        ).scalar_one_or_none()
        return None if value is None else dict(value)

    async def export_snapshot(self, export_id: UUID) -> dict:
        statement = text(
            "SELECT public.slice7_export_snapshot_v1(:export_id) AS value"
        ).bindparams(bindparam("export_id", type_=_UUID))
        return dict(
            (
                await self.session.execute(statement, {"export_id": export_id})
            ).scalar_one()
        )

    async def export_source_file(self, export_id: UUID, file_id: UUID) -> dict:
        statement = text(
            "SELECT public.slice7_export_source_file_v1(:export_id,:file_id) AS value"
        ).bindparams(
            bindparam("export_id", type_=_UUID),
            bindparam("file_id", type_=_UUID),
        )
        return dict(
            (
                await self.session.execute(
                    statement, {"export_id": export_id, "file_id": file_id}
                )
            ).scalar_one()
        )

    async def bind_export_artifact(self, payload: Mapping[str, object]) -> dict:
        value = json.dumps(
            _json_value(dict(payload)),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return dict(
            (
                await self.session.execute(
                    text(
                        "SELECT public.slice7_export_artifact_bind_v1("
                        "CAST(:value AS jsonb)) AS value"
                    ),
                    {"value": value},
                )
            ).scalar_one()
        )

    async def consume_export_download(self, payload: Mapping[str, object]) -> bool:
        value = json.dumps(
            _json_value(dict(payload)),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return bool(
            (
                await self.session.execute(
                    text(
                        "SELECT public.slice7_export_download_consume_v1("
                        "CAST(:value AS jsonb))"
                    ),
                    {"value": value},
                )
            ).scalar_one()
        )

    async def fail_export(self, payload: Mapping[str, object]) -> dict:
        value = json.dumps(
            _json_value(dict(payload)),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return dict(
            (
                await self.session.execute(
                    text(
                        "SELECT public.slice7_export_fail_v1("
                        "CAST(:value AS jsonb)) AS value"
                    ),
                    {"value": value},
                )
            ).scalar_one()
        )

    async def recover_export(self, payload: Mapping[str, object]) -> dict | None:
        value = json.dumps(
            _json_value(dict(payload)),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        recovered = (
            await self.session.execute(
                text(
                    "SELECT public.slice7_export_recover_v1("
                    "CAST(:value AS jsonb)) AS value"
                ),
                {"value": value},
            )
        ).scalar_one_or_none()
        return None if recovered is None else dict(recovered)

    async def claim_export_cleanup(self, cutoff: datetime, limit: int) -> list[dict]:
        statement = text(
            "SELECT public.slice7_export_cleanup_claim_v1(:cutoff,:limit) AS value"
        ).bindparams(bindparam("limit", type_=BigInteger()))
        value = (
            await self.session.execute(
                statement, {"cutoff": cutoff, "limit": limit}
            )
        ).scalar_one()
        return [dict(row) for row in value]

    async def complete_export_cleanup(self, payload: Mapping[str, object]) -> bool:
        value = json.dumps(
            _json_value(dict(payload)),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return bool(
            (
                await self.session.execute(
                    text(
                        "SELECT public.slice7_export_cleanup_complete_v1("
                        "CAST(:value AS jsonb))"
                    ),
                    {"value": value},
                )
            ).scalar_one()
        )

    async def outbox_claim(self, event_id: UUID | None, seconds: int = 60) -> dict | None:
        statement = text(
            "SELECT public.slice7_outbox_claim_v1(:event_id,:seconds) AS value"
        ).bindparams(
            bindparam("event_id", type_=_UUID), bindparam("seconds", type_=BigInteger())
        )
        return (await self.session.execute(statement, {"event_id": event_id, "seconds": seconds})).scalar_one_or_none()

    async def outbox_consume(self, payload: Mapping[str, object]) -> dict:
        value = json.dumps(_json_value(dict(payload)), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        return dict((await self.session.execute(
            text("SELECT public.slice7_outbox_consume_v1(CAST(:value AS jsonb)) AS value"),
            {"value": value},
        )).scalar_one())

    async def outbox_recover(self, cutoff: datetime) -> dict:
        return dict((await self.session.execute(
            text("SELECT public.slice7_outbox_recover_v1(:cutoff) AS value"), {"cutoff": cutoff}
        )).scalar_one())

    @staticmethod
    def postimage_digest(value: Mapping[str, object]) -> str:
        return digest_hex(value)
