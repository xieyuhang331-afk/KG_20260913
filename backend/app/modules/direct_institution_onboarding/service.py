from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets as stdlib_secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, ConfigDict

from app.modules.direct_institution_onboarding.schemas import (
    AdminHandoffActivationRequest,
    AdminHandoffCreateRequest,
    ComplianceDecisionRequest,
    ComplianceLicense,
    CompliancePayloadV1,
    DirectActivationRequest,
    DirectComplianceRequest,
    DirectCreateRequest,
    VersionedStepUpRequest,
)

_ADMIN_PHONE_AAD = b"phase1/direct-institution/admin-phone/v1"
_HANDOFF_PHONE_AAD = b"phase1/direct-institution/handoff-phone/v1"
_LICENSE_NO_AAD = b"phase1/direct-institution/license-no/v1"
_COMPLIANCE_PAYLOAD_AAD = b"phase1/direct-institution/compliance-pii/v1"
_PLATFORM_ADMIN_TOTP_AAD = b"phase1/direct-institution/platform-admin-totp/v1"
_DIRECT_ORG_ADMIN_TOTP_AAD = b"phase1/direct-institution/org-admin-totp/v2"
_COMPLIANCE_PAYLOAD_DIGEST = (
    b"phase1/direct-institution/compliance-payload-digest/v1\0"
)
_ACCOUNT_PHONE_CLAIM_DIGEST = b"phase1/account-phone-claim/v1\0"
_DIRECT_ONBOARDING_CURSOR_DOMAIN = b"phase1/direct-institution/cursor/v1\0"
_CREDENTIAL_CODE_DIGEST = b"phase1/direct-institution/credential-code/v1\0"
_REQUEST_DIGEST = b"phase1/direct-institution/request-digest/v1\0"
_POSTIMAGE_DIGEST = b"phase1/direct-institution/postimage-digest/v1\0"
_COMPLIANCE_BUSINESS_DIGESTS = {
    "CREDIT_CODE": b"phase1/direct-institution/credit-code-digest/v1\0",
    "LICENSE_NO": b"phase1/direct-institution/license-no-digest/v1\0",
    "LICENSE_SET": b"phase1/direct-institution/license-set-digest/v1\0",
}
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", re.ASCII)
SAFE_UNAVAILABLE = "DIRECT_ONBOARDING_DEPENDENCY_UNAVAILABLE"


def direct_institution_code(onboarding_id: UUID) -> str:
    """Derive the public 32-character code from the operation's UUIDv7."""
    return _cursor_uuid_v7(onboarding_id).hex.upper()


def accepted_totp_step(secret: str, code: str, *, at: datetime) -> int | None:
    """Return the exact accepted 30-second step, without widening the TOTP window."""
    from app.modules.institution_onboarding.domain import generate_totp

    if (
        type(secret) is not str
        or type(code) is not str
        or re.fullmatch(r"[0-9]{6}", code) is None
        or not isinstance(at, datetime)
        or at.tzinfo is None
    ):
        return None
    current_step = int(at.timestamp()) // 30
    for offset in (-1, 0, 1):
        candidate_step = current_step + offset
        candidate_at = at + timedelta(seconds=offset * 30)
        if hmac.compare_digest(generate_totp(secret, at=candidate_at), code):
            return candidate_step
    return None


def _load_keyring(current_name: str, keyring_name: str) -> tuple[str, dict[str, bytes]]:
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        current = os.environ[current_name]
        source = json.loads(os.environ[keyring_name], object_pairs_hook=unique_pairs)
        if type(source) is not dict or not source:
            raise ValueError
        keys = {
            str(key): base64.b64decode(value, validate=True)
            for key, value in source.items()
        }
        if (
            _KEY_ID_RE.fullmatch(current) is None
            or current not in keys
            or any(
                _KEY_ID_RE.fullmatch(key_id) is None or len(material) != 32
                for key_id, material in keys.items()
            )
        ):
            raise ValueError
        return current, keys
    except Exception:
        raise RuntimeError(SAFE_UNAVAILABLE) from None


class DirectOnboardingSecrets:
    """Five isolated key domains required by direct onboarding V1."""

    def __init__(self) -> None:
        self.pii_key_id, self.pii_keys = _load_keyring(
            "KG_DIRECT_INSTITUTION_PII_CURRENT_KEY_ID",
            "KG_DIRECT_INSTITUTION_PII_KEYRING_JSON",
        )
        self.code_key_id, self.code_keys = _load_keyring(
            "KG_DIRECT_INSTITUTION_CODE_CURRENT_KEY_ID",
            "KG_DIRECT_INSTITUTION_CODE_KEYRING_JSON",
        )
        self.digest_key_id, self.digest_keys = _load_keyring(
            "KG_DIRECT_INSTITUTION_DIGEST_CURRENT_KEY_ID",
            "KG_DIRECT_INSTITUTION_DIGEST_KEYRING_JSON",
        )
        self.totp_key_id, self.totp_keys = _load_keyring(
            "KG_PLATFORM_ADMIN_TOTP_CURRENT_KEY_ID",
            "KG_PLATFORM_ADMIN_TOTP_KEYRING_JSON",
        )
        self.phone_claim_key_id, self.phone_claim_keys = _load_keyring(
            "KG_ACCOUNT_PHONE_CLAIM_DIGEST_CURRENT_KEY_ID",
            "KG_ACCOUNT_PHONE_CLAIM_DIGEST_KEYRING_JSON",
        )
        seen: dict[bytes, str] = {}
        for domain, keyring in self.keyrings:
            for material in keyring.values():
                previous = seen.setdefault(material, domain)
                if previous != domain:
                    raise RuntimeError(SAFE_UNAVAILABLE) from None

    @property
    def keyrings(self) -> tuple[tuple[str, dict[str, bytes]], ...]:
        return (
            ("direct-pii", self.pii_keys),
            ("direct-code", self.code_keys),
            ("direct-digest", self.digest_keys),
            ("platform-totp", self.totp_keys),
            ("account-phone-claim", self.phone_claim_keys),
        )


def account_phone_claim_digest(phone: str) -> tuple[str, str]:
    if type(phone) is not str or re.fullmatch(r"1[3-9][0-9]{9}", phone) is None:
        raise RuntimeError(SAFE_UNAVAILABLE) from None
    key_id, keys = _load_keyring(
        "KG_ACCOUNT_PHONE_CLAIM_DIGEST_CURRENT_KEY_ID",
        "KG_ACCOUNT_PHONE_CLAIM_DIGEST_KEYRING_JSON",
    )
    return key_id, hmac.new(
        keys[key_id], _ACCOUNT_PHONE_CLAIM_DIGEST + phone.encode("ascii"), hashlib.sha256
    ).hexdigest()


def account_phone_claim_digest_candidates(phone: str) -> list[dict[str, str]]:
    if type(phone) is not str or re.fullmatch(r"1[3-9][0-9]{9}", phone) is None:
        raise RuntimeError(SAFE_UNAVAILABLE) from None
    current, keys = _load_keyring(
        "KG_ACCOUNT_PHONE_CLAIM_DIGEST_CURRENT_KEY_ID",
        "KG_ACCOUNT_PHONE_CLAIM_DIGEST_KEYRING_JSON",
    )
    if len(keys) > 16:
        raise RuntimeError(SAFE_UNAVAILABLE) from None
    candidates = [
        {
            "key_id": key_id,
            "digest": hmac.new(
                keys[key_id],
                _ACCOUNT_PHONE_CLAIM_DIGEST + phone.encode("ascii"),
                hashlib.sha256,
            ).hexdigest(),
        }
        for key_id in sorted(keys)
    ]
    if not any(candidate["key_id"] == current for candidate in candidates):
        raise RuntimeError(SAFE_UNAVAILABLE) from None
    return candidates


def account_phone_claim_digest_for_key(phone: str, key_id: str) -> tuple[str, str]:
    if type(phone) is not str or re.fullmatch(r"1[3-9][0-9]{9}", phone) is None:
        raise RuntimeError(SAFE_UNAVAILABLE) from None
    _, keys = _load_keyring(
        "KG_ACCOUNT_PHONE_CLAIM_DIGEST_CURRENT_KEY_ID",
        "KG_ACCOUNT_PHONE_CLAIM_DIGEST_KEYRING_JSON",
    )
    if type(key_id) is not str or key_id not in keys:
        raise RuntimeError(SAFE_UNAVAILABLE) from None
    return key_id, hmac.new(
        keys[key_id], _ACCOUNT_PHONE_CLAIM_DIGEST + phone.encode("ascii"), hashlib.sha256
    ).hexdigest()


def generate_one_time_credential() -> str:
    return stdlib_secrets.token_urlsafe(32)


def credential_digest(value: str) -> tuple[str, str]:
    if type(value) is not str or not 16 <= len(value) <= 256 or not value.isascii():
        raise ValueError("INVALID_REQUEST")
    keyring = DirectOnboardingSecrets()
    return keyring.code_key_id, hmac.new(
        keyring.code_keys[keyring.code_key_id],
        _CREDENTIAL_CODE_DIGEST + value.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def credential_digest_candidates(value: str) -> list[dict[str, str]]:
    if type(value) is not str or not 16 <= len(value) <= 256 or not value.isascii():
        raise ValueError("INVALID_REQUEST")
    keyring = DirectOnboardingSecrets()
    if len(keyring.code_keys) > 16:
        raise RuntimeError(SAFE_UNAVAILABLE) from None
    return [
        {
            "key_id": key_id,
            "digest": hmac.new(
                keyring.code_keys[key_id],
                _CREDENTIAL_CODE_DIGEST + value.encode("ascii"),
                hashlib.sha256,
            ).hexdigest(),
        }
        for key_id in sorted(keyring.code_keys)
    ]


def _digest_candidate_for_key(
    candidates: list[dict[str, str]], key_id: str
) -> tuple[str, str]:
    for candidate in candidates:
        if candidate["key_id"] == key_id:
            return key_id, candidate["digest"]
    raise RuntimeError(SAFE_UNAVAILABLE) from None


def _canonical_digest_value(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise ValueError("INVALID_REQUEST") from None


def _operation_digest_for_key(
    domain: bytes, operation: str, value: object, key_id: str | None = None
) -> tuple[str, str]:
    if type(operation) is not str or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", operation) is None:
        raise ValueError("INVALID_REQUEST")
    keyring = DirectOnboardingSecrets()
    selected_key_id = key_id or keyring.digest_key_id
    if selected_key_id not in keyring.digest_keys:
        raise RuntimeError(SAFE_UNAVAILABLE) from None
    payload = operation.encode("ascii") + b"\0" + _canonical_digest_value(value)
    return selected_key_id, hmac.new(
        keyring.digest_keys[selected_key_id],
        domain + payload,
        hashlib.sha256,
    ).hexdigest()


def _operation_digest(domain: bytes, operation: str, value: object) -> str:
    return _operation_digest_for_key(domain, operation, value)[1]


def _operation_digest_candidates(
    domain: bytes, operation: str, value: object
) -> list[dict[str, str]]:
    keyring = DirectOnboardingSecrets()
    if len(keyring.digest_keys) > 16:
        raise RuntimeError(SAFE_UNAVAILABLE) from None
    return [
        {
            "key_id": key_id,
            "digest": _operation_digest_for_key(domain, operation, value, key_id)[1],
        }
        for key_id in sorted(keyring.digest_keys)
    ]


def request_digest(operation: str, value: object) -> str:
    return _operation_digest(_REQUEST_DIGEST, operation, value)


def postimage_digest(operation: str, value: object) -> str:
    return _operation_digest(_POSTIMAGE_DIGEST, operation, value)


def request_digest_candidates(operation: str, value: object) -> list[dict[str, str]]:
    return _operation_digest_candidates(_REQUEST_DIGEST, operation, value)


def postimage_digest_candidates(operation: str, value: object) -> list[dict[str, str]]:
    return _operation_digest_candidates(_POSTIMAGE_DIGEST, operation, value)


def current_digest_key_id() -> str:
    return DirectOnboardingSecrets().digest_key_id


def direct_create_request_digest(request: DirectCreateRequest) -> str:
    if not isinstance(request, DirectCreateRequest):
        raise ValueError("INVALID_REQUEST")
    return request_digest(
        "CREATE",
        request.model_dump(mode="json", exclude={"totp_code"}),
    )


def direct_create_request_digest_candidates(
    request: DirectCreateRequest,
) -> list[dict[str, str]]:
    return request_digest_candidates(
        "CREATE", request.model_dump(mode="json", exclude={"totp_code"})
    )


def build_direct_create_mutation(
    *,
    request: DirectCreateRequest,
    actor_user_id: int,
    idempotency_key: str,
    accepted_totp_step: int,
    id_factory: Callable[[], UUID],
    activation_code: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    if type(actor_user_id) is not int or actor_user_id < 1:
        raise ValueError("INVALID_REQUEST")
    if type(idempotency_key) is not str or not 1 <= len(idempotency_key) <= 128:
        raise ValueError("INVALID_REQUEST")
    if type(accepted_totp_step) is not int or accepted_totp_step < 0:
        raise ValueError("STEP_UP_FORBIDDEN")
    operation_id = _cursor_uuid_v7(id_factory())
    onboarding_id = _cursor_uuid_v7(id_factory())
    tenant_public_id = _cursor_uuid_v7(id_factory())
    credential_id = _cursor_uuid_v7(id_factory())
    audit_id = _cursor_uuid_v7(id_factory())
    event_id = _cursor_uuid_v7(id_factory())
    receipt_id = _cursor_uuid_v7(id_factory())
    credential_value = activation_code or generate_one_time_credential()
    credential_key_id, credential_value_digest = credential_digest(credential_value)
    phone_key_id, phone_digest = account_phone_claim_digest(request.admin_phone)
    phone_digest_candidates = account_phone_claim_digest_candidates(
        request.admin_phone
    )
    secrets = DirectOnboardingSecrets()
    crypto = DirectInstitutionCrypto(
        pii_current_key_id=secrets.pii_key_id,
        pii_keys=secrets.pii_keys,
        digest_current_key_id=secrets.digest_key_id,
        digest_keys=secrets.digest_keys,
    )
    phone_ciphertext = crypto.seal_text(
        request.admin_phone,
        aad=admin_phone_aad(tenant_public_id, onboarding_id),
    )
    request_value_digest = direct_create_request_digest(request)
    digest_key_id = secrets.digest_key_id
    expected_response = {
        "onboarding_id": str(onboarding_id),
        "tenant_id": str(tenant_public_id),
        "status": "PENDING_ACTIVATION",
        "version": 1,
        "credential_delivery_state": "ISSUED",
        "credential_id": str(credential_id),
    }
    expected_digest = postimage_digest("CREATE", expected_response)
    actor_scope = f"user:{actor_user_id}:platform"
    envelope = {
        "operation_id": str(operation_id),
        "actor_user_id": actor_user_id,
        "actor_role": "super_admin",
        "actor_scope": actor_scope,
        "idempotency_key": idempotency_key,
        "request_digest": request_value_digest,
        "digest_key_id": digest_key_id,
        "audit_id": str(audit_id),
        "event_id": str(event_id),
        "receipt_id": str(receipt_id),
        "expected_postimage_digest": expected_digest,
        "accepted_totp_step": accepted_totp_step,
        "onboarding_id": str(onboarding_id),
        "tenant_public_id": str(tenant_public_id),
        "institution_code": direct_institution_code(onboarding_id),
        "institution_name": request.institution_name,
        "institution_type": request.institution_type,
        "administrative_region_id": request.administrative_region_id,
        "admin_phone_ciphertext": base64.b64encode(phone_ciphertext).decode("ascii"),
        "admin_phone_key_id": secrets.pii_key_id,
        "phone_digest_key_id": phone_key_id,
        "phone_digest": phone_digest,
        "phone_digest_candidates": phone_digest_candidates,
        "duplicate_acknowledged": request.duplicate_acknowledged,
        "reason_code": request.reason_code,
        "reason_note": None,
        "credential_id": str(credential_id),
        "credential_digest_key_id": credential_key_id,
        "credential_digest": credential_value_digest,
    }
    confirmation = {
        "operation_id": str(operation_id),
        "actor_user_id": actor_user_id,
        "actor_role": "super_admin",
        "actor_scope": actor_scope,
        "idempotency_key": idempotency_key,
        "request_digest": request_value_digest,
        "digest_key_id": digest_key_id,
        "expected_version": 0,
        "audit_id": str(audit_id),
        "event_id": str(event_id),
        "receipt_id": str(receipt_id),
        "expected_postimage_digest": expected_digest,
        "operation": "CREATE",
        "target_id": str(onboarding_id),
        "target_status": "PENDING_ACTIVATION",
        "target_version": 1,
        "credential_id": str(credential_id),
        "credential_digest_key_id": credential_key_id,
        "credential_digest": credential_value_digest,
        "phone_claim_state": "PENDING",
        "tenant_public_id": str(tenant_public_id),
        "tenant_status": "pending",
        "user_id": None,
        "revision_id": None,
        "revision_status": None,
        "audit_action": "DIRECT_INSTITUTION_CREATE",
        "audit_evidence_digest": expected_digest,
        "outbox_event_type": "DIRECT_INSTITUTION_CREATED",
        "outbox_payload_digest": expected_digest,
        "receipt_response_digest": expected_digest,
    }
    return envelope, confirmation, credential_value


def direct_activation_request_digest(request: DirectActivationRequest) -> str:
    if not isinstance(request, DirectActivationRequest):
        raise ValueError("INVALID_REQUEST")
    return request_digest(
        "ACTIVATE",
        request.model_dump(mode="json", exclude={"totp_code"}),
    )


def direct_activation_request_digest_candidates(
    request: DirectActivationRequest,
) -> list[dict[str, str]]:
    return request_digest_candidates(
        "ACTIVATE", request.model_dump(mode="json", exclude={"totp_code"})
    )


def admin_handoff_create_request_digest(
    onboarding_id: UUID, request: AdminHandoffCreateRequest
) -> str:
    return request_digest(
        "HANDOFF_CREATE",
        {
            "onboarding_id": str(_cursor_uuid_v7(onboarding_id)),
            **request.model_dump(mode="json", exclude={"totp_code"}),
        },
    )


def admin_handoff_create_request_digest_candidates(
    onboarding_id: UUID, request: AdminHandoffCreateRequest
) -> list[dict[str, str]]:
    return request_digest_candidates(
        "HANDOFF_CREATE",
        {
            "onboarding_id": str(_cursor_uuid_v7(onboarding_id)),
            **request.model_dump(mode="json", exclude={"totp_code"}),
        },
    )


def admin_handoff_regenerate_request_digest(
    onboarding_id: UUID, handoff_id: UUID, request: VersionedStepUpRequest
) -> str:
    return request_digest(
        "HANDOFF_REGENERATE",
        {
            "onboarding_id": str(_cursor_uuid_v7(onboarding_id)),
            "handoff_id": str(_cursor_uuid_v7(handoff_id)),
            **request.model_dump(mode="json", exclude={"totp_code"}),
        },
    )


def admin_handoff_regenerate_request_digest_candidates(
    onboarding_id: UUID, handoff_id: UUID, request: VersionedStepUpRequest
) -> list[dict[str, str]]:
    return request_digest_candidates(
        "HANDOFF_REGENERATE",
        {
            "onboarding_id": str(_cursor_uuid_v7(onboarding_id)),
            "handoff_id": str(_cursor_uuid_v7(handoff_id)),
            **request.model_dump(mode="json", exclude={"totp_code"}),
        },
    )


def admin_handoff_activation_request_digest(
    request: AdminHandoffActivationRequest,
) -> str:
    if not isinstance(request, AdminHandoffActivationRequest):
        raise ValueError("INVALID_REQUEST")
    return request_digest(
        "HANDOFF_ACTIVATE", request.model_dump(mode="json", exclude={"totp_code"})
    )


def admin_handoff_activation_request_digest_candidates(
    request: AdminHandoffActivationRequest,
) -> list[dict[str, str]]:
    return request_digest_candidates(
        "HANDOFF_ACTIVATE", request.model_dump(mode="json", exclude={"totp_code"})
    )


def _handoff_review_confirmation(
    *,
    operation: str,
    operation_id: UUID,
    actor_user_id: int,
    actor_scope: str,
    idempotency_key: str,
    request_digest_value: str,
    digest_key_id: str,
    expected_version: int,
    audit_id: UUID,
    event_id: UUID,
    receipt_id: UUID,
    expected_postimage_digest: str,
    handoff_id: UUID,
    target_version: int,
    credential_id: UUID,
    credential_digest_key_id: str,
    credential_digest_value: str,
    tenant_id: int,
    phone_claim_state: str,
) -> dict[str, Any]:
    return {
        "operation_id": str(operation_id),
        "actor_user_id": actor_user_id,
        "actor_role": "super_admin",
        "actor_scope": actor_scope,
        "idempotency_key": idempotency_key,
        "request_digest": request_digest_value,
        "digest_key_id": digest_key_id,
        "expected_version": expected_version,
        "audit_id": str(audit_id),
        "event_id": str(event_id),
        "receipt_id": str(receipt_id),
        "expected_postimage_digest": expected_postimage_digest,
        "operation": operation,
        "target_id": str(handoff_id),
        "target_status": "ISSUED",
        "target_version": target_version,
        "credential_id": str(credential_id),
        "credential_digest_key_id": credential_digest_key_id,
        "credential_digest": credential_digest_value,
        "phone_claim_state": phone_claim_state,
        "tenant_id": tenant_id,
        "tenant_public_id": None,
        "tenant_status": "active",
        "user_id": None,
        "revision_id": None,
        "revision_status": None,
        "audit_action": f"ADMIN_{operation}",
        "audit_evidence_digest": expected_postimage_digest,
        "outbox_event_type": f"ADMIN_{operation}D",
        "outbox_payload_digest": expected_postimage_digest,
        "receipt_response_digest": expected_postimage_digest,
    }


def build_admin_handoff_create_mutation(
    *,
    onboarding_id: UUID,
    request: AdminHandoffCreateRequest,
    authority: dict[str, Any],
    actor_user_id: int,
    idempotency_key: str,
    accepted_totp_step: int,
    id_factory: Callable[[], UUID],
    activation_code: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    onboarding_id = _cursor_uuid_v7(onboarding_id)
    try:
        tenant_public_id = _cursor_uuid_v7(UUID(str(authority["tenant_public_id"])))
        tenant_id = int(authority["tenant_id"])
        target_version = int(authority["target_version"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("DIRECT_ONBOARDING_STATE_CONFLICT") from None
    if (
        type(actor_user_id) is not int
        or actor_user_id < 1
        or type(accepted_totp_step) is not int
        or accepted_totp_step < 0
    ):
        raise ValueError("INVALID_REQUEST")
    if (
        str(authority.get("onboarding_id")) != str(onboarding_id)
        or target_version != request.expected_version
    ):
        raise ValueError("STALE_VERSION")
    handoff_id = _cursor_uuid_v7(id_factory())
    operation_id = _cursor_uuid_v7(id_factory())
    credential_id = _cursor_uuid_v7(id_factory())
    audit_id = _cursor_uuid_v7(id_factory())
    event_id = _cursor_uuid_v7(id_factory())
    receipt_id = _cursor_uuid_v7(id_factory())
    phone_digest_key_id, phone_digest = account_phone_claim_digest(request.new_phone)
    phone_digest_candidates = account_phone_claim_digest_candidates(request.new_phone)
    secrets = DirectOnboardingSecrets()
    crypto = DirectInstitutionCrypto(
        pii_current_key_id=secrets.pii_key_id,
        pii_keys=secrets.pii_keys,
        digest_current_key_id=secrets.digest_key_id,
        digest_keys=secrets.digest_keys,
    )
    phone_ciphertext = crypto.seal_text(
        request.new_phone,
        aad=handoff_phone_aad(tenant_public_id, onboarding_id, handoff_id),
    )
    credential_value = activation_code or generate_one_time_credential()
    credential_key_id, credential_value_digest = credential_digest(credential_value)
    request_value_digest = admin_handoff_create_request_digest(onboarding_id, request)
    digest_key_id = secrets.digest_key_id
    expected_response = {
        "onboarding_id": str(onboarding_id),
        "tenant_id": str(tenant_public_id),
        "handoff_id": str(handoff_id),
        "status": "ISSUED",
        "version": 1,
        "credential_delivery_state": "ISSUED",
        "credential_id": str(credential_id),
    }
    expected_digest = postimage_digest("HANDOFF_CREATE", expected_response)
    actor_scope = f"user:{actor_user_id}:platform"
    envelope = {
        "operation_id": str(operation_id),
        "actor_user_id": actor_user_id,
        "actor_role": "super_admin",
        "actor_scope": actor_scope,
        "idempotency_key": idempotency_key,
        "request_digest": request_value_digest,
        "digest_key_id": digest_key_id,
        "expected_version": request.expected_version,
        "audit_id": str(audit_id),
        "event_id": str(event_id),
        "receipt_id": str(receipt_id),
        "expected_postimage_digest": expected_digest,
        "accepted_totp_step": accepted_totp_step,
        "handoff_id": str(handoff_id),
        "onboarding_id": str(onboarding_id),
        "new_phone_ciphertext": base64.b64encode(phone_ciphertext).decode("ascii"),
        "new_phone_key_id": secrets.pii_key_id,
        "new_phone_digest_key_id": phone_digest_key_id,
        "new_phone_digest_candidates": phone_digest_candidates,
        "new_phone_digest": phone_digest,
        "credential_id": str(credential_id),
        "credential_digest_key_id": credential_key_id,
        "credential_digest": credential_value_digest,
        "reason_code": request.reason_code,
    }
    confirmation = _handoff_review_confirmation(
        operation="HANDOFF_CREATE",
        operation_id=operation_id,
        actor_user_id=actor_user_id,
        actor_scope=actor_scope,
        idempotency_key=idempotency_key,
        request_digest_value=request_value_digest,
        digest_key_id=digest_key_id,
        expected_version=request.expected_version,
        audit_id=audit_id,
        event_id=event_id,
        receipt_id=receipt_id,
        expected_postimage_digest=expected_digest,
        handoff_id=handoff_id,
        target_version=1,
        credential_id=credential_id,
        credential_digest_key_id=credential_key_id,
        credential_digest_value=credential_value_digest,
        tenant_id=tenant_id,
        phone_claim_state="PENDING",
    )
    return envelope, confirmation, credential_value


def build_admin_handoff_regenerate_mutation(
    *,
    onboarding_id: UUID,
    handoff_id: UUID,
    request: VersionedStepUpRequest,
    authority: dict[str, Any],
    actor_user_id: int,
    idempotency_key: str,
    accepted_totp_step: int,
    id_factory: Callable[[], UUID],
    activation_code: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    onboarding_id = _cursor_uuid_v7(onboarding_id)
    handoff_id = _cursor_uuid_v7(handoff_id)
    try:
        tenant_id = int(authority["tenant_id"])
        old_credential_id = _cursor_uuid_v7(UUID(str(authority["active_credential_id"])))
        target_version = int(authority["target_version"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("DIRECT_ONBOARDING_STATE_CONFLICT") from None
    if (
        str(authority.get("onboarding_id")) != str(onboarding_id)
        or str(authority.get("handoff_id")) != str(handoff_id)
        or target_version != request.expected_version
    ):
        raise ValueError("STALE_VERSION")
    operation_id = _cursor_uuid_v7(id_factory())
    credential_id = _cursor_uuid_v7(id_factory())
    audit_id = _cursor_uuid_v7(id_factory())
    event_id = _cursor_uuid_v7(id_factory())
    receipt_id = _cursor_uuid_v7(id_factory())
    credential_value = activation_code or generate_one_time_credential()
    credential_key_id, credential_value_digest = credential_digest(credential_value)
    request_value_digest = admin_handoff_regenerate_request_digest(
        onboarding_id, handoff_id, request
    )
    digest_key_id = current_digest_key_id()
    expected_response = {
        "onboarding_id": str(onboarding_id),
        "handoff_id": str(handoff_id),
        "status": "ISSUED",
        "version": request.expected_version + 1,
        "credential_delivery_state": "ISSUED",
        "credential_id": str(credential_id),
    }
    expected_digest = postimage_digest("HANDOFF_REGENERATE", expected_response)
    actor_scope = f"user:{actor_user_id}:platform"
    envelope = {
        "operation_id": str(operation_id),
        "actor_user_id": actor_user_id,
        "actor_role": "super_admin",
        "actor_scope": actor_scope,
        "idempotency_key": idempotency_key,
        "request_digest": request_value_digest,
        "digest_key_id": digest_key_id,
        "expected_version": request.expected_version,
        "audit_id": str(audit_id),
        "event_id": str(event_id),
        "receipt_id": str(receipt_id),
        "expected_postimage_digest": expected_digest,
        "accepted_totp_step": accepted_totp_step,
        "handoff_id": str(handoff_id),
        "onboarding_id": str(onboarding_id),
        "old_credential_id": str(old_credential_id),
        "new_credential_id": str(credential_id),
        "new_credential_digest_key_id": credential_key_id,
        "new_credential_digest": credential_value_digest,
        "reason_code": request.reason_code,
    }
    confirmation = _handoff_review_confirmation(
        operation="HANDOFF_REGENERATE",
        operation_id=operation_id,
        actor_user_id=actor_user_id,
        actor_scope=actor_scope,
        idempotency_key=idempotency_key,
        request_digest_value=request_value_digest,
        digest_key_id=digest_key_id,
        expected_version=request.expected_version,
        audit_id=audit_id,
        event_id=event_id,
        receipt_id=receipt_id,
        expected_postimage_digest=expected_digest,
        handoff_id=handoff_id,
        target_version=request.expected_version + 1,
        credential_id=credential_id,
        credential_digest_key_id=credential_key_id,
        credential_digest_value=credential_value_digest,
        tenant_id=tenant_id,
        phone_claim_state="PENDING",
    )
    return envelope, confirmation, credential_value


def build_admin_handoff_activation_mutation(
    *,
    request: AdminHandoffActivationRequest,
    authority: dict[str, Any],
    idempotency_key: str,
    accepted_totp_step: int,
    id_factory: Callable[[], UUID],
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        tenant_public_id = _cursor_uuid_v7(UUID(str(authority["tenant_public_id"])))
        handoff_version = int(authority["handoff_version"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("DIRECT_ONBOARDING_STATE_CONFLICT") from None
    if (
        str(authority.get("onboarding_id")) != str(request.onboarding_id)
        or str(authority.get("handoff_id")) != str(request.handoff_id)
        or str(authority.get("credential_id")) != str(request.credential_id)
        or handoff_version != request.expected_version
    ):
        raise ValueError("DIRECT_ONBOARDING_STATE_CONFLICT")
    presented_credential_digests = credential_digest_candidates(
        request.activation_code
    )
    try:
        credential_key_id = str(authority["credential_digest_key_id"])
        stored_phone_key_id = str(authority["phone_digest_key_id"])
    except KeyError:
        raise ValueError("DIRECT_ONBOARDING_STATE_CONFLICT") from None
    _, presented_credential_digest = _digest_candidate_for_key(
        presented_credential_digests, credential_key_id
    )
    phone_key_id, phone_digest = account_phone_claim_digest_for_key(
        request.phone, stored_phone_key_id
    )
    totp_key_id, totp_ciphertext = seal_totp_secret(
        request.totp_secret,
        aad=direct_org_admin_totp_aad(
            tenant_public_id,
            request.onboarding_id,
            "ADMIN_HANDOFF",
            request.credential_id,
        ),
    )
    from app.modules.auth.service import hash_password

    operation_id = _cursor_uuid_v7(id_factory())
    audit_id = _cursor_uuid_v7(id_factory())
    event_id = _cursor_uuid_v7(id_factory())
    receipt_id = _cursor_uuid_v7(id_factory())
    request_value_digest = admin_handoff_activation_request_digest(request)
    digest_key_id = current_digest_key_id()
    expected_response = {
        "handoff_id": str(request.handoff_id),
        "onboarding_id": str(request.onboarding_id),
        "tenant_id": str(tenant_public_id),
        "status": "ACTIVATED",
        "version": handoff_version + 1,
    }
    expected_digest = postimage_digest("HANDOFF_ACTIVATE", expected_response)
    envelope = {
        "operation_id": str(operation_id),
        "idempotency_key": idempotency_key,
        "request_digest": request_value_digest,
        "digest_key_id": digest_key_id,
        "expected_version": handoff_version,
        "audit_id": str(audit_id),
        "event_id": str(event_id),
        "receipt_id": str(receipt_id),
        "expected_postimage_digest": expected_digest,
        "credential_id": str(request.credential_id),
        "presented_credential_digests": presented_credential_digests,
        "handoff_id": str(request.handoff_id),
        "onboarding_id": str(request.onboarding_id),
        "new_phone_digest_key_id": phone_key_id,
        "new_phone_digest": phone_digest,
        "new_phone": request.phone,
        "password_hash": hash_password(request.password),
        "totp_ciphertext": base64.b64encode(totp_ciphertext).decode("ascii"),
        "totp_key_id": totp_key_id,
        "totp_secret_digest": request_digest(
            "TOTP_SECRET",
            {"credential_id": str(request.credential_id), "secret": request.totp_secret},
        ),
        "accepted_totp_step": accepted_totp_step,
    }
    confirmation = {
        "operation_id": str(operation_id),
        "idempotency_key": idempotency_key,
        "request_digest": request_value_digest,
        "digest_key_id": digest_key_id,
        "expected_version": handoff_version,
        "audit_id": str(audit_id),
        "event_id": str(event_id),
        "receipt_id": str(receipt_id),
        "expected_postimage_digest": expected_digest,
        "operation": "HANDOFF_ACTIVATE",
        "handoff_id": str(request.handoff_id),
        "onboarding_id": str(request.onboarding_id),
        "tenant_public_id": str(tenant_public_id),
        "target_status": "ACTIVATED",
        "target_version": handoff_version + 1,
        "credential_id": str(request.credential_id),
        "credential_digest_key_id": credential_key_id,
        "credential_digest": presented_credential_digest,
        "phone_claim_state": "BOUND",
        "audit_action": "ADMIN_HANDOFF_ACTIVATE",
        "audit_evidence_digest": expected_digest,
        "outbox_event_type": "ADMIN_HANDOFF_ACTIVATED",
        "outbox_payload_digest": expected_digest,
        "receipt_response_digest": expected_digest,
    }
    return envelope, confirmation


def direct_regenerate_request_digest(
    onboarding_id: UUID, request: VersionedStepUpRequest
) -> str:
    return request_digest(
        "REGENERATE",
        {
            "onboarding_id": str(_cursor_uuid_v7(onboarding_id)),
            **request.model_dump(mode="json", exclude={"totp_code"}),
        },
    )


def direct_regenerate_request_digest_candidates(
    onboarding_id: UUID, request: VersionedStepUpRequest
) -> list[dict[str, str]]:
    return request_digest_candidates(
        "REGENERATE",
        {
            "onboarding_id": str(_cursor_uuid_v7(onboarding_id)),
            **request.model_dump(mode="json", exclude={"totp_code"}),
        },
    )


def build_direct_regenerate_mutation(
    *,
    onboarding_id: UUID,
    request: VersionedStepUpRequest,
    current: dict[str, Any],
    step_up: dict[str, Any],
    actor_user_id: int,
    idempotency_key: str,
    accepted_totp_step: int,
    id_factory: Callable[[], UUID],
    activation_code: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    onboarding_id = _cursor_uuid_v7(onboarding_id)
    if type(actor_user_id) is not int or actor_user_id < 1:
        raise ValueError("INVALID_REQUEST")
    if type(idempotency_key) is not str or not 1 <= len(idempotency_key) <= 128:
        raise ValueError("INVALID_REQUEST")
    if type(accepted_totp_step) is not int or accepted_totp_step < 0:
        raise ValueError("STEP_UP_FORBIDDEN")
    try:
        tenant_public_id = _cursor_uuid_v7(UUID(str(current["tenant_id"])))
        active_credential_id = _cursor_uuid_v7(
            UUID(str(step_up["active_credential_id"]))
        )
        target_version = int(step_up["target_version"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("DIRECT_ONBOARDING_STATE_CONFLICT") from None
    if (
        str(current.get("onboarding_id")) != str(onboarding_id)
        or current.get("status") != "PENDING_ACTIVATION"
        or int(current.get("version", 0)) != request.expected_version
        or target_version != request.expected_version
    ):
        raise ValueError("STALE_VERSION")
    operation_id = _cursor_uuid_v7(id_factory())
    new_credential_id = _cursor_uuid_v7(id_factory())
    audit_id = _cursor_uuid_v7(id_factory())
    event_id = _cursor_uuid_v7(id_factory())
    receipt_id = _cursor_uuid_v7(id_factory())
    credential_value = activation_code or generate_one_time_credential()
    credential_key_id, credential_value_digest = credential_digest(credential_value)
    digest = direct_regenerate_request_digest(onboarding_id, request)
    digest_key_id = current_digest_key_id()
    expected_response = {
        "onboarding_id": str(onboarding_id),
        "tenant_id": str(tenant_public_id),
        "status": "PENDING_ACTIVATION",
        "version": request.expected_version + 1,
        "credential_delivery_state": "ISSUED",
        "credential_id": str(new_credential_id),
    }
    expected_postimage_digest = postimage_digest("REGENERATE", expected_response)
    actor_scope = f"user:{actor_user_id}:platform"
    envelope = {
        "operation_id": str(operation_id),
        "actor_user_id": actor_user_id,
        "actor_role": "super_admin",
        "actor_scope": actor_scope,
        "idempotency_key": idempotency_key,
        "request_digest": digest,
        "digest_key_id": digest_key_id,
        "expected_version": request.expected_version,
        "audit_id": str(audit_id),
        "event_id": str(event_id),
        "receipt_id": str(receipt_id),
        "expected_postimage_digest": expected_postimage_digest,
        "accepted_totp_step": accepted_totp_step,
        "onboarding_id": str(onboarding_id),
        "old_credential_id": str(active_credential_id),
        "new_credential_id": str(new_credential_id),
        "new_credential_digest_key_id": credential_key_id,
        "new_credential_digest": credential_value_digest,
        "reason_code": request.reason_code,
    }
    confirmation = {
        "operation_id": str(operation_id),
        "actor_user_id": actor_user_id,
        "actor_role": "super_admin",
        "actor_scope": actor_scope,
        "idempotency_key": idempotency_key,
        "request_digest": digest,
        "digest_key_id": digest_key_id,
        "expected_version": request.expected_version,
        "audit_id": str(audit_id),
        "event_id": str(event_id),
        "receipt_id": str(receipt_id),
        "expected_postimage_digest": expected_postimage_digest,
        "operation": "REGENERATE",
        "target_id": str(onboarding_id),
        "target_status": "PENDING_ACTIVATION",
        "target_version": request.expected_version + 1,
        "credential_id": str(new_credential_id),
        "credential_digest_key_id": credential_key_id,
        "credential_digest": credential_value_digest,
        "phone_claim_state": "PENDING",
        "tenant_public_id": str(tenant_public_id),
        "tenant_status": "pending",
        "user_id": None,
        "revision_id": None,
        "revision_status": None,
        "audit_action": "DIRECT_ACTIVATION_REGENERATE",
        "audit_evidence_digest": expected_postimage_digest,
        "outbox_event_type": "DIRECT_ACTIVATION_REGENERATED",
        "outbox_payload_digest": expected_postimage_digest,
        "receipt_response_digest": expected_postimage_digest,
    }
    return envelope, confirmation, credential_value


def direct_revoke_request_digest(
    onboarding_id: UUID, request: VersionedStepUpRequest
) -> str:
    return request_digest(
        "REVOKE",
        {
            "onboarding_id": str(_cursor_uuid_v7(onboarding_id)),
            **request.model_dump(mode="json", exclude={"totp_code"}),
        },
    )


def direct_revoke_request_digest_candidates(
    onboarding_id: UUID, request: VersionedStepUpRequest
) -> list[dict[str, str]]:
    return request_digest_candidates(
        "REVOKE",
        {
            "onboarding_id": str(_cursor_uuid_v7(onboarding_id)),
            **request.model_dump(mode="json", exclude={"totp_code"}),
        },
    )


def build_direct_revoke_mutation(
    *,
    onboarding_id: UUID,
    request: VersionedStepUpRequest,
    current: dict[str, Any],
    step_up: dict[str, Any],
    actor_user_id: int,
    idempotency_key: str,
    accepted_totp_step: int,
    id_factory: Callable[[], UUID],
) -> tuple[dict[str, Any], dict[str, Any]]:
    onboarding_id = _cursor_uuid_v7(onboarding_id)
    if type(actor_user_id) is not int or actor_user_id < 1:
        raise ValueError("INVALID_REQUEST")
    if type(idempotency_key) is not str or not 1 <= len(idempotency_key) <= 128:
        raise ValueError("INVALID_REQUEST")
    if type(accepted_totp_step) is not int or accepted_totp_step < 0:
        raise ValueError("STEP_UP_FORBIDDEN")
    try:
        tenant_public_id = _cursor_uuid_v7(UUID(str(current["tenant_id"])))
        active_credential_id = _cursor_uuid_v7(
            UUID(str(step_up["active_credential_id"]))
        )
        target_version = int(step_up["target_version"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("DIRECT_ONBOARDING_STATE_CONFLICT") from None
    if (
        str(current.get("onboarding_id")) != str(onboarding_id)
        or current.get("status") != "PENDING_ACTIVATION"
        or int(current.get("version", 0)) != request.expected_version
        or target_version != request.expected_version
    ):
        raise ValueError("STALE_VERSION")
    operation_id = _cursor_uuid_v7(id_factory())
    audit_id = _cursor_uuid_v7(id_factory())
    event_id = _cursor_uuid_v7(id_factory())
    receipt_id = _cursor_uuid_v7(id_factory())
    digest = direct_revoke_request_digest(onboarding_id, request)
    digest_key_id = current_digest_key_id()
    expected_response = {
        "onboarding_id": str(onboarding_id),
        "tenant_id": str(tenant_public_id),
        "status": "REVOKED_BEFORE_ACTIVATION",
        "version": request.expected_version + 1,
    }
    expected_postimage_digest = postimage_digest("REVOKE", expected_response)
    actor_scope = f"user:{actor_user_id}:platform"
    envelope = {
        "operation_id": str(operation_id),
        "actor_user_id": actor_user_id,
        "actor_role": "super_admin",
        "actor_scope": actor_scope,
        "idempotency_key": idempotency_key,
        "request_digest": digest,
        "digest_key_id": digest_key_id,
        "expected_version": request.expected_version,
        "audit_id": str(audit_id),
        "event_id": str(event_id),
        "receipt_id": str(receipt_id),
        "expected_postimage_digest": expected_postimage_digest,
        "accepted_totp_step": accepted_totp_step,
        "onboarding_id": str(onboarding_id),
        "active_credential_id": str(active_credential_id),
        "reason_code": request.reason_code,
    }
    confirmation = {
        "operation_id": str(operation_id),
        "actor_user_id": actor_user_id,
        "actor_role": "super_admin",
        "actor_scope": actor_scope,
        "idempotency_key": idempotency_key,
        "request_digest": digest,
        "digest_key_id": digest_key_id,
        "expected_version": request.expected_version,
        "audit_id": str(audit_id),
        "event_id": str(event_id),
        "receipt_id": str(receipt_id),
        "expected_postimage_digest": expected_postimage_digest,
        "operation": "REVOKE",
        "target_id": str(onboarding_id),
        "target_status": "REVOKED_BEFORE_ACTIVATION",
        "target_version": request.expected_version + 1,
        "credential_id": str(active_credential_id),
        "credential_digest_key_id": None,
        "credential_digest": None,
        "phone_claim_state": "RELEASED",
        "tenant_public_id": str(tenant_public_id),
        "tenant_status": "pending",
        "user_id": None,
        "revision_id": None,
        "revision_status": None,
        "audit_action": "DIRECT_INSTITUTION_REVOKE",
        "audit_evidence_digest": expected_postimage_digest,
        "outbox_event_type": "DIRECT_INSTITUTION_REVOKED",
        "outbox_payload_digest": expected_postimage_digest,
        "receipt_response_digest": expected_postimage_digest,
    }
    return envelope, confirmation


def build_direct_activation_mutation(
    *,
    request: DirectActivationRequest,
    authority: dict[str, Any],
    idempotency_key: str,
    accepted_totp_step: int,
    id_factory: Callable[[], UUID],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if type(idempotency_key) is not str or not 1 <= len(idempotency_key) <= 128:
        raise ValueError("INVALID_REQUEST")
    if type(accepted_totp_step) is not int or accepted_totp_step < 0:
        raise ValueError("STEP_UP_FORBIDDEN")
    try:
        onboarding_id = _cursor_uuid_v7(UUID(str(authority["onboarding_id"])))
        tenant_public_id = _cursor_uuid_v7(UUID(str(authority["tenant_public_id"])))
        credential_id = _cursor_uuid_v7(UUID(str(authority["credential_id"])))
        root_version = int(authority["root_version"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("DIRECT_ONBOARDING_STATE_CONFLICT") from None
    if (
        onboarding_id != request.onboarding_id
        or credential_id != request.credential_id
        or authority.get("source_kind") != "DIRECT_ACTIVATION"
        or root_version != request.expected_version
    ):
        raise ValueError("DIRECT_ONBOARDING_STATE_CONFLICT")
    presented_credential_digests = credential_digest_candidates(
        request.activation_code
    )
    try:
        credential_key_id = str(authority["credential_digest_key_id"])
        stored_phone_key_id = str(authority["phone_digest_key_id"])
    except KeyError:
        raise ValueError("DIRECT_ONBOARDING_STATE_CONFLICT") from None
    _, presented_credential_digest = _digest_candidate_for_key(
        presented_credential_digests, credential_key_id
    )
    phone_key_id, phone_digest = account_phone_claim_digest_for_key(
        request.phone, stored_phone_key_id
    )
    totp_key_id, totp_ciphertext = seal_totp_secret(
        request.totp_secret,
        aad=direct_org_admin_totp_aad(
            tenant_public_id,
            onboarding_id,
            "DIRECT_ACTIVATION",
            credential_id,
        ),
    )
    from app.modules.auth.service import hash_password

    operation_id = _cursor_uuid_v7(id_factory())
    audit_id = _cursor_uuid_v7(id_factory())
    event_id = _cursor_uuid_v7(id_factory())
    receipt_id = _cursor_uuid_v7(id_factory())
    request_value_digest = direct_activation_request_digest(request)
    digest_key_id = current_digest_key_id()
    expected_response = {
        "onboarding_id": str(onboarding_id),
        "tenant_id": str(tenant_public_id),
        "status": "ACTIVE_COMPLIANCE_PENDING",
        "version": root_version + 1,
    }
    expected_digest = postimage_digest("ACTIVATE", expected_response)
    envelope = {
        "operation_id": str(operation_id),
        "idempotency_key": idempotency_key,
        "request_digest": request_value_digest,
        "digest_key_id": digest_key_id,
        "expected_version": root_version,
        "audit_id": str(audit_id),
        "event_id": str(event_id),
        "receipt_id": str(receipt_id),
        "expected_postimage_digest": expected_digest,
        "credential_id": str(credential_id),
        "presented_credential_digests": presented_credential_digests,
        "onboarding_id": str(onboarding_id),
        "phone_digest_key_id": phone_key_id,
        "phone_digest": phone_digest,
        "phone": request.phone,
        "password_hash": hash_password(request.password),
        "totp_ciphertext": base64.b64encode(totp_ciphertext).decode("ascii"),
        "totp_key_id": totp_key_id,
        "totp_secret_digest": request_digest(
            "TOTP_SECRET", {"credential_id": str(credential_id), "secret": request.totp_secret}
        ),
        "accepted_totp_step": accepted_totp_step,
    }
    confirmation = {
        "operation_id": str(operation_id),
        "idempotency_key": idempotency_key,
        "request_digest": request_value_digest,
        "digest_key_id": digest_key_id,
        "expected_version": root_version,
        "audit_id": str(audit_id),
        "event_id": str(event_id),
        "receipt_id": str(receipt_id),
        "expected_postimage_digest": expected_digest,
        "operation": "ACTIVATE",
        "target_id": str(onboarding_id),
        "target_status": "ACTIVE_COMPLIANCE_PENDING",
        "target_version": root_version + 1,
        "credential_id": str(credential_id),
        "credential_digest_key_id": credential_key_id,
        "credential_digest": presented_credential_digest,
        "phone_claim_state": "BOUND",
        "tenant_status": "active",
        "revision_id": None,
        "revision_status": None,
        "audit_action": "DIRECT_INSTITUTION_ACTIVATE",
        "audit_evidence_digest": expected_digest,
        "outbox_event_type": "DIRECT_INSTITUTION_ACTIVATED",
        "outbox_payload_digest": expected_digest,
        "receipt_response_digest": expected_digest,
    }
    return envelope, confirmation


def _digest_key(key_id: str) -> bytes:
    if type(key_id) is not str or _KEY_ID_RE.fullmatch(key_id) is None:
        raise RuntimeError(SAFE_UNAVAILABLE)
    key = DirectOnboardingSecrets().digest_keys.get(key_id)
    if key is None:
        raise RuntimeError(SAFE_UNAVAILABLE)
    return key


def compliance_business_digest(
    purpose: str,
    value: object,
    *,
    key_id: str | None = None,
) -> tuple[str, str]:
    domain = _COMPLIANCE_BUSINESS_DIGESTS.get(purpose)
    if domain is None:
        raise ValueError("INVALID_REQUEST")
    if key_id is None:
        keyring = DirectOnboardingSecrets()
        key_id = keyring.digest_key_id
        key = keyring.digest_keys[key_id]
    else:
        key = _digest_key(key_id)
    digest = hmac.new(
        key,
        domain + _canonical_digest_value(value),
        hashlib.sha256,
    ).hexdigest()
    return key_id, digest


def compliance_license_set_digest(
    licenses: tuple[ComplianceLicense, ...],
    *,
    key_id: str | None = None,
) -> tuple[str, str]:
    if type(licenses) is not tuple or not 1 <= len(licenses) <= 10:
        raise ValueError("INVALID_REQUEST")
    values = sorted(
        (license.model_dump(mode="json") for license in licenses),
        key=lambda value: value["license_id"],
    )
    if len({value["license_id"] for value in values}) != len(values):
        raise ValueError("INVALID_REQUEST")
    return compliance_business_digest("LICENSE_SET", values, key_id=key_id)


def compliance_payload_integrity_digest(
    payload: CompliancePayloadV1,
    *,
    key_id: str | None = None,
) -> tuple[str, str]:
    if key_id is None:
        keyring = DirectOnboardingSecrets()
        key_id = keyring.digest_key_id
        key = keyring.digest_keys[key_id]
    else:
        key = _digest_key(key_id)
    return key_id, hmac.new(
        key,
        _COMPLIANCE_PAYLOAD_DIGEST + canonical_compliance_payload(payload),
        hashlib.sha256,
    ).hexdigest()


def build_compliance_mutation(
    *,
    operation: str,
    request: DirectComplianceRequest,
    current: dict[str, Any],
    stored_licenses: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    actor_user_id: int,
    actor_tenant_id: int,
    idempotency_key: str,
    id_factory: Callable[[], UUID],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if operation not in {"COMPLIANCE_SAVE", "COMPLIANCE_SUBMIT"}:
        raise ValueError("INVALID_REQUEST")
    if type(actor_user_id) is not int or actor_user_id < 1:
        raise ValueError("INVALID_REQUEST")
    if type(actor_tenant_id) is not int or actor_tenant_id < 1:
        raise ValueError("INVALID_REQUEST")
    onboarding_id = _cursor_uuid_v7(current["onboarding_id"])
    tenant_public_id = _cursor_uuid_v7(current["tenant_public_id"])
    current_version = int(current["root_version"])
    if current_version != request.expected_version:
        raise ValueError("STALE_VERSION")
    if operation == "COMPLIANCE_SAVE":
        revision_id = _cursor_uuid_v7(id_factory())
        revision_no = int(current["revision_no"] or 0) + 1
        target_status = str(current["status"])
    else:
        if current.get("revision_id") is None or current.get("revision_no") is None:
            raise ValueError("DIRECT_ONBOARDING_STATE_CONFLICT")
        revision_id = _cursor_uuid_v7(current["revision_id"])
        revision_no = int(current["revision_no"])
        target_status = "COMPLIANCE_UNDER_REVIEW"

    operation_id = _cursor_uuid_v7(id_factory())
    audit_id = _cursor_uuid_v7(id_factory())
    receipt_id = _cursor_uuid_v7(id_factory())
    event_id = _cursor_uuid_v7(id_factory()) if operation == "COMPLIANCE_SUBMIT" else None
    secrets = DirectOnboardingSecrets()
    crypto = DirectInstitutionCrypto(
        pii_current_key_id=secrets.pii_key_id,
        pii_keys=secrets.pii_keys,
        digest_current_key_id=secrets.digest_key_id,
        digest_keys=secrets.digest_keys,
    )
    payload = CompliancePayloadV1.from_request(request)
    if operation == "COMPLIANCE_SAVE":
        payload_digest_key_id, payload_digest = compliance_payload_integrity_digest(
            payload
        )
    else:
        payload_digest_key_id = current.get("compliance_payload_digest_key_id")
        stored_payload_digest = current.get("compliance_payload_digest")
        _, payload_digest = compliance_payload_integrity_digest(
            payload,
            key_id=payload_digest_key_id,
        )
        if type(stored_payload_digest) is not str or not hmac.compare_digest(
            payload_digest, stored_payload_digest
        ):
            raise ValueError("DIRECT_ONBOARDING_STATE_CONFLICT")
    sealed_payload = (
        crypto.seal_compliance_payload(
            payload,
            aad=compliance_payload_aad(
                tenant_public_id, onboarding_id, revision_id, revision_no
            ),
        )
        if operation == "COMPLIANCE_SAVE"
        else None
    )
    stored_by_id: dict[str, dict[str, Any]] = {}
    if operation == "COMPLIANCE_SUBMIT":
        for stored_license in stored_licenses:
            stored_license_id = stored_license.get("license_id")
            if type(stored_license_id) is str:
                try:
                    stored_license_id = UUID(stored_license_id)
                except ValueError:
                    raise ValueError("DIRECT_ONBOARDING_STATE_CONFLICT") from None
            stored_id = str(_cursor_uuid_v7(stored_license_id))
            if stored_id in stored_by_id:
                raise ValueError("DIRECT_ONBOARDING_STATE_CONFLICT")
            stored_by_id[stored_id] = stored_license
        if len(stored_by_id) != len(request.licenses):
            raise ValueError("DIRECT_ONBOARDING_STATE_CONFLICT")
    licenses: list[dict[str, Any]] = []
    for license_value in request.licenses:
        license_no_ciphertext = None
        license_no_key_id = None
        license_no_digest_key_id = None
        license_no_digest = None
        if operation == "COMPLIANCE_SAVE" and license_value.license_no is not None:
            license_no_ciphertext = base64.b64encode(
                crypto.seal_text(
                    license_value.license_no,
                    aad=license_no_aad(
                        tenant_public_id,
                        onboarding_id,
                        revision_id,
                        license_value.license_id,
                    ),
                )
            ).decode("ascii")
            license_no_key_id = secrets.pii_key_id
            license_no_digest_key_id, license_no_digest = compliance_business_digest(
                "LICENSE_NO", license_value.license_no
            )
        license_item = {
                "license_id": str(license_value.license_id),
                "license_type": license_value.license_type,
                "license_no_digest_key_id": license_no_digest_key_id,
                "license_no_digest": license_no_digest,
                "private_file_id": str(license_value.private_file_id),
                "valid_from": license_value.valid_from.isoformat(),
                "valid_until": license_value.valid_until.isoformat(),
        }
        if operation == "COMPLIANCE_SAVE":
            license_item.update(
                {
                    "license_no_ciphertext": license_no_ciphertext,
                    "license_no_key_id": license_no_key_id,
                }
            )
        else:
            stored_license = stored_by_id.get(str(license_value.license_id))
            if stored_license is None:
                raise ValueError("DIRECT_ONBOARDING_STATE_CONFLICT")
            stored_key_id = stored_license.get("license_no_digest_key_id")
            stored_digest = stored_license.get("license_no_digest")
            if license_value.license_no is None:
                if stored_key_id is not None or stored_digest is not None:
                    raise ValueError("DIRECT_ONBOARDING_STATE_CONFLICT")
            else:
                if type(stored_digest) is not str:
                    raise ValueError("DIRECT_ONBOARDING_STATE_CONFLICT")
                license_item["license_no_digest_key_id"], computed_digest = (
                    compliance_business_digest(
                        "LICENSE_NO",
                        license_value.license_no,
                        key_id=stored_key_id,
                    )
                )
                if not hmac.compare_digest(computed_digest, stored_digest):
                    raise ValueError("DIRECT_ONBOARDING_STATE_CONFLICT")
                license_item["license_no_digest"] = computed_digest
        licenses.append(license_item)
    if operation == "COMPLIANCE_SAVE":
        credit_key_id, credit_digest = compliance_business_digest(
            "CREDIT_CODE", request.unified_social_credit_code
        )
        license_set_key_id, license_set_digest = compliance_license_set_digest(
            request.licenses
        )
    else:
        credit_key_id = current.get("unified_social_credit_code_digest_key_id")
        stored_credit_digest = current.get("unified_social_credit_code_digest")
        _, credit_digest = compliance_business_digest(
            "CREDIT_CODE",
            request.unified_social_credit_code,
            key_id=credit_key_id,
        )
        license_set_key_id = current.get("license_set_digest_key_id")
        stored_license_set_digest = current.get("license_set_digest")
        _, license_set_digest = compliance_license_set_digest(
            request.licenses,
            key_id=license_set_key_id,
        )
        if (
            type(stored_credit_digest) is not str
            or not hmac.compare_digest(credit_digest, stored_credit_digest)
            or type(stored_license_set_digest) is not str
            or not hmac.compare_digest(license_set_digest, stored_license_set_digest)
        ):
            raise ValueError("DIRECT_ONBOARDING_STATE_CONFLICT")
    digest = request_digest(operation, request.model_dump(mode="json"))
    digest_key_id = current_digest_key_id()
    digest_candidates = request_digest_candidates(
        operation, request.model_dump(mode="json")
    )
    response = {
        "onboarding_id": str(onboarding_id),
        "tenant_id": str(tenant_public_id),
        "revision_id": str(revision_id),
        "status": target_status,
        "version": current_version + 1,
    }
    expected_postimage_digest = postimage_digest(operation, response)
    actor_scope = f"user:{actor_user_id}:tenant:{actor_tenant_id}"
    envelope: dict[str, Any] = {
        "operation_id": str(operation_id),
        "actor_user_id": actor_user_id,
        "actor_role": "org_admin",
        "actor_scope": actor_scope,
        "idempotency_key": idempotency_key,
        "request_digest": digest,
        "digest_key_id": digest_key_id,
        "request_digest_candidates": digest_candidates,
        "expected_version": request.expected_version,
        "audit_id": str(audit_id),
        "receipt_id": str(receipt_id),
        "expected_postimage_digest": expected_postimage_digest,
        "onboarding_id": str(onboarding_id),
        "revision_id": str(revision_id),
        "revision_no": revision_no,
        "institution_name": request.institution_name,
        "institution_type": request.institution_type,
        "administrative_region_id": request.administrative_region_id,
        "institution_code": request.institution_code,
        "compliance_schema_version": payload.schema_version,
        "compliance_payload_digest_key_id": payload_digest_key_id,
        "compliance_payload_digest": payload_digest,
        "unified_social_credit_code_digest_key_id": credit_key_id,
        "unified_social_credit_code_digest": credit_digest,
        "license_set_digest_key_id": license_set_key_id,
        "license_set_digest": license_set_digest,
        "service_tags": list(request.service_tags),
        "licenses": licenses,
    }
    if event_id is not None:
        envelope["event_id"] = str(event_id)
    else:
        assert sealed_payload is not None
        envelope["compliance_payload_ciphertext"] = base64.b64encode(
            sealed_payload.ciphertext
        ).decode("ascii")
        envelope["compliance_payload_key_id"] = sealed_payload.key_id

    confirmation: dict[str, Any] = {
        "operation_id": str(operation_id),
        "actor_user_id": actor_user_id,
        "actor_role": "org_admin",
        "actor_scope": actor_scope,
        "idempotency_key": idempotency_key,
        "request_digest": digest,
        "digest_key_id": digest_key_id,
        "expected_version": request.expected_version,
        "audit_id": str(audit_id),
        "receipt_id": str(receipt_id),
        "expected_postimage_digest": expected_postimage_digest,
        "operation": operation,
        "target_status": target_status,
        "target_version": current_version + 1,
        "tenant_id": actor_tenant_id,
        "tenant_status": "active",
        "revision_id": str(revision_id),
        "revision_status": "DRAFT" if operation == "COMPLIANCE_SAVE" else "SUBMITTED",
        "audit_action": f"DIRECT_{operation}",
        "audit_evidence_digest": expected_postimage_digest,
        "receipt_response_digest": expected_postimage_digest,
    }
    if operation == "COMPLIANCE_SAVE":
        confirmation.update(
            {
                "onboarding_id": str(onboarding_id),
                "revision_no": revision_no,
                "compliance_schema_version": payload.schema_version,
                "compliance_payload_digest_key_id": payload_digest_key_id,
                "compliance_payload_digest": payload_digest,
                "unified_social_credit_code_digest_key_id": credit_key_id,
                "unified_social_credit_code_digest": credit_digest,
                "license_count": len(licenses),
                "license_set_digest_key_id": license_set_key_id,
                "license_set_digest": license_set_digest,
                "service_tags": list(request.service_tags),
                "licenses": licenses,
            }
        )
    else:
        confirmation.update(
            {
                "event_id": str(event_id),
                "target_id": str(onboarding_id),
                "credential_id": None,
                "credential_digest_key_id": None,
                "credential_digest": None,
                "credential_issued_at": None,
                "credential_expires_at": None,
                "phone_claim_state": None,
                "user_id": None,
                "outbox_event_type": "DIRECT_COMPLIANCE_SUBMITTED",
                "outbox_payload_digest": expected_postimage_digest,
            }
        )
    return envelope, confirmation


def direct_compliance_decision_request_digest(
    onboarding_id: UUID, request: ComplianceDecisionRequest
) -> str:
    return request_digest(
        "COMPLIANCE_DECIDE",
        {
            "onboarding_id": str(_cursor_uuid_v7(onboarding_id)),
            **request.model_dump(mode="json", exclude={"totp_code"}),
        },
    )


def direct_compliance_decision_request_digest_candidates(
    onboarding_id: UUID, request: ComplianceDecisionRequest
) -> list[dict[str, str]]:
    return request_digest_candidates(
        "COMPLIANCE_DECIDE",
        {
            "onboarding_id": str(_cursor_uuid_v7(onboarding_id)),
            **request.model_dump(mode="json", exclude={"totp_code"}),
        },
    )


def build_compliance_decision_mutation(
    *,
    onboarding_id: UUID,
    request: ComplianceDecisionRequest,
    authority: dict[str, Any],
    actor_user_id: int,
    idempotency_key: str,
    accepted_totp_step: int,
    id_factory: Callable[[], UUID],
) -> tuple[dict[str, Any], dict[str, Any]]:
    onboarding_id = _cursor_uuid_v7(onboarding_id)
    if type(actor_user_id) is not int or actor_user_id < 1:
        raise ValueError("INVALID_REQUEST")
    if type(accepted_totp_step) is not int or accepted_totp_step < 0:
        raise ValueError("STEP_UP_FORBIDDEN")
    if request.decision == "APPROVE" and request.correction_fields:
        raise ValueError("INVALID_REQUEST")
    if request.decision == "NEEDS_CORRECTION" and not request.correction_fields:
        raise ValueError("INVALID_REQUEST")
    try:
        tenant_public_id = _cursor_uuid_v7(UUID(str(authority["tenant_public_id"])))
        revision_id = _cursor_uuid_v7(UUID(str(authority["revision_id"])))
        revision_no = int(authority["revision_no"])
        target_version = int(authority["target_version"])
        tenant_id = int(authority["tenant_id"])
        sealed = SealedCompliancePayload(
            ciphertext=base64.b64decode(
                authority["compliance_payload_ciphertext"], validate=True
            ),
            key_id=authority["compliance_payload_key_id"],
            digest_key_id=authority["compliance_payload_digest_key_id"],
            digest=authority["compliance_payload_digest"],
        )
        licenses = list(authority["licenses"])
        service_tags = list(authority["service_tags"])
    except (KeyError, TypeError, ValueError, binascii.Error):
        raise ValueError("DIRECT_ONBOARDING_STATE_CONFLICT") from None
    if (
        str(authority.get("onboarding_id")) != str(onboarding_id)
        or revision_id != request.revision_id
        or target_version != request.expected_version
    ):
        raise ValueError("STALE_VERSION")
    secrets = DirectOnboardingSecrets()
    crypto = DirectInstitutionCrypto(
        pii_current_key_id=secrets.pii_key_id,
        pii_keys=secrets.pii_keys,
        digest_current_key_id=secrets.digest_key_id,
        digest_keys=secrets.digest_keys,
    )
    credit_code = approved_credit_code(
        crypto,
        sealed=sealed,
        aad=compliance_payload_aad(
            tenant_public_id, onboarding_id, revision_id, revision_no
        ),
    )
    operation_id = _cursor_uuid_v7(id_factory())
    audit_id = _cursor_uuid_v7(id_factory())
    event_id = _cursor_uuid_v7(id_factory())
    receipt_id = _cursor_uuid_v7(id_factory())
    digest = direct_compliance_decision_request_digest(onboarding_id, request)
    digest_key_id = current_digest_key_id()
    target_status = (
        "COMPLIANCE_APPROVED"
        if request.decision == "APPROVE"
        else "COMPLIANCE_NEEDS_CORRECTION"
    )
    response = {
        "onboarding_id": str(onboarding_id),
        "revision_id": str(revision_id),
        "revision_no": revision_no,
        "status": target_status,
        "institution_name": authority["institution_name"],
        "institution_type": authority["institution_type"],
        "administrative_region_id": authority["administrative_region_id"],
        "institution_code": authority["institution_code"],
        "service_tags": service_tags,
        "licenses": licenses,
        "correction_fields": (
            list(request.correction_fields)
            if request.decision == "NEEDS_CORRECTION"
            else []
        ),
        "version": request.expected_version + 1,
    }
    expected_digest = postimage_digest("COMPLIANCE_DECIDE", response)
    actor_scope = f"user:{actor_user_id}:platform"
    envelope = {
        "operation_id": str(operation_id),
        "actor_user_id": actor_user_id,
        "actor_role": "super_admin",
        "actor_scope": actor_scope,
        "idempotency_key": idempotency_key,
        "request_digest": digest,
        "digest_key_id": digest_key_id,
        "expected_version": request.expected_version,
        "audit_id": str(audit_id),
        "event_id": str(event_id),
        "receipt_id": str(receipt_id),
        "expected_postimage_digest": expected_digest,
        "accepted_totp_step": accepted_totp_step,
        "onboarding_id": str(onboarding_id),
        "revision_id": str(revision_id),
        "decision_id": str(operation_id),
        "decision": request.decision,
        "reason_code": request.reason_code,
        "correction_fields": list(request.correction_fields),
    }
    if request.decision == "APPROVE":
        envelope["unified_social_credit_code"] = credit_code
    confirmation = {
        "operation_id": str(operation_id),
        "actor_user_id": actor_user_id,
        "actor_role": "super_admin",
        "actor_scope": actor_scope,
        "idempotency_key": idempotency_key,
        "request_digest": digest,
        "digest_key_id": digest_key_id,
        "expected_version": request.expected_version,
        "audit_id": str(audit_id),
        "event_id": str(event_id),
        "receipt_id": str(receipt_id),
        "expected_postimage_digest": expected_digest,
        "operation": "COMPLIANCE_DECIDE",
        "target_id": str(onboarding_id),
        "target_status": target_status,
        "target_version": request.expected_version + 1,
        "credential_id": None,
        "credential_digest_key_id": None,
        "credential_digest": None,
        "phone_claim_state": None,
        "tenant_id": tenant_id,
        "tenant_public_id": str(tenant_public_id),
        "tenant_status": "active",
        "user_id": None,
        "revision_id": str(revision_id),
        "revision_status": "APPROVED" if request.decision == "APPROVE" else "NEEDS_CORRECTION",
        "audit_action": "DIRECT_COMPLIANCE_DECIDE",
        "audit_evidence_digest": expected_digest,
        "outbox_event_type": "DIRECT_COMPLIANCE_DECIDED",
        "outbox_payload_digest": expected_digest,
        "receipt_response_digest": expected_digest,
    }
    return envelope, confirmation


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _cursor_uuid_v7(value: UUID) -> UUID:
    try:
        normalized = UUID(int=value.int)
    except (AttributeError, TypeError, ValueError):
        raise ValueError("INVALID_REQUEST") from None
    if normalized.version != 7:
        raise ValueError("INVALID_REQUEST")
    return normalized


def _cursor_scope(*, actor_user_id: int, status: str | None) -> dict[str, object]:
    if type(actor_user_id) is not int or actor_user_id < 1:
        raise ValueError("INVALID_REQUEST")
    if status is not None and type(status) is not str:
        raise ValueError("INVALID_REQUEST")
    return {"actor_user_id": actor_user_id, "status": status}


def encode_direct_onboarding_cursor(
    cursor_id: UUID,
    *,
    ceiling_id: UUID,
    actor_user_id: int,
    status: str | None,
) -> str:
    cursor_uuid = _cursor_uuid_v7(cursor_id)
    ceiling_uuid = _cursor_uuid_v7(ceiling_id)
    if cursor_uuid > ceiling_uuid:
        raise ValueError("INVALID_REQUEST")
    raw = json.dumps(
        {
            "ceiling_id": str(ceiling_uuid),
            "cursor_id": str(cursor_uuid),
            "scope": _cursor_scope(actor_user_id=actor_user_id, status=status),
            "v": 1,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    keyring = DirectOnboardingSecrets()
    signature = hmac.new(
        keyring.digest_keys[keyring.digest_key_id],
        _DIRECT_ONBOARDING_CURSOR_DOMAIN + raw,
        hashlib.sha256,
    ).digest()
    return f"{_base64url(raw)}.{_base64url(signature)}"


def decode_direct_onboarding_cursor(
    cursor: str,
    *,
    actor_user_id: int,
    status: str | None,
) -> tuple[UUID, UUID]:
    expected_scope = _cursor_scope(actor_user_id=actor_user_id, status=status)
    try:
        encoded_payload, encoded_signature = cursor.split(".")
        raw = base64.b64decode(
            (encoded_payload + "=" * (-len(encoded_payload) % 4)).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        signature = base64.b64decode(
            (encoded_signature + "=" * (-len(encoded_signature) % 4)).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        if _base64url(raw) != encoded_payload or _base64url(signature) != encoded_signature:
            raise ValueError
        keyring = DirectOnboardingSecrets()
        expected_signature = hmac.new(
            keyring.digest_keys[keyring.digest_key_id],
            _DIRECT_ONBOARDING_CURSOR_DOMAIN + raw,
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError
        payload = json.loads(raw, object_pairs_hook=_unique_object)
        if (
            type(payload) is not dict
            or set(payload) != {"ceiling_id", "cursor_id", "scope", "v"}
            or payload["v"] != 1
            or payload["scope"] != expected_scope
        ):
            raise ValueError
        cursor_uuid = _cursor_uuid_v7(UUID(payload["cursor_id"]))
        ceiling_uuid = _cursor_uuid_v7(UUID(payload["ceiling_id"]))
        if cursor_uuid > ceiling_uuid:
            raise ValueError
        return cursor_uuid, ceiling_uuid
    except (
        AttributeError,
        binascii.Error,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        raise ValueError("INVALID_REQUEST") from None


def _uuid_v7_bytes(value: UUID) -> bytes:
    normalized = UUID(int=value.int)
    if normalized.version != 7:
        raise ValueError("UUID_V7_REQUIRED")
    return str(normalized).encode("ascii")


def _aad(domain: bytes, *parts: bytes) -> bytes:
    return b"\0".join((domain, *parts))


def _actor_user_id_bytes(actor_user_id: int) -> bytes:
    if type(actor_user_id) is not int or actor_user_id < 1:
        raise ValueError("ACTOR_USER_ID_INVALID")
    return str(actor_user_id).encode("ascii")


def platform_admin_totp_aad(actor_user_id: int) -> bytes:
    return _aad(_PLATFORM_ADMIN_TOTP_AAD, _actor_user_id_bytes(actor_user_id))


def direct_org_admin_totp_aad(
    tenant_public_id: UUID,
    onboarding_id: UUID,
    source_kind: str,
    credential_id: UUID,
) -> bytes:
    if source_kind not in {"DIRECT_ACTIVATION", "ADMIN_HANDOFF"}:
        raise ValueError("TOTP_SOURCE_KIND_INVALID")
    return _aad(
        _DIRECT_ORG_ADMIN_TOTP_AAD,
        _uuid_v7_bytes(tenant_public_id),
        _uuid_v7_bytes(onboarding_id),
        source_kind.encode("ascii"),
        _uuid_v7_bytes(credential_id),
    )


def seal_totp_secret(value: str, *, aad: bytes) -> tuple[str, bytes]:
    if type(value) is not str or type(aad) is not bytes or not aad:
        raise ValueError("TOTP_ENCRYPTION_FAILED")
    keyring = DirectOnboardingSecrets()
    nonce = os.urandom(12)
    ciphertext = nonce + AESGCM(keyring.totp_keys[keyring.totp_key_id]).encrypt(
        nonce, value.encode("ascii"), aad
    )
    return keyring.totp_key_id, ciphertext


def open_totp_secret(value: bytes, *, key_id: str, aad: bytes) -> str:
    keyring = DirectOnboardingSecrets()
    key = keyring.totp_keys.get(key_id)
    if key is None:
        raise ValueError("TOTP_KEY_UNAVAILABLE")
    if type(value) is not bytes or len(value) < 28 or type(aad) is not bytes or not aad:
        raise ValueError("TOTP_DECRYPTION_FAILED")
    try:
        return AESGCM(key).decrypt(value[:12], value[12:], aad).decode("ascii")
    except (InvalidTag, UnicodeDecodeError, ValueError):
        raise ValueError("TOTP_DECRYPTION_FAILED") from None


def admin_phone_aad(tenant_public_id: UUID, onboarding_id: UUID) -> bytes:
    return _aad(
        _ADMIN_PHONE_AAD,
        _uuid_v7_bytes(tenant_public_id),
        _uuid_v7_bytes(onboarding_id),
    )


def handoff_phone_aad(
    tenant_public_id: UUID, onboarding_id: UUID, handoff_id: UUID
) -> bytes:
    return _aad(
        _HANDOFF_PHONE_AAD,
        _uuid_v7_bytes(tenant_public_id),
        _uuid_v7_bytes(onboarding_id),
        _uuid_v7_bytes(handoff_id),
    )


def license_no_aad(
    tenant_public_id: UUID,
    onboarding_id: UUID,
    revision_id: UUID,
    license_id: UUID,
) -> bytes:
    return _aad(
        _LICENSE_NO_AAD,
        _uuid_v7_bytes(tenant_public_id),
        _uuid_v7_bytes(onboarding_id),
        _uuid_v7_bytes(revision_id),
        _uuid_v7_bytes(license_id),
    )


def compliance_payload_aad(
    tenant_public_id: UUID,
    onboarding_id: UUID,
    revision_id: UUID,
    revision_no: int,
) -> bytes:
    if type(revision_no) is not int or revision_no < 1:
        raise ValueError("REVISION_NO_INVALID")
    return _aad(
        _COMPLIANCE_PAYLOAD_AAD,
        _uuid_v7_bytes(tenant_public_id),
        _uuid_v7_bytes(onboarding_id),
        _uuid_v7_bytes(revision_id),
        str(revision_no).encode("ascii"),
    )


def canonical_compliance_payload(payload: CompliancePayloadV1) -> bytes:
    return json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("COMPLIANCE_PAYLOAD_INVALID")
        value[key] = item
    return value


def _validated_keys(current_key_id: str, keys: dict[str, bytes]) -> dict[str, bytes]:
    if _KEY_ID_RE.fullmatch(current_key_id) is None or current_key_id not in keys:
        raise ValueError("KEYRING_INVALID")
    if not keys or any(
        _KEY_ID_RE.fullmatch(key_id) is None or type(material) is not bytes or len(material) != 32
        for key_id, material in keys.items()
    ):
        raise ValueError("KEYRING_INVALID")
    return dict(keys)


class SealedCompliancePayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    ciphertext: bytes
    key_id: str
    digest_key_id: str
    digest: str


class DirectInstitutionCrypto:
    def __init__(
        self,
        *,
        pii_current_key_id: str,
        pii_keys: dict[str, bytes],
        digest_current_key_id: str,
        digest_keys: dict[str, bytes],
    ) -> None:
        self._pii_current_key_id = pii_current_key_id
        self._pii_keys = _validated_keys(pii_current_key_id, pii_keys)
        self._digest_current_key_id = digest_current_key_id
        self._digest_keys = _validated_keys(digest_current_key_id, digest_keys)
        if set(self._pii_keys.values()) & set(self._digest_keys.values()):
            raise ValueError("KEYRING_PURPOSE_REUSE")

    def seal_text(self, value: str, *, aad: bytes) -> bytes:
        if type(value) is not str or type(aad) is not bytes or not aad:
            raise ValueError("PII_ENCRYPTION_FAILED")
        nonce = os.urandom(12)
        return nonce + AESGCM(self._pii_keys[self._pii_current_key_id]).encrypt(
            nonce, value.encode("utf-8"), aad
        )

    def open_text(self, value: bytes, *, key_id: str, aad: bytes) -> str:
        key = self._pii_keys.get(key_id)
        if key is None:
            raise ValueError("PII_KEY_UNAVAILABLE")
        if type(value) is not bytes or len(value) < 28 or type(aad) is not bytes or not aad:
            raise ValueError("PII_DECRYPTION_FAILED")
        try:
            return AESGCM(key).decrypt(value[:12], value[12:], aad).decode("utf-8")
        except (InvalidTag, UnicodeDecodeError, ValueError):
            raise ValueError("PII_DECRYPTION_FAILED") from None

    def seal_compliance_payload(
        self, payload: CompliancePayloadV1, *, aad: bytes
    ) -> SealedCompliancePayload:
        plaintext = canonical_compliance_payload(payload)
        nonce = os.urandom(12)
        ciphertext = nonce + AESGCM(
            self._pii_keys[self._pii_current_key_id]
        ).encrypt(nonce, plaintext, aad)
        digest = hmac.new(
            self._digest_keys[self._digest_current_key_id],
            _COMPLIANCE_PAYLOAD_DIGEST + plaintext,
            hashlib.sha256,
        ).hexdigest()
        return SealedCompliancePayload(
            ciphertext=ciphertext,
            key_id=self._pii_current_key_id,
            digest_key_id=self._digest_current_key_id,
            digest=digest,
        )

    def open_compliance_payload(
        self, sealed: SealedCompliancePayload, *, aad: bytes
    ) -> CompliancePayloadV1:
        key = self._pii_keys.get(sealed.key_id)
        if key is None:
            raise ValueError("PII_KEY_UNAVAILABLE")
        if len(sealed.ciphertext) < 28:
            raise ValueError("PII_DECRYPTION_FAILED")
        try:
            plaintext = AESGCM(key).decrypt(
                sealed.ciphertext[:12], sealed.ciphertext[12:], aad
            )
            decoded = json.loads(plaintext, object_pairs_hook=_unique_object)
            payload = CompliancePayloadV1.model_validate(decoded)
        except (InvalidTag, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise ValueError("PII_DECRYPTION_FAILED") from None
        if canonical_compliance_payload(payload) != plaintext:
            raise ValueError("PII_DECRYPTION_FAILED")
        digest_key = self._digest_keys.get(sealed.digest_key_id)
        if digest_key is None:
            raise ValueError("DIGEST_KEY_UNAVAILABLE")
        actual_digest = hmac.new(
            digest_key,
            _COMPLIANCE_PAYLOAD_DIGEST + plaintext,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(actual_digest, sealed.digest):
            raise ValueError("COMPLIANCE_PAYLOAD_DIGEST_INVALID")
        return payload


def approved_credit_code(
    crypto: DirectInstitutionCrypto,
    *,
    sealed: SealedCompliancePayload,
    aad: bytes,
) -> str:
    """Return the exact approved value only after AEAD and payload digest checks."""
    return crypto.open_compliance_payload(sealed, aad=aad).unified_social_credit_code
