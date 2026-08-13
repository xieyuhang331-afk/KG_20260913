import hashlib
from datetime import UTC, datetime, timedelta

from app.modules.organization_projection.service import (
    BuildCheckpoint, BuildState, ConfirmationResult, ProjectionCheckpointConflict,
    ProjectionCommitOutcomeUnknown, ProjectionDigestKeyring,
    ProjectionDigestKeyUnavailable, ProjectionHeartbeat, ProjectionLeaseConflict,
    ProjectionUnitOfWork, ProjectionUnavailable, ProjectionSessionLock,
    _builder_lock_key, _checkpoint_postimage, _digest, _replay_operation,
    _replay_before_heartbeat, _require_live_lease, _validate_checkpoint,
)
from .domain import build_health_projection_rows
from .models import HealthProjectionCheckpoint, HealthProjectionGeneration


def _window_lock_key(generation_id, subject_id, indicator, business_day):
    value = f"{generation_id}:{subject_id}:{indicator}:{business_day.isoformat()}".encode()
    return int.from_bytes(hashlib.sha256(value).digest()[:8], "big", signed=True)


class HealthProjectionBuilder:
    def __init__(self, uow_factory, confirmation_uow_factory, keyring: ProjectionDigestKeyring, lock_connection_factory=None):
        self.uow_factory, self.confirmation_uow_factory, self.keyring = uow_factory, confirmation_uow_factory, keyring
        self.lock_connection_factory = lock_connection_factory
        self.heartbeat = ProjectionHeartbeat(uow_factory)

    def _session_lock(self):
        if self.lock_connection_factory is None:
            raise ProjectionUnavailable("Projection lock is unavailable")
        return ProjectionSessionLock(self.lock_connection_factory, _builder_lock_key("health"))

    async def start(self, *, generation_no, builder_id, operation_id):
        async with self._session_lock(), self.uow_factory() as uow:
            preimage = {"operation": "start", "generation_no": generation_no, "builder_id": builder_id, "projection_version": 1}
            replay = await _replay_operation(uow.repository, operation_id, preimage)
            if replay is not None: return int(replay["generation_id"])
            source_snapshot = await uow.repository.export_source_snapshot()
            max_id = await uow.repository.max_source_id(source_snapshot=source_snapshot); remaining = await uow.repository.count_sources(max_id, source_snapshot=source_snapshot)
            hwm = {"max_fact_id": max_id, "source_snapshot": source_snapshot}
            generation = HealthProjectionGeneration(projection_version=1, generation_no=generation_no, status="BUILDING", high_watermark=hwm, digest_key_id=self.keyring.current_key_id, input_digest=_digest(hwm), start_operation_id=operation_id, builder_id=builder_id, lease_epoch=0, lease_expires_at=datetime.now(UTC) + timedelta(seconds=60))
            checkpoint = HealthProjectionCheckpoint(last_source_id=None, processed_count=0, projected_count=0, skipped_count=0, remaining_count=remaining, checkpoint_digest=_digest({"last_source_id": None, "processed_count": 0, "remaining_count": remaining}), version=1)
            await uow.repository.add_generation(generation, checkpoint)
            postimage = {"generation_id": generation.id, "generation_version": generation.version, "checkpoint_version": checkpoint.version, "high_watermark": hwm, "digest_key_id": generation.digest_key_id}
            await uow.repository.add_audit(generation_id=generation.id, action="projection_generation_started", operation_id=operation_id, payload={"preimage": preimage, "postimage": postimage})
            await uow.commit(); return generation.id

    async def build_page(self, *, generation_id, builder_id, lease_epoch, expected_checkpoint_digest, operation_id, heartbeat_operation_id, page_size=100):
        preimage = {"operation": "build_page", "generation_id": generation_id, "builder_id": builder_id, "lease_epoch": lease_epoch, "checkpoint_digest": expected_checkpoint_digest, "page_size": page_size}
        replay = await _replay_before_heartbeat(self.confirmation_uow_factory, operation_id, preimage)
        if replay is not None: return BuildCheckpoint(replay["last_source_id"], replay["processed_count"], replay["projected_count"], replay["skipped_count"], replay["remaining_count"])
        await self.heartbeat.beat(generation_id=generation_id, builder_id=builder_id, lease_epoch=lease_epoch, operation_id=heartbeat_operation_id)
        async with self._session_lock(), self.uow_factory() as uow:
            generation = await uow.repository.get_generation(generation_id, lock=True); checkpoint = await uow.repository.get_checkpoint(generation_id, lock=True)
            replay = await _replay_operation(uow.repository, operation_id, preimage)
            if replay is not None: return BuildCheckpoint(replay["last_source_id"], replay["processed_count"], replay["projected_count"], replay["skipped_count"], replay["remaining_count"])
            _require_live_lease(generation, builder_id=builder_id, lease_epoch=lease_epoch)
            if checkpoint is None or checkpoint.checkpoint_digest != expected_checkpoint_digest: raise ProjectionCheckpointConflict("Projection checkpoint conflicts")
            key = self.keyring.stored(generation.digest_key_id)
            ids = await uow.repository.list_source_ids(after_id=checkpoint.last_source_id, max_id=generation.high_watermark["max_fact_id"], limit=page_size, source_snapshot=generation.high_watermark["source_snapshot"])
            state = BuildCheckpoint(checkpoint.last_source_id, checkpoint.processed_count, checkpoint.projected_count, checkpoint.skipped_count, checkpoint.remaining_count)
            if not ids: return state.complete()
            facts = await uow.repository.load_facts(ids); rows, _ = build_health_projection_rows(facts=facts, digest_key=key)
            await uow.repository.add_facts(generation_id, generation.digest_key_id, tuple(rows))
            state = state.advance(source_ids=ids, projected=len(rows))
            for name in ("last_source_id", "processed_count", "projected_count", "skipped_count", "remaining_count"): setattr(checkpoint, name, getattr(state, name))
            checkpoint.last_operation_id = operation_id; checkpoint.version += 1; checkpoint.checkpoint_digest = _digest({"last_source_id": state.last_source_id, "processed_count": state.processed_count, "remaining_count": state.remaining_count})
            generation.version += 1; generation.updated_at = datetime.now(UTC)
            await uow.repository.add_audit(generation_id=generation_id, action="projection_batch_committed", operation_id=operation_id, payload={"preimage": preimage, "postimage": _checkpoint_postimage(checkpoint)})
            await uow.commit(); return state

    async def complete(self, *, generation_id, builder_id, lease_epoch, expected_generation_version, expected_checkpoint_digest, operation_id):
        async with self._session_lock(), self.uow_factory() as uow:
            generation = await uow.repository.get_generation(generation_id, lock=True); checkpoint = await uow.repository.get_checkpoint(generation_id, lock=True)
            preimage = {"operation": "complete", "generation_id": generation_id, "builder_id": builder_id, "lease_epoch": lease_epoch, "checkpoint_digest": expected_checkpoint_digest, "generation_version": expected_generation_version}
            if await _replay_operation(uow.repository, operation_id, preimage) is not None: return
            _require_live_lease(generation, builder_id=builder_id, lease_epoch=lease_epoch)
            if checkpoint is None or checkpoint.checkpoint_digest != expected_checkpoint_digest or generation.version != expected_generation_version: raise ProjectionCheckpointConflict("Projection checkpoint conflicts")
            _validate_checkpoint(generation, checkpoint)
            if await uow.repository.has_remaining_sources(checkpoint.last_source_id, generation.high_watermark["max_fact_id"], generation.high_watermark["source_snapshot"]): raise ProjectionCheckpointConflict("Projection checkpoint conflicts")
            key = self.keyring.stored(generation.digest_key_id)
            selections = []
            for subject_id, indicator, business_day in await uow.repository.list_projected_windows(generation_id):
                await uow.repository.acquire_window_lock(_window_lock_key(generation_id, subject_id, indicator, business_day))
                window = await uow.repository.load_projected_window(generation_id, subject_id, indicator, business_day)
                _, selected = build_health_projection_rows(facts=window, digest_key=key); selections.extend(selected)
            await uow.repository.add_selections(generation_id, generation.digest_key_id, tuple(selections))
            completion_evidence = await uow.repository.validate_completion(generation_id, checkpoint, generation.digest_key_id, key, generation.high_watermark)
            generation.status = "BUILD_COMPLETE"; generation.builder_id = None; generation.lease_expires_at = None; generation.completed_at = datetime.now(UTC); generation.version += 1
            postimage = {"status": generation.status, "generation_version": generation.version, "checkpoint_digest": checkpoint.checkpoint_digest, "checkpoint_version": checkpoint.version, "completion_evidence": completion_evidence}
            await uow.repository.add_audit(generation_id=generation_id, action="projection_generation_build_complete", operation_id=operation_id, payload={"preimage": preimage, "postimage": postimage, "completion_evidence": completion_evidence})
            await uow.commit()

    async def takeover(self, *, generation_id, builder_id, expected_lease_epoch, expected_checkpoint_digest, operation_id):
        async with self._session_lock(), self.uow_factory() as uow:
            generation = await uow.repository.get_generation(generation_id, lock=True)
            checkpoint = await uow.repository.get_checkpoint(generation_id, lock=True)
            preimage = {"operation": "takeover", "generation_id": generation_id, "builder_id": builder_id, "lease_epoch": expected_lease_epoch, "checkpoint_digest": expected_checkpoint_digest}
            replay = await _replay_operation(uow.repository, operation_id, preimage)
            if replay is not None: return int(replay["lease_epoch"])
            if generation is None or generation.status != "BUILDING" or generation.lease_epoch != expected_lease_epoch or checkpoint is None or checkpoint.checkpoint_digest != expected_checkpoint_digest or generation.lease_expires_at >= datetime.now(UTC): raise ProjectionLeaseConflict("Projection lease conflicts")
            _validate_checkpoint(generation, checkpoint)
            self.keyring.stored(generation.digest_key_id)
            generation.builder_id = builder_id; generation.lease_epoch += 1; generation.lease_expires_at = datetime.now(UTC) + timedelta(seconds=60); generation.version += 1
            await uow.repository.add_audit(generation_id=generation_id, action="projection_generation_takeover", operation_id=operation_id, payload={"preimage": preimage, "postimage": {"lease_epoch": generation.lease_epoch, "builder_id": builder_id, "generation_version": generation.version}})
            await uow.commit(); return generation.lease_epoch

    async def fail(self, *, generation_id, builder_id, lease_epoch, expected_generation_version, failure_code, operation_id):
        async with self._session_lock(), self.uow_factory() as uow:
            generation = await uow.repository.get_generation(generation_id, lock=True); checkpoint = await uow.repository.get_checkpoint(generation_id, lock=True)
            preimage = {"operation": "fail", "generation_id": generation_id, "builder_id": builder_id, "lease_epoch": lease_epoch, "failure_code": failure_code, "generation_version": expected_generation_version}
            if await _replay_operation(uow.repository, operation_id, preimage) is not None: return
            _require_live_lease(generation, builder_id=builder_id, lease_epoch=lease_epoch)
            if generation.version != expected_generation_version: raise ProjectionCheckpointConflict("Projection operation conflicts")
            if failure_code not in {"PROJECTION_SOURCE_INVALID", "PROJECTION_DIGEST_KEY_UNAVAILABLE", "PROJECTION_CHECKPOINT_CONFLICT", "PROJECTION_LEASE_CONFLICT", "PROJECTION_UNAVAILABLE"}: raise ProjectionCheckpointConflict("Projection operation conflicts")
            generation.status = "FAILED"; generation.failure_code = failure_code; generation.completed_at = datetime.now(UTC); generation.builder_id = None; generation.lease_expires_at = None; generation.version += 1
            await uow.repository.add_audit(generation_id=generation_id, action="projection_generation_failed", operation_id=operation_id, payload={"preimage": preimage, "postimage": {"status": "FAILED", "failure_code": failure_code, "generation_version": generation.version}}); await uow.commit()

    async def supersede(self, *, generation_id, expected_version, operation_id):
        async with self._session_lock(), self.uow_factory() as uow:
            generation = await uow.repository.get_generation(generation_id, lock=True)
            preimage = {"operation": "supersede", "generation_id": generation_id, "generation_version": expected_version, "status": "BUILD_COMPLETE"}
            if await _replay_operation(uow.repository, operation_id, preimage) is not None: return
            if generation is None or generation.status != "BUILD_COMPLETE" or generation.version != expected_version or not await uow.repository.has_newer_complete_generation(generation): raise ProjectionCheckpointConflict("Projection operation conflicts")
            generation.status = "SUPERSEDED"; generation.version += 1; generation.updated_at = datetime.now(UTC)
            await uow.repository.add_audit(generation_id=generation_id, action="projection_generation_superseded", operation_id=operation_id, payload={"preimage": preimage, "postimage": {"status": "SUPERSEDED", "generation_version": generation.version}}); await uow.commit()

    async def confirm_operation(self, *, generation_id: int | None = None, operation_id, expected_preimage, expected_postimage):
        try:
            async with self.confirmation_uow_factory() as uow:
                audit_record = await uow.repository.audit_record(operation_id)
                if generation_id is None and audit_record is not None: generation_id = audit_record[0]
                generation = None if generation_id is None else await uow.repository.get_generation(generation_id); checkpoint = None if generation_id is None else await uow.repository.get_checkpoint(generation_id); audit = None if audit_record is None else audit_record[1]
                if audit is None:
                    actual_preimage = await uow.repository.operation_preimage(generation, checkpoint, expected_preimage)
                    return ConfirmationResult.ROLLED_BACK if actual_preimage == expected_preimage else ConfirmationResult.UNKNOWN
                if generation is None or checkpoint is None or audit.get("preimage") != expected_preimage: return ConfirmationResult.UNKNOWN
                if await uow.repository.operation_postimage(generation, checkpoint, audit) == expected_postimage: return ConfirmationResult.COMMITTED
                return ConfirmationResult.UNKNOWN
        except __import__('asyncio').CancelledError: raise
        except Exception:
            return ConfirmationResult.UNKNOWN


__all__ = ["BuildCheckpoint", "BuildState", "ConfirmationResult", "HealthProjectionBuilder", "ProjectionCheckpointConflict", "ProjectionCommitOutcomeUnknown", "ProjectionDigestKeyring", "ProjectionDigestKeyUnavailable", "ProjectionHeartbeat", "ProjectionLeaseConflict", "ProjectionUnitOfWork", "ProjectionUnavailable"]
