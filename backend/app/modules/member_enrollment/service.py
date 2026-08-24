from __future__ import annotations

import base64
import asyncio
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Awaitable, Callable, Mapping
from uuid import UUID
from zoneinfo import ZoneInfo

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.exc import IntegrityError

from app.core.security import CurrentUser
from app.core.uuid_generator import Uuid7Generator
from app.modules.member.application.member_no_allocator import generate_member_no_candidate
from app.modules.member_enrollment.domain import (
    AssignmentStatus,
    ConsentDocument,
    ConsentDocumentStatus,
    ConsentRecord,
    ConsentStatus,
    IdentityStatus,
    IdentityVerification,
    InvitationAttemptRejected,
    InvitationStatus,
    MemberEnrollmentConflict,
    MemberServiceInvitation,
    PrimaryTherapistAssignment,
    ProxyGrant,
    ProxyGrantStatus,
)
from app.modules.member_enrollment.identity_authority import (
    parse_prc_resident_identity_birth_date,
    verified_adult_on,
)
from app.modules.member_enrollment.repository import MemberEnrollmentRepository
from app.modules.member_enrollment.schemas import (
    AcceptEnrollmentRequest,
    CreateMemberInvitationRequest,
    IdentitySubmissionRequest,
    IdentityResubmitRequest,
    InstitutionIdentityCheckRequest,
    PlatformIdentityDecisionRequest,
    PiiAccessRequest,
    CreateAssignmentRequest,
    CreateConsentDocumentRequest,
    PublishConsentDocumentRequest,
    RecordConsentRequest,
    ReasonedVersionRequest,
)


SAFE_UNAVAILABLE = "MEMBER_ENROLLMENT_DEPENDENCY_UNAVAILABLE"
REQUIRED_PROXY_PERMISSIONS = (
    "IDENTITY_SUBMIT", "CONSENT_ACCEPT", "DAILY_VIEW", "DAILY_INPUT", "REPORT_UPLOAD"
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


_STEP_UP_PROOF_FIELDS = (
    "proof_version", "reviewer_user_id", "user_version", "user_updated_at",
    "verification_id", "current_revision_id", "actor_scope", "idempotency_key",
    "request_id", "request_digest", "access_token_digest", "currentness_digest",
    "reason_code", "password_valid", "proof_issued_at", "proof_expires_at",
)


def _proof_text(value: object) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if isinstance(value, UUID):
        return str(value)
    if type(value) is bool:
        return "1" if value else "0"
    return str(value)


def reviewer_credential_proof(password_hash: str, values: Mapping[str, object]) -> str:
    if tuple(values) != _STEP_UP_PROOF_FIELDS:
        raise RuntimeError(SAFE_UNAVAILABLE) from None
    payload = b"".join(
        str(len(encoded)).encode("ascii") + b":" + encoded
        for encoded in (_proof_text(values[name]).encode("utf-8") for name in _STEP_UP_PROOF_FIELDS)
    )
    credential_key = hashlib.sha256(
        ("slice3-reviewer-credential-key:v1:" + password_hash).encode("utf-8")
    ).digest()
    return hashlib.sha256(credential_key + payload + credential_key).hexdigest()


def reviewer_credential_proof_marker(credential_proof_digest: str) -> str:
    if (
        type(credential_proof_digest) is not str
        or len(credential_proof_digest) != 64
        or any(value not in "0123456789abcdef" for value in credential_proof_digest)
    ):
        raise RuntimeError(SAFE_UNAVAILABLE) from None
    return hashlib.sha256(
        ("slice3-stepup-proof:v1:" + credential_proof_digest).encode("ascii")
    ).hexdigest()


def _canonical(value: object) -> bytes:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()


def digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _load_keyring(current_env: str, keyring_env: str) -> tuple[str, dict[str, bytes]]:
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        current = os.environ[current_env]
        source = json.loads(os.environ[keyring_env], object_pairs_hook=unique_pairs)
        if type(source) is not dict or not source:
            raise ValueError
        values = {str(key): base64.b64decode(value, validate=True) for key, value in source.items()}
        if current not in values or any(len(value) != 32 for value in values.values()):
            raise ValueError
        return current, values
    except Exception:
        raise RuntimeError(SAFE_UNAVAILABLE) from None


class MemberEnrollmentSecrets:
    """Eleven isolated domains with stored-key-bound AEAD and HMAC digests."""

    def __init__(self) -> None:
        self.pii_key_id, self.pii_keys = _load_keyring(
            "KG_MEMBER_ENROLLMENT_PII_CURRENT_KEY_ID", "KG_MEMBER_ENROLLMENT_PII_KEYRING_JSON"
        )
        self.lookup_key_id, self.lookup_keys = _load_keyring(
            "KG_MEMBER_ENROLLMENT_LOOKUP_CURRENT_KEY_ID", "KG_MEMBER_ENROLLMENT_LOOKUP_KEYRING_JSON"
        )
        self.code_key_id, self.code_keys = _load_keyring(
            "KG_MEMBER_ENROLLMENT_CODE_CURRENT_KEY_ID", "KG_MEMBER_ENROLLMENT_CODE_KEYRING_JSON"
        )
        self.replay_key_id, self.replay_keys = _load_keyring(
            "KG_MEMBER_ENROLLMENT_REPLAY_CURRENT_KEY_ID", "KG_MEMBER_ENROLLMENT_REPLAY_KEYRING_JSON"
        )
        self.delivery_key_id, self.delivery_keys = _load_keyring(
            "KG_MEMBER_ENROLLMENT_DELIVERY_CURRENT_KEY_ID", "KG_MEMBER_ENROLLMENT_DELIVERY_KEYRING_JSON"
        )
        try:
            self.fingerprint_key_id = os.environ["KG_IDENTITY_PII_KEY_ID"]
            fingerprint_material = base64.b64decode(
                os.environ["KG_IDENTITY_PII_HMAC_KEY_B64"], validate=True
            )
            if not self.fingerprint_key_id or len(fingerprint_material) < 32:
                raise ValueError
            self.fingerprint_keys = {self.fingerprint_key_id: fingerprint_material}
        except Exception:
            raise RuntimeError(SAFE_UNAVAILABLE) from None
        self.coordination_key_id, self.coordination_keys = _load_keyring(
            "KG_MEMBER_ENROLLMENT_COORDINATION_CURRENT_KEY_ID", "KG_MEMBER_ENROLLMENT_COORDINATION_KEYRING_JSON"
        )
        self.request_digest_key_id, self.request_digest_keys = _load_keyring(
            "KG_MEMBER_ENROLLMENT_REQUEST_DIGEST_CURRENT_KEY_ID",
            "KG_MEMBER_ENROLLMENT_REQUEST_DIGEST_KEYRING_JSON",
        )
        self.audit_digest_key_id, self.audit_digest_keys = _load_keyring(
            "KG_MEMBER_ENROLLMENT_AUDIT_DIGEST_CURRENT_KEY_ID",
            "KG_MEMBER_ENROLLMENT_AUDIT_DIGEST_KEYRING_JSON",
        )
        self.outbox_digest_key_id, self.outbox_digest_keys = _load_keyring(
            "KG_MEMBER_ENROLLMENT_OUTBOX_DIGEST_CURRENT_KEY_ID",
            "KG_MEMBER_ENROLLMENT_OUTBOX_DIGEST_KEYRING_JSON",
        )
        self.consent_digest_key_id, self.consent_digest_keys = _load_keyring(
            "KG_MEMBER_ENROLLMENT_CONSENT_DIGEST_CURRENT_KEY_ID",
            "KG_MEMBER_ENROLLMENT_CONSENT_DIGEST_KEYRING_JSON",
        )
        used: dict[bytes, str] = {}
        for domain, ring in (
            ("pii", self.pii_keys), ("lookup", self.lookup_keys), ("code", self.code_keys),
            ("replay", self.replay_keys), ("delivery", self.delivery_keys),
            ("fingerprint", self.fingerprint_keys), ("coordination", self.coordination_keys),
            ("request-digest", self.request_digest_keys),
            ("audit-digest", self.audit_digest_keys),
            ("outbox-digest", self.outbox_digest_keys),
            ("consent-digest", self.consent_digest_keys),
        ):
            for material in ring.values():
                if material in used and used[material] != domain:
                    raise RuntimeError(SAFE_UNAVAILABLE) from None
                used[material] = domain

    @staticmethod
    def _aad(field: str, tenant_public_id: UUID, object_id: UUID, key_id: str) -> bytes:
        if field not in {"invitation-phone", "identity-real-name", "identity-number", "identity-birth-date"}:
            raise RuntimeError(SAFE_UNAVAILABLE) from None
        if not isinstance(tenant_public_id, UUID) or not isinstance(object_id, UUID):
            raise RuntimeError(SAFE_UNAVAILABLE) from None
        if not key_id:
            raise RuntimeError(SAFE_UNAVAILABLE) from None
        tenant_uuid = UUID(str(tenant_public_id))
        object_uuid = UUID(str(object_id))
        return f"SLICE3_PII_ENCRYPTION_V1\0{field}\0{tenant_uuid}\0{object_uuid}\0{key_id}".encode()

    def encrypt(self, value: str, *, field: str, tenant_public_id: UUID, object_id: UUID) -> tuple[bytes, str]:
        nonce = secrets.token_bytes(12)
        encrypted = AESGCM(self.pii_keys[self.pii_key_id]).encrypt(
            nonce, value.encode(), self._aad(field, tenant_public_id, object_id, self.pii_key_id)
        )
        return nonce + encrypted, self.pii_key_id

    def decrypt(self, value: bytes, key_id: str, *, field: str, tenant_public_id: UUID, object_id: UUID) -> str:
        key = self.pii_keys.get(key_id)
        if key is None or len(value) < 29:
            raise RuntimeError(SAFE_UNAVAILABLE) from None
        try:
            return AESGCM(key).decrypt(
                value[:12], value[12:], self._aad(field, tenant_public_id, object_id, key_id)
            ).decode()
        except Exception:
            raise RuntimeError(SAFE_UNAVAILABLE) from None

    @staticmethod
    def _replay_aad(
        actor_scope: str, operation: str, target_id: UUID, key: str, key_id: str
    ) -> bytes:
        if not actor_scope or not operation or not isinstance(target_id, UUID) or not key:
            raise RuntimeError(SAFE_UNAVAILABLE) from None
        if not key_id:
            raise RuntimeError(SAFE_UNAVAILABLE) from None
        normalized_target_id = UUID(int=target_id.int)
        return f"SLICE3_REPLAY_ENCRYPTION_V1\0{actor_scope}\0{operation}\0{normalized_target_id}\0{key}\0{key_id}".encode()

    def encrypt_replay(
        self, value: object, *, actor_scope: str, operation: str,
        target_id: UUID, key: str,
    ) -> tuple[bytes, str]:
        nonce = secrets.token_bytes(12)
        encrypted = AESGCM(self.replay_keys[self.replay_key_id]).encrypt(
            nonce, _canonical(value),
            self._replay_aad(actor_scope, operation, target_id, key, self.replay_key_id),
        )
        return nonce + encrypted, self.replay_key_id

    def decrypt_replay(
        self, value: bytes, key_id: str, *, actor_scope: str, operation: str,
        target_id: UUID, key: str,
    ) -> object:
        material = self.replay_keys.get(key_id)
        if material is None or len(value) < 29:
            raise RuntimeError(SAFE_UNAVAILABLE) from None
        try:
            plain = AESGCM(material).decrypt(
                value[:12], value[12:],
                self._replay_aad(actor_scope, operation, target_id, key, key_id),
            )
            return json.loads(plain)
        except Exception:
            raise RuntimeError(SAFE_UNAVAILABLE) from None

    @staticmethod
    def _hmac(key: bytes, domain: str, value: str) -> str:
        return hmac.new(key, f"phase1-slice3/{domain}/v1\0{value}".encode(), hashlib.sha256).hexdigest()

    def digest_candidates(self, domain: str, value: str) -> tuple[tuple[str, str], ...]:
        return tuple((key_id, self._hmac(key, domain, value)) for key_id, key in sorted(self.lookup_keys.items()))

    def current_digest(self, domain: str, value: str) -> tuple[str, str]:
        return self.lookup_key_id, self._hmac(self.lookup_keys[self.lookup_key_id], domain, value)

    def invitation_phone_coordination_digest(self, value: str) -> str:
        return self._hmac(
            self.coordination_keys[self.coordination_key_id],
            "SLICE3_INVITATION_PHONE_V1",
            value,
        )

    def code_digest(self, value: str, key_id: str | None = None) -> str:
        selected = key_id or self.code_key_id
        key = self.code_keys.get(selected)
        if key is None:
            raise RuntimeError(SAFE_UNAVAILABLE) from None
        return self._hmac(key, "invitation-code", value)

    def identity_fingerprint(self, value: str) -> tuple[str, str]:
        material = self.fingerprint_keys[self.fingerprint_key_id]
        return self.fingerprint_key_id, hmac.new(
            material,
            f"identity-submission:id-card:v1:{value}".encode(),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _canonical_hmac(key: bytes, separator: str, value: object) -> str:
        return hmac.new(
            key,
            separator.encode() + b"\0" + _canonical(value),
            hashlib.sha256,
        ).hexdigest()

    def request_digest(self, value: object) -> str:
        return self._canonical_hmac(
            self.request_digest_keys[self.request_digest_key_id],
            "SLICE3_REQUEST_DIGEST_V1",
            value,
        )

    def audit_digest(self, value: object) -> str:
        return self._canonical_hmac(
            self.audit_digest_keys[self.audit_digest_key_id],
            "SLICE3_AUDIT_DIGEST_V1",
            value,
        )

    def outbox_digest(self, value: object) -> str:
        return self._canonical_hmac(
            self.outbox_digest_keys[self.outbox_digest_key_id],
            "SLICE3_OUTBOX_DIGEST_V1",
            value,
        )

    def consent_digest(self, value: object) -> str:
        return self._canonical_hmac(
            self.consent_digest_keys[self.consent_digest_key_id],
            "SLICE3_CONSENT_DIGEST_V1",
            value,
        )

    @staticmethod
    def _material_check(material: bytes) -> str:
        return hashlib.sha256(b"SLICE3_KEY_CHECK_V1\0" + material).hexdigest()

    def digest_guard_payload(self, kind: str) -> dict[str, str]:
        domains = {
            "request": (
                self.request_digest_key_id,
                self.request_digest_keys[self.request_digest_key_id],
            ),
            "audit": (
                self.audit_digest_key_id,
                self.audit_digest_keys[self.audit_digest_key_id],
            ),
            "outbox": (
                self.outbox_digest_key_id,
                self.outbox_digest_keys[self.outbox_digest_key_id],
            ),
            "consent": (
                self.consent_digest_key_id,
                self.consent_digest_keys[self.consent_digest_key_id],
            ),
            "delivery": (
                self.delivery_key_id,
                self.delivery_keys[self.delivery_key_id],
            ),
        }
        allowed = {
            "enrollment_writer": ("request", "audit", "outbox", "consent"),
            "review_writer": ("request", "audit", "outbox", "consent"),
            "case_writer": ("request", "audit", "outbox"),
            "workflow_worker": ("audit", "outbox", "delivery"),
        }.get(kind)
        if allowed is None:
            raise RuntimeError(SAFE_UNAVAILABLE) from None
        return {
            key: value
            for domain in allowed
            for key, value in (
                (f"{domain}_key_id", domains[domain][0]),
                (f"{domain}_material_check", self._material_check(domains[domain][1])),
            )
        }

    def short_code(self) -> str:
        return f"{secrets.randbelow(1_000_000):06d}"


class CommitOutcome(StrEnum):
    COMMITTED = "COMMITTED"
    NOT_COMMITTED = "NOT_COMMITTED"
    UNKNOWN = "UNKNOWN"


async def commit_with_confirmation(
    session,
    *,
    confirm: Callable[[], Awaitable[CommitOutcome]],
) -> CommitOutcome:
    try:
        await session.commit()
        return CommitOutcome.COMMITTED
    except BaseException as error:
        if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise
        try:
            await session.rollback()
        except Exception:
            pass
        try:
            outcome = await confirm()
        except Exception:
            outcome = CommitOutcome.UNKNOWN
        if outcome is CommitOutcome.COMMITTED:
            return outcome
        if outcome is CommitOutcome.NOT_COMMITTED:
            raise RuntimeError(SAFE_UNAVAILABLE) from None
        raise RuntimeError("COMMIT_OUTCOME_UNKNOWN") from None


@dataclass(frozen=True, slots=True)
class MutationContext:
    actor: CurrentUser
    tenant_id: int | None
    tenant_public_id: UUID
    idempotency_key: str
    request_id: UUID
    platform_scope: bool = False

    @property
    def actor_scope(self) -> str:
        scope = "platform" if self.platform_scope else f"tenant:{self.tenant_id}"
        return f"user:{self.actor.id}:{scope}"


class MemberEnrollmentService:
    def __init__(
        self,
        repository: MemberEnrollmentRepository,
        *,
        secrets_port: MemberEnrollmentSecrets,
        now: Callable[[], datetime] = utcnow,
        uuid_generator: Uuid7Generator | None = None,
    ) -> None:
        self.repo = repository
        self.secrets = secrets_port
        self.now = now
        self.uuids = uuid_generator or Uuid7Generator()

    async def require_proxy_permission(
        self,
        enrollment: Mapping[str, Any],
        member_id: UUID,
        permission: str,
        *,
        require_full_service_permissions: bool = False,
    ) -> None:
        if enrollment["subject_member_id"] == member_id:
            return
        if (
            enrollment["mode"] != "PROXY_ELDER"
            or enrollment["proxy_member_id"] != member_id
        ):
            raise MemberEnrollmentConflict("PROXY_PERMISSION_FORBIDDEN")
        grant = await self.repo.proxy_grant_by_enrollment_for_update(
            enrollment["enrollment_id"]
        )
        now = self.now()
        required = (
            set(REQUIRED_PROXY_PERMISSIONS)
            if require_full_service_permissions
            else {permission}
        )
        if (
            grant is None
            or grant["principal_member_id"] != enrollment["subject_member_id"]
            or grant["proxy_member_id"] != member_id
            or grant["status"] != "ACTIVE"
            or grant["valid_from"] is None
            or grant["valid_from"] > now
            or (grant["valid_until"] is not None and grant["valid_until"] < now)
            or not required <= set(grant["permission_codes"])
        ):
            raise MemberEnrollmentConflict("PROXY_PERMISSION_FORBIDDEN")

    async def require_proxy_service_authorization(
        self, enrollment: Mapping[str, Any]
    ) -> None:
        if enrollment["mode"] == "SELF":
            return
        grant = await self.repo.proxy_grant_by_enrollment_for_update(
            enrollment["enrollment_id"]
        )
        now = self.now()
        if (
            grant is None
            or grant["principal_member_id"] != enrollment["subject_member_id"]
            or grant["proxy_member_id"] != enrollment["proxy_member_id"]
            or grant["status"] != "ACTIVE"
            or grant["valid_from"] is None
            or grant["valid_from"] > now
            or (grant["valid_until"] is not None and grant["valid_until"] < now)
            or not set(REQUIRED_PROXY_PERMISSIONS) <= set(grant["permission_codes"])
        ):
            raise MemberEnrollmentConflict("PROXY_GRANT_INVALID")

    async def create_invitation(
        self, context: MutationContext, request: CreateMemberInvitationRequest
    ) -> tuple[UUID, str]:
        if context.tenant_id is None or await self.repo.readiness_guard(context.tenant_id) is None:
            raise MemberEnrollmentConflict("SERVICE_NOT_READY")
        invitation_id = self.uuids.generate()
        await self.repo.lock_operation(context.actor_scope, "INVITATION_CREATE", invitation_id, context.idempotency_key)
        await self.repo.lock_invitation_phone(
            context.tenant_id,
            self.secrets.invitation_phone_coordination_digest(request.phone),
        )
        candidates = self.secrets.digest_candidates("invitation-phone", request.phone)
        if await self.repo.open_invitation_candidates(context.tenant_id, candidates):
            raise MemberEnrollmentConflict("INVITATION_OPEN_CONFLICT")
        phone_key_id, phone_digest = self.secrets.current_digest("invitation-phone", request.phone)
        ciphertext, encryption_key_id = self.secrets.encrypt(
            request.phone,
            field="invitation-phone",
            tenant_public_id=context.tenant_public_id,
            object_id=invitation_id,
        )
        code = self.secrets.short_code()
        now = self.now()
        await self.repo.add_invitation(
            invitation_id=invitation_id,
            tenant_id=context.tenant_id,
            mode=request.mode,
            phone_ciphertext=ciphertext,
            phone_key_id=encryption_key_id,
            phone_digest=phone_digest,
            phone_digest_key_id=phone_key_id,
            phone_masked="*******" + request.phone[-4:],
            code_digest=self.secrets.code_digest(code),
            code_key_id=self.secrets.code_key_id,
            status="INVITED",
            failed_attempts=0,
            expires_at=now + timedelta(hours=24),
            issued_by=context.actor.id,
            issued_at=now,
            version=1,
        )
        await self._audit_outbox(
            context, invitation_id, "MEMBER_INVITATION_CREATED", pre={}, post={"status": "INVITED", "version": 1}
        )
        return invitation_id, code

    async def resend_invitation(
        self, context: MutationContext, invitation_id: UUID, *, expected_version: int
    ) -> tuple[UUID, str]:
        await self.repo.lock_operation(
            context.actor_scope, "INVITATION_RESEND", invitation_id,
            context.idempotency_key,
        )
        row = await self.repo.invitation_for_update(invitation_id)
        if row is None or row["tenant_id"] != context.tenant_id:
            raise MemberEnrollmentConflict("INVITATION_NOT_FOUND")
        if row["version"] != expected_version:
            raise MemberEnrollmentConflict("VERSION_CONFLICT")
        if row["status"] != "INVITED" or row["failed_attempts"] >= 5:
            raise MemberEnrollmentConflict("INVITATION_EXHAUSTED")
        code = self.secrets.short_code()
        now = self.now()
        await self.repo.update_invitation(
            invitation_id,
            code_digest=self.secrets.code_digest(code),
            code_key_id=self.secrets.code_key_id,
            failed_attempts=0,
            expires_at=now + timedelta(hours=24),
            issued_at=now,
            version=row["version"] + 1,
        )
        await self._audit_outbox(
            context, invitation_id, "MEMBER_INVITATION_RESENT",
            pre={"status": row["status"], "version": row["version"]},
            post={"status": "INVITED", "version": row["version"] + 1},
        )
        return invitation_id, code

    async def revoke_invitation(
        self, context: MutationContext, invitation_id: UUID,
        request: ReasonedVersionRequest,
    ) -> MemberServiceInvitation:
        await self.repo.lock_operation(
            context.actor_scope, "INVITATION_REVOKE", invitation_id,
            context.idempotency_key,
        )
        row = await self.repo.invitation_for_update(invitation_id)
        if row is None or row["tenant_id"] != context.tenant_id:
            raise MemberEnrollmentConflict("INVITATION_NOT_FOUND")
        value = MemberServiceInvitation(
            invitation_id=invitation_id, tenant_id=row["tenant_id"], mode=row["mode"],
            status=InvitationStatus(row["status"]), expires_at=row["expires_at"],
            version=row["version"], failed_attempts=row["failed_attempts"],
            accepted_at=row["accepted_at"], revoked_at=row["revoked_at"],
        ).revoke(expected_version=request.expected_version, now=self.now())
        await self.repo.update_invitation(
            invitation_id, status=value.status.value, revoked_at=value.revoked_at,
            version=value.version,
        )
        await self._audit_outbox(
            context, invitation_id, "MEMBER_INVITATION_REVOKED",
            pre={"status": row["status"], "version": row["version"]},
            post={"status": value.status.value, "version": value.version},
        )
        return value

    async def accept_invitation(
        self,
        context: MutationContext,
        request: AcceptEnrollmentRequest,
        *,
        actor_member_id: UUID,
        adult_birth_date: date | None = None,
        adult_eligible: bool | None = None,
    ) -> tuple[UUID, UUID]:
        await self.repo.lock_operation(context.actor_scope, "ENROLLMENT_ACCEPT", request.invitation_id, context.idempotency_key)
        row = await self.repo.invitation_for_update(request.invitation_id)
        if row is None or row["tenant_id"] != context.tenant_id:
            raise MemberEnrollmentConflict("INVITATION_NOT_FOUND")
        domain = MemberServiceInvitation(
            invitation_id=row["invitation_id"], tenant_id=row["tenant_id"], mode=row["mode"],
            status=InvitationStatus(row["status"]), expires_at=row["expires_at"],
            version=row["version"], failed_attempts=row["failed_attempts"],
            accepted_at=row["accepted_at"], revoked_at=row["revoked_at"],
        )
        if domain.status is not InvitationStatus.INVITED or domain.failed_attempts >= 5:
            raise MemberEnrollmentConflict("INVITATION_EXHAUSTED")
        phone_matches = any(
            key_id == row["phone_digest_key_id"] and value == row["phone_digest"]
            for key_id, value in self.secrets.digest_candidates("invitation-phone", request.phone)
        )
        code_matches = hmac.compare_digest(
            self.secrets.code_digest(request.short_code, row["code_key_id"]), row["code_digest"]
        )
        if not phone_matches or not code_matches:
            failed = domain.record_failed_attempt(now=self.now())
            await self.repo.update_invitation(
                request.invitation_id, status=failed.status.value,
                failed_attempts=failed.failed_attempts, version=failed.version,
            )
            if failed.status is InvitationStatus.EXPIRED:
                await self._audit_outbox(
                    context,
                    request.invitation_id,
                    "MEMBER_INVITATION_EXPIRED",
                    pre={"status": domain.status.value, "version": domain.version},
                    post={"status": failed.status.value, "version": failed.version},
                )
                raise InvitationAttemptRejected("INVITATION_EXHAUSTED")
            await self._audit_only(
                context,
                request.invitation_id,
                "MEMBER_INVITATION_CODE_REJECTED",
                pre={"status": domain.status.value, "version": domain.version},
                post={"status": failed.status.value, "version": failed.version},
            )
            raise InvitationAttemptRejected("INVITATION_CODE_INVALID")
        accepted = domain.accept(expected_version=domain.version, now=self.now())
        enrollment_id = self.uuids.generate()
        subject_member_id = actor_member_id
        proxy_member_id = None
        member_no = None
        if domain.mode == "PROXY_ELDER":
            if adult_eligible is not True and (
                adult_birth_date is None
                or not verified_adult_on(adult_birth_date, self.now().date())
            ):
                raise MemberEnrollmentConflict("PROXY_ADULT_IDENTITY_REQUIRED")
            await self.repo.lock_proxy_slot_boundary(actor_member_id)
            slots = await self.repo.active_proxy_slots(actor_member_id)
            slot_no = next((value for value in (1, 2) if value not in slots), None)
            if slot_no is None:
                raise MemberEnrollmentConflict("PROXY_LIMIT_REACHED")
            subject_member_id = self.uuids.generate()
            for _attempt in range(3):
                candidate = generate_member_no_candidate().value
                try:
                    async with self.repo.session.begin_nested():
                        await self.repo.add_controlled_member(
                            member_id=subject_member_id,
                            member_no=candidate,
                            created_at=self.now(),
                        )
                except IntegrityError as error:
                    if getattr(error.orig, "constraint_name", None) != "uq_member_member_no":
                        raise
                    continue
                member_no = candidate
                break
            if member_no is None:
                raise MemberEnrollmentConflict("MEMBER_NO_ALLOCATION_CONFLICT")
            proxy_member_id = actor_member_id
        if await self.repo.active_enrollments_for_subject(subject_member_id):
            raise MemberEnrollmentConflict("ACTIVE_ENROLLMENT_EXISTS")
        now = self.now()
        await self.repo.add_enrollment(
            enrollment_id=enrollment_id, invitation_id=request.invitation_id,
            tenant_id=context.tenant_id, subject_member_id=subject_member_id,
            proxy_member_id=proxy_member_id, mode=domain.mode, status="ACCEPTED",
            service_scope_tags=[], accepted_at=now, created_at=now, updated_at=now, version=1,
        )
        if proxy_member_id is not None:
            await self.repo.add_bootstrap(
                bootstrap_id=self.uuids.generate(), enrollment_id=enrollment_id,
                member_id=subject_member_id, member_no=member_no,
                creation_source="controlled_proxy_enrollment", created_at=now,
                request_digest=self.secrets.request_digest(request),
            )
            grant_id = self.uuids.generate()
            grant = ProxyGrant.create_pending(
                grant_id=grant_id, enrollment_id=enrollment_id,
                principal_member_id=subject_member_id, proxy_member_id=proxy_member_id,
                slot_no=slot_no,
            )
            await self.repo.add_proxy_grant(
                grant_id=grant.grant_id, enrollment_id=enrollment_id,
                principal_member_id=subject_member_id, proxy_member_id=proxy_member_id,
                slot_no=slot_no, permission_codes=list(REQUIRED_PROXY_PERMISSIONS),
                status=grant.status.value, version=grant.version,
            )
        await self.repo.update_invitation(
            request.invitation_id, status=accepted.status.value,
            accepted_at=accepted.accepted_at, version=accepted.version,
        )
        await self._audit_outbox(
            context, enrollment_id, "MEMBER_ENROLLMENT_ACCEPTED", pre={},
            post={"status": "ACCEPTED", "version": 1, "mode": domain.mode},
        )
        return enrollment_id, subject_member_id

    async def expire_invitations(self, *, limit: int = 100) -> int:
        now = self.now()
        rows = await self.repo.expired_invitations_for_update(now=now, limit=limit)
        for row in rows:
            await self.repo.update_invitation(
                row["invitation_id"], status="EXPIRED", version=row["version"] + 1
            )
            context = MutationContext(
                actor=CurrentUser(
                    id=row["issued_by"], role="org_admin", tenant_id=row["tenant_id"]
                ),
                tenant_id=row["tenant_id"],
                tenant_public_id=UUID(int=0),
                idempotency_key=f"expiry:{row['invitation_id']}",
                request_id=self.uuids.generate(),
            )
            await self._audit_outbox(
                context,
                row["invitation_id"],
                "MEMBER_INVITATION_EXPIRED",
                pre={"status": row["status"], "version": row["version"]},
                post={"status": "EXPIRED", "version": row["version"] + 1},
            )
        return len(rows)

    async def submit_identity(
        self,
        context: MutationContext,
        enrollment_id: UUID,
        request: IdentitySubmissionRequest,
        *,
        submitted_by_member_id: UUID,
    ) -> tuple[UUID, UUID]:
        await self.repo.lock_operation(context.actor_scope, "IDENTITY_SUBMIT", enrollment_id, context.idempotency_key)
        enrollment = await self.repo.enrollment_for_update(enrollment_id)
        if enrollment is None or enrollment["tenant_id"] != context.tenant_id:
            raise MemberEnrollmentConflict("ENROLLMENT_NOT_FOUND")
        await self.require_proxy_permission(
            enrollment, submitted_by_member_id, "IDENTITY_SUBMIT"
        )
        if enrollment["version"] != request.expected_version or enrollment["status"] != "ACCEPTED":
            raise MemberEnrollmentConflict("VERSION_CONFLICT")
        verification_id = self.uuids.generate()
        revision_id = self.uuids.generate()
        birth_date = parse_prc_resident_identity_birth_date(request.id_number)
        key_id, fingerprint = self.secrets.identity_fingerprint(request.id_number)
        await self.repo.lock_identity_fingerprint(fingerprint)
        real_name_cipher, real_name_key = self.secrets.encrypt(
            request.real_name, field="identity-real-name",
            tenant_public_id=context.tenant_public_id, object_id=revision_id,
        )
        id_cipher, id_key = self.secrets.encrypt(
            request.id_number, field="identity-number",
            tenant_public_id=context.tenant_public_id, object_id=revision_id,
        )
        birth_cipher, birth_key = self.secrets.encrypt(
            birth_date.isoformat(), field="identity-birth-date",
            tenant_public_id=context.tenant_public_id, object_id=revision_id,
        )
        now = self.now()
        await self.repo.add_verification(
            verification_id=verification_id, enrollment_id=enrollment_id,
            member_id=enrollment["subject_member_id"], current_revision_id=None,
            status="SUBMITTED", submitted_at=now, version=1,
        )
        await self.repo.add_identity_revision(
            revision_id=revision_id, verification_id=verification_id, revision_no=1,
            document_type="PRC_RESIDENT_ID", real_name_ciphertext=real_name_cipher,
            real_name_key_id=real_name_key, id_ciphertext=id_cipher, id_key_id=id_key,
            birth_date_ciphertext=birth_cipher, birth_date_key_id=birth_key,
            id_masked=request.id_number[:3] + "***********" + request.id_number[-4:],
            identity_fingerprint=fingerprint,
            fingerprint_key_id=key_id,
            input_digest=self.secrets.request_digest(request),
            submitted_by_member_id=submitted_by_member_id, created_at=now,
        )
        await self.repo.update_verification(verification_id, current_revision_id=revision_id)
        await self.repo.update_enrollment(
            enrollment_id, status="IDENTITY_SUBMITTED",
            current_identity_verification_id=verification_id, updated_at=now,
            version=enrollment["version"] + 1,
        )
        await self._audit_outbox(
            context, verification_id, "MEMBER_IDENTITY_SUBMITTED", pre={},
            post={"status": "SUBMITTED", "revision_id": revision_id, "version": 1},
        )
        return verification_id, revision_id

    async def resubmit_identity(
        self,
        context: MutationContext,
        enrollment_id: UUID,
        request: IdentityResubmitRequest,
        *,
        submitted_by_member_id: UUID,
    ) -> tuple[UUID, UUID]:
        await self.repo.lock_operation(
            context.actor_scope, "IDENTITY_RESUBMIT", enrollment_id,
            context.idempotency_key,
        )
        enrollment = await self.repo.enrollment_for_update(enrollment_id)
        if enrollment is None or enrollment["tenant_id"] != context.tenant_id:
            raise MemberEnrollmentConflict("ENROLLMENT_NOT_FOUND")
        await self.require_proxy_permission(
            enrollment, submitted_by_member_id, "IDENTITY_SUBMIT"
        )
        verification = await self.repo.verification_by_enrollment_for_update(enrollment_id)
        if verification is None or verification["status"] != "NEEDS_CORRECTION":
            raise MemberEnrollmentConflict("STATE_CONFLICT")
        if enrollment["version"] != request.expected_version:
            raise MemberEnrollmentConflict("VERSION_CONFLICT")
        decision = await self.repo.latest_correction_decision(
            verification["verification_id"]
        )
        changed = request.model_fields_set - {"document_type", "expected_version"}
        if decision is None or not changed or not changed <= set(decision["correction_fields"] or ()):
            raise MemberEnrollmentConflict("CORRECTION_SCOPE_CONFLICT")
        prior = await self.repo.identity_revision_for_update(
            verification["verification_id"], verification["current_revision_id"]
        )
        if prior is None:
            raise MemberEnrollmentConflict("IDENTITY_REVISION_STALE")
        revision_id = self.uuids.generate()
        real_name = request.real_name
        id_number = request.id_number
        if real_name is None:
            real_name = self.secrets.decrypt(
                prior["real_name_ciphertext"], prior["real_name_key_id"],
                field="identity-real-name", tenant_public_id=context.tenant_public_id,
                object_id=prior["revision_id"],
            )
        if id_number is None:
            id_number = self.secrets.decrypt(
                prior["id_ciphertext"], prior["id_key_id"],
                field="identity-number", tenant_public_id=context.tenant_public_id,
                object_id=prior["revision_id"],
            )
        birth_date = parse_prc_resident_identity_birth_date(id_number)
        _key_id, fingerprint = self.secrets.identity_fingerprint(id_number)
        await self.repo.lock_identity_fingerprint(fingerprint)
        real_cipher, real_key = self.secrets.encrypt(
            real_name, field="identity-real-name", tenant_public_id=context.tenant_public_id,
            object_id=revision_id,
        )
        id_cipher, id_key = self.secrets.encrypt(
            id_number, field="identity-number", tenant_public_id=context.tenant_public_id,
            object_id=revision_id,
        )
        birth_cipher, birth_key = self.secrets.encrypt(
            birth_date.isoformat(), field="identity-birth-date",
            tenant_public_id=context.tenant_public_id, object_id=revision_id,
        )
        now = self.now()
        await self.repo.add_identity_revision(
            revision_id=revision_id, verification_id=verification["verification_id"],
            revision_no=prior["revision_no"] + 1, document_type="PRC_RESIDENT_ID",
            real_name_ciphertext=real_cipher, real_name_key_id=real_key,
            id_ciphertext=id_cipher, id_key_id=id_key,
            birth_date_ciphertext=birth_cipher, birth_date_key_id=birth_key,
            id_masked=id_number[:3] + "***********" + id_number[-4:],
            identity_fingerprint=fingerprint,
            fingerprint_key_id=_key_id,
            input_digest=self.secrets.request_digest(request),
            submitted_by_member_id=submitted_by_member_id, created_at=now,
        )
        await self.repo.update_verification(
            verification["verification_id"], current_revision_id=revision_id,
            status="RESUBMITTED", submitted_at=now, institution_decision_id=None,
            platform_decision_id=None, institution_checked_at=None,
            platform_decided_at=None, version=verification["version"] + 1,
        )
        await self.repo.update_enrollment(
            enrollment_id, status="RESUBMITTED", updated_at=now,
            version=enrollment["version"] + 1,
        )
        await self._audit_outbox(
            context, verification["verification_id"], "MEMBER_IDENTITY_RESUBMITTED",
            pre={"revision_id": prior["revision_id"], "version": verification["version"]},
            post={"revision_id": revision_id, "version": verification["version"] + 1},
        )
        return verification["verification_id"], revision_id

    async def institution_identity_check(
        self,
        context: MutationContext,
        verification_id: UUID,
        request: InstitutionIdentityCheckRequest,
    ) -> IdentityVerification:
        await self.repo.lock_operation(context.actor_scope, "INSTITUTION_IDENTITY_CHECK", verification_id, context.idempotency_key)
        row = await self.repo.verification_for_update(verification_id)
        if row is None:
            raise MemberEnrollmentConflict("IDENTITY_REVIEW_NOT_FOUND")
        value = IdentityVerification(
            verification_id=verification_id, current_revision_id=row["current_revision_id"],
            status=IdentityStatus(row["status"]), version=row["version"],
        ).institution_decide(
            revision_id=request.revision_id, decision=request.decision,
            expected_version=request.expected_version,
        )
        decision_id = self.uuids.generate()
        now = self.now()
        await self.repo.add_review_decision(
            decision_id=decision_id, verification_id=verification_id,
            revision_id=request.revision_id, phase="INSTITUTION",
            reviewer_user_id=context.actor.id, decision=request.decision,
            reason_code=request.reason_code,
            correction_fields=list(request.correction_fields) or None,
            attestation_code=request.attestation_code,
            request_digest=self.secrets.request_digest(request),
            evidence_digest=self.secrets.audit_digest(
                {"decision": request.decision, "revision_id": request.revision_id}
            ),
            created_at=now,
        )
        await self.repo.update_verification(
            verification_id, status=value.status.value, institution_decision_id=decision_id,
            institution_checked_at=now, version=value.version,
        )
        enrollment = await self.repo.enrollment_by_verification_for_update(verification_id)
        if enrollment is None or enrollment["tenant_id"] != context.tenant_id:
            raise MemberEnrollmentConflict("ENROLLMENT_NOT_FOUND")
        if request.decision == "CHECKED":
            expected_attestation = (
                "PRINCIPAL_PRESENT_AND_AUTHORIZED_PROXY"
                if enrollment["mode"] == "PROXY_ELDER"
                else "OFFLINE_IDENTITY_CHECKED"
            )
            if request.attestation_code != expected_attestation:
                raise MemberEnrollmentConflict("INVALID_REQUEST")
        enrollment_status = {
            "INSTITUTION_CHECKED": "INSTITUTION_CHECKED",
            "NEEDS_CORRECTION": "NEEDS_CORRECTION",
            "REJECTED": "REJECTED",
        }[value.status.value]
        await self.repo.update_enrollment(
            enrollment["enrollment_id"], status=enrollment_status, updated_at=now,
            version=enrollment["version"] + 1,
        )
        event = {
            "INSTITUTION_CHECKED": "MEMBER_IDENTITY_INSTITUTION_CHECKED",
            "NEEDS_CORRECTION": "MEMBER_IDENTITY_CORRECTION_REQUESTED",
            "REJECTED": "MEMBER_IDENTITY_REJECTED",
        }[value.status.value]
        await self._audit_outbox(
            context, verification_id, event,
            pre={"status": row["status"], "version": row["version"]},
            post={"status": value.status.value, "version": value.version,
                  "revision_id": request.revision_id, "decision_id": decision_id},
        )
        return value

    async def claim_identity_review(
        self, context: MutationContext, verification_id: UUID, *, expected_version: int
    ) -> IdentityVerification:
        await self.repo.lock_operation(
            context.actor_scope, "IDENTITY_REVIEW_CLAIM", verification_id,
            context.idempotency_key,
        )
        row = await self.repo.verification_for_update(verification_id)
        if row is None:
            raise MemberEnrollmentConflict("IDENTITY_REVIEW_NOT_FOUND")
        value = IdentityVerification(
            verification_id=verification_id,
            current_revision_id=row["current_revision_id"],
            status=IdentityStatus(row["status"]), version=row["version"],
        ).claim(expected_version=expected_version)
        await self.repo.update_verification(
            verification_id, status=value.status.value, version=value.version
        )
        await self.repo.add_audit(
            actor_scope=context.actor_scope, action="IDENTITY_REVIEW_CLAIMED",
            object_id=verification_id, result="SUCCESS", request_id=context.request_id,
            preimage_digest=self.secrets.audit_digest(
                {"status": row["status"], "version": row["version"]}
            ),
            postimage_digest=self.secrets.audit_digest(
                {"status": value.status.value, "version": value.version}
            ),
            created_at=self.now(),
        )
        return value

    async def platform_identity_decide(
        self,
        context: MutationContext,
        verification_id: UUID,
        request: PlatformIdentityDecisionRequest,
        *,
        source_member_id: UUID,
        enrollment_mode: str,
        access_token_digest: str,
        currentness_digest: str,
    ) -> IdentityVerification:
        await self.repo.lock_identity_review_boundary(verification_id)
        if not await self.repo.reviewer_claim_is_current(
            verification_id, context.actor.id
        ):
            raise MemberEnrollmentConflict("STEP_UP_FORBIDDEN")
        await self.repo.lock_operation(
            context.actor_scope, "IDENTITY_REVIEW_DECIDE", verification_id,
            context.idempotency_key,
        )
        row = await self.repo.verification_for_update(verification_id)
        if row is None:
            raise MemberEnrollmentConflict("IDENTITY_REVIEW_NOT_FOUND")
        enrollment = await self.repo.review_enrollment_for_update(row["enrollment_id"])
        if enrollment is None or enrollment["status"] != "INSTITUTION_CHECKED":
            raise MemberEnrollmentConflict("STATE_CONFLICT")
        institution = await self.repo.institution_decision(row["institution_decision_id"])
        if institution is None or institution["revision_id"] != request.revision_id or institution["decision"] != "CHECKED":
            raise MemberEnrollmentConflict("IDENTITY_REVISION_STALE")
        if enrollment_mode == "PROXY_ELDER" and request.decision == "APPROVED" and request.represented_elder_eligible is not True:
            raise MemberEnrollmentConflict("PROXY_GRANT_INVALID")
        if enrollment_mode == "SELF" and request.represented_elder_eligible is not None:
            raise MemberEnrollmentConflict("INVALID_REQUEST")
        step_up = await self.repo.canonical_step_up_access_for_update(
            verification_id=verification_id,
            revision_id=request.revision_id,
            reviewer_user_id=context.actor.id,
            access_token_digest=access_token_digest,
            currentness_digest=currentness_digest,
            now=self.now(),
        )
        if (
            step_up is None
            or step_up["status"] != "CONSUMED"
            or step_up["consumed_at"] is None
            or step_up["version"] != 2
        ):
            raise MemberEnrollmentConflict("STEP_UP_FORBIDDEN")
        enrollment_preimage = await self.repo.review_enrollment_preimage_for_update(
            verification_id=verification_id,
            enrollment_id=enrollment["enrollment_id"],
            reviewer_user_id=context.actor.id,
        )
        if enrollment_preimage is None:
            raise MemberEnrollmentConflict("STEP_UP_FORBIDDEN")
        if (
            enrollment_preimage["status"] != enrollment["status"]
            or enrollment_preimage["version"] != enrollment["version"]
        ):
            raise MemberEnrollmentConflict("STATE_CONFLICT")
        value = IdentityVerification(
            verification_id=verification_id,
            current_revision_id=row["current_revision_id"],
            status=IdentityStatus(row["status"]), version=row["version"],
        ).platform_decide(
            revision_id=request.revision_id, decision=request.decision,
            expected_version=request.expected_version,
        )
        decision_id = self.uuids.generate()
        now = self.now()
        evidence = {
            "verification_id": verification_id, "revision_id": request.revision_id,
            "institution_decision_id": institution["decision_id"],
            "decision": request.decision,
            "represented_elder_eligible": request.represented_elder_eligible,
            "next_version": value.version,
        }
        evidence_digest = self.secrets.audit_digest(evidence)
        await self.repo.add_review_decision(
            decision_id=decision_id, verification_id=verification_id,
            revision_id=request.revision_id, phase="PLATFORM",
            reviewer_user_id=context.actor.id, decision=request.decision,
            reason_code=request.reason_code,
            correction_fields=list(request.correction_fields) or None,
            represented_elder_eligible=request.represented_elder_eligible,
            request_digest=self.secrets.request_digest(request),
            evidence_digest=evidence_digest,
            created_at=now,
        )
        await self.repo.update_verification(
            verification_id, status=value.status.value,
            platform_decision_id=decision_id, platform_decided_at=now,
            version=value.version,
        )
        enrollment_status = {
            "APPROVED": "IDENTITY_VERIFIED",
            "NEEDS_CORRECTION": "NEEDS_CORRECTION",
            "REJECTED": "REJECTED",
        }[request.decision]
        await self.repo.update_enrollment(
            enrollment["enrollment_id"], status=enrollment_status,
            identity_verified_at=now if request.decision == "APPROVED" else None,
            updated_at=now, version=enrollment["version"] + 1,
        )
        if request.decision == "APPROVED":
            await self.repo.claim_identity_subject(
                claim_id=self.uuids.generate(), user_ref=None,
                member_id=source_member_id, identity_fingerprint=(
                    await self.repo.current_identity_revision(verification_id, request.revision_id)
                )["identity_fingerprint"],
                fingerprint_key_id=(
                    await self.repo.current_identity_revision(
                        verification_id, request.revision_id
                    )
                )["fingerprint_key_id"],
                source_kind="SLICE3", p1_submission_id=None,
                p1_decision_ref=None, slice3_revision_id=request.revision_id,
                slice3_decision_id=decision_id, source_facts_version=value.version,
                source_evidence_digest=evidence_digest, adult_eligible=None,
                represented_elder_eligible=request.represented_elder_eligible,
                claimed_at=now,
            )
            if enrollment_mode == "PROXY_ELDER":
                grant = await self.repo.proxy_grant_by_enrollment_for_update(
                    row["enrollment_id"]
                )
                if grant is None or grant["status"] != "CONSENT_PENDING":
                    raise MemberEnrollmentConflict("PROXY_GRANT_INVALID")
                bound = ProxyGrant(
                    grant_id=grant["grant_id"], enrollment_id=grant["enrollment_id"],
                    principal_member_id=grant["principal_member_id"],
                    proxy_member_id=grant["proxy_member_id"], slot_no=grant["slot_no"],
                    status=ProxyGrantStatus(grant["status"]), version=grant["version"],
                    witness_decision_id=grant["witness_decision_id"],
                    authorization_document_version_id=grant["authorization_document_version_id"],
                    valid_from=grant["valid_from"], revoked_at=grant["revoked_at"],
                ).bind_witness(
                    witness_decision_id=decision_id, expected_version=grant["version"]
                )
                await self.repo.update_proxy_grant(
                    grant["grant_id"], witness_decision_id=decision_id,
                    version=bound.version,
                )
            event = "MEMBER_IDENTITY_VERIFIED"
        elif request.decision == "NEEDS_CORRECTION":
            event = "MEMBER_IDENTITY_CORRECTION_REQUESTED"
        else:
            event = "MEMBER_IDENTITY_REJECTED"
        await self._audit_outbox(
            context, verification_id, event,
            pre={"status": row["status"], "version": row["version"]},
            post={"status": value.status.value, "version": value.version,
                  "revision_id": request.revision_id, "decision_id": decision_id},
        )
        return value

    async def access_identity_pii(
        self,
        context: MutationContext,
        verification_id: UUID,
        request: PiiAccessRequest,
        *,
        currentness_digest: str,
        access_token_digest: str,
        password_valid: bool,
        request_digest: str,
        proof_values: Mapping[str, object],
        credential_proof_digest: str,
    ) -> dict[str, object]:
        await self.repo.lock_identity_review_boundary(verification_id)
        if not await self.repo.reviewer_claim_is_current(
            verification_id, context.actor.id
        ):
            raise MemberEnrollmentConflict("STEP_UP_FORBIDDEN")
        await self.repo.lock_operation(
            context.actor_scope, "IDENTITY_PII_ACCESS", verification_id,
            context.idempotency_key,
        )
        now = self.now()
        budget = await self.repo.reviewer_step_up_budget(
            proof_values=proof_values,
            credential_proof_digest=credential_proof_digest,
        )
        if budget is None:
            raise MemberEnrollmentConflict("STEP_UP_FORBIDDEN")
        self.repo.session.info[("slice3-step-up-result", context.request_id)] = {
            "result_variant": budget["result_variant"],
            "error_code": budget["error_code"],
            "failure_count": budget["failure_count"],
            "locked_until": budget["locked_until"],
            "budget_postimage_digest": budget["budget_postimage_digest"],
        }
        if budget["result_variant"] != "ELIGIBLE":
            return {"error_code": budget["error_code"]}
        revision_id = UUID(str(proof_values["current_revision_id"]))
        access_id = self.uuids.generate()
        nonce = self.uuids.generate()
        issued_postimage = self.secrets.audit_digest({
            "access_id": access_id, "verification_id": verification_id,
            "revision_id": revision_id, "reviewer_user_id": context.actor.id,
            "reason_code": request.reason_code, "status": "ISSUED", "version": 1,
        })
        await self.repo.add_pii_access(
            access_id=access_id, verification_id=verification_id,
            current_revision_id=revision_id, reviewer_user_id=context.actor.id,
            nonce=nonce, status="ISSUED", reason_code=request.reason_code,
            access_token_digest=access_token_digest,
            currentness_digest=currentness_digest,
            idempotency_key=context.idempotency_key,
            request_digest=request_digest,
            postimage_digest=issued_postimage, issued_at=now, consumed_at=None,
            expires_at=now + timedelta(seconds=15), version=1,
        )
        protected = await self.repo.reviewer_pii(
            access_id=access_id,
            nonce=nonce,
            verification_id=verification_id,
            reviewer_user_id=context.actor.id,
            revision_id=revision_id,
            access_token_digest=access_token_digest,
            currentness_digest=currentness_digest,
            request_id=context.request_id,
            actor_scope=context.actor_scope,
            request_digest=request_digest,
            reason_code=request.reason_code,
            preimage_digest=issued_postimage,
            credential_proof_marker=reviewer_credential_proof_marker(
                credential_proof_digest
            ),
            consumed_at=now,
            postimage_digest=self.secrets.audit_digest({
                "access_id": access_id, "verification_id": verification_id,
                "revision_id": revision_id, "reviewer_user_id": context.actor.id,
                "reason_code": request.reason_code, "status": "CONSUMED", "version": 2,
            }),
        )
        if protected is None:
            raise MemberEnrollmentConflict("IDENTITY_REVIEW_NOT_FOUND")
        response = {
            "review_id": verification_id,
            "revision_id": revision_id,
            "real_name": self.secrets.decrypt(
                protected["real_name_ciphertext"], protected["real_name_key_id"],
                field="identity-real-name", tenant_public_id=context.tenant_public_id,
                object_id=revision_id,
            ),
            "id_number": self.secrets.decrypt(
                protected["id_ciphertext"], protected["id_key_id"],
                field="identity-number", tenant_public_id=context.tenant_public_id,
                object_id=revision_id,
            ),
            "birth_date": date.fromisoformat(self.secrets.decrypt(
                protected["birth_date_ciphertext"], protected["birth_date_key_id"],
                field="identity-birth-date", tenant_public_id=context.tenant_public_id,
                object_id=revision_id,
            )),
            "access_id": access_id,
        }
        return response

    async def create_consent_document(
        self, context: MutationContext, request: CreateConsentDocumentRequest
    ) -> UUID:
        document_id = self.uuids.generate()
        await self.repo.lock_operation(
            context.actor_scope, "CONSENT_DOCUMENT_CREATE", document_id,
            context.idempotency_key,
        )
        now = self.now()
        manifest = [
            {"locale": item.locale, "title": item.title, "content_sha256": digest(item.body)}
            for item in request.renditions
        ]
        await self.repo.add_consent_document(
            document_version_id=document_id, document_type=request.document_type,
            semantic_version=request.semantic_version, status="DRAFT",
            requires_reconsent=True, manifest_digest=digest(manifest),
            created_at=now, version=1,
        )
        for item in request.renditions:
            await self.repo.add_consent_rendition(
                rendition_id=self.uuids.generate(), document_version_id=document_id,
                locale=item.locale, title=item.title, body=item.body,
                content_sha256=digest(item.body), created_at=now,
            )
        return document_id

    async def publish_consent_document(
        self,
        context: MutationContext,
        document_version_id: UUID,
        request: PublishConsentDocumentRequest,
    ) -> ConsentDocument:
        await self.repo.lock_operation(
            context.actor_scope, "CONSENT_DOCUMENT_PUBLISH", document_version_id,
            context.idempotency_key,
        )
        row = await self.repo.document_for_update(document_version_id)
        if row is None:
            raise MemberEnrollmentConflict("CONSENT_DOCUMENT_NOT_FOUND")
        renditions = await self.repo.document_renditions(document_version_id)
        if not any(item["locale"] == "zh-CN" for item in renditions):
            raise MemberEnrollmentConflict("CONSENT_RENDITION_INCOMPLETE")
        for prior in await self.repo.published_documents_for_update(row["document_type"]):
            if prior["document_version_id"] != document_version_id:
                retired_at = self.now()
                await self.repo.update_consent_document(
                    prior["document_version_id"], status="RETIRED",
                    retired_at=retired_at, version=prior["version"] + 1,
                )
                await self._audit_outbox(
                    context,
                    prior["document_version_id"],
                    "CONSENT_DOCUMENT_RETIRED",
                    pre={"status": prior["status"], "version": prior["version"]},
                    post={"status": "RETIRED", "version": prior["version"] + 1},
                )
        value = ConsentDocument(
            document_version_id=document_version_id,
            status=ConsentDocumentStatus(row["status"]), version=row["version"],
            effective_at=row["effective_at"], retired_at=row["retired_at"],
        ).publish(
            expected_version=request.expected_version,
            now=self.now(), effective_at=request.effective_at,
        )
        await self.repo.update_consent_document(
            document_version_id, status=value.status.value,
            effective_at=value.effective_at, published_by=context.actor.id,
            version=value.version,
        )
        await self._audit_outbox(
            context, document_version_id, "CONSENT_DOCUMENT_PUBLISHED",
            pre={"status": row["status"], "version": row["version"]},
            post={"status": value.status.value, "version": value.version},
        )
        return value

    async def retire_consent_document(
        self, context: MutationContext, document_version_id: UUID,
        request: ReasonedVersionRequest,
    ) -> ConsentDocument:
        await self.repo.lock_operation(
            context.actor_scope, "CONSENT_DOCUMENT_RETIRE", document_version_id,
            context.idempotency_key,
        )
        row = await self.repo.document_for_update(document_version_id)
        if row is None:
            raise MemberEnrollmentConflict("CONSENT_DOCUMENT_NOT_FOUND")
        value = ConsentDocument(
            document_version_id=document_version_id,
            status=ConsentDocumentStatus(row["status"]), version=row["version"],
            effective_at=row["effective_at"], retired_at=row["retired_at"],
        ).retire(expected_version=request.expected_version, now=self.now())
        await self.repo.update_consent_document(
            document_version_id, status=value.status.value,
            retired_at=value.retired_at, version=value.version,
        )
        await self._audit_outbox(
            context, document_version_id, "CONSENT_DOCUMENT_RETIRED",
            pre={"status": row["status"], "version": row["version"]},
            post={"status": value.status.value, "version": value.version},
        )
        return value

    async def record_consent(
        self,
        context: MutationContext,
        enrollment_id: UUID,
        request: RecordConsentRequest,
        *,
        subject_member_id: UUID,
        proxy_member_id: UUID | None,
        document_type: str,
        locale: str,
    ) -> UUID:
        await self.repo.lock_operation(
            context.actor_scope, "CONSENT_RECORD", enrollment_id,
            context.idempotency_key,
        )
        await self.repo.lock_consent_boundary(enrollment_id)
        enrollment = await self.repo.enrollment_for_update(enrollment_id)
        if enrollment is None or enrollment["version"] != request.expected_version:
            raise MemberEnrollmentConflict("VERSION_CONFLICT")
        verification = await self.repo.verification_by_enrollment_for_update(enrollment_id)
        if verification is None or verification["status"] != "VERIFIED":
            raise MemberEnrollmentConflict("IDENTITY_REQUIRED")
        if enrollment["status"] not in {
            "PLATFORM_REVIEWING", "IDENTITY_VERIFIED", "CONSENT_PENDING",
        }:
            raise MemberEnrollmentConflict("STATE_CONFLICT")
        documents = await self.repo.current_consent_documents((document_type,), locale)
        if len(documents) != 1:
            raise MemberEnrollmentConflict("CONSENT_VERSION_STALE")
        current = documents[0]
        if current["document_version_id"] != request.document_version_id or current["rendition_id"] != request.rendition_id:
            raise MemberEnrollmentConflict("CONSENT_VERSION_STALE")
        record_id = self.uuids.generate()
        now = self.now()
        status = request.choice
        predecessors = await self.repo.accepted_consents_for_update(
            enrollment_id, document_type
        )
        predecessor_id = predecessors[-1]["consent_record_id"] if predecessors else None
        for prior in predecessors:
            await self.repo.update_consent(
                prior["consent_record_id"], status="SUPERSEDED", version=prior["version"] + 1
            )
            await self._audit_outbox(
                context,
                prior["consent_record_id"],
                "CONSENT_SUPERSEDED",
                pre={"status": prior["status"], "version": prior["version"]},
                post={"status": "SUPERSEDED", "version": prior["version"] + 1},
            )
        await self.repo.add_consent(
            consent_record_id=record_id, enrollment_id=enrollment_id,
            subject_member_id=subject_member_id, proxy_member_id=proxy_member_id,
            document_type=document_type, document_version_id=request.document_version_id,
            rendition_id=request.rendition_id, purpose_codes=list(request.purpose_codes),
            choice=request.choice, status=status, presented_at=now,
            accepted_at=now if status == "ACCEPTED" else None,
            predecessor_id=predecessor_id,
            version=1,
        )
        await self.repo.update_enrollment(
            enrollment_id, status="CONSENT_PENDING",
            identity_verified_at=(
                enrollment["identity_verified_at"]
                or verification["platform_decided_at"]
                or now
            ),
            updated_at=now, version=enrollment["version"] + 1,
        )
        await self._audit_outbox(
            context, record_id, f"CONSENT_{status}", pre={},
            post={"status": status, "version": 1, "document_type": document_type},
        )
        if (
            document_type == "PROXY_AUTHORIZATION"
            and status == "ACCEPTED"
            and proxy_member_id is not None
        ):
            grant = await self.repo.proxy_grant_by_enrollment_for_update(enrollment_id)
            if grant is None:
                raise MemberEnrollmentConflict("PROXY_GRANT_INVALID")
            activated = ProxyGrant(
                grant_id=grant["grant_id"], enrollment_id=enrollment_id,
                principal_member_id=grant["principal_member_id"],
                proxy_member_id=grant["proxy_member_id"], slot_no=grant["slot_no"],
                status=ProxyGrantStatus(grant["status"]), version=grant["version"],
                witness_decision_id=grant["witness_decision_id"],
                authorization_document_version_id=grant["authorization_document_version_id"],
                valid_from=grant["valid_from"], revoked_at=grant["revoked_at"],
            ).activate(
                witness_decision_id=grant["witness_decision_id"],
                authorization_document_version_id=request.document_version_id,
                expected_version=grant["version"], now=now,
            )
            await self.repo.update_proxy_grant(
                grant["grant_id"], status=activated.status.value,
                authorization_document_version_id=request.document_version_id,
                valid_from=activated.valid_from, version=activated.version,
            )
            await self._audit_outbox(
                context, grant["grant_id"], "PROXY_GRANT_ACTIVATED",
                pre={"status": grant["status"], "version": grant["version"]},
                post={"status": activated.status.value, "version": activated.version},
            )
        return record_id

    async def withdraw_consent(
        self, context: MutationContext, consent_record_id: UUID,
        request: ReasonedVersionRequest,
    ) -> ConsentRecord:
        await self.repo.lock_operation(
            context.actor_scope, "CONSENT_WITHDRAW", consent_record_id,
            context.idempotency_key,
        )
        enrollment_id = await self.repo.consent_enrollment_id(consent_record_id)
        if enrollment_id is None:
            raise MemberEnrollmentConflict("CONSENT_RECORD_NOT_FOUND")
        await self.repo.lock_consent_boundary(enrollment_id)
        row = await self.repo.consent_for_update(consent_record_id)
        if row is None:
            raise MemberEnrollmentConflict("CONSENT_RECORD_NOT_FOUND")
        value = ConsentRecord(
            consent_record_id=consent_record_id, status=ConsentStatus(row["status"]),
            version=row["version"], withdrawn_at=row["withdrawn_at"],
        ).withdraw(expected_version=request.expected_version, now=self.now())
        await self.repo.update_consent(
            consent_record_id, status=value.status.value,
            withdrawn_at=value.withdrawn_at, version=value.version,
        )
        await self._audit_outbox(
            context, consent_record_id, "CONSENT_WITHDRAWN",
            pre={"status": row["status"], "version": row["version"]},
            post={"status": value.status.value, "version": value.version},
        )
        return value

    async def revoke_proxy(
        self, context: MutationContext, grant_id: UUID,
        request: ReasonedVersionRequest,
    ) -> ProxyGrant:
        await self.repo.lock_operation(
            context.actor_scope, "PROXY_REVOKE", grant_id, context.idempotency_key
        )
        row = await self.repo.proxy_grant_for_update(grant_id)
        if row is None:
            raise MemberEnrollmentConflict("PROXY_GRANT_NOT_FOUND")
        if row["version"] != request.expected_version or row["status"] not in {
            "CONSENT_PENDING", "ACTIVE"
        }:
            raise MemberEnrollmentConflict("STATE_CONFLICT")
        now = self.now()
        await self.repo.update_proxy_grant(
            grant_id, status="REVOKED", revoked_at=now, version=row["version"] + 1
        )
        await self._audit_outbox(
            context, grant_id, "PROXY_GRANT_REVOKED",
            pre={"status": row["status"], "version": row["version"]},
            post={"status": "REVOKED", "version": row["version"] + 1},
        )
        return ProxyGrant(
            grant_id=grant_id, enrollment_id=row["enrollment_id"],
            principal_member_id=row["principal_member_id"],
            proxy_member_id=row["proxy_member_id"], slot_no=row["slot_no"],
            status=ProxyGrantStatus.REVOKED, version=row["version"] + 1,
            witness_decision_id=row["witness_decision_id"],
            authorization_document_version_id=row["authorization_document_version_id"],
            valid_from=row["valid_from"], revoked_at=now,
        )

    async def create_assignment(
        self,
        context: MutationContext,
        enrollment_id: UUID,
        request: CreateAssignmentRequest,
    ) -> UUID:
        await self.repo.lock_operation(
            context.actor_scope, "ASSIGNMENT_CREATE", enrollment_id,
            context.idempotency_key,
        )
        await self.repo.lock_consent_boundary(enrollment_id)
        enrollment = await self.repo.enrollment_for_update(enrollment_id)
        if enrollment is None or enrollment["tenant_id"] != context.tenant_id:
            raise MemberEnrollmentConflict("ENROLLMENT_NOT_FOUND")
        if enrollment["version"] != request.expected_version or enrollment["status"] not in {"CONSENT_PENDING", "IDENTITY_VERIFIED"}:
            raise MemberEnrollmentConflict("VERSION_CONFLICT")
        verification = await self.repo.verification_by_enrollment_for_update(enrollment_id)
        if verification is None or verification["status"] != "VERIFIED":
            raise MemberEnrollmentConflict("IDENTITY_REQUIRED")
        required_document_types = (
            "USER_AGREEMENT", "PRIVACY_POLICY", "HEALTH_DATA_PROCESSING",
            "INSTITUTION_SERVICE", "NON_MEDICAL_RISK",
        ) + (("PROXY_AUTHORIZATION",) if enrollment["mode"] == "PROXY_ELDER" else ())
        accepted = {
            item["document_type"]: item
            for item in await self.repo.accepted_consents(enrollment_id)
        }
        current = {
            item["document_type"]: item
            for item in await self.repo.current_consent_documents(
                required_document_types, "zh-CN"
            )
        }
        if set(accepted) != set(required_document_types) or set(current) != set(required_document_types):
            raise MemberEnrollmentConflict("CONSENT_REQUIRED")
        if any(
            accepted[name]["document_version_id"] != current[name]["document_version_id"]
            for name in required_document_types
        ):
            raise MemberEnrollmentConflict("CONSENT_REQUIRED")
        await self.require_proxy_service_authorization(enrollment)
        readiness = await self.repo.readiness_guard(context.tenant_id)
        if readiness is None:
            raise MemberEnrollmentConflict("SERVICE_NOT_READY")
        if not await self.repo.assignment_candidate_guard(
            request.therapist_id, context.tenant_id, request.service_scope_tags
        ):
            raise MemberEnrollmentConflict("THERAPIST_NOT_ELIGIBLE")
        assignment_id = self.uuids.generate()
        now = self.now()
        await self.repo.add_assignment(
            assignment_id=assignment_id, enrollment_id=enrollment_id,
            tenant_id=context.tenant_id, subject_member_id=enrollment["subject_member_id"],
            therapist_id=request.therapist_id, status="PENDING_ACCEPTANCE",
            service_scope_tags=list(request.service_scope_tags), created_by=context.actor.id,
            created_at=now, version=1,
        )
        await self.repo.update_enrollment(
            enrollment_id, status="THERAPIST_PENDING", current_assignment_id=assignment_id,
            updated_at=now, version=enrollment["version"] + 1,
        )
        await self._audit_outbox(
            context, assignment_id, "PRIMARY_ASSIGNMENT_CREATED", pre={},
            post={"status": "PENDING_ACCEPTANCE", "version": 1},
        )
        return assignment_id

    async def decide_assignment(
        self, context: MutationContext, assignment_id: UUID,
        request: ReasonedVersionRequest, *, target: str,
    ) -> PrimaryTherapistAssignment:
        operation = "ASSIGNMENT_CANCEL" if target == "CANCELLED" else "ASSIGNMENT_DECLINE"
        await self.repo.lock_operation(
            context.actor_scope, operation, assignment_id, context.idempotency_key
        )
        row = await self.repo.assignment_for_update(assignment_id)
        if row is None:
            raise MemberEnrollmentConflict("ASSIGNMENT_NOT_FOUND")
        if row["version"] != request.expected_version or row["status"] != "PENDING_ACCEPTANCE":
            raise MemberEnrollmentConflict("STATE_CONFLICT")
        now = self.now()
        await self.repo.update_assignment(
            assignment_id, status=target, reason_code=request.reason_code,
            decided_at=now, version=row["version"] + 1,
        )
        event = "PRIMARY_ASSIGNMENT_CANCELLED" if target == "CANCELLED" else "PRIMARY_ASSIGNMENT_DECLINED"
        await self._audit_outbox(
            context, assignment_id, event,
            pre={"status": row["status"], "version": row["version"]},
            post={"status": target, "version": row["version"] + 1},
        )
        return PrimaryTherapistAssignment(
            assignment_id=assignment_id, status=AssignmentStatus(target),
            version=row["version"] + 1, decided_at=now,
        )

    async def accept_assignment(
        self,
        context: MutationContext,
        assignment_id: UUID,
        *,
        expected_version: int,
        required_document_types: tuple[str, ...],
    ) -> UUID:
        await self.repo.lock_operation(
            context.actor_scope, "ASSIGNMENT_ACCEPT", assignment_id,
            context.idempotency_key,
        )
        assignment = await self.repo.assignment_for_update(assignment_id)
        if assignment is None:
            raise MemberEnrollmentConflict("ASSIGNMENT_NOT_FOUND")
        subject_guard = await self.repo.assignment_subject_guard(assignment_id)
        if subject_guard is None or subject_guard["proxy_authorized"] is not True:
            raise MemberEnrollmentConflict("PROXY_GRANT_INVALID")
        await self.repo.lock_consent_boundary(assignment["enrollment_id"])
        enrollment = await self.repo.case_enrollment_preimage_for_update(
            assignment_id=assignment_id,
            enrollment_id=assignment["enrollment_id"],
            therapist_id=assignment["therapist_id"],
            actor_user_id=context.actor.id,
        )
        readiness = await self.repo.readiness_guard(assignment["tenant_id"])
        therapist = await self.repo.therapist_for_case_update(assignment["therapist_id"])
        if readiness is None:
            raise MemberEnrollmentConflict("SERVICE_NOT_READY")
        business_date = self.now().astimezone(ZoneInfo("Asia/Shanghai")).date()
        if (
            therapist is None
            or therapist["tenant_id"] != assignment["tenant_id"]
            or therapist["status"] != "APPROVED_ACTIVE"
            or therapist["current_qualification_version_id"] is None
            or therapist["qualification_valid_until"] < business_date
            or not set(assignment["service_scope_tags"]) <= set(therapist["service_tags"])
        ):
            raise MemberEnrollmentConflict("THERAPIST_NOT_ELIGIBLE")
        if enrollment is None or enrollment["status"] != "THERAPIST_PENDING":
            raise MemberEnrollmentConflict("STATE_CONFLICT")
        verification = await self.repo.case_verification(enrollment["enrollment_id"])
        if verification is None or verification["status"] != "VERIFIED":
            raise MemberEnrollmentConflict("IDENTITY_REQUIRED")
        accepted = {item["document_type"]: item for item in await self.repo.accepted_consents(enrollment["enrollment_id"])}
        current = {
            item["document_type"]: item
            for item in await self.repo.current_consent_documents(required_document_types, "zh-CN")
        }
        if set(accepted) != set(required_document_types) or set(current) != set(required_document_types):
            raise MemberEnrollmentConflict("CONSENT_REQUIRED")
        if any(accepted[k]["document_version_id"] != current[k]["document_version_id"] for k in required_document_types):
            raise MemberEnrollmentConflict("CONSENT_REQUIRED")
        value = PrimaryTherapistAssignment(
            assignment_id=assignment_id, status=AssignmentStatus(assignment["status"]),
            version=assignment["version"], decided_at=assignment["decided_at"],
        ).accept(
            expected_version=expected_version,
            active_case_count=therapist["active_case_count"],
            capacity_limit=therapist["capacity_limit"], now=self.now(),
        )
        case_id = self.uuids.generate()
        now = self.now()
        consent_digest = self.secrets.consent_digest(
            sorted((key, str(accepted[key]["document_version_id"])) for key in accepted)
        )
        await self.repo.add_service_case(
            case_id=case_id, enrollment_id=enrollment["enrollment_id"],
            subject_member_id=enrollment["subject_member_id"], tenant_id=assignment["tenant_id"],
            primary_therapist_id=assignment["therapist_id"], assignment_id=assignment_id,
            status="PREPARING", identity_verification_id=verification["verification_id"],
            identity_revision_id=verification["current_revision_id"],
            consent_set_digest=consent_digest,
            readiness_evidence_version=readiness["evidence_version"],
            readiness_result_digest=readiness["result_digest"],
            service_scope_tags=assignment["service_scope_tags"],
            created_at=now, updated_at=now, version=1,
        )
        await self.repo.update_assignment(
            assignment_id, status=value.status.value, service_case_id=case_id,
            decided_at=now, version=value.version,
        )
        await self.repo.update_enrollment(
            enrollment["enrollment_id"], status="CASE_CREATED", service_case_id=case_id,
            case_created_at=now, updated_at=now, version=enrollment["version"] + 1,
        )
        if not await self.repo.update_therapist_case_count(
            assignment["therapist_id"], expected_version=therapist["version"], now=now
        ):
            raise MemberEnrollmentConflict("THERAPIST_CAPACITY_REACHED")
        await self._audit_outbox(
            context, case_id, "SERVICE_CASE_PREPARING_CREATED", pre={},
            post={"status": "PREPARING", "version": 1, "assignment_id": assignment_id},
        )
        return case_id

    async def _audit_outbox(
        self,
        context: MutationContext,
        object_id: UUID,
        event_type: str,
        *,
        pre: object,
        post: object,
    ) -> UUID:
        event_id = self.uuids.generate()
        now = self.now()
        await self.repo.add_audit(
            actor_scope=context.actor_scope, action=event_type, object_id=object_id,
            result="SUCCESS", request_id=context.request_id,
            preimage_digest=self.secrets.audit_digest(pre),
            postimage_digest=self.secrets.audit_digest(post),
            created_at=now,
        )
        payload = {
            "v": 1,
            "event_id": str(event_id),
            "aggregate_id": str(object_id),
            "request_id": str(context.request_id),
        }
        await self.repo.add_outbox(
            event_id=event_id, event_type=event_type, aggregate_id=object_id,
            tenant_id=context.tenant_id,
            payload=payload,
            payload_digest=self.secrets.outbox_digest(payload),
            status="PENDING", attempts=0, created_at=now, version=1,
        )
        return event_id

    async def _audit_only(
        self,
        context: MutationContext,
        object_id: UUID,
        action: str,
        *,
        pre: object,
        post: object,
    ) -> None:
        await self.repo.add_audit(
            actor_scope=context.actor_scope,
            action=action,
            object_id=object_id,
            result="REJECTED",
            request_id=context.request_id,
            preimage_digest=self.secrets.audit_digest(pre),
            postimage_digest=self.secrets.audit_digest(post),
            created_at=self.now(),
        )


def safe_error_code(error: Exception) -> str:
    if isinstance(error, MemberEnrollmentConflict):
        value = str(error)
        if value and value.isascii() and value.replace("_", "").isalnum():
            return value
    return "DEPENDENCY_UNAVAILABLE"
