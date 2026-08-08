from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


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
