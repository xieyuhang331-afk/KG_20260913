from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from sqlalchemy import func, insert, select, tuple_

from app.core.sqlalchemy_mapping import map_core_model_classes
from app.modules.system.models import OperationLog
from app.modules.user_health.models import HealthIndicator
from app.modules.health_fact.models import CanonicalHealthFactOrmModel

from .domain import (
    HealthIndicatorLegacyMapping,
    HealthIndicatorSourceSnapshot,
    HealthLegacyMappingUnavailable,
    decode_health_high_watermark,
)
from .models import HealthIndicatorLegacyMappingOrmModel


class HealthFactMappingRepository:
    def __init__(self, session) -> None:
        self.session = session
        map_core_model_classes()
        self.source = HealthIndicator.__table__

    async def acquire_source_lock(self, legacy_indicator_id: int, legacy_recorded_at, mapping_version: int) -> None:
        raw = f"health-legacy:{legacy_indicator_id}:{legacy_recorded_at.isoformat()}:v{mapping_version}".encode()
        key = int.from_bytes(hashlib.sha256(raw).digest()[:8], "big", signed=True)
        await _safe(self.session.execute(select(func.pg_advisory_xact_lock(key))))

    async def get_source(self, legacy_indicator_id: int, legacy_recorded_at) -> HealthIndicatorSourceSnapshot | None:
        row = (await _safe(self.session.execute(select(self.source.c.id, self.source.c.user_id, self.source.c.indicator_type, self.source.c.value, self.source.c.unit, self.source.c.source, self.source.c.recorded_at, self.source.c.created_at, self.source.c.batch_id).where(self.source.c.id == legacy_indicator_id, self.source.c.recorded_at == legacy_recorded_at)))).mappings().one_or_none()
        return None if row is None else HealthIndicatorSourceSnapshot(row["id"], row["user_id"], row["indicator_type"], row["value"], row["unit"], row["source"], row["recorded_at"], row["created_at"], None if row["batch_id"] is None else str(row["batch_id"]))

    async def find_existing(self, legacy_indicator_id: int, legacy_recorded_at, mapping_version: int):
        model = (await _safe(self.session.execute(select(HealthIndicatorLegacyMappingOrmModel).where(HealthIndicatorLegacyMappingOrmModel.legacy_indicator_id == legacy_indicator_id, HealthIndicatorLegacyMappingOrmModel.legacy_recorded_at == legacy_recorded_at, HealthIndicatorLegacyMappingOrmModel.mapping_version == mapping_version)))).scalar_one_or_none()
        return None if model is None else _restore(model)

    async def add(self, mapping: HealthIndicatorLegacyMapping):
        model = HealthIndicatorLegacyMappingOrmModel(**{name: getattr(mapping, name) for name in ("legacy_indicator_id","legacy_recorded_at","canonical_fact_id","mapping_version","batch_id","source_fingerprint","digest_key_id","disposition","reason_code")})
        self.session.add(model)
        await _safe(self.session.flush())
        await _safe(self.session.refresh(model))
        return replace(mapping, id=model.id, created_at=model.created_at)

    async def add_audit(self, mapping: HealthIndicatorLegacyMapping, action: str):
        audit=OperationLog(); audit.module="health_fact_mapping"; audit.object_type="health_indicator_legacy_mapping"; audit.object_id=mapping.id; audit.action=action
        audit.payload={"mapping_id":mapping.id,"batch_id":mapping.batch_id,"mapping_version":mapping.mapping_version,"disposition":mapping.disposition,"reason_code":mapping.reason_code,"source_fingerprint":mapping.source_fingerprint,"digest_key_id":mapping.digest_key_id,"canonical_fact_id":mapping.canonical_fact_id}
        self.session.add(audit); await _safe(self.session.flush())

    async def has_audit(self, mapping_id: int, action: str) -> bool:
        value=(await _safe(self.session.execute(select(OperationLog.id).where(OperationLog.module=="health_fact_mapping",OperationLog.object_id==mapping_id,OperationLog.action==action)))).scalar_one_or_none()
        return value is not None

    async def list_shadow_sources(self, high_watermark):
        recorded_at, source_id = _high_watermark_tuple(high_watermark)
        rows = (await _safe(self.session.execute(select(
            self.source.c.id, self.source.c.user_id, self.source.c.indicator_type,
            self.source.c.value, self.source.c.unit, self.source.c.source,
            self.source.c.recorded_at, self.source.c.created_at, self.source.c.batch_id,
        ).where(tuple_(self.source.c.recorded_at, self.source.c.id) <= tuple_(recorded_at, source_id))
        .order_by(self.source.c.recorded_at, self.source.c.id)))).mappings().all()
        return [HealthIndicatorSourceSnapshot(
            row["id"], row["user_id"], row["indicator_type"], row["value"], row["unit"],
            row["source"], row["recorded_at"], row["created_at"],
            None if row["batch_id"] is None else str(row["batch_id"]),
        ) for row in rows]

    async def list_shadow_mappings(self, high_watermark, mapping_version: int):
        recorded_at, source_id = _high_watermark_tuple(high_watermark)
        models = (await _safe(self.session.execute(select(HealthIndicatorLegacyMappingOrmModel).where(
            tuple_(HealthIndicatorLegacyMappingOrmModel.legacy_recorded_at, HealthIndicatorLegacyMappingOrmModel.legacy_indicator_id)
            <= tuple_(recorded_at, source_id),
            HealthIndicatorLegacyMappingOrmModel.mapping_version == mapping_version,
        ).order_by(HealthIndicatorLegacyMappingOrmModel.legacy_recorded_at, HealthIndicatorLegacyMappingOrmModel.legacy_indicator_id)))).scalars().all()
        return [_restore(model) for model in models]

    async def get_shadow_canonical(self, fact_id: int):
        row = (await _safe(self.session.execute(select(
            CanonicalHealthFactOrmModel.id,
            CanonicalHealthFactOrmModel.subject_user_id,
            CanonicalHealthFactOrmModel.indicator_code,
            CanonicalHealthFactOrmModel.numeric_value,
            CanonicalHealthFactOrmModel.unit,
            CanonicalHealthFactOrmModel.measured_at,
            CanonicalHealthFactOrmModel.source_type,
            CanonicalHealthFactOrmModel.producer_event_key,
            CanonicalHealthFactOrmModel.received_at,
        ).where(CanonicalHealthFactOrmModel.id == fact_id)))).mappings().one_or_none()
        return None if row is None else HealthFactShadowProjection(**dict(row))


@dataclass(frozen=True, slots=True)
class HealthFactShadowProjection:
    id: int
    subject_user_id: int
    indicator_code: str
    numeric_value: object
    unit: str
    measured_at: object
    source_type: str
    producer_event_key: str
    received_at: object


def _restore(model):
    return HealthIndicatorLegacyMapping(id=model.id,legacy_indicator_id=model.legacy_indicator_id,legacy_recorded_at=model.legacy_recorded_at,canonical_fact_id=model.canonical_fact_id,mapping_version=model.mapping_version,batch_id=str(model.batch_id),source_fingerprint=model.source_fingerprint,digest_key_id=model.digest_key_id,disposition=model.disposition,reason_code=model.reason_code,created_at=model.created_at)


class HealthMappingBatchControl:
    def __init__(self, engine) -> None:
        self._engine = engine
        self._connection = None
        self._lock_key = 0
        map_core_model_classes()
        self._source = HealthIndicator.__table__
        self._audit = OperationLog.__table__
        self._mapping = HealthIndicatorLegacyMappingOrmModel.__table__

    async def __aenter__(self):
        self._connection = await _batch_safe(self._engine.connect())
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        if self._connection is not None:
            try:
                await _batch_safe(self._connection.execute(select(func.pg_advisory_unlock(self._lock_key))))
            finally:
                await _batch_safe(self._connection.close())

    async def acquire_batch_lock(self, mapping_version: int) -> bool:
        raw = f"health-legacy-batch:v{mapping_version}".encode()
        self._lock_key = int.from_bytes(hashlib.sha256(raw).digest()[:8], "big", signed=True)
        acquired = bool((await _batch_safe(self._connection.execute(select(func.pg_try_advisory_lock(self._lock_key))))).scalar_one())
        await _batch_safe(self._connection.commit())
        return acquired

    async def get_batch_started(self, batch_id: str):
        return await self._get_batch_audit("mapping_batch_started", batch_id)

    async def get_batch_completed(self, batch_id: str):
        return await self._get_batch_audit("mapping_batch_completed", batch_id)

    async def _get_batch_audit(self, action: str, batch_id: str):
        rows = (await _batch_safe(self._connection.execute(
            select(self._audit.c.payload).where(
                self._audit.c.module == "health_fact_mapping",
                self._audit.c.action == action,
                self._audit.c.payload["batch_id"].as_string() == batch_id,
            )
        ))).scalars().all()
        if len(rows) > 1:
            raise HealthLegacyMappingUnavailable("Health mapping batch audit is unavailable")
        return rows[0] if rows else None

    async def add_batch_started(self, payload: dict) -> None:
        await self._add_batch_audit("mapping_batch_started", payload)

    async def add_batch_completed(self, payload: dict) -> None:
        await self._add_batch_audit("mapping_batch_completed", payload)

    async def _add_batch_audit(self, action: str, payload: dict) -> None:
        failed = False
        try:
            if self._connection.in_transaction():
                await self._connection.commit()
            async with self._connection.begin():
                await self._connection.execute(insert(self._audit).values(
                    module="health_fact_mapping", object_type="legacy_mapping_batch",
                    object_id=None, action=action, payload=payload,
                ))
        except Exception:
            failed = True
        if not failed:
            return
        try:
            if self._connection.in_transaction():
                await self._connection.rollback()
            async with self._engine.connect() as fresh:
                rows = (await fresh.execute(select(self._audit.c.payload).where(
                    self._audit.c.module == "health_fact_mapping",
                    self._audit.c.action == action,
                    self._audit.c.payload["batch_id"].as_string() == payload["batch_id"],
                ))).scalars().all()
                confirmed = len(rows) == 1 and _health_batch_payload_equal(rows[0], payload)
        except Exception:
            confirmed = False
        if confirmed:
            raise HealthLegacyMappingUnavailable(
                "Health mapping batch outcome is unknown"
            )
        raise HealthLegacyMappingUnavailable(
            "Health mapping batch outcome is unknown"
        )

    async def list_unprocessed(self, *, mapping_version: int, high_watermark, limit: int):
        recorded_at, source_id = _high_watermark_tuple(high_watermark)
        mapped = select(self._mapping.c.id).where(
            self._mapping.c.legacy_indicator_id == self._source.c.id,
            self._mapping.c.legacy_recorded_at == self._source.c.recorded_at,
            self._mapping.c.mapping_version == mapping_version,
        ).exists()
        rows = (await _batch_safe(self._connection.execute(
            select(self._source.c.recorded_at, self._source.c.id).where(
                tuple_(self._source.c.recorded_at, self._source.c.id) <= tuple_(recorded_at, source_id),
                ~mapped,
            ).order_by(self._source.c.recorded_at, self._source.c.id).limit(limit)
        ))).all()
        return list(rows)

    async def count_remaining(self, *, mapping_version: int, high_watermark) -> int:
        recorded_at, source_id = _high_watermark_tuple(high_watermark)
        mapped = select(self._mapping.c.id).where(
            self._mapping.c.legacy_indicator_id == self._source.c.id,
            self._mapping.c.legacy_recorded_at == self._source.c.recorded_at,
            self._mapping.c.mapping_version == mapping_version,
        ).exists()
        return int((await _batch_safe(self._connection.execute(
            select(func.count()).select_from(self._source).where(
                tuple_(self._source.c.recorded_at, self._source.c.id) <= tuple_(recorded_at, source_id),
                ~mapped,
            )
        ))).scalar_one())

    async def count_dispositions(self, *, mapping_version: int, high_watermark) -> dict[str, int]:
        recorded_at, source_id = _high_watermark_tuple(high_watermark)
        rows = (await _batch_safe(self._connection.execute(select(
            self._mapping.c.disposition, func.count()
        ).where(
            tuple_(self._mapping.c.legacy_recorded_at, self._mapping.c.legacy_indicator_id)
            <= tuple_(recorded_at, source_id),
            self._mapping.c.mapping_version == mapping_version,
        ).group_by(self._mapping.c.disposition)))).all()
        return {disposition: int(count) for disposition, count in rows}


def _high_watermark_tuple(value):
    normalized = decode_health_high_watermark(value)
    recorded_at = datetime.strptime(
        normalized["max_recorded_at"], "%Y-%m-%dT%H:%M:%S.%fZ"
    ).replace(tzinfo=timezone.utc)
    return recorded_at, normalized["max_id"]


async def _safe(awaitable):
    try:
        return await awaitable
    except Exception:
        raise HealthLegacyMappingUnavailable(
            "Health mapping persistence is unavailable"
        ) from None


async def _batch_safe(awaitable):
    try:
        return await awaitable
    except Exception:
        pass
    raise HealthLegacyMappingUnavailable(
        "Health mapping batch persistence is unavailable"
    )


def _health_batch_payload_equal(actual, expected) -> bool:
    from .domain import health_high_watermarks_equal
    try:
        return (
            isinstance(actual, dict)
            and set(actual) == set(expected)
            and health_high_watermarks_equal(
                actual["high_watermark"], expected["high_watermark"]
            )
            and {key: value for key, value in actual.items() if key != "high_watermark"}
            == {key: value for key, value in expected.items() if key != "high_watermark"}
        )
    except KeyError:
        return False
