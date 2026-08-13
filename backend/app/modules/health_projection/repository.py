import asyncio

from sqlalchemy import BigInteger, column, func, insert, select, table, text, update

from app.core.sqlalchemy_mapping import map_core_model_classes
from app.modules.health_fact.models import CanonicalHealthFactOrmModel
from app.modules.system.models import OperationLog
from .domain import HealthCurrentFact, HealthProjectionFactRow, HealthWindowSelection, build_health_projection_rows
from app.modules.organization_projection.service import OperationPostimage, ProjectionCheckpointConflict, _digest
from .models import (
    HealthProjectionCheckpoint, HealthProjectionFactModel,
    HealthProjectionGeneration, HealthProjectionWindowSelectionModel,
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

    async def get_generation(self, generation_id: int, *, lock: bool = False):
        stmt = select(HealthProjectionGeneration).where(HealthProjectionGeneration.id == generation_id)
        result = await _safe(self.session.execute(stmt.with_for_update() if lock else stmt))
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
