import asyncio
import hashlib
import hmac
import json
from uuid import UUID

from app.modules.auth.registration_outbox_worker import (
    ConfirmationStatus,
    DeliveryStatus,
    RegistrationOutboxDeliveryResult,
    RegistrationOutboxWorkItem,
)

from .member_no_allocator import (
    AllocateRegistrationMemberNoCommand,
    InvalidMemberNoAllocationCommand,
    MemberNoAllocationConflict,
    MemberNoAllocationFailed,
    MemberNoAllocationOutcomeUnknown,
    MemberNoAllocationUnavailable,
)
from .registration_bootstrap import (
    InvalidRegistrationIdentityBootstrapCommand,
    RegistrationIdentityBootstrapCommand,
    RegistrationIdentityBootstrapConflict,
    RegistrationIdentityBootstrapFailed,
    RegistrationIdentityBootstrapInconsistent,
    RegistrationIdentityBootstrapOutcomeUnknown,
)
from .registration_orchestrator_ports import (
    RegistrationOrchestratorEligibilityInconsistent,
    RegistrationOrchestratorEligibilityProofMissing,
    RegistrationOrchestratorEligibilityStale,
    RegistrationOrchestratorEligibilityUnavailable,
    RegistrationOrchestratorNotEligible,
)


class RegistrationOrchestrator:
    def __init__(
        self,
        *,
        eligibility_reader,
        member_no_allocator,
        bootstrap_service,
        outcome_reader,
    ) -> None:
        self._eligibility_reader = eligibility_reader
        self._member_no_allocator = member_no_allocator
        self._bootstrap_service = bootstrap_service
        self._outcome_reader = outcome_reader

    async def deliver(
        self, item: RegistrationOutboxWorkItem
    ) -> RegistrationOutboxDeliveryResult:
        if not self._valid_input(item):
            return _result(DeliveryStatus.PERMANENT, "INVALID_ENVELOPE")
        try:
            eligibility = (
                await self._eligibility_reader.get_current_for_verified_transition(
                    user_ref=item.source_ref,
                    verification_decision_ref=item.verification_decision_ref,
                    facts_version=item.facts_version,
                )
            )
            if (
                eligibility.user_ref != item.source_ref
                or eligibility.verification_decision_ref
                != item.verification_decision_ref
                or eligibility.facts_version != item.facts_version
            ):
                raise RegistrationOrchestratorEligibilityInconsistent
            allocation = await self._member_no_allocator.allocate(
                AllocateRegistrationMemberNoCommand(
                    user_ref=item.source_ref,
                    request_ref=item.event_id,
                )
            )
            bootstrap = await self._bootstrap_service.execute(
                RegistrationIdentityBootstrapCommand(
                    user_ref=item.source_ref,
                    registration_event_id=item.event_id,
                    eligibility_decision_ref=eligibility.decision_ref,
                    member_no_allocation_ref=allocation.allocation_id,
                )
            )
            return RegistrationOutboxDeliveryResult(
                DeliveryStatus.REPLAYED
                if bootstrap.replayed
                else DeliveryStatus.COMPLETED
            )
        except asyncio.CancelledError:
            raise
        except RegistrationOrchestratorEligibilityProofMissing:
            return _result(DeliveryStatus.REVIEW_REQUIRED, "ELIGIBILITY_PROOF_MISSING")
        except RegistrationOrchestratorEligibilityStale:
            return _result(DeliveryStatus.REVIEW_REQUIRED, "ELIGIBILITY_STALE")
        except (
            RegistrationOrchestratorEligibilityInconsistent,
            RegistrationOrchestratorNotEligible,
        ):
            return _result(DeliveryStatus.REVIEW_REQUIRED, "ELIGIBILITY_INCONSISTENT")
        except RegistrationOrchestratorEligibilityUnavailable:
            return _result(DeliveryStatus.RETRYABLE, "ORCHESTRATOR_RETRYABLE")
        except InvalidMemberNoAllocationCommand:
            return _result(DeliveryStatus.PERMANENT, "INVALID_ENVELOPE")
        except MemberNoAllocationUnavailable:
            return _result(DeliveryStatus.RETRYABLE, "ORCHESTRATOR_RETRYABLE")
        except MemberNoAllocationOutcomeUnknown:
            return _result(DeliveryStatus.OUTCOME_UNKNOWN, "OUTCOME_UNKNOWN_UNCONFIRMED")
        except MemberNoAllocationConflict:
            return _result(DeliveryStatus.REVIEW_REQUIRED, "BOOTSTRAP_CONFLICT")
        except MemberNoAllocationFailed:
            return _result(DeliveryStatus.INTERNAL_UNKNOWN, "UNEXPECTED_INTERNAL")
        except InvalidRegistrationIdentityBootstrapCommand:
            return _result(DeliveryStatus.PERMANENT, "INVALID_ENVELOPE")
        except (
            RegistrationIdentityBootstrapConflict,
            RegistrationIdentityBootstrapInconsistent,
        ):
            return _result(DeliveryStatus.REVIEW_REQUIRED, "BOOTSTRAP_CONFLICT")
        except RegistrationIdentityBootstrapOutcomeUnknown:
            return _result(DeliveryStatus.OUTCOME_UNKNOWN, "OUTCOME_UNKNOWN_UNCONFIRMED")
        except RegistrationIdentityBootstrapFailed:
            return _result(DeliveryStatus.INTERNAL_UNKNOWN, "UNEXPECTED_INTERNAL")
        except Exception:
            return _result(DeliveryStatus.INTERNAL_UNKNOWN, "UNEXPECTED_INTERNAL")

    async def confirm(
        self, item: RegistrationOutboxWorkItem
    ) -> ConfirmationStatus:
        if not self._valid_input(item):
            return ConfirmationStatus.PARTIAL_OR_UNKNOWN
        try:
            result = await self._outcome_reader.confirm(item)
        except asyncio.CancelledError:
            raise
        except Exception:
            return ConfirmationStatus.PARTIAL_OR_UNKNOWN
        if type(result) is not ConfirmationStatus:
            return ConfirmationStatus.PARTIAL_OR_UNKNOWN
        return result

    @staticmethod
    def _valid_input(item: object) -> bool:
        if not (
            type(item) is RegistrationOutboxWorkItem
            and item.event_type
            == "identity.registration.verification_verified"
            and item.event_schema_version == 1
            and item.source_system == "P1_USER"
            and type(item.source_ref) is int
            and item.source_ref > 0
            and type(item.facts_version) is int
            and item.facts_version > 0
            and type(item.event_id) is UUID
            and item.event_id.version == 7
            and type(item.verification_decision_ref) is UUID
            and item.verification_decision_ref.version == 7
            and type(item.authority_decision_key) is str
            and len(item.authority_decision_key) == 64
            and all(
                character in "0123456789abcdef"
                for character in item.authority_decision_key
            )
            and item.trace_ref is None
        ):
            return False
        semantic_key = (
            "identity.registration.verification_verified:v1:"
            f"p1_user:{item.source_ref}:authority:"
            f"{item.authority_decision_key}"
        )
        if item.semantic_idempotency_key != semantic_key:
            return False
        canonical = json.dumps(
            {
                "authority_decision_key": item.authority_decision_key,
                "event_id": str(item.event_id),
                "event_schema_version": item.event_schema_version,
                "event_type": item.event_type,
                "facts_version": item.facts_version,
                "occurred_at": item.occurred_at.isoformat(),
                "source_ref": item.source_ref,
                "source_system": item.source_system,
                "trace_ref": None,
                "verification_decision_ref": str(
                    item.verification_decision_ref
                ),
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        expected_digest = hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()
        return (
            type(item.payload_digest) is str
            and hmac.compare_digest(item.payload_digest, expected_digest)
        )


def _result(status: DeliveryStatus, code: str) -> RegistrationOutboxDeliveryResult:
    return RegistrationOutboxDeliveryResult(
        status=status,
        error_code=code,
        error_digest=hashlib.sha256(code.encode("ascii")).hexdigest(),
    )
