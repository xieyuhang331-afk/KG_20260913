from datetime import datetime, timedelta
from typing import NoReturn

from sqlalchemy import select
from sqlalchemy.exc import (
    DBAPIError,
    DisconnectionError,
    IntegrityError,
    InterfaceError,
    OperationalError,
    TimeoutError as SqlAlchemyTimeoutError,
)

from ..application.registration_bootstrap_ports import (
    RegistrationBootstrapConflict,
    RegistrationBootstrapNotFound,
    RegistrationBootstrapPersistenceError,
    RegistrationBootstrapRecord,
    RegistrationBootstrapUnavailable,
    RegistrationEligibilityProof,
    RegistrationMemberNoAllocationProof,
    UserMemberSelfLink,
)
from ..application.member_no_allocator import (
    AllocationScope,
    AllocationSourceSystem,
    MemberNoAllocationKey,
)
from app.modules.auth.eligibility_evidence_repository import (
    SqlAlchemyEligibilityDecisionEvidenceReader,
)
from ..repository import (
    MemberNotFoundError,
    MemberPersistenceUnavailableError,
    MemberRepositoryError,
    MemberUniquenessConflictError,
)
from ..value_objects import MemberNo
from .models import (
    RegistrationBootstrapRecordOrmModel,
    UserMemberSelfLinkOrmModel,
)
from .sqlalchemy_member_no_allocation_ledger import (
    SqlAlchemyMemberNoAllocationLedger,
)


_UNIQUE_VIOLATION_SQLSTATE = "23505"
_BUSINESS_CONFLICT_CONSTRAINTS = frozenset(
    {
        "uq_user_member_self_link_user",
        "uq_user_member_self_link_member",
        "uq_registration_bootstrap_canonical_source",
        "uq_registration_bootstrap_member",
        "uq_registration_bootstrap_self_link",
        "uq_registration_bootstrap_allocation",
    }
)
_UNAVAILABLE_ERRORS = (
    DisconnectionError,
    InterfaceError,
    OperationalError,
    SqlAlchemyTimeoutError,
)


class SqlAlchemyRegistrationEligibilityProofReader:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def get_current_eligible(self, *, decision_ref, user_ref):
        try:
            async with self._session_factory() as session:
                proof = await SqlAlchemyEligibilityDecisionEvidenceReader(
                    session
                ).get_current_eligible(
                    decision_ref=decision_ref,
                    user_ref=user_ref,
                )
            return RegistrationEligibilityProof(
                decision_ref=proof.decision_ref,
                user_ref=proof.user_ref,
                decision="APPROVED",
                policy_version=proof.policy_version,
            )
        except Exception:
            raise RegistrationBootstrapPersistenceError(
                "registration eligibility proof is unavailable"
            ) from None


class SqlAlchemyRegistrationMemberNoAllocationProofReader:
    def __init__(self, session_factory, clock) -> None:
        self._session_factory = session_factory
        self._clock = clock

    async def get_allocated(self, *, allocation_ref, user_ref):
        try:
            async with self._session_factory() as session:
                allocation = await SqlAlchemyMemberNoAllocationLedger(
                    session, self._clock
                ).get_by_key(
                    MemberNoAllocationKey(
                        allocation_scope=AllocationScope.REGISTRATION_BOOTSTRAP,
                        source_system=AllocationSourceSystem.P1_USER,
                        source_ref=user_ref,
                    )
                )
            if allocation.allocation_id != allocation_ref:
                raise ValueError
            return RegistrationMemberNoAllocationProof(
                allocation_ref=allocation.allocation_id,
                user_ref=user_ref,
                member_no=allocation.member_no.value,
            )
        except Exception:
            raise RegistrationBootstrapPersistenceError(
                "registration member number allocation proof is unavailable"
            ) from None


class SqlAlchemyRegistrationMemberStore:
    def __init__(self, repository) -> None:
        self._repository = repository

    async def get(self, member_no: str):
        try:
            return await self._repository.get_by_member_no(MemberNo(member_no))
        except MemberNotFoundError:
            raise RegistrationBootstrapNotFound(
                "registration member was not found"
            ) from None
        except MemberPersistenceUnavailableError:
            raise RegistrationBootstrapUnavailable(
                "registration member persistence is unavailable"
            ) from None
        except MemberRepositoryError:
            raise RegistrationBootstrapPersistenceError(
                "registration member persistence operation failed"
            ) from None

    async def add(self, member) -> None:
        try:
            await self._repository.add(member)
        except MemberUniquenessConflictError:
            raise RegistrationBootstrapConflict(
                "registration member conflicts with persisted state"
            ) from None
        except MemberPersistenceUnavailableError:
            raise RegistrationBootstrapUnavailable(
                "registration member persistence is unavailable"
            ) from None
        except MemberRepositoryError:
            raise RegistrationBootstrapPersistenceError(
                "registration member persistence operation failed"
            ) from None


class SqlAlchemyUserMemberSelfLinkStore:
    def __init__(self, session, clock) -> None:
        self._session = session
        self._clock = clock

    async def get(self, user_ref: int) -> UserMemberSelfLink:
        try:
            result = await self._session.execute(
                select(UserMemberSelfLinkOrmModel).where(
                    UserMemberSelfLinkOrmModel.user_ref == user_ref
                )
            )
            model = result.scalar_one_or_none()
        except Exception as exc:
            self._raise_translated(exc)
        if model is None:
            raise RegistrationBootstrapNotFound("self link was not found")
        try:
            return UserMemberSelfLink(
                link_id=model.link_id,
                user_ref=model.user_ref,
                member_id=model.member_id,
                source=model.source,
                eligibility_decision_ref=model.eligibility_decision_ref,
                establishment_basis=model.establishment_basis,
                establishment_record_ref=model.establishment_record_ref,
            )
        except Exception:
            raise RegistrationBootstrapPersistenceError(
                "stored self link is invalid"
            ) from None

    async def add(self, link: UserMemberSelfLink) -> None:
        if type(link) is not UserMemberSelfLink:
            raise RegistrationBootstrapPersistenceError(
                "self link persistence input is invalid"
            )
        try:
            self._session.add(
                UserMemberSelfLinkOrmModel(
                    link_id=link.link_id,
                    user_ref=link.user_ref,
                    member_id=link.member_id,
                    source=link.source,
                    eligibility_decision_ref=link.eligibility_decision_ref,
                    establishment_basis=link.establishment_basis,
                    establishment_record_ref=link.establishment_record_ref,
                    created_at=self._now(),
                )
            )
            await self._session.flush()
        except Exception as exc:
            self._raise_translated(exc)

    def _now(self) -> datetime:
        value = self._clock()
        if (
            type(value) is not datetime
            or value.tzinfo is None
            or value.utcoffset() != timedelta(0)
        ):
            raise RegistrationBootstrapPersistenceError(
                "registration bootstrap clock is invalid"
            )
        return value

    @staticmethod
    def _raise_translated(exc: Exception) -> NoReturn:
        _raise_translated(exc)


class SqlAlchemyRegistrationBootstrapRecordStore:
    def __init__(self, session, clock) -> None:
        self._session = session
        self._clock = clock

    async def get(self, user_ref: int) -> RegistrationBootstrapRecord:
        try:
            result = await self._session.execute(
                select(RegistrationBootstrapRecordOrmModel).where(
                    RegistrationBootstrapRecordOrmModel.source
                    == "REGISTRATION_VERIFIED",
                    RegistrationBootstrapRecordOrmModel.user_ref == user_ref,
                )
            )
            model = result.scalar_one_or_none()
        except Exception as exc:
            _raise_translated(exc)
        if model is None:
            raise RegistrationBootstrapNotFound(
                "registration bootstrap record was not found"
            )
        try:
            return RegistrationBootstrapRecord(
                record_id=model.record_id,
                user_ref=model.user_ref,
                member_id=model.member_id,
                self_link_id=model.self_link_id,
                registration_event_id=model.registration_event_id,
                eligibility_decision_ref=model.eligibility_decision_ref,
                member_no_allocation_ref=model.member_no_allocation_ref,
                member_no=model.member_no,
                bootstrap_scope=model.bootstrap_scope,
                source_system=model.source_system,
                source_ref=model.source_ref,
                decision=model.decision,
                policy_version=model.policy_version,
                source=model.source,
            )
        except Exception:
            raise RegistrationBootstrapPersistenceError(
                "stored registration bootstrap record is invalid"
            ) from None

    async def add(self, record: RegistrationBootstrapRecord) -> None:
        if type(record) is not RegistrationBootstrapRecord:
            raise RegistrationBootstrapPersistenceError(
                "registration bootstrap persistence input is invalid"
            )
        try:
            now = self._clock()
            if (
                type(now) is not datetime
                or now.tzinfo is None
                or now.utcoffset() != timedelta(0)
            ):
                raise RegistrationBootstrapPersistenceError(
                    "registration bootstrap clock is invalid"
                )
            self._session.add(
                RegistrationBootstrapRecordOrmModel(
                    record_id=record.record_id,
                    user_ref=record.user_ref,
                    member_id=record.member_id,
                    self_link_id=record.self_link_id,
                    registration_event_id=record.registration_event_id,
                    eligibility_decision_ref=record.eligibility_decision_ref,
                    member_no_allocation_ref=record.member_no_allocation_ref,
                    member_no=record.member_no,
                    bootstrap_scope=record.bootstrap_scope,
                    source_system=record.source_system,
                    source_ref=record.source_ref,
                    decision=record.decision,
                    policy_version=record.policy_version,
                    source=record.source,
                    created_at=now,
                )
            )
            await self._session.flush()
        except RegistrationBootstrapPersistenceError:
            raise
        except Exception as exc:
            _raise_translated(exc)


def _raise_translated(exc: Exception) -> NoReturn:
    constraint = _unique_constraint(exc)
    if constraint in _BUSINESS_CONFLICT_CONSTRAINTS:
        error = RegistrationBootstrapConflict(
            "registration bootstrap conflicts with persisted state"
        )
    elif isinstance(exc, _UNAVAILABLE_ERRORS) or (
        isinstance(exc, DBAPIError) and exc.connection_invalidated
    ):
        error = RegistrationBootstrapUnavailable(
            "registration bootstrap persistence is unavailable"
        )
    else:
        error = RegistrationBootstrapPersistenceError(
            "registration bootstrap persistence operation failed"
        )
    raise error from None


def _unique_constraint(exc: Exception) -> str | None:
    if not isinstance(exc, IntegrityError):
        return None
    original = exc.orig
    driver_error = getattr(original, "__cause__", None)
    sqlstate = getattr(original, "sqlstate", None) or getattr(
        driver_error, "sqlstate", None
    )
    if sqlstate != _UNIQUE_VIOLATION_SQLSTATE:
        return None
    return getattr(original, "constraint_name", None) or getattr(
        driver_error, "constraint_name", None
    )
