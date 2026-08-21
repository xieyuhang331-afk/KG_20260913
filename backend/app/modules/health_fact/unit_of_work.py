from __future__ import annotations

import hmac
from dataclasses import dataclass
from decimal import Decimal

from .domain import (
    CanonicalHealthFact,
    CanonicalHealthFactDraft,
    HealthFactCommitOutcomeUnknown,
    HealthFactCorrectionConflict,
    HealthFactDigestKeyring,
    HealthFactError,
    HealthFactIdempotencyConflict,
    HealthFactSourceForbidden,
    HealthFactUnavailable,
    prepare_fact,
    semantic_lock_key,
    source_identity_digests,
    verify_payload,
)
from .repository import SqlAlchemyHealthFactRepository


class SqlAlchemyHealthFactUnitOfWork:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory
        self._session = None
        self.repository = None
        self._finalized = False

    async def __aenter__(self):
        try:
            self._session = self._session_factory()
            await self._session.begin()
            self.repository = SqlAlchemyHealthFactRepository(self._session)
            return self
        except Exception:
            await self._cleanup()
            raise HealthFactUnavailable(
                "Health fact transaction is unavailable"
            ) from None
        except BaseException:
            await self._cleanup()
            raise

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        try:
            if not self._finalized and self._session is not None:
                await self._session.rollback()
        finally:
            if self._session is not None:
                await self._session.close()
            self._session = None
            self.repository = None
            self._finalized = True
        return False

    async def commit(self) -> None:
        if self._session is None or self._finalized:
            raise HealthFactUnavailable("Health fact transaction is unavailable")
        try:
            await self._session.commit()
        except BaseException as exc:
            if not isinstance(exc, Exception):
                raise
            raise HealthFactCommitOutcomeUnknown(
                "Health fact commit outcome is unknown"
            ) from None
        self._finalized = True

    async def rollback(self) -> None:
        if self._session is None or self._finalized:
            raise HealthFactUnavailable("Health fact transaction is unavailable")
        try:
            await self._session.rollback()
        except Exception:
            raise HealthFactUnavailable(
                "Health fact transaction is unavailable"
            ) from None
        except BaseException:
            raise
        self._finalized = True

    async def _cleanup(self) -> None:
        if self._session is None:
            return
        try:
            await self._session.rollback()
        finally:
            await self._session.close()


@dataclass(frozen=True, slots=True)
class HealthFactWriteResult:
    fact: CanonicalHealthFact
    outcome: str


class CanonicalHealthFactWriter:
    def __init__(
        self, *, uow_factory, readonly_uow_factory, keyring, producer_authority
    ) -> None:
        self._uow_factory = uow_factory
        self._readonly_uow_factory = readonly_uow_factory
        self._keyring: HealthFactDigestKeyring = keyring
        self._producer_authority = producer_authority

    async def write(self, draft: CanonicalHealthFactDraft) -> HealthFactWriteResult:
        authority_failed = False
        authorized = None
        try:
            authorized = await self._producer_authority.authorize(draft)
        except HealthFactError:
            raise
        except Exception:
            authority_failed = True
        if authority_failed:
            raise HealthFactSourceForbidden("Health fact source is forbidden")
        if not isinstance(authorized, CanonicalHealthFactDraft):
            raise HealthFactSourceForbidden("Health fact source is forbidden")
        prepared = prepare_fact(authorized, self._keyring)
        try:
            async with self._uow_factory() as uow:
                repository = uow.repository
                await repository.acquire_semantic_lock(
                    semantic_lock_key(
                        source_type=authorized.source_type,
                        producer_event_key=authorized.producer_event_key,
                    )
                )
                digests = source_identity_digests(
                    source_identity=authorized.source_identity,
                    keyring=self._keyring,
                )
                existing = await repository.find_by_semantic_identity(
                    source_type=authorized.source_type,
                    producer_event_key=authorized.producer_event_key,
                    source_identity_digests=tuple(digests.values()),
                )
                if existing is not None:
                    if not verify_payload(
                        stored=existing, draft=authorized, keyring=self._keyring
                    ):
                        raise HealthFactIdempotencyConflict(
                            "Health fact idempotency conflict"
                        )
                    return HealthFactWriteResult(existing, "REPLAYED")
                if authorized.supersedes_fact_id is not None:
                    predecessor = await repository.get_by_id(
                        authorized.supersedes_fact_id
                    )
                    successor = await repository.get_successor(
                        authorized.supersedes_fact_id
                    )
                    if (
                        predecessor is None
                        or successor is not None
                        or predecessor.subject_user_id != authorized.subject_user_id
                        or predecessor.subject_member_id != authorized.subject_member_id
                        or predecessor.catalog_version != authorized.catalog_version
                        or predecessor.indicator_code != authorized.indicator_code
                    ):
                        raise HealthFactCorrectionConflict(
                            "Health fact correction conflict"
                        )
                stored = await repository.add(prepared)
                await repository.add_audit(
                    fact=stored,
                    action=(
                        "fact_corrected"
                        if authorized.supersedes_fact_id is not None
                        else "fact_appended"
                    ),
                )
                await uow.commit()
                return HealthFactWriteResult(stored, "CREATED")
        except HealthFactCommitOutcomeUnknown:
            await self._confirm_outcome(authorized)
            raise

    async def append_in_uow(
        self, draft: CanonicalHealthFactDraft, *, repository
    ) -> HealthFactWriteResult:
        """Append using a caller-owned transaction; never commit or rollback."""
        authorized = await self._authorize(draft)
        return await self._append_authorized(authorized, repository=repository)

    async def _authorize(
        self, draft: CanonicalHealthFactDraft
    ) -> CanonicalHealthFactDraft:
        authority_failed = False
        authorized = None
        try:
            authorized = await self._producer_authority.authorize(draft)
        except HealthFactError:
            raise
        except Exception:
            authority_failed = True
        if authority_failed:
            raise HealthFactSourceForbidden("Health fact source is forbidden")
        if not isinstance(authorized, CanonicalHealthFactDraft):
            raise HealthFactSourceForbidden("Health fact source is forbidden")
        return authorized

    async def _append_authorized(
        self, authorized: CanonicalHealthFactDraft, *, repository
    ) -> HealthFactWriteResult:
        prepared = prepare_fact(authorized, self._keyring)
        await repository.acquire_semantic_lock(
            semantic_lock_key(
                source_type=authorized.source_type,
                producer_event_key=authorized.producer_event_key,
            )
        )
        digests = source_identity_digests(
            source_identity=authorized.source_identity,
            keyring=self._keyring,
        )
        existing = await repository.find_by_semantic_identity(
            source_type=authorized.source_type,
            producer_event_key=authorized.producer_event_key,
            source_identity_digests=tuple(digests.values()),
        )
        if existing is not None:
            if not verify_payload(
                stored=existing, draft=authorized, keyring=self._keyring
            ):
                raise HealthFactIdempotencyConflict(
                    "Health fact idempotency conflict"
                )
            return HealthFactWriteResult(existing, "REPLAYED")
        if authorized.supersedes_fact_id is not None:
            predecessor = await repository.get_by_id(authorized.supersedes_fact_id)
            successor = await repository.get_successor(authorized.supersedes_fact_id)
            if (
                predecessor is None
                or successor is not None
                or predecessor.subject_user_id != authorized.subject_user_id
                or predecessor.subject_member_id != authorized.subject_member_id
                or predecessor.catalog_version != authorized.catalog_version
                or predecessor.indicator_code != authorized.indicator_code
            ):
                raise HealthFactCorrectionConflict(
                    "Health fact correction conflict"
                )
        stored = await repository.add(prepared)
        await repository.add_audit(
            fact=stored,
            action=(
                "fact_corrected"
                if authorized.supersedes_fact_id is not None
                else "fact_appended"
            ),
        )
        return HealthFactWriteResult(stored, "CREATED")

    async def _confirm_outcome(self, draft: CanonicalHealthFactDraft) -> bool:
        try:
            async with self._readonly_uow_factory() as uow:
                digests = source_identity_digests(
                    source_identity=draft.source_identity, keyring=self._keyring
                )
                fact = await uow.repository.find_by_semantic_identity(
                    source_type=draft.source_type,
                    producer_event_key=draft.producer_event_key,
                    source_identity_digests=tuple(digests.values()),
                )
                if fact is None or not _fact_matches(
                    fact=fact,
                    draft=draft,
                    keyring=self._keyring,
                    identity_digests=digests,
                ):
                    return False
                return await uow.repository.has_audit(
                    fact_id=fact.id,
                    action=(
                        "fact_corrected"
                        if draft.supersedes_fact_id is not None
                        else "fact_appended"
                    ),
                )
        except BaseException as exc:
            if not isinstance(exc, Exception):
                raise
            return False


def _fact_matches(
    *,
    fact: CanonicalHealthFact,
    draft: CanonicalHealthFactDraft,
    keyring: HealthFactDigestKeyring,
    identity_digests: dict[str, str],
) -> bool:
    expected_identity = identity_digests.get(fact.digest_key_id)
    if expected_identity is None or not hmac.compare_digest(
        fact.source_identity_digest, expected_identity
    ):
        return False
    return (
        fact.subject_user_id == draft.subject_user_id
        and fact.subject_member_id == draft.subject_member_id
        and fact.fact_ref == draft.fact_ref
        and fact.indicator_code == draft.indicator_code
        and fact.catalog_version == draft.catalog_version
        and fact.value_kind == "NUMERIC"
        and fact.numeric_value == draft.numeric_value.quantize(Decimal("0.01"))
        and fact.unit == draft.unit
        and fact.measured_at == draft.measured_at
        and fact.source_type == draft.source_type
        and fact.producer_event_key == draft.producer_event_key
        and fact.supersedes_fact_id == draft.supersedes_fact_id
        and fact.correction_reason_code == draft.correction_reason_code
        and fact.created_by == draft.created_by
        and verify_payload(stored=fact, draft=draft, keyring=keyring)
    )
