from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import RFC_4122, UUID

from app.modules.member.entities import Member
from app.modules.member.value_objects import MemberNo


class RegistrationBootstrapPersistenceError(RuntimeError):
    pass


class RegistrationBootstrapNotFound(RegistrationBootstrapPersistenceError):
    pass


class RegistrationBootstrapConflict(RegistrationBootstrapPersistenceError):
    pass


class RegistrationBootstrapUnavailable(RegistrationBootstrapPersistenceError):
    pass


class RegistrationBootstrapCommitOutcomeUnknown(
    RegistrationBootstrapPersistenceError
):
    pass


def _require_uuid7(value: object, field_name: str) -> UUID:
    if (
        type(value) is not UUID
        or value.version != 7
        or value.variant != RFC_4122
    ):
        raise ValueError(f"{field_name} must be an RFC UUID version 7")
    return value


def _require_user_ref(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("user_ref must be a positive built-in int")
    return value


def _require_token(value: object, field_name: str, *, maximum: int) -> str:
    if (
        type(value) is not str
        or value != value.strip()
        or not value
        or len(value) > maximum
    ):
        raise ValueError(f"{field_name} is invalid")
    return value


@dataclass(frozen=True, slots=True)
class RegistrationEligibilityProof:
    decision_ref: UUID
    user_ref: int
    decision: str
    policy_version: str

    def __post_init__(self) -> None:
        _require_uuid7(self.decision_ref, "decision_ref")
        _require_user_ref(self.user_ref)
        if self.decision != "APPROVED":
            raise ValueError("eligibility decision is invalid")
        _require_token(self.policy_version, "policy_version", maximum=32)


@dataclass(frozen=True, slots=True)
class RegistrationMemberNoAllocationProof:
    allocation_ref: UUID
    user_ref: int
    member_no: str

    def __post_init__(self) -> None:
        _require_uuid7(self.allocation_ref, "allocation_ref")
        _require_user_ref(self.user_ref)
        MemberNo(self.member_no)


@dataclass(frozen=True, slots=True)
class UserMemberSelfLink:
    link_id: UUID
    user_ref: int
    member_id: UUID
    eligibility_decision_ref: UUID
    establishment_basis: str
    establishment_record_ref: UUID
    source: str = "REGISTRATION_VERIFIED"

    def __post_init__(self) -> None:
        _require_uuid7(self.link_id, "link_id")
        _require_user_ref(self.user_ref)
        _require_uuid7(self.member_id, "member_id")
        _require_uuid7(
            self.eligibility_decision_ref, "eligibility_decision_ref"
        )
        if self.establishment_basis != "REGISTRATION_VERIFIED_BOOTSTRAP":
            raise ValueError("self link establishment basis is invalid")
        _require_uuid7(
            self.establishment_record_ref, "establishment_record_ref"
        )
        if self.source != "REGISTRATION_VERIFIED":
            raise ValueError("self link source is invalid")


@dataclass(frozen=True, slots=True)
class RegistrationBootstrapRecord:
    record_id: UUID
    user_ref: int
    member_id: UUID
    self_link_id: UUID
    registration_event_id: UUID
    eligibility_decision_ref: UUID
    member_no_allocation_ref: UUID
    member_no: str
    bootstrap_scope: str
    source_system: str
    source_ref: int
    decision: str
    policy_version: str
    source: str = "REGISTRATION_VERIFIED"

    def __post_init__(self) -> None:
        _require_uuid7(self.record_id, "record_id")
        _require_user_ref(self.user_ref)
        _require_uuid7(self.member_id, "member_id")
        _require_uuid7(self.self_link_id, "self_link_id")
        _require_uuid7(self.registration_event_id, "registration_event_id")
        _require_uuid7(
            self.eligibility_decision_ref, "eligibility_decision_ref"
        )
        _require_uuid7(
            self.member_no_allocation_ref, "member_no_allocation_ref"
        )
        MemberNo(self.member_no)
        if self.bootstrap_scope != "REGISTRATION_VERIFIED":
            raise ValueError("bootstrap scope is invalid")
        if self.source_system != "P1_USER":
            raise ValueError("bootstrap source system is invalid")
        _require_user_ref(self.source_ref)
        if self.source_ref != self.user_ref:
            raise ValueError("bootstrap source reference is invalid")
        if self.decision != "APPROVED":
            raise ValueError("bootstrap decision is invalid")
        _require_token(self.policy_version, "policy_version", maximum=32)
        if self.source != "REGISTRATION_VERIFIED":
            raise ValueError("bootstrap source is invalid")


@runtime_checkable
class RegistrationEligibilityProofReader(Protocol):
    async def get_current_eligible(
        self, *, decision_ref: UUID, user_ref: int
    ) -> RegistrationEligibilityProof: ...


@runtime_checkable
class RegistrationMemberNoAllocationProofReader(Protocol):
    async def get_allocated(
        self, *, allocation_ref: UUID, user_ref: int
    ) -> RegistrationMemberNoAllocationProof: ...


class RegistrationMemberStore(Protocol):
    async def get(self, member_no: str) -> Member: ...

    async def add(self, member: Member) -> None: ...


class UserMemberSelfLinkStore(Protocol):
    async def get(self, user_ref: int) -> UserMemberSelfLink: ...

    async def add(self, link: UserMemberSelfLink) -> None: ...


class RegistrationBootstrapRecordStore(Protocol):
    async def get(self, user_ref: int) -> RegistrationBootstrapRecord: ...

    async def add(self, record: RegistrationBootstrapRecord) -> None: ...


@runtime_checkable
class RegistrationBootstrapUnitOfWork(Protocol):
    members: RegistrationMemberStore
    self_links: UserMemberSelfLinkStore
    bootstrap_records: RegistrationBootstrapRecordStore

    async def __aenter__(self) -> "RegistrationBootstrapUnitOfWork": ...

    async def __aexit__(self, exc_type, exc, traceback) -> bool: ...

    async def commit(self) -> None: ...
