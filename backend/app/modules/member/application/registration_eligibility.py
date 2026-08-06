from dataclasses import dataclass
from enum import Enum
from uuid import UUID


class RegistrationUserRole(str, Enum):
    MEMBER = "member"
    OTHER = "other"


class RegistrationAccountStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    SUSPENDED = "suspended"


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    SUBMITTED = "submitted"
    UNVERIFIED = "unverified"
    FAILED = "failed"


class VerificationOutcome(str, Enum):
    VERIFIED = "verified"
    FAILED = "failed"
    UNKNOWN = "unknown"


class AccountClass(str, Enum):
    NATURAL_PERSON = "natural_person"
    STAFF = "staff"
    SERVICE = "service"
    TEST = "test"
    AUTOMATION = "automation"
    UNKNOWN = "unknown"


class EligibilityDecision(str, Enum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    INDETERMINATE = "indeterminate"


class EligibilityReason(str, Enum):
    ELIGIBLE = "eligible"
    UNSUPPORTED_FACTS = "unsupported_facts"
    USER_NOT_FOUND = "user_not_found"
    FACTS_UNAVAILABLE = "facts_unavailable"
    ROLE_NOT_MEMBER = "role_not_member"
    ACCOUNT_NOT_ACTIVE = "account_not_active"
    IDENTITY_NOT_VERIFIED = "identity_not_verified"
    VERIFICATION_FACTS_INCONSISTENT = "verification_facts_inconsistent"
    CLASSIFICATION_FACTS_INCONSISTENT = "classification_facts_inconsistent"
    FACTS_STALE = "facts_stale"
    ACCOUNT_CLASS_FORBIDDEN = "account_class_forbidden"
    ACCOUNT_CLASS_UNKNOWN = "account_class_unknown"


@dataclass(frozen=True, slots=True)
class TrustedRegistrationEligibilityFacts:
    user_ref: int
    exists: bool
    facts_version: int
    role: RegistrationUserRole | None = None
    status: RegistrationAccountStatus | None = None
    verify_status: VerificationStatus | None = None
    verification_decision_ref: UUID | None = None
    verification_subject_user_ref: int | None = None
    verification_outcome: VerificationOutcome | None = None
    verification_epoch: int | None = None
    verification_is_current: bool | None = None
    account_class: AccountClass | None = None
    classification_decision_ref: UUID | None = None
    classification_subject_user_ref: int | None = None
    classification_version: int | None = None
    classification_is_current: bool | None = None


@dataclass(frozen=True, slots=True)
class RegistrationEligibilityResult:
    decision: EligibilityDecision
    reason: EligibilityReason


class RegistrationEligibilityPolicy:
    @classmethod
    def evaluate(
        cls, facts: TrustedRegistrationEligibilityFacts
    ) -> RegistrationEligibilityResult:
        if not cls._valid_envelope(facts):
            return cls._result(
                EligibilityDecision.INDETERMINATE,
                EligibilityReason.UNSUPPORTED_FACTS,
            )
        if facts.exists is False:
            return cls._result(
                EligibilityDecision.INELIGIBLE,
                EligibilityReason.USER_NOT_FOUND,
            )
        if cls._basic_facts_missing(facts):
            return cls._result(
                EligibilityDecision.INDETERMINATE,
                EligibilityReason.FACTS_UNAVAILABLE,
            )
        if not cls._basic_facts_typed(facts):
            return cls._result(
                EligibilityDecision.INDETERMINATE,
                EligibilityReason.UNSUPPORTED_FACTS,
            )
        if facts.role is not RegistrationUserRole.MEMBER:
            return cls._result(
                EligibilityDecision.INELIGIBLE,
                EligibilityReason.ROLE_NOT_MEMBER,
            )
        if facts.status is not RegistrationAccountStatus.ACTIVE:
            return cls._result(
                EligibilityDecision.INELIGIBLE,
                EligibilityReason.ACCOUNT_NOT_ACTIVE,
            )
        if facts.verify_status is not VerificationStatus.VERIFIED:
            return cls._result(
                EligibilityDecision.INELIGIBLE,
                EligibilityReason.IDENTITY_NOT_VERIFIED,
            )

        verification_result = cls._validate_verification(facts)
        if verification_result is not None:
            return verification_result

        classification_result = cls._validate_classification(facts)
        if classification_result is not None:
            return classification_result

        return cls._result(
            EligibilityDecision.ELIGIBLE,
            EligibilityReason.ELIGIBLE,
        )

    @staticmethod
    def _valid_envelope(facts: object) -> bool:
        return (
            type(facts) is TrustedRegistrationEligibilityFacts
            and type(facts.user_ref) is int
            and facts.user_ref > 0
            and type(facts.exists) is bool
            and type(facts.facts_version) is int
            and facts.facts_version > 0
        )

    @staticmethod
    def _basic_facts_missing(
        facts: TrustedRegistrationEligibilityFacts,
    ) -> bool:
        return (
            facts.role is None
            or facts.status is None
            or facts.verify_status is None
        )

    @staticmethod
    def _basic_facts_typed(
        facts: TrustedRegistrationEligibilityFacts,
    ) -> bool:
        return (
            type(facts.role) is RegistrationUserRole
            and type(facts.status) is RegistrationAccountStatus
            and type(facts.verify_status) is VerificationStatus
        )

    @classmethod
    def _validate_verification(
        cls, facts: TrustedRegistrationEligibilityFacts
    ) -> RegistrationEligibilityResult | None:
        values = (
            facts.verification_decision_ref,
            facts.verification_subject_user_ref,
            facts.verification_outcome,
            facts.verification_epoch,
            facts.verification_is_current,
        )
        if any(value is None for value in values):
            return cls._result(
                EligibilityDecision.INDETERMINATE,
                EligibilityReason.VERIFICATION_FACTS_INCONSISTENT,
            )
        if not (
            cls._is_uuid7(facts.verification_decision_ref)
            and type(facts.verification_subject_user_ref) is int
            and facts.verification_subject_user_ref > 0
            and type(facts.verification_outcome) is VerificationOutcome
            and type(facts.verification_epoch) is int
            and facts.verification_epoch > 0
            and type(facts.verification_is_current) is bool
        ):
            return cls._result(
                EligibilityDecision.INDETERMINATE,
                EligibilityReason.UNSUPPORTED_FACTS,
            )
        if facts.verification_is_current is False:
            return cls._result(
                EligibilityDecision.INDETERMINATE,
                EligibilityReason.FACTS_STALE,
            )
        if (
            facts.verification_subject_user_ref != facts.user_ref
            or facts.verification_outcome is not VerificationOutcome.VERIFIED
        ):
            return cls._result(
                EligibilityDecision.INDETERMINATE,
                EligibilityReason.VERIFICATION_FACTS_INCONSISTENT,
            )
        return None

    @classmethod
    def _validate_classification(
        cls, facts: TrustedRegistrationEligibilityFacts
    ) -> RegistrationEligibilityResult | None:
        if facts.account_class is None:
            return cls._result(
                EligibilityDecision.INDETERMINATE,
                EligibilityReason.ACCOUNT_CLASS_UNKNOWN,
            )
        if type(facts.account_class) is not AccountClass:
            return cls._result(
                EligibilityDecision.INDETERMINATE,
                EligibilityReason.UNSUPPORTED_FACTS,
            )

        values = (
            facts.classification_decision_ref,
            facts.classification_subject_user_ref,
            facts.classification_version,
            facts.classification_is_current,
        )
        if any(value is None for value in values):
            return cls._result(
                EligibilityDecision.INDETERMINATE,
                EligibilityReason.CLASSIFICATION_FACTS_INCONSISTENT,
            )
        if not (
            cls._is_uuid7(facts.classification_decision_ref)
            and type(facts.classification_subject_user_ref) is int
            and facts.classification_subject_user_ref > 0
            and type(facts.classification_version) is int
            and facts.classification_version > 0
            and type(facts.classification_is_current) is bool
        ):
            return cls._result(
                EligibilityDecision.INDETERMINATE,
                EligibilityReason.UNSUPPORTED_FACTS,
            )
        if facts.classification_is_current is False:
            return cls._result(
                EligibilityDecision.INDETERMINATE,
                EligibilityReason.FACTS_STALE,
            )
        if facts.classification_subject_user_ref != facts.user_ref:
            return cls._result(
                EligibilityDecision.INDETERMINATE,
                EligibilityReason.CLASSIFICATION_FACTS_INCONSISTENT,
            )
        if facts.account_class is AccountClass.UNKNOWN:
            return cls._result(
                EligibilityDecision.INDETERMINATE,
                EligibilityReason.ACCOUNT_CLASS_UNKNOWN,
            )
        if facts.account_class is not AccountClass.NATURAL_PERSON:
            return cls._result(
                EligibilityDecision.INELIGIBLE,
                EligibilityReason.ACCOUNT_CLASS_FORBIDDEN,
            )
        return None

    @staticmethod
    def _is_uuid7(value: object) -> bool:
        return type(value) is UUID and value.version == 7

    @staticmethod
    def _result(
        decision: EligibilityDecision, reason: EligibilityReason
    ) -> RegistrationEligibilityResult:
        return RegistrationEligibilityResult(decision=decision, reason=reason)
