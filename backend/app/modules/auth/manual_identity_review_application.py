from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib

from app.modules.auth.identity_verification_authority import (
    IdentityVerificationAuthorityRejected,
    IdentityVerificationAuthorityUnavailable,
    InvalidIdentityVerificationAuthorityDecision,
)
from app.modules.auth.registration_outbox import (
    P1VerificationTransitionInconsistent,
    P1VerificationTransitionNotReady,
    P1VerificationTransitionOutcomeUnknown,
    P1VerificationTransitionUnavailable,
)


class PlatformAdminManualIdentityReviewError(RuntimeError):
    pass


class PlatformAdminManualIdentityReviewForbidden(
    PlatformAdminManualIdentityReviewError
):
    pass


class PlatformAdminManualIdentityReviewNotFound(
    PlatformAdminManualIdentityReviewError
):
    pass


class PlatformAdminManualIdentityReviewConflict(
    PlatformAdminManualIdentityReviewError
):
    pass


class PlatformAdminManualIdentityReviewUnavailable(
    PlatformAdminManualIdentityReviewError
):
    pass


@dataclass(frozen=True, slots=True)
class PlatformAdminManualIdentityReviewRequest:
    idempotency_key: str
    decided_at: datetime
    evidence_digest: str

    def __post_init__(self) -> None:
        if (
            type(self.idempotency_key) is not str
            or not self.idempotency_key
            or self.idempotency_key != self.idempotency_key.strip()
            or len(self.idempotency_key) > 128
        ):
            raise ValueError("idempotency_key is invalid")
        if (
            type(self.decided_at) is not datetime
            or self.decided_at.tzinfo is None
            or self.decided_at.utcoffset()
            != timezone.utc.utcoffset(self.decided_at)
        ):
            raise ValueError("decided_at must be an aware UTC datetime")
        if (
            type(self.evidence_digest) is not str
            or len(self.evidence_digest) != 64
            or self.evidence_digest != self.evidence_digest.lower()
            or any(
                character not in "0123456789abcdef"
                for character in self.evidence_digest
            )
        ):
            raise ValueError("evidence_digest is invalid")


def _authority_decision_id(user_ref: int, idempotency_key: str) -> str:
    digest = hashlib.sha256(
        f"{user_ref}:{idempotency_key}".encode("utf-8")
    ).hexdigest()
    return f"manual-review-{digest}"


class PlatformAdminManualIdentityReviewService:
    def __init__(
        self,
        *,
        authority_port_factory,
        transition_service_factory,
        session_factory=None,
        uuid_generator=None,
    ) -> None:
        self._authority_port_factory = authority_port_factory
        self._transition_service_factory = transition_service_factory
        self._session_factory = session_factory
        self._uuid_generator = uuid_generator

    async def execute(self, *, current_user, user_ref: int, request):
        self._require_reviewer(current_user, user_ref)
        if type(request) is not PlatformAdminManualIdentityReviewRequest:
            raise PlatformAdminManualIdentityReviewConflict(
                "manual identity review request is invalid"
            )

        authority_decision_id = _authority_decision_id(
            user_ref, request.idempotency_key
        )
        conflict = False
        unavailable = False
        result = None
        try:
            authority_port = self._authority_port_factory(
                reviewer_subject_id=current_user.id,
                user_ref=user_ref,
                request=request,
                authority_decision_id=authority_decision_id,
            )
            transition_service = self._transition_service_factory(
                authority_port
            )
            result = await transition_service.execute(authority_decision_id)
        except (
            PlatformAdminManualIdentityReviewForbidden,
            PlatformAdminManualIdentityReviewNotFound,
            PlatformAdminManualIdentityReviewConflict,
            PlatformAdminManualIdentityReviewUnavailable,
        ):
            raise
        except (
            InvalidIdentityVerificationAuthorityDecision,
            IdentityVerificationAuthorityRejected,
            P1VerificationTransitionNotReady,
            P1VerificationTransitionInconsistent,
        ):
            conflict = True
        except (
            IdentityVerificationAuthorityUnavailable,
            P1VerificationTransitionUnavailable,
            P1VerificationTransitionOutcomeUnknown,
        ):
            unavailable = True
        except Exception:
            unavailable = True

        if conflict:
            raise PlatformAdminManualIdentityReviewConflict(
                "manual identity review decision conflicts with current state"
            )
        if unavailable:
            raise PlatformAdminManualIdentityReviewUnavailable(
                "manual identity review service is unavailable"
            )
        return result

    @staticmethod
    def _require_reviewer(current_user, user_ref: int) -> None:
        if (
            type(user_ref) is not int
            or user_ref <= 0
            or type(getattr(current_user, "id", None)) is not int
            or current_user.id <= 0
            or getattr(current_user, "role", None) != "super_admin"
            or getattr(current_user, "tenant_id", None) is not None
            or getattr(current_user, "org_id", None) is not None
            or current_user.id == user_ref
        ):
            raise PlatformAdminManualIdentityReviewForbidden("forbidden")
