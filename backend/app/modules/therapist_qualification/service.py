from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException

from app.core.security import CurrentUser
from app.core.uuid_generator import Uuid7Generator
from app.modules.auth.service import hash_password
from app.modules.therapist_qualification.domain import (
    ProfileStatus,
    ReadinessInputs,
    TherapistConflict,
    compute_service_readiness,
)
from app.modules.therapist_qualification.models import (
    InstitutionServiceReadinessModel,
    ReadinessEvidenceModel,
    TherapistInvitationModel,
    TherapistProfileModel,
    TherapistProfileRevisionModel,
    TherapistQualificationAttachmentModel,
    TherapistQualificationVersionModel,
    TherapistReviewDecisionModel,
    TherapistReviewItemModel,
    TherapistRevisionQualificationModel,
    TherapistStatusDecisionModel,
    TherapistWorkflowAuditModel,
    TherapistWorkflowIdempotencyModel,
    TherapistWorkflowOutboxModel,
)
from app.modules.therapist_qualification.repository import TherapistQualificationRepository
from app.modules.therapist_qualification.schemas import (
    QualificationInput,
    TherapistActivate,
    TherapistInvitationCreate,
    TherapistInvitationRevoke,
    TherapistProfileDraft,
    TherapistRenew,
    TherapistResubmit,
    TherapistReviewDecisionRequest,
    TherapistSubmit,
    TherapistRenewalResubmit,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _canonical(value: object) -> bytes:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


COMMITTED = "COMMITTED"
NOT_COMMITTED = "NOT_COMMITTED"
UNKNOWN = "UNKNOWN"


def _load_keyring(current_name: str, keyring_name: str) -> tuple[str, dict[str, bytes]]:
    def unique_pairs(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError
            value[key] = item
        return value

    try:
        current = os.environ[current_name]
        source = json.loads(os.environ[keyring_name], object_pairs_hook=unique_pairs)
        if not isinstance(source, dict) or not source:
            raise ValueError
        keyring = {key: base64.b64decode(value, validate=True) for key, value in source.items()}
        if current not in keyring or any(len(value) != 32 for value in keyring.values()):
            raise ValueError
        return current, keyring
    except Exception:
        raise RuntimeError("Therapist secret configuration is unavailable") from None


class TherapistSecrets:
    def __init__(self) -> None:
        self.pii_key_id, self.pii_keys = _load_keyring(
            "KG_THERAPIST_PII_ENCRYPTION_CURRENT_KEY_ID",
            "KG_THERAPIST_PII_ENCRYPTION_KEYRING_JSON",
        )
        self.digest_key_id, self.digest_keys = _load_keyring(
            "KG_THERAPIST_PII_DIGEST_CURRENT_KEY_ID",
            "KG_THERAPIST_PII_DIGEST_KEYRING_JSON",
        )
        self.totp_key_id, self.totp_keys = _load_keyring(
            "KG_THERAPIST_TOTP_ENCRYPTION_CURRENT_KEY_ID",
            "KG_THERAPIST_TOTP_ENCRYPTION_KEYRING_JSON",
        )
        self.code_key_id, self.code_keys = _load_keyring(
            "KG_THERAPIST_INVITATION_CODE_HMAC_CURRENT_KEY_ID",
            "KG_THERAPIST_INVITATION_CODE_HMAC_KEYRING_JSON",
        )
        self.replay_key_id, self.replay_keys = _load_keyring(
            "KG_THERAPIST_REPLAY_ENCRYPTION_CURRENT_KEY_ID",
            "KG_THERAPIST_REPLAY_ENCRYPTION_KEYRING_JSON",
        )
        self.readiness_key_id, self.readiness_keys = _load_keyring(
            "KG_THERAPIST_READINESS_DIGEST_CURRENT_KEY_ID",
            "KG_THERAPIST_READINESS_DIGEST_KEYRING_JSON",
        )
        self.delivery_key_id, self.delivery_keys = _load_keyring(
            "KG_THERAPIST_DELIVERY_TARGET_HMAC_CURRENT_KEY_ID",
            "KG_THERAPIST_DELIVERY_TARGET_HMAC_KEYRING_JSON",
        )
        material_domains: dict[bytes, str] = {}
        for domain, keyring in (
            ("pii-encryption", self.pii_keys),
            ("pii-digest", self.digest_keys),
            ("totp", self.totp_keys),
            ("invitation-code", self.code_keys),
            ("replay", self.replay_keys),
            ("readiness", self.readiness_keys),
            ("delivery", self.delivery_keys),
        ):
            for material in keyring.values():
                previous = material_domains.setdefault(material, domain)
                if previous != domain:
                    raise RuntimeError("Therapist secret configuration is unavailable") from None

    @staticmethod
    def _encrypt(key: bytes, value: str, aad: bytes) -> bytes:
        nonce = secrets.token_bytes(12)
        return nonce + AESGCM(key).encrypt(nonce, value.encode(), aad)

    @staticmethod
    def _decrypt(key: bytes, value: bytes, aad: bytes) -> str:
        return AESGCM(key).decrypt(value[:12], value[12:], aad).decode()

    @staticmethod
    def _aad(field: str, tenant_public_id, object_id) -> bytes:
        if field not in {
            "invitation-phone",
            "profile-real-name",
            "qualification-certificate",
            "totp-secret",
        }:
            raise RuntimeError("Therapist secret configuration is unavailable") from None
        try:
            tenant = str(UUID(str(tenant_public_id))).lower()
            object_value = str(UUID(str(object_id))).lower()
        except (TypeError, ValueError, AttributeError):
            raise RuntimeError("Therapist secret configuration is unavailable") from None
        return f"phase1-slice2/{field}/v1\0{tenant}\0{object_value}".encode()

    def encrypt_pii(self, value: str, *, field: str, tenant_public_id, object_id) -> bytes:
        return self._encrypt(
            self.pii_keys[self.pii_key_id],
            value,
            self._aad(field, tenant_public_id, object_id),
        )

    def decrypt_pii(self, value: bytes, key_id: str, *, field: str, tenant_public_id, object_id) -> str:
        key = self.pii_keys.get(key_id)
        if key is None:
            raise RuntimeError("Therapist secret configuration is unavailable")
        try:
            return self._decrypt(key, value, self._aad(field, tenant_public_id, object_id))
        except Exception:
            raise RuntimeError("Therapist secret configuration is unavailable") from None

    def digest_pii(self, value: str, *, field: str, key_id: str | None = None) -> str:
        selected = key_id or self.digest_key_id
        key = self.digest_keys.get(selected)
        if key is None or field not in {"invitation-phone", "profile-real-name", "qualification-certificate"}:
            raise RuntimeError("Therapist secret configuration is unavailable") from None
        prefix = f"phase1-slice2/{field}-lookup/v1\0".encode()
        return hmac.new(key, prefix + value.encode(), hashlib.sha256).hexdigest()

    def code_digest(self, code: str, *, key_id: str | None = None) -> str:
        selected = key_id or self.code_key_id
        key = self.code_keys[selected]
        return hmac.new(key, b"phase1-slice2/invitation-code/v1\0" + code.encode(), hashlib.sha256).hexdigest()

    def encrypt_totp(self, value: str, tenant_public_id, therapist_id) -> bytes:
        return self._encrypt(
            self.totp_keys[self.totp_key_id],
            value,
            self._aad("totp-secret", tenant_public_id, therapist_id),
        )

    def decrypt_totp(self, value: bytes, key_id: str, tenant_public_id, therapist_id) -> str:
        key = self.totp_keys.get(key_id)
        if key is None:
            raise RuntimeError("Therapist secret configuration is unavailable")
        try:
            return self._decrypt(
                key,
                value,
                self._aad("totp-secret", tenant_public_id, therapist_id),
            )
        except Exception:
            raise RuntimeError("Therapist secret configuration is unavailable") from None

    def encrypt_replay(self, value: dict, scope: str, operation: str, key: str) -> bytes:
        aad = f"phase1-slice2/replay/v1\0{scope}\0{operation}\0{key}".encode()
        return self._encrypt(self.replay_keys[self.replay_key_id], _canonical(value).decode(), aad)

    def decrypt_replay(self, value: bytes, key_id: str, scope: str, operation: str, key: str) -> dict:
        aad = f"phase1-slice2/replay/v1\0{scope}\0{operation}\0{key}".encode()
        raw = self._decrypt(self.replay_keys[key_id], value, aad)
        return json.loads(raw)

    def readiness_digest(self, domain: str, value: object) -> str:
        key = self.readiness_keys[self.readiness_key_id]
        prefix = f"phase1-slice2/readiness/{domain}/v1\0".encode()
        return hmac.new(key, prefix + _canonical(value), hashlib.sha256).hexdigest()

    def delivery_target_digest(self, value: dict, *, key_id: str | None = None) -> str:
        selected = key_id or self.delivery_key_id
        key = self.delivery_keys[selected]
        return hmac.new(
            key,
            b"phase1-slice2/delivery-target/v1\0" + _canonical(value),
            hashlib.sha256,
        ).hexdigest()


async def _rollback(session) -> None:
    task = asyncio.create_task(session.rollback())
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


async def _replay(repo: TherapistQualificationRepository, *, scope: str, operation: str, key: str, request: object):
    request_digest = _digest(request)
    await repo.lock_operation(scope, operation, key)
    row = await repo.idempotency(scope, operation, key)
    if row is None:
        return request_digest, None
    if not hmac.compare_digest(row.request_digest, request_digest):
        raise HTTPException(409, "IDEMPOTENCY_CONFLICT")
    return request_digest, TherapistSecrets().decrypt_replay(
        row.response_ciphertext, row.response_encryption_key_id, scope, operation, key
    )


async def _record(repo: TherapistQualificationRepository, *, scope: str, operation: str, key: str, request_digest: str, response: dict, now: datetime) -> None:
    secret = TherapistSecrets()
    await repo.add(TherapistWorkflowIdempotencyModel(
        actor_scope=scope,
        operation=operation,
        idempotency_key=key,
        request_digest=request_digest,
        response_ciphertext=secret.encrypt_replay(response, scope, operation, key),
        response_encryption_key_id=secret.replay_key_id,
        postimage_digest=_digest(response),
        created_at=now,
    ))


async def _commit(session, *, confirm=None) -> None:
    try:
        await session.commit()
    except asyncio.CancelledError:
        await _rollback(session)
        raise
    except Exception:
        await _rollback(session)
        if confirm is not None and await confirm() == COMMITTED:
            return
        raise HTTPException(503, "COMMIT_OUTCOME_UNKNOWN") from None


def _safe_snapshot_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _safe_snapshot_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_snapshot_value(item) for item in value]
    return value


async def _mutation_postimage_snapshot(
    session,
    *,
    scope: str,
    operation: str,
    key: str,
    response: dict,
    confirmation_context: dict[str, object] | None = None,
) -> dict[str, object]:
    """Read every durable mutation boundary without exposing secret payloads."""
    from sqlalchemy import text

    expected_postimage = _digest(response)
    receipt = (await session.execute(text(
        "SELECT request_digest,postimage_digest,created_at "
        "FROM public.therapist_workflow_idempotency "
        "WHERE actor_scope=:scope AND operation=:operation AND idempotency_key=:key"
    ), {"scope": scope, "operation": operation, "key": key})).mappings().one_or_none()

    context = confirmation_context or {}
    invitation_id = context.get("invitation_id") or response.get("invitation_id")
    profile_value = response.get("profile") if isinstance(response.get("profile"), dict) else response
    therapist_id = profile_value.get("therapist_id")
    revision_id = response.get("revision_id")
    review_item_id = response.get("review_item_id")
    if isinstance(response.get("review_item"), dict):
        review_item_id = response["review_item"].get("review_item_id")
    decision_id = context.get("decision_id") or response.get("decision_id")
    status_decision_id = context.get("status_decision_id")

    invitation = None
    if invitation_id is not None:
        invitation = (await session.execute(text(
            "SELECT invitation_id,tenant_id,status,failed_attempts,expires_at,"
            "issued_at,activated_at,revoked_at,version "
            "FROM public.therapist_invitation WHERE invitation_id=:value"
        ), {"value": str(invitation_id)})).mappings().one_or_none()

    profile = None
    if therapist_id is not None:
        profile = (await session.execute(text(
            "SELECT therapist_id,user_id,tenant_id,invitation_id,status,display_name,practice_summary,"
            "service_tags,current_revision_no,current_qualification_version_id,"
            "qualification_valid_until,suspension_reason_code,active_case_count,capacity_limit,"
            "activated_at,submitted_at,reviewed_at,suspended_at,resumed_at,"
            "exited_at,created_at,updated_at,version "
            "FROM public.therapist_profile WHERE therapist_id=:value"
        ), {"value": str(therapist_id)})).mappings().one_or_none()

    review_item = None
    if review_item_id is not None:
        review_item = (await session.execute(text(
            "SELECT review_item_id,therapist_id,revision_id,qualification_version_id,"
            "review_kind,status,reviewer_user_id,previous_review_item_id,"
            "created_at,claimed_at,decided_at,version "
            "FROM public.therapist_review_item WHERE review_item_id=:value"
        ), {"value": str(review_item_id)})).mappings().one_or_none()
        if revision_id is None and review_item is not None:
            revision_id = review_item["revision_id"]

    revision = None
    qualification = ()
    if revision_id is not None:
        revision = (await session.execute(text(
            "SELECT revision_id,therapist_id,revision_no,input_digest,created_at "
            "FROM public.therapist_profile_revision WHERE revision_id=:value"
        ), {"value": str(revision_id)})).mappings().one_or_none()
        qualification = tuple((await session.execute(text(
            "SELECT q.qualification_version_id,q.therapist_id,q.profile_revision_id,"
            "q.previous_version_id,q.qualification_type,q.certificate_no_masked,"
            "q.issuer_name,q.valid_from,q.valid_until,q.attachment_count,q.version_no,"
            "q.created_at,r.position,count(a.private_file_id) AS persisted_attachment_count "
            "FROM public.therapist_profile_revision_qualification r "
            "JOIN public.therapist_qualification_version q "
            "ON q.qualification_version_id=r.qualification_version_id "
            "LEFT JOIN public.therapist_qualification_attachment a "
            "ON a.qualification_version_id=q.qualification_version_id "
            "WHERE r.revision_id=:value "
            "GROUP BY q.qualification_version_id,r.position"
        ), {"value": str(revision_id)})).mappings())

    created_at = receipt["created_at"] if receipt is not None else None
    audit = ()
    outbox = ()
    decisions = ()
    status_decisions = ()
    readiness = None
    evidence = ()
    if created_at is not None:
        audit = tuple((await session.execute(text(
            "SELECT actor_scope,action,object_id,result,reason_code,request_id,"
            "preimage_digest,postimage_digest,created_at "
            "FROM public.therapist_workflow_audit "
            "WHERE actor_scope=:scope AND postimage_digest=:postimage "
            "AND created_at=:created_at ORDER BY action,object_id"
        ), {"scope": scope, "postimage": expected_postimage, "created_at": created_at})).mappings())

        candidates = tuple(dict.fromkeys(
            str(value) for value in (invitation_id, therapist_id, revision_id, review_item_id)
            if value is not None
        ))
        outbox_rows = []
        for aggregate_id in candidates:
            outbox_rows.extend((await session.execute(text(
                "SELECT event_id,event_type,aggregate_id,tenant_id,payload_digest,status,"
                "attempts,processing_at,lease_owner,delivered_at,failed_at,created_at,version "
                "FROM public.therapist_workflow_outbox "
                "WHERE aggregate_id=:aggregate_id AND created_at=:created_at"
            ), {"aggregate_id": aggregate_id, "created_at": created_at})).mappings())
        outbox = tuple(sorted(outbox_rows, key=lambda row: str(row["event_id"])))

        if decision_id is not None and operation == "REVIEW_DECISION":
            decisions = tuple((await session.execute(text(
                "SELECT decision_id,review_item_id,therapist_id,revision_id,"
                "reviewer_user_id,decision,qualification_outcomes,reason_code,"
                "correction_fields,request_digest,created_at "
                "FROM public.therapist_review_decision "
                "WHERE decision_id=:decision_id AND created_at=:created_at"
            ), {"decision_id": str(decision_id), "created_at": created_at})).mappings())

        if status_decision_id is not None:
            status_decisions = tuple((await session.execute(text(
                "SELECT status_decision_id,therapist_id,actor_kind,actor_user_id,"
                "worker_identity,decision,reason_code,expected_profile_version,"
                "request_digest,created_at FROM public.therapist_status_decision "
                "WHERE status_decision_id=:status_decision_id AND created_at=:created_at"
            ), {
                "status_decision_id": str(status_decision_id),
                "created_at": created_at,
            })).mappings())

        tenant_id = None
        if profile is not None:
            tenant_id = profile["tenant_id"]
        elif invitation is not None:
            tenant_id = invitation["tenant_id"]
        readiness_operations = {"REVIEW_DECISION", "SUSPENDED", "RESUMED", "EXITED"}
        if tenant_id is not None and operation in readiness_operations:
            readiness = (await session.execute(text(
                "SELECT tenant_id,readiness_status,reason_codes,qualified_therapist_count,"
                "computed_at,evidence_version,input_digest,result_digest,source_versions,"
                "next_expiry_at,version FROM public.institution_service_readiness "
                "WHERE tenant_id=:tenant_id AND computed_at=:created_at"
            ), {"tenant_id": tenant_id, "created_at": created_at})).mappings().one_or_none()
            evidence = tuple((await session.execute(text(
                "SELECT tenant_id,evidence_version,readiness_status,reason_codes,"
                "qualified_therapist_count,computed_at,input_digest,result_digest "
                "FROM public.readiness_evidence "
                "WHERE tenant_id=:tenant_id AND computed_at=:created_at "
                "ORDER BY evidence_version"
            ), {"tenant_id": tenant_id, "created_at": created_at})).mappings())

    return _safe_snapshot_value({
        "receipt": dict(receipt) if receipt is not None else None,
        "aggregate": {
            "invitation": dict(invitation) if invitation is not None else None,
            "profile": dict(profile) if profile is not None else None,
        },
        "revision": dict(revision) if revision is not None else None,
        "qualification": [dict(row) for row in qualification],
        "review_item": dict(review_item) if review_item is not None else None,
        "review_decision": [dict(row) for row in decisions],
        "status_decision": [dict(row) for row in status_decisions],
        "audit": [dict(row) for row in audit],
        "outbox": [dict(row) for row in outbox],
        "readiness": dict(readiness) if readiness is not None else None,
        "readiness_evidence": [dict(row) for row in evidence],
    })


async def _confirm_mutation_outcome(
    confirmation,
    *,
    expected: dict[str, object],
    scope: str,
    operation: str,
    key: str,
    response: dict,
    confirmation_context: dict[str, object] | None = None,
) -> str:
    actual = await _mutation_postimage_snapshot(
        confirmation,
        scope=scope,
        operation=operation,
        key=key,
        response=response,
        confirmation_context=confirmation_context,
    )
    receipt = actual["receipt"]
    if receipt is not None and _digest(actual) == _digest(expected):
        return COMMITTED
    has_commit_footprint = bool(
        receipt
        or actual["review_decision"]
        or actual["status_decision"]
        or actual["audit"]
        or actual["outbox"]
        or actual["readiness_evidence"]
    )
    aggregate = actual["aggregate"]
    expected_aggregate = expected["aggregate"]
    if not has_commit_footprint:
        actual_invitation = aggregate["invitation"]
        expected_invitation = expected_aggregate["invitation"]
        actual_profile = aggregate["profile"]
        expected_profile = expected_aggregate["profile"]
        if expected_invitation is not None and (
            actual_invitation is None
            or actual_invitation.get("version") == expected_invitation.get("version") - 1
        ):
            return NOT_COMMITTED
        if expected_profile is not None and (
            actual_profile is None
            or actual_profile.get("version") == expected_profile.get("version") - 1
        ):
            return NOT_COMMITTED
    return UNKNOWN


async def _commit_receipt(
    session,
    *,
    scope: str,
    operation: str,
    key: str,
    request_digest: str,
    response: dict,
    confirmation_context: dict[str, object] | None = None,
    precommit_check=None,
) -> None:
    expected = await _mutation_postimage_snapshot(
        session,
        scope=scope,
        operation=operation,
        key=key,
        response=response,
        confirmation_context=confirmation_context,
    )
    receipt = expected["receipt"]
    if (
        not isinstance(receipt, dict)
        or not hmac.compare_digest(str(receipt.get("request_digest")), request_digest)
        or not hmac.compare_digest(str(receipt.get("postimage_digest")), _digest(response))
        or not expected["audit"]
    ):
        raise HTTPException(503, "DEPENDENCY_UNAVAILABLE")

    async def confirm() -> str:
        from app.core.database import get_slice2_session_factory

        confirmation_kind = (
            "review_writer"
            if operation in {"REVIEW_DECISION", "SUSPENDED", "RESUMED", "EXITED"}
            else "onboarding_writer"
        )
        factory = get_slice2_session_factory(confirmation_kind)
        async with factory() as confirmation:
            outcome = await _confirm_mutation_outcome(
                confirmation,
                expected=expected,
                scope=scope,
                operation=operation,
                key=key,
                response=response,
                confirmation_context=confirmation_context,
            )
            await confirmation.rollback()
        return outcome

    if precommit_check is not None:
        await precommit_check()
    await _commit(session, confirm=confirm)


async def _commit_invitation_failure(
    session,
    *,
    invitation_id: str,
    failed_attempts: int,
    version: int,
) -> None:
    async def confirm() -> bool:
        from app.core.database import get_slice2_session_factory
        from sqlalchemy import text

        factory = get_slice2_session_factory("reader")
        async with factory() as confirmation:
            row = (await confirmation.execute(text(
                "SELECT status,failed_attempts,version "
                "FROM public.therapist_invitation WHERE invitation_id=:invitation_id"
            ), {"invitation_id": invitation_id})).one_or_none()
            await confirmation.rollback()
        return row == ("INVITED", failed_attempts, version)

    await _commit(session, confirm=confirm)


async def _commit_readiness(
    session,
    *,
    tenant_id: int,
    trigger_event_id: str,
    evidence_version: int,
    input_digest: str,
    result_digest: str,
) -> None:
    async def confirm() -> bool:
        from app.core.database import get_slice2_session_factory
        from sqlalchemy import text

        factory = get_slice2_session_factory("reader")
        async with factory() as confirmation:
            readiness = (await confirmation.execute(
                text(
                    "SELECT evidence_version,input_digest,result_digest "
                    "FROM public.institution_service_readiness WHERE tenant_id=:tenant_id"
                ),
                {"tenant_id": tenant_id},
            )).one_or_none()
            evidence = (await confirmation.execute(
                text(
                    "SELECT tenant_id,evidence_version,input_digest,result_digest "
                    "FROM public.readiness_evidence WHERE trigger_event_id=:trigger_event_id"
                ),
                {"trigger_event_id": trigger_event_id},
            )).one_or_none()
            await confirmation.rollback()
        expected = (tenant_id, evidence_version, input_digest, result_digest)
        return bool(
            readiness == expected[1:]
            and evidence == expected
        )

    await _commit(session, confirm=confirm)


def _mask_phone(phone: str) -> str:
    return "*******" + phone[-4:]


def _mask_certificate(value: str) -> str:
    return "****" + value[-4:]


def _outbox(event_type: str, aggregate_id: str, tenant_id: int, now: datetime) -> TherapistWorkflowOutboxModel:
    payload = {"v": 1, "event_type": event_type, "aggregate_id": aggregate_id}
    return TherapistWorkflowOutboxModel(
        event_id=str(Uuid7Generator().generate()),
        event_type=event_type,
        aggregate_id=aggregate_id,
        tenant_id=tenant_id,
        payload=payload,
        payload_digest=_digest(payload),
        status="PENDING",
        attempts=0,
        created_at=now,
        version=1,
    )


def _audit(scope: str, action: str, object_id: str, request_id: str, preimage: object, postimage: object, now: datetime) -> TherapistWorkflowAuditModel:
    return TherapistWorkflowAuditModel(
        actor_scope=scope,
        action=action,
        object_id=object_id,
        result="SUCCESS",
        reason_code=None,
        request_id=request_id,
        preimage_digest=_digest(preimage),
        postimage_digest=_digest(postimage),
        created_at=now,
    )


async def require_institution_actor(session, actor: CurrentUser) -> None:
    if actor.role not in {"org_admin", "org_operator"} or actor.tenant_id is None:
        raise HTTPException(403, "ACTOR_CURRENTNESS_FORBIDDEN")
    row = (await session.execute(
        __import__("sqlalchemy").text(
            "SELECT u.role,u.status,u.tenant_id,t.status FROM public.\"user\" u "
            "JOIN public.tenant t ON t.id=u.tenant_id WHERE u.id=:id FOR SHARE OF u,t"
        ), {"id": actor.id}
    )).one_or_none()
    if row is None or row[0] != actor.role or row[1] != "active" or row[2] != actor.tenant_id or row[3] != "active":
        raise HTTPException(403, "ACTOR_CURRENTNESS_FORBIDDEN")


async def require_therapist(session, actor: CurrentUser) -> None:
    if actor.role != "therapist" or actor.tenant_id is None or actor.org_id is not None:
        raise HTTPException(403, "ACTOR_CURRENTNESS_FORBIDDEN")
    row = (await session.execute(
        __import__("sqlalchemy").text(
            "SELECT u.role,u.status,u.tenant_id,t.status,p.tenant_id,p.therapist_status,p.totp_enabled "
            "FROM public.\"user\" u JOIN public.tenant t ON t.id=u.tenant_id "
            "JOIN LATERAL public.therapist_totp_for_login_v1(u.id) p ON TRUE "
            "WHERE u.id=:id FOR SHARE OF u,t"
        ), {"id": actor.id}
    )).one_or_none()
    allowed = {"ACTIVATED", "DRAFT", "SUBMITTED", "UNDER_REVIEW", "NEEDS_CORRECTION", "RESUBMITTED", "APPROVED_ACTIVE", "SUSPENDED"}
    if (
        row is None
        or row[0] != "therapist"
        or row[1] != "active"
        or row[2] != actor.tenant_id
        or row[3] != "active"
        or row[4] != actor.tenant_id
        or row[5] not in allowed
        or row[6] is not True
    ):
        raise HTTPException(403, "ACTOR_CURRENTNESS_FORBIDDEN")


async def require_reviewer(session, actor: CurrentUser) -> None:
    if actor.role != "super_admin" or actor.tenant_id is not None or actor.org_id is not None:
        raise HTTPException(403, "REVIEWER_CURRENTNESS_FORBIDDEN")
    row = (await session.execute(
        __import__("sqlalchemy").text("SELECT role,status,tenant_id FROM public.\"user\" WHERE id=:id FOR SHARE"),
        {"id": actor.id},
    )).one_or_none()
    if row != ("super_admin", "active", None):
        raise HTTPException(403, "REVIEWER_CURRENTNESS_FORBIDDEN")


async def create_invitation(session, actor: CurrentUser, payload: TherapistInvitationCreate, request_id: str, idempotency_key: str, *, tenant_public_id: str, precommit_check=None) -> dict:
    now = utcnow()
    scope = f"tenant:{actor.tenant_id}:actor:{actor.id}"
    repo = TherapistQualificationRepository(session)
    request_digest, replay = await _replay(repo, scope=scope, operation="CREATE_INVITATION", key=idempotency_key, request=payload)
    if replay is not None:
        return replay
    if actor.tenant_id is None:
        raise HTTPException(403, "ACTOR_CURRENTNESS_FORBIDDEN")
    await repo.lock_tenant(actor.tenant_id)
    box = TherapistSecrets()
    phone = payload.phone
    candidates = tuple((key_id, box.digest_pii(phone, field="invitation-phone", key_id=key_id)) for key_id in box.digest_keys)
    if await repo.invitation_by_phone_candidates(actor.tenant_id, candidates):
        raise HTTPException(409, "THERAPIST_INVITATION_CONFLICT")
    code = f"{secrets.randbelow(1_000_000):06d}"
    invitation_id = str(Uuid7Generator().generate())
    row = TherapistInvitationModel(
        invitation_id=invitation_id,
        tenant_id=actor.tenant_id,
        phone_ciphertext=box.encrypt_pii(
            phone,
            field="invitation-phone",
            tenant_public_id=tenant_public_id,
            object_id=invitation_id,
        ),
        phone_encryption_key_id=box.pii_key_id,
        phone_digest=box.digest_pii(phone, field="invitation-phone"),
        phone_digest_key_id=box.digest_key_id,
        phone_masked=_mask_phone(phone),
        code_digest=box.code_digest(code),
        code_digest_key_id=box.code_key_id,
        expires_at=now + timedelta(minutes=payload.expires_in_minutes),
        status="INVITED",
        failed_attempts=0,
        issued_by=actor.id,
        issued_at=now,
        version=1,
    )
    response = {
        "invitation_id": invitation_id,
        "masked_phone": row.phone_masked,
        "short_code": code,
        "status": row.status,
        "expires_at": row.expires_at.isoformat(),
        "issued_at": now.isoformat(),
        "activated_at": None,
        "revoked_at": None,
        "version": 1,
    }
    await repo.add_invitation(row)
    await repo.add(_outbox("THERAPIST_INVITED", invitation_id, actor.tenant_id, now))
    await repo.add_audit(_audit(scope, "THERAPIST_INVITED", invitation_id, request_id, payload, response, now))
    await _record(repo, scope=scope, operation="CREATE_INVITATION", key=idempotency_key, request_digest=request_digest, response=response, now=now)
    await _commit_receipt(session, scope=scope, operation="CREATE_INVITATION", key=idempotency_key, request_digest=request_digest, response=response, precommit_check=precommit_check)
    return response


async def revoke_invitation(session, actor: CurrentUser, invitation_id: str, payload: TherapistInvitationRevoke, request_id: str, idempotency_key: str, *, precommit_check=None) -> dict:
    now = utcnow()
    scope = f"actor:{actor.id}:invitation:{invitation_id}"
    repo = TherapistQualificationRepository(session)
    request_digest, replay = await _replay(repo, scope=scope, operation="REVOKE_INVITATION", key=idempotency_key, request=payload)
    if replay is not None:
        return replay
    row = await repo.invitation_for_update(invitation_id)
    if row is None or row.tenant_id != actor.tenant_id:
        raise HTTPException(404, "THERAPIST_INVITATION_NOT_FOUND")
    if row.version != payload.expected_version:
        raise HTTPException(409, "THERAPIST_VERSION_CONFLICT")
    if row.status != "INVITED":
        raise HTTPException(409, "THERAPIST_INVITATION_STATE_CONFLICT")
    row.status, row.revoked_at, row.version = "REVOKED", now, row.version + 1
    response = {"invitation_id": row.invitation_id, "masked_phone": row.phone_masked, "status": row.status, "expires_at": row.expires_at.isoformat(), "issued_at": row.issued_at.isoformat(), "activated_at": None, "revoked_at": now.isoformat(), "version": row.version}
    await repo.add(_outbox("THERAPIST_INVITATION_REVOKED", invitation_id, row.tenant_id, now))
    await repo.add_audit(_audit(scope, "THERAPIST_INVITATION_REVOKED", invitation_id, request_id, payload, response, now))
    await _record(repo, scope=scope, operation="REVOKE_INVITATION", key=idempotency_key, request_digest=request_digest, response=response, now=now)
    await _commit_receipt(session, scope=scope, operation="REVOKE_INVITATION", key=idempotency_key, request_digest=request_digest, response=response, precommit_check=precommit_check)
    return response


async def activate(session, payload: TherapistActivate, request_id: str, idempotency_key: str, *, tenant_public_id: str, precommit_check=None) -> dict:
    now = utcnow()
    invitation_id = str(payload.invitation_id)
    scope = f"invitation:{invitation_id}"
    repo = TherapistQualificationRepository(session)
    request_digest, replay = await _replay(repo, scope=scope, operation="ACTIVATE", key=idempotency_key, request=payload)
    if replay is not None:
        return replay
    invitation = await repo.invitation_for_update(invitation_id)
    if invitation is None or invitation.status != "INVITED":
        raise HTTPException(401, "THERAPIST_INVITATION_INVALID")
    if invitation.failed_attempts >= 5:
        raise HTTPException(401, "THERAPIST_ACTIVATION_ATTEMPTS_EXHAUSTED")
    box = TherapistSecrets()
    phone_ok = any(hmac.compare_digest(invitation.phone_digest, box.digest_pii(payload.phone, field="invitation-phone", key_id=key_id)) for key_id in box.digest_keys if key_id == invitation.phone_digest_key_id)
    code_ok = any(hmac.compare_digest(invitation.code_digest, box.code_digest(payload.short_code, key_id=key_id)) for key_id in box.code_keys if key_id == invitation.code_digest_key_id)
    from app.modules.institution_onboarding.domain import verify_totp
    totp_ok = verify_totp(payload.totp_secret, payload.totp_code, at=now)
    if now >= invitation.expires_at or not phone_ok or not code_ok or not totp_ok:
        invitation.failed_attempts = min(5, invitation.failed_attempts + 1)
        invitation.version += 1
        await _commit_invitation_failure(
            session,
            invitation_id=invitation_id,
            failed_attempts=invitation.failed_attempts,
            version=invitation.version,
        )
        raise HTTPException(401, "THERAPIST_INVITATION_INVALID")
    user_id = await repo.add_therapist_user(
        phone=payload.phone,
        password_hash=hash_password(payload.password),
        tenant_id=invitation.tenant_id,
    )
    therapist_id = str(Uuid7Generator().generate())
    profile = TherapistProfileModel(
        therapist_id=therapist_id, user_id=user_id, tenant_id=invitation.tenant_id,
        invitation_id=invitation_id, status="ACTIVATED", capacity_limit=30,
        active_case_count=0, current_revision_no=0,
        totp_secret_ciphertext=box.encrypt_totp(payload.totp_secret, tenant_public_id, therapist_id),
        totp_encryption_key_id=box.totp_key_id, totp_enabled=True,
        activated_at=now, created_at=now, updated_at=now, version=1,
    )
    invitation.status, invitation.activated_at, invitation.version = "ACTIVATED", now, invitation.version + 1
    response = {"therapist_id": therapist_id, "status": "ACTIVATED", "revision_id": None, "revision_no": None, "review_item_id": None, "version": 1}
    await repo.add_profile(profile)
    await repo.add(_outbox("THERAPIST_ACTIVATED", therapist_id, invitation.tenant_id, now))
    await repo.add_audit(_audit(scope, "THERAPIST_ACTIVATED", therapist_id, request_id, payload, response, now))
    await _record(repo, scope=scope, operation="ACTIVATE", key=idempotency_key, request_digest=request_digest, response=response, now=now)
    await _commit_receipt(
        session, scope=scope, operation="ACTIVATE", key=idempotency_key,
        request_digest=request_digest, response=response,
        confirmation_context={"invitation_id": invitation_id},
        precommit_check=precommit_check,
    )
    return response


async def save_profile(session, actor: CurrentUser, payload: TherapistProfileDraft, request_id: str, idempotency_key: str, *, tenant_public_id: str, precommit_check=None) -> dict:
    now = utcnow()
    scope = f"therapist-user:{actor.id}"
    repo = TherapistQualificationRepository(session)
    request_digest, replay = await _replay(repo, scope=scope, operation="SAVE_PROFILE", key=idempotency_key, request=payload)
    if replay is not None:
        return replay
    profile = await repo.current_profile_for_user(actor.id, for_update=True)
    if profile is None:
        raise HTTPException(404, "THERAPIST_NOT_FOUND")
    if profile.version != payload.expected_version:
        raise HTTPException(409, "THERAPIST_VERSION_CONFLICT")
    if profile.status not in {"ACTIVATED", "DRAFT"}:
        raise HTTPException(409, "THERAPIST_STATE_CONFLICT")
    box = TherapistSecrets()
    profile.real_name_ciphertext = box.encrypt_pii(
        payload.real_name,
        field="profile-real-name",
        tenant_public_id=tenant_public_id,
        object_id=profile.therapist_id,
    )
    profile.real_name_encryption_key_id = box.pii_key_id
    profile.real_name_digest = box.digest_pii(payload.real_name, field="profile-real-name")
    profile.real_name_digest_key_id = box.digest_key_id
    profile.display_name = payload.display_name
    profile.practice_summary = payload.practice_summary
    profile.service_tags = list(payload.service_tags)
    profile.status = "DRAFT"
    profile.updated_at = now
    profile.version += 1
    response = {"therapist_id": profile.therapist_id, "status": profile.status, "revision_id": None, "revision_no": None, "review_item_id": None, "version": profile.version}
    await repo.add_audit(_audit(scope, "THERAPIST_DRAFT_SAVED", profile.therapist_id, request_id, payload, response, now))
    await _record(repo, scope=scope, operation="SAVE_PROFILE", key=idempotency_key, request_digest=request_digest, response=response, now=now)
    await _commit_receipt(session, scope=scope, operation="SAVE_PROFILE", key=idempotency_key, request_digest=request_digest, response=response, precommit_check=precommit_check)
    return response


async def _create_revision(repo: TherapistQualificationRepository, profile: TherapistProfileModel, qualification: QualificationInput, now: datetime, *, tenant_public_id: str, previous_version_id: str | None = None):
    files = await repo.lock_clean_files(file_ids=tuple(str(value) for value in qualification.attachment_file_ids), owner_user_id=profile.user_id)
    if len(files) != len(qualification.attachment_file_ids) or any(file.bound_application_id is not None for file in files):
        raise HTTPException(409, "PRIVATE_FILE_NOT_CLEAN")
    box = TherapistSecrets()
    revision_id = str(Uuid7Generator().generate())
    qualification_id = str(Uuid7Generator().generate())
    revision_no = profile.current_revision_no + 1
    version_no = 1
    current = await repo.current_qualification(profile.therapist_id)
    if current is not None:
        version_no = current.version_no + 1
    candidates = tuple(
        (
            key_id,
            box.digest_pii(
                qualification.certificate_no,
                field="qualification-certificate",
                key_id=key_id,
            ),
        )
        for key_id in box.digest_keys
    )
    if await repo.certificate_digest_candidates(profile.therapist_id, candidates):
        raise HTTPException(409, "THERAPIST_QUALIFICATION_CONFLICT")
    snapshot = {"v": 1, "real_name_envelope": {"ciphertext": base64.b64encode(profile.real_name_ciphertext or b"").decode(), "key_id": profile.real_name_encryption_key_id}, "real_name_digest": {"digest": profile.real_name_digest, "key_id": profile.real_name_digest_key_id}, "display_name": profile.display_name, "practice_summary": profile.practice_summary, "service_tags": profile.service_tags}
    revision = TherapistProfileRevisionModel(revision_id=revision_id, therapist_id=profile.therapist_id, revision_no=revision_no, profile_snapshot=snapshot, input_digest=_digest(snapshot), created_at=now)
    q = TherapistQualificationVersionModel(
        qualification_version_id=qualification_id, therapist_id=profile.therapist_id,
        profile_revision_id=revision_id, previous_version_id=previous_version_id,
        qualification_type=qualification.qualification_type,
        certificate_no_ciphertext=box.encrypt_pii(
            qualification.certificate_no,
            field="qualification-certificate",
            tenant_public_id=tenant_public_id,
            object_id=qualification_id,
        ),
        certificate_encryption_key_id=box.pii_key_id,
        certificate_no_digest=box.digest_pii(
            qualification.certificate_no,
            field="qualification-certificate",
        ),
        certificate_digest_key_id=box.digest_key_id,
        certificate_no_masked=_mask_certificate(qualification.certificate_no),
        issuer_name=qualification.issuer_name, valid_from=qualification.valid_from,
        valid_until=qualification.valid_until, attachment_count=len(files), version_no=version_no,
        created_at=now,
    )
    link = TherapistRevisionQualificationModel(therapist_id=profile.therapist_id, revision_id=revision_id, qualification_version_id=qualification_id, position=1)
    attachments = [TherapistQualificationAttachmentModel(qualification_version_id=qualification_id, slot=index, private_file_id=file.file_id, created_at=now) for index, file in enumerate(files, 1)]
    await repo.add_revision_bundle(revision, q, link, attachments)
    return revision, q


async def _reuse_qualification_revision(
    repo: TherapistQualificationRepository,
    profile: TherapistProfileModel,
    qualification: QualificationInput,
    current: TherapistQualificationVersionModel,
    now: datetime,
    *,
    tenant_public_id: str,
):
    box = TherapistSecrets()
    stored_certificate = box.decrypt_pii(
        current.certificate_no_ciphertext,
        current.certificate_encryption_key_id,
        field="qualification-certificate",
        tenant_public_id=tenant_public_id,
        object_id=current.qualification_version_id,
    )
    stored_files = await repo.qualification_attachment_ids(current.qualification_version_id)
    supplied_files = tuple(str(value) for value in qualification.attachment_file_ids)
    if (
        qualification.qualification_type != current.qualification_type
        or qualification.certificate_no != stored_certificate
        or qualification.issuer_name != current.issuer_name
        or qualification.valid_from != current.valid_from
        or qualification.valid_until != current.valid_until
        or supplied_files != stored_files
    ):
        raise HTTPException(409, "THERAPIST_CORRECTION_SCOPE_CONFLICT")
    revision_id = str(Uuid7Generator().generate())
    revision_no = profile.current_revision_no + 1
    snapshot = {
        "v": 1,
        "real_name_envelope": {
            "ciphertext": base64.b64encode(profile.real_name_ciphertext or b"").decode(),
            "key_id": profile.real_name_encryption_key_id,
        },
        "real_name_digest": {
            "digest": profile.real_name_digest,
            "key_id": profile.real_name_digest_key_id,
        },
        "display_name": profile.display_name,
        "practice_summary": profile.practice_summary,
        "service_tags": profile.service_tags,
    }
    revision = TherapistProfileRevisionModel(
        revision_id=revision_id,
        therapist_id=profile.therapist_id,
        revision_no=revision_no,
        profile_snapshot=snapshot,
        input_digest=_digest(snapshot),
        created_at=now,
    )
    link = TherapistRevisionQualificationModel(
        therapist_id=profile.therapist_id,
        revision_id=revision_id,
        qualification_version_id=current.qualification_version_id,
        position=1,
    )
    await repo.add_all((revision, link))
    return revision, current


async def submit(session, actor: CurrentUser, payload: TherapistSubmit, request_id: str, idempotency_key: str, *, tenant_public_id: str, precommit_check=None) -> dict:
    now = utcnow()
    scope = f"therapist-user:{actor.id}"
    repo = TherapistQualificationRepository(session)
    request_digest, replay = await _replay(repo, scope=scope, operation="SUBMIT", key=idempotency_key, request=payload)
    if replay is not None:
        return replay
    profile = await repo.current_profile_for_user(actor.id, for_update=True)
    if profile is None or profile.status not in {"ACTIVATED", "DRAFT"}:
        raise HTTPException(409, "THERAPIST_SUBMISSION_INCOMPLETE")
    if profile.version != payload.expected_version or not all((profile.real_name_ciphertext, profile.display_name, profile.service_tags is not None)):
        raise HTTPException(409, "THERAPIST_SUBMISSION_INCOMPLETE")
    revision, qualification = await _create_revision(
        repo, profile, payload.qualification, now, tenant_public_id=tenant_public_id
    )
    review_item_id = str(Uuid7Generator().generate())
    item = TherapistReviewItemModel(review_item_id=review_item_id, therapist_id=profile.therapist_id, revision_id=revision.revision_id, qualification_version_id=None, review_kind="INITIAL", status="QUEUED", created_at=now, version=1)
    profile.status, profile.current_revision_no, profile.submitted_at, profile.updated_at, profile.version = "SUBMITTED", revision.revision_no, now, now, profile.version + 1
    response = {"therapist_id": profile.therapist_id, "status": profile.status, "revision_id": revision.revision_id, "revision_no": revision.revision_no, "review_item_id": review_item_id, "version": profile.version}
    await repo.add_all((item, _outbox("THERAPIST_SUBMITTED", review_item_id, profile.tenant_id, now)))
    await repo.add_audit(_audit(scope, "THERAPIST_SUBMITTED", profile.therapist_id, request_id, payload, response, now))
    await _record(repo, scope=scope, operation="SUBMIT", key=idempotency_key, request_digest=request_digest, response=response, now=now)
    await _commit_receipt(session, scope=scope, operation="SUBMIT", key=idempotency_key, request_digest=request_digest, response=response, precommit_check=precommit_check)
    return response


async def resubmit(session, actor: CurrentUser, payload: TherapistResubmit, request_id: str, idempotency_key: str, *, tenant_public_id: str, precommit_check=None) -> dict:
    now = utcnow()
    scope = f"therapist-user:{actor.id}:decision:{payload.decision_id}"
    repo = TherapistQualificationRepository(session)
    request_digest, replay = await _replay(repo, scope=scope, operation="RESUBMIT", key=idempotency_key, request=payload)
    if replay is not None:
        return replay
    profile = await repo.current_profile_for_user(actor.id, for_update=True)
    if profile is None or profile.status != "NEEDS_CORRECTION" or profile.version != payload.expected_version:
        raise HTTPException(409, "THERAPIST_CORRECTION_SCOPE_CONFLICT")
    decision = (await session.execute(
        __import__("sqlalchemy").select(TherapistReviewDecisionModel)
        .where(TherapistReviewDecisionModel.decision_id == str(payload.decision_id))
        .with_for_update()
    )).scalar_one_or_none()
    if decision is None or decision.therapist_id != profile.therapist_id or decision.decision != "NEEDS_CORRECTION":
        raise HTTPException(409, "THERAPIST_CORRECTION_SCOPE_CONFLICT")
    allowed = set(decision.correction_fields or ())
    profile_changes = payload.profile_changes.model_dump(exclude_unset=True)
    requested = set(profile_changes)
    allowed_profile = {value for value in allowed if not value.startswith("qualification:")}
    if not requested.issubset(allowed_profile) or (not requested and not any(value.startswith("qualification:") for value in allowed)):
        raise HTTPException(409, "THERAPIST_CORRECTION_SCOPE_CONFLICT")
    current_q = await repo.qualification_for_revision(profile.therapist_id, decision.revision_id)
    qualification_target = f"qualification:{current_q.qualification_version_id}" if current_q else None
    if current_q is None:
        raise HTTPException(409, "THERAPIST_CORRECTION_SCOPE_CONFLICT")
    box = TherapistSecrets()
    for field, value in profile_changes.items():
        if field == "real_name":
            profile.real_name_ciphertext = box.encrypt_pii(
                value,
                field="profile-real-name",
                tenant_public_id=tenant_public_id,
                object_id=profile.therapist_id,
            )
            profile.real_name_encryption_key_id = box.pii_key_id
            profile.real_name_digest = box.digest_pii(value, field="profile-real-name")
            profile.real_name_digest_key_id = box.digest_key_id
        elif field == "service_tags":
            tags = tuple(sorted(str(item) for item in value)) if isinstance(value, (list, tuple)) else ()
            if not tags or not set(tags).issubset({"HYPERTENSION","GLUCOSE_METABOLISM","DYSLIPIDEMIA","OBESITY"}):
                raise HTTPException(409, "THERAPIST_CORRECTION_SCOPE_CONFLICT")
            profile.service_tags = list(tags)
        elif field in {"display_name", "practice_summary"}:
            setattr(profile, field, str(value))
    if qualification_target in allowed:
        revision, qualification = await _create_revision(
            repo, profile, payload.qualification, now,
            tenant_public_id=tenant_public_id,
            previous_version_id=current_q.qualification_version_id,
        )
    else:
        revision, qualification = await _reuse_qualification_revision(
            repo, profile, payload.qualification, current_q, now,
            tenant_public_id=tenant_public_id,
        )
    review_item_id = str(Uuid7Generator().generate())
    item = TherapistReviewItemModel(
        review_item_id=review_item_id, therapist_id=profile.therapist_id,
        revision_id=revision.revision_id,
        qualification_version_id=None,
        review_kind="INITIAL", status="QUEUED",
        previous_review_item_id=decision.review_item_id,
        created_at=now, version=1,
    )
    profile.status = "RESUBMITTED"
    profile.current_revision_no = revision.revision_no
    profile.submitted_at = now
    profile.reviewed_at = None
    profile.updated_at = now
    profile.version += 1
    response = {"therapist_id": profile.therapist_id, "status": profile.status, "revision_id": revision.revision_id, "revision_no": revision.revision_no, "review_item_id": review_item_id, "version": profile.version}
    await repo.add_all((item, _outbox("THERAPIST_RESUBMITTED", review_item_id, profile.tenant_id, now)))
    await repo.add_audit(_audit(scope, "THERAPIST_RESUBMITTED", profile.therapist_id, request_id, payload, response, now))
    await _record(repo, scope=scope, operation="RESUBMIT", key=idempotency_key, request_digest=request_digest, response=response, now=now)
    await _commit_receipt(session, scope=scope, operation="RESUBMIT", key=idempotency_key, request_digest=request_digest, response=response, precommit_check=precommit_check)
    return response


async def review_decision(session, actor: CurrentUser, therapist_id: str, payload: TherapistReviewDecisionRequest, request_id: str, idempotency_key: str, *, review_item_id: str | None = None, precommit_check=None) -> dict:
    now = utcnow()
    repo = TherapistQualificationRepository(session)
    profile = await repo.profile_for_update(therapist_id)
    if profile is None:
        raise HTTPException(404, "THERAPIST_NOT_FOUND")
    if review_item_id is None:
        result = await session.execute(__import__("sqlalchemy").select(TherapistReviewItemModel).where(TherapistReviewItemModel.therapist_id == therapist_id, TherapistReviewItemModel.status != "DECIDED").order_by(TherapistReviewItemModel.created_at.desc()).limit(1).with_for_update())
        item = result.scalar_one_or_none()
    else:
        item = await repo.review_item_for_update(review_item_id)
    if item is None or item.therapist_id != therapist_id or item.status == "DECIDED":
        raise HTTPException(409, "THERAPIST_REVIEW_STATE_CONFLICT")
    initial = item.review_kind == "INITIAL"
    scope = f"reviewer:{actor.id}:review:{item.review_item_id}"
    request_digest, replay = await _replay(repo, scope=scope, operation="REVIEW_DECISION", key=idempotency_key, request=payload)
    if replay is not None:
        return replay
    if initial:
        if profile.version != payload.expected_version:
            raise HTTPException(409, "THERAPIST_VERSION_CONFLICT")
        if profile.status not in {"SUBMITTED", "RESUBMITTED", "UNDER_REVIEW"}:
            raise HTTPException(409, "THERAPIST_REVIEW_STATE_CONFLICT")
    else:
        if item.version != payload.expected_version:
            raise HTTPException(409, "THERAPIST_REVIEW_VERSION_CONFLICT")
        if profile.status not in {"APPROVED_ACTIVE", "SUSPENDED"}:
            raise HTTPException(409, "THERAPIST_REVIEW_STATE_CONFLICT")

    if payload.decision == "START_REVIEW":
        if item.status != "QUEUED":
            raise HTTPException(409, "THERAPIST_REVIEW_STATE_CONFLICT")
        previous_status = profile.status
        item.status = "UNDER_REVIEW"
        item.reviewer_user_id = actor.id
        item.claimed_at = now
        item.version += 1
        if initial:
            profile.status = "UNDER_REVIEW"
            profile.updated_at = now
            profile.version += 1
        action = (
            "THERAPIST_REVIEW_RESTARTED"
            if initial and previous_status == "RESUBMITTED"
            else "THERAPIST_REVIEW_STARTED"
        )
        response = {
            "review_item": {
                "review_item_id": item.review_item_id,
                "status": item.status,
                "version": item.version,
            },
            "profile": {
                "therapist_id": therapist_id,
                "status": profile.status,
                "version": profile.version,
            },
            "decision_id": None,
        }
        await repo.add_audit(
            _audit(scope, action, therapist_id, request_id, payload, response, now)
        )
        await _record(
            repo,
            scope=scope,
            operation="REVIEW_DECISION",
            key=idempotency_key,
            request_digest=request_digest,
            response=response,
            now=now,
        )
        await _commit_receipt(
            session,
            scope=scope,
            operation="REVIEW_DECISION",
            key=idempotency_key,
            request_digest=request_digest,
            response=response,
            precommit_check=precommit_check,
        )
        return response

    if item.status != "UNDER_REVIEW" or item.reviewer_user_id != actor.id:
        raise HTTPException(409, "THERAPIST_REVIEW_STATE_CONFLICT")
    qualification = await repo.qualification_for_revision(therapist_id, item.revision_id)
    if qualification is None:
        raise HTTPException(409, "THERAPIST_REVIEW_STATE_CONFLICT")
    qualification_id = qualification.qualification_version_id
    supplied_outcomes = {str(key): value for key, value in payload.qualification_outcomes.items()}
    targets = tuple(str(value) for value in payload.qualification_targets)
    if payload.decision == "NEEDS_CORRECTION":
        if targets not in {(), (qualification_id,)}:
            raise HTTPException(409, "THERAPIST_CORRECTION_SCOPE_CONFLICT")
    elif supplied_outcomes != {qualification_id: payload.decision}:
        raise HTTPException(409, "THERAPIST_REVIEW_OUTCOME_CONFLICT")
    persisted_outcomes = dict(supplied_outcomes)
    if not initial and payload.decision == "APPROVED":
        if qualification.previous_version_id is None:
            raise HTTPException(409, "THERAPIST_REVIEW_OUTCOME_CONFLICT")
        persisted_outcomes[qualification.previous_version_id] = "SUPERSEDED"

    decision_id = str(Uuid7Generator().generate())
    previous_profile_version = profile.version
    was_suspended = profile.status == "SUSPENDED"
    item.status, item.decided_at, item.version = "DECIDED", now, item.version + 1
    if payload.decision == "NEEDS_CORRECTION":
        if initial:
            profile.status = "NEEDS_CORRECTION"
            profile.reviewed_at = now
        event_type = "THERAPIST_CORRECTION_REQUESTED"
    elif payload.decision == "REJECTED":
        if initial:
            profile.status = "REJECTED"
            profile.reviewed_at = now
            event_type = "THERAPIST_REJECTED"
        else:
            event_type = "THERAPIST_QUALIFICATION_REVIEWED"
    else:
        if qualification.valid_until < now.date():
            raise HTTPException(409, "THERAPIST_APPROVAL_PRECONDITION_FAILED")
        if not await repo.qualification_files_are_clean(
            qualification_id,
            qualification.attachment_count,
        ):
            raise HTTPException(409, "THERAPIST_APPROVAL_PRECONDITION_FAILED")
        guard = await repo.readiness_guard(profile.tenant_id)
        if guard is None or guard["tenant_status"] != "active":
            raise HTTPException(409, "THERAPIST_APPROVAL_PRECONDITION_FAILED")
        if initial:
            profile.status = "APPROVED_ACTIVE"
        profile.current_qualification_version_id = qualification_id
        profile.qualification_valid_until = qualification.valid_until
        profile.reviewed_at = now
        if initial:
            profile.suspension_reason_code = None
            event_type = "THERAPIST_APPROVED_ACTIVE"
        else:
            event_type = "THERAPIST_QUALIFICATION_REVIEWED"
            if profile.status == "SUSPENDED":
                profile.status = "APPROVED_ACTIVE"
                profile.resumed_at = now
                profile.suspension_reason_code = None
    profile.updated_at, profile.version = now, profile.version + 1
    decision = TherapistReviewDecisionModel(
        decision_id=decision_id, review_item_id=item.review_item_id,
        therapist_id=therapist_id, revision_id=item.revision_id,
        reviewer_user_id=actor.id, decision=payload.decision,
        qualification_outcomes=persisted_outcomes,
        reason_code=payload.reason_code,
        correction_fields=[*payload.profile_fields, *(f"qualification:{value}" for value in payload.qualification_targets)] or None,
        request_digest=request_digest, created_at=now,
    )
    response = {"review_item": {"review_item_id": item.review_item_id, "status": item.status, "version": item.version}, "profile": {"therapist_id": therapist_id, "status": profile.status, "version": profile.version}, "decision_id": decision_id}
    review_event = event_type in {"THERAPIST_CORRECTION_REQUESTED", "THERAPIST_QUALIFICATION_REVIEWED"}
    business_event = _outbox(
        event_type,
        item.review_item_id if review_event else profile.therapist_id,
        profile.tenant_id,
        now,
    )
    values: list[object] = [decision, business_event]
    status_decision = None
    if not initial and payload.decision == "APPROVED" and was_suspended:
        status_decision = TherapistStatusDecisionModel(
            status_decision_id=str(Uuid7Generator().generate()),
            therapist_id=therapist_id,
            actor_kind="USER",
            actor_user_id=actor.id,
            worker_identity=None,
            decision="RESUMED",
            reason_code="QUALIFICATION_RENEWED",
            expected_profile_version=previous_profile_version,
            request_digest=request_digest,
            created_at=now,
        )
        values.extend((
            status_decision,
            _outbox("THERAPIST_RESUMED", therapist_id, profile.tenant_id, now),
        ))
    await repo.add_all(values)
    if payload.decision == "APPROVED":
        await recompute_readiness(
            session,
            profile.tenant_id,
            trigger_event_id=business_event.event_id,
            now=now,
            commit=False,
        )
    await repo.add_audit(_audit(scope, event_type, therapist_id, request_id, payload, response, now))
    if not initial and payload.decision == "APPROVED" and was_suspended:
        await repo.add_audit(_audit(
            scope,
            "THERAPIST_RESUMED",
            therapist_id,
            request_id,
            {"expected_version": previous_profile_version, "reason_code": "QUALIFICATION_RENEWED"},
            {"status": profile.status, "version": profile.version},
            now,
        ))
    await _record(repo, scope=scope, operation="REVIEW_DECISION", key=idempotency_key, request_digest=request_digest, response=response, now=now)
    await _commit_receipt(
        session, scope=scope, operation="REVIEW_DECISION", key=idempotency_key,
        request_digest=request_digest, response=response,
        confirmation_context={
            "decision_id": decision_id,
            "status_decision_id": (
                status_decision.status_decision_id if status_decision is not None else None
            ),
        },
        precommit_check=precommit_check,
    )
    return response


async def change_status(session, actor: CurrentUser, therapist_id: str, *, expected_version: int, decision: str, reason_code: str, request_id: str, idempotency_key: str, precommit_check=None) -> dict:
    now = utcnow()
    repo = TherapistQualificationRepository(session)
    scope = f"actor:{actor.id}:therapist:{therapist_id}"
    request = {"expected_version": expected_version, "decision": decision, "reason_code": reason_code}
    request_digest, replay = await _replay(repo, scope=scope, operation=decision, key=idempotency_key, request=request)
    if replay is not None:
        return replay
    profile = await repo.profile_for_update(therapist_id)
    if profile is None:
        raise HTTPException(404, "THERAPIST_NOT_FOUND")
    if profile.version != expected_version:
        raise HTTPException(409, "THERAPIST_VERSION_CONFLICT")
    if decision == "SUSPENDED" and profile.status == "APPROVED_ACTIVE":
        profile.status, profile.suspended_at, profile.suspension_reason_code = "SUSPENDED", now, reason_code
        event = "THERAPIST_SUSPENDED"
    elif (
        decision == "RESUMED"
        and profile.status == "SUSPENDED"
        and profile.qualification_valid_until
        and profile.qualification_valid_until >= now.date()
        and profile.suspended_at is not None
        and await repo.has_approved_renewal_since(profile.therapist_id, profile.suspended_at)
    ):
        profile.status, profile.resumed_at, profile.suspension_reason_code = "APPROVED_ACTIVE", now, None
        event = "THERAPIST_RESUMED"
    elif decision == "EXITED" and profile.status in {"APPROVED_ACTIVE", "SUSPENDED"} and profile.active_case_count == 0:
        profile.status, profile.exited_at = "EXITED", now
        event = "THERAPIST_EXITED"
    else:
        raise HTTPException(409, "THERAPIST_STATE_CONFLICT")
    profile.updated_at, profile.version = now, profile.version + 1
    status_decision = TherapistStatusDecisionModel(status_decision_id=str(Uuid7Generator().generate()), therapist_id=therapist_id, actor_kind="USER", actor_user_id=actor.id, worker_identity=None, decision=decision, reason_code=reason_code, expected_profile_version=expected_version, request_digest=request_digest, created_at=now)
    response = {"therapist_id": therapist_id, "status": profile.status, "revision_id": None, "revision_no": None, "review_item_id": None, "version": profile.version}
    business_event = _outbox(event, therapist_id, profile.tenant_id, now)
    await repo.add_all((status_decision, business_event))
    await recompute_readiness(
        session,
        profile.tenant_id,
        trigger_event_id=business_event.event_id,
        now=now,
        commit=False,
    )
    await repo.add_audit(_audit(scope, event, therapist_id, request_id, request, response, now))
    await _record(repo, scope=scope, operation=decision, key=idempotency_key, request_digest=request_digest, response=response, now=now)
    await _commit_receipt(
        session, scope=scope, operation=decision, key=idempotency_key,
        request_digest=request_digest, response=response,
        confirmation_context={
            "status_decision_id": status_decision.status_decision_id,
        },
        precommit_check=precommit_check,
    )
    return response


async def recompute_readiness(
    session,
    tenant_id: int,
    *,
    trigger_event_id: str,
    now: datetime | None = None,
    commit: bool = True,
) -> InstitutionServiceReadinessModel:
    now = now or utcnow()
    repo = TherapistQualificationRepository(session)
    await repo.lock_tenant(tenant_id)
    previous_evidence = await repo.readiness_evidence_for_trigger(trigger_event_id)
    if previous_evidence is not None:
        existing = await repo.readiness(tenant_id, for_update=True)
        if existing is None:
            raise HTTPException(503, "DEPENDENCY_UNAVAILABLE")
        return existing
    guard = await repo.readiness_guard(tenant_id)
    profiles = (await session.execute(__import__("sqlalchemy").select(
        TherapistProfileModel.status,
        TherapistProfileModel.qualification_valid_until,
        TherapistProfileModel.service_tags,
        TherapistProfileModel.version,
    ).where(TherapistProfileModel.tenant_id == tenant_id).with_for_update(read=True))).all()
    local_date = now.astimezone(ZoneInfo("Asia/Shanghai")).date()
    approved_therapists = tuple(
        (valid_until, tuple(service_tags or ()))
        for status, valid_until, service_tags, _ in profiles
        if status == "APPROVED_ACTIVE"
        and valid_until is not None
        and valid_until >= local_date
    )
    readiness_source = (await session.execute(__import__("sqlalchemy").text(
        "SELECT institution_type,service_tags,license_type,valid_from,valid_until "
        "FROM public.institution_readiness_source_v1 WHERE tenant_id=:id"
    ), {"id": tenant_id})).all()
    tags = tuple(sorted({tag for row in readiness_source for tag in (row[1] or [])}))
    institution_types = {row[0] for row in readiness_source}
    institution_type = next(iter(institution_types)) if len(institution_types) == 1 else "INVALID"
    valid_license_types = tuple(sorted({
        row[2]
        for row in readiness_source
        if row[3] is not None
        and row[4] is not None
        and row[3] <= local_date <= row[4]
    }))
    license_expiries = tuple(
        row[4]
        for row in readiness_source
        if row[3] is not None
        and row[4] is not None
        and row[3] <= local_date <= row[4]
    )
    tenant_status = guard["tenant_status"] if guard else "missing"
    result = compute_service_readiness(ReadinessInputs(
        tenant_active=tenant_status == "active",
        institution_type=institution_type,
        valid_license_types=valid_license_types,
        approved_therapists=approved_therapists,
        service_tags=tags,
        compliance_suspended=tenant_status == "closed",
        institution_approval_source_valid=guard is not None and bool(readiness_source),
        institution_license_expiries=license_expiries,
    ))
    secret = TherapistSecrets()
    source_versions = {
        "tenant_public_id": str(guard["tenant_public_id"]) if guard else None,
        "tenant_status": tenant_status,
        "application_id": str(guard["application_id"]) if guard else None,
        "application_version": guard["application_version"] if guard else None,
        "license_versions": guard["license_versions"] if guard else [],
        "current_therapist_versions": guard["current_therapist_versions"] if guard else [],
        "digest_key_id": secret.readiness_key_id,
    }
    input_payload = {"tenant_public_id": source_versions["tenant_public_id"], "source_versions": source_versions, "v": 1}
    result_payload = {"qualified_therapist_count": result.qualified_therapist_count, "reason_codes": list(result.reason_codes), "next_expiry_at": result.next_expiry_at.isoformat() if result.next_expiry_at else None, "readiness_status": str(result.status), "tenant_public_id": source_versions["tenant_public_id"], "v": 1}
    input_digest = secret.readiness_digest("input", input_payload)
    result_digest = secret.readiness_digest("result", result_payload)
    row = await repo.readiness(tenant_id, for_update=True)
    if row is not None and row.input_digest == input_digest and row.result_digest == result_digest:
        return row
    evidence_version = 1 if row is None else row.evidence_version + 1
    if row is None:
        row = InstitutionServiceReadinessModel(tenant_id=tenant_id, readiness_status=result.status, reason_codes=list(result.reason_codes), qualified_therapist_count=result.qualified_therapist_count, computed_at=now, evidence_version=evidence_version, input_digest=input_digest, result_digest=result_digest, source_versions=source_versions, next_expiry_at=result.next_expiry_at, version=1)
        await repo.add(row)
    else:
        row.readiness_status, row.reason_codes, row.qualified_therapist_count = result.status, list(result.reason_codes), result.qualified_therapist_count
        row.computed_at, row.evidence_version, row.input_digest, row.result_digest = now, evidence_version, input_digest, result_digest
        row.source_versions, row.next_expiry_at, row.version = source_versions, result.next_expiry_at, row.version + 1
    evidence = ReadinessEvidenceModel(evidence_id=str(Uuid7Generator().generate()), tenant_id=tenant_id, evidence_version=evidence_version, readiness_status=result.status, reason_codes=list(result.reason_codes), qualified_therapist_count=result.qualified_therapist_count, computed_at=now, tenant_status=tenant_status, institution_license_digest=secret.readiness_digest("licenses", guard["license_versions"] if guard else []), therapist_set_digest=secret.readiness_digest("therapists", guard["current_therapist_versions"] if guard else []), service_scope_digest=secret.readiness_digest("service-tags", {"service_tags": list(tags), "v": 1}), source_versions=source_versions, next_expiry_at=result.next_expiry_at, trigger_event_id=trigger_event_id, input_digest=input_digest, result_digest=result_digest, created_at=now)
    values = [evidence]
    if guard is not None:
        values.append(_outbox("SERVICE_READINESS_RECOMPUTED", str(guard["tenant_public_id"]), tenant_id, now))
    await repo.add_all(values)
    if commit:
        await _commit_readiness(
            session,
            tenant_id=tenant_id,
            trigger_event_id=trigger_event_id,
            evidence_version=evidence_version,
            input_digest=input_digest,
            result_digest=result_digest,
        )
    return row


async def read_readiness_fail_closed(
    session,
    tenant_id: int,
    *,
    establish_transaction: bool = True,
) -> tuple[dict | None, bool]:
    """Return a public READY DTO only when its persisted evidence is still current."""
    if establish_transaction:
        await session.execute(__import__("sqlalchemy").text(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        ))
    repo = TherapistQualificationRepository(session)
    row = await repo.readiness(tenant_id)
    guard = await repo.readiness_guard(tenant_id)
    if row is None or guard is None:
        return None, row is not None
    expected_sources = {
        "tenant_public_id": str(guard["tenant_public_id"]),
        "tenant_status": guard["tenant_status"],
        "application_id": str(guard["application_id"]),
        "application_version": guard["application_version"],
        "license_versions": guard["license_versions"],
        "current_therapist_versions": guard["current_therapist_versions"],
        "digest_key_id": row.source_versions.get("digest_key_id"),
    }
    local_date = utcnow().astimezone(ZoneInfo("Asia/Shanghai")).date()
    current = (
        row.source_versions == expected_sources
        and row.next_expiry_at == guard["next_expiry_at"]
        and (
            row.readiness_status != "SERVICE_READY"
            or (row.next_expiry_at is not None and local_date < row.next_expiry_at)
        )
    )
    if current:
        status = row.readiness_status
        reasons = list(row.reason_codes)
        count = row.qualified_therapist_count
    else:
        status = "NOT_READY"
        reasons = sorted(set(row.reason_codes) | {"INSTITUTION_APPROVAL_SOURCE_INVALID"})
        count = 0
    return {
        "tenant_id": str(guard["tenant_public_id"]),
        "readiness_status": status,
        "reason_codes": reasons,
        "qualified_therapist_count": count,
        "computed_at": row.computed_at,
        "evidence_version": row.evidence_version,
    }, not current


async def renew(session, actor: CurrentUser, payload: TherapistRenew, request_id: str, idempotency_key: str, *, tenant_public_id: str, precommit_check=None) -> dict:
    repo = TherapistQualificationRepository(session)
    scope = f"therapist-user:{actor.id}:predecessor:{payload.predecessor_version_id}"
    request_digest, replay = await _replay(repo, scope=scope, operation="RENEW", key=idempotency_key, request=payload)
    if replay is not None:
        return replay
    profile = await repo.current_profile_for_user(actor.id, for_update=True)
    if profile is None or profile.status not in {"APPROVED_ACTIVE", "SUSPENDED"}:
        raise HTTPException(409, "THERAPIST_RENEWAL_CONFLICT")
    if profile.version != payload.expected_version:
        raise HTTPException(409, "THERAPIST_RENEWAL_CONFLICT")
    current = await repo.current_qualification(profile.therapist_id)
    if current is None or current.qualification_version_id != str(payload.predecessor_version_id):
        raise HTTPException(409, "THERAPIST_RENEWAL_CONFLICT")
    now = utcnow()
    revision, qualification = await _create_revision(
        repo,
        profile,
        payload.qualification,
        now,
        tenant_public_id=tenant_public_id,
        previous_version_id=current.qualification_version_id,
    )
    item_id = str(Uuid7Generator().generate())
    item = TherapistReviewItemModel(review_item_id=item_id, therapist_id=profile.therapist_id, revision_id=revision.revision_id, qualification_version_id=qualification.qualification_version_id, review_kind="RENEWAL", status="QUEUED", created_at=now, version=1)
    profile.current_revision_no, profile.updated_at, profile.version = revision.revision_no, now, profile.version + 1
    await repo.add_all((item, _outbox("THERAPIST_QUALIFICATION_RENEWAL_SUBMITTED", item_id, profile.tenant_id, now)))
    response = {"therapist_id": profile.therapist_id, "status": profile.status, "revision_id": revision.revision_id, "revision_no": revision.revision_no, "review_item_id": item_id, "version": profile.version}
    await repo.add_audit(_audit(scope, "THERAPIST_QUALIFICATION_RENEWAL_SUBMITTED", profile.therapist_id, request_id, payload, response, now))
    await _record(repo, scope=scope, operation="RENEW", key=idempotency_key, request_digest=request_digest, response=response, now=now)
    await _commit_receipt(session, scope=scope, operation="RENEW", key=idempotency_key, request_digest=request_digest, response=response, precommit_check=precommit_check)
    return response


async def renewal_resubmit(session, actor: CurrentUser, review_item_id: str, payload: TherapistRenewalResubmit, request_id: str, idempotency_key: str, *, tenant_public_id: str, precommit_check=None) -> dict:
    now = utcnow()
    scope = f"therapist-user:{actor.id}:review:{review_item_id}:decision:{payload.decision_id}"
    repo = TherapistQualificationRepository(session)
    request = {"review_item_id": review_item_id, **payload.model_dump(mode="json")}
    request_digest, replay = await _replay(repo, scope=scope, operation="RENEWAL_RESUBMIT", key=idempotency_key, request=request)
    if replay is not None:
        return replay
    profile = await repo.current_profile_for_user(actor.id, for_update=True)
    old_item = await repo.review_item_for_update(review_item_id)
    decision = (await session.execute(
        __import__("sqlalchemy").select(TherapistReviewDecisionModel)
        .where(TherapistReviewDecisionModel.decision_id == str(payload.decision_id))
        .with_for_update()
    )).scalar_one_or_none()
    if (
        profile is None or profile.status not in {"APPROVED_ACTIVE", "SUSPENDED"}
        or old_item is None or old_item.therapist_id != profile.therapist_id
        or old_item.review_kind != "RENEWAL" or old_item.status != "DECIDED"
        or old_item.version != payload.expected_review_version
        or decision is None or decision.review_item_id != review_item_id
        or decision.decision != "NEEDS_CORRECTION"
        or f"qualification:{old_item.qualification_version_id}" not in set(decision.correction_fields or ())
    ):
        raise HTTPException(409, "THERAPIST_RENEWAL_CORRECTION_SCOPE_CONFLICT")
    revision, qualification = await _create_revision(
        repo, profile, payload.qualification, now,
        tenant_public_id=tenant_public_id,
        previous_version_id=old_item.qualification_version_id,
    )
    new_item_id = str(Uuid7Generator().generate())
    item = TherapistReviewItemModel(
        review_item_id=new_item_id, therapist_id=profile.therapist_id,
        revision_id=revision.revision_id,
        qualification_version_id=qualification.qualification_version_id,
        review_kind="RENEWAL", status="QUEUED",
        previous_review_item_id=review_item_id, created_at=now, version=1,
    )
    profile.current_revision_no = revision.revision_no
    profile.updated_at = now
    profile.version += 1
    response = {"therapist_id": profile.therapist_id, "status": profile.status, "revision_id": revision.revision_id, "revision_no": revision.revision_no, "review_item_id": new_item_id, "version": profile.version}
    await repo.add_all((item, _outbox("THERAPIST_QUALIFICATION_RENEWAL_RESUBMITTED", new_item_id, profile.tenant_id, now)))
    await repo.add_audit(_audit(scope, "THERAPIST_QUALIFICATION_RENEWAL_RESUBMITTED", profile.therapist_id, request_id, request, response, now))
    await _record(repo, scope=scope, operation="RENEWAL_RESUBMIT", key=idempotency_key, request_digest=request_digest, response=response, now=now)
    await _commit_receipt(session, scope=scope, operation="RENEWAL_RESUBMIT", key=idempotency_key, request_digest=request_digest, response=response, precommit_check=precommit_check)
    return response


def translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, TherapistConflict):
        code = str(exc)
        return HTTPException(409, code)
    return HTTPException(503, "DEPENDENCY_UNAVAILABLE")
