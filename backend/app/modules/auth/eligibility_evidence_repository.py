from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.sqlalchemy_mapping import map_core_model_classes
from app.modules.auth.models import User
from app.modules.member.application.registration_eligibility import (
    AccountClass,
    EligibilityDecision,
    EligibilityReason,
    RegistrationAccountStatus,
    RegistrationUserRole,
    TrustedRegistrationEligibilityFacts,
    VerificationOutcome,
    VerificationStatus,
)

from .eligibility_evidence import (
    CurrentEligibilityDecisionProof,
    IdentityVerificationEvidence,
    RegistrationEligibilityDecisionEvidence,
    RegistrationEligibilityEvidenceConflict,
    RegistrationEligibilityEvidenceInconsistent,
    RegistrationEligibilityEvidenceNotFound,
    RegistrationEligibilityEvidenceUnavailable,
    RegistrationEligibilityFactsSnapshot,
    UserAccountClassificationEvidence,
    build_p1_projection_digest,
)
from .eligibility_evidence_models import (
    IdentityVerificationEvidenceOrmModel,
    RegistrationEligibilityDecisionEvidenceOrmModel,
    UserAccountClassificationEvidenceOrmModel,
)


_UNIQUE_VIOLATION_SQLSTATE = "23505"
_BUSINESS_CONFLICT_CONSTRAINTS = frozenset(
    {
        "uq_identity_verification_user_epoch",
        "uq_identity_verification_user_facts",
        "uq_identity_verification_single_successor",
        "uq_account_classification_user_version",
        "uq_account_classification_user_facts",
        "uq_account_classification_single_successor",
        "uq_registration_eligibility_canonical_key",
    }
)


class SqlAlchemyRegistrationEligibilityEvidenceStore:
    def __init__(self, session) -> None:
        self._session = session

    async def add_verification(
        self, evidence: IdentityVerificationEvidence
    ) -> None:
        latest = await self._latest_verification(evidence.user_ref)
        if latest is None:
            if evidence.supersedes_ref is not None:
                raise RegistrationEligibilityEvidenceConflict(
                    "first verification evidence cannot supersede a decision"
                )
        elif not (
            evidence.supersedes_ref == latest.decision_ref
            and evidence.verification_epoch > latest.verification_epoch
            and evidence.facts_version > latest.facts_version
        ):
            raise RegistrationEligibilityEvidenceConflict(
                "verification evidence does not supersede the current decision"
            )
        model = IdentityVerificationEvidenceOrmModel(
            decision_ref=evidence.decision_ref,
            user_ref=evidence.user_ref,
            facts_version=evidence.facts_version,
            verification_epoch=evidence.verification_epoch,
            outcome=evidence.outcome.value,
            evidence_digest=evidence.evidence_digest,
            actor_type=evidence.actor_type,
            actor_ref=evidence.actor_ref,
            supersedes_ref=evidence.supersedes_ref,
            decided_at=evidence.decided_at,
        )
        await self._add(model)

    async def add_classification(
        self, evidence: UserAccountClassificationEvidence
    ) -> None:
        latest = await self._latest_classification(evidence.user_ref)
        if latest is None:
            if evidence.supersedes_ref is not None:
                raise RegistrationEligibilityEvidenceConflict(
                    "first classification evidence cannot supersede a decision"
                )
        elif not (
            evidence.supersedes_ref == latest.decision_ref
            and evidence.classification_version > latest.classification_version
            and evidence.facts_version > latest.facts_version
        ):
            raise RegistrationEligibilityEvidenceConflict(
                "classification evidence does not supersede the current decision"
            )
        model = UserAccountClassificationEvidenceOrmModel(
            decision_ref=evidence.decision_ref,
            user_ref=evidence.user_ref,
            facts_version=evidence.facts_version,
            classification_version=evidence.classification_version,
            account_class=evidence.account_class.value,
            decision_basis_code=evidence.decision_basis_code,
            supersedes_ref=evidence.supersedes_ref,
            decided_at=evidence.decided_at,
        )
        await self._add(model)

    async def add_decision(
        self, evidence: RegistrationEligibilityDecisionEvidence
    ) -> None:
        model = RegistrationEligibilityDecisionEvidenceOrmModel(
            decision_ref=evidence.decision_ref,
            user_ref=evidence.user_ref,
            facts_version=evidence.facts_version,
            verification_decision_ref=evidence.verification_decision_ref,
            classification_decision_ref=evidence.classification_decision_ref,
            policy_version=evidence.policy_version,
            facts_digest=evidence.facts_digest,
            p1_projection_digest=evidence.p1_projection_digest,
            decision=evidence.decision.value,
            reason=evidence.reason.value,
            decided_at=evidence.decided_at,
        )
        await self._add(model)

    async def get_decision_by_key(
        self, *, user_ref: int, facts_version: int, policy_version: str
    ) -> RegistrationEligibilityDecisionEvidence | None:
        statement = select(
            RegistrationEligibilityDecisionEvidenceOrmModel
        ).where(
            RegistrationEligibilityDecisionEvidenceOrmModel.user_ref
            == user_ref,
            RegistrationEligibilityDecisionEvidenceOrmModel.facts_version
            == facts_version,
            RegistrationEligibilityDecisionEvidenceOrmModel.policy_version
            == policy_version,
        )
        pending_error = None
        restored = None
        try:
            result = await self._session.execute(statement)
            row = result.scalar_one_or_none()
            restored = None if row is None else _restore_decision(row)
        except Exception:
            pending_error = RegistrationEligibilityEvidenceUnavailable(
                "eligibility evidence persistence is unavailable"
            )
        if pending_error is not None:
            raise pending_error
        return restored

    async def _latest_verification(
        self, user_ref: int
    ) -> IdentityVerificationEvidence | None:
        statement = (
            select(IdentityVerificationEvidenceOrmModel)
            .where(IdentityVerificationEvidenceOrmModel.user_ref == user_ref)
            .order_by(
                IdentityVerificationEvidenceOrmModel.verification_epoch.desc()
            )
            .limit(1)
        )
        pending_error = None
        restored = None
        try:
            result = await self._session.execute(statement)
            row = result.scalar_one_or_none()
            restored = None if row is None else _restore_verification(row)
        except Exception:
            pending_error = RegistrationEligibilityEvidenceUnavailable(
                "eligibility evidence persistence is unavailable"
            )
        if pending_error is not None:
            raise pending_error
        return restored

    async def _latest_classification(
        self, user_ref: int
    ) -> UserAccountClassificationEvidence | None:
        statement = (
            select(UserAccountClassificationEvidenceOrmModel)
            .where(
                UserAccountClassificationEvidenceOrmModel.user_ref == user_ref
            )
            .order_by(
                UserAccountClassificationEvidenceOrmModel.classification_version.desc()
            )
            .limit(1)
        )
        pending_error = None
        restored = None
        try:
            result = await self._session.execute(statement)
            row = result.scalar_one_or_none()
            restored = None if row is None else _restore_classification(row)
        except Exception:
            pending_error = RegistrationEligibilityEvidenceUnavailable(
                "eligibility evidence persistence is unavailable"
            )
        if pending_error is not None:
            raise pending_error
        return restored

    async def _add(self, model) -> None:
        pending_error = None
        try:
            self._session.add(model)
            await self._session.flush()
        except IntegrityError as exc:
            if _known_business_conflict_constraint(exc) is not None:
                pending_error = RegistrationEligibilityEvidenceConflict(
                    "eligibility evidence conflicts with persisted state"
                )
            else:
                pending_error = RegistrationEligibilityEvidenceUnavailable(
                    "eligibility evidence persistence is unavailable"
                )
        except Exception:
            pending_error = RegistrationEligibilityEvidenceUnavailable(
                "eligibility evidence persistence is unavailable"
            )
        if pending_error is not None:
            raise pending_error


class SqlAlchemyRegistrationEligibilityFactsReader:
    def __init__(self, session) -> None:
        self._session = session

    async def get_current(
        self, user_ref: int
    ) -> RegistrationEligibilityFactsSnapshot:
        map_core_model_classes()
        pending_error = None
        try:
            user_result = await self._session.execute(
                select(User).where(User.id == user_ref).limit(1)
            )
            user = user_result.scalar_one_or_none()
            verification = await _fetch_latest_verification(
                self._session, user_ref
            )
            classification = await _fetch_latest_classification(
                self._session, user_ref
            )
        except Exception:
            pending_error = RegistrationEligibilityEvidenceUnavailable(
                "registration eligibility facts are unavailable"
            )
        if pending_error is not None:
            raise pending_error

        if user is None:
            return RegistrationEligibilityFactsSnapshot(
                facts=TrustedRegistrationEligibilityFacts(
                    user_ref=user_ref, exists=False, facts_version=1
                ),
                p1_projection_digest=build_p1_projection_digest(
                    user_ref=user_ref,
                    role=None,
                    status=None,
                    verify_status=None,
                    updated_at=None,
                ),
            )
        if verification is None or classification is None:
            raise RegistrationEligibilityEvidenceInconsistent(
                "registration eligibility evidence is incomplete"
            )
        if verification.facts_version != classification.facts_version:
            raise RegistrationEligibilityEvidenceInconsistent(
                "registration eligibility evidence versions disagree"
            )

        projection_error = None
        try:
            role = (
                RegistrationUserRole.MEMBER
                if user.role == "member"
                else RegistrationUserRole.OTHER
            )
            status = RegistrationAccountStatus(user.status)
            verify_status = VerificationStatus(user.verify_status)
        except (TypeError, ValueError):
            projection_error = RegistrationEligibilityEvidenceInconsistent(
                "registration user projection is unsupported"
            )
        if projection_error is not None:
            raise projection_error

        return RegistrationEligibilityFactsSnapshot(
            facts=TrustedRegistrationEligibilityFacts(
                user_ref=user_ref,
                exists=True,
                facts_version=verification.facts_version,
                role=role,
                status=status,
                verify_status=verify_status,
                verification_decision_ref=verification.decision_ref,
                verification_subject_user_ref=user_ref,
                verification_outcome=verification.outcome,
                verification_epoch=verification.verification_epoch,
                verification_is_current=True,
                account_class=classification.account_class,
                classification_decision_ref=classification.decision_ref,
                classification_subject_user_ref=user_ref,
                classification_version=classification.classification_version,
                classification_is_current=True,
            ),
            p1_projection_digest=_projection_digest(user_ref, user),
        )


class SqlAlchemyEligibilityDecisionEvidenceReader:
    def __init__(self, session) -> None:
        self._session = session

    async def get_current_eligible(
        self, *, decision_ref: UUID, user_ref: int
    ) -> CurrentEligibilityDecisionProof:
        map_core_model_classes()
        pending_error = None
        try:
            result = await self._session.execute(
                select(RegistrationEligibilityDecisionEvidenceOrmModel).where(
                    RegistrationEligibilityDecisionEvidenceOrmModel.decision_ref
                    == decision_ref,
                    RegistrationEligibilityDecisionEvidenceOrmModel.user_ref
                    == user_ref,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise RegistrationEligibilityEvidenceNotFound(
                    "eligibility decision evidence was not found"
                )
            latest_result = await self._session.execute(
                select(RegistrationEligibilityDecisionEvidenceOrmModel)
                .where(
                    RegistrationEligibilityDecisionEvidenceOrmModel.user_ref
                    == user_ref,
                    RegistrationEligibilityDecisionEvidenceOrmModel.policy_version
                    == row.policy_version,
                )
                .order_by(
                    RegistrationEligibilityDecisionEvidenceOrmModel.facts_version.desc()
                )
                .limit(1)
            )
            latest = latest_result.scalar_one()
            verification = await _fetch_latest_verification(
                self._session, user_ref
            )
            classification = await _fetch_latest_classification(
                self._session, user_ref
            )
            user_result = await self._session.execute(
                select(User)
                .where(User.id == user_ref)
                .execution_options(populate_existing=True)
            )
            user = user_result.scalar_one_or_none()
        except (
            RegistrationEligibilityEvidenceNotFound,
            RegistrationEligibilityEvidenceInconsistent,
        ):
            raise
        except Exception:
            pending_error = RegistrationEligibilityEvidenceUnavailable(
                "eligibility decision evidence is unavailable"
            )
        if pending_error is not None:
            raise pending_error

        if (
            latest.decision_ref != row.decision_ref
            or row.decision != EligibilityDecision.ELIGIBLE.value
            or verification is None
            or classification is None
            or row.verification_decision_ref != verification.decision_ref
            or row.classification_decision_ref != classification.decision_ref
            or row.facts_version != verification.facts_version
            or row.facts_version != classification.facts_version
            or verification.outcome is not VerificationOutcome.VERIFIED
            or classification.account_class is not AccountClass.NATURAL_PERSON
            or row.reason != EligibilityReason.ELIGIBLE.value
            or user is None
            or user.role != "member"
            or user.status != "active"
            or user.verify_status != "verified"
            or row.p1_projection_digest != _projection_digest(user_ref, user)
        ):
            raise RegistrationEligibilityEvidenceInconsistent(
                "eligibility decision evidence is not current and eligible"
            )
        return CurrentEligibilityDecisionProof(
            decision_ref=UUID(int=row.decision_ref.int),
            user_ref=row.user_ref,
            facts_version=row.facts_version,
            verification_decision_ref=UUID(
                int=row.verification_decision_ref.int
            ),
            classification_decision_ref=UUID(
                int=row.classification_decision_ref.int
            ),
            policy_version=row.policy_version,
            facts_digest=row.facts_digest,
            p1_projection_digest=row.p1_projection_digest,
            decided_at=row.decided_at,
        )


def _known_business_conflict_constraint(
    exc: IntegrityError,
) -> str | None:
    original = exc.orig
    driver_error = getattr(original, "__cause__", None)
    sqlstate = getattr(original, "sqlstate", None) or getattr(
        driver_error, "sqlstate", None
    )
    if sqlstate != _UNIQUE_VIOLATION_SQLSTATE:
        return None
    constraint_name = getattr(
        original, "constraint_name", None
    ) or getattr(driver_error, "constraint_name", None)
    if constraint_name not in _BUSINESS_CONFLICT_CONSTRAINTS:
        return None
    return constraint_name


async def _fetch_latest_verification(session, user_ref: int):
    result = await session.execute(
        select(IdentityVerificationEvidenceOrmModel)
        .where(IdentityVerificationEvidenceOrmModel.user_ref == user_ref)
        .order_by(
            IdentityVerificationEvidenceOrmModel.verification_epoch.desc()
        )
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return None if row is None else _restore_verification(row)


async def _fetch_latest_classification(session, user_ref: int):
    result = await session.execute(
        select(UserAccountClassificationEvidenceOrmModel)
        .where(UserAccountClassificationEvidenceOrmModel.user_ref == user_ref)
        .order_by(
            UserAccountClassificationEvidenceOrmModel.classification_version.desc()
        )
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return None if row is None else _restore_classification(row)


def _restore_verification(row) -> IdentityVerificationEvidence:
    return IdentityVerificationEvidence(
        decision_ref=UUID(int=row.decision_ref.int),
        user_ref=row.user_ref,
        facts_version=row.facts_version,
        verification_epoch=row.verification_epoch,
        outcome=VerificationOutcome(row.outcome),
        evidence_digest=row.evidence_digest,
        actor_type=row.actor_type,
        actor_ref=row.actor_ref,
        supersedes_ref=(
            None
            if row.supersedes_ref is None
            else UUID(int=row.supersedes_ref.int)
        ),
        decided_at=row.decided_at,
    )


def _restore_classification(row) -> UserAccountClassificationEvidence:
    return UserAccountClassificationEvidence(
        decision_ref=UUID(int=row.decision_ref.int),
        user_ref=row.user_ref,
        facts_version=row.facts_version,
        classification_version=row.classification_version,
        account_class=AccountClass(row.account_class),
        decision_basis_code=row.decision_basis_code,
        supersedes_ref=(
            None
            if row.supersedes_ref is None
            else UUID(int=row.supersedes_ref.int)
        ),
        decided_at=row.decided_at,
    )


def _restore_decision(row) -> RegistrationEligibilityDecisionEvidence:
    return RegistrationEligibilityDecisionEvidence(
        decision_ref=UUID(int=row.decision_ref.int),
        user_ref=row.user_ref,
        facts_version=row.facts_version,
        verification_decision_ref=UUID(
            int=row.verification_decision_ref.int
        ),
        classification_decision_ref=UUID(
            int=row.classification_decision_ref.int
        ),
        policy_version=row.policy_version,
        facts_digest=row.facts_digest,
        p1_projection_digest=row.p1_projection_digest,
        decision=EligibilityDecision(row.decision),
        reason=EligibilityReason(row.reason),
        decided_at=row.decided_at,
    )


def _projection_digest(user_ref: int, user) -> str:
    return build_p1_projection_digest(
        user_ref=user_ref,
        role=user.role,
        status=user.status,
        verify_status=user.verify_status,
        updated_at=user.updated_at,
    )
