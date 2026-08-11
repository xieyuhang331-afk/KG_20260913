from dataclasses import dataclass, replace
import hashlib
import hmac
import json

from app.modules.health_fact.repository import SqlAlchemyHealthFactRepository

from .domain import (
    HealthIndicatorLegacyMapping,
    HealthLegacyMappingConflict,
    HealthLegacyMappingUnavailable,
    decode_health_high_watermark,
    health_high_watermarks_equal,
    health_source_fingerprint,
    normalize_health_high_watermark,
    prepare_health_mapping,
)
from .repository import HealthFactMappingRepository


class HealthFactMappingUnitOfWork:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory
        self._session = None
        self.fact_repository = None
        self.mapping_repository = None
        self._finalized = False

    async def __aenter__(self):
        self._session = self._session_factory()
        await self._session.begin()
        self.fact_repository = SqlAlchemyHealthFactRepository(self._session)
        self.mapping_repository = HealthFactMappingRepository(self._session)
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
            raise HealthLegacyMappingUnavailable(
                "Health mapping commit outcome is unknown"
            ) from None
        self._finalized = True

    async def rollback(self):
        await self._session.rollback()
        self._finalized = True


@dataclass(frozen=True, slots=True)
class HealthMappingResult:
    mapping: HealthIndicatorLegacyMapping
    outcome: str


class HealthLegacyMappingService:
    def __init__(self, *, writer, uow_factory, readonly_uow_factory, keyring) -> None:
        self._writer = writer
        self._uow_factory = uow_factory
        self._readonly_uow_factory = readonly_uow_factory
        self._keyring = keyring

    async def map_one(self, *, legacy_indicator_id: int, legacy_recorded_at, batch_id: str):
        stored = None
        expected_fact = None
        try:
            async with self._uow_factory() as uow:
                repository = uow.mapping_repository
                await repository.acquire_source_lock(legacy_indicator_id, legacy_recorded_at, 1)
                source = await repository.get_source(legacy_indicator_id, legacy_recorded_at)
                if source is None:
                    raise HealthLegacyMappingUnavailable("Health mapping source is unavailable")
                prepared = prepare_health_mapping(
                    source=source, mapping_version=1, batch_id=batch_id, keyring=self._keyring
                )
                existing = await repository.find_existing(legacy_indicator_id, legacy_recorded_at, 1)
                if existing is not None:
                    expected = health_source_fingerprint(
                        source=source,
                        mapping_version=1,
                        keyring=self._keyring,
                        key_id=existing.digest_key_id,
                    )
                    if not hmac.compare_digest(existing.source_fingerprint, expected):
                        raise HealthLegacyMappingConflict("Health mapping conflict")
                    return HealthMappingResult(existing, "REPLAYED")
                mapping = prepared.mapping
                if prepared.fact_draft is not None:
                    fact_result = await self._writer.append_in_uow(
                        prepared.fact_draft, repository=uow.fact_repository
                    )
                    expected_fact = fact_result.fact
                    mapping = replace(mapping, canonical_fact_id=fact_result.fact.id)
                stored = await repository.add(mapping)
                await repository.add_audit(stored, "health_mapping_recorded")
                await uow.commit()
                return HealthMappingResult(stored, "CREATED")
        except HealthLegacyMappingUnavailable:
            pass
        if stored is not None:
            await self._confirm_unknown(stored, expected_fact)
        raise HealthLegacyMappingUnavailable(
            "Health mapping commit outcome is unknown"
        )

    async def _confirm_unknown(self, expected: HealthIndicatorLegacyMapping, expected_fact=None) -> None:
        try:
            async with self._readonly_uow_factory() as uow:
                actual = await uow.mapping_repository.find_existing(
                    expected.legacy_indicator_id,
                    expected.legacy_recorded_at,
                    expected.mapping_version,
                )
                mapping_audit = await uow.mapping_repository.has_audit(
                    expected.id, "health_mapping_recorded"
                )
                confirmed = actual == expected and mapping_audit
                if expected.canonical_fact_id is not None:
                    actual_fact = await uow.fact_repository.get_by_id(
                        expected.canonical_fact_id
                    )
                    fact_audit = await uow.fact_repository.has_audit(
                        fact_id=expected.canonical_fact_id, action="fact_appended"
                    )
                    confirmed = confirmed and actual_fact == expected_fact and fact_audit
                if not confirmed:
                    raise HealthLegacyMappingUnavailable(
                        "Health mapping confirmation is unavailable"
                    )
        except BaseException as exc:
            if not isinstance(exc, Exception):
                raise
            raise HealthLegacyMappingUnavailable(
                "Health mapping confirmation is unavailable"
            ) from None


class HealthMappingBatchService:
    def __init__(self, *, control_factory, item_service) -> None:
        self._control_factory = control_factory
        self._item_service = item_service

    async def run(self, *, batch_id: str, high_watermark, config_hash: str, page_size: int):
        if not batch_id or not 1 <= page_size <= 500:
            raise HealthLegacyMappingUnavailable("Health mapping batch is invalid")
        try:
            high_watermark = normalize_health_high_watermark(high_watermark)
        except HealthLegacyMappingUnavailable:
            high_watermark = decode_health_high_watermark(high_watermark)
        started = {
            "domain": "health",
            "batch_id": batch_id,
            "mapping_version": 1,
            "high_watermark": high_watermark,
            "config_hash": config_hash,
        }
        async with self._control_factory() as control:
            if not await control.acquire_batch_lock(1):
                raise HealthLegacyMappingUnavailable("Health mapping batch is already running")
            existing = await control.get_batch_started(batch_id)
            if existing is None:
                await control.add_batch_started(started)
            elif not _health_started_equal(existing, started):
                raise HealthLegacyMappingConflict("Health mapping batch conflicts")
            completed_existing = await control.get_batch_completed(batch_id)
            if completed_existing is not None:
                counts = await control.count_dispositions(
                    mapping_version=1, high_watermark=high_watermark
                )
                remaining = await control.count_remaining(
                    mapping_version=1, high_watermark=high_watermark
                )
                expected = _health_completed(started, counts)
                if remaining or not _health_completed_equal(completed_existing, expected):
                    raise HealthLegacyMappingConflict(
                        "Health mapping completed batch conflicts"
                    )
                return expected
            while True:
                source_keys = await control.list_unprocessed(
                    mapping_version=1, high_watermark=high_watermark, limit=page_size
                )
                if not source_keys:
                    break
                for recorded_at, source_id in source_keys:
                    result = await self._item_service.map_one(
                        legacy_indicator_id=source_id,
                        legacy_recorded_at=recorded_at,
                        batch_id=batch_id,
                    )
            if await control.count_remaining(mapping_version=1, high_watermark=high_watermark):
                raise HealthLegacyMappingUnavailable("Health mapping batch is incomplete")
            counts = await control.count_dispositions(
                mapping_version=1, high_watermark=high_watermark
            )
            completed = _health_completed(started, counts)
            await control.add_batch_completed(completed)
            return completed


class HealthShadowValidator:
    def __init__(self, *, repository, keyring) -> None:
        self._repository = repository
        self._keyring = keyring

    async def validate(self, high_watermark):
        try:
            high_watermark = normalize_health_high_watermark(high_watermark)
        except HealthLegacyMappingUnavailable:
            high_watermark = decode_health_high_watermark(high_watermark)
        sources = await self._repository.list_shadow_sources(high_watermark)
        mappings = await self._repository.list_shadow_mappings(high_watermark, 1)
        by_source = {}
        blocker = review_required = informational = 0
        canonical_ids = set()
        for mapping in mappings:
            key = (mapping.legacy_recorded_at, mapping.legacy_indicator_id)
            if key in by_source:
                blocker += 1
            else:
                by_source[key] = mapping
            if mapping.canonical_fact_id is not None:
                if mapping.canonical_fact_id in canonical_ids:
                    blocker += 1
                canonical_ids.add(mapping.canonical_fact_id)
        source_keys = set()
        for source in sources:
            key = (source.recorded_at, source.legacy_indicator_id)
            source_keys.add(key)
            mapping = by_source.get(key)
            if mapping is None:
                blocker += 1
                continue
            try:
                expected = health_source_fingerprint(
                    source=source, mapping_version=1, keyring=self._keyring,
                    key_id=mapping.digest_key_id,
                )
            except Exception:
                blocker += 1
                continue
            if not hmac.compare_digest(expected, mapping.source_fingerprint):
                blocker += 1
                continue
            prepared = prepare_health_mapping(
                source=source,
                mapping_version=1,
                batch_id=mapping.batch_id,
                keyring=self._keyring,
            )
            if (
                mapping.disposition != prepared.mapping.disposition
                or mapping.reason_code != prepared.mapping.reason_code
            ):
                blocker += 1
                continue
            if mapping.disposition == "MAPPED":
                fact = await self._repository.get_shadow_canonical(
                    mapping.canonical_fact_id
                )
                draft = prepared.fact_draft
                if (
                    fact is None
                    or draft is None
                    or mapping.created_at is None
                    or not _fact_matches_source(
                        fact, draft, expected_received_at=mapping.created_at
                    )
                ):
                    blocker += 1
            elif mapping.disposition == "REVIEW_REQUIRED":
                review_required += 1
            elif mapping.disposition == "UNMAPPED":
                informational += 1
            else:
                blocker += 1
        blocker += sum(
            1
            for mapping in mappings
            if (mapping.legacy_recorded_at, mapping.legacy_indicator_id)
            not in source_keys
        )
        return build_health_shadow_summary(
            blocker=blocker,
            review_required=review_required,
            informational=informational,
        )


def build_health_shadow_summary(*, blocker: int, review_required: int, informational: int):
    result = {
        "blocker": blocker,
        "review_required": review_required,
        "informational": informational,
    }
    return {**result, "summary_hash": _health_summary_hash(result)}


def _health_summary_hash(value: dict) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _health_started_equal(actual, expected) -> bool:
    try:
        return (
            isinstance(actual, dict)
            and set(actual) == set(expected)
            and health_high_watermarks_equal(
                actual["high_watermark"], expected["high_watermark"]
            )
            and {k: v for k, v in actual.items() if k != "high_watermark"}
            == {k: v for k, v in expected.items() if k != "high_watermark"}
        )
    except KeyError:
        return False


def _health_completed(started, counts):
    completed = {**started, "counts": dict(sorted(counts.items()))}
    completed["summary_hash"] = _health_summary_hash(completed)
    return completed


def _health_completed_equal(actual, expected) -> bool:
    if not isinstance(actual, dict) or set(actual) != set(expected):
        return False
    if not health_high_watermarks_equal(
        actual.get("high_watermark"), expected["high_watermark"]
    ):
        return False
    return (
        {k: v for k, v in actual.items() if k != "high_watermark"}
        == {k: v for k, v in expected.items() if k != "high_watermark"}
    )


def _fact_matches_source(fact, draft, *, expected_received_at=None) -> bool:
    fields_match = all(
        getattr(fact, name, None) == getattr(draft, name)
        for name in (
            "subject_user_id",
            "indicator_code",
            "numeric_value",
            "unit",
            "measured_at",
            "source_type",
            "producer_event_key",
        )
    )
    return fields_match and (
        expected_received_at is None
        or getattr(fact, "received_at", None) == expected_received_at
    )
