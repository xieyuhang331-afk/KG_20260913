from dataclasses import dataclass
import hashlib
import hmac
import json

from .domain import (
    OrganizationLegacyMapping,
    OrganizationMappingConflict,
    OrganizationMappingUnavailable,
    build_organization_mapping,
    normalize_organization_high_watermark,
    organization_source_fingerprint,
)
from .repository import OrganizationMappingRepository


class OrganizationMappingUnitOfWork:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory
        self._session = None
        self.repository = None
        self._finalized = False

    async def __aenter__(self):
        self._session = self._session_factory()
        await self._session.begin()
        self.repository = OrganizationMappingRepository(self._session)
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        try:
            if self._session is not None and not self._finalized:
                await self._session.rollback()
        finally:
            if self._session is not None:
                await self._session.close()

    async def commit(self):
        try:
            await self._session.commit()
        except BaseException as exc:
            if not isinstance(exc, Exception):
                raise
            raise OrganizationMappingUnavailable(
                "Organization mapping commit outcome is unknown"
            ) from None
        self._finalized = True

    async def rollback(self):
        await self._session.rollback()
        self._finalized = True


@dataclass(frozen=True, slots=True)
class OrganizationMappingResult:
    mapping: OrganizationLegacyMapping
    outcome: str


class OrganizationLegacyMappingService:
    def __init__(self, *, uow_factory, readonly_uow_factory, keyring) -> None:
        self._uow_factory = uow_factory
        self._readonly_uow_factory = readonly_uow_factory
        self._keyring = keyring

    async def map_one(self, *, legacy_tenant_id: int, batch_id: str) -> OrganizationMappingResult:
        stored = None
        try:
            async with self._uow_factory() as uow:
                repository = uow.repository
                await repository.acquire_source_lock(legacy_tenant_id, 1)
                source = await repository.get_source(legacy_tenant_id)
                candidate = build_organization_mapping(
                    source=source, mapping_version=1, batch_id=batch_id, keyring=self._keyring
                )
                existing = await repository.find_existing(legacy_tenant_id, 1)
                if existing is not None:
                    expected = organization_source_fingerprint(
                        source=source,
                        mapping_version=1,
                        keyring=self._keyring,
                        key_id=existing.digest_key_id,
                    )
                    if (
                        not hmac.compare_digest(existing.source_fingerprint, expected)
                        or existing.disposition != candidate.disposition
                        or existing.reason_code != candidate.reason_code
                        or existing.canonical_organization_id
                        != candidate.canonical_organization_id
                    ):
                        raise OrganizationMappingConflict("Organization mapping conflict")
                    return OrganizationMappingResult(existing, "REPLAYED")
                stored = await repository.add(candidate)
                await repository.add_audit(stored, "organization_mapping_recorded")
                await uow.commit()
                return OrganizationMappingResult(stored, "CREATED")
        except OrganizationMappingUnavailable:
            pass
        if stored is not None:
            await self._confirm_unknown(stored)
        raise OrganizationMappingUnavailable(
            "Organization mapping commit outcome is unknown"
        )

    async def _confirm_unknown(self, expected: OrganizationLegacyMapping) -> None:
        try:
            async with self._readonly_uow_factory() as uow:
                actual = await uow.repository.find_existing(
                    expected.legacy_tenant_id, expected.mapping_version
                )
                audit_exists = await uow.repository.has_audit(
                    expected.id, "organization_mapping_recorded"
                )
                if actual != expected or not audit_exists:
                    raise OrganizationMappingUnavailable(
                        "Organization mapping confirmation is unavailable"
                    )
        except BaseException as exc:
            if not isinstance(exc, Exception):
                raise
            raise OrganizationMappingUnavailable(
                "Organization mapping confirmation is unavailable"
            ) from None


class OrganizationMappingBatchService:
    def __init__(self, *, control_factory, item_service) -> None:
        self._control_factory = control_factory
        self._item_service = item_service

    async def run(self, *, batch_id: str, high_watermark, config_hash: str, page_size: int):
        high_watermark = normalize_organization_high_watermark(high_watermark)
        if not batch_id or not 1 <= page_size <= 500:
            raise OrganizationMappingUnavailable("Organization mapping batch is invalid")
        started = {
            "domain": "organization",
            "batch_id": batch_id,
            "mapping_version": 1,
            "high_watermark": high_watermark,
            "config_hash": config_hash,
        }
        async with self._control_factory() as control:
            if not await control.acquire_batch_lock(1):
                raise OrganizationMappingUnavailable("Organization mapping batch is already running")
            existing = await control.get_batch_started(batch_id)
            if existing is None:
                await control.add_batch_started(started)
            elif not _organization_started_equal(existing, started):
                raise OrganizationMappingConflict("Organization mapping batch conflicts")
            completed_existing = await control.get_batch_completed(batch_id)
            if completed_existing is not None:
                counts = await control.count_dispositions(
                    mapping_version=1, high_watermark=high_watermark
                )
                remaining = await control.count_remaining(
                    mapping_version=1, high_watermark=high_watermark
                )
                expected = _organization_completed(started, counts)
                if remaining or completed_existing != expected:
                    raise OrganizationMappingConflict(
                        "Organization mapping completed batch conflicts"
                    )
                return expected
            while True:
                source_ids = await control.list_unprocessed(
                    mapping_version=1, high_watermark=high_watermark, limit=page_size
                )
                if not source_ids:
                    break
                for source_id in source_ids:
                    result = await self._item_service.map_one(
                        legacy_tenant_id=source_id, batch_id=batch_id
                    )
            if await control.count_remaining(mapping_version=1, high_watermark=high_watermark):
                raise OrganizationMappingUnavailable("Organization mapping batch is incomplete")
            counts = await control.count_dispositions(
                mapping_version=1, high_watermark=high_watermark
            )
            completed = _organization_completed(started, counts)
            await control.add_batch_completed(completed)
            return completed


class OrganizationShadowValidator:
    def __init__(self, *, repository, keyring) -> None:
        self._repository = repository
        self._keyring = keyring

    async def validate(self, high_watermark):
        high_watermark = normalize_organization_high_watermark(high_watermark)
        sources = await self._repository.list_shadow_sources(high_watermark)
        mappings = await self._repository.list_shadow_mappings(high_watermark, 1)
        by_source = {}
        blocker = review_required = informational = 0
        for mapping in mappings:
            if mapping.legacy_tenant_id in by_source:
                blocker += 1
            else:
                by_source[mapping.legacy_tenant_id] = mapping
        source_ids = set()
        for source in sources:
            source_ids.add(source.legacy_tenant_id)
            mapping = by_source.get(source.legacy_tenant_id)
            if mapping is None:
                blocker += 1
                continue
            try:
                expected = organization_source_fingerprint(
                    source=source, mapping_version=1, keyring=self._keyring,
                    key_id=mapping.digest_key_id,
                )
            except OrganizationMappingUnavailable:
                blocker += 1
                continue
            if not hmac.compare_digest(expected, mapping.source_fingerprint):
                blocker += 1
                continue
            expected_mapping = build_organization_mapping(
                source=source,
                mapping_version=1,
                batch_id=mapping.batch_id,
                keyring=self._keyring,
            )
            if (
                mapping.disposition != expected_mapping.disposition
                or mapping.reason_code != expected_mapping.reason_code
                or mapping.canonical_organization_id
                != expected_mapping.canonical_organization_id
            ):
                blocker += 1
                continue
            if mapping.disposition == "MAPPED":
                canonical = await self._repository.get_shadow_canonical(
                    mapping.canonical_organization_id
                )
                if canonical != source.target:
                    blocker += 1
            elif mapping.disposition == "REVIEW_REQUIRED":
                review_required += 1
            elif mapping.disposition == "UNMAPPED":
                informational += 1
            else:
                blocker += 1
        blocker += sum(
            1 for mapping in mappings if mapping.legacy_tenant_id not in source_ids
        )
        return build_organization_shadow_summary(
            blocker=blocker,
            review_required=review_required,
            informational=informational,
        )


def build_organization_shadow_summary(*, blocker: int, review_required: int, informational: int):
    result = {
        "blocker": blocker,
        "review_required": review_required,
        "informational": informational,
    }
    return {**result, "summary_hash": _safe_summary_hash(result)}


def _safe_summary_hash(value: dict) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _organization_started_equal(actual, expected) -> bool:
    try:
        return (
            isinstance(actual, dict)
            and set(actual) == set(expected)
            and normalize_organization_high_watermark(actual["high_watermark"])
            == expected["high_watermark"]
            and {k: v for k, v in actual.items() if k != "high_watermark"}
            == {k: v for k, v in expected.items() if k != "high_watermark"}
        )
    except (KeyError, OrganizationMappingUnavailable):
        return False


def _organization_completed(started, counts):
    completed = {**started, "counts": dict(sorted(counts.items()))}
    completed["summary_hash"] = _safe_summary_hash(completed)
    return completed
