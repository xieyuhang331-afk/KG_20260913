import asyncio
from datetime import datetime

from sqlalchemy import func, insert, select, text, update

from app.core.sqlalchemy_mapping import map_core_model_classes
from app.modules.system.models import OperationLog, PlatformOrg
from .domain import OrganizationProjectionRow, OrganizationSourceNode, build_organization_projection_row
from .service import OperationPostimage, ProjectionCheckpointConflict, _digest
from .models import (
    OrganizationProjectionCheckpoint, OrganizationProjectionGeneration,
    OrganizationProjectionModel,
)


class ProjectionPersistenceUnavailable(Exception):
    pass


async def _safe(awaitable):
    try:
        return await awaitable
    except asyncio.CancelledError:
        raise
    except Exception:
        raise ProjectionPersistenceUnavailable("Projection persistence is unavailable") from None


class OrganizationProjectionRepository:
    def __init__(self, session):
        map_core_model_classes()
        self.session = session
        self.source = PlatformOrg.__table__

    async def max_source_id(self) -> int:
        result = await _safe(self.session.execute(select(func.coalesce(func.max(self.source.c.id), 0))))
        return int(result.scalar_one())

    async def count_sources(self, max_id: int) -> int:
        result = await _safe(self.session.execute(select(func.count()).select_from(self.source).where(self.source.c.org_type == "county", self.source.c.id <= max_id)))
        return int(result.scalar_one())

    async def list_leaf_ids(self, *, after_id, max_id, limit):
        stmt = select(self.source.c.id).where(self.source.c.org_type == "county", self.source.c.id <= max_id)
        if after_id is not None:
            stmt = stmt.where(self.source.c.id > after_id)
        result = await _safe(self.session.execute(stmt.order_by(self.source.c.id).limit(limit)))
        return tuple(result.scalars())

    async def load_chain(self, leaf_id: int) -> tuple[OrganizationSourceNode, ...]:
        rows = []
        current = leaf_id
        columns = (self.source.c.id, self.source.c.parent_id, self.source.c.org_name, self.source.c.org_code, self.source.c.org_type, self.source.c.status, self.source.c.sort_order, self.source.c.version)
        for _ in range(5):
            result = await _safe(self.session.execute(select(*columns).where(self.source.c.id == current)))
            row = result.mappings().one_or_none()
            if row is None:
                break
            rows.append(OrganizationSourceNode(row["id"], row["parent_id"], row["org_code"], row["org_name"], row["org_type"], row["status"], row["sort_order"], row["version"]))
            if row["parent_id"] is None:
                break
            current = row["parent_id"]
        return tuple(rows)

    async def get_generation(self, generation_id: int, *, lock: bool = False):
        stmt = select(OrganizationProjectionGeneration).where(OrganizationProjectionGeneration.id == generation_id)
        result = await _safe(self.session.execute(stmt.with_for_update() if lock else stmt))
        return result.scalar_one_or_none()

    async def get_checkpoint(self, generation_id: int, *, lock: bool = False):
        stmt = select(OrganizationProjectionCheckpoint).where(OrganizationProjectionCheckpoint.generation_id == generation_id)
        result = await _safe(self.session.execute(stmt.with_for_update() if lock else stmt))
        return result.scalar_one_or_none()

    async def add_generation(self, generation, checkpoint) -> None:
        result = await _safe(self.session.execute(
            insert(OrganizationProjectionGeneration).values(
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
            ).returning(OrganizationProjectionGeneration.id)
        ))
        generation.id = int(result.scalar_one())
        generation.version = 1
        checkpoint.generation_id = generation.id
        checkpoint.version = 1
        await _safe(self.session.execute(
            insert(OrganizationProjectionCheckpoint).inline().values(
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

    async def add_rows(self, generation_id: int, key_id: str, rows: tuple[OrganizationProjectionRow, ...]) -> None:
        for row in rows:
            self.session.add(OrganizationProjectionModel(
                generation_id=generation_id, organization_id=row.organization_id,
                parent_id=row.parent_id, org_code=row.org_code, org_name=row.org_name,
                org_type=row.org_type, status=row.status, sort_order=row.sort_order,
                source_version=row.source_version, path_ids=list(row.path_ids),
                path_codes=list(row.path_codes), compatibility_mode=row.compatibility_mode,
                scope_eligible=row.scope_eligible, row_digest=row.row_digest,
                digest_key_id=key_id,
            ))
        await _safe(self.session.flush())

    async def heartbeat(self, *, generation_id, builder_id, lease_epoch, expires_at, operation_id):
        result = await _safe(self.session.execute(update(OrganizationProjectionGeneration).where(
            OrganizationProjectionGeneration.id == generation_id,
            OrganizationProjectionGeneration.status == "BUILDING",
            OrganizationProjectionGeneration.builder_id == builder_id,
            OrganizationProjectionGeneration.lease_epoch == lease_epoch,
            OrganizationProjectionGeneration.lease_expires_at > func.now(),
        ).values(lease_expires_at=expires_at, updated_at=func.now(), version=OrganizationProjectionGeneration.version + 1)))
        return result.rowcount == 1

    async def add_audit(self, *, generation_id: int, action: str, operation_id: str, payload: dict) -> None:
        statement = insert(OperationLog).inline().values(
            operator_id=None,
            module="basic_projection_builder",
            object_type="organization_projection_generation",
            object_id=generation_id,
            action=action,
            payload={"operation_id": operation_id, **payload},
        )
        await _safe(self.session.execute(statement))

    async def audit_payload(self, operation_id: str):
        record = await self.audit_record(operation_id)
        return None if record is None else record[1]

    async def audit_record(self, operation_id: str):
        result = await _safe(self.session.execute(text("SELECT object_id,payload FROM public.organization_projection_operation_audit WHERE payload->>'operation_id'=:operation_id").bindparams(operation_id=operation_id)))
        row = result.one_or_none()
        return None if row is None else (int(row.object_id), row.payload)

    async def has_newer_complete_generation(self, generation) -> bool:
        result = await _safe(self.session.execute(select(func.count()).select_from(OrganizationProjectionGeneration).where(
            OrganizationProjectionGeneration.projection_version == generation.projection_version,
            OrganizationProjectionGeneration.generation_no > generation.generation_no,
            OrganizationProjectionGeneration.status == "BUILD_COMPLETE",
        )))
        return int(result.scalar_one()) > 0

    async def count_rows(self, generation_id: int) -> int:
        result = await _safe(self.session.execute(select(func.count()).select_from(OrganizationProjectionModel).where(OrganizationProjectionModel.generation_id == generation_id)))
        return int(result.scalar_one())

    async def has_remaining_sources(self, after_id, max_id: int) -> bool:
        return bool(await self.list_leaf_ids(after_id=after_id, max_id=max_id, limit=1))

    async def validate_completion(self, generation_id, checkpoint, key_id, stored_key, high_watermark) -> str:
        ids = await self.list_leaf_ids(after_id=None, max_id=high_watermark["max_organization_id"], limit=max(checkpoint.processed_count + 1, 1))
        result = await _safe(self.session.execute(select(OrganizationProjectionModel).where(OrganizationProjectionModel.generation_id == generation_id).order_by(OrganizationProjectionModel.organization_id)))
        persisted = tuple(result.scalars())
        expected_rows = []
        for source_id in ids:
            chain = await self.load_chain(source_id)
            expected_rows.append(
                build_organization_projection_row(
                    chain=chain,
                    digest_key=stored_key,
                )
            )
        expected = tuple(expected_rows)
        actual = tuple((row.organization_id, row.parent_id, row.org_code, row.org_name, row.org_type, row.status, row.sort_order, row.source_version, tuple(row.path_ids), tuple(row.path_codes), row.compatibility_mode, row.scope_eligible, row.row_digest, row.digest_key_id) for row in persisted)
        wanted = tuple((row.organization_id, row.parent_id, row.org_code, row.org_name, row.org_type, row.status, row.sort_order, row.source_version, row.path_ids, row.path_codes, row.compatibility_mode, row.scope_eligible, row.row_digest, key_id) for row in expected)
        if ids != tuple(row.organization_id for row in expected) or actual != wanted or len(ids) != checkpoint.projected_count or checkpoint.remaining_count != 0:
            raise ProjectionCheckpointConflict("Projection checkpoint conflicts")
        return _digest({"generation_id": generation_id, "rows": actual, "digest_key_id": key_id})

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
        return OperationPostimage(generation.status, generation.version, generation.high_watermark, generation.digest_key_id, checkpoint.checkpoint_digest, checkpoint.version, checkpoint.processed_count, checkpoint.projected_count, checkpoint.skipped_count, checkpoint.remaining_count, rows, 0, audit.get("completion_evidence", ""), generation.builder_id, generation.lease_epoch, generation.lease_expires_at, generation.completed_at, generation.failure_code, checkpoint.last_operation_id)
