from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib

from app.modules.auth.identity_verification_authority import (
    IdentityVerificationAuthorityRejected,
    IdentityVerificationAuthorityUnavailable,
    InvalidIdentityVerificationAuthorityDecision,
)
from app.modules.auth.identity_review_step_up import (
    PlatformIdentityReviewStepUpError,
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


@dataclass(frozen=True, slots=True)
class PlatformAdminManualIdentityReviewExecutionResult:
    user_ref: int
    verification_decision_ref: object
    registration_event_id: object
    authority_decision_key: str
    transition_digest: str
    facts_version: int
    status: str
    replayed: bool
    decided_at: datetime


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
            if hasattr(result, "user_ref"):
                result = PlatformAdminManualIdentityReviewExecutionResult(
                    user_ref=result.user_ref,
                    verification_decision_ref=result.verification_decision_ref,
                    registration_event_id=result.registration_event_id,
                    authority_decision_key=result.authority_decision_key,
                    transition_digest=result.transition_digest,
                    facts_version=result.facts_version,
                    status=result.status,
                    replayed=result.replayed,
                    decided_at=(
                        getattr(authority_port, "last_decided_at", None)
                        or request.decided_at
                    ),
                )
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
    def __init__(
        self,
        *,
        application_repository,
        writer_session_factory,
        crypto,
        step_up_service=None,
        application_session_factory=None,
        clock=lambda: datetime.now(timezone.utc),
    ) -> None:
        self._application_repository = application_repository
        self._writer_session_factory = writer_session_factory
        self._crypto = crypto
        self._step_up_service = step_up_service
        self._application_session_factory = application_session_factory
        self._clock = clock

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

    async def approval_evidence_digest(
        self,
        *,
        current_user,
        user_ref: int,
        request,
        allow_verified_replay: bool = False,
    ) -> str | None:
        self._require_reviewer(current_user, user_ref)
        model = await self._application_repository.get_submission(
            user_ref=user_ref, version=request.submission_version
        )
        if (
            model is None
            or model.status
            not in ({"submitted", "verified"} if allow_verified_replay else {"submitted"})
            or request.decision_basis_code != "APPROVED_OFFLINE_IDENTITY_CHECK"
        ):
            raise PlatformAdminManualIdentityReviewConflict("stale")
        return hashlib.sha256(
            (
                f"identity-submission-review:v1:{model.submission_id}:"
                f"{model.version}:{model.content_digest}:{request.decision_basis_code}"
            ).encode()
        ).hexdigest()

    async def detail(
        self,
        *,
        current_user,
        user_ref: int,
        purpose_code: str,
        access_token: str | None = None,
        step_up_token: str | None = None,
    ):
        await self._require_current_reviewer(current_user, user_ref)
        if self._step_up_service is None:
            raise PlatformAdminManualIdentityReviewUnavailable("unavailable")
        consumption = None
        try:
            consumption = await self._step_up_service.begin_consumption(
                current_user=current_user,
                subject_user_id=user_ref,
                purpose=purpose_code,
                access_token=access_token,
                step_up_token=step_up_token,
            )
            model = await self._application_repository.get_submission(
                user_ref=user_ref
            )
            if model is None or model.status != "submitted":
                raise PlatformAdminManualIdentityReviewNotFound("not found")
            name = self._crypto.decrypt(
                self._encrypted(model.real_name_ciphertext, model.real_name_nonce),
                aad=self._aad(model, "real_name"),
            )
            card = self._crypto.decrypt(
                self._encrypted(model.id_card_ciphertext, model.id_card_nonce),
                aad=self._aad(model, "id_card"),
            )
            await self._application_repository.add_sensitive_read_audit(
                reviewer_id=current_user.id,
                model=model,
                purpose_code=purpose_code,
                nonce_digest=consumption.nonce_digest,
                attempt_digest=consumption.attempt_digest,
                reviewer_projection_digest=(
                    consumption.reviewer_projection_digest
                ),
            )
            await self._application_repository.commit()
            return model, name, card
        except (
            PlatformAdminManualIdentityReviewForbidden,
            PlatformAdminManualIdentityReviewNotFound,
            PlatformIdentityReviewStepUpError,
        ):
            await self._application_repository.rollback()
            raise
        except BaseException as exc:
            await self._application_repository.rollback()
            if isinstance(exc, asyncio.CancelledError):
                raise
            if consumption is not None:
                await self._confirm_consumption_outcome(consumption)
            raise PlatformAdminManualIdentityReviewUnavailable("unavailable") from None

    @asynccontextmanager
    async def subject_coordination(self, user_ref: int):
        await self._application_repository.acquire_subject_lock(user_ref)
        try:
            yield
        finally:
            await self._application_repository.rollback()

    async def _confirm_consumption_outcome(self, consumption) -> None:
        if self._application_session_factory is None:
            return
        from app.modules.auth.manual_identity_review_repository import (
            SqlAlchemyPlatformIdentitySubmissionReviewRepository,
        )

        try:
            async with self._application_session_factory() as session:
                repository = SqlAlchemyPlatformIdentitySubmissionReviewRepository(
                    session
                )
                await repository.acquire_nonce_lock(consumption.nonce_digest)
                await repository.find_step_up_consumption(
                    consumption.nonce_digest,
                    consumption.reviewer_id,
                    consumption.subject_user_id,
                    consumption.attempt_digest,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def reject(self, *, current_user, user_ref: int, request):
        await self._require_current_reviewer(current_user, user_ref)
        digest = self._crypto.digest(
            "review-reject",
            f"{user_ref}:{current_user.id}:{request.submission_version}:"
            f"{request.idempotency_key}:{request.reason_code}",
        )
        from app.modules.auth.manual_identity_review_repository import (
            SqlAlchemyPlatformIdentitySubmissionReviewRepository,
        )
        try:
            async with self.subject_coordination(user_ref):
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
                        model=model,
                        reviewer_id=current_user.id,
                        decided_at=self._clock(),
                        reason_code=request.reason_code,
                        evidence_digest=digest,
                    )
                    if not changed:
                        raise PlatformAdminManualIdentityReviewConflict("stale")
                    await repository.commit()
                    model.status = "rejected"
                    return model, False
        except (PlatformAdminManualIdentityReviewNotFound, PlatformAdminManualIdentityReviewConflict):
            raise
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            confirmed = await self._confirm_reject_outcome(
                user_ref=user_ref,
                version=request.submission_version,
                reviewer_id=current_user.id,
                evidence_digest=digest,
            )
            if confirmed is not None:
                return confirmed, True
            raise PlatformAdminManualIdentityReviewUnavailable("unavailable") from None

    async def _confirm_reject_outcome(
        self, *, user_ref: int, version: int, reviewer_id: int, evidence_digest: str
    ):
        from app.modules.auth.manual_identity_review_repository import (
            SqlAlchemyPlatformIdentitySubmissionReviewRepository,
        )

        try:
            async with self._writer_session_factory() as session:
                repository = SqlAlchemyPlatformIdentitySubmissionReviewRepository(session)
                model = await repository.get_submission(
                    user_ref=user_ref, version=version, lock=False
                )
                if (
                    model is not None
                    and model.status == "rejected"
                    and model.version == version
                    and model.reviewed_by == reviewer_id
                    and model.evidence_digest == evidence_digest
                ):
                    return model
        except asyncio.CancelledError:
            raise
        except Exception:
            return None
        return None

    async def mark_verified(
        self, *, current_user, user_ref: int, request, evidence_digest: str,
        decided_at: datetime,
    ):
        self._require_reviewer(current_user, user_ref)
        if request.submission_version is None:
            return
        from app.modules.auth.manual_identity_review_repository import (
            SqlAlchemyPlatformIdentitySubmissionReviewRepository,
        )
        try:
            async with self._writer_session_factory() as session:
                repository = SqlAlchemyPlatformIdentitySubmissionReviewRepository(session)
                model = await repository.get_submission(
                    user_ref=user_ref, version=request.submission_version, lock=True
                )
                if self._verified_submission_matches(
                    model,
                    version=request.submission_version,
                    reviewer_id=current_user.id,
                    evidence_digest=evidence_digest,
                    decided_at=decided_at,
                ):
                    return
                if model is None or model.status != "submitted":
                    raise PlatformAdminManualIdentityReviewConflict("stale")
                changed = await repository.mark_verified(
                    user_ref=user_ref, version=request.submission_version,
                    reviewer_id=current_user.id, decided_at=decided_at,
                    evidence_digest=evidence_digest,
                )
                if not changed:
                    raise PlatformAdminManualIdentityReviewConflict("stale")
                await repository.commit()
        except PlatformAdminManualIdentityReviewConflict:
            raise
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            if await self._confirm_verified_outcome(
                user_ref=user_ref,
                version=request.submission_version,
                reviewer_id=current_user.id,
                evidence_digest=evidence_digest,
                decided_at=decided_at,
            ):
                return
            raise PlatformAdminManualIdentityReviewUnavailable("unavailable") from None

    async def _confirm_verified_outcome(
        self,
        *,
        user_ref: int,
        version: int,
        reviewer_id: int,
        evidence_digest: str,
        decided_at: datetime,
    ) -> bool:
        from app.modules.auth.manual_identity_review_repository import (
            SqlAlchemyPlatformIdentitySubmissionReviewRepository,
        )

        try:
            async with self._writer_session_factory() as session:
                repository = SqlAlchemyPlatformIdentitySubmissionReviewRepository(session)
                model = await repository.get_submission(
                    user_ref=user_ref, version=version, lock=False
                )
                return self._verified_submission_matches(
                    model,
                    version=version,
                    reviewer_id=reviewer_id,
                    evidence_digest=evidence_digest,
                    decided_at=decided_at,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            return False

    @staticmethod
    def _verified_submission_matches(
        model,
        *,
        version: int,
        reviewer_id: int,
        evidence_digest: str,
        decided_at: datetime,
    ) -> bool:
        return (
            model is not None
            and model.status == "verified"
            and model.version == version
            and model.reviewed_by == reviewer_id
            and model.evidence_digest == evidence_digest
            and model.decided_at == decided_at
        )

    @staticmethod
    def _encrypted(ciphertext, nonce):
        from app.modules.auth.identity_submission_crypto import EncryptedIdentityValue
        return EncryptedIdentityValue(ciphertext=bytes(ciphertext), nonce=bytes(nonce))

    def _aad(self, model, field: str):
        return self._crypto.aad(
            submission_id=str(model.submission_id), user_ref=model.user_ref,
            version=model.version, field=field, key_id=model.encryption_key_id,
        )
