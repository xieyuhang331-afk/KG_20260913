from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Mapping
from uuid import UUID

import asyncpg
from sqlalchemy import BigInteger, DateTime, String, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.exc import DBAPIError


class HealthPlanRepositoryError(RuntimeError):
    pass


_PUBLIC_MUTATION_OPERATIONS = frozenset({
    "PLAN_GENERATION_REQUEST", "PLAN_TEMPLATE_CREATE", "PLAN_TEMPLATE_PUBLISH",
    "PLAN_TEMPLATE_RETIRE", "PLAN_REVIEW_CLAIM", "PLAN_REVIEW_DECISION",
    "PLAN_EXPLANATION", "PLAN_USER_DECISION",
})
_DATABASE_ERRORS = {
    "MUTATION_REPLAY": frozenset({"IDEMPOTENCY_CONFLICT"}),
    "GENERATION_REQUEST": frozenset({
        "INVALID_REQUEST", "IDEMPOTENCY_CONFLICT", "SERVICE_CASE_NOT_FOUND", "STALE_VERSION",
        "TENANT_NOT_SERVICE_READY", "SERVICE_CASE_NOT_CURRENT", "CONSENT_NOT_CURRENT",
        "PRIMARY_THERAPIST_NOT_CURRENT", "ASSESSMENT_INPUT_NOT_READY",
        "ASSESSMENT_DISPUTED", "ASSESSMENT_SUPERSEDED", "HIGH_RISK_BLOCKING",
        "TEMPLATE_NOT_AVAILABLE", "ACTIVE_GENERATION_EXISTS", "ACTIVE_PLAN_CONFLICT",
    }),
    "REVIEW_CLAIM": frozenset({"IDEMPOTENCY_CONFLICT", "FORBIDDEN", "STALE_VERSION"}),
    "REVIEW_DECIDE": frozenset({
        "IDEMPOTENCY_CONFLICT", "FORBIDDEN", "STALE_VERSION",
        "REVIEW_DECISION_CONFLICT", "REVIEW_NOT_CLAIMED",
    }),
    "PLAN_EXPLANATION": frozenset({"IDEMPOTENCY_CONFLICT", "PLAN_NOT_FOUND", "FORBIDDEN"}),
    "PLAN_USER_DECISION": frozenset({
        "IDEMPOTENCY_CONFLICT", "PLAN_NOT_FOUND", "USER_DECISION_FORBIDDEN",
        "USER_DECISION_CONFLICT",
    }),
    "TEMPLATE_CREATE": frozenset({"IDEMPOTENCY_CONFLICT", "FORBIDDEN", "INVALID_REQUEST"}),
    "TEMPLATE_PUBLISH": frozenset({"IDEMPOTENCY_CONFLICT", "FORBIDDEN", "INVALID_REQUEST", "STALE_VERSION"}),
    "TEMPLATE_RETIRE": frozenset({"IDEMPOTENCY_CONFLICT", "FORBIDDEN", "INVALID_REQUEST", "STALE_VERSION"}),
}


def _database_error(exc: DBAPIError, callpoint: str) -> str | None:
    original = exc.orig
    direct_cause = getattr(original, "__cause__", None)
    driver_error = next(
        (
            candidate
            for candidate in (original, direct_cause)
            if isinstance(candidate, asyncpg.PostgresError)
        ),
        None,
    )
    if driver_error is None or driver_error.sqlstate != "P0001":
        return None
    if len(driver_error.args) != 1 or type(driver_error.args[0]) is not str:
        return None
    code = driver_error.args[0]
    return code if code in _DATABASE_ERRORS.get(callpoint, ()) else None


async def _execute_registered(session, statement, parameters, callpoint: str):
    try:
        return await session.execute(statement, parameters)
    except DBAPIError as exc:
        code = _database_error(exc, callpoint)
        if code is None:
            raise
        raise HealthPlanRepositoryError(code) from None


def _json_value(value: object) -> object:
    if is_dataclass(value):
        return _json_value(asdict(value))
    if type(value) is UUID:
        return str(value)
    if type(value) in (datetime, date):
        return value.isoformat()
    if type(value) is Decimal:
        return str(value)
    if type(value) is bytes:
        return value.hex()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if type(value) in (tuple, list):
        return [_json_value(item) for item in value]
    return value


def _payload(value: Mapping[str, object]) -> str:
    return json.dumps(_json_value(value), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def expected_mutation_postimage(
    operation: str, payload: Mapping[str, object]
) -> dict[str, object]:
    clean_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"expected_postimage", "expected_confirmed_digest"}
    }
    return {
        "operation": operation,
        "payload": _json_value(clean_payload),
        "expected_confirmed_digest": None,
    }


class HealthPlanRepository:
    def __init__(self, session) -> None:
        self.session = session
        self.mutation_confirmation: dict[str, object] | None = None
        self.mutation_replayed = False

    async def _prepare_mutation(
        self, operation: str, payload: Mapping[str, object]
    ) -> dict[str, object]:
        expected = expected_mutation_postimage(operation, payload)
        row = (
            await self.session.execute(
                text(
                    "SELECT public.slice6_mutation_expected_v1("
                    "CAST(:expected AS jsonb)) AS value"
                ),
                {"expected": _payload(expected)},
            )
        ).mappings().one()
        digest = row["value"]
        if type(digest) is not str or len(digest) != 64:
            raise HealthPlanRepositoryError("COMMIT_OUTCOME_UNKNOWN") from None
        prepared = {
            **dict(payload),
            "expected_postimage": expected,
            "expected_confirmed_digest": digest,
        }
        self.mutation_confirmation = {
            "expected_postimage": expected,
            "expected_confirmed_digest": digest,
        }
        return prepared

    async def confirm_mutation_outcome(
        self, confirmation: Mapping[str, object]
    ) -> dict[str, object]:
        row = (
            await self.session.execute(
                text(
                    "SELECT public.slice6_mutation_confirm_v1("
                    "CAST(:confirmation AS jsonb)) AS value"
                ),
                {"confirmation": _payload(confirmation)},
            )
        ).mappings().one()
        return row["value"]

    async def _json_function(
        self, name: str, payload: Mapping[str, object], *, callpoint: str | None = None
    ) -> dict | None:
        statement = text(f"SELECT public.{name}(CAST(:payload AS jsonb)) AS value")
        parameters = {"payload": _payload(payload)}
        result = (
            await self.session.execute(statement, parameters)
            if callpoint is None
            else await _execute_registered(self.session, statement, parameters, callpoint)
        )
        row = result.mappings().one()
        return row["value"]

    async def generation_authority(
        self, service_case_id: UUID, actor_user_id: int, actor_role: str
    ) -> dict | None:
        statement = text(
            "SELECT public.slice6_generation_authority_v1("
            ":service_case_id,:actor_user_id,:actor_role) AS value"
        ).bindparams(bindparam("service_case_id", type_=PostgreSQLUUID(as_uuid=True)))
        row = (
            await self.session.execute(
                statement,
                {
                    "service_case_id": service_case_id,
                    "actor_user_id": actor_user_id,
                    "actor_role": actor_role,
                },
            )
        ).mappings().one()
        return row["value"]

    async def generation_replay(
        self, actor_user_id: int, idempotency_key: str, request_digest: str
    ) -> dict | None:
        row = (
            await _execute_registered(
                self.session,
                text(
                    "SELECT public.slice6_mutation_replay_v1("
                    ":actor_user_id,'PLAN_GENERATION_REQUEST',:idempotency_key,"
                    "decode(:request_digest,'hex')) AS value"
                ),
                {
                    "actor_user_id": actor_user_id,
                    "idempotency_key": idempotency_key,
                    "request_digest": request_digest,
                },
                "MUTATION_REPLAY",
            )
        ).mappings().one()
        replay = row["value"]
        self.mutation_replayed = replay is not None
        return replay

    async def mutation_replay(
        self, actor_user_id: int, operation: str, idempotency_key: str, request_digest: str
    ) -> dict | None:
        statement = text(
            "SELECT public.slice6_mutation_replay_v1("
            ":actor_user_id,:operation,:idempotency_key,decode(:request_digest,'hex')) AS value"
        )
        parameters = {
            "actor_user_id": actor_user_id,
            "operation": operation,
            "idempotency_key": idempotency_key,
            "request_digest": request_digest,
        }
        result = (
            await _execute_registered(
                self.session, statement, parameters, "MUTATION_REPLAY"
            )
            if operation in _PUBLIC_MUTATION_OPERATIONS
            else await self.session.execute(statement, parameters)
        )
        row = (
            result
        ).mappings().one()
        replay = row["value"]
        self.mutation_replayed = replay is not None
        return replay

    async def create_generation_request(self, payload: Mapping[str, object]) -> dict:
        prepared = await self._prepare_mutation("PLAN_GENERATION_REQUEST", payload)
        return await self._json_function(
            "slice6_generation_request_v1", prepared, callpoint="GENERATION_REQUEST"
        )  # type: ignore[return-value]

    async def claim_generation(self, request_id: UUID, lease_owner: str) -> dict | None:
        return await self._json_function(
            "slice6_generation_worker_v1",
            {"operation": "CLAIM", "request_id": request_id, "lease_owner": lease_owner},
        )

    async def generation_input(self, request_id: UUID) -> dict | None:
        statement = text(
            "SELECT public.slice6_generation_input_v1(:request_id) AS value"
        ).bindparams(bindparam("request_id", type_=PostgreSQLUUID(as_uuid=True)))
        row = (await self.session.execute(statement, {"request_id": request_id})).mappings().one()
        return row["value"]

    async def complete_generation(self, payload: Mapping[str, object]) -> dict:
        return await self._json_function("slice6_generation_complete_v1", payload)  # type: ignore[return-value]

    async def fail_generation(self, payload: Mapping[str, object]) -> dict:
        return await self._json_function("slice6_generation_worker_v1", {"operation": "FAIL", **payload})  # type: ignore[return-value]

    async def transition_review(self, operation: str, payload: Mapping[str, object]) -> dict:
        prepared = await self._prepare_mutation(
            "PLAN_REVIEW_CLAIM" if operation == "CLAIM" else "PLAN_REVIEW_DECISION",
            {"operation": operation, **payload},
        )
        return await self._json_function(
            "slice6_review_transition_v1", prepared, callpoint=f"REVIEW_{operation}"
        )  # type: ignore[return-value]

    async def explain_plan(self, payload: Mapping[str, object]) -> dict:
        prepared = await self._prepare_mutation("PLAN_EXPLANATION", payload)
        return await self._json_function(
            "slice6_plan_explanation_v1", prepared, callpoint="PLAN_EXPLANATION"
        )  # type: ignore[return-value]

    async def decide_plan(self, payload: Mapping[str, object]) -> dict:
        prepared = await self._prepare_mutation("PLAN_USER_DECISION", payload)
        return await self._json_function(
            "slice6_user_decision_v1", prepared, callpoint="PLAN_USER_DECISION"
        )  # type: ignore[return-value]

    async def govern_template(self, operation: str, payload: Mapping[str, object]) -> dict:
        prepared = await self._prepare_mutation(
            f"PLAN_TEMPLATE_{operation}", {"operation": operation, **payload}
        )
        return await self._json_function(
            "slice6_template_governance_v1", prepared, callpoint=f"TEMPLATE_{operation}"
        )  # type: ignore[return-value]

    async def next_template_version(self, template_code: str) -> int:
        row = (
            await self.session.execute(
                text(
                    "SELECT COALESCE(max(version_no),0)+1 AS value "
                    "FROM public.slice6_template_governance_read_v1 "
                    "WHERE template_code=:template_code"
                ),
                {"template_code": template_code},
            )
        ).mappings().one()
        return int(row["value"])

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
            "SELECT public.slice6_actor_read_authority_v1("
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

    async def case_read_authority(
        self, *, actor_user_id: int, actor_role: str, service_case_id: UUID
    ) -> dict:
        statement = text(
            "SELECT public.slice6_case_read_authority_v1("
            ":actor_user_id,:actor_role,:service_case_id) AS value"
        ).bindparams(bindparam("service_case_id", type_=PostgreSQLUUID(as_uuid=True)))
        row = (
            await self.session.execute(
                statement,
                {
                    "actor_user_id": actor_user_id,
                    "actor_role": actor_role,
                    "service_case_id": service_case_id,
                },
            )
        ).mappings().one()
        return row["value"]

    async def generation_detail(self, request_id: UUID) -> dict | None:
        return await self._one_by_uuid("slice6_generation_read_v1", "request_id", request_id)

    async def template_detail(self, template_version_id: UUID) -> dict | None:
        return await self._one_by_uuid(
            "slice6_template_governance_read_v1", "template_version_id", template_version_id
        )

    async def review_detail(self, review_id: UUID) -> dict | None:
        return await self._one_by_uuid("slice6_review_read_v1", "review_id", review_id)

    async def plan_detail(self, plan_id: UUID) -> dict | None:
        return await self._one_by_uuid("slice6_plan_read_v1", "plan_id", plan_id)

    async def plan_details(self, plan_ids: tuple[UUID, ...]) -> list[dict]:
        if not plan_ids:
            return []
        statement = text(
            "SELECT * FROM public.slice6_plan_read_v1 "
            "WHERE plan_id=ANY(:plan_ids) ORDER BY plan_id"
        ).bindparams(
            bindparam("plan_ids", type_=ARRAY(PostgreSQLUUID(as_uuid=True)))
        )
        return list(
            (await self.session.execute(statement, {"plan_ids": list(plan_ids)}))
            .mappings()
            .all()
        )

    async def previous_plan_detail(
        self, *, service_case_id: UUID, plan_version_no: int
    ) -> dict | None:
        statement = text(
            "SELECT * FROM public.slice6_plan_read_v1 "
            "WHERE service_case_id=:service_case_id AND version_no<:plan_version_no "
            "ORDER BY version_no DESC LIMIT 1"
        ).bindparams(
            bindparam("service_case_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("plan_version_no", type_=BigInteger),
        )
        return (
            await self.session.execute(
                statement,
                {
                    "service_case_id": service_case_id,
                    "plan_version_no": plan_version_no,
                },
            )
        ).mappings().first()

    async def plan_history(
        self, *, service_case_id: UUID, through_version_no: int
    ) -> list[dict]:
        statement = text(
            "SELECT * FROM public.slice6_plan_read_v1 "
            "WHERE service_case_id=:service_case_id AND version_no<=:through_version_no "
            "ORDER BY version_no"
        ).bindparams(
            bindparam("service_case_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("through_version_no", type_=BigInteger),
        )
        return list(
            (
                await self.session.execute(
                    statement,
                    {
                        "service_case_id": service_case_id,
                        "through_version_no": through_version_no,
                    },
                )
            )
            .mappings()
            .all()
        )

    async def review_history(
        self, *, service_case_id: UUID, through_version_no: int
    ) -> list[dict]:
        statement = text(
            "SELECT * FROM public.slice6_review_read_v1 "
            "WHERE service_case_id=:service_case_id AND plan_version_no<=:through_version_no "
            "ORDER BY plan_version_no,review_id"
        ).bindparams(
            bindparam("service_case_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("through_version_no", type_=BigInteger),
        )
        return list(
            (
                await self.session.execute(
                    statement,
                    {
                        "service_case_id": service_case_id,
                        "through_version_no": through_version_no,
                    },
                )
            )
            .mappings()
            .all()
        )

    async def formal_assessment_context(
        self, *, service_case_id: UUID, plan_created_at
    ) -> dict | None:
        statement = text(
            "SELECT overall_risk,input_evidence,module_results "
            "FROM public.slice5_assessment_read_v1 "
            "WHERE service_case_id=:service_case_id AND status='COMPLETED' "
            "AND completed_at<=:plan_created_at "
            "ORDER BY sequence_no DESC LIMIT 1"
        ).bindparams(
            bindparam("service_case_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("plan_created_at", type_=DateTime(timezone=True)),
        )
        return (
            await self.session.execute(
                statement,
                {
                    "service_case_id": service_case_id,
                    "plan_created_at": plan_created_at,
                },
            )
        ).mappings().first()

    async def _one_by_uuid(self, view: str, column: str, value: UUID) -> dict | None:
        statement = text(f"SELECT * FROM public.{view} WHERE {column}=:value").bindparams(
            bindparam("value", type_=PostgreSQLUUID(as_uuid=True))
        )
        return (await self.session.execute(statement, {"value": value})).mappings().first()

    async def _page(self, view: str, cursor_column: str, cursor_id: UUID | None, limit: int) -> list[dict]:
        statement = text(
            f"SELECT * FROM public.{view} WHERE (:cursor_id IS NULL OR {cursor_column}>:cursor_id) "
            f"ORDER BY {cursor_column} LIMIT :limit"
        ).bindparams(bindparam("cursor_id", type_=PostgreSQLUUID(as_uuid=True)))
        return list(
            (await self.session.execute(statement, {"cursor_id": cursor_id, "limit": limit}))
            .mappings()
            .all()
        )

    async def template_page(self, cursor_id: UUID | None, limit: int) -> list[dict]:
        return await self._page("slice6_template_governance_read_v1", "template_version_id", cursor_id, limit)

    async def review_page(
        self, *, cursor_id: UUID | None, status: str | None, limit: int
    ) -> list[dict]:
        statement = text(
            "SELECT * FROM public.slice6_review_read_v1 "
            "WHERE (:status IS NULL OR status=:status) "
            "AND (:cursor_id IS NULL OR review_id>:cursor_id) "
            "ORDER BY review_id LIMIT :limit"
        ).bindparams(
            bindparam("cursor_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("status", type_=String),
        )
        return list(
            (
                await self.session.execute(
                    statement,
                    {"cursor_id": cursor_id, "status": status, "limit": limit},
                )
            )
            .mappings()
            .all()
        )

    async def plan_page(
        self, *, service_case_id: UUID, cursor_id: UUID | None, limit: int
    ) -> list[dict]:
        statement = text(
            "SELECT * FROM public.slice6_plan_read_v1 WHERE service_case_id=:service_case_id "
            "AND (:cursor_id IS NULL OR plan_id>:cursor_id) ORDER BY plan_id LIMIT :limit"
        ).bindparams(
            bindparam("service_case_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("cursor_id", type_=PostgreSQLUUID(as_uuid=True)),
        )
        return list(
            (
                await self.session.execute(
                    statement,
                    {"service_case_id": service_case_id, "cursor_id": cursor_id, "limit": limit},
                )
            )
            .mappings()
            .all()
        )

    async def outbox_claim(self, event_id: UUID | None, lease_seconds: int) -> dict | None:
        statement = text(
            "SELECT public.slice6_outbox_claim_v1(:event_id,:lease_seconds) AS value"
        ).bindparams(bindparam("event_id", type_=PostgreSQLUUID(as_uuid=True)))
        row = (
            await self.session.execute(
                statement, {"event_id": event_id, "lease_seconds": lease_seconds}
            )
        ).mappings().one()
        return row["value"]

    async def outbox_consume(self, payload: Mapping[str, object]) -> dict:
        return await self._json_function("slice6_outbox_consume_v1", payload)  # type: ignore[return-value]

    async def outbox_recover(self, cutoff: datetime) -> dict:
        row = (
            await self.session.execute(
                text("SELECT public.slice6_outbox_recover_v1(:cutoff) AS value"),
                {"cutoff": cutoff},
            )
        ).mappings().one()
        return row["value"]
