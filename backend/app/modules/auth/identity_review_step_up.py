from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import secrets
from typing import Any, Callable

from sqlalchemy import literal, select

from app.core.config import get_settings
from app.core.security import decode_access_token_for_step_up
from app.core.sqlalchemy_mapping import map_core_model_classes
from app.modules.auth.models import User
from app.modules.auth.service import verify_password


_ISSUER = "kanglin-step-up"
_AUDIENCE = "platform-identity-review-detail"
_TOKEN_USE = "identity_review_step_up"
_TOKEN_HEADER_TYPE = "identity-review-step-up+jwt"
_PURPOSE = "MANUAL_REVIEW"
_TTL_SECONDS = 120
_CLOCK_SKEW_SECONDS = 5
_MAX_FAILED_ATTEMPTS = 5
_FAILED_ATTEMPT_WINDOW_SECONDS = 15 * 60


class PlatformIdentityReviewStepUpError(RuntimeError):
    pass


class PlatformIdentityReviewStepUpUnauthorized(PlatformIdentityReviewStepUpError):
    pass


class PlatformIdentityReviewStepUpForbidden(PlatformIdentityReviewStepUpError):
    pass


class PlatformIdentityReviewStepUpNotFound(PlatformIdentityReviewStepUpError):
    pass


class PlatformIdentityReviewStepUpRateLimited(PlatformIdentityReviewStepUpError):
    pass


class PlatformIdentityReviewStepUpUnavailable(PlatformIdentityReviewStepUpError):
    pass


@dataclass(frozen=True, slots=True)
class PlatformIdentityReviewStepUpIssueResult:
    token: str
    expires_in: int = _TTL_SECONDS


@dataclass(frozen=True, slots=True)
class PlatformIdentityReviewStepUpConsumption:
    nonce_digest: str
    attempt_digest: str
    reviewer_projection_digest: str
    reviewer_id: int
    subject_user_id: int


class SqlAlchemyPlatformIdentityReviewStepUpRepository:
    def __init__(self, *, session, delegate) -> None:
        self._session = session
        self._delegate = delegate

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    async def get_reviewer_auth_state(self, reviewer_id: int, *, lock: bool):
        map_core_model_classes()
        statement = (
            select(
                User.id,
                User.password_hash,
                User.role,
                User.status,
                User.tenant_id,
                literal(None).label("org_id"),
                User.verify_status,
                User.updated_at,
            )
            .where(User.id == reviewer_id)
            .limit(1)
        )
        if lock:
            statement = statement.with_for_update(of=User)
        result = await self._session.execute(statement)
        return result.one_or_none()


def _decode_access_token(access_token: str) -> dict[str, Any]:
    try:
        return decode_access_token_for_step_up(access_token)
    except Exception:
        raise PlatformIdentityReviewStepUpUnauthorized(
            "invalid access token"
        ) from None


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _require_secret() -> str:
    settings = get_settings()
    secret = settings.identity_review_step_up_secret_key
    if (
        type(secret) is not str
        or len(secret.encode("utf-8")) < 32
        or hmac.compare_digest(secret, settings.jwt_secret_key)
    ):
        raise PlatformIdentityReviewStepUpUnavailable(
            "identity review step-up is unavailable"
        )
    return secret


def _access_token_fingerprint(token: str) -> str:
    return _digest(f"identity-review-access-token:v1:{token}")


def _projection_digest(secret: str, reviewer) -> str:
    updated_at = getattr(reviewer, "updated_at", None)
    if not isinstance(updated_at, datetime) or updated_at.tzinfo is None:
        raise PlatformIdentityReviewStepUpForbidden("forbidden")
    canonical = _canonical_json(
        {
            "id": reviewer.id,
            "org_id": getattr(reviewer, "org_id", None),
            "role": reviewer.role,
            "status": reviewer.status,
            "tenant_id": reviewer.tenant_id,
            "updated_at": updated_at.astimezone(timezone.utc).isoformat(),
            "version": 1,
        }
    )
    derived_key = hmac.new(
        secret.encode("utf-8"), b"identity-review-currentness:v1", hashlib.sha256
    ).digest()
    return hmac.new(derived_key, canonical, hashlib.sha256).hexdigest()


def _require_reviewer(reviewer, *, reviewer_id: int, subject_user_id: int) -> None:
    if (
        reviewer is None
        or reviewer.id != reviewer_id
        or reviewer.role != "super_admin"
        or reviewer.status != "active"
        or reviewer.tenant_id is not None
        or getattr(reviewer, "org_id", None) is not None
        or reviewer_id == subject_user_id
    ):
        raise PlatformIdentityReviewStepUpForbidden("forbidden")


class _StepUpCodec:
    def __init__(self, *, secret: str, clock: Callable[[], datetime]) -> None:
        self._secret = secret
        self._clock = clock

    def encode(
        self,
        *,
        reviewer_id: int,
        subject_user_id: int,
        access_token_fingerprint: str,
        reviewer_projection_digest: str,
        nonce: str,
    ) -> str:
        now = self._clock().astimezone(timezone.utc)
        issued_at = int(now.timestamp())
        expires_at = issued_at + _TTL_SECONDS
        header = {"alg": "HS256", "typ": _TOKEN_HEADER_TYPE}
        payload = {
            "iss": _ISSUER,
            "aud": _AUDIENCE,
            "token_use": _TOKEN_USE,
            "sub": str(reviewer_id),
            "reviewer_user_id": reviewer_id,
            "subject_user_id": subject_user_id,
            "purpose": _PURPOSE,
            "nonce": nonce,
            "iat": issued_at,
            "nbf": issued_at,
            "exp": expires_at,
            "issued_at": issued_at,
            "expires_at": expires_at,
            "ath": access_token_fingerprint,
            "reviewer_projection_digest": reviewer_projection_digest,
        }
        encoded_header = _b64encode(_canonical_json(header))
        encoded_payload = _b64encode(_canonical_json(payload))
        signing_input = f"{encoded_header}.{encoded_payload}"
        signature = hmac.new(
            self._secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256
        ).digest()
        return f"{signing_input}.{_b64encode(signature)}"

    def decode(self, token: str) -> dict[str, Any]:
        try:
            encoded_header, encoded_payload, encoded_signature = token.split(".", 2)
            header = json.loads(_b64decode(encoded_header))
            payload = json.loads(_b64decode(encoded_payload))
            signature = _b64decode(encoded_signature)
        except Exception:
            raise PlatformIdentityReviewStepUpUnauthorized("invalid step-up") from None
        if header != {"alg": "HS256", "typ": _TOKEN_HEADER_TYPE}:
            raise PlatformIdentityReviewStepUpUnauthorized("invalid step-up")
        expected = hmac.new(
            self._secret.encode("utf-8"),
            f"{encoded_header}.{encoded_payload}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(signature, expected):
            raise PlatformIdentityReviewStepUpUnauthorized("invalid step-up")

        now = int(self._clock().astimezone(timezone.utc).timestamp())
        required_ints = (
            "reviewer_user_id",
            "subject_user_id",
            "iat",
            "nbf",
            "exp",
            "issued_at",
            "expires_at",
        )
        if any(type(payload.get(name)) is not int for name in required_ints):
            raise PlatformIdentityReviewStepUpUnauthorized("invalid step-up")
        required_strings = (
            "sub",
            "purpose",
            "nonce",
            "ath",
            "reviewer_projection_digest",
        )
        if any(type(payload.get(name)) is not str for name in required_strings):
            raise PlatformIdentityReviewStepUpUnauthorized("invalid step-up")
        valid = (
            payload.get("iss") == _ISSUER
            and payload.get("aud") == _AUDIENCE
            and payload.get("token_use") == _TOKEN_USE
            and payload["purpose"] == _PURPOSE
            and payload["sub"] == str(payload["reviewer_user_id"])
            and payload["reviewer_user_id"] > 0
            and payload["subject_user_id"] > 0
            and payload["issued_at"] == payload["iat"]
            and payload["expires_at"] == payload["exp"]
            and payload["nbf"] == payload["iat"]
            and payload["exp"] - payload["iat"] == _TTL_SECONDS
            and payload["iat"] <= now + _CLOCK_SKEW_SECONDS
            and payload["nbf"] <= now + _CLOCK_SKEW_SECONDS
            and now < payload["exp"]
            and len(payload["nonce"]) >= 32
            and len(payload["ath"]) == 64
            and len(payload["reviewer_projection_digest"]) == 64
        )
        if not valid:
            raise PlatformIdentityReviewStepUpUnauthorized("invalid step-up")
        return payload


class PlatformIdentityReviewStepUpService:
    def __init__(
        self,
        *,
        repository,
        secret: str | None = None,
        clock: Callable[[], datetime] = _utc_now,
        nonce_factory: Callable[[int], str] = secrets.token_urlsafe,
        password_verifier: Callable[[str, str], bool] = verify_password,
    ) -> None:
        resolved_secret = _require_secret() if secret is None else secret
        if type(resolved_secret) is not str or len(resolved_secret.encode()) < 32:
            raise PlatformIdentityReviewStepUpUnavailable(
                "identity review step-up is unavailable"
            )
        self._repository = repository
        self._clock = clock
        self._nonce_factory = nonce_factory
        self._password_verifier = password_verifier
        self._secret = resolved_secret
        self._codec = _StepUpCodec(secret=resolved_secret, clock=clock)

    async def issue(
        self,
        *,
        current_user,
        subject_user_id: int,
        password: str,
        access_token: str,
    ) -> PlatformIdentityReviewStepUpIssueResult:
        try:
            claims = _decode_access_token(access_token)
            if int(claims["sub"]) != current_user.id:
                raise PlatformIdentityReviewStepUpUnauthorized("invalid access token")
            await self._repository.acquire_reviewer_lock(current_user.id)
            now = self._clock().astimezone(timezone.utc)
            failed_count = await self._repository.count_failed_reauth_attempts(
                reviewer_id=current_user.id,
                since=now - timedelta(seconds=_FAILED_ATTEMPT_WINDOW_SECONDS),
            )
            if failed_count >= _MAX_FAILED_ATTEMPTS:
                raise PlatformIdentityReviewStepUpRateLimited("reauth rate limited")
            reviewer = await self._repository.get_reviewer_auth_state(
                current_user.id, lock=True
            )
            _require_reviewer(
                reviewer,
                reviewer_id=current_user.id,
                subject_user_id=subject_user_id,
            )
            submission = await self._repository.get_submission(
                user_ref=subject_user_id
            )
            if submission is None or submission.status != "submitted":
                raise PlatformIdentityReviewStepUpNotFound("subject not found")
            if not self._password_verifier(password, reviewer.password_hash):
                await self._repository.add_step_up_audit(
                    reviewer_id=current_user.id,
                    subject_user_id=subject_user_id,
                    action="identity_step_up_password_failed",
                    payload={"purpose": _PURPOSE},
                )
                await self._repository.commit()
                raise PlatformIdentityReviewStepUpUnauthorized("invalid credentials")

            projection = _projection_digest(self._secret, reviewer)
            token = self._codec.encode(
                reviewer_id=current_user.id,
                subject_user_id=subject_user_id,
                access_token_fingerprint=_access_token_fingerprint(access_token),
                reviewer_projection_digest=projection,
                nonce=self._nonce_factory(32),
            )
            await self._repository.add_step_up_audit(
                reviewer_id=current_user.id,
                subject_user_id=subject_user_id,
                action="identity_step_up_issued",
                payload={"purpose": _PURPOSE, "submission_version": submission.version},
            )
            await self._repository.commit()
            return PlatformIdentityReviewStepUpIssueResult(token=token)
        except asyncio.CancelledError:
            await self._safe_rollback()
            raise
        except PlatformIdentityReviewStepUpError:
            await self._safe_rollback()
            raise
        except Exception:
            await self._safe_rollback()
            raise PlatformIdentityReviewStepUpUnavailable(
                "identity review step-up is unavailable"
            ) from None

    async def begin_consumption(
        self,
        *,
        current_user,
        subject_user_id: int,
        purpose: str,
        access_token: str,
        step_up_token: str,
    ) -> PlatformIdentityReviewStepUpConsumption:
        try:
            access_claims = _decode_access_token(access_token)
            step_up_claims = self._codec.decode(step_up_token)
            if (
                int(access_claims["sub"]) != current_user.id
                or step_up_claims["reviewer_user_id"] != current_user.id
                or step_up_claims["subject_user_id"] != subject_user_id
                or purpose != _PURPOSE
                or step_up_claims["purpose"] != purpose
                or not hmac.compare_digest(
                    step_up_claims["ath"], _access_token_fingerprint(access_token)
                )
            ):
                raise PlatformIdentityReviewStepUpUnauthorized("invalid step-up")
            reviewer = await self._repository.get_reviewer_auth_state(
                current_user.id, lock=True
            )
            _require_reviewer(
                reviewer,
                reviewer_id=current_user.id,
                subject_user_id=subject_user_id,
            )
            projection = _projection_digest(self._secret, reviewer)
            if not hmac.compare_digest(
                projection, step_up_claims["reviewer_projection_digest"]
            ):
                raise PlatformIdentityReviewStepUpForbidden("forbidden")

            nonce_digest = _digest(
                f"identity-review-step-up:v1:{step_up_claims['nonce']}"
            )
            attempt_digest = _digest(
                f"identity-review-step-up-attempt:v1:{self._nonce_factory(32)}"
            )
            await self._repository.acquire_nonce_lock(nonce_digest)
            if await self._repository.find_step_up_consumption(
                nonce_digest,
                current_user.id,
                subject_user_id,
            ):
                raise PlatformIdentityReviewStepUpUnauthorized("step-up consumed")
            await self._repository.acquire_subject_lock(subject_user_id)
            return PlatformIdentityReviewStepUpConsumption(
                nonce_digest=nonce_digest,
                attempt_digest=attempt_digest,
                reviewer_projection_digest=projection,
                reviewer_id=current_user.id,
                subject_user_id=subject_user_id,
            )
        except asyncio.CancelledError:
            await self._safe_rollback()
            raise
        except PlatformIdentityReviewStepUpError:
            await self._safe_rollback()
            raise
        except Exception:
            await self._safe_rollback()
            raise PlatformIdentityReviewStepUpUnavailable(
                "identity review step-up is unavailable"
            ) from None

    async def _safe_rollback(self) -> None:
        try:
            await self._repository.rollback()
        except Exception:
            pass


def create_platform_identity_review_step_up_service(*, repository):
    return PlatformIdentityReviewStepUpService(repository=repository)
