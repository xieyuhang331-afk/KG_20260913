from __future__ import annotations

import json
from collections.abc import Mapping
from uuid import UUID

import asyncpg
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


class ReadinessPolicyRepositoryError(RuntimeError):
    pass


_SAFE_ERRORS = {
    "READINESS_POLICY_GOVERNANCE_FORBIDDEN",
    "READINESS_POLICY_INVALID",
    "READINESS_POLICY_STATE_CONFLICT",
    "READINESS_POLICY_REASON_INVALID",
    "POLICY_MEDICAL_APPROVAL_REQUIRED",
    "IDEMPOTENCY_CONFLICT",
}


def _safe_database_error(exc: DBAPIError) -> str | None:
    original = exc.orig
    cause = getattr(original, "__cause__", None)
    error = next(
        (item for item in (original, cause) if isinstance(item, asyncpg.PostgresError)),
        None,
    )
    if error is None or error.sqlstate != "P0001" or len(error.args) != 1:
        return None
    code = error.args[0]
    return code if code in _SAFE_ERRORS else None


class AssessmentReadinessRepository:
    def __init__(self, session) -> None:
        self._session = session

    async def current_readiness(self, service_case_id: UUID) -> dict | None:
        row = (
            await self._session.execute(
                text(
                    "SELECT service_case_id,assembly_id,status,reason_codes,missing_codes,"
                    "expired_codes,disputed_codes,profile_revision_id,policy_version,"
                    "projection_status,data_as_of,generated_at "
                    "FROM public.slice4_assessment_readiness_read_v1(:case_id)"
                ),
                {"case_id": service_case_id},
            )
        ).mappings().one_or_none()
        return dict(row) if row is not None else None

    async def currentness(self, service_case_id: UUID, *, actor_user_id: int = 0) -> dict | None:
        value = await self._session.scalar(
            text("SELECT public.slice4_readiness_currentness_v1(:case_id,:actor_id)"),
            {"case_id": service_case_id, "actor_id": actor_user_id},
        )
        return dict(value) if value is not None else None

    async def write_assembly(self, **values) -> dict:
        parameters = dict(values)
        for name in ("source_vector", "encrypted_fact_rows", "reason_codes"):
            parameters[name] = json.dumps(
                parameters[name], ensure_ascii=True, separators=(",", ":"), sort_keys=True
            )
        row = (
            await self._session.execute(
                text(
                    "SELECT * FROM public.slice4_assessment_assembly_write_v1("
                    ":service_case_id,:requested_assembly_id,:subject_member_id,"
                    ":tenant_public_id,:primary_therapist_id,:profile_revision_id,"
                    ":policy_version_id,:projection_version,:rule_version,:source_snapshot,"
                    "CAST(:source_vector AS jsonb),CAST(:encrypted_fact_rows AS jsonb),"
                    ":readiness_status,CAST(:reason_codes AS jsonb),:idempotency_key,"
                    ":request_digest,:expected_postimage_digest)"
                ),
                parameters,
            )
        ).mappings().one()
        return dict(row)

    async def bind_measurement_contexts(self, assembly_id: UUID, fact_rows: list[dict]) -> None:
        await self._session.execute(
            text(
                "SELECT public.slice5_assembly_measurement_context_bind_v1("
                ":assembly_id,CAST(:facts AS jsonb))"
            ),
            {
                "assembly_id": assembly_id,
                "facts": json.dumps(fact_rows, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
            },
        )

    async def confirm_assembly(
        self, *, assembly_id: UUID, audit_id: UUID, event_id: UUID
    ) -> dict | None:
        value = await self._session.scalar(
            text(
                "SELECT public.slice4_assembly_confirm_v1("
                ":assembly_id,:audit_id,:event_id)"
            ),
            {"assembly_id": assembly_id, "audit_id": audit_id, "event_id": event_id},
        )
        return dict(value) if value is not None else None

    async def govern_readiness_policy(
        self, operation: str, payload: Mapping[str, object]
    ) -> dict:
        try:
            value = await self._session.scalar(
                text(
                    "SELECT public.readiness_policy_governance_v1("
                    ":operation,CAST(:payload AS jsonb))"
                ),
                {
                    "operation": operation,
                    "payload": json.dumps(
                        dict(payload), ensure_ascii=True, separators=(",", ":"), sort_keys=True
                    ),
                },
            )
        except DBAPIError as exc:
            code = _safe_database_error(exc)
            if code is None:
                raise
            raise ReadinessPolicyRepositoryError(code) from None
        return dict(value)

    async def confirm_readiness_policy(self, payload: Mapping[str, object]) -> str:
        value = await self._session.scalar(
            text(
                "SELECT public.readiness_policy_governance_confirm_v1("
                "CAST(:payload AS jsonb))"
            ),
            {
                "payload": json.dumps(
                    dict(payload), ensure_ascii=True, separators=(",", ":"), sort_keys=True
                )
            },
        )
        if type(value) is not dict or value.get("outcome") not in {
            "COMMITTED",
            "NOT_COMMITTED",
            "UNKNOWN",
        }:
            return "UNKNOWN"
        return str(value["outcome"])

    async def readiness_policy_detail(self, policy_version_id: UUID) -> dict | None:
        row = (
            await self._session.execute(
                text(
                    "SELECT * FROM public.readiness_policy_governance_read_v1 "
                    "WHERE policy_version_id=:policy_version_id"
                ),
                {"policy_version_id": policy_version_id},
            )
        ).mappings().one_or_none()
        return dict(row) if row is not None else None

    async def readiness_policy_page(self, limit: int) -> list[dict]:
        rows = (
            await self._session.execute(
                text(
                    "SELECT * FROM public.readiness_policy_governance_read_v1 "
                    "ORDER BY version_no DESC,policy_version_id DESC LIMIT :limit"
                ),
                {"limit": limit},
            )
        ).mappings().all()
        return [dict(row) for row in rows]
