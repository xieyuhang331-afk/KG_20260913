import asyncio

from sqlalchemy import select

from app.modules.auth.eligibility_evidence import (
    RegistrationEligibilityEvidenceInconsistent,
    RegistrationEligibilityEvidenceNotFound,
)
from app.modules.auth.eligibility_evidence_models import (
    RegistrationEligibilityDecisionEvidenceOrmModel,
)
from app.modules.auth.eligibility_evidence_repository import (
    SqlAlchemyEligibilityDecisionEvidenceReader,
)
from app.modules.auth.registration_outbox import POLICY_VERSION
from app.modules.auth.registration_outbox_worker import ConfirmationStatus
from app.modules.member.application.registration_orchestrator_ports import (
    RegistrationOrchestratorEligibilityInconsistent,
    RegistrationOrchestratorEligibilityProof,
    RegistrationOrchestratorEligibilityProofMissing,
    RegistrationOrchestratorEligibilityStale,
    RegistrationOrchestratorEligibilityUnavailable,
)

from .models import (
    MemberNoAllocationOrmModel,
    MemberOrmModel,
    RegistrationBootstrapRecordOrmModel,
    UserMemberSelfLinkOrmModel,
)


class SqlAlchemyRegistrationOrchestratorEligibilityReader:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def get_current_for_verified_transition(
        self, *, user_ref, verification_decision_ref, facts_version
    ):
        try:
            async with self._session_factory() as session:
                rows = (
                    await session.scalars(
                        select(
                            RegistrationEligibilityDecisionEvidenceOrmModel.decision_ref
                        ).where(
                            RegistrationEligibilityDecisionEvidenceOrmModel.user_ref
                            == user_ref,
                            RegistrationEligibilityDecisionEvidenceOrmModel.verification_decision_ref
                            == verification_decision_ref,
                            RegistrationEligibilityDecisionEvidenceOrmModel.facts_version
                            == facts_version,
                            RegistrationEligibilityDecisionEvidenceOrmModel.policy_version
                            == POLICY_VERSION,
                        )
                    )
                ).all()
                if not rows:
                    raise RegistrationOrchestratorEligibilityProofMissing
                if len(rows) != 1:
                    raise RegistrationOrchestratorEligibilityInconsistent
                proof = await SqlAlchemyEligibilityDecisionEvidenceReader(
                    session
                ).get_current_eligible(
                    decision_ref=rows[0], user_ref=user_ref
                )
            if (
                proof.verification_decision_ref != verification_decision_ref
                or proof.facts_version != facts_version
                or proof.policy_version != POLICY_VERSION
            ):
                raise RegistrationOrchestratorEligibilityInconsistent
            return RegistrationOrchestratorEligibilityProof(
                decision_ref=proof.decision_ref,
                user_ref=proof.user_ref,
                verification_decision_ref=proof.verification_decision_ref,
                facts_version=proof.facts_version,
            )
        except asyncio.CancelledError:
            raise
        except (
            RegistrationOrchestratorEligibilityProofMissing,
            RegistrationOrchestratorEligibilityInconsistent,
        ):
            raise
        except RegistrationEligibilityEvidenceNotFound:
            raise RegistrationOrchestratorEligibilityProofMissing from None
        except RegistrationEligibilityEvidenceInconsistent:
            raise RegistrationOrchestratorEligibilityStale from None
        except Exception:
            raise RegistrationOrchestratorEligibilityUnavailable from None


class SqlAlchemyRegistrationOrchestratorOutcomeReader:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def confirm(self, item):
        try:
            async with self._session_factory() as session:
                allocation = await session.scalar(
                    select(MemberNoAllocationOrmModel).where(
                        MemberNoAllocationOrmModel.allocation_scope
                        == "registration_bootstrap",
                        MemberNoAllocationOrmModel.source_system == "p1_user",
                        MemberNoAllocationOrmModel.source_ref == item.source_ref,
                    )
                )
                record = await session.scalar(
                    select(RegistrationBootstrapRecordOrmModel).where(
                        RegistrationBootstrapRecordOrmModel.bootstrap_scope
                        == "REGISTRATION_VERIFIED",
                        RegistrationBootstrapRecordOrmModel.source_system
                        == "P1_USER",
                        RegistrationBootstrapRecordOrmModel.source_ref
                        == item.source_ref,
                    )
                )
                link = await session.scalar(
                    select(UserMemberSelfLinkOrmModel).where(
                        UserMemberSelfLinkOrmModel.user_ref == item.source_ref
                    )
                )
                member = None
                if record is not None:
                    member = await session.get(MemberOrmModel, record.member_id)
                eligibility_rows = (
                    await session.scalars(
                        select(
                            RegistrationEligibilityDecisionEvidenceOrmModel
                        ).where(
                            RegistrationEligibilityDecisionEvidenceOrmModel.user_ref
                            == item.source_ref,
                            RegistrationEligibilityDecisionEvidenceOrmModel.verification_decision_ref
                            == item.verification_decision_ref,
                            RegistrationEligibilityDecisionEvidenceOrmModel.facts_version
                            == item.facts_version,
                            RegistrationEligibilityDecisionEvidenceOrmModel.policy_version
                            == POLICY_VERSION,
                        )
                    )
                ).all()

            trio = (record, link, member)
            if all(value is None for value in trio):
                if allocation is None:
                    return ConfirmationStatus.ABSENT
                if self._allocation_matches(allocation, item):
                    return ConfirmationStatus.ABSENT
                return ConfirmationStatus.PARTIAL_OR_UNKNOWN
            if any(value is None for value in trio):
                return ConfirmationStatus.PARTIAL_OR_UNKNOWN
            if len(eligibility_rows) != 1 or allocation is None:
                return ConfirmationStatus.PARTIAL_OR_UNKNOWN
            eligibility = eligibility_rows[0]
            return (
                ConfirmationStatus.COMPLETE
                if self._complete_matches(
                    item, allocation, record, link, member, eligibility
                )
                else ConfirmationStatus.PARTIAL_OR_UNKNOWN
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return ConfirmationStatus.PARTIAL_OR_UNKNOWN

    @staticmethod
    def _allocation_matches(allocation, item) -> bool:
        return (
            allocation.source_ref == item.source_ref
            and allocation.request_ref == item.event_id
            and allocation.state == "allocated"
            and allocation.version == 1
        )

    @classmethod
    def _complete_matches(
        cls, item, allocation, record, link, member, eligibility
    ) -> bool:
        return (
            cls._allocation_matches(allocation, item)
            and record.user_ref == item.source_ref
            and record.registration_event_id == item.event_id
            and record.eligibility_decision_ref == eligibility.decision_ref
            and record.member_no_allocation_ref == allocation.allocation_id
            and record.member_no == allocation.member_no == member.member_no
            and record.member_id == member.member_id == link.member_id
            and record.self_link_id == link.link_id
            and link.user_ref == item.source_ref
            and link.eligibility_decision_ref == eligibility.decision_ref
            and link.establishment_record_ref == record.record_id
            and member.creation_source == "registration"
            and member.status == "created"
        )

