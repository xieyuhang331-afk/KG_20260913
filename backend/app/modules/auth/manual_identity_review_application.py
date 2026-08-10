from __future__ import annotations

import asyncio
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
    evidence_digest: str | None = None
    submission_version: int | None = None
    decision_basis_code: str | None = None

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
        if self.evidence_digest is not None and (
            type(self.evidence_digest) is not str
            or len(self.evidence_digest) != 64
            or self.evidence_digest != self.evidence_digest.lower()
            or any(character not in "0123456789abcdef" for character in self.evidence_digest)
        ):
            raise ValueError("evidence_digest is invalid")
        if self.submission_version is not None and (
            type(self.submission_version) is not int or self.submission_version <= 0
        ):
            raise ValueError("submission_version is invalid")
        if self.decision_basis_code not in {None, "APPROVED_OFFLINE_IDENTITY_CHECK"}:
            raise ValueError("decision_basis_code is invalid")


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


class PlatformIdentitySubmissionReviewService:
    def __init__(self, *, application_repository, writer_session_factory, crypto) -> None:
        self._application_repository = application_repository
        self._writer_session_factory = writer_session_factory
        self._crypto = crypto

    @staticmethod
    def _require_reviewer(current_user, user_ref: int | None = None) -> None:
        if (
            getattr(current_user, "role", None) != "super_admin"
            or getattr(current_user, "tenant_id", None) is not None
            or getattr(current_user, "org_id", None) is not None
            or (user_ref is not None and current_user.id == user_ref)
        ):
            raise PlatformAdminManualIdentityReviewForbidden("forbidden")

    async def _require_current_reviewer(
        self, current_user, user_ref: int | None = None
    ) -> None:
        self._require_reviewer(current_user, user_ref)
        unavailable = False
        reviewer = None
        try:
            reviewer = await self._application_repository.get_reviewer_state(
                current_user.id
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            unavailable = True
        if unavailable:
            raise PlatformAdminManualIdentityReviewUnavailable(
                "manual identity review service is unavailable"
            ) from None
        if (
            reviewer is None
            or reviewer.id != current_user.id
            or reviewer.role != "super_admin"
            or reviewer.status != "active"
            or reviewer.tenant_id is not None
            or reviewer.org_id is not None
        ):
            raise PlatformAdminManualIdentityReviewForbidden("forbidden")

    async def list_queue(self, *, current_user, page: int, page_size: int):
        await self._require_current_reviewer(current_user)
        items = await self._application_repository.list_submitted(
            offset=(page - 1) * page_size, limit=page_size
        )
        total = await self._application_repository.count_submitted()
        return items, total

    async def approval_evidence_digest(self, *, current_user, user_ref: int, request) -> str | None:
        self._require_reviewer(current_user, user_ref)
        if request.submission_version is None:
            return request.evidence_digest
        model = await self._application_repository.get_submission(
            user_ref=user_ref, version=request.submission_version
        )
        if (
            model is None or model.status != "submitted"
            or request.decision_basis_code != "APPROVED_OFFLINE_IDENTITY_CHECK"
        ):
            raise PlatformAdminManualIdentityReviewConflict("stale")
        return hashlib.sha256(
            (
                f"identity-submission-review:v1:{model.submission_id}:"
                f"{model.version}:{model.content_digest}:{request.decision_basis_code}"
            ).encode()
        ).hexdigest()

    async def detail(self, *, current_user, user_ref: int, purpose_code: str):
        await self._require_current_reviewer(current_user, user_ref)
        model = await self._application_repository.get_submission(user_ref=user_ref)
        if model is None:
            raise PlatformAdminManualIdentityReviewNotFound("not found")
        try:
            name = self._crypto.decrypt(
                self._encrypted(model.real_name_ciphertext, model.real_name_nonce),
                aad=self._aad(model, "real_name"),
            )
            card = self._crypto.decrypt(
                self._encrypted(model.id_card_ciphertext, model.id_card_nonce),
                aad=self._aad(model, "id_card"),
            )
            await self._application_repository.add_sensitive_read_audit(
                reviewer_id=current_user.id, model=model, purpose_code=purpose_code
            )
            await self._application_repository.commit()
            return model, name, card
        except BaseException as exc:
            import asyncio
            await self._application_repository.rollback()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise PlatformAdminManualIdentityReviewUnavailable("unavailable") from None

    async def reject(self, *, current_user, user_ref: int, request):
        await self._require_current_reviewer(current_user, user_ref)
        digest = self._crypto.digest(
            "review-reject",
            f"{user_ref}:{request.submission_version}:{request.idempotency_key}:{request.reason_code}",
        )
        from app.modules.auth.manual_identity_review_repository import (
            SqlAlchemyPlatformIdentitySubmissionReviewRepository,
        )
        try:
            async with self._writer_session_factory() as session:
                repository = SqlAlchemyPlatformIdentitySubmissionReviewRepository(session)
                model = await repository.get_submission(
                    user_ref=user_ref, version=request.submission_version, lock=True
                )
                if model is None:
                    raise PlatformAdminManualIdentityReviewNotFound("not found")
                if model.status == "rejected" and model.evidence_digest == digest:
                    return model, True
                if model.status != "submitted":
                    raise PlatformAdminManualIdentityReviewConflict("stale")
                changed = await repository.mark_rejected(
                    model=model, reviewer_id=current_user.id, decided_at=request.decided_at,
                    reason_code=request.reason_code, evidence_digest=digest,
                )
                if not changed:
                    raise PlatformAdminManualIdentityReviewConflict("stale")
                await repository.commit()
                model.status = "rejected"
                return model, False
        except (PlatformAdminManualIdentityReviewNotFound, PlatformAdminManualIdentityReviewConflict):
            raise
        except BaseException as exc:
            import asyncio
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise PlatformAdminManualIdentityReviewUnavailable("unavailable") from None

    async def mark_verified(self, *, current_user, user_ref: int, request, evidence_digest: str):
        self._require_reviewer(current_user, user_ref)
        if request.submission_version is None:
            return
        from app.modules.auth.manual_identity_review_repository import (
            SqlAlchemyPlatformIdentitySubmissionReviewRepository,
        )
        try:
            async with self._writer_session_factory() as session:
                repository = SqlAlchemyPlatformIdentitySubmissionReviewRepository(session)
                changed = await repository.mark_verified(
                    user_ref=user_ref, version=request.submission_version,
                    reviewer_id=current_user.id, decided_at=request.decided_at,
                    evidence_digest=evidence_digest,
                )
                if not changed:
                    raise PlatformAdminManualIdentityReviewConflict("stale")
                await repository.commit()
        except PlatformAdminManualIdentityReviewConflict:
            raise
        except BaseException as exc:
            import asyncio
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise PlatformAdminManualIdentityReviewUnavailable("unavailable") from None

    @staticmethod
    def _encrypted(ciphertext, nonce):
        from app.modules.auth.identity_submission_crypto import EncryptedIdentityValue
        return EncryptedIdentityValue(ciphertext=bytes(ciphertext), nonce=bytes(nonce))

    def _aad(self, model, field: str):
        return self._crypto.aad(
            submission_id=str(model.submission_id), user_ref=model.user_ref,
            version=model.version, field=field, key_id=model.encryption_key_id,
        )
