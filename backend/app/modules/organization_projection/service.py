import asyncio
import base64
import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from sqlalchemy import text

from .domain import build_organization_projection_row
from .models import OrganizationProjectionCheckpoint, OrganizationProjectionGeneration


class ProjectionError(Exception): pass
class ProjectionCheckpointConflict(ProjectionError): pass
class ProjectionDigestKeyUnavailable(ProjectionError): pass
class ProjectionLeaseConflict(ProjectionError): pass
class ProjectionCommitOutcomeUnknown(ProjectionError): pass
class ProjectionUnavailable(ProjectionError): pass


class BuildState(str, Enum):
    BUILDING = "BUILDING"
    BUILD_COMPLETE = "BUILD_COMPLETE"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"


class ConfirmationResult(str, Enum):
    COMMITTED = "COMMITTED"
    ROLLED_BACK = "ROLLED_BACK"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class OperationPostimage:
    status: str
    generation_version: int
    high_watermark: dict
    digest_key_id: str
    checkpoint_digest: str
    checkpoint_version: int
    processed_count: int
    projected_count: int
    skipped_count: int
    remaining_count: int
    row_count: int
    selection_count: int
    completion_evidence: str
    builder_id: str | None = None
    lease_epoch: int = 0
    lease_expires_at: datetime | None = None
    completed_at: datetime | None = None
    failure_code: str | None = None
    last_operation_id: str | None = None


@dataclass(frozen=True, slots=True)
class BuildCheckpoint:
    last_source_id: int | None
    processed_count: int
    projected_count: int
    skipped_count: int
    remaining_count: int

    def advance(self, *, source_ids, projected):
        ids = tuple(source_ids)
        invalid = (
            not ids or any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in ids)
            or tuple(sorted(set(ids))) != ids
            or (self.last_source_id is not None and ids[0] <= self.last_source_id)
            or projected < 0 or projected > len(ids) or self.remaining_count < len(ids)
        )
        if invalid:
            raise ProjectionCheckpointConflict("Projection checkpoint conflicts")
        return replace(self, last_source_id=ids[-1], processed_count=self.processed_count + len(ids), projected_count=self.projected_count + projected, skipped_count=self.skipped_count + len(ids) - projected, remaining_count=self.remaining_count - len(ids))

    def complete(self):
        if self.remaining_count != 0 or self.processed_count != self.projected_count + self.skipped_count:
            raise ProjectionCheckpointConflict("Projection checkpoint conflicts")
        return self


class ProjectionDigestKeyring:
    def __init__(self, *, current_key_id, keys):
        if not current_key_id or current_key_id not in keys or any(not isinstance(key, str) or not key or not isinstance(value, bytes) or len(value) != 32 for key, value in keys.items()):
            raise ProjectionDigestKeyUnavailable("Projection digest key is unavailable")
        self.current_key_id = current_key_id
        self._keys = dict(keys)

    def stored(self, key_id):
        try: return self._keys[key_id]
        except KeyError: raise ProjectionDigestKeyUnavailable("Projection digest key is unavailable") from None

    @classmethod
    def from_json(cls, current_key_id, raw):
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result: raise ValueError
                result[key] = value
            return result
        try:
            values = json.loads(raw, object_pairs_hook=pairs)
            keys = {key: base64.b64decode(value, validate=True) for key, value in values.items()}
            return cls(current_key_id=current_key_id, keys=keys)
        except Exception:
            raise ProjectionDigestKeyUnavailable("Projection digest key is unavailable") from None


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class ProjectionHeartbeat:
    def __init__(self, uow_factory): self._uow_factory = uow_factory
    async def beat(self, *, generation_id, builder_id, lease_epoch, operation_id):
        async with self._uow_factory() as uow:
            generation = await uow.repository.get_generation(generation_id)
            preimage = {"operation": "heartbeat", "generation_id": generation_id, "builder_id": builder_id, "lease_epoch": lease_epoch}
            replay = await _replay_operation(uow.repository, operation_id, preimage)
            if replay is not None: return replay
            expires_at = datetime.now(UTC) + timedelta(seconds=60)
            ok = await uow.repository.heartbeat(generation_id=generation_id, builder_id=builder_id, lease_epoch=lease_epoch, expires_at=expires_at, operation_id=operation_id)
            if not ok: raise ProjectionLeaseConflict("Projection lease conflicts")
            postimage = {"generation_id": generation_id, "generation_version": generation.version + 1, "builder_id": builder_id, "lease_epoch": lease_epoch, "lease_expires_at": expires_at.isoformat()}
            await uow.repository.add_audit(generation_id=generation_id, action="projection_heartbeat", operation_id=operation_id, payload={"preimage": preimage, "postimage": postimage})
            await uow.commit()
            return postimage


class ProjectionUnitOfWork:
    def __init__(self, session_factory, repository_type, *, isolation_level="REPEATABLE READ"):
        self.factory, self.repository_type, self.isolation_level = session_factory, repository_type, isolation_level
        self.session = None
        self.final = False
    async def __aenter__(self):
        self.session = self.factory()
        await self.session.connection(execution_options={"isolation_level": self.isolation_level})
        self.repository = self.repository_type(self.session)
        return self
    async def __aexit__(self, *_):
        try:
            if not self.final: await self.session.rollback()
        finally: await self.session.close()
    async def commit(self):
        try: await self.session.commit()
        except asyncio.CancelledError: raise
        except Exception: raise ProjectionCommitOutcomeUnknown("Projection commit outcome is unknown") from None
        self.final = True


class ProjectionSessionLock:
    def __init__(self, connection_factory, lock_key: int):
        self.connection_factory, self.lock_key, self.connection = connection_factory, lock_key, None
    async def __aenter__(self):
        self.connection = await self.connection_factory()
        try:
            result = await self.connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": self.lock_key})
            acquired = bool(result.scalar_one())
            if not acquired: raise ProjectionLeaseConflict("Projection lease conflicts")
            return self
        except asyncio.CancelledError:
            await self._finish_cleanup(); raise
        except ProjectionLeaseConflict:
            await self.connection.close(); self.connection = None; raise
        except Exception:
            await self._invalidate(); raise ProjectionUnavailable("Projection lock is unavailable") from None
    async def __aexit__(self, *_):
        if self.connection is None: return
        try:
            result = await self.connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": self.lock_key})
            released = bool(result.scalar_one())
            if not released: raise ProjectionUnavailable("Projection lock is unavailable")
            await self.connection.close(); self.connection = None
        except asyncio.CancelledError:
            await self._finish_cleanup(); raise
        except Exception:
            await self._invalidate(); raise ProjectionUnavailable("Projection lock is unavailable") from None
    async def _invalidate(self):
        connection, self.connection = self.connection, None
        if connection is not None:
            try: await connection.invalidate()
            except Exception:
                try: await connection.close()
                except Exception: pass

    async def _finish_cleanup(self):
        task = asyncio.create_task(self._invalidate())
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        await task


def _builder_lock_key(domain: str, projection_version: int = 1) -> int:
    value = f"basic-projection:{domain}:{projection_version}".encode("ascii")
    return int.from_bytes(hashlib.sha256(value).digest()[:8], "big", signed=True)


def _validate_checkpoint(generation, checkpoint) -> None:
    if checkpoint is None:
        raise ProjectionCheckpointConflict("Projection checkpoint conflicts")
    if checkpoint.processed_count != checkpoint.projected_count + checkpoint.skipped_count:
        raise ProjectionCheckpointConflict("Projection checkpoint conflicts")
    expected = _digest({
        "last_source_id": checkpoint.last_source_id,
        "processed_count": checkpoint.processed_count,
        "remaining_count": checkpoint.remaining_count,
    })
    if checkpoint.checkpoint_digest != expected:
        raise ProjectionCheckpointConflict("Projection checkpoint conflicts")


def _require_live_lease(generation, *, builder_id, lease_epoch, now=None) -> None:
    current = now or datetime.now(UTC)
    if (
        generation is None
        or generation.status != "BUILDING"
        or generation.builder_id != builder_id
        or generation.lease_epoch != lease_epoch
        or generation.lease_expires_at is None
        or generation.lease_expires_at <= current
    ):
        raise ProjectionLeaseConflict("Projection lease conflicts")


async def _replay_operation(repository, operation_id, preimage):
    existing = await repository.audit_payload(operation_id)
    if existing is None:
        return None
    if existing.get("preimage") != preimage:
        raise ProjectionCheckpointConflict("Projection operation conflicts")
    return existing.get("postimage", {})


def _checkpoint_postimage(checkpoint):
    return {
        "last_source_id": checkpoint.last_source_id,
        "processed_count": checkpoint.processed_count,
        "projected_count": checkpoint.projected_count,
        "skipped_count": checkpoint.skipped_count,
        "remaining_count": checkpoint.remaining_count,
        "checkpoint_digest": checkpoint.checkpoint_digest,
        "checkpoint_version": checkpoint.version,
    }


async def _replay_before_heartbeat(confirmation_uow_factory, operation_id, preimage):
    async with confirmation_uow_factory() as uow:
        return await _replay_operation(uow.repository, operation_id, preimage)


class OrganizationProjectionBuilder:
    def __init__(self, uow_factory, confirmation_uow_factory, keyring: ProjectionDigestKeyring, lock_connection_factory=None):
        self.uow_factory, self.confirmation_uow_factory, self.keyring = uow_factory, confirmation_uow_factory, keyring
        self.lock_connection_factory = lock_connection_factory
        self.heartbeat = ProjectionHeartbeat(uow_factory)

    def _session_lock(self):
        if self.lock_connection_factory is None:
            raise ProjectionUnavailable("Projection lock is unavailable")
        return ProjectionSessionLock(self.lock_connection_factory, _builder_lock_key("organization"))

    async def start(self, *, generation_no: int, builder_id: str, operation_id: str) -> int:
        async with self._session_lock(), self.uow_factory() as uow:
            preimage = {"operation": "start", "generation_no": generation_no, "builder_id": builder_id, "projection_version": 1}
            replay = await _replay_operation(uow.repository, operation_id, preimage)
            if replay is not None:
                return int(replay["generation_id"])
            max_id = await uow.repository.max_source_id()
            remaining = await uow.repository.count_sources(max_id)
            hwm = {"max_organization_id": max_id}
            generation = OrganizationProjectionGeneration(projection_version=1, generation_no=generation_no, status="BUILDING", high_watermark=hwm, digest_key_id=self.keyring.current_key_id, input_digest=_digest(hwm), start_operation_id=operation_id, builder_id=builder_id, lease_epoch=0, lease_expires_at=datetime.now(UTC) + timedelta(seconds=60))
            checkpoint = OrganizationProjectionCheckpoint(last_source_id=None, processed_count=0, projected_count=0, skipped_count=0, remaining_count=remaining, checkpoint_digest=_digest({"last_source_id": None, "processed_count": 0, "remaining_count": remaining}), version=1)
            await uow.repository.add_generation(generation, checkpoint)
            postimage = {"generation_id": generation.id, "generation_version": generation.version, "checkpoint_version": checkpoint.version, "high_watermark": hwm, "digest_key_id": generation.digest_key_id}
            await uow.repository.add_audit(generation_id=generation.id, action="projection_generation_started", operation_id=operation_id, payload={"preimage": preimage, "postimage": postimage})
            await uow.commit()
            return generation.id

    async def build_page(self, *, generation_id: int, builder_id: str, lease_epoch: int, expected_checkpoint_digest: str, operation_id: str, heartbeat_operation_id: str, page_size: int = 100) -> BuildCheckpoint:
        preimage = {"operation": "build_page", "generation_id": generation_id, "builder_id": builder_id, "lease_epoch": lease_epoch, "checkpoint_digest": expected_checkpoint_digest, "page_size": page_size}
        replay = await _replay_before_heartbeat(self.confirmation_uow_factory, operation_id, preimage)
        if replay is not None:
            return BuildCheckpoint(replay["last_source_id"], replay["processed_count"], replay["projected_count"], replay["skipped_count"], replay["remaining_count"])
        await self.heartbeat.beat(generation_id=generation_id, builder_id=builder_id, lease_epoch=lease_epoch, operation_id=heartbeat_operation_id)
        async with self._session_lock(), self.uow_factory() as uow:
            generation = await uow.repository.get_generation(generation_id, lock=True)
            checkpoint = await uow.repository.get_checkpoint(generation_id, lock=True)
            replay = await _replay_operation(uow.repository, operation_id, preimage)
            if replay is not None:
                return BuildCheckpoint(replay["last_source_id"], replay["processed_count"], replay["projected_count"], replay["skipped_count"], replay["remaining_count"])
            _require_live_lease(generation, builder_id=builder_id, lease_epoch=lease_epoch)
            if checkpoint is None or checkpoint.checkpoint_digest != expected_checkpoint_digest: raise ProjectionCheckpointConflict("Projection checkpoint conflicts")
            key = self.keyring.stored(generation.digest_key_id)
            ids = await uow.repository.list_leaf_ids(after_id=checkpoint.last_source_id, max_id=generation.high_watermark["max_organization_id"], limit=page_size)
            state = BuildCheckpoint(checkpoint.last_source_id, checkpoint.processed_count, checkpoint.projected_count, checkpoint.skipped_count, checkpoint.remaining_count)
            if not ids:
                return state.complete()
            rows = []
            for source_id in ids:
                chain = await uow.repository.load_chain(source_id)
                rows.append(build_organization_projection_row(chain=chain, digest_key=key))
            rows = tuple(rows)
            await uow.repository.add_rows(generation_id, generation.digest_key_id, rows)
            state = state.advance(source_ids=ids, projected=len(rows))
            for name in ("last_source_id", "processed_count", "projected_count", "skipped_count", "remaining_count"):
                setattr(checkpoint, name, getattr(state, name))
            checkpoint.last_operation_id = operation_id
            checkpoint.version += 1
            checkpoint.checkpoint_digest = _digest({"last_source_id": state.last_source_id, "processed_count": state.processed_count, "remaining_count": state.remaining_count})
            generation.version += 1
            generation.updated_at = datetime.now(UTC)
            await uow.repository.add_audit(generation_id=generation_id, action="projection_batch_committed", operation_id=operation_id, payload={"preimage": preimage, "postimage": _checkpoint_postimage(checkpoint)})
            await uow.commit()
            return state

    async def complete(self, *, generation_id: int, builder_id: str, lease_epoch: int, expected_generation_version: int, expected_checkpoint_digest: str, operation_id: str) -> None:
        async with self._session_lock(), self.uow_factory() as uow:
            generation = await uow.repository.get_generation(generation_id, lock=True)
            checkpoint = await uow.repository.get_checkpoint(generation_id, lock=True)
            preimage = {"operation": "complete", "generation_id": generation_id, "builder_id": builder_id, "lease_epoch": lease_epoch, "checkpoint_digest": expected_checkpoint_digest, "generation_version": expected_generation_version}
            if await _replay_operation(uow.repository, operation_id, preimage) is not None:
                return
            _require_live_lease(generation, builder_id=builder_id, lease_epoch=lease_epoch)
            if checkpoint is None or checkpoint.checkpoint_digest != expected_checkpoint_digest or generation.version != expected_generation_version: raise ProjectionCheckpointConflict("Projection checkpoint conflicts")
            _validate_checkpoint(generation, checkpoint)
            if await uow.repository.has_remaining_sources(checkpoint.last_source_id, generation.high_watermark["max_organization_id"]):
                raise ProjectionCheckpointConflict("Projection checkpoint conflicts")
            completion_evidence = await uow.repository.validate_completion(generation_id, checkpoint, generation.digest_key_id, self.keyring.stored(generation.digest_key_id), generation.high_watermark)
            generation.status = "BUILD_COMPLETE"; generation.builder_id = None; generation.lease_expires_at = None; generation.completed_at = datetime.now(UTC); generation.version += 1
            postimage = {"status": generation.status, "generation_version": generation.version, "checkpoint_digest": checkpoint.checkpoint_digest, "checkpoint_version": checkpoint.version, "completion_evidence": completion_evidence}
            await uow.repository.add_audit(generation_id=generation_id, action="projection_generation_build_complete", operation_id=operation_id, payload={"preimage": preimage, "postimage": postimage, "completion_evidence": completion_evidence})
            await uow.commit()

    async def takeover(self, *, generation_id: int, builder_id: str, expected_lease_epoch: int, expected_checkpoint_digest: str, operation_id: str) -> int:
        async with self._session_lock(), self.uow_factory() as uow:
            generation = await uow.repository.get_generation(generation_id, lock=True)
            checkpoint = await uow.repository.get_checkpoint(generation_id, lock=True)
            preimage = {"operation": "takeover", "generation_id": generation_id, "builder_id": builder_id, "lease_epoch": expected_lease_epoch, "checkpoint_digest": expected_checkpoint_digest}
            replay = await _replay_operation(uow.repository, operation_id, preimage)
            if replay is not None:
                return int(replay["lease_epoch"])
            if generation is None or generation.status != "BUILDING" or generation.lease_epoch != expected_lease_epoch or checkpoint is None or checkpoint.checkpoint_digest != expected_checkpoint_digest or generation.lease_expires_at >= datetime.now(UTC):
                raise ProjectionLeaseConflict("Projection lease conflicts")
            _validate_checkpoint(generation, checkpoint)
            self.keyring.stored(generation.digest_key_id)
            generation.builder_id = builder_id; generation.lease_epoch += 1; generation.lease_expires_at = datetime.now(UTC) + timedelta(seconds=60); generation.version += 1
            await uow.repository.add_audit(generation_id=generation_id, action="projection_generation_takeover", operation_id=operation_id, payload={"preimage": preimage, "postimage": {"lease_epoch": generation.lease_epoch, "builder_id": builder_id, "generation_version": generation.version}})
            await uow.commit()
            return generation.lease_epoch

    async def fail(self, *, generation_id: int, builder_id: str, lease_epoch: int, expected_generation_version: int, failure_code: str, operation_id: str) -> None:
        async with self._session_lock(), self.uow_factory() as uow:
            generation = await uow.repository.get_generation(generation_id, lock=True); checkpoint = await uow.repository.get_checkpoint(generation_id, lock=True)
            preimage = {"operation": "fail", "generation_id": generation_id, "builder_id": builder_id, "lease_epoch": lease_epoch, "failure_code": failure_code, "generation_version": expected_generation_version}
            if await _replay_operation(uow.repository, operation_id, preimage) is not None: return
            _require_live_lease(generation, builder_id=builder_id, lease_epoch=lease_epoch)
            if generation.version != expected_generation_version: raise ProjectionCheckpointConflict("Projection operation conflicts")
            if failure_code not in {"PROJECTION_SOURCE_INVALID", "PROJECTION_DIGEST_KEY_UNAVAILABLE", "PROJECTION_CHECKPOINT_CONFLICT", "PROJECTION_LEASE_CONFLICT", "PROJECTION_UNAVAILABLE"}: raise ProjectionCheckpointConflict("Projection operation conflicts")
            generation.status = "FAILED"; generation.failure_code = failure_code; generation.completed_at = datetime.now(UTC); generation.builder_id = None; generation.lease_expires_at = None; generation.version += 1
            await uow.repository.add_audit(generation_id=generation_id, action="projection_generation_failed", operation_id=operation_id, payload={"preimage": preimage, "postimage": {"status": "FAILED", "failure_code": failure_code, "generation_version": generation.version}}); await uow.commit()

    async def supersede(self, *, generation_id: int, expected_version: int, operation_id: str) -> None:
        async with self._session_lock(), self.uow_factory() as uow:
            generation = await uow.repository.get_generation(generation_id, lock=True)
            preimage = {"operation": "supersede", "generation_id": generation_id, "generation_version": expected_version, "status": "BUILD_COMPLETE"}
            if await _replay_operation(uow.repository, operation_id, preimage) is not None: return
            if generation is None or generation.status != "BUILD_COMPLETE" or generation.version != expected_version or not await uow.repository.has_newer_complete_generation(generation): raise ProjectionCheckpointConflict("Projection operation conflicts")
            generation.status = "SUPERSEDED"; generation.version += 1; generation.updated_at = datetime.now(UTC)
            await uow.repository.add_audit(generation_id=generation_id, action="projection_generation_superseded", operation_id=operation_id, payload={"preimage": preimage, "postimage": {"status": "SUPERSEDED", "generation_version": generation.version}}); await uow.commit()

    async def confirm_operation(self, *, generation_id: int | None = None, operation_id: str, expected_preimage: dict, expected_postimage: OperationPostimage) -> ConfirmationResult:
        try:
            async with self.confirmation_uow_factory() as uow:
                audit_record = await uow.repository.audit_record(operation_id)
                if generation_id is None and audit_record is not None:
                    generation_id = audit_record[0]
                generation = None if generation_id is None else await uow.repository.get_generation(generation_id); checkpoint = None if generation_id is None else await uow.repository.get_checkpoint(generation_id); audit = None if audit_record is None else audit_record[1]
                if audit is None:
                    actual_preimage = await uow.repository.operation_preimage(generation, checkpoint, expected_preimage)
                    return ConfirmationResult.ROLLED_BACK if actual_preimage == expected_preimage else ConfirmationResult.UNKNOWN
                if generation is None or checkpoint is None or audit.get("preimage") != expected_preimage: return ConfirmationResult.UNKNOWN
                actual = await uow.repository.operation_postimage(generation, checkpoint, audit)
                if actual == expected_postimage: return ConfirmationResult.COMMITTED
                return ConfirmationResult.UNKNOWN
        except asyncio.CancelledError: raise
        except Exception: return ConfirmationResult.UNKNOWN
