from datetime import date, datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from .domain import (
    HealthFactCursor, HealthProjectionFactDTO, HealthProjectionSelectionDTO,
    HealthSelectionCursor, OrganizationChildCursor, OrganizationProjectionNodeDTO,
    ProjectionGenerationDTO,
    HealthProjectionCoverageItem, MemberHealthProjectionFactDTO,
    ReadyHealthProjectionEvidence,
)


ORG_GENERATION = sa.table("organization_ready_projection_generation_v1", sa.column("generation_id"), sa.column("projection_version"), sa.column("ready_at"), schema="public")
ORG = sa.table("organization_ready_projection_v1", *[sa.column(name) for name in ("generation_id", "projection_version", "ready_at", "organization_id", "parent_id", "org_code", "org_name", "org_type", "status", "sort_order", "path_ids", "path_codes", "scope_eligible")], schema="public")
HEALTH_GENERATION = sa.table("health_ready_projection_generation_v1", sa.column("generation_id"), sa.column("projection_version"), sa.column("ready_at"), schema="public")
HEALTH_FACT = sa.table("health_ready_projection_fact_v1", *[sa.column(name) for name in ("generation_id", "projection_version", "ready_at", "fact_id", "subject_user_id", "indicator_code", "numeric_value", "unit", "measured_at", "received_at", "source_type", "business_day")], schema="public")
HEALTH_SELECTION = sa.table("health_ready_projection_window_selection_v1", *[sa.column(name) for name in ("generation_id", "projection_version", "ready_at", "subject_user_id", "indicator_code", "business_day", "winner_fact_id", "rule_version")], schema="public")


def _generation(row) -> ProjectionGenerationDTO:
    return ProjectionGenerationDTO(row.generation_id, row.projection_version, row.ready_at)


def _organization(row) -> OrganizationProjectionNodeDTO:
    return OrganizationProjectionNodeDTO(row.generation_id, row.organization_id, row.parent_id, row.org_code, row.org_name, row.org_type, row.status, row.sort_order, tuple(row.path_ids), tuple(row.path_codes), row.scope_eligible)


class OrganizationProjectionReadRepository:
    def __init__(self, session): self.session = session

    async def generation(self, generation_id: int):
        row = (await self.session.execute(sa.select(ORG_GENERATION).where(ORG_GENERATION.c.generation_id == generation_id))).one_or_none()
        return _generation(row) if row else None

    @staticmethod
    def _scope(roots: tuple[int, ...]):
        return sa.or_(*(ORG.c.path_ids.cast(JSONB).contains([root]) for root in roots))

    async def node(self, generation_id: int, organization_id: int, roots: tuple[int, ...], tenant_id: int, authorized_tenant_id: int):
        tenant_guard = sa.bindparam("tenant_id", tenant_id) == sa.bindparam("authorized_tenant_id", authorized_tenant_id)
        stmt = sa.select(ORG).where(ORG.c.generation_id == generation_id, ORG.c.organization_id == organization_id, ORG.c.status == "active", ORG.c.scope_eligible.is_(True), self._scope(roots), tenant_guard)
        row = (await self.session.execute(stmt)).one_or_none()
        return _organization(row) if row else None

    async def children(self, generation_id: int, parent_id: int, roots: tuple[int, ...], tenant_id: int, authorized_tenant_id: int, cursor: OrganizationChildCursor | None, limit: int):
        stmt = sa.select(ORG).where(ORG.c.generation_id == generation_id, ORG.c.parent_id == parent_id, ORG.c.status == "active", ORG.c.scope_eligible.is_(True), self._scope(roots), sa.bindparam("tenant_id", tenant_id) == sa.bindparam("authorized_tenant_id", authorized_tenant_id))
        if cursor: stmt = stmt.where(sa.tuple_(ORG.c.sort_order, ORG.c.organization_id) > (cursor.sort_order, cursor.organization_id))
        rows = (await self.session.execute(stmt.order_by(ORG.c.sort_order, ORG.c.organization_id).limit(limit + 1))).all()
        return tuple(_organization(row) for row in rows)

    async def path(self, generation_id: int, path_ids: tuple[int, ...], roots: tuple[int, ...], tenant_id: int, authorized_tenant_id: int):
        stmt = sa.select(ORG).where(ORG.c.generation_id == generation_id, ORG.c.organization_id.in_(path_ids), ORG.c.status == "active", ORG.c.scope_eligible.is_(True), self._scope(roots), sa.bindparam("tenant_id", tenant_id) == sa.bindparam("authorized_tenant_id", authorized_tenant_id))
        rows = (await self.session.execute(stmt)).all(); by_id = {row.organization_id: _organization(row) for row in rows}
        return tuple(by_id[value] for value in path_ids if value in by_id)


class HealthProjectionReadRepository:
    def __init__(self, session): self.session = session

    async def generation(self, generation_id: int):
        row = (await self.session.execute(sa.select(HEALTH_GENERATION).where(HEALTH_GENERATION.c.generation_id == generation_id))).one_or_none()
        return _generation(row) if row else None

    async def facts(self, generation_id: int, subject_id: int, indicators: tuple[str, ...], start: datetime, end: datetime, cursor: HealthFactCursor | None, limit: int, tenant_id: int, authorized_tenant_id: int):
        stmt = sa.select(HEALTH_FACT).where(HEALTH_FACT.c.generation_id == generation_id, HEALTH_FACT.c.subject_user_id == subject_id, HEALTH_FACT.c.indicator_code.in_(indicators), HEALTH_FACT.c.measured_at >= start, HEALTH_FACT.c.measured_at < end, sa.bindparam("tenant_id", tenant_id) == sa.bindparam("authorized_tenant_id", authorized_tenant_id))
        if cursor: stmt = stmt.where(sa.tuple_(HEALTH_FACT.c.measured_at, HEALTH_FACT.c.fact_id) > (cursor.measured_at, cursor.fact_id))
        rows = (await self.session.execute(stmt.order_by(HEALTH_FACT.c.measured_at, HEALTH_FACT.c.fact_id).limit(limit + 1))).all()
        return tuple(HealthProjectionFactDTO(row.generation_id, row.fact_id, row.subject_user_id, row.indicator_code, row.numeric_value, row.unit, row.measured_at, row.received_at, row.source_type, row.business_day) for row in rows)

    async def selections(self, generation_id: int, subject_id: int, indicators: tuple[str, ...], start: date, end: date, cursor: HealthSelectionCursor | None, limit: int, tenant_id: int, authorized_tenant_id: int):
        stmt = sa.select(HEALTH_SELECTION).where(HEALTH_SELECTION.c.generation_id == generation_id, HEALTH_SELECTION.c.subject_user_id == subject_id, HEALTH_SELECTION.c.indicator_code.in_(indicators), HEALTH_SELECTION.c.business_day >= start, HEALTH_SELECTION.c.business_day < end, sa.bindparam("tenant_id", tenant_id) == sa.bindparam("authorized_tenant_id", authorized_tenant_id))
        if cursor: stmt = stmt.where(sa.tuple_(HEALTH_SELECTION.c.business_day, HEALTH_SELECTION.c.indicator_code, HEALTH_SELECTION.c.winner_fact_id) > (cursor.business_day, cursor.indicator_code, cursor.winner_fact_id))
        rows = (await self.session.execute(stmt.order_by(HEALTH_SELECTION.c.business_day, HEALTH_SELECTION.c.indicator_code, HEALTH_SELECTION.c.winner_fact_id).limit(limit + 1))).all()
        return tuple(HealthProjectionSelectionDTO(row.generation_id, row.subject_user_id, row.indicator_code, row.business_day, row.winner_fact_id, row.rule_version) for row in rows)


class MemberHealthProjectionReadRepository:
    def __init__(self, session):
        self.session = session

    async def coverage(
        self, subject_member_id: UUID, indicators: tuple[str, ...]
    ) -> tuple[str, tuple[HealthProjectionCoverageItem, ...]]:
        value = await self.session.scalar(
            sa.text(
                "SELECT public.slice4_projection_coverage_v2(:subject_member_id,CAST(:indicators AS jsonb))"
            ),
            {
                "subject_member_id": subject_member_id,
                "indicators": __import__("json").dumps(indicators, separators=(",", ":")),
            },
        )
        rows = () if not value else tuple(value.get("items") or ())
        items = tuple(
            HealthProjectionCoverageItem(
                indicator_code=row["indicator_code"],
                max_fact_id=int(row["max_fact_id"]),
                max_status_event_seq=int(row["max_status_event_seq"]),
                fact_count=int(row["fact_count"]),
                status_event_count=int(row["status_event_count"]),
                current_fact_set_digest=row["fact_set_digest"],
                current_status_set_digest=row["status_set_digest"],
            )
            for row in rows
        )
        candidates = await self.ready_candidates(subject_member_id, indicators)
        matching = [candidate for candidate in candidates if candidate.items == items]
        if matching:
            newest = max(candidate.generation_no for candidate in matching)
            winners = [candidate for candidate in matching if candidate.generation_no == newest]
            if len(winners) == 1:
                return winners[0].source_snapshot, items
        return str(value["source_snapshot"]), items

    async def ready_candidates(
        self, subject_member_id: UUID, indicators: tuple[str, ...]
    ) -> tuple[ReadyHealthProjectionEvidence, ...]:
        rows = (
            await self.session.execute(
                sa.text(
                    "SELECT e.generation_id,e.generation_no,e.projection_version,e.rule_version,e.ready_at,"
                    "e.subject_member_id,e.source_snapshot,e.indicator_code,e.fact_count,"
                    "e.status_event_count,e.max_fact_id,e.max_status_event_seq,e.fact_set_digest,"
                    "e.status_set_digest FROM public.health_ready_subject_indicator_evidence_v2 e "
                    "WHERE e.subject_member_id=:subject_member_id AND e.indicator_code=ANY(:indicators) "
                    "ORDER BY e.generation_no,e.indicator_code"
                ),
                {"subject_member_id": subject_member_id, "indicators": list(indicators)},
            )
        ).mappings().all()
        grouped: dict[int, list] = {}
        for row in rows:
            grouped.setdefault(row["generation_id"], []).append(row)
        values = []
        for generation_id, evidence_rows in grouped.items():
            if tuple(row["indicator_code"] for row in evidence_rows) != tuple(sorted(indicators)):
                continue
            first = evidence_rows[0]
            items = tuple(
                HealthProjectionCoverageItem(
                    indicator_code=row["indicator_code"],
                    max_fact_id=row["max_fact_id"],
                    max_status_event_seq=row["max_status_event_seq"],
                    fact_count=row["fact_count"],
                    status_event_count=row["status_event_count"],
                    current_fact_set_digest=row["fact_set_digest"],
                    current_status_set_digest=row["status_set_digest"],
                )
                for row in evidence_rows
            )
            values.append(
                ReadyHealthProjectionEvidence(
                    generation_id=generation_id,
                    generation_no=first["generation_no"],
                    projection_version=first["projection_version"],
                    rule_version=first["rule_version"],
                    subject_member_id=UUID(str(first["subject_member_id"])),
                    source_snapshot=first["source_snapshot"],
                    policy_indicator_digest="",
                    items=items,
                    ready_at=first["ready_at"],
                )
            )
        return tuple(values)

    async def facts(
        self,
        *,
        generation_id: int,
        subject_member_id: UUID,
        indicators: tuple[str, ...],
        measured_from: datetime,
        measured_to: datetime,
        limit: int,
        cursor: tuple[datetime, UUID] | None = None,
    ) -> tuple[MemberHealthProjectionFactDTO, ...]:
        cursor_sql = ""
        parameters = {
            "generation_id": generation_id,
            "subject_member_id": subject_member_id,
            "indicators": list(indicators),
            "measured_from": measured_from,
            "measured_to": measured_to,
            "limit": limit,
        }
        if cursor is not None:
            cursor_sql = " AND (measured_at,fact_ref)<(:cursor_measured_at,:cursor_fact_ref)"
            parameters.update(
                cursor_measured_at=cursor[0], cursor_fact_ref=cursor[1]
            )
        rows = (
            await self.session.execute(
                sa.text(
                    "SELECT generation_id,fact_ref,subject_member_id,subject_user_id,indicator_code,"
                    "numeric_value,unit,measured_at,received_at,source_type,business_day,"
                    "verification_state,status_event_seq FROM public.health_ready_projection_fact_v2 "
                    "WHERE generation_id=:generation_id AND subject_member_id=:subject_member_id "
                    "AND indicator_code=ANY(:indicators) AND measured_at>=:measured_from "
                    "AND measured_at<:measured_to" + cursor_sql
                    + " ORDER BY measured_at DESC,fact_ref DESC LIMIT :limit"
                ),
                parameters,
            )
        ).mappings().all()
        return tuple(
            MemberHealthProjectionFactDTO(
                generation_id=row["generation_id"],
                fact_ref=UUID(str(row["fact_ref"])),
                subject_member_id=UUID(str(row["subject_member_id"])),
                subject_user_id=row["subject_user_id"],
                indicator_code=row["indicator_code"],
                numeric_value=row["numeric_value"],
                unit=row["unit"],
                measured_at=row["measured_at"],
                received_at=row["received_at"],
                source_type=row["source_type"],
                business_day=row["business_day"],
                verification_state=row["verification_state"],
                status_event_seq=row["status_event_seq"],
            )
            for row in rows
        )
