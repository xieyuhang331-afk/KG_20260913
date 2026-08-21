from __future__ import annotations

from uuid import UUID
import json

from sqlalchemy import text


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
