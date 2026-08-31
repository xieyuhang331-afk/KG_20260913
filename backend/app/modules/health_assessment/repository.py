from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
import json
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy import BigInteger, String, bindparam, text
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID


def _json_value(value: Any) -> Any:
    if is_dataclass(value):
        return _json_value(asdict(value))
    if type(value) is UUID:
        return str(value)
    if type(value) is datetime:
        return value.isoformat()
    if type(value) is date:
        return value.isoformat()
    if type(value) is Decimal:
        return str(value)
    if type(value) is bytes:
        return value.hex()
    if type(value) is dict:
        return {str(key): _json_value(item) for key, item in value.items()}
    if type(value) in (tuple, list):
        return [_json_value(item) for item in value]
    return value


def _payload(value: Mapping[str, object]) -> str:
    return json.dumps(_json_value(dict(value)), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


class HealthAssessmentRepository:
    def __init__(self, session) -> None:
        self.session = session

    async def assessment_start_replay(
        self, actor_user_id: int, idempotency_key: str, request_digest: bytes
    ) -> dict | None:
        row = (
            await self.session.execute(
                text(
                    "SELECT public.slice5_assessment_start_replay_v1("
                    ":actor_user_id,:idempotency_key,:request_digest) AS value"
                ),
                {
                    "actor_user_id": actor_user_id,
                    "idempotency_key": idempotency_key,
                    "request_digest": request_digest,
                },
            )
        ).mappings().one()
        return row["value"]

    async def start_authority(self, service_case_id: UUID, actor_user_id: int) -> dict | None:
        statement = text(
            "SELECT public.slice5_assessment_start_authority_v1(:case_id,:actor_user_id) AS value"
        ).bindparams(bindparam("case_id", type_=PostgreSQLUUID(as_uuid=True)))
        row = (
            await self.session.execute(
                statement, {"case_id": service_case_id, "actor_user_id": actor_user_id}
            )
        ).mappings().one()
        return row["value"]

    async def write_assessment_start(self, payload: Mapping[str, object]) -> dict:
        row = (
            await self.session.execute(
                text("SELECT public.slice5_assessment_snapshot_write_v1(CAST(:payload AS jsonb)) AS value"),
                {"payload": _payload(payload)},
            )
        ).mappings().one()
        return row["value"]

    async def claim_assessment(self, assessment_id: UUID, lease_owner: str) -> dict | None:
        statement = text(
            "SELECT public.slice5_assessment_worker_v1('CLAIM',CAST(:payload AS jsonb)) AS value"
        )
        row = (
            await self.session.execute(
                statement,
                {"payload": _payload({"assessment_id": assessment_id, "lease_owner": lease_owner})},
            )
        ).mappings().one()
        return row["value"]

    async def complete_assessment(self, payload: Mapping[str, object]) -> dict:
        row = (
            await self.session.execute(
                text("SELECT public.slice5_assessment_complete_v1(CAST(:payload AS jsonb)) AS value"),
                {"payload": _payload(payload)},
            )
        ).mappings().one()
        return row["value"]

    async def fail_assessment(self, payload: Mapping[str, object]) -> dict:
        row = (
            await self.session.execute(
                text("SELECT public.slice5_assessment_worker_v1('FAIL',CAST(:payload AS jsonb)) AS value"),
                {"payload": _payload(payload)},
            )
        ).mappings().one()
        return row["value"]

    async def confirm_assessment(self, assessment_id: UUID) -> dict | None:
        statement = text(
            "SELECT public.slice5_assessment_confirm_v1(:assessment_id) AS value"
        ).bindparams(bindparam("assessment_id", type_=PostgreSQLUUID(as_uuid=True)))
        row = (
            await self.session.execute(statement, {"assessment_id": assessment_id})
        ).mappings().one()
        return row["value"]

    async def assessment_input(self, assessment_id: UUID) -> dict | None:
        statement = text(
            "SELECT public.slice5_assessment_input_v1(:assessment_id) AS value"
        ).bindparams(bindparam("assessment_id", type_=PostgreSQLUUID(as_uuid=True)))
        row = (
            await self.session.execute(statement, {"assessment_id": assessment_id})
        ).mappings().one()
        return row["value"]

    async def transition_high_risk_task(self, payload: Mapping[str, object]) -> dict:
        row = (
            await self.session.execute(
                text("SELECT public.slice5_high_risk_transition_v1(CAST(:payload AS jsonb)) AS value"),
                {"payload": _payload(payload)},
            )
        ).mappings().one()
        return row["value"]

    async def raise_dispute(self, payload: Mapping[str, object]) -> dict:
        row = (
            await self.session.execute(
                text("SELECT public.slice5_assessment_dispute_v1(CAST(:payload AS jsonb)) AS value"),
                {"payload": _payload(payload)},
            )
        ).mappings().one()
        return row["value"]

    async def actor_read_is_current(
        self,
        *,
        actor_user_id: int,
        actor_role: str,
        service_case_id: UUID,
        subject_member_id: UUID,
        tenant_id: int,
    ) -> bool:
        statement = text(
            "SELECT public.slice5_actor_read_authority_v1("
            ":actor_user_id,:actor_role,:service_case_id,:subject_member_id,:tenant_id) AS value"
        ).bindparams(bindparam("service_case_id", type_=PostgreSQLUUID(as_uuid=True)))
        row = (
            await self.session.execute(
                statement,
                {
                    "actor_user_id": actor_user_id,
                    "actor_role": actor_role,
                    "service_case_id": service_case_id,
                    "subject_member_id": subject_member_id,
                    "tenant_id": tenant_id,
                },
            )
        ).mappings().one()
        return row["value"] is True

    async def govern_rule_set(self, operation: str, payload: Mapping[str, object]) -> dict:
        row = (
            await self.session.execute(
                text("SELECT public.slice5_rule_governance_v2(:operation,CAST(:payload AS jsonb)) AS value"),
                {"operation": operation, "payload": _payload(payload)},
            )
        ).mappings().one()
        return row["value"]

    async def confirm_rule_governance(self, payload: Mapping[str, object]) -> str:
        row = (
            await self.session.execute(
                text(
                    "SELECT public.slice5_rule_governance_confirm_v1("
                    "CAST(:payload AS jsonb)) AS value"
                ),
                {"payload": _payload(payload)},
            )
        ).mappings().one()
        value = row["value"]
        if type(value) is not dict or value.get("outcome") not in {
            "COMMITTED", "NOT_COMMITTED", "UNKNOWN"
        }:
            return "UNKNOWN"
        return value["outcome"]

    async def rule_set_detail(self, version_id: UUID) -> dict | None:
        statement = text(
            "SELECT * FROM public.slice5_rule_set_governance_read_v2 "
            "WHERE rule_set_version_id=:version_id"
        ).bindparams(bindparam("version_id", type_=PostgreSQLUUID(as_uuid=True)))
        return (await self.session.execute(statement, {"version_id": version_id})).mappings().first()

    async def rule_set_page(self, cursor_id: UUID | None, limit: int) -> list[dict]:
        statement = text(
            "SELECT * FROM public.slice5_rule_set_governance_read_v2 "
            "WHERE (:cursor_id IS NULL OR rule_set_version_id>:cursor_id) "
            "ORDER BY rule_set_version_id LIMIT :limit"
        ).bindparams(bindparam("cursor_id", type_=PostgreSQLUUID(as_uuid=True)))
        return list(
            (
                await self.session.execute(
                    statement, {"cursor_id": cursor_id, "limit": limit}
                )
            ).mappings().all()
        )

    async def subject_scope(
        self,
        *,
        actor_user_id: int,
        actor_context: str,
        enrollment_id: UUID | None = None,
    ) -> dict | None:
        if actor_context not in {"SELF", "PROXY_DAILY_VIEW"}:
            raise ValueError("unsupported family subject context")
        statement = text(
            "SELECT public.slice5_family_subject_authority_v1("
            ":actor_user_id,:enrollment_id) AS value"
        ).bindparams(bindparam("enrollment_id", type_=PostgreSQLUUID(as_uuid=True)))
        row = (
            await self.session.execute(
                statement,
                {
                    "enrollment_id": enrollment_id,
                    "actor_user_id": actor_user_id,
                },
            )
        ).mappings().first()
        return None if row is None else row["value"]

    async def assessment_detail(self, assessment_id: UUID) -> dict | None:
        statement = text(
            "SELECT * FROM public.slice5_assessment_read_v1 WHERE assessment_id=:assessment_id"
        ).bindparams(bindparam("assessment_id", type_=PostgreSQLUUID(as_uuid=True)))
        return (
            await self.session.execute(statement, {"assessment_id": assessment_id})
        ).mappings().first()

    async def assessment_page(self, scope: Mapping[str, object]) -> list[dict]:
        statement = text(
            "SELECT * FROM public.slice5_assessment_read_v1 "
            "WHERE (:service_case_id IS NULL OR service_case_id=:service_case_id) "
            "AND (:subject_member_id IS NULL OR subject_member_id=:subject_member_id) "
            "AND (:tenant_id IS NULL OR tenant_id=:tenant_id) "
            "AND (:cursor_id IS NULL OR assessment_id>:cursor_id) "
            "ORDER BY assessment_id LIMIT :limit"
        ).bindparams(
            bindparam("service_case_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("subject_member_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("tenant_id", type_=BigInteger()),
            bindparam("cursor_id", type_=PostgreSQLUUID(as_uuid=True)),
        )
        values = {
            "service_case_id": scope.get("service_case_id"),
            "subject_member_id": scope.get("subject_member_id"),
            "tenant_id": scope.get("tenant_id"),
            "cursor_id": scope.get("cursor_id"),
            "limit": scope.get("limit", 50),
        }
        return list((await self.session.execute(statement, values)).mappings().all())

    async def task_detail(self, task_id: UUID) -> dict | None:
        statement = text(
            "SELECT * FROM public.slice5_high_risk_task_read_v2 WHERE task_id=:task_id"
        ).bindparams(bindparam("task_id", type_=PostgreSQLUUID(as_uuid=True)))
        return (await self.session.execute(statement, {"task_id": task_id})).mappings().first()

    async def task_page(self, scope: Mapping[str, object]) -> list[dict]:
        statement = text(
            "SELECT * FROM public.slice5_high_risk_task_read_v2 "
            "WHERE (:tenant_id IS NULL OR tenant_id=:tenant_id) "
            "AND (:service_case_id IS NULL OR service_case_id=:service_case_id) "
            "AND (:status IS NULL OR status=:status) "
            "AND (:cursor_id IS NULL OR task_id>:cursor_id) ORDER BY task_id LIMIT :limit"
        ).bindparams(
            bindparam("tenant_id", type_=BigInteger()),
            bindparam("service_case_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("status", type_=String()),
            bindparam("cursor_id", type_=PostgreSQLUUID(as_uuid=True)),
        )
        values = {
            "tenant_id": scope.get("tenant_id"),
            "service_case_id": scope.get("service_case_id"),
            "status": scope.get("status"),
            "cursor_id": scope.get("cursor_id"),
            "limit": scope.get("limit", 50),
        }
        return list((await self.session.execute(statement, values)).mappings().all())

    async def outbox_claim(self, lease_owner: str, limit: int) -> list[dict]:
        statement = text(
            "SELECT public.slice5_outbox_claim_v1(:owner,:limit) AS value"
        ).bindparams(bindparam("owner", type_=PostgreSQLUUID(as_uuid=True)))
        value = (
            await self.session.execute(
                statement, {"owner": UUID(lease_owner), "limit": limit}
            )
        ).mappings().one()["value"]
        return list(value or [])

    async def outbox_consume(self, payload: Mapping[str, object]) -> dict:
        value = (
            await self.session.execute(
                text(
                    "SELECT public.slice5_outbox_consume_v1(CAST(:payload AS jsonb)) AS value"
                ),
                {"payload": _payload(payload)},
            )
        ).mappings().one()["value"]
        return dict(value or {})

    async def outbox_recover(self, now: datetime) -> dict:
        value = (
            await self.session.execute(
                text("SELECT public.slice5_outbox_recover_v1(:now) AS value"),
                {"now": now},
            )
        ).mappings().one()["value"]
        return dict(value or {})

    async def outbox_reopen(self, event_id: UUID, expected_attempts: int) -> dict:
        statement = text(
            "SELECT public.slice5_outbox_reopen_v1(:event_id,:expected_attempts) AS value"
        ).bindparams(bindparam("event_id", type_=PostgreSQLUUID(as_uuid=True)))
        value = (
            await self.session.execute(
                statement,
                {"event_id": event_id, "expected_attempts": expected_attempts},
            )
        ).mappings().one()["value"]
        return dict(value or {})
