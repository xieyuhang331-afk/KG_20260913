from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sqlalchemy_mapping import map_core_model_classes
from app.core.uuid_generator import Uuid7Generator
from app.modules.auth.models import User
from app.modules.auth.service import hash_password
from app.modules.institution_onboarding.domain import (
    ApplicationStatus,
    CORRECTION_FIELD_TO_DRAFT,
    InstitutionApplication,
    InstitutionInvitation,
    InvitationStatus,
    OnboardingConflict,
    OnboardingForbidden,
    verify_totp,
)
from app.modules.institution_onboarding.models import (
    InstitutionApplicationModel,
    InstitutionApplicationRevisionModel,
    InstitutionInvitationModel,
    InstitutionLicenseModel,
    InstitutionOnboardingAccountModel,
    InstitutionOnboardingAuditModel,
    InstitutionOnboardingIdempotencyModel,
    InstitutionOnboardingOutboxModel,
)
from app.modules.institution_onboarding.repository import InstitutionOnboardingRepository
from app.modules.institution_onboarding.schemas import (
    ActivationRequest,
    ApplicationDraftRequest,
    ApplicationResubmitRequest,
    ApplicationSubmitRequest,
    InvitationCreate,
    InvitationResendRequest,
    InvitationRevokeRequest,
    ReviewDecisionRequest,
)
from app.modules.tenant.models import Tenant


LICENSE_TYPE_TO_CORRECTION_FIELD = {
    "BUSINESS_LICENSE": "business_license",
    "MEDICAL_INSTITUTION_LICENSE": "medical_institution_license",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OnboardingSecrets:
    def __init__(self) -> None:
        try:
            kek = base64.b64decode(os.environ["KG_ONBOARDING_PII_KEK_B64"], validate=True)
            hmac_key = base64.b64decode(os.environ["KG_ONBOARDING_PII_HMAC_KEY_B64"], validate=True)
        except Exception:
            raise RuntimeError("Onboarding secret configuration is unavailable") from None
        if len(kek) != 32 or len(hmac_key) < 32:
            raise RuntimeError("Onboarding secret configuration is unavailable") from None
        self._aes = AESGCM(kek)
        self._hmac = hmac_key

    def encrypt(self, value: str) -> bytes:
        nonce = secrets.token_bytes(12)
        return nonce + self._aes.encrypt(nonce, value.encode(), b"institution-onboarding-v1")

    def decrypt(self, value: bytes) -> str:
        return self._aes.decrypt(value[:12], value[12:], b"institution-onboarding-v1").decode()

    def digest(self, value: str) -> str:
        return hmac.new(self._hmac, value.encode(), hashlib.sha256).hexdigest()


def _domain_invitation(row: InstitutionInvitationModel) -> InstitutionInvitation:
    return InstitutionInvitation(
        invitation_id=UUID(row.invitation_id), institution_name=row.institution_name,
        institution_type=row.institution_type, applicant_phone_digest=row.applicant_phone_digest,
        pilot_batch_code=row.pilot_batch_code, administrative_region_id=row.administrative_region_id,
        code_digest=row.code_digest, expires_at=row.expires_at, issued_by=row.issued_by,
        issued_at=row.issued_at, status=InvitationStatus(row.status), failed_attempts=row.failed_attempts, version=row.version,
    )


def _domain_application(row: InstitutionApplicationModel) -> InstitutionApplication:
    return InstitutionApplication(
        application_id=UUID(row.application_id), invitation_id=UUID(row.invitation_id),
        applicant_user_id=row.applicant_user_id, institution_type=row.institution_type,
        created_at=row.created_at, status=ApplicationStatus(row.status), version=row.version,
        draft=dict(row.draft_payload), correction_fields=frozenset(row.correction_fields),
        correction_reason_code=row.correction_reason_code,
    )


def _sync_application(row: InstitutionApplicationModel, value: InstitutionApplication, now: datetime) -> None:
    row.status = value.status.value
    row.version = value.version
    row.draft_payload = dict(value.draft)
    row.correction_fields = sorted(value.correction_fields)
    row.correction_reason_code = value.correction_reason_code
    row.updated_at = now


def _public_application(row: InstitutionApplicationModel, licenses=()) -> dict:
    public_draft = {
        key: value
        for key, value in row.draft_payload.items()
        if not key.endswith(("_ciphertext", "_digest"))
    }
    return {
        "application_id": row.application_id, "status": row.status,
        "institution_type": row.institution_type, "draft": public_draft,
        "correction_fields": row.correction_fields, "correction_reason_code": row.correction_reason_code,
        "current_revision_no": row.current_revision_no, "version": row.version,
        "tenant_id": row.tenant_public_id, "tenant_active": row.status == "APPROVED",
        "service_ready": row.service_ready,
        "licenses": [
            {
                "license_type": value.license_type,
                "private_file_id": value.private_file_id,
                "valid_from": value.valid_from.isoformat() if value.valid_from else None,
                "valid_until": value.valid_until.isoformat() if value.valid_until else None,
            }
            for value in licenses
        ],
    }


def review_draft_projection(payload: dict) -> dict:
    secrets_box = OnboardingSecrets()
    result = {
        key: payload[key]
        for key in (
            "legal_representative_name",
            "registered_address",
            "service_address",
            "contact_name",
            "contact_email",
            "service_tags",
        )
        if key in payload
    }
    if "credit_code_ciphertext" in payload:
        result["credit_code"] = secrets_box.decrypt(
            base64.b64decode(payload["credit_code_ciphertext"], validate=True)
        )
    if "contact_phone_ciphertext" in payload:
        result["contact_phone"] = secrets_box.decrypt(
            base64.b64decode(payload["contact_phone_ciphertext"], validate=True)
        )
    return result


def _draft_postimage(row: InstitutionApplicationModel) -> dict:
    return {
        "application_id": row.application_id,
        "applicant_user_id": row.applicant_user_id,
        "status": row.status,
        "draft_payload": json.loads(json.dumps(row.draft_payload)),
        "version": row.version,
        "updated_at": row.updated_at.isoformat(),
    }


def _invitation_failure_postimage(row: InstitutionInvitationModel) -> dict:
    return {
        "invitation_id": row.invitation_id,
        "status": row.status,
        "failed_attempts": row.failed_attempts,
        "version": row.version,
        "expires_at": row.expires_at.isoformat(),
        "activated_at": row.activated_at.isoformat() if row.activated_at else None,
        "code_digest": row.code_digest,
    }


def _canonical_payload(payload: object) -> tuple[str, object]:
    raw = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
    normalized = json.loads(json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str))
    return hashlib.sha256(json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(), normalized


def _secure_invitation_response(response: dict) -> dict:
    secured = dict(response)
    short_code = secured.pop("short_code", None)
    if short_code is not None:
        secured["short_code_ciphertext"] = base64.b64encode(
            OnboardingSecrets().encrypt(short_code)
        ).decode()
    return secured


def _public_invitation_response(response: dict) -> dict:
    public = dict(response)
    ciphertext = public.pop("short_code_ciphertext", None)
    if ciphertext is not None:
        public["short_code"] = OnboardingSecrets().decrypt(
            base64.b64decode(ciphertext, validate=True)
        )
    return public


async def _replay(repo: InstitutionOnboardingRepository, scope: str, operation: str, key: str, payload: object):
    digest, _ = _canonical_payload(payload)
    await repo.lock_operation(scope, operation, key)
    existing = await repo.idempotency(scope, operation, key)
    if existing is None: return digest, None
    if not hmac.compare_digest(existing.request_digest, digest): raise HTTPException(409, "ONBOARDING_IDEMPOTENCY_CONFLICT")
    return digest, dict(existing.response_payload)


async def _safe_rollback(session) -> None:
    rollback_task = asyncio.create_task(session.rollback())
    try:
        await asyncio.shield(rollback_task)
    except asyncio.CancelledError:
        try:
            await rollback_task
        except Exception:
            await session.close()
        raise
    except Exception:
        await session.close()


async def _commit_operation(
    session,
    *,
    scope: str,
    operation: str,
    key: str,
    digest: str,
    response: dict,
) -> dict:
    try:
        await session.commit()
        return response
    except asyncio.CancelledError:
        await _safe_rollback(session)
        raise
    except Exception:
        await _safe_rollback(session)

    bind = session.bind
    if bind is None:
        raise HTTPException(503, "ONBOARDING_COMMIT_OUTCOME_UNKNOWN") from None
    try:
        async with AsyncSession(bind=bind, expire_on_commit=False) as confirmation:
            persisted = await InstitutionOnboardingRepository(confirmation).idempotency(
                scope, operation, key
            )
            if persisted is None:
                raise HTTPException(503, "ONBOARDING_COMMIT_ROLLED_BACK")
            if not hmac.compare_digest(persisted.request_digest, digest):
                raise HTTPException(503, "ONBOARDING_COMMIT_OUTCOME_UNKNOWN")
            persisted_response = dict(persisted.response_payload)
            if persisted_response != response:
                raise HTTPException(503, "ONBOARDING_COMMIT_OUTCOME_UNKNOWN")
            return persisted_response
    except HTTPException:
        raise
    except asyncio.CancelledError:
        raise
    except Exception:
        raise HTTPException(503, "ONBOARDING_COMMIT_OUTCOME_UNKNOWN") from None


async def _commit_draft(
    session,
    *,
    user_id: int,
    previous_version: int,
    expected_postimage: dict,
    response: dict,
) -> dict:
    try:
        await session.commit()
        return response
    except asyncio.CancelledError:
        await _safe_rollback(session)
        raise
    except Exception:
        await _safe_rollback(session)
    bind = session.bind
    if bind is None:
        raise HTTPException(503, "ONBOARDING_COMMIT_OUTCOME_UNKNOWN") from None
    try:
        async with AsyncSession(bind=bind, expire_on_commit=False) as confirmation:
            persisted = await InstitutionOnboardingRepository(
                confirmation
            ).get_application_for_user(user_id)
            if persisted is None:
                raise HTTPException(503, "ONBOARDING_COMMIT_OUTCOME_UNKNOWN")
            if _draft_postimage(persisted) == expected_postimage:
                return _public_application(persisted)
            if persisted.version == previous_version:
                raise HTTPException(503, "ONBOARDING_COMMIT_ROLLED_BACK")
            raise HTTPException(503, "ONBOARDING_COMMIT_OUTCOME_UNKNOWN")
    except HTTPException:
        raise
    except asyncio.CancelledError:
        raise
    except Exception:
        raise HTTPException(503, "ONBOARDING_COMMIT_OUTCOME_UNKNOWN") from None


async def _commit_invitation_failure(
    session,
    *,
    invitation_id: str,
    previous_postimage: dict,
    expected_postimage: dict,
) -> None:
    try:
        await session.commit()
        return
    except asyncio.CancelledError:
        await _safe_rollback(session)
        raise
    except Exception:
        await _safe_rollback(session)
    bind = session.bind
    if bind is None:
        raise HTTPException(503, "ONBOARDING_COMMIT_OUTCOME_UNKNOWN") from None
    try:
        async with AsyncSession(bind=bind, expire_on_commit=False) as confirmation:
            persisted = await InstitutionOnboardingRepository(
                confirmation
            ).get_invitation(invitation_id)
            if persisted is None:
                raise HTTPException(503, "ONBOARDING_COMMIT_OUTCOME_UNKNOWN")
            postimage = _invitation_failure_postimage(persisted)
            if postimage == expected_postimage:
                return
            if postimage == previous_postimage:
                raise HTTPException(503, "ONBOARDING_COMMIT_ROLLED_BACK")
            raise HTTPException(503, "ONBOARDING_COMMIT_OUTCOME_UNKNOWN")
    except HTTPException:
        raise
    except asyncio.CancelledError:
        raise
    except Exception:
        raise HTTPException(503, "ONBOARDING_COMMIT_OUTCOME_UNKNOWN") from None


def _translate_domain_error(exc: Exception) -> HTTPException:
    if isinstance(exc, OnboardingForbidden):
        return HTTPException(403, str(exc))
    if isinstance(exc, OnboardingConflict):
        return HTTPException(409, str(exc))
    return HTTPException(503, "ONBOARDING_PERSISTENCE_UNAVAILABLE")


async def require_current_reviewer(session, actor):
    if (
        actor.role != "super_admin"
        or actor.tenant_id is not None
        or actor.org_id is not None
    ):
        raise HTTPException(403, "ONBOARDING_REVIEW_ROLE_REQUIRED")
    current = await InstitutionOnboardingRepository(session).reviewer_currentness(actor.id)
    if (
        current is None
        or current["role"] != "super_admin"
        or current["status"] != "active"
        or current["tenant_id"] is not None
    ):
        raise HTTPException(403, "ONBOARDING_REVIEW_CURRENTNESS_REQUIRED")
    return current


async def _record_idempotency(repo: InstitutionOnboardingRepository, scope: str, operation: str, key: str, digest: str, response: dict, now: datetime) -> dict:
    normalized = json.loads(json.dumps(response, ensure_ascii=False, default=str))
    await repo.add_idempotency(InstitutionOnboardingIdempotencyModel(actor_scope=scope, operation=operation, idempotency_key=key, request_digest=digest, response_payload=normalized, created_at=now))
    return normalized


async def create_invitation(
    session,
    actor,
    payload: InvitationCreate,
    request_id: str,
    idempotency_key: str,
    *,
    region_session=None,
) -> dict:
    if actor.role != "super_admin":
        raise HTTPException(403, "ONBOARDING_PLATFORM_ROLE_REQUIRED")
    if (
        await InstitutionOnboardingRepository(region_session or session).active_canonical_county(
            payload.administrative_region_id
        )
        is None
    ):
        raise HTTPException(409, "ONBOARDING_ADMINISTRATIVE_REGION_INVALID")
    secrets_box = OnboardingSecrets(); repo = InstitutionOnboardingRepository(session)
    request_digest, replay = await _replay(repo, str(actor.id), "INVITATION_CREATE", idempotency_key, payload)
    if replay is not None: return _public_invitation_response(replay)
    now = utcnow(); invitation_id = Uuid7Generator().generate(); short_code = f"{secrets.randbelow(1_000_000):06d}"
    row = InstitutionInvitationModel(
        invitation_id=str(invitation_id), institution_name=payload.institution_name,
        institution_type=payload.institution_type, applicant_phone_ciphertext=secrets_box.encrypt(payload.applicant_phone),
        applicant_phone_digest=secrets_box.digest(payload.applicant_phone), pilot_batch_code=payload.pilot_batch_code,
        administrative_region_id=payload.administrative_region_id, code_digest=secrets_box.digest(short_code),
        status="ISSUED", failed_attempts=0, expires_at=now + timedelta(minutes=payload.expires_in_minutes),
        issued_by=actor.id, issued_at=now, version=1,
    )
    await repo.add(row)
    await repo.add_audit(InstitutionOnboardingAuditModel(actor_user_id=actor.id, actor_role=actor.role, action="INVITATION_CREATE", object_type="INSTITUTION_INVITATION", object_id=str(invitation_id), result="SUCCESS", request_id=request_id, created_at=now))
    response = await _record_idempotency(repo, str(actor.id), "INVITATION_CREATE", idempotency_key, request_digest, _secure_invitation_response({"invitation_id": str(invitation_id), "institution_name": row.institution_name, "institution_type": row.institution_type, "status": row.status, "short_code": short_code, "expires_at": row.expires_at, "version": row.version}), now)
    persisted = await _commit_operation(
        session, scope=str(actor.id), operation="INVITATION_CREATE",
        key=idempotency_key, digest=request_digest, response=response,
    )
    return _public_invitation_response(persisted)


async def resend_invitation(
    session,
    actor,
    invitation_id: str,
    payload: InvitationResendRequest,
    request_id: str,
    idempotency_key: str,
) -> dict:
    if actor.role != "super_admin":
        raise HTTPException(403, "ONBOARDING_PLATFORM_ROLE_REQUIRED")
    repo = InstitutionOnboardingRepository(session)
    scope = f"{actor.id}:{invitation_id}"
    request_digest, replay = await _replay(
        repo, scope, "INVITATION_RESEND", idempotency_key, payload
    )
    if replay is not None:
        return _public_invitation_response(replay)
    row = await repo.get_invitation_for_update(invitation_id)
    if row is None:
        raise HTTPException(404, "ONBOARDING_INVITATION_NOT_FOUND")
    if row.version != payload.expected_version:
        raise HTTPException(409, "ONBOARDING_INVITATION_VERSION_CONFLICT")
    now = utcnow()
    short_code = f"{secrets.randbelow(1_000_000):06d}"
    value = _domain_invitation(row)
    try:
        value.resend(
            code_digest=OnboardingSecrets().digest(short_code),
            expires_at=now + timedelta(minutes=payload.expires_in_minutes),
            now=now,
        )
    except (OnboardingConflict, OnboardingForbidden) as exc:
        raise _translate_domain_error(exc) from None
    row.code_digest = value.code_digest
    row.expires_at = value.expires_at
    row.failed_attempts = value.failed_attempts
    row.version = value.version
    await repo.add_audit(
        InstitutionOnboardingAuditModel(
            actor_user_id=actor.id,
            actor_role=actor.role,
            action="INVITATION_RESEND",
            object_type="INSTITUTION_INVITATION",
            object_id=row.invitation_id,
            result="SUCCESS",
            request_id=request_id,
            created_at=now,
        )
    )
    response = await _record_idempotency(
        repo,
        scope,
        "INVITATION_RESEND",
        idempotency_key,
        request_digest,
        _secure_invitation_response({
            "invitation_id": row.invitation_id,
            "institution_name": row.institution_name,
            "institution_type": row.institution_type,
            "status": row.status,
            "short_code": short_code,
            "expires_at": row.expires_at,
            "version": row.version,
        }),
        now,
    )
    persisted = await _commit_operation(
        session,
        scope=scope,
        operation="INVITATION_RESEND",
        key=idempotency_key,
        digest=request_digest,
        response=response,
    )
    return _public_invitation_response(persisted)


async def revoke_invitation(
    session,
    actor,
    invitation_id: str,
    payload: InvitationRevokeRequest,
    request_id: str,
    idempotency_key: str,
) -> dict:
    if actor.role != "super_admin":
        raise HTTPException(403, "ONBOARDING_PLATFORM_ROLE_REQUIRED")
    repo = InstitutionOnboardingRepository(session)
    scope = f"{actor.id}:{invitation_id}"
    request_digest, replay = await _replay(
        repo, scope, "INVITATION_REVOKE", idempotency_key, payload
    )
    if replay is not None:
        return replay
    row = await repo.get_invitation_for_update(invitation_id)
    if row is None:
        raise HTTPException(404, "ONBOARDING_INVITATION_NOT_FOUND")
    if row.version != payload.expected_version:
        raise HTTPException(409, "ONBOARDING_INVITATION_VERSION_CONFLICT")
    now = utcnow()
    value = _domain_invitation(row)
    try:
        value.revoke(now=now)
    except (OnboardingConflict, OnboardingForbidden) as exc:
        raise _translate_domain_error(exc) from None
    row.status = value.status.value
    row.version = value.version
    await repo.add_audit(
        InstitutionOnboardingAuditModel(
            actor_user_id=actor.id,
            actor_role=actor.role,
            action="INVITATION_REVOKE",
            object_type="INSTITUTION_INVITATION",
            object_id=row.invitation_id,
            result="SUCCESS",
            request_id=request_id,
            created_at=now,
        )
    )
    response = await _record_idempotency(
        repo,
        scope,
        "INVITATION_REVOKE",
        idempotency_key,
        request_digest,
        {
            "invitation_id": row.invitation_id,
            "institution_name": row.institution_name,
            "institution_type": row.institution_type,
            "status": row.status,
            "version": row.version,
        },
        now,
    )
    return await _commit_operation(
        session,
        scope=scope,
        operation="INVITATION_REVOKE",
        key=idempotency_key,
        digest=request_digest,
        response=response,
    )


async def activate(session, payload: ActivationRequest, request_id: str, idempotency_key: str) -> dict:
    secrets_box = OnboardingSecrets(); repo = InstitutionOnboardingRepository(session); now = utcnow()
    request_digest, replay = await _replay(repo, str(payload.invitation_id), "INVITATION_ACTIVATE", idempotency_key, payload)
    if replay is not None: return replay
    row = await repo.get_invitation_for_update(str(payload.invitation_id))
    if row is None:
        raise HTTPException(404, "ONBOARDING_INVITATION_NOT_FOUND")
    invitation = _domain_invitation(row)
    previous_failure_postimage = _invitation_failure_postimage(row)
    try:
        invitation.activate(phone_digest=secrets_box.digest(payload.phone), code_digest=secrets_box.digest(payload.short_code), now=now)
        if not verify_totp(payload.totp_secret, payload.totp_code, at=now):
            raise HTTPException(403, "ONBOARDING_TOTP_INVALID")
    except HTTPException:
        raise
    except (OnboardingConflict, OnboardingForbidden) as exc:
        row.failed_attempts = invitation.failed_attempts
        await _commit_invitation_failure(
            session,
            invitation_id=row.invitation_id,
            previous_postimage=previous_failure_postimage,
            expected_postimage=_invitation_failure_postimage(row),
        )
        raise _translate_domain_error(exc) from None
    map_core_model_classes()
    user = User(); user.phone = payload.phone; user.password_hash = hash_password(payload.password); user.role = "org_admin"; user.status = "active"; user.tenant_id = None
    await repo.add_onboarding_user(user)
    row.status = invitation.status.value; row.failed_attempts = invitation.failed_attempts; row.version = invitation.version; row.activated_at = now
    account = InstitutionOnboardingAccountModel(user_id=user.id, invitation_id=row.invitation_id, totp_secret_ciphertext=secrets_box.encrypt(payload.totp_secret), totp_enabled=True, activated_at=now)
    application_id = Uuid7Generator().generate()
    application = InstitutionApplicationModel(application_id=str(application_id), invitation_id=row.invitation_id, applicant_user_id=user.id, institution_type=row.institution_type, status="DRAFT", draft_payload={}, correction_fields=[], current_revision_no=0, service_ready=False, created_at=now, updated_at=now, version=1)
    await repo.add(account); await repo.add(application)
    await repo.add_audit(InstitutionOnboardingAuditModel(actor_user_id=user.id, actor_role="org_admin", action="INVITATION_ACTIVATE", object_type="INSTITUTION_APPLICATION", object_id=str(application_id), result="SUCCESS", request_id=request_id, created_at=now))
    response = await _record_idempotency(repo, str(payload.invitation_id), "INVITATION_ACTIVATE", idempotency_key, request_digest, {"application_id": str(application_id), "user_id": user.id, "status": "DRAFT", "version": 1}, now)
    return await _commit_operation(
        session, scope=str(payload.invitation_id), operation="INVITATION_ACTIVATE",
        key=idempotency_key, digest=request_digest, response=response,
    )


async def get_application(session, user_id: int) -> dict:
    repo = InstitutionOnboardingRepository(session)
    row = await repo.get_application_for_user(user_id)
    if row is None: raise HTTPException(404, "ONBOARDING_APPLICATION_NOT_FOUND")
    licenses = await repo.licenses_for_application(row.application_id)
    return _public_application(row, licenses)


async def save_draft(session, user_id: int, payload: ApplicationDraftRequest) -> dict:
    repo = InstitutionOnboardingRepository(session); row = await repo.get_application_for_user(user_id, for_update=True)
    if row is None: raise HTTPException(404, "ONBOARDING_APPLICATION_NOT_FOUND")
    secrets_box = OnboardingSecrets(); now = utcnow(); domain = _domain_application(row)
    values = payload.model_dump(exclude={"expected_version", "licenses"})
    credit_code = values.pop("credit_code")
    contact_phone = values.pop("contact_phone")
    values["credit_code_digest"] = secrets_box.digest(credit_code)
    values["credit_code_ciphertext"] = base64.b64encode(
        secrets_box.encrypt(credit_code)
    ).decode("ascii")
    values["contact_phone_digest"] = secrets_box.digest(contact_phone)
    values["contact_phone_ciphertext"] = base64.b64encode(
        secrets_box.encrypt(contact_phone)
    ).decode("ascii")
    try:
        domain.save_draft(values, expected_version=payload.expected_version, now=now)
    except (OnboardingConflict, OnboardingForbidden) as exc:
        raise _translate_domain_error(exc) from None
    _sync_application(row, domain, now)
    response = _public_application(row)
    return await _commit_draft(
        session,
        user_id=user_id,
        previous_version=payload.expected_version,
        expected_postimage=_draft_postimage(row),
        response=response,
    )


async def _submit(session, user_id: int, payload: ApplicationSubmitRequest, *, resubmit: bool, idempotency_key: str) -> dict:
    repo = InstitutionOnboardingRepository(session); row = await repo.get_application_for_user(user_id, for_update=True)
    if row is None: raise HTTPException(404, "ONBOARDING_APPLICATION_NOT_FOUND")
    operation = "APPLICATION_RESUBMIT" if resubmit else "APPLICATION_SUBMIT"
    request_digest, replay = await _replay(repo, str(user_id), operation, idempotency_key, payload)
    if replay is not None: return replay
    ids = tuple(str(value.private_file_id) for value in payload.licenses)
    if len(set(ids)) != len(ids):
        raise HTTPException(409, "PRIVATE_FILE_BIND_CONFLICT")
    files = await repo.lock_files_for_binding(tuple(sorted(ids)))
    if len(files) != len(ids):
        raise HTTPException(409, "PRIVATE_FILE_NOT_CLEAN")
    files_by_id = {value.file_id: value for value in files}
    purposes: list[str] = []
    for item in payload.licenses:
        file = files_by_id[str(item.private_file_id)]
        if file.owner_user_id != user_id or file.status != "CLEAN":
            raise HTTPException(409, "PRIVATE_FILE_NOT_CLEAN")
        if file.bound_application_id not in (None, row.application_id):
            raise HTTPException(409, "PRIVATE_FILE_BIND_CONFLICT")
        if file.purpose != item.license_type:
            raise HTTPException(409, "PRIVATE_FILE_PURPOSE_MISMATCH")
        purposes.append(file.purpose)
    now = utcnow(); domain = _domain_application(row)
    try:
        revision = (domain.resubmit if resubmit else domain.submit)(
            clean_file_purposes=tuple(purposes),
            expected_version=payload.expected_version,
            now=now,
        )
    except (OnboardingConflict, OnboardingForbidden) as exc:
        raise _translate_domain_error(exc) from None
    revision_no = row.current_revision_no + 1
    _sync_application(row, domain, now); row.current_revision_no = revision_no; row.submitted_at = now
    await repo.add(InstitutionApplicationRevisionModel(revision_id=str(Uuid7Generator().generate()), application_id=row.application_id, revision_no=revision_no, snapshot=dict(revision.snapshot), created_at=now))
    secrets_box = OnboardingSecrets()
    existing_licenses = await repo.licenses_for_application(row.application_id) if resubmit else ()
    existing_by_type = {value.license_type: value for value in existing_licenses}
    for item in payload.licenses:
        file = files_by_id[str(item.private_file_id)]
        file.bound_application_id = row.application_id; file.bound_at = now
        existing = existing_by_type.get(item.license_type)
        if existing is not None:
            if existing.private_file_id != str(item.private_file_id):
                raise HTTPException(409, "PRIVATE_FILE_BIND_CONFLICT")
            continue
        await repo.add(InstitutionLicenseModel(license_id=str(Uuid7Generator().generate()), application_id=row.application_id, license_type=item.license_type, license_no_ciphertext=secrets_box.encrypt(item.license_no) if item.license_no else None, license_no_digest=secrets_box.digest(item.license_no) if item.license_no else None, private_file_id=str(item.private_file_id), valid_from=item.valid_from, valid_until=item.valid_until, created_at=now))
    response = await _record_idempotency(repo, str(user_id), operation, idempotency_key, request_digest, _public_application(row), now)
    return await _commit_operation(
        session, scope=str(user_id), operation=operation, key=idempotency_key,
        digest=request_digest, response=response,
    )


async def submit_application(session, user_id: int, payload: ApplicationSubmitRequest, idempotency_key: str) -> dict:
    return await _submit(session, user_id, payload, resubmit=False, idempotency_key=idempotency_key)


async def resubmit_application(session, user_id: int, payload: ApplicationResubmitRequest, idempotency_key: str) -> dict:
    repo = InstitutionOnboardingRepository(session)
    row = await repo.get_application_for_user(user_id, for_update=True)
    if row is None:
        raise HTTPException(404, "ONBOARDING_APPLICATION_NOT_FOUND")
    canonical_digest, _ = _canonical_payload(payload)
    request_digest, replay = await _replay(
        repo, str(user_id), "APPLICATION_RESUBMIT", idempotency_key, payload
    )
    if not hmac.compare_digest(canonical_digest, request_digest):
        raise HTTPException(409, "ONBOARDING_IDEMPOTENCY_CONFLICT")
    if replay is not None:
        return replay
    domain = _domain_application(row)
    raw_values = payload.model_dump(exclude={"expected_version", "licenses"})
    secrets_box = OnboardingSecrets()
    credit_code = raw_values.pop("credit_code")
    contact_phone = raw_values.pop("contact_phone")
    raw_values["credit_code_digest"] = secrets_box.digest(credit_code)
    raw_values["credit_code_ciphertext"] = base64.b64encode(
        secrets_box.encrypt(credit_code)
    ).decode("ascii")
    raw_values["contact_phone_digest"] = secrets_box.digest(contact_phone)
    raw_values["contact_phone_ciphertext"] = base64.b64encode(
        secrets_box.encrypt(contact_phone)
    ).decode("ascii")
    correction_values = {
        CORRECTION_FIELD_TO_DRAFT[field]: raw_values[
            CORRECTION_FIELD_TO_DRAFT[field]
        ]
        for field in domain.correction_fields
        if field in CORRECTION_FIELD_TO_DRAFT
    }
    if "credit_code" in domain.correction_fields:
        correction_values["credit_code_ciphertext"] = raw_values[
            "credit_code_ciphertext"
        ]
    if "contact_phone" in domain.correction_fields:
        correction_values["contact_phone_ciphertext"] = raw_values[
            "contact_phone_ciphertext"
        ]
    now = utcnow()
    try:
        domain.save_correction(
            correction_values, expected_version=payload.expected_version, now=now
        )
    except (OnboardingConflict, OnboardingForbidden) as exc:
        raise _translate_domain_error(exc) from None
    _sync_application(row, domain, now)
    submit_payload = ApplicationSubmitRequest(
        expected_version=domain.version, licenses=payload.licenses
    )
    # Preserve the original correction payload as the idempotency contract while
    # completing correction and resubmission in this same transaction.
    return await _submit_existing(
        session, repo, row, user_id, submit_payload, idempotency_key,
        request_digest=request_digest,
    )


async def _submit_existing(
    session, repo, row, user_id: int, payload: ApplicationSubmitRequest,
    idempotency_key: str, *, request_digest: str,
) -> dict:
    secrets_box = OnboardingSecrets()
    ids = tuple(str(value.private_file_id) for value in payload.licenses)
    if len(set(ids)) != len(ids):
        raise HTTPException(409, "PRIVATE_FILE_BIND_CONFLICT")
    existing_rows = await repo.licenses_for_update(row.application_id)
    existing = {value.license_type: value for value in existing_rows}
    old_ids = tuple(value.private_file_id for value in existing_rows)
    domain = _domain_application(row)
    files = await repo.lock_files_for_binding(tuple(sorted(set(ids + old_ids))))
    files_by_id = {value.file_id: value for value in files}
    if not set(ids).issubset(files_by_id):
        raise HTTPException(409, "PRIVATE_FILE_NOT_CLEAN")
    purposes = []
    for item in payload.licenses:
        license_row = existing.get(item.license_type)
        if license_row is None:
            raise HTTPException(409, "PRIVATE_FILE_BIND_CONFLICT")
        if (
            license_row.private_file_id != str(item.private_file_id)
            and LICENSE_TYPE_TO_CORRECTION_FIELD.get(item.license_type)
            not in domain.correction_fields
        ):
            raise HTTPException(409, "ONBOARDING_CORRECTION_FIELD_FORBIDDEN")
        file = files_by_id[str(item.private_file_id)]
        if (
            file.owner_user_id != user_id
            or file.status != "CLEAN"
            or file.bound_application_id not in (None, row.application_id)
            or file.purpose != item.license_type
        ):
            raise HTTPException(409, "PRIVATE_FILE_BIND_CONFLICT")
        purposes.append(file.purpose)
    now = utcnow()
    try:
        revision = domain.resubmit(
            clean_file_purposes=tuple(purposes),
            expected_version=payload.expected_version,
            now=now,
        )
    except (OnboardingConflict, OnboardingForbidden) as exc:
        raise _translate_domain_error(exc) from None
    revision_no = row.current_revision_no + 1
    _sync_application(row, domain, now); row.current_revision_no = revision_no; row.submitted_at = now
    await repo.add(InstitutionApplicationRevisionModel(
        revision_id=str(Uuid7Generator().generate()), application_id=row.application_id,
        revision_no=revision_no, snapshot=dict(revision.snapshot), created_at=now,
    ))
    for item in payload.licenses:
        file = files_by_id[str(item.private_file_id)]
        file.bound_application_id = row.application_id; file.bound_at = now
        license_row = existing.get(item.license_type)
        if license_row is None:
            raise HTTPException(409, "PRIVATE_FILE_BIND_CONFLICT")
        if license_row.private_file_id != str(item.private_file_id):
            old_file = files_by_id[license_row.private_file_id]
            old_file.bound_application_id = None
            old_file.bound_at = None
            license_row.private_file_id = str(item.private_file_id)
            license_row.license_no_ciphertext = (
                secrets_box.encrypt(item.license_no) if item.license_no else None
            )
            license_row.license_no_digest = (
                secrets_box.digest(item.license_no) if item.license_no else None
            )
        license_row.valid_from = item.valid_from
        license_row.valid_until = item.valid_until
    response = await _record_idempotency(
        repo, str(user_id), "APPLICATION_RESUBMIT", idempotency_key,
        request_digest, _public_application(row), now,
    )
    return await _commit_operation(
        session, scope=str(user_id), operation="APPLICATION_RESUBMIT",
        key=idempotency_key, digest=request_digest, response=response,
    )


async def review_decision(session, actor, application_id: str, payload: ReviewDecisionRequest, request_id: str, idempotency_key: str, *, currentness_session=None) -> dict:
    if currentness_session is None:
        raise HTTPException(403, "ONBOARDING_REVIEW_CURRENTNESS_REQUIRED")
    await require_current_reviewer(currentness_session, actor)
    repo = InstitutionOnboardingRepository(session)
    request_digest, replay = await _replay(repo, f"{actor.id}:{application_id}", "REVIEW_DECISION", idempotency_key, payload)
    if replay is not None: return replay
    row = await repo.get_application_for_review(application_id, for_update=True)
    if row is None: raise HTTPException(404, "ONBOARDING_APPLICATION_NOT_FOUND")
    now = utcnow(); domain = _domain_application(row)
    invitation = None
    try:
        if row.status == "SUBMITTED":
            domain.start_review(expected_version=payload.expected_version, now=now)
            expected = payload.expected_version + 1
        elif row.status == "UNDER_REVIEW":
            domain._expect(payload.expected_version, ApplicationStatus.UNDER_REVIEW)
            expected = payload.expected_version
        else:
            raise OnboardingConflict("ONBOARDING_APPLICATION_STATE_CONFLICT")
        if payload.decision == "NEEDS_CORRECTION":
            domain.request_correction(fields=payload.correction_fields, reason_code=payload.reason_code or "CORRECTION_REQUIRED", expected_version=expected, now=now)
        elif payload.decision == "REJECTED":
            domain.reject(reason_code=payload.reason_code or "REJECTED", expected_version=expected, now=now)
        else:
            revision = await repo.require_current_revision(row.application_id, row.current_revision_no)
            if row.current_revision_no < 1 or revision is None or dict(revision.snapshot) != dict(row.draft_payload):
                raise OnboardingConflict("ONBOARDING_CURRENT_REVISION_REQUIRED")
            bound_files = await repo.require_clean_bound_files(row.application_id)
            required = {"BUSINESS_LICENSE"}
            if row.institution_type == "LICENSED_CLINIC":
                required.add("MEDICAL_INSTITUTION_LICENSE")
            clean_purposes = {
                file["license_type"]
                for file in bound_files
                if file["status"] == "CLEAN"
                and file["bound_application_id"] == row.application_id
                and file["purpose"] == file["license_type"]
            }
            if not required.issubset(clean_purposes):
                raise OnboardingConflict("ONBOARDING_REQUIRED_CLEAN_FILES_MISSING")
            invitation = await repo.invitation_for_review(row.invitation_id)
            if invitation is None or await InstitutionOnboardingRepository(
                currentness_session
            ).active_canonical_county(invitation.administrative_region_id) is None:
                raise HTTPException(409, "ONBOARDING_ADMINISTRATIVE_REGION_INVALID")
            domain.approve(expected_version=expected, now=now)
    except (OnboardingConflict, OnboardingForbidden) as exc:
        raise _translate_domain_error(exc) from None
    _sync_application(row, domain, now)
    row.reviewed_at = now
    if payload.decision == "APPROVED":
        map_core_model_classes()
        secrets_box = OnboardingSecrets()
        tenant = Tenant()
        tenant.org_id = invitation.administrative_region_id
        tenant.tenant_code = f"KL-{str(row.application_id).replace('-', '')[:12].upper()}"
        tenant.name = invitation.institution_name
        tenant.type = "store" if row.institution_type == "HEALTH_STORE" else "clinic"
        tenant.credit_code = secrets_box.decrypt(
            base64.b64decode(row.draft_payload["credit_code_ciphertext"])
        )
        tenant.legal_person_name = row.draft_payload["legal_representative_name"]
        tenant.province = "受控行政区"; tenant.city = "受控行政区"; tenant.district = "受控行政区"
        tenant.address = row.draft_payload["service_address"]
        tenant.contact_name = row.draft_payload["contact_name"]
        tenant.contact_phone = secrets_box.decrypt(
            base64.b64decode(row.draft_payload["contact_phone_ciphertext"])
        )
        tenant.contact_email = row.draft_payload["contact_email"]
        tenant.status = "active"; tenant.reviewed_by = actor.id
        tenant.reviewed_at = now; tenant.approved_at = now
        await repo.add_approved_tenant(tenant); row.tenant_internal_id = tenant.id; row.tenant_public_id = str(Uuid7Generator().generate()); row.service_ready = False
        await repo.bind_user_tenant(user_id=row.applicant_user_id, tenant_id=tenant.id)
        await repo.add(InstitutionOnboardingOutboxModel(event_id=str(Uuid7Generator().generate()), event_type="INSTITUTION_APPROVED", aggregate_id=row.application_id, payload={"application_id": row.application_id, "tenant_id": row.tenant_public_id}, status="PENDING", attempts=0, created_at=now, processing_at=None))
        await session.flush()
        bound_tenant_public_id = await repo.bind_controlled_tenant_origin(
            row.application_id
        )
        if bound_tenant_public_id != row.tenant_public_id:
            raise RuntimeError("ONBOARDING_TENANT_ORIGIN_BIND_FAILED")
    await repo.add_audit(InstitutionOnboardingAuditModel(
        actor_user_id=actor.id, actor_role=actor.role,
        action=f"REVIEW_{payload.decision}", object_type="INSTITUTION_APPLICATION",
        object_id=row.application_id, result="SUCCESS",
        reason_code=payload.reason_code, request_id=request_id, created_at=now,
    ))
    scope = f"{actor.id}:{application_id}"
    response = await _record_idempotency(
        repo, scope, "REVIEW_DECISION", idempotency_key,
        request_digest, _public_application(row), now,
    )
    return await _commit_operation(
        session, scope=scope, operation="REVIEW_DECISION", key=idempotency_key,
        digest=request_digest, response=response,
    )
