from __future__ import annotations

from datetime import timezone
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy import func, update

from app.core.sqlalchemy_mapping import map_core_model_classes
from app.modules.auth.eligibility_evidence_models import (
    IdentityVerificationEvidenceOrmModel,
    UserAccountClassificationEvidenceOrmModel,
)
from app.modules.auth.identity_verification_authority import (
    ManualIdentityReviewAuthorityDecision,
)
from app.modules.auth.manual_identity_review_application import (
    PlatformAdminManualIdentityReviewConflict,
    PlatformAdminManualIdentityReviewForbidden,
    PlatformAdminManualIdentityReviewNotFound,
    PlatformAdminManualIdentityReviewUnavailable,
)
from app.modules.auth.models import User
from app.modules.auth.identity_submission_models import IdentityVerificationSubmissionOrmModel
from app.modules.auth.registration_outbox import (
    P1VerificationTransitionCommand,
)
from app.modules.tenant.models import Tenant


_WRITER_AUTHORITY = "P1_MANUAL_IDENTITY_REVIEW"


class SqlAlchemyManualIdentityReviewAuthorityPort:
    def __init__(
        self,
        *,
        session_factory,
        reviewer_subject_id: int,
        user_ref: int,
        request,
        authority_decision_id: str,
    ) -> None:
        self._session_factory = session_factory
        self._reviewer_subject_id = reviewer_subject_id
        self._user_ref = user_ref
        self._request = request
        self._authority_decision_id = authority_decision_id

    async def load_current_decision(
        self, authority_decision_id: str
    ) -> ManualIdentityReviewAuthorityDecision:
        if authority_decision_id != self._authority_decision_id:
            raise PlatformAdminManualIdentityReviewConflict(
                "authority decision identity is inconsistent"
            )
        unavailable = False
        decision = None
        try:
            async with self._session_factory() as session:
                snapshot = await self._load_snapshot(session)
            decision = self._build_decision(snapshot)
        except (
            PlatformAdminManualIdentityReviewForbidden,
            PlatformAdminManualIdentityReviewNotFound,
            PlatformAdminManualIdentityReviewConflict,
        ):
            raise
        except Exception:
            unavailable = True

        if unavailable:
            raise PlatformAdminManualIdentityReviewUnavailable(
                "manual identity review authority is unavailable"
            )
        return decision

    async def _load_snapshot(self, session):
        map_core_model_classes()
        reviewer = await self._one_or_none(
            session,
            _user_projection_statement(self._reviewer_subject_id),
        )
        subject = await self._one_or_none(
            session,
            _user_projection_statement(self._user_ref),
        )
        classification = await self._scalar_one_or_none(
            session,
            select(UserAccountClassificationEvidenceOrmModel)
            .where(
                UserAccountClassificationEvidenceOrmModel.user_ref
                == self._user_ref
            )
            .order_by(
                UserAccountClassificationEvidenceOrmModel.classification_version.desc()
            )
            .limit(1),
        )
        verification = await self._scalar_one_or_none(
            session,
            select(IdentityVerificationEvidenceOrmModel)
            .where(
                IdentityVerificationEvidenceOrmModel.user_ref
                == self._user_ref
            )
            .order_by(
                IdentityVerificationEvidenceOrmModel.verification_epoch.desc()
            )
            .limit(1),
        )
        submission = None
        if self._request.submission_version is not None:
            submission = await self._scalar_one_or_none(
                session,
                select(IdentityVerificationSubmissionOrmModel).where(
                    IdentityVerificationSubmissionOrmModel.user_ref == self._user_ref,
                    IdentityVerificationSubmissionOrmModel.version == self._request.submission_version,
                ).limit(1),
            )
        return SimpleNamespace(
            reviewer=reviewer,
            subject=subject,
            classification=classification,
            verification=verification,
            submission=submission,
        )

    def _build_decision(self, snapshot):
        reviewer = snapshot.reviewer
        subject = snapshot.subject
        classification = snapshot.classification
        verification = snapshot.verification
        submission = getattr(snapshot, "submission", None)

        if reviewer is None or (
            reviewer.id != self._reviewer_subject_id
            or reviewer.role != "super_admin"
            or reviewer.status != "active"
            or reviewer.tenant_id is not None
        ):
            raise PlatformAdminManualIdentityReviewForbidden("forbidden")
        if subject is None:
            raise PlatformAdminManualIdentityReviewNotFound(
                "manual identity review subject was not found"
            )
        if (
            subject.id != self._user_ref
            or subject.role != "member"
            or subject.status != "active"
            or subject.tenant_id is not None
            or classification is None
            or getattr(classification, "user_ref", None) != self._user_ref
            or getattr(classification, "account_class", None)
            != "natural_person"
            or type(getattr(classification, "facts_version", None))
            is not int
            or classification.facts_version <= 0
        ):
            raise PlatformAdminManualIdentityReviewConflict(
                "manual identity review prerequisites are not ready"
            )

        evidence_digest = self._request.evidence_digest
        if self._request.submission_version is not None:
            if (
                submission is None
                or submission.status != "submitted"
                or submission.user_ref != self._user_ref
                or self._request.decision_basis_code != "APPROVED_OFFLINE_IDENTITY_CHECK"
            ):
                raise PlatformAdminManualIdentityReviewConflict(
                    "manual identity review submission is stale"
                )
            import hashlib
            evidence_digest = hashlib.sha256(
                (
                    f"identity-submission-review:v1:{submission.submission_id}:"
                    f"{submission.version}:{submission.content_digest}:"
                    f"{self._request.decision_basis_code}"
                ).encode()
            ).hexdigest()
        if evidence_digest is None:
            raise PlatformAdminManualIdentityReviewConflict(
                "manual identity review evidence is missing"
            )

        decision = ManualIdentityReviewAuthorityDecision(
            authority_source="manual_review",
            authority_decision_id=self._authority_decision_id,
            reviewer_subject_id=reviewer.id,
            reviewer_role=reviewer.role,
            reviewer_is_active=True,
            reviewer_tenant_scope=None,
            reviewer_org_scope="platform",
            user_ref=subject.id,
            subject_tenant_id=None,
            subject_org_id=None,
            subject_binding_started=False,
            outcome="verified",
            facts_version=classification.facts_version,
            currentness_version=1,
            verification_epoch=1,
            predecessor_currentness_version=None,
            predecessor_verification_epoch=None,
            decided_at=self._request.decided_at,
            evidence_digest=evidence_digest,
            correlation_id=self._authority_decision_id,
            is_current=True,
            revocation_reference=None,
        )

        if verification is None:
            if subject.verify_status not in {"submitted", "pending"}:
                raise PlatformAdminManualIdentityReviewConflict(
                    "manual identity review subject is not pending"
                )
            return decision

        if subject.verify_status != "verified" or not self._is_replay(
            verification, decision
        ):
            raise PlatformAdminManualIdentityReviewConflict(
                "manual identity review decision conflicts with current state"
            )
        return decision

    @staticmethod
    def _is_replay(verification, decision) -> bool:
        command = P1VerificationTransitionCommand(
            authority=_WRITER_AUTHORITY,
            case_ref=decision.authority_decision_id,
            decision_version=decision.currentness_version,
            target_facts_version=decision.facts_version,
            user_ref=decision.user_ref,
            verification_epoch=decision.verification_epoch,
            outcome=decision.outcome,
            evidence_digest=decision.evidence_digest,
            actor_type=decision.reviewer_role,
            actor_ref=str(decision.reviewer_subject_id),
            decided_at=decision.decided_at,
        )
        decided_at = getattr(verification, "decided_at", None)
        if getattr(decided_at, "tzinfo", None) is not None:
            decided_at = decided_at.astimezone(timezone.utc)
        return (
            getattr(verification, "authority_decision_key", None)
            == command.authority_decision_key
            and getattr(verification, "user_ref", None) == decision.user_ref
            and getattr(verification, "facts_version", None)
            == decision.facts_version
            and getattr(verification, "verification_epoch", None) == 1
            and getattr(verification, "outcome", None) == "verified"
            and getattr(verification, "evidence_digest", None)
            == decision.evidence_digest
            and getattr(verification, "actor_type", None) == "super_admin"
            and getattr(verification, "actor_ref", None)
            == str(decision.reviewer_subject_id)
            and decided_at == decision.decided_at
        )

    @staticmethod
    async def _one_or_none(session, statement):
        result = await session.execute(statement)
        return result.one_or_none()

    @staticmethod
    async def _scalar_one_or_none(session, statement):
        result = await session.execute(statement)
        return result.scalar_one_or_none()


def _user_projection_statement(user_ref: int):
    return (
        select(
            User.id,
            User.role,
            User.status,
            User.tenant_id,
            User.verify_status,
            User.updated_at,
        )
        .where(User.id == user_ref)
        .limit(1)
    )


def _reviewer_projection_statement(reviewer_id: int):
    return (
        select(
            User.id,
            User.role,
            User.status,
            User.tenant_id,
            Tenant.org_id.label("org_id"),
            User.verify_status,
            User.updated_at,
        )
        .outerjoin(Tenant, Tenant.id == User.tenant_id)
        .where(User.id == reviewer_id)
        .limit(1)
    )


class SqlAlchemyPlatformIdentitySubmissionReviewRepository:
    def __init__(self, session) -> None:
        self._session = session

    async def get_reviewer_state(self, reviewer_id: int):
        map_core_model_classes()
        result = await self._session.execute(
            _reviewer_projection_statement(reviewer_id)
        )
        return result.one_or_none()

    async def list_submitted(self, *, offset: int, limit: int):
        statement = (
            select(IdentityVerificationSubmissionOrmModel)
            .where(IdentityVerificationSubmissionOrmModel.status == "submitted")
            .order_by(IdentityVerificationSubmissionOrmModel.submitted_at)
            .offset(offset).limit(limit)
        )
        return list((await self._session.execute(statement)).scalars().all())

    async def count_submitted(self) -> int:
        value = await self._session.execute(
            select(func.count()).select_from(IdentityVerificationSubmissionOrmModel)
            .where(IdentityVerificationSubmissionOrmModel.status == "submitted")
        )
        return int(value.scalar_one())

    async def get_submission(self, *, user_ref: int, version: int | None = None, lock=False):
        statement = select(IdentityVerificationSubmissionOrmModel).where(
            IdentityVerificationSubmissionOrmModel.user_ref == user_ref
        )
        if version is not None:
            statement = statement.where(
                IdentityVerificationSubmissionOrmModel.version == version
            )
        statement = statement.order_by(
            IdentityVerificationSubmissionOrmModel.version.desc()
        ).limit(1)
        if lock:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def add_sensitive_read_audit(self, *, reviewer_id: int, model, purpose_code: str):
        from app.modules.system.repository import create_operation_log

        await create_operation_log(
            self._session,
            operator_id=reviewer_id,
            module="identity_review",
            object_type="identity_submission",
            object_id=model.user_ref,
            action="identity_sensitive_detail_read",
            payload={"submission_version": model.version, "purpose_code": purpose_code},
        )

    async def mark_rejected(self, *, model, reviewer_id: int, decided_at, reason_code: str, evidence_digest: str):
        result = await self._session.execute(
            update(IdentityVerificationSubmissionOrmModel)
            .where(
                IdentityVerificationSubmissionOrmModel.submission_id == model.submission_id,
                IdentityVerificationSubmissionOrmModel.status == "submitted",
                IdentityVerificationSubmissionOrmModel.version == model.version,
            )
            .values(
                status="rejected", reviewed_by=reviewer_id, decided_at=decided_at,
                rejection_reason_code=reason_code,
                decision_basis_code="REJECTED_OFFLINE_IDENTITY_CHECK",
                evidence_digest=evidence_digest,
            )
        )
        if result.rowcount != 1:
            return False
        map_core_model_classes()
        user_result = await self._session.execute(
            update(User).where(
                User.id == model.user_ref,
                User.verify_status == "submitted",
            ).values(verify_status="rejected")
        )
        return user_result.rowcount == 1

    async def mark_verified(self, *, user_ref: int, version: int, reviewer_id: int, decided_at, evidence_digest: str):
        result = await self._session.execute(
            update(IdentityVerificationSubmissionOrmModel)
            .where(
                IdentityVerificationSubmissionOrmModel.user_ref == user_ref,
                IdentityVerificationSubmissionOrmModel.version == version,
                IdentityVerificationSubmissionOrmModel.status == "submitted",
                IdentityVerificationSubmissionOrmModel.content_digest.is_not(None),
            )
            .values(
                status="verified", reviewed_by=reviewer_id, decided_at=decided_at,
                decision_basis_code="APPROVED_OFFLINE_IDENTITY_CHECK",
                evidence_digest=evidence_digest,
            )
        )
        return result.rowcount == 1

    async def commit(self):
        await self._session.commit()

    async def rollback(self):
        await self._session.rollback()
