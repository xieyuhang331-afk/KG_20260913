from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from app.modules.auth.identity_submission_crypto import (
    IdentitySubmissionCrypto,
    mask_id_card,
)


class IdentitySubmissionError(RuntimeError):
    pass


class IdentitySubmissionForbidden(IdentitySubmissionError):
    pass


class IdentitySubmissionConflict(IdentitySubmissionError):
    pass


class IdentitySubmissionRateLimited(IdentitySubmissionError):
    pass


class IdentitySubmissionUnavailable(IdentitySubmissionError):
    pass


@dataclass(frozen=True, slots=True)
class IdentitySubmissionResult:
    status: str
    submission_version: int | None
    id_card_masked: str | None
    submitted_at: datetime | None
    decided_at: datetime | None = None
    rejection_reason_code: str | None = None
    resubmit_available_at: datetime | None = None
    outcome: str | None = None


class IdentitySubmissionService:
    def __init__(self, *, repository, crypto: IdentitySubmissionCrypto, clock=None) -> None:
        self._repository = repository
        self._crypto = crypto
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def submit(self, *, current_user, request) -> IdentitySubmissionResult:
        self._require_member(current_user)
        now = self._clock()
        try:
            user = await self._repository.lock_user(current_user.id)
            self._require_eligible_user(user, current_user.id)
            idempotency_digest = self._crypto.digest(
                "idempotency", request.idempotency_key
            )
            content_digest = self._crypto.digest(
                "content",
                f"{request.real_name}\x1f{request.id_card}\x1f{request.consent_version}",
            )
            existing = await self._repository.find_by_idempotency(
                current_user.id, idempotency_digest
            )
            if existing is not None:
                if existing.content_digest != content_digest:
                    raise IdentitySubmissionConflict(
                        "identity submission idempotency conflict"
                    )
                return self._result(existing, outcome="REPLAYED")

            latest = await self._repository.find_latest(current_user.id)
            if latest is not None and latest.status in {"submitted", "verified"}:
                raise IdentitySubmissionConflict("identity submission is already active")
            if latest is not None and latest.status == "rejected":
                available_at = latest.decided_at + timedelta(hours=24)
                if now < available_at:
                    raise IdentitySubmissionRateLimited(
                        "identity submission cooldown is active"
                    )
            if await self._repository.count_since(
                current_user.id, now - timedelta(hours=24)
            ) >= 3:
                raise IdentitySubmissionRateLimited(
                    "identity submission rate limit exceeded"
                )

            version = 1 if latest is None else latest.version + 1
            submission_id = uuid4()
            name_aad = self._crypto.aad(
                submission_id=str(submission_id), user_ref=current_user.id,
                version=version, field="real_name", key_id=self._crypto.key_id,
            )
            card_aad = self._crypto.aad(
                submission_id=str(submission_id), user_ref=current_user.id,
                version=version, field="id_card", key_id=self._crypto.key_id,
            )
            encrypted_name = self._crypto.encrypt(request.real_name, aad=name_aad)
            encrypted_card = self._crypto.encrypt(request.id_card, aad=card_aad)
            await self._repository.mark_user_submitted(current_user.id)
            model = await self._repository.add(
                submission_id=submission_id,
                user_ref=current_user.id,
                version=version,
                real_name=encrypted_name,
                id_card=encrypted_card,
                id_card_masked=mask_id_card(request.id_card),
                key_id=self._crypto.key_id,
                content_digest=content_digest,
                id_card_digest=self._crypto.digest("id-card", request.id_card),
                idempotency_digest=idempotency_digest,
                consent_version=request.consent_version,
                submitted_at=now,
            )
            await self._repository.commit()
            return self._result(model, outcome="CREATED")
        except (
            IdentitySubmissionForbidden,
            IdentitySubmissionConflict,
            IdentitySubmissionRateLimited,
        ):
            await self._repository.rollback()
            raise
        except BaseException as exc:
            await self._repository.rollback()
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            import asyncio
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise IdentitySubmissionUnavailable(
                "identity submission service unavailable"
            ) from None

    async def status(self, *, current_user) -> IdentitySubmissionResult:
        self._require_member(current_user)
        try:
            user = await self._repository.get_user(current_user.id)
            self._require_status_user(user, current_user.id)
            latest = await self._repository.find_latest(current_user.id)
            if latest is None:
                return IdentitySubmissionResult(
                    status="pending", submission_version=None,
                    id_card_masked=None, submitted_at=None,
                )
            return self._result(latest)
        except IdentitySubmissionForbidden:
            raise
        except BaseException as exc:
            import asyncio
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise IdentitySubmissionUnavailable(
                "identity submission service unavailable"
            ) from None

    @staticmethod
    def _require_member(current_user) -> None:
        if (
            getattr(current_user, "role", None) != "member"
            or getattr(current_user, "tenant_id", None) is not None
            or getattr(current_user, "org_id", None) is not None
        ):
            raise IdentitySubmissionForbidden("forbidden")

    @staticmethod
    def _require_eligible_user(user, user_ref: int) -> None:
        if (
            user is None or user.id != user_ref or user.role != "member"
            or user.status != "active" or user.tenant_id is not None
            or user.verify_status == "verified"
        ):
            raise IdentitySubmissionForbidden("forbidden")

    @staticmethod
    def _require_status_user(user, user_ref: int) -> None:
        if (
            user is None or user.id != user_ref or user.role != "member"
            or user.status != "active" or user.tenant_id is not None
        ):
            raise IdentitySubmissionForbidden("forbidden")

    @staticmethod
    def _result(model, outcome: str | None = None) -> IdentitySubmissionResult:
        resubmit_at = None
        if model.status == "rejected" and model.decided_at is not None:
            resubmit_at = model.decided_at + timedelta(hours=24)
        return IdentitySubmissionResult(
            status=model.status,
            submission_version=model.version,
            id_card_masked=model.id_card_masked,
            submitted_at=model.submitted_at,
            decided_at=model.decided_at,
            rejection_reason_code=model.rejection_reason_code,
            resubmit_available_at=resubmit_at,
            outcome=outcome,
        )
