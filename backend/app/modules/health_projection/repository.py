import asyncio
import hashlib
import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import BigInteger, column, func, insert, select, table, text, update
from sqlalchemy.orm import load_only

from app.core.sqlalchemy_mapping import map_core_model_classes
from app.modules.health_fact.models import CanonicalHealthFactOrmModel
from app.modules.health_fact_mapping.models import HealthIndicatorLegacyMappingOrmModel
from app.modules.system.models import OperationLog
from .domain import (
    HealthCurrentFact,
    HealthCurrentFactV2,
    HealthProjectionFactRow,
    HealthProjectionFactRowV2,
    HealthWindowSelection,
    HealthWindowSelectionV2,
    build_health_projection_rows,
    build_health_projection_rows_v2,
)
from app.modules.organization_projection.service import OperationPostimage, ProjectionCheckpointConflict, _digest
from .models import (
    HealthProjectionCheckpoint, HealthProjectionFactModel,
    HealthProjectionGeneration, HealthProjectionShadowAudit,
    HealthProjectionShadowRun, HealthProjectionWindowSelectionModel,
)


class HealthProjectionPersistenceUnavailable(Exception):
    pass


async def _safe(awaitable):
    try:
        return await awaitable
    except asyncio.CancelledError:
        raise
    except Exception:
        raise HealthProjectionPersistenceUnavailable("Health projection persistence is unavailable") from None


class HealthProjectionRepository:
    def __init__(self, session):
        map_core_model_classes()
        self.session = session
        self.source = CanonicalHealthFactOrmModel.__table__
        self.visibility = table(
            "health_projection_source_visibility_v1",
            column("id", BigInteger),
            column("supersedes_fact_id", BigInteger),
            column("inserting_xid", BigInteger),
            schema="public",
        )

    def _visible(self, source_snapshot):
        return text("txid_visible_in_snapshot(inserting_xid, CAST(:source_snapshot AS txid_snapshot))").bindparams(source_snapshot=source_snapshot)

    async def export_source_snapshot(self):
        result = await _safe(self.session.execute(text("SELECT txid_current_snapshot()::text")))
        return str(result.scalar_one())

    async def max_source_id(self, *, source_snapshot=None):
        stmt = select(func.coalesce(func.max(self.visibility.c.id), 0))
        if source_snapshot is not None: stmt = stmt.where(self._visible(source_snapshot))
        result = await _safe(self.session.execute(stmt))
        return int(result.scalar_one())

    async def current_source_ids(self, *, after_id, max_id, limit, source_snapshot=None):
        successor = self.visibility.alias("successor")
        successor_visible = text("txid_visible_in_snapshot(successor.inserting_xid, CAST(:source_snapshot AS txid_snapshot))").bindparams(source_snapshot=source_snapshot) if source_snapshot is not None else True
        stmt = select(self.visibility.c.id).where(
            self.visibility.c.id <= max_id,
            ~select(successor.c.id).where(successor.c.supersedes_fact_id == self.visibility.c.id, successor.c.id <= max_id, successor_visible).exists(),
        )
        if source_snapshot is not None: stmt = stmt.where(self._visible(source_snapshot))
        if after_id is not None:
            stmt = stmt.where(self.visibility.c.id > after_id)
        result = await _safe(self.session.execute(stmt.order_by(self.visibility.c.id).limit(limit)))
        return tuple(result.scalars())

    async def count_sources(self, max_id: int, *, source_snapshot=None) -> int:
        successor = self.visibility.alias("successor")
        successor_visible = text("txid_visible_in_snapshot(successor.inserting_xid, CAST(:source_snapshot AS txid_snapshot))").bindparams(source_snapshot=source_snapshot) if source_snapshot is not None else True
        stmt = select(func.count()).select_from(self.visibility).where(
            self.visibility.c.id <= max_id,
            ~select(successor.c.id).where(successor.c.supersedes_fact_id == self.visibility.c.id, successor.c.id <= max_id, successor_visible).exists(),
        )
        if source_snapshot is not None: stmt = stmt.where(self._visible(source_snapshot))
        result = await _safe(self.session.execute(stmt))
        return int(result.scalar_one())

    async def list_source_ids(self, *, after_id, max_id, limit, source_snapshot=None):
        return await self.current_source_ids(after_id=after_id, max_id=max_id, limit=limit, source_snapshot=source_snapshot)

    async def load_facts(self, ids: tuple[int, ...]) -> tuple[HealthCurrentFact, ...]:
        columns = (self.source.c.id, self.source.c.subject_user_id, self.source.c.indicator_code, self.source.c.numeric_value, self.source.c.unit, self.source.c.measured_at, self.source.c.received_at, self.source.c.source_type)
        result = await _safe(self.session.execute(select(*columns).where(self.source.c.id.in_(ids)).order_by(self.source.c.id)))
        return tuple(HealthCurrentFact(row.id, row.subject_user_id, row.indicator_code, row.numeric_value, row.unit, row.measured_at, row.received_at, row.source_type) for row in result)

    async def capture_v2_sources(self, *, source_snapshot: str) -> dict:
        result = await _safe(
            self.session.execute(
                text(
                    "SELECT public.health_projection_builder_source_v2("
                    "9223372036854775807,CAST(:page AS jsonb),:source_snapshot)"
                ),
                {
                    "page": json.dumps({"capture": True}, separators=(",", ":")),
                    "source_snapshot": source_snapshot,
                },
            )
        )
        return dict(result.scalar_one())

    async def load_v2_facts(
        self,
        *,
        after_id: int | None,
        max_fact_id: int,
        max_status_event_seq: int,
        source_snapshot: str,
        limit: int,
    ) -> tuple[HealthCurrentFactV2, ...]:
        result = await _safe(
            self.session.execute(
                text(
                    "SELECT public.health_projection_builder_source_v2("
                    ":max_fact_id,CAST(:page AS jsonb),:source_snapshot)"
                ),
                {
                    "max_fact_id": max_fact_id,
                    "page": json.dumps(
                        {
                            "after_fact_id": after_id,
                            "limit": limit,
                            "max_status_event_seq": max_status_event_seq,
                        },
                        separators=(",", ":"),
                    ),
                    "source_snapshot": source_snapshot,
                },
            )
        )
        payload = dict(result.scalar_one())
        return tuple(
            HealthCurrentFactV2(
                id=int(row["fact_id"]),
                fact_ref=UUID(str(row["fact_ref"])),
                subject_member_id=UUID(str(row["subject_member_id"])),
                subject_user_id=row.get("subject_user_id"),
                indicator_code=str(row["indicator_code"]),
                numeric_value=Decimal(str(row["numeric_value"])),
                unit=str(row["unit"]),
                measured_at=datetime.fromisoformat(str(row["measured_at"])),
                received_at=datetime.fromisoformat(str(row["received_at"])),
                source_type=str(row["source_type"]),
                verification_state=str(row["verification_state"]),
                status_event_seq=int(row["status_event_seq"]),
                superseded=False,
                fact_payload_digest=str(row["fact_payload_digest"]),
                status_event_digest=str(row["status_event_digest"]),
            )
            for row in payload["facts"]
        )

    async def load_all_v2_facts(self, high_watermark: dict) -> tuple[HealthCurrentFactV2, ...]:
        rows: list[HealthCurrentFactV2] = []
        after_id = None
        while True:
            page = await self.load_v2_facts(
                after_id=after_id,
                max_fact_id=int(high_watermark["max_fact_id"]),
                max_status_event_seq=int(high_watermark["max_status_event_seq"]),
                source_snapshot=str(high_watermark["source_snapshot"]),
                limit=500,
            )
            if not page:
                return tuple(rows)
            rows.extend(page)
            after_id = page[-1].id

    async def get_generation(self, generation_id: int, *, lock: bool = False):
        fields = tuple(getattr(HealthProjectionGeneration, name) for name in (
            "id", "projection_version", "generation_no", "status", "high_watermark",
            "digest_key_id", "input_digest", "start_operation_id", "builder_id",
            "lease_epoch", "lease_expires_at", "created_at", "updated_at",
            "completed_at", "failure_code", "version",
        ))
        stmt = select(HealthProjectionGeneration).options(load_only(*fields)).where(HealthProjectionGeneration.id == generation_id)
        result = await _safe(self.session.execute(stmt.with_for_update() if lock else stmt))
        return result.scalar_one_or_none()

    async def get_shadow_generation(self, generation_id: int, *, lock: bool = False):
        fields = tuple(getattr(HealthProjectionGeneration, name) for name in (
            "id", "projection_version", "status", "high_watermark", "digest_key_id",
            "input_digest", "completed_at", "updated_at", "version",
            "current_shadow_run_id", "shadow_success_count", "ready_at",
            "ready_operation_id",
        ))
        statement = select(HealthProjectionGeneration).options(load_only(*fields)).where(
            HealthProjectionGeneration.id == generation_id
        )
        result = await _safe(self.session.execute(statement.with_for_update() if lock else statement))
        return result.scalar_one_or_none()

    async def get_checkpoint(self, generation_id: int, *, lock: bool = False):
        stmt = select(HealthProjectionCheckpoint).where(HealthProjectionCheckpoint.generation_id == generation_id)
        result = await _safe(self.session.execute(stmt.with_for_update() if lock else stmt))
        return result.scalar_one_or_none()

    async def add_generation(self, generation, checkpoint) -> None:
        result = await _safe(self.session.execute(
            insert(HealthProjectionGeneration).values(
                projection_version=generation.projection_version,
                generation_no=generation.generation_no,
                status=generation.status,
                high_watermark=generation.high_watermark,
                digest_key_id=generation.digest_key_id,
                input_digest=generation.input_digest,
                start_operation_id=generation.start_operation_id,
                builder_id=generation.builder_id,
                lease_epoch=generation.lease_epoch,
                lease_expires_at=generation.lease_expires_at,
            ).returning(HealthProjectionGeneration.id)
        ))
        generation.id = int(result.scalar_one())
        generation.version = 1
        checkpoint.generation_id = generation.id
        checkpoint.version = 1
        await _safe(self.session.execute(
            insert(HealthProjectionCheckpoint).inline().values(
                generation_id=checkpoint.generation_id,
                last_source_id=checkpoint.last_source_id,
                processed_count=checkpoint.processed_count,
                projected_count=checkpoint.projected_count,
                skipped_count=checkpoint.skipped_count,
                remaining_count=checkpoint.remaining_count,
                last_operation_id=checkpoint.last_operation_id,
                checkpoint_digest=checkpoint.checkpoint_digest,
                version=checkpoint.version,
            )
        ))

    async def acquire_window_lock(self, key: int) -> None:
        await _safe(self.session.execute(select(func.pg_advisory_xact_lock(key))))

    async def add_facts(self, generation_id: int, key_id: str, rows: tuple[HealthProjectionFactRow, ...]) -> None:
        for row in rows:
            self.session.add(HealthProjectionFactModel(
                generation_id=generation_id, fact_id=row.fact_id,
                subject_user_id=row.subject_user_id, indicator_code=row.indicator_code,
                numeric_value=row.numeric_value, unit=row.unit, measured_at=row.measured_at,
                received_at=row.received_at, source_type=row.source_type,
                business_day=row.business_day, window_start_utc=row.window_start_utc,
                window_end_utc=row.window_end_utc, row_digest=row.row_digest,
                digest_key_id=key_id,
            ))
        await _safe(self.session.flush())

    async def add_facts_v2(
        self,
        generation_id: int,
        key_id: str,
        rows: tuple[HealthProjectionFactRowV2, ...],
    ) -> None:
        for row in rows:
            self.session.add(HealthProjectionFactModel(
                generation_id=generation_id,
                fact_id=row.fact_id,
                fact_ref=str(row.fact_ref),
                subject_member_id=str(row.subject_member_id),
                subject_user_id=row.subject_user_id,
                status_event_seq=row.status_event_seq,
                indicator_code=row.indicator_code,
                numeric_value=row.numeric_value,
                unit=row.unit,
                measured_at=row.measured_at,
                received_at=row.received_at,
                source_type=row.source_type,
                business_day=row.business_day,
                window_start_utc=row.window_start_utc,
                window_end_utc=row.window_end_utc,
                row_digest=row.row_digest,
                digest_key_id=key_id,
            ))
        await _safe(self.session.flush())

    async def load_projected_window(self, generation_id: int, subject_user_id: int, indicator_code: str, business_day) -> tuple[HealthCurrentFact, ...]:
        stmt = select(
            HealthProjectionFactModel.fact_id,
            HealthProjectionFactModel.subject_user_id,
            HealthProjectionFactModel.indicator_code,
            HealthProjectionFactModel.numeric_value,
            HealthProjectionFactModel.unit,
            HealthProjectionFactModel.measured_at,
            HealthProjectionFactModel.received_at,
            HealthProjectionFactModel.source_type,
        ).where(
            HealthProjectionFactModel.generation_id == generation_id,
            HealthProjectionFactModel.subject_user_id == subject_user_id,
            HealthProjectionFactModel.indicator_code == indicator_code,
            HealthProjectionFactModel.business_day == business_day,
        ).order_by(HealthProjectionFactModel.fact_id)
        result = await _safe(self.session.execute(stmt))
        return tuple(HealthCurrentFact(row.fact_id, row.subject_user_id, row.indicator_code, row.numeric_value, row.unit, row.measured_at, row.received_at, row.source_type) for row in result)

    async def add_selections(self, generation_id: int, key_id: str, selections: tuple[HealthWindowSelection, ...]) -> None:
        for selection in selections:
            self.session.add(HealthProjectionWindowSelectionModel(
                generation_id=generation_id, subject_user_id=selection.subject_user_id,
                indicator_code=selection.indicator_code, business_day=selection.business_day,
                winner_fact_id=selection.winner_fact_id, rule_version=selection.rule_version,
                selection_digest=selection.selection_digest, digest_key_id=key_id,
            ))
        await _safe(self.session.flush())

    async def add_selections_v2(
        self,
        generation_id: int,
        key_id: str,
        selections: tuple[HealthWindowSelectionV2, ...],
    ) -> None:
        for selection in selections:
            self.session.add(HealthProjectionWindowSelectionModel(
                generation_id=generation_id,
                subject_user_id=None,
                subject_member_id=str(selection.subject_member_id),
                indicator_code=selection.indicator_code,
                business_day=selection.business_day,
                winner_fact_id=selection.winner_fact_id,
                winner_fact_ref=str(selection.winner_fact_ref),
                rule_version=selection.rule_version,
                selection_digest=selection.selection_digest,
                digest_key_id=key_id,
            ))
        await _safe(self.session.flush())

    async def add_subject_evidence_v2(
        self,
        *,
        generation_id: int,
        key_id: str,
        source_snapshot: str,
        facts: tuple[HealthCurrentFactV2, ...],
    ) -> None:
        groups: dict[tuple[UUID, str], list[HealthCurrentFactV2]] = {}
        for fact in facts:
            groups.setdefault((fact.subject_member_id, fact.indicator_code), []).append(fact)
        for (member_id, indicator), values in sorted(
            groups.items(), key=lambda item: (str(item[0][0]), item[0][1])
        ):
            fact_digests = sorted(str(value.fact_payload_digest) for value in values)
            status_digests = sorted(str(value.status_event_digest) for value in values)
            fact_set = hashlib.sha256(",".join(fact_digests).encode()).hexdigest()
            status_set = hashlib.sha256(",".join(status_digests).encode()).hexdigest()
            evidence = hashlib.sha256(
                (
                    f"{generation_id}:{member_id}:{indicator}:{fact_set}:"
                    f"{status_set}:{source_snapshot}"
                ).encode()
            ).hexdigest()
            await _safe(self.session.execute(
                text(
                    "INSERT INTO public.health_projection_subject_indicator_evidence_v2("
                    "generation_id,subject_member_id,indicator_code,fact_count,"
                    "status_event_count,max_fact_id,max_status_event_seq,source_snapshot,"
                    "fact_set_digest,status_set_digest,evidence_digest,digest_key_id,created_at) "
                    "VALUES(:generation_id,:member_id,:indicator,:fact_count,:status_count,"
                    ":max_fact_id,:max_status_seq,:source_snapshot,:fact_set,:status_set,"
                    ":evidence,:key_id,clock_timestamp())"
                ),
                {
                    "generation_id": generation_id,
                    "member_id": member_id,
                    "indicator": indicator,
                    "fact_count": len(values),
                    "status_count": len(values),
                    "max_fact_id": max(value.id for value in values),
                    "max_status_seq": max(value.status_event_seq for value in values),
                    "source_snapshot": source_snapshot,
                    "fact_set": fact_set,
                    "status_set": status_set,
                    "evidence": evidence,
                    "key_id": key_id,
                },
            ))

    async def heartbeat(self, *, generation_id, builder_id, lease_epoch, expires_at, operation_id):
        result = await _safe(self.session.execute(update(HealthProjectionGeneration).where(
            HealthProjectionGeneration.id == generation_id,
            HealthProjectionGeneration.status == "BUILDING",
            HealthProjectionGeneration.builder_id == builder_id,
            HealthProjectionGeneration.lease_epoch == lease_epoch,
            HealthProjectionGeneration.lease_expires_at > func.now(),
        ).values(lease_expires_at=expires_at, updated_at=func.now(), version=HealthProjectionGeneration.version + 1)))
        return result.rowcount == 1

    async def add_audit(self, *, generation_id: int, action: str, operation_id: str, payload: dict) -> None:
        statement = insert(OperationLog).inline().values(
            operator_id=None,
            module="basic_projection_builder",
            object_type="health_projection_generation",
            object_id=generation_id,
            action=action,
            payload={"operation_id": operation_id, **payload},
        )
        await _safe(self.session.execute(statement))

    async def audit_payload(self, operation_id: str):
        record = await self.audit_record(operation_id)
        return None if record is None else record[1]

    async def audit_record(self, operation_id: str):
        result = await _safe(self.session.execute(text("SELECT object_id,payload FROM public.health_projection_operation_audit WHERE payload->>'operation_id'=:operation_id").bindparams(operation_id=operation_id)))
        row = result.one_or_none()
        return None if row is None else (int(row.object_id), row.payload)

    async def has_newer_complete_generation(self, generation) -> bool:
        result = await _safe(self.session.execute(select(func.count()).select_from(HealthProjectionGeneration).where(
            HealthProjectionGeneration.projection_version == generation.projection_version,
            HealthProjectionGeneration.generation_no > generation.generation_no,
            HealthProjectionGeneration.status == "BUILD_COMPLETE",
        )))
        return int(result.scalar_one()) > 0

    async def count_rows(self, generation_id: int) -> int:
        result = await _safe(self.session.execute(select(func.count()).select_from(HealthProjectionFactModel).where(HealthProjectionFactModel.generation_id == generation_id)))
        return int(result.scalar_one())

    async def has_remaining_sources(self, after_id, max_id: int, source_snapshot=None) -> bool:
        return bool(await self.list_source_ids(after_id=after_id, max_id=max_id, limit=1, source_snapshot=source_snapshot))

    async def load_all_source_facts(self, max_id: int, source_snapshot: str):
        ids = await self.current_source_ids(after_id=None, max_id=max_id, limit=max_id + 1, source_snapshot=source_snapshot)
        return await self.load_facts(ids)

    async def load_all_projected_facts(self, generation_id: int):
        result = await _safe(self.session.execute(select(HealthProjectionFactModel).where(HealthProjectionFactModel.generation_id == generation_id).order_by(HealthProjectionFactModel.fact_id)))
        return tuple(HealthCurrentFact(r.fact_id, r.subject_user_id, r.indicator_code, r.numeric_value, r.unit, r.measured_at, r.received_at, r.source_type) for r in result.scalars())

    async def list_projected_windows(self, generation_id: int):
        result = await _safe(self.session.execute(select(
            HealthProjectionFactModel.subject_user_id,
            HealthProjectionFactModel.indicator_code,
            HealthProjectionFactModel.business_day,
        ).where(HealthProjectionFactModel.generation_id == generation_id).distinct().order_by(
            HealthProjectionFactModel.subject_user_id,
            HealthProjectionFactModel.indicator_code,
            HealthProjectionFactModel.business_day,
        )))
        return tuple(result)

    async def validate_completion(self, generation_id, checkpoint, key_id, stored_key, high_watermark) -> str:
        facts = await self.load_all_source_facts(high_watermark["max_fact_id"], high_watermark["source_snapshot"])
        result = await _safe(self.session.execute(select(HealthProjectionWindowSelectionModel).where(HealthProjectionWindowSelectionModel.generation_id == generation_id)))
        selections = tuple(result.scalars())
        expected_rows, expected_selections = build_health_projection_rows(facts=facts, digest_key=stored_key)
        windows = {(f.subject_user_id, f.indicator_code, f.measured_at.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).date()) for f in facts}
        identities = {(s.subject_user_id, s.indicator_code, s.business_day) for s in selections}
        persisted = await _safe(self.session.execute(select(HealthProjectionFactModel).where(HealthProjectionFactModel.generation_id == generation_id).order_by(HealthProjectionFactModel.fact_id)))
        persisted_rows = tuple(persisted.scalars())
        row_digests = tuple((row.fact_id, row.row_digest, row.digest_key_id) for row in persisted_rows)
        wanted_rows = tuple((row.fact_id, row.row_digest, key_id) for row in expected_rows)
        selection_digests = tuple(sorted((s.subject_user_id, s.indicator_code, s.business_day, s.winner_fact_id, s.rule_version, s.selection_digest, s.digest_key_id) for s in selections))
        wanted_selections = tuple(sorted((s.subject_user_id, s.indicator_code, s.business_day, s.winner_fact_id, s.rule_version, s.selection_digest, key_id) for s in expected_selections))
        if len(facts) != checkpoint.projected_count or checkpoint.remaining_count != 0 or identities != windows or len(selections) != len(identities) or row_digests != wanted_rows or selection_digests != wanted_selections:
            raise ProjectionCheckpointConflict("Projection checkpoint conflicts")
        return _digest({"generation_id": generation_id, "rows": row_digests, "selections": selection_digests, "digest_key_id": key_id})

    async def validate_completion_v2(
        self, generation_id, checkpoint, key_id, stored_key, high_watermark
    ) -> tuple[str, tuple[HealthCurrentFactV2, ...]]:
        facts = await self.load_all_v2_facts(high_watermark)
        expected_rows, expected_selections = build_health_projection_rows_v2(
            facts=facts, digest_key=stored_key
        )
        persisted = await _safe(self.session.execute(
            select(HealthProjectionFactModel).where(
                HealthProjectionFactModel.generation_id == generation_id
            ).order_by(HealthProjectionFactModel.fact_id)
        ))
        actual_rows = tuple(
            (
                row.fact_id, row.fact_ref, row.subject_member_id,
                row.status_event_seq, row.row_digest, row.digest_key_id,
            )
            for row in persisted.scalars()
        )
        wanted_rows = tuple(
            (
                row.fact_id, str(row.fact_ref), str(row.subject_member_id),
                row.status_event_seq, row.row_digest, key_id,
            )
            for row in expected_rows
        )
        selections_result = await _safe(self.session.execute(
            select(HealthProjectionWindowSelectionModel).where(
                HealthProjectionWindowSelectionModel.generation_id == generation_id
            )
        ))
        actual_selections = tuple(sorted(
            (
                row.subject_member_id, row.indicator_code, row.business_day,
                row.winner_fact_id, row.winner_fact_ref, row.rule_version,
                row.selection_digest, row.digest_key_id,
            )
            for row in selections_result.scalars()
        ))
        wanted_selections = tuple(sorted(
            (
                str(row.subject_member_id), row.indicator_code, row.business_day,
                row.winner_fact_id, str(row.winner_fact_ref), row.rule_version,
                row.selection_digest, key_id,
            )
            for row in expected_selections
        ))
        if (
            len(facts) != checkpoint.projected_count
            or checkpoint.remaining_count != 0
            or actual_rows != wanted_rows
            or actual_selections != wanted_selections
        ):
            raise ProjectionCheckpointConflict("Projection checkpoint conflicts")
        evidence = _digest({
            "generation_id": generation_id,
            "rows": actual_rows,
            "selections": actual_selections,
            "high_watermark": high_watermark,
            "digest_key_id": key_id,
        })
        return evidence, facts

    async def operation_preimage(self, generation, checkpoint, expected):
        if generation is None:
            return expected if expected.get("operation") == "start" else None
        actual = dict(expected)
        if "generation_version" in actual: actual["generation_version"] = generation.version
        if "checkpoint_digest" in actual: actual["checkpoint_digest"] = None if checkpoint is None else checkpoint.checkpoint_digest
        if "lease_epoch" in actual: actual["lease_epoch"] = generation.lease_epoch
        if "status" in actual: actual["status"] = generation.status
        return actual

    async def operation_postimage(self, generation, checkpoint, audit):
        rows = await self.count_rows(generation.id)
        result = await _safe(self.session.execute(select(func.count()).select_from(HealthProjectionWindowSelectionModel).where(HealthProjectionWindowSelectionModel.generation_id == generation.id)))
        selections = int(result.scalar_one())
        return OperationPostimage(generation.status, generation.version, generation.high_watermark, generation.digest_key_id, checkpoint.checkpoint_digest, checkpoint.version, checkpoint.processed_count, checkpoint.projected_count, checkpoint.skipped_count, checkpoint.remaining_count, rows, selections, audit.get("completion_evidence", ""), generation.builder_id, generation.lease_epoch, generation.lease_expires_at, generation.completed_at, generation.failure_code, checkpoint.last_operation_id)

    async def load_shadow_inputs(self, generation):
        if generation.projection_version == 2:
            facts = await self.load_all_v2_facts(generation.high_watermark)
            facts_by_id = {fact.id: fact for fact in facts}
            projected_result = await _safe(self.session.execute(
                select(HealthProjectionFactModel).options(load_only(*(
                    getattr(HealthProjectionFactModel, name)
                    for name in HealthProjectionFactRowV2.__dataclass_fields__
                    if name != "verification_state"
                ))).where(
                    HealthProjectionFactModel.generation_id == generation.id
                ).order_by(HealthProjectionFactModel.fact_id)
            ))
            projected = tuple(
                type("ProjectedV2", (), {
                    name: (
                        facts_by_id[row.fact_id].verification_state
                        if name == "verification_state"
                        else getattr(row, name)
                    )
                    for name in HealthProjectionFactRowV2.__dataclass_fields__
                })()
                for row in projected_result.scalars()
            )
            selection_result = await _safe(self.session.execute(
                select(HealthProjectionWindowSelectionModel).options(load_only(*(
                    getattr(HealthProjectionWindowSelectionModel, name)
                    for name in HealthWindowSelectionV2.__dataclass_fields__
                ))).where(
                    HealthProjectionWindowSelectionModel.generation_id == generation.id
                ).order_by(
                    HealthProjectionWindowSelectionModel.subject_member_id,
                    HealthProjectionWindowSelectionModel.indicator_code,
                    HealthProjectionWindowSelectionModel.business_day,
                )
            ))
            selections = tuple(selection_result.scalars())
            visibility_rows = tuple(type("VisibilityV2", (), {
                "fact_id": fact.id,
                "supersedes_fact_id": None,
                "inserting_xid": 0,
                "is_current": not fact.superseded,
                "status_event_seq": fact.status_event_seq,
                "fact_payload_digest": fact.fact_payload_digest,
                "status_event_digest": fact.status_event_digest,
            })() for fact in facts)
            return facts, (), projected, selections, visibility_rows, ()

        maximum = generation.high_watermark["max_fact_id"]
        snapshot = generation.high_watermark["source_snapshot"]
        facts = await self.load_all_source_facts(maximum, snapshot)
        mappings = await _safe(self.session.execute(
            select(HealthIndicatorLegacyMappingOrmModel).options(load_only(*(
                getattr(HealthIndicatorLegacyMappingOrmModel, name) for name in (
                    "id", "legacy_indicator_id", "legacy_recorded_at",
                    "canonical_fact_id", "mapping_version", "source_fingerprint",
                    "digest_key_id", "disposition", "reason_code", "created_at",
                )
            ))).order_by(
                HealthIndicatorLegacyMappingOrmModel.legacy_recorded_at,
                HealthIndicatorLegacyMappingOrmModel.legacy_indicator_id,
                HealthIndicatorLegacyMappingOrmModel.mapping_version,
            )
        ))
        projected = await _safe(self.session.execute(
            select(HealthProjectionFactModel).options(load_only(*(
                getattr(HealthProjectionFactModel, name) for name in (
                    "generation_id", "fact_id", "subject_user_id", "indicator_code",
                    "numeric_value", "unit", "measured_at", "received_at", "source_type",
                    "business_day", "window_start_utc", "window_end_utc", "row_digest",
                    "digest_key_id",
                )
            ))).where(
                HealthProjectionFactModel.generation_id == generation.id
            ).order_by(HealthProjectionFactModel.fact_id)
        ))
        selections = await _safe(self.session.execute(
            select(HealthProjectionWindowSelectionModel).options(load_only(*(
                getattr(HealthProjectionWindowSelectionModel, name) for name in (
                    "generation_id", "subject_user_id", "indicator_code", "business_day",
                    "winner_fact_id", "rule_version", "selection_digest", "digest_key_id",
                )
            ))).where(
                HealthProjectionWindowSelectionModel.generation_id == generation.id
            ).order_by(
                HealthProjectionWindowSelectionModel.subject_user_id,
                HealthProjectionWindowSelectionModel.indicator_code,
                HealthProjectionWindowSelectionModel.business_day,
            )
        ))
        visibility = await _safe(self.session.execute(
            select(
                self.visibility.c.id.label("fact_id"),
                self.visibility.c.supersedes_fact_id,
                self.visibility.c.inserting_xid,
            ).where(self.visibility.c.id <= maximum).order_by(self.visibility.c.id)
        ))
        visibility_rows = tuple(type("Visibility", (), {
            "fact_id": row.fact_id, "supersedes_fact_id": row.supersedes_fact_id,
            "inserting_xid": row.inserting_xid,
            "is_current": any(fact.id == row.fact_id for fact in facts),
        })() for row in visibility)
        post_hwm_ids = await _safe(self.session.execute(
            select(self.source.c.id).where(self.source.c.id > maximum).order_by(self.source.c.id)
        ))
        post_hwm = tuple(type("PostHwm", (), {"id": value})() for value in post_hwm_ids.scalars())
        return (
            facts, tuple(mappings.scalars()), tuple(projected.scalars()),
            tuple(selections.scalars()), visibility_rows, post_hwm,
        )

    async def get_shadow_run(self, run_id: str, *, lock: bool = False):
        statement = select(HealthProjectionShadowRun).where(
            HealthProjectionShadowRun.run_id == run_id
        )
        result = await _safe(self.session.execute(statement.with_for_update() if lock else statement))
        return result.scalar_one_or_none()

    async def get_shadow_audit(self, operation_id: str):
        result = await _safe(self.session.execute(
            select(HealthProjectionShadowAudit).where(
                HealthProjectionShadowAudit.operation_id == operation_id
            )
        ))
        return result.scalar_one_or_none()

    async def next_shadow_sequence(self, generation_id: int) -> int:
        result = await _safe(self.session.execute(select(func.coalesce(
            func.max(HealthProjectionShadowRun.run_sequence), 0
        )).where(HealthProjectionShadowRun.generation_id == generation_id)))
        return int(result.scalar_one()) + 1

    async def add_shadow_run(self, run) -> None:
        statement = insert(HealthProjectionShadowRun).values(
            run_id=run.run_id,
            generation_id=run.generation_id,
            run_sequence=run.run_sequence,
            status=run.status,
            projection_version=run.projection_version,
            rule_version=run.rule_version,
            high_watermark=run.high_watermark,
            high_watermark_digest=run.high_watermark_digest,
            digest_key_id=run.digest_key_id,
            generation_input_digest=run.generation_input_digest,
            start_operation_id=run.start_operation_id,
            validator_id=run.validator_id,
            lease_epoch=run.lease_epoch,
            lease_expires_at=run.lease_expires_at,
            version=run.version,
        ).returning(HealthProjectionShadowRun.started_at)
        run.started_at = (await _safe(self.session.execute(statement))).scalar_one()

    async def add_shadow_audit(self, audit) -> None:
        self.session.add(audit)
        await _safe(self.session.flush())

    async def passed_shadow_runs(self, generation_id: int):
        result = await _safe(self.session.execute(
            select(HealthProjectionShadowRun).where(
                HealthProjectionShadowRun.generation_id == generation_id,
                HealthProjectionShadowRun.status == "PASSED",
            ).order_by(HealthProjectionShadowRun.run_sequence.desc()).limit(2)
        ))
        return tuple(reversed(tuple(result.scalars())))

    async def heartbeat_shadow(self, *, run_id, validator_id, lease_epoch, expires_at):
        result = await _safe(self.session.execute(update(HealthProjectionShadowRun).where(
            HealthProjectionShadowRun.run_id == run_id,
            HealthProjectionShadowRun.status == "RUNNING",
            HealthProjectionShadowRun.validator_id == validator_id,
            HealthProjectionShadowRun.lease_epoch == lease_epoch,
            HealthProjectionShadowRun.lease_expires_at > func.now(),
        ).values(lease_expires_at=expires_at, version=HealthProjectionShadowRun.version + 1)))
        return result.rowcount == 1
