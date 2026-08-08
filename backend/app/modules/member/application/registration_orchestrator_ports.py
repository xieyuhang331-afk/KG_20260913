from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.modules.auth.registration_outbox_worker import (
    ConfirmationStatus,
    RegistrationOutboxWorkItem,
)


class RegistrationOrchestratorEligibilityError(RuntimeError):
    pass


class RegistrationOrchestratorNotEligible(
    RegistrationOrchestratorEligibilityError
):
    pass


class RegistrationOrchestratorEligibilityProofMissing(
    RegistrationOrchestratorNotEligible
):
    pass


class RegistrationOrchestratorEligibilityStale(
    RegistrationOrchestratorNotEligible
):
    pass


class RegistrationOrchestratorEligibilityInconsistent(
    RegistrationOrchestratorNotEligible
):
    pass


class RegistrationOrchestratorEligibilityUnavailable(
    RegistrationOrchestratorEligibilityError
):
    pass


@dataclass(frozen=True, slots=True)
class RegistrationOrchestratorEligibilityProof:
    decision_ref: UUID
    user_ref: int
    verification_decision_ref: UUID
    facts_version: int


class RegistrationOrchestratorEligibilityReader(Protocol):
    async def get_current_for_verified_transition(
        self,
        *,
        user_ref: int,
        verification_decision_ref: UUID,
        facts_version: int,
    ) -> RegistrationOrchestratorEligibilityProof: ...


class RegistrationOrchestratorOutcomeReader(Protocol):
    async def confirm(
        self, item: RegistrationOutboxWorkItem
    ) -> ConfirmationStatus: ...
