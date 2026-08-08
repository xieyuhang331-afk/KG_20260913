from __future__ import annotations

from app.modules.auth.identity_verification_authority import (
    IdentityVerificationAuthorityError,
    IdentityVerificationAuthorityPort,
    IdentityVerificationAuthorityRejected,
    IdentityVerificationAuthorityUnavailable,
    InvalidIdentityVerificationAuthorityDecision,
    ManualIdentityReviewAuthorityDecision,
)
from app.modules.auth.registration_outbox import (
    P1VerificationTransitionCommand,
)


WRITER_AUTHORITY = "P1_MANUAL_IDENTITY_REVIEW"


def _require_decision_id(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > 128
    ):
        raise InvalidIdentityVerificationAuthorityDecision(
            "authority_decision_id is invalid"
        )
    return value


class ManualIdentityReviewVerifiedTransitionService:
    def __init__(self, *, authority_port, transition_writer) -> None:
        self._authority_port = authority_port
        self._transition_writer = transition_writer

    async def execute(self, authority_decision_id: str):
        decision_id = _require_decision_id(authority_decision_id)
        failed = False
        decision = None
        try:
            decision = await self._authority_port.load_current_decision(
                decision_id
            )
        except IdentityVerificationAuthorityError:
            raise
        except Exception:
            failed = True

        if failed:
            raise IdentityVerificationAuthorityUnavailable(
                "identity verification authority is unavailable"
            )
        if type(decision) is not ManualIdentityReviewAuthorityDecision:
            raise IdentityVerificationAuthorityRejected(
                "identity verification authority decision is rejected"
            )
        if decision.authority_decision_id != decision_id:
            raise IdentityVerificationAuthorityRejected(
                "identity verification authority decision is rejected"
            )

        command = P1VerificationTransitionCommand(
            authority=WRITER_AUTHORITY,
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
        return await self._transition_writer.execute(command)

