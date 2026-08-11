from __future__ import annotations

from dataclasses import replace

from sqlalchemy import func, insert, select, text

from app.core.sqlalchemy_mapping import map_core_model_classes
from app.modules.system.models import OperationLog, PlatformOrg
from app.modules.tenant.models import Tenant

from .domain import (
    OrganizationLegacyMapping,
    OrganizationMappingUnavailable,
    OrganizationSourceSnapshot,
    normalize_organization_high_watermark,
    organization_mapping_lock_key,
)
from .models import OrganizationLegacyMappingOrmModel


class OrganizationMappingRepository:
    def __init__(self, session) -> None:
        self.session = session
        map_core_model_classes()
        self.tenant = Tenant.__table__
        self.org = PlatformOrg.__table__

    async def acquire_source_lock(self, legacy_tenant_id: int, mapping_version: int) -> None:
        await _safe(self.session.execute(select(func.pg_advisory_xact_lock(organization_mapping_lock_key(legacy_tenant_id, mapping_version)))))

    async def get_source(self, legacy_tenant_id: int) -> OrganizationSourceSnapshot:
        tenant = (await _safe(self.session.execute(select(self.tenant.c.id, self.tenant.c.org_id, self.tenant.c.status).where(self.tenant.c.id == legacy_tenant_id)))).mappings().one_or_none()
        if tenant is None:
            return OrganizationSourceSnapshot(legacy_tenant_id, None, None)
        target = None
        ancestors: list[dict | None] = []
        if tenant["org_id"] is not None:
            target = (await _safe(self.session.execute(_org_select(self.org, tenant["org_id"])))).mappings().one_or_none()
            target = None if target is None else dict(target)
            current = target
            seen = {tenant["org_id"]}
            while current is not None and current["parent_id"] is not None:
                parent_id = current["parent_id"]
                if parent_id in seen:
                    ancestors.append(dict(current))
                    break
                seen.add(parent_id)
                parent = (await _safe(self.session.execute(_org_select(self.org, parent_id)))).mappings().one_or_none()
                if parent is None:
                    ancestors.append(None)
                    break
                current = dict(parent)
                ancestors.append(current)
                if len(ancestors) > 4:
                    break
        return OrganizationSourceSnapshot(tenant["id"], tenant["org_id"], target, tuple(ancestors))

    async def find_existing(self, legacy_tenant_id: int, mapping_version: int):
        model = (await _safe(self.session.execute(select(OrganizationLegacyMappingOrmModel).where(OrganizationLegacyMappingOrmModel.legacy_tenant_id == legacy_tenant_id, OrganizationLegacyMappingOrmModel.mapping_version == mapping_version)))).scalar_one_or_none()
        return None if model is None else _restore(model)

    async def add(self, mapping: OrganizationLegacyMapping):
        model = OrganizationLegacyMappingOrmModel(**{name: getattr(mapping, name) for name in ("legacy_tenant_id","legacy_org_id","canonical_organization_id","mapping_version","batch_id","source_fingerprint","digest_key_id","disposition","reason_code")})
        self.session.add(model)
        await _safe(self.session.flush())
        await _safe(self.session.refresh(model))
        return replace(mapping, id=model.id)

    async def add_audit(self, mapping: OrganizationLegacyMapping, action: str):
        audit = OperationLog()
        audit.module = "organization_mapping"
        audit.object_type = "organization_legacy_mapping"
        audit.object_id = mapping.id
        audit.action = action
        audit.payload = {"mapping_id": mapping.id, "batch_id": mapping.batch_id, "mapping_version": mapping.mapping_version, "disposition": mapping.disposition, "reason_code": mapping.reason_code, "source_fingerprint": mapping.source_fingerprint, "digest_key_id": mapping.digest_key_id}
        self.session.add(audit)
        await _safe(self.session.flush())

    async def has_audit(self, mapping_id: int, action: str) -> bool:
        value = (await _safe(self.session.execute(select(OperationLog.id).where(OperationLog.module == "organization_mapping", OperationLog.object_id == mapping_id, OperationLog.action == action)))).scalar_one_or_none()
        return value is not None

    async def list_shadow_sources(self, high_watermark):
        maximum = normalize_organization_high_watermark(high_watermark)["max_tenant_id"]
        ids = (await _safe(self.session.execute(select(self.tenant.c.id).where(self.tenant.c.id <= maximum).order_by(self.tenant.c.id)))).scalars().all()
        return [await self.get_source(source_id) for source_id in ids]

    async def list_shadow_mappings(self, high_watermark, mapping_version: int):
        maximum = normalize_organization_high_watermark(high_watermark)["max_tenant_id"]
        models = (await _safe(self.session.execute(select(OrganizationLegacyMappingOrmModel).where(
            OrganizationLegacyMappingOrmModel.legacy_tenant_id <= maximum,
            OrganizationLegacyMappingOrmModel.mapping_version == mapping_version,
        ).order_by(OrganizationLegacyMappingOrmModel.legacy_tenant_id)))).scalars().all()
        return [_restore(model) for model in models]

    async def get_shadow_canonical(self, organization_id: int):
        row = (await _safe(self.session.execute(_org_select(self.org, organization_id)))).mappings().one_or_none()
        return None if row is None else dict(row)


def _restore(model):
    return OrganizationLegacyMapping(id=model.id, legacy_tenant_id=model.legacy_tenant_id, legacy_org_id=model.legacy_org_id, canonical_organization_id=model.canonical_organization_id, mapping_version=model.mapping_version, batch_id=str(model.batch_id), source_fingerprint=model.source_fingerprint, digest_key_id=model.digest_key_id, disposition=model.disposition, reason_code=model.reason_code)


class OrganizationMappingBatchControl:
    _lock_key = organization_mapping_lock_key(0, 1)

    def __init__(self, engine) -> None:
        self._engine = engine
        self._connection = None
        map_core_model_classes()
        self._source = Tenant.__table__
        self._audit = OperationLog.__table__
        self._mapping = OrganizationLegacyMappingOrmModel.__table__

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
        self._lock_key = organization_mapping_lock_key(0, mapping_version)
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
                self._audit.c.module == "organization_mapping",
                self._audit.c.action == action,
                self._audit.c.payload["batch_id"].as_string() == batch_id,
            )
        ))).scalars().all()
        if len(rows) > 1:
            raise OrganizationMappingUnavailable("Organization mapping batch audit is unavailable")
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
                    module="organization_mapping", object_type="legacy_mapping_batch",
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
                    self._audit.c.module == "organization_mapping",
                    self._audit.c.action == action,
                    self._audit.c.payload["batch_id"].as_string() == payload["batch_id"],
                ))).scalars().all()
                confirmed = len(rows) == 1 and _organization_batch_payload_equal(rows[0], payload)
        except Exception:
            confirmed = False
        # A confirmed commit remains a safe failure; callers must never write a second audit.
        if confirmed:
            raise OrganizationMappingUnavailable(
                "Organization mapping batch outcome is unknown"
            )
        raise OrganizationMappingUnavailable(
            "Organization mapping batch outcome is unknown"
        )

    async def list_unprocessed(self, *, mapping_version: int, high_watermark: int, limit: int):
        high_watermark = normalize_organization_high_watermark(high_watermark)["max_tenant_id"]
        mapped = select(self._mapping.c.id).where(
            self._mapping.c.legacy_tenant_id == self._source.c.id,
            self._mapping.c.mapping_version == mapping_version,
        ).exists()
        return list((await _batch_safe(self._connection.execute(
            select(self._source.c.id).where(
                self._source.c.id <= high_watermark, ~mapped
            ).order_by(self._source.c.id).limit(limit)
        ))).scalars())

    async def count_remaining(self, *, mapping_version: int, high_watermark: int) -> int:
        high_watermark = normalize_organization_high_watermark(high_watermark)["max_tenant_id"]
        mapped = select(self._mapping.c.id).where(
            self._mapping.c.legacy_tenant_id == self._source.c.id,
            self._mapping.c.mapping_version == mapping_version,
        ).exists()
        return int((await _batch_safe(self._connection.execute(
            select(func.count()).select_from(self._source).where(
                self._source.c.id <= high_watermark, ~mapped
            )
        ))).scalar_one())

    async def count_dispositions(self, *, mapping_version: int, high_watermark) -> dict[str, int]:
        maximum = normalize_organization_high_watermark(high_watermark)["max_tenant_id"]
        rows = (await _batch_safe(self._connection.execute(
            select(self._mapping.c.disposition, func.count()).where(
                self._mapping.c.legacy_tenant_id <= maximum,
                self._mapping.c.mapping_version == mapping_version,
            ).group_by(self._mapping.c.disposition)
        ))).all()
        return {disposition: int(count) for disposition, count in rows}


def _org_select(table, organization_id):
    return select(
        table.c.id, table.c.parent_id, table.c.org_type, table.c.status, table.c.version
    ).where(table.c.id == organization_id)


async def _safe(awaitable):
    try:
        return await awaitable
    except Exception:
        raise OrganizationMappingUnavailable(
            "Organization mapping persistence is unavailable"
        ) from None


async def _batch_safe(awaitable):
    try:
        return await awaitable
    except Exception:
        pass
    raise OrganizationMappingUnavailable(
        "Organization mapping batch persistence is unavailable"
    )


def _organization_batch_payload_equal(actual, expected) -> bool:
    try:
        return (
            isinstance(actual, dict)
            and set(actual) == set(expected)
            and normalize_organization_high_watermark(actual["high_watermark"])
            == normalize_organization_high_watermark(expected["high_watermark"])
            and {key: value for key, value in actual.items() if key != "high_watermark"}
            == {key: value for key, value in expected.items() if key != "high_watermark"}
        )
    except (KeyError, OrganizationMappingUnavailable):
        return False
