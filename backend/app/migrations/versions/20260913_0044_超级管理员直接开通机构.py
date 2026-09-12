"""Add controlled direct institution onboarding and canonical institution origin.

Revision ID: 20260913_0044
Revises: 20260912_0043
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.engine import make_url

from app.core.uuid_generator import Uuid7Generator

revision = "20260913_0044"
down_revision = "20260912_0043"
branch_labels = None
depends_on = None

_LOCK_KEY = 6052316115572212044
_PHONE_CLAIM_DIGEST_DOMAIN = b"phase1/account-phone-claim/v1\0"
_PHONE_RE = re.compile(r"1[3-9][0-9]{9}", re.ASCII)
_ROLE_CONFIG = (
    ("onboarding_writer", "KG_INSTITUTION_ONBOARDING_WRITER_ROLE", "KG_INSTITUTION_ONBOARDING_WRITER_DATABASE_URL"),
    ("review_writer", "KG_INSTITUTION_REVIEW_WRITER_ROLE", "KG_INSTITUTION_REVIEW_WRITER_DATABASE_URL"),
    ("reader", "KG_INSTITUTION_ONBOARDING_READER_ROLE", "KG_INSTITUTION_ONBOARDING_READER_DATABASE_URL"),
    ("application", "KG_DATABASE_USER", "KG_DATABASE_URL"),
    ("therapist_writer", "KG_THERAPIST_ONBOARDING_WRITER_ROLE", "KG_THERAPIST_ONBOARDING_WRITER_DATABASE_URL"),
    ("delivery_worker", "KG_DELIVERY_WORKER_ROLE", "KG_DELIVERY_WORKER_DATABASE_URL"),
    ("private_file_writer", "KG_PRIVATE_FILE_WRITER_ROLE", "KG_PRIVATE_FILE_WRITER_DATABASE_URL"),
    ("clinical_reader", "KG_SLICE4_CLINICAL_READER_ROLE", "KG_SLICE4_CLINICAL_READER_DATABASE_URL"),
    ("institution_reader", "KG_SLICE4_INSTITUTION_READER_ROLE", "KG_SLICE4_INSTITUTION_READER_DATABASE_URL"),
    ("therapist_review_writer", "KG_THERAPIST_REVIEW_WRITER_ROLE", "KG_THERAPIST_REVIEW_WRITER_DATABASE_URL"),
    ("therapist_reader", "KG_THERAPIST_READER_ROLE", "KG_THERAPIST_READER_DATABASE_URL"),
    ("member_enrollment_reader", "KG_MEMBER_ENROLLMENT_READER_ROLE", "KG_MEMBER_ENROLLMENT_READER_DATABASE_URL"),
    ("member_case_writer", "KG_MEMBER_CASE_WRITER_ROLE", "KG_MEMBER_CASE_WRITER_DATABASE_URL"),
    ("health_record_writer", "KG_HEALTH_RECORD_WRITER_ROLE", "KG_HEALTH_RECORD_WRITER_DATABASE_URL"),
    ("assessment_readiness_writer", "KG_ASSESSMENT_READINESS_WRITER_ROLE", "KG_ASSESSMENT_READINESS_WRITER_DATABASE_URL"),
    ("slice4_identity_authority", "KG_SLICE4_IDENTITY_AUTHORITY_ROLE", "KG_SLICE4_IDENTITY_AUTHORITY_DATABASE_URL"),
    ("slice5_worker", "KG_SLICE5_WORKFLOW_WORKER_ROLE", "KG_SLICE5_WORKFLOW_WORKER_DATABASE_URL"),
    ("slice7_milestone_writer", "KG_SLICE7_MILESTONE_WRITER_ROLE", "KG_SLICE7_MILESTONE_WRITER_DATABASE_URL"),
    ("slice7_case_writer", "KG_SLICE7_CASE_WRITER_ROLE", "KG_SLICE7_CASE_WRITER_DATABASE_URL"),
    ("slice7_transfer_writer", "KG_SLICE7_TRANSFER_WRITER_ROLE", "KG_SLICE7_TRANSFER_WRITER_DATABASE_URL"),
    ("slice7_export_worker", "KG_SLICE7_EXPORT_WORKER_ROLE", "KG_SLICE7_EXPORT_WORKER_DATABASE_URL"),
    ("slice7_family_reader", "KG_SLICE7_FAMILY_READER_ROLE", "KG_SLICE7_FAMILY_READER_DATABASE_URL"),
    ("slice7_oversight_reader", "KG_SLICE7_OVERSIGHT_READER_ROLE", "KG_SLICE7_OVERSIGHT_READER_DATABASE_URL"),
)


def _configuration_error() -> None:
    raise RuntimeError("DIRECT_ONBOARDING_CONFIGURATION_INVALID") from None


def _roles() -> dict[str, str]:
    values: dict[str, str] = {}
    targets: set[tuple[object, object, object]] = set()
    for key, role_var, url_var in _ROLE_CONFIG:
        role = os.getenv(role_var, "").strip()
        raw_url = os.getenv(url_var, "").strip()
        if re.fullmatch(r"[a-z][a-z0-9_]{0,62}", role) is None or not raw_url:
            _configuration_error()
        try:
            url = make_url(raw_url)
        except Exception:
            _configuration_error()
        if not url.drivername.startswith("postgresql") or url.username != role:
            _configuration_error()
        values[key] = role
        targets.add((url.host, url.port, url.database))
    if len(set(values.values())) != len(values) or len(targets) != 1:
        _configuration_error()
    return values


def _lock() -> None:
    op.get_bind().execute(
        sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY}
    )


def _keyring(current_name: str, keyring_name: str) -> tuple[str, dict[str, bytes]]:
    def unique_pairs(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                _configuration_error()
            value[key] = item
        return value

    try:
        current = os.environ[current_name]
        raw = json.loads(os.environ[keyring_name], object_pairs_hook=unique_pairs)
        keys = {
            str(key): base64.b64decode(value, validate=True)
            for key, value in raw.items()
        }
        if (
            type(raw) is not dict
            or not raw
            or current not in keys
            or any(len(value) != 32 for value in keys.values())
        ):
            _configuration_error()
        return current, keys
    except Exception:
        _configuration_error()


def _phone_claim_digest(key: bytes, phone: str) -> str:
    if _PHONE_RE.fullmatch(phone) is None:
        raise RuntimeError("DIRECT_ONBOARDING_PHONE_CLAIM_BACKFILL_INVALID")
    return hmac.new(
        key, _PHONE_CLAIM_DIGEST_DOMAIN + phone.encode("ascii"), hashlib.sha256
    ).hexdigest()


def _preflight_phone_claims() -> list[dict[str, object]]:
    """Resolve every pre-0044 account phone before the first DDL statement."""
    connection = op.get_bind()
    connection.execute(
        sa.text(
            'LOCK TABLE public."user", public.institution_invitation, '
            "public.therapist_invitation, public.institution_application IN SHARE MODE"
        )
    )
    global_key_id, global_keys = _keyring(
        "KG_ACCOUNT_PHONE_CLAIM_DIGEST_CURRENT_KEY_ID",
        "KG_ACCOUNT_PHONE_CLAIM_DIGEST_KEYRING_JSON",
    )
    try:
        controlled_key = base64.b64decode(
            os.environ["KG_ONBOARDING_PII_KEK_B64"], validate=True
        )
        if len(controlled_key) != 32:
            _configuration_error()
    except Exception:
        _configuration_error()
    _, therapist_keys = _keyring(
        "KG_THERAPIST_PII_ENCRYPTION_CURRENT_KEY_ID",
        "KG_THERAPIST_PII_ENCRYPTION_KEYRING_JSON",
    )

    resolved: list[tuple[str, UUID | None, int | None, str]] = []
    for row in connection.execute(
        sa.text('SELECT id,phone FROM public."user" WHERE phone IS NOT NULL ORDER BY id')
    ).mappings():
        resolved.append(("EXISTING_USER", None, int(row["id"]), str(row["phone"])))

    controlled_aes = AESGCM(controlled_key)
    for row in connection.execute(
        sa.text(
            "SELECT invitation_id,applicant_phone_ciphertext "
            "FROM public.institution_invitation WHERE status='ISSUED' "
            "ORDER BY invitation_id"
        )
    ).mappings():
        try:
            ciphertext = bytes(row["applicant_phone_ciphertext"])
            phone = controlled_aes.decrypt(
                ciphertext[:12], ciphertext[12:], b"institution-onboarding-v1"
            ).decode("utf-8")
            invitation_id = UUID(str(row["invitation_id"]))
        except Exception:
            raise RuntimeError(
                "DIRECT_ONBOARDING_PHONE_CLAIM_BACKFILL_INVALID"
            ) from None
        resolved.append(("CONTROLLED_ORG_ADMIN", invitation_id, None, phone))

    therapist_rows = connection.execute(
        sa.text(
            "SELECT invitation.invitation_id,invitation.phone_ciphertext,"
            "invitation.phone_encryption_key_id,app.tenant_public_id "
            "FROM public.therapist_invitation invitation "
            "JOIN public.institution_application app "
            "ON app.tenant_internal_id=invitation.tenant_id AND app.status='APPROVED' "
            "WHERE invitation.status='INVITED' ORDER BY invitation.invitation_id"
        )
    ).mappings().all()
    active_therapist_count = connection.execute(
        sa.text("SELECT count(*) FROM public.therapist_invitation WHERE status='INVITED'")
    ).scalar_one()
    if len(therapist_rows) != active_therapist_count:
        raise RuntimeError("DIRECT_ONBOARDING_PHONE_CLAIM_BACKFILL_INVALID")
    therapist_refs: set[UUID] = set()
    for row in therapist_rows:
        try:
            invitation_id = UUID(str(row["invitation_id"]))
            tenant_public_id = UUID(str(row["tenant_public_id"]))
            if invitation_id in therapist_refs:
                raise ValueError
            therapist_refs.add(invitation_id)
            key = therapist_keys[str(row["phone_encryption_key_id"])]
            ciphertext = bytes(row["phone_ciphertext"])
            aad = (
                "phase1-slice2/invitation-phone/v1\0"
                f"{str(tenant_public_id).lower()}\0{str(invitation_id).lower()}"
            ).encode()
            phone = AESGCM(key).decrypt(
                ciphertext[:12], ciphertext[12:], aad
            ).decode("utf-8")
        except Exception:
            raise RuntimeError(
                "DIRECT_ONBOARDING_PHONE_CLAIM_BACKFILL_INVALID"
            ) from None
        resolved.append(("THERAPIST_ACCOUNT", invitation_id, None, phone))

    claims: list[dict[str, object]] = []
    active_digests: set[tuple[str, str]] = set()
    for claim_kind, claim_ref, user_id, phone in resolved:
        digest = _phone_claim_digest(global_keys[global_key_id], phone)
        identity = (global_key_id, digest)
        if identity in active_digests:
            raise RuntimeError("DIRECT_ONBOARDING_PHONE_CLAIM_BACKFILL_CONFLICT")
        active_digests.add(identity)
        claims.append(
            {
                "claim_id": Uuid7Generator().generate(),
                "phone_digest_key_id": global_key_id,
                "phone_digest": digest,
                "claim_kind": claim_kind,
                "claim_ref": claim_ref,
                "state": "BOUND" if user_id is not None else "PENDING",
                "user_id": user_id,
            }
        )
    return claims


def _backfill_phone_claims(claims: list[dict[str, object]]) -> None:
    connection = op.get_bind()
    if claims:
        connection.execute(
            sa.text(
                "INSERT INTO public.identity_phone_claim("
                "claim_id,phone_digest_key_id,phone_digest,claim_kind,claim_ref,state,user_id) "
                "VALUES(:claim_id,:phone_digest_key_id,:phone_digest,:claim_kind,"
                ":claim_ref,:state,:user_id)"
            ),
            claims,
        )
    actual = {
        (
            str(row["phone_digest_key_id"]),
            str(row["phone_digest"]),
            str(row["claim_kind"]),
            UUID(str(row["claim_ref"])) if row["claim_ref"] is not None else None,
            int(row["user_id"]) if row["user_id"] is not None else None,
            str(row["state"]),
        )
        for row in connection.execute(
            sa.text(
                "SELECT phone_digest_key_id,phone_digest,claim_kind,claim_ref,user_id,state "
                "FROM public.identity_phone_claim"
            )
        ).mappings()
    }
    expected = {
        (
            str(claim["phone_digest_key_id"]),
            str(claim["phone_digest"]),
            str(claim["claim_kind"]),
            claim["claim_ref"],
            claim["user_id"],
            str(claim["state"]),
        )
        for claim in claims
    }
    if actual != expected:
        raise RuntimeError("DIRECT_ONBOARDING_PHONE_CLAIM_BACKFILL_INVALID")


def _create_tables() -> None:
    ddl_script = """
CREATE TABLE public.direct_institution_onboarding (
  onboarding_id UUID PRIMARY KEY,
  tenant_id BIGINT NOT NULL UNIQUE REFERENCES public.tenant(id) ON DELETE RESTRICT,
  tenant_public_id UUID NOT NULL UNIQUE,
  institution_code VARCHAR(32) NOT NULL UNIQUE,
  institution_name VARCHAR(100) NOT NULL,
  institution_type VARCHAR(32) NOT NULL,
  administrative_region_id BIGINT NOT NULL REFERENCES public.platform_org(id) ON DELETE RESTRICT,
  admin_phone_ciphertext BYTEA NOT NULL,
  admin_phone_key_id VARCHAR(64) NOT NULL,
  phone_digest_key_id VARCHAR(64) NOT NULL,
  phone_digest CHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL,
  created_by BIGINT NOT NULL REFERENCES public."user"(id) ON DELETE RESTRICT,
  activated_user_id BIGINT NULL REFERENCES public."user"(id) ON DELETE RESTRICT,
  current_revision_id UUID NULL,
  compliance_due_at TIMESTAMPTZ NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  activated_at TIMESTAMPTZ NULL,
  version BIGINT NOT NULL DEFAULT 1,
  CONSTRAINT ck_direct_institution_type CHECK (institution_type IN ('HEALTH_STORE','LICENSED_CLINIC')),
  CONSTRAINT ck_direct_institution_state CHECK (
    status IN ('PENDING_ACTIVATION','ACTIVE_COMPLIANCE_PENDING','COMPLIANCE_UNDER_REVIEW',
      'COMPLIANCE_NEEDS_CORRECTION','COMPLIANCE_APPROVED','REVOKED_BEFORE_ACTIVATION')
    AND version>=1
    AND ((status IN ('PENDING_ACTIVATION','REVOKED_BEFORE_ACTIVATION')
          AND activated_user_id IS NULL AND activated_at IS NULL AND compliance_due_at IS NULL)
      OR (status NOT IN ('PENDING_ACTIVATION','REVOKED_BEFORE_ACTIVATION')
          AND activated_user_id IS NOT NULL AND activated_at IS NOT NULL AND compliance_due_at IS NOT NULL))
  )
);

CREATE TABLE public.identity_phone_claim (
  claim_id UUID PRIMARY KEY,
  phone_digest_key_id VARCHAR(64) NOT NULL,
  phone_digest CHAR(64) NOT NULL,
  claim_kind VARCHAR(32) NOT NULL,
  claim_ref UUID NULL,
  state VARCHAR(16) NOT NULL,
  user_id BIGINT NULL REFERENCES public."user"(id) ON DELETE RESTRICT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  version BIGINT NOT NULL DEFAULT 1,
  CONSTRAINT uq_identity_phone_claim_kind_ref UNIQUE(claim_kind,claim_ref),
  CONSTRAINT ck_identity_phone_claim_kind CHECK (claim_kind IN (
    'EXISTING_USER','MEMBER_ACCOUNT','CONTROLLED_ORG_ADMIN','DIRECT_ORG_ADMIN',
    'THERAPIST_ACCOUNT','ADMIN_HANDOFF')),
  CONSTRAINT ck_identity_phone_claim_state CHECK (
    state IN ('PENDING','BOUND','RELEASED') AND version>=1
    AND ((state='BOUND' AND user_id IS NOT NULL)
      OR (state IN ('PENDING','RELEASED') AND user_id IS NULL))
    AND ((claim_kind='EXISTING_USER' AND state='BOUND' AND claim_ref IS NULL)
      OR (claim_kind<>'EXISTING_USER' AND claim_ref IS NOT NULL))
  )
);
CREATE UNIQUE INDEX uq_identity_phone_claim_active_digest
  ON public.identity_phone_claim(phone_digest_key_id,phone_digest)
  WHERE state IN ('PENDING','BOUND');
CREATE UNIQUE INDEX uq_identity_phone_claim_bound_user
  ON public.identity_phone_claim(user_id) WHERE state='BOUND';

CREATE TABLE public.institution_tenant_origin (
  tenant_id BIGINT PRIMARY KEY REFERENCES public.tenant(id) ON DELETE RESTRICT,
  tenant_public_id UUID NOT NULL UNIQUE,
  origin_type VARCHAR(32) NOT NULL,
  controlled_application_id UUID NULL UNIQUE REFERENCES public.institution_application(application_id) ON DELETE RESTRICT,
  direct_onboarding_id UUID NULL UNIQUE REFERENCES public.direct_institution_onboarding(onboarding_id) ON DELETE RESTRICT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  version BIGINT NOT NULL DEFAULT 1,
  CONSTRAINT ck_institution_tenant_origin CHECK (
    version>=1 AND ((origin_type='CONTROLLED_APPLICATION' AND controlled_application_id IS NOT NULL AND direct_onboarding_id IS NULL)
      OR (origin_type='DIRECT_PROVISIONING' AND controlled_application_id IS NULL AND direct_onboarding_id IS NOT NULL))
  )
);

CREATE TABLE public.direct_institution_activation_credential (
  credential_id UUID PRIMARY KEY,
  onboarding_id UUID NOT NULL REFERENCES public.direct_institution_onboarding(onboarding_id) ON DELETE RESTRICT,
  credential_digest_key_id VARCHAR(64) NOT NULL,
  credential_digest CHAR(64) NOT NULL,
  status VARCHAR(16) NOT NULL,
  failed_attempts SMALLINT NOT NULL DEFAULT 0,
  issued_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ NULL,
  version BIGINT NOT NULL DEFAULT 1,
  CONSTRAINT uq_direct_activation_credential_digest UNIQUE(credential_digest_key_id,credential_digest),
  CONSTRAINT ck_direct_activation_credential CHECK (
    status IN ('ISSUED','CONSUMED','REVOKED') AND failed_attempts BETWEEN 0 AND 5
    AND expires_at>issued_at AND version>=1
    AND ((status='CONSUMED' AND consumed_at IS NOT NULL) OR (status<>'CONSUMED' AND consumed_at IS NULL))
  )
);
CREATE UNIQUE INDEX uq_direct_activation_active
  ON public.direct_institution_activation_credential(onboarding_id)
  WHERE status='ISSUED';

CREATE TABLE public.platform_admin_security_profile (
  user_id BIGINT PRIMARY KEY REFERENCES public."user"(id) ON DELETE RESTRICT,
  secret_ciphertext BYTEA NOT NULL,
  key_id VARCHAR(64) NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  profile_version BIGINT NOT NULL DEFAULT 1,
  failed_attempts SMALLINT NOT NULL DEFAULT 0,
  locked_until TIMESTAMPTZ NULL,
  last_accepted_time_step BIGINT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  CONSTRAINT ck_platform_admin_security_profile CHECK (
    profile_version>=1 AND failed_attempts BETWEEN 0 AND 5
  )
);

CREATE TABLE public.direct_institution_admin_account (
  user_id BIGINT PRIMARY KEY REFERENCES public."user"(id) ON DELETE RESTRICT,
  onboarding_id UUID NOT NULL REFERENCES public.direct_institution_onboarding(onboarding_id) ON DELETE RESTRICT,
  activation_credential_id UUID NULL,
  handoff_credential_id UUID NULL,
  totp_secret_ciphertext BYTEA NOT NULL,
  totp_key_id VARCHAR(64) NOT NULL,
  totp_secret_digest CHAR(64) NOT NULL,
  totp_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  last_accepted_time_step BIGINT NOT NULL,
  activated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  version BIGINT NOT NULL DEFAULT 1,
  CONSTRAINT uq_direct_admin_account_onboarding UNIQUE(onboarding_id,user_id),
  CONSTRAINT uq_direct_admin_account_activation_credential UNIQUE(activation_credential_id),
  CONSTRAINT uq_direct_admin_account_handoff_credential UNIQUE(handoff_credential_id),
  CONSTRAINT ck_direct_admin_account CHECK (
    version>=1 AND (
      (activation_credential_id IS NOT NULL AND handoff_credential_id IS NULL)
      OR (activation_credential_id IS NULL AND handoff_credential_id IS NOT NULL)
    )
  )
);

CREATE TABLE public.direct_institution_compliance_revision (
  revision_id UUID PRIMARY KEY,
  onboarding_id UUID NOT NULL REFERENCES public.direct_institution_onboarding(onboarding_id) ON DELETE RESTRICT,
  revision_no INTEGER NOT NULL,
  status VARCHAR(24) NOT NULL,
  created_operation_id UUID NOT NULL UNIQUE,
  compliance_schema_version SMALLINT NOT NULL,
  compliance_payload_ciphertext BYTEA NOT NULL,
  compliance_payload_key_id VARCHAR(64) NOT NULL,
  compliance_payload_digest_key_id VARCHAR(64) NOT NULL,
  compliance_payload_digest CHAR(64) NOT NULL,
  service_tags JSONB NOT NULL,
  unified_social_credit_code_digest_key_id VARCHAR(64) NOT NULL,
  unified_social_credit_code_digest CHAR(64) NOT NULL,
  license_count SMALLINT NOT NULL,
  license_set_digest_key_id VARCHAR(64) NOT NULL,
  license_set_digest CHAR(64) NOT NULL,
  submitted_by BIGINT NOT NULL REFERENCES public."user"(id) ON DELETE RESTRICT,
  reviewed_by BIGINT NULL REFERENCES public."user"(id) ON DELETE RESTRICT,
  reason_code VARCHAR(64) NULL,
  correction_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  submitted_at TIMESTAMPTZ NULL,
  reviewed_at TIMESTAMPTZ NULL,
  version BIGINT NOT NULL DEFAULT 1,
  CONSTRAINT uq_direct_compliance_revision_no UNIQUE(onboarding_id,revision_no),
  CONSTRAINT ck_direct_compliance_revision CHECK (
    revision_no>=1 AND version>=1 AND compliance_schema_version=1
    AND license_count BETWEEN 1 AND 10
    AND status IN ('DRAFT','SUBMITTED','UNDER_REVIEW','NEEDS_CORRECTION','APPROVED')
    AND jsonb_typeof(service_tags)='array'
  )
);
ALTER TABLE public.direct_institution_onboarding
  ADD CONSTRAINT fk_direct_onboarding_current_revision
  FOREIGN KEY(current_revision_id) REFERENCES public.direct_institution_compliance_revision(revision_id) ON DELETE RESTRICT;

CREATE TABLE public.direct_institution_license (
  license_id UUID PRIMARY KEY,
  onboarding_id UUID NOT NULL REFERENCES public.direct_institution_onboarding(onboarding_id) ON DELETE RESTRICT,
  revision_id UUID NOT NULL REFERENCES public.direct_institution_compliance_revision(revision_id) ON DELETE RESTRICT,
  license_type VARCHAR(32) NOT NULL,
  license_no_ciphertext BYTEA NULL,
  license_no_key_id VARCHAR(64) NULL,
  license_no_digest_key_id VARCHAR(64) NULL,
  license_no_digest CHAR(64) NULL,
  private_file_id UUID NOT NULL REFERENCES public.private_file(file_id) ON DELETE RESTRICT,
  valid_from DATE NOT NULL,
  valid_until DATE NOT NULL,
  status VARCHAR(16) NOT NULL,
  version BIGINT NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  superseded_at TIMESTAMPTZ NULL,
  CONSTRAINT uq_direct_license_revision_id UNIQUE(revision_id,license_id),
  CONSTRAINT uq_direct_license_revision_type_file UNIQUE(revision_id,license_type,private_file_id),
  CONSTRAINT ck_direct_license CHECK (
    license_type IN ('BUSINESS_LICENSE','MEDICAL_INSTITUTION_LICENSE')
    AND valid_until>=valid_from AND version>=1
    AND ((license_no_ciphertext IS NULL AND license_no_key_id IS NULL
      AND license_no_digest_key_id IS NULL AND license_no_digest IS NULL)
      OR (license_no_ciphertext IS NOT NULL AND license_no_key_id IS NOT NULL
        AND license_no_digest_key_id IS NOT NULL AND license_no_digest IS NOT NULL))
    AND ((status IN ('DRAFT','SUBMITTED','CURRENT') AND superseded_at IS NULL)
      OR (status='SUPERSEDED' AND superseded_at IS NOT NULL))
  )
);

CREATE TABLE public.institution_admin_handoff (
  handoff_id UUID PRIMARY KEY,
  onboarding_id UUID NOT NULL REFERENCES public.direct_institution_onboarding(onboarding_id) ON DELETE RESTRICT,
  old_user_id BIGINT NOT NULL REFERENCES public."user"(id) ON DELETE RESTRICT,
  new_phone_ciphertext BYTEA NOT NULL,
  new_phone_key_id VARCHAR(64) NOT NULL,
  new_phone_digest_key_id VARCHAR(64) NOT NULL,
  new_phone_digest CHAR(64) NOT NULL,
  status VARCHAR(16) NOT NULL,
  new_user_id BIGINT NULL REFERENCES public."user"(id) ON DELETE RESTRICT,
  created_by BIGINT NOT NULL REFERENCES public."user"(id) ON DELETE RESTRICT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  expires_at TIMESTAMPTZ NOT NULL,
  activated_at TIMESTAMPTZ NULL,
  version BIGINT NOT NULL DEFAULT 1,
  CONSTRAINT ck_institution_admin_handoff CHECK (
    status IN ('ISSUED','ACTIVATED','REVOKED') AND expires_at>created_at AND version>=1
  )
);
CREATE UNIQUE INDEX uq_institution_admin_handoff_active
  ON public.institution_admin_handoff(onboarding_id)
  WHERE status='ISSUED';

CREATE TABLE public.institution_admin_handoff_credential (
  credential_id UUID PRIMARY KEY,
  handoff_id UUID NOT NULL REFERENCES public.institution_admin_handoff(handoff_id) ON DELETE RESTRICT,
  credential_digest_key_id VARCHAR(64) NOT NULL,
  credential_digest CHAR(64) NOT NULL,
  status VARCHAR(16) NOT NULL,
  failed_attempts SMALLINT NOT NULL DEFAULT 0,
  issued_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ NULL,
  version BIGINT NOT NULL DEFAULT 1,
  CONSTRAINT uq_handoff_credential_digest UNIQUE(credential_digest_key_id,credential_digest),
  CONSTRAINT ck_handoff_credential CHECK (
    status IN ('ISSUED','CONSUMED','REVOKED') AND failed_attempts BETWEEN 0 AND 5
    AND expires_at>issued_at AND version>=1
    AND ((status='CONSUMED' AND consumed_at IS NOT NULL)
      OR (status<>'CONSUMED' AND consumed_at IS NULL))
  )
);
CREATE UNIQUE INDEX uq_handoff_credential_active
  ON public.institution_admin_handoff_credential(handoff_id)
  WHERE status='ISSUED';

ALTER TABLE public.direct_institution_admin_account
  ADD CONSTRAINT fk_direct_admin_account_activation_credential
  FOREIGN KEY(activation_credential_id)
  REFERENCES public.direct_institution_activation_credential(credential_id) ON DELETE RESTRICT,
  ADD CONSTRAINT fk_direct_admin_account_handoff_credential
  FOREIGN KEY(handoff_credential_id)
  REFERENCES public.institution_admin_handoff_credential(credential_id) ON DELETE RESTRICT;

CREATE TABLE public.direct_onboarding_receipt (
  receipt_id UUID PRIMARY KEY,
  actor_scope VARCHAR(128) NOT NULL,
  operation VARCHAR(64) NOT NULL,
  idempotency_key VARCHAR(128) NOT NULL,
  digest_key_id VARCHAR(64) NOT NULL,
  request_digest CHAR(64) NOT NULL,
  postimage_digest CHAR(64) NOT NULL,
  response_payload JSONB NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  CONSTRAINT uq_direct_onboarding_receipt UNIQUE(actor_scope,operation,idempotency_key),
  CONSTRAINT ck_direct_onboarding_receipt_digest_key CHECK (
    digest_key_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
  ),
  CONSTRAINT ck_direct_onboarding_receipt_response CHECK (
    (CASE
      WHEN operation='REVOKE' THEN response_payload IS NULL
      WHEN response_payload IS NULL OR jsonb_typeof(response_payload)<>'object' THEN FALSE
      WHEN operation IN ('CREATE','REGENERATE') THEN
        response_payload ?& ARRAY[
          'onboarding_id','tenant_id','status','version','credential_delivery_state',
          'credential_id','credential_issued_at','credential_expires_at']
        AND (response_payload-ARRAY[
          'onboarding_id','tenant_id','status','version','credential_delivery_state',
          'credential_id','credential_issued_at','credential_expires_at'])='{}'::jsonb
        AND jsonb_typeof(response_payload->'onboarding_id')='string'
        AND jsonb_typeof(response_payload->'tenant_id')='string'
        AND jsonb_typeof(response_payload->'status')='string'
        AND jsonb_typeof(response_payload->'version')='number'
        AND jsonb_typeof(response_payload->'credential_delivery_state')='string'
        AND jsonb_typeof(response_payload->'credential_id')='string'
        AND jsonb_typeof(response_payload->'credential_issued_at')='string'
        AND jsonb_typeof(response_payload->'credential_expires_at')='string'
        AND response_payload->>'credential_delivery_state'='ISSUED'
      WHEN operation IN ('HANDOFF_CREATE','HANDOFF_REGENERATE') THEN
        response_payload ?& ARRAY[
          'onboarding_id','tenant_id','handoff_id','status','version',
          'credential_delivery_state','credential_id','credential_issued_at','credential_expires_at']
        AND (response_payload-ARRAY[
          'onboarding_id','tenant_id','handoff_id','status','version',
          'credential_delivery_state','credential_id','credential_issued_at','credential_expires_at'])='{}'::jsonb
        AND jsonb_typeof(response_payload->'onboarding_id')='string'
        AND jsonb_typeof(response_payload->'tenant_id')='string'
        AND jsonb_typeof(response_payload->'handoff_id')='string'
        AND jsonb_typeof(response_payload->'status')='string'
        AND jsonb_typeof(response_payload->'version')='number'
        AND jsonb_typeof(response_payload->'credential_delivery_state')='string'
        AND jsonb_typeof(response_payload->'credential_id')='string'
        AND jsonb_typeof(response_payload->'credential_issued_at')='string'
        AND jsonb_typeof(response_payload->'credential_expires_at')='string'
        AND response_payload->>'status'='ISSUED'
        AND response_payload->>'credential_delivery_state'='ISSUED'
      WHEN operation='HANDOFF_REVOKE' THEN
        response_payload ?& ARRAY[
          'onboarding_id','tenant_id','handoff_id','status','version','credential_delivery_state']
        AND (response_payload-ARRAY[
          'onboarding_id','tenant_id','handoff_id','status','version','credential_delivery_state'])='{}'::jsonb
        AND jsonb_typeof(response_payload->'onboarding_id')='string'
        AND jsonb_typeof(response_payload->'tenant_id')='string'
        AND jsonb_typeof(response_payload->'handoff_id')='string'
        AND jsonb_typeof(response_payload->'status')='string'
        AND jsonb_typeof(response_payload->'version')='number'
        AND jsonb_typeof(response_payload->'credential_delivery_state')='string'
        AND response_payload->>'status'='REVOKED'
        AND response_payload->>'credential_delivery_state'='REVOKED'
      WHEN operation='ACTIVATE' THEN
        response_payload ?& ARRAY[
          'onboarding_id','tenant_id','institution_code','institution_name','institution_type',
          'administrative_region_id','status','compliance_due_at','current_revision_id','version']
        AND (response_payload-ARRAY[
          'onboarding_id','tenant_id','institution_code','institution_name','institution_type',
          'administrative_region_id','status','compliance_due_at','current_revision_id','version'])='{}'::jsonb
        AND jsonb_typeof(response_payload->'onboarding_id')='string'
        AND jsonb_typeof(response_payload->'tenant_id')='string'
        AND jsonb_typeof(response_payload->'institution_code')='string'
        AND jsonb_typeof(response_payload->'institution_name')='string'
        AND jsonb_typeof(response_payload->'institution_type')='string'
        AND jsonb_typeof(response_payload->'administrative_region_id')='number'
        AND jsonb_typeof(response_payload->'status')='string'
        AND jsonb_typeof(response_payload->'compliance_due_at')='string'
        AND jsonb_typeof(response_payload->'current_revision_id')='null'
        AND jsonb_typeof(response_payload->'version')='number'
        AND response_payload->>'status'='ACTIVE_COMPLIANCE_PENDING'
      WHEN operation IN ('COMPLIANCE_SAVE','COMPLIANCE_SUBMIT') THEN
        response_payload ?& ARRAY['onboarding_id','tenant_id','revision_id','status','version']
        AND (response_payload-ARRAY['onboarding_id','tenant_id','revision_id','status','version'])='{}'::jsonb
        AND jsonb_typeof(response_payload->'onboarding_id')='string'
        AND jsonb_typeof(response_payload->'tenant_id')='string'
        AND jsonb_typeof(response_payload->'revision_id')='string'
        AND jsonb_typeof(response_payload->'status')='string'
        AND jsonb_typeof(response_payload->'version')='number'
      WHEN operation='COMPLIANCE_DECIDE' THEN
        response_payload ?& ARRAY['onboarding_id','revision_id','revision_no','status','institution_name',
          'institution_type','administrative_region_id','institution_code','service_tags','licenses','correction_fields','version']
        AND (response_payload-ARRAY['onboarding_id','revision_id','revision_no','status','institution_name',
          'institution_type','administrative_region_id','institution_code','service_tags','licenses','correction_fields','version'])='{}'::jsonb
        AND jsonb_typeof(response_payload->'onboarding_id')='string'
        AND jsonb_typeof(response_payload->'revision_id')='string'
        AND jsonb_typeof(response_payload->'revision_no')='number'
        AND jsonb_typeof(response_payload->'status')='string'
        AND jsonb_typeof(response_payload->'institution_name')='string'
        AND jsonb_typeof(response_payload->'institution_type')='string'
        AND jsonb_typeof(response_payload->'administrative_region_id')='number'
        AND jsonb_typeof(response_payload->'institution_code')='string'
        AND jsonb_typeof(response_payload->'service_tags')='array'
        AND jsonb_typeof(response_payload->'licenses')='array'
        AND jsonb_typeof(response_payload->'correction_fields')='array'
        AND jsonb_typeof(response_payload->'version')='number'
      WHEN operation='HANDOFF_ACTIVATE' THEN
        response_payload ?& ARRAY['handoff_id','onboarding_id','tenant_id','status','version']
        AND (response_payload-ARRAY['handoff_id','onboarding_id','tenant_id','status','version'])='{}'::jsonb
        AND jsonb_typeof(response_payload->'handoff_id')='string'
        AND jsonb_typeof(response_payload->'onboarding_id')='string'
        AND jsonb_typeof(response_payload->'tenant_id')='string'
        AND jsonb_typeof(response_payload->'status')='string'
        AND jsonb_typeof(response_payload->'version')='number'
        AND response_payload->>'status'='ACTIVATED'
      ELSE FALSE
    END) IS TRUE
  )
);
CREATE TABLE public.direct_onboarding_audit (
  audit_id UUID PRIMARY KEY,
  actor_user_id BIGINT NULL,
  actor_kind VARCHAR(32) NOT NULL,
  action VARCHAR(64) NOT NULL,
  object_id UUID NOT NULL,
  result VARCHAR(16) NOT NULL,
  reason_code VARCHAR(64) NULL,
  evidence_digest CHAR(64) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
CREATE TABLE public.direct_onboarding_outbox (
  event_id UUID PRIMARY KEY,
  event_type VARCHAR(64) NOT NULL,
  aggregate_id UUID NOT NULL REFERENCES public.direct_institution_onboarding(onboarding_id) ON DELETE RESTRICT,
  payload JSONB NOT NULL,
  payload_digest CHAR(64) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
  attempts SMALLINT NOT NULL DEFAULT 0,
  worker_id VARCHAR(64) NULL,
  lease_token VARCHAR(128) NULL,
  lease_until TIMESTAMPTZ NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
  delivered_at TIMESTAMPTZ NULL,
  version BIGINT NOT NULL DEFAULT 1,
  CONSTRAINT ck_direct_outbox CHECK (status IN ('PENDING','PROCESSING','DELIVERED','FAILED') AND attempts BETWEEN 0 AND 3 AND version>=1)
);
"""
    for statement in ddl_script.split(";"):
        if statement.strip():
            op.execute(sa.text(statement))


def _create_functions(roles: dict[str, str]) -> None:
    onboarding = roles["onboarding_writer"]
    review = roles["review_writer"]
    reader = roles["reader"]
    application = roles["application"]
    therapist = roles["therapist_writer"]
    delivery = roles["delivery_worker"]
    therapist_review = roles["therapist_review_writer"]
    therapist_reader = roles["therapist_reader"]
    member_reader = roles["member_enrollment_reader"]
    case_writer = roles["member_case_writer"]
    health_record_writer = roles["health_record_writer"]
    assessment_readiness_writer = roles["assessment_readiness_writer"]
    slice4_identity_authority = roles["slice4_identity_authority"]
    slice5_worker = roles["slice5_worker"]
    slice7_milestone_writer = roles["slice7_milestone_writer"]
    slice7_case_writer = roles["slice7_case_writer"]
    slice7_transfer_writer = roles["slice7_transfer_writer"]
    slice7_export_worker = roles["slice7_export_worker"]
    slice7_family_reader = roles["slice7_family_reader"]
    slice7_oversight_reader = roles["slice7_oversight_reader"]
    function_script = f"""
CREATE FUNCTION public.institution_tenant_origin_current_v1(
  p_tenant_id BIGINT,p_tenant_public_id UUID
) RETURNS TABLE(tenant_id BIGINT,tenant_public_id UUID,origin_type VARCHAR,origin_ref UUID,tenant_status VARCHAR,source_status VARCHAR,source_version BIGINT)
LANGUAGE plpgsql SECURITY DEFINER STABLE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF session_user NOT IN ('{reader}','{onboarding}','{review}','{application}','{therapist}',
    '{therapist_review}','{therapist_reader}','{member_reader}','{case_writer}',
    '{health_record_writer}','{assessment_readiness_writer}','{slice4_identity_authority}',
    '{slice5_worker}','{slice7_milestone_writer}','{slice7_case_writer}',
    '{slice7_transfer_writer}','{slice7_export_worker}','{slice7_family_reader}',
    '{slice7_oversight_reader}') THEN
    RAISE EXCEPTION 'INSTITUTION_ORIGIN_CURRENTNESS_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  IF p_tenant_id IS NULL AND p_tenant_public_id IS NULL THEN RETURN; END IF;
  RETURN QUERY
  SELECT origin.tenant_id,origin.tenant_public_id,origin.origin_type,
         COALESCE(origin.controlled_application_id,origin.direct_onboarding_id),
         tenant_row.status::VARCHAR,
         CASE WHEN origin.origin_type='CONTROLLED_APPLICATION' THEN app.status::VARCHAR ELSE direct_row.status END,
         CASE WHEN origin.origin_type='CONTROLLED_APPLICATION' THEN app.version ELSE direct_row.version END
    FROM public.institution_tenant_origin origin
    JOIN public.tenant tenant_row ON tenant_row.id=origin.tenant_id
    LEFT JOIN public.institution_application app ON app.application_id=origin.controlled_application_id
    LEFT JOIN public.direct_institution_onboarding direct_row ON direct_row.onboarding_id=origin.direct_onboarding_id
   WHERE (p_tenant_id IS NULL OR origin.tenant_id=p_tenant_id)
     AND (p_tenant_public_id IS NULL OR origin.tenant_public_id=p_tenant_public_id)
     AND tenant_row.status='active'
     AND ((origin.origin_type='CONTROLLED_APPLICATION' AND app.status='APPROVED'
       AND app.tenant_internal_id=origin.tenant_id AND app.tenant_public_id=origin.tenant_public_id)
       OR (origin.origin_type='DIRECT_PROVISIONING'
         AND direct_row.status NOT IN ('PENDING_ACTIVATION','REVOKED_BEFORE_ACTIVATION')
         AND direct_row.tenant_id=origin.tenant_id AND direct_row.tenant_public_id=origin.tenant_public_id));
END $$;

CREATE FUNCTION public.institution_controlled_origin_bind_v1(p_application_id UUID)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE app_tenant BIGINT; app_public UUID; app_status TEXT;
BEGIN
  IF session_user<>'{review}' THEN RAISE EXCEPTION 'CONTROLLED_ORIGIN_BIND_FORBIDDEN' USING ERRCODE='42501'; END IF;
  SELECT tenant_internal_id,tenant_public_id,status INTO app_tenant,app_public,app_status
    FROM public.institution_application WHERE application_id=p_application_id FOR UPDATE;
  IF NOT FOUND OR app_status<>'APPROVED' OR app_tenant IS NULL OR app_public IS NULL THEN RETURN NULL; END IF;
  PERFORM 1 FROM public.tenant WHERE id=app_tenant FOR SHARE;
  IF NOT FOUND THEN RETURN NULL; END IF;
  INSERT INTO public.institution_tenant_origin(tenant_id,tenant_public_id,origin_type,controlled_application_id)
  VALUES(app_tenant,app_public,'CONTROLLED_APPLICATION',p_application_id)
  ON CONFLICT(tenant_id) DO NOTHING;
  IF NOT EXISTS(SELECT 1 FROM public.institution_tenant_origin WHERE tenant_id=app_tenant AND tenant_public_id=app_public AND controlled_application_id=p_application_id) THEN RETURN NULL; END IF;
  RETURN app_public;
END $$;

CREATE FUNCTION public.identity_phone_claim_internal_v1(p_envelope JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE
  claim_row public.identity_phone_claim%ROWTYPE;
  action_value TEXT:=p_envelope->>'action';
  candidate JSONB;
  candidate_keys TEXT[]:=ARRAY[]::TEXT[];
  previous_key TEXT;
  current_pair_found BOOLEAN:=FALSE;
BEGIN
  IF jsonb_typeof(p_envelope) IS DISTINCT FROM 'object'
     OR (jsonb_typeof(p_envelope->'action')='string') IS NOT TRUE THEN
    RAISE EXCEPTION 'PHONE_CLAIM_ENVELOPE_INVALID' USING ERRCODE='22023';
  END IF;
  IF current_user=session_user THEN
    RAISE EXCEPTION 'PHONE_CLAIM_INTERNAL_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  IF action_value='RESERVE' THEN
    IF (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(p_envelope) key)
       IS DISTINCT FROM ARRAY['action','claim_id','claim_kind','claim_ref','expected_state','phone_digest','phone_digest_candidates','phone_digest_key_id'] THEN
      RAISE EXCEPTION 'PHONE_CLAIM_ENVELOPE_INVALID' USING ERRCODE='22023';
    END IF;
    IF (jsonb_typeof(p_envelope->'expected_state')='string') IS NOT TRUE
       OR (jsonb_typeof(p_envelope->'claim_id')='string') IS NOT TRUE
       OR (jsonb_typeof(p_envelope->'claim_kind')='string') IS NOT TRUE
       OR (jsonb_typeof(p_envelope->'claim_ref')='string') IS NOT TRUE
       OR (jsonb_typeof(p_envelope->'phone_digest_key_id')='string') IS NOT TRUE
       OR (jsonb_typeof(p_envelope->'phone_digest')='string') IS NOT TRUE
       OR (jsonb_typeof(p_envelope->'phone_digest_candidates')='array') IS NOT TRUE
       OR (p_envelope->>'claim_id' ~* '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$') IS NOT TRUE
       OR (p_envelope->>'claim_ref' ~* '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$') IS NOT TRUE
       OR (p_envelope->>'expected_state'='NO_ACTIVE_CLAIM') IS NOT TRUE
       OR (p_envelope->>'phone_digest_key_id' ~ '^[A-Za-z0-9][A-Za-z0-9._-]{{0,63}}$') IS NOT TRUE
       OR (p_envelope->>'phone_digest' ~ '^[0-9a-f]{{64}}$') IS NOT TRUE THEN
      RAISE EXCEPTION 'PHONE_CLAIM_INPUT_INVALID' USING ERRCODE='22023';
    END IF;
    IF (get_byte(uuid_send((p_envelope->>'claim_id')::UUID),6) >> 4)<>7
       OR jsonb_array_length(p_envelope->'phone_digest_candidates') NOT BETWEEN 1 AND 16
       OR jsonb_array_length(p_envelope->'phone_digest_candidates') IS NULL THEN
      RAISE EXCEPTION 'PHONE_CLAIM_INPUT_INVALID' USING ERRCODE='22023';
    END IF;
    FOR candidate IN SELECT value FROM jsonb_array_elements(p_envelope->'phone_digest_candidates')
    LOOP
      IF (jsonb_typeof(candidate)='object') IS NOT TRUE THEN
        RAISE EXCEPTION 'PHONE_CLAIM_CANDIDATES_INVALID' USING ERRCODE='22023';
      END IF;
      IF (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(candidate) key)
            IS DISTINCT FROM ARRAY['digest','key_id']
         OR (jsonb_typeof(candidate->'key_id')='string') IS NOT TRUE
         OR (jsonb_typeof(candidate->'digest')='string') IS NOT TRUE
         OR (candidate->>'key_id' ~ '^[A-Za-z0-9][A-Za-z0-9._-]{{0,63}}$') IS NOT TRUE
         OR (candidate->>'digest' ~ '^[0-9a-f]{{64}}$') IS NOT TRUE
         OR (previous_key IS NOT NULL AND (candidate->>'key_id'>previous_key) IS NOT TRUE)
         OR (candidate->>'key_id'=ANY(candidate_keys)) IS TRUE THEN
        RAISE EXCEPTION 'PHONE_CLAIM_CANDIDATES_INVALID' USING ERRCODE='22023';
      END IF;
      candidate_keys:=array_append(candidate_keys,candidate->>'key_id');
      previous_key:=candidate->>'key_id';
      current_pair_found:=current_pair_found OR (
        candidate->>'key_id'=p_envelope->>'phone_digest_key_id'
        AND candidate->>'digest'=p_envelope->>'phone_digest'
      );
    END LOOP;
    IF current_pair_found IS NOT TRUE THEN
      RAISE EXCEPTION 'PHONE_CLAIM_CURRENT_PAIR_MISSING' USING ERRCODE='22023';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('identity_phone_claim/reserve/v1',0));
    FOR candidate IN SELECT value FROM jsonb_array_elements(p_envelope->'phone_digest_candidates')
    LOOP
      PERFORM pg_advisory_xact_lock(hashtextextended(
        (candidate->>'key_id')||chr(31)||(candidate->>'digest'),0));
    END LOOP;
    IF EXISTS(
      SELECT 1 FROM public.identity_phone_claim active_claim
       WHERE active_claim.state IN ('PENDING','BOUND')
         AND (active_claim.phone_digest_key_id=ANY(candidate_keys)) IS NOT TRUE
    ) THEN
      RAISE EXCEPTION 'PHONE_CLAIM_KEY_COVERAGE_INCOMPLETE' USING ERRCODE='55000';
    END IF;
    IF EXISTS(
      SELECT 1 FROM public.identity_phone_claim active_claim
       WHERE active_claim.state IN ('PENDING','BOUND')
         AND EXISTS(
           SELECT 1 FROM jsonb_array_elements(p_envelope->'phone_digest_candidates') item
            WHERE item->>'key_id'=active_claim.phone_digest_key_id
              AND item->>'digest'=active_claim.phone_digest
         )
    ) THEN
      RAISE EXCEPTION 'PHONE_CLAIM_OCCUPIED'
        USING ERRCODE='23505',CONSTRAINT='uq_identity_phone_claim_active_digest';
    END IF;
    INSERT INTO public.identity_phone_claim(
      claim_id,phone_digest_key_id,phone_digest,claim_kind,claim_ref,state
    ) VALUES(
      (p_envelope->>'claim_id')::UUID,p_envelope->>'phone_digest_key_id',
      p_envelope->>'phone_digest',p_envelope->>'claim_kind',
      (p_envelope->>'claim_ref')::UUID,'PENDING'
    );
  ELSIF action_value='BIND' THEN
    IF (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(p_envelope) key)
       <> ARRAY['action','claim_id','claim_kind','claim_ref','expected_version','user_id'] THEN
      RAISE EXCEPTION 'PHONE_CLAIM_ENVELOPE_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT * INTO claim_row FROM public.identity_phone_claim
     WHERE claim_id=(p_envelope->>'claim_id')::UUID
       AND claim_kind=p_envelope->>'claim_kind'
       AND claim_ref=(p_envelope->>'claim_ref')::UUID FOR UPDATE;
    IF NOT FOUND OR claim_row.state<>'PENDING'
       OR claim_row.version<>(p_envelope->>'expected_version')::BIGINT THEN
      RETURN NULL;
    END IF;
    UPDATE public.identity_phone_claim
       SET state='BOUND',user_id=(p_envelope->>'user_id')::BIGINT,
           updated_at=clock_timestamp(),version=version+1
     WHERE claim_id=claim_row.claim_id;
  ELSIF action_value='RELEASE' THEN
    IF (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(p_envelope) key)
       <> ARRAY['action','claim_id','claim_kind','claim_ref','expected_version'] THEN
      RAISE EXCEPTION 'PHONE_CLAIM_ENVELOPE_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT * INTO claim_row FROM public.identity_phone_claim
     WHERE claim_id=(p_envelope->>'claim_id')::UUID
       AND claim_kind=p_envelope->>'claim_kind'
       AND claim_ref=(p_envelope->>'claim_ref')::UUID FOR UPDATE;
    IF NOT FOUND OR claim_row.state<>'PENDING'
       OR claim_row.version<>(p_envelope->>'expected_version')::BIGINT THEN
      RETURN NULL;
    END IF;
    UPDATE public.identity_phone_claim
       SET state='RELEASED',updated_at=clock_timestamp(),version=version+1
     WHERE claim_id=claim_row.claim_id;
  ELSE
    RAISE EXCEPTION 'PHONE_CLAIM_ACTION_INVALID' USING ERRCODE='22023';
  END IF;
  RETURN jsonb_build_object('claim_id',p_envelope->>'claim_id','state',
    CASE WHEN action_value='RESERVE' THEN 'PENDING' WHEN action_value='BIND' THEN 'BOUND' ELSE 'RELEASED' END);
END $$;

CREATE FUNCTION public.direct_keyed_digest_candidate_v1(
  p_candidates JSONB,p_key_id VARCHAR
) RETURNS CHAR(64) LANGUAGE plpgsql SECURITY DEFINER STABLE PARALLEL SAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE candidate JSONB; previous_key TEXT; result_value CHAR(64);
BEGIN
  IF (p_key_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{{0,63}}$') IS NOT TRUE
     OR (jsonb_typeof(p_candidates)='array') IS NOT TRUE THEN
    RAISE EXCEPTION 'KEYED_DIGEST_CANDIDATES_INVALID' USING ERRCODE='22023';
  END IF;
  IF jsonb_array_length(p_candidates) NOT BETWEEN 1 AND 16
     OR jsonb_array_length(p_candidates) IS NULL THEN
    RAISE EXCEPTION 'KEYED_DIGEST_CANDIDATES_INVALID' USING ERRCODE='22023';
  END IF;
  FOR candidate IN SELECT value FROM jsonb_array_elements(p_candidates)
  LOOP
    IF (jsonb_typeof(candidate)='object') IS NOT TRUE THEN
      RAISE EXCEPTION 'KEYED_DIGEST_CANDIDATES_INVALID' USING ERRCODE='22023';
    END IF;
    IF (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(candidate) key)
          IS DISTINCT FROM ARRAY['digest','key_id']
       OR (jsonb_typeof(candidate->'key_id')='string') IS NOT TRUE
       OR (jsonb_typeof(candidate->'digest')='string') IS NOT TRUE
       OR (candidate->>'key_id' ~ '^[A-Za-z0-9][A-Za-z0-9._-]{{0,63}}$') IS NOT TRUE
       OR (candidate->>'digest' ~ '^[0-9a-f]{{64}}$') IS NOT TRUE
       OR (previous_key IS NOT NULL AND (candidate->>'key_id'>previous_key) IS NOT TRUE) THEN
      RAISE EXCEPTION 'KEYED_DIGEST_CANDIDATES_INVALID' USING ERRCODE='22023';
    END IF;
    IF (candidate->>'key_id'=p_key_id) IS TRUE THEN
      result_value:=candidate->>'digest';
    END IF;
    previous_key:=candidate->>'key_id';
  END LOOP;
  IF result_value IS NULL THEN
    RAISE EXCEPTION 'DIGEST_KEY_UNAVAILABLE' USING ERRCODE='55000';
  END IF;
  RETURN result_value;
END $$;

CREATE FUNCTION public.direct_create_step_up_begin_v1(
  p_actor_user_id BIGINT,p_actor_scope VARCHAR,p_idempotency_key VARCHAR,p_request_digest CHAR,
  p_phone_digest_key_id VARCHAR,p_phone_digest CHAR
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor_row public."user"%ROWTYPE; profile_row public.platform_admin_security_profile%ROWTYPE;
BEGIN
  IF session_user<>'{review}' THEN RAISE EXCEPTION 'DIRECT_STEP_UP_FORBIDDEN' USING ERRCODE='42501'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_actor_scope||E'\\000'||p_idempotency_key,0));
  SELECT * INTO actor_row FROM public."user" WHERE id=p_actor_user_id FOR SHARE;
  IF NOT FOUND OR actor_row.role::TEXT<>'super_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id IS NOT NULL THEN RETURN NULL; END IF;
  SELECT * INTO profile_row FROM public.platform_admin_security_profile
   WHERE user_id=p_actor_user_id FOR UPDATE;
  IF NOT FOUND OR NOT profile_row.enabled
     OR (profile_row.locked_until IS NOT NULL AND profile_row.locked_until>clock_timestamp()) THEN RETURN NULL; END IF;
  RETURN jsonb_build_object(
    'actor_user_id',actor_row.id,'actor_role',actor_row.role::TEXT,'actor_status',actor_row.status::TEXT,
    'profile_secret_ciphertext',replace(encode(profile_row.secret_ciphertext,'base64'),E'\n',''),'profile_key_id',profile_row.key_id,
    'profile_version',profile_row.profile_version,'failed_attempts',profile_row.failed_attempts,
    'locked_until',profile_row.locked_until,'last_accepted_time_step',profile_row.last_accepted_time_step,
    'request_digest',p_request_digest
  );
END $$;

CREATE FUNCTION public.direct_create_step_up_failure_v1(
  p_actor_user_id BIGINT,p_actor_scope VARCHAR,p_idempotency_key VARCHAR,p_operation_id UUID,
  p_request_digest CHAR,p_observed_profile_version BIGINT,p_failed_time_step BIGINT,p_failure_id UUID,
  p_phone_digest_key_id VARCHAR,p_phone_digest CHAR
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor_row public."user"%ROWTYPE; profile_row public.platform_admin_security_profile%ROWTYPE;
        next_failed SMALLINT; next_locked TIMESTAMPTZ;
BEGIN
  IF session_user<>'{review}' THEN RAISE EXCEPTION 'DIRECT_CREATE_STEP_UP_FAILURE_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF get_byte(uuid_send(p_operation_id),6) >> 4 <> 7 OR get_byte(uuid_send(p_failure_id),6) >> 4 <> 7
     OR p_request_digest !~ '^[0-9a-f]{{64}}$' OR p_phone_digest !~ '^[0-9a-f]{{64}}$' THEN
    RAISE EXCEPTION 'DIRECT_CREATE_STEP_UP_FAILURE_INVALID' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_actor_scope||E'\\000'||p_idempotency_key,0));
  SELECT * INTO actor_row FROM public."user" WHERE id=p_actor_user_id FOR SHARE;
  IF NOT FOUND OR actor_row.role::TEXT<>'super_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id IS NOT NULL THEN RETURN NULL; END IF;
  SELECT * INTO profile_row FROM public.platform_admin_security_profile WHERE user_id=p_actor_user_id FOR UPDATE;
  IF NOT FOUND OR NOT profile_row.enabled OR profile_row.profile_version<>p_observed_profile_version THEN RETURN NULL; END IF;
  IF EXISTS(SELECT 1 FROM public.direct_onboarding_audit a WHERE a.audit_id=p_failure_id
    AND a.actor_user_id=p_actor_user_id AND a.action='DIRECT_CREATE_STEP_UP_FAILURE'
    AND a.object_id=p_operation_id AND a.evidence_digest=p_request_digest) THEN
    RETURN jsonb_build_object('failed_attempts',profile_row.failed_attempts,'locked_until',profile_row.locked_until);
  END IF;
  next_failed:=LEAST(profile_row.failed_attempts+1,5);
  next_locked:=CASE WHEN next_failed>=5 THEN clock_timestamp()+INTERVAL '15 minutes' ELSE profile_row.locked_until END;
  UPDATE public.platform_admin_security_profile
     SET failed_attempts=next_failed,locked_until=next_locked,updated_at=clock_timestamp(),profile_version=profile_version+1
   WHERE user_id=profile_row.user_id;
  INSERT INTO public.direct_onboarding_audit(audit_id,actor_user_id,actor_kind,action,object_id,result,reason_code,evidence_digest)
  VALUES(p_failure_id,actor_row.id,'SUPER_ADMIN','DIRECT_CREATE_STEP_UP_FAILURE',p_operation_id,
    'DENIED','STEP_UP_FORBIDDEN',p_request_digest);
  RETURN jsonb_build_object('failed_attempts',next_failed,'locked_until',next_locked,'failed_time_step',p_failed_time_step);
END $$;

CREATE FUNCTION public.direct_institution_create_v1(p_envelope JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE
  actor_row public."user"%ROWTYPE;
  county_row public.platform_org%ROWTYPE;
  city_row public.platform_org%ROWTYPE;
  province_row public.platform_org%ROWTYPE;
  headquarter_row public.platform_org%ROWTYPE;
  profile_row public.platform_admin_security_profile%ROWTYPE;
  tenant_value BIGINT;
  accepted_step BIGINT;
  current_step BIGINT;
  issued_value TIMESTAMPTZ:=clock_timestamp();
  expires_value TIMESTAMPTZ;
  response_value JSONB;
BEGIN
  IF session_user<>'{review}' THEN
    RAISE EXCEPTION 'DIRECT_CREATE_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  IF jsonb_typeof(p_envelope)<>'object'
     OR NOT p_envelope ?& ARRAY[
       'accepted_totp_step','actor_role','actor_scope','actor_user_id','admin_phone_ciphertext','admin_phone_key_id','administrative_region_id',
       'audit_id','credential_digest','credential_digest_key_id','credential_id','digest_key_id','duplicate_acknowledged',
       'event_id','expected_postimage_digest','idempotency_key','institution_code','institution_name',
       'institution_type','onboarding_id','operation_id','phone_digest','phone_digest_candidates','phone_digest_key_id',
       'reason_code','reason_note','receipt_id','request_digest','tenant_public_id']
     OR (p_envelope-ARRAY[
       'accepted_totp_step','actor_role','actor_scope','actor_user_id','admin_phone_ciphertext','admin_phone_key_id','administrative_region_id',
       'audit_id','credential_digest','credential_digest_key_id','credential_id','digest_key_id','duplicate_acknowledged',
       'event_id','expected_postimage_digest','idempotency_key','institution_code','institution_name',
       'institution_type','onboarding_id','operation_id','phone_digest','phone_digest_candidates','phone_digest_key_id',
       'reason_code','reason_note','receipt_id','request_digest','tenant_public_id'])<>'{{}}'::jsonb THEN
    RAISE EXCEPTION 'DIRECT_CREATE_ENVELOPE_INVALID' USING ERRCODE='22023';
  END IF;
  IF jsonb_typeof(p_envelope->'accepted_totp_step')<>'number'
     OR (p_envelope->>'accepted_totp_step')!~'^(0|[1-9][0-9]*)$' THEN
    RAISE EXCEPTION 'DIRECT_CREATE_ENVELOPE_INVALID' USING ERRCODE='22023';
  END IF;
  IF (p_envelope->>'accepted_totp_step')::NUMERIC>9223372036854775807 THEN
    RAISE EXCEPTION 'DIRECT_CREATE_ENVELOPE_INVALID' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(
    (p_envelope->>'actor_scope')||E'\\000'||(p_envelope->>'idempotency_key'),0));
  SELECT * INTO actor_row FROM public."user"
   WHERE id=(p_envelope->>'actor_user_id')::BIGINT FOR SHARE;
  IF NOT FOUND OR actor_row.role::TEXT<>'super_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id IS NOT NULL OR p_envelope->>'actor_role'<>'super_admin' THEN
    RAISE EXCEPTION 'DIRECT_CREATE_ACTOR_NOT_CURRENT' USING ERRCODE='42501';
  END IF;
  SELECT * INTO profile_row FROM public.platform_admin_security_profile
   WHERE user_id=actor_row.id FOR UPDATE;
  accepted_step:=(p_envelope->>'accepted_totp_step')::BIGINT;
  current_step:=floor(extract(epoch FROM clock_timestamp())/30)::BIGINT;
  IF NOT FOUND OR NOT profile_row.enabled
     OR (profile_row.locked_until IS NOT NULL AND profile_row.locked_until>clock_timestamp())
     OR accepted_step<=COALESCE(profile_row.last_accepted_time_step,-1)
     OR NOT (accepted_step BETWEEN current_step-1 AND current_step+1) THEN
    RAISE EXCEPTION 'DIRECT_CREATE_STEP_UP_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  SELECT * INTO county_row FROM public.platform_org
   WHERE id=(p_envelope->>'administrative_region_id')::BIGINT FOR SHARE;
  IF NOT FOUND OR county_row.org_type<>'county' OR county_row.status<>'active' THEN
    RAISE EXCEPTION 'DIRECT_CREATE_REGION_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT * INTO city_row FROM public.platform_org WHERE id=county_row.parent_id FOR SHARE;
  IF NOT FOUND OR city_row.org_type<>'city' OR city_row.status<>'active' THEN
    RAISE EXCEPTION 'DIRECT_CREATE_REGION_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT * INTO province_row FROM public.platform_org WHERE id=city_row.parent_id FOR SHARE;
  IF NOT FOUND OR province_row.org_type<>'province' OR province_row.status<>'active' THEN
    RAISE EXCEPTION 'DIRECT_CREATE_REGION_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT * INTO headquarter_row FROM public.platform_org WHERE id=province_row.parent_id FOR SHARE;
  IF NOT FOUND OR headquarter_row.org_type<>'headquarter' OR headquarter_row.status<>'active'
     OR headquarter_row.parent_id IS NOT NULL THEN
    RAISE EXCEPTION 'DIRECT_CREATE_REGION_INVALID' USING ERRCODE='22023';
  END IF;
  IF EXISTS(
    SELECT 1 FROM public.direct_institution_onboarding
     WHERE institution_name=p_envelope->>'institution_name'
       AND administrative_region_id=county_row.id
  ) AND NOT (p_envelope->>'duplicate_acknowledged')::BOOLEAN THEN
    RAISE EXCEPTION 'DIRECT_CREATE_DUPLICATE_ACK_REQUIRED' USING ERRCODE='23505';
  END IF;
  SELECT nextval(pg_get_serial_sequence('public.tenant','id')) INTO tenant_value;
  INSERT INTO public.tenant(
    id,org_id,tenant_code,name,type,province,city,status,created_at,updated_at
  ) VALUES(
    tenant_value,county_row.id,p_envelope->>'institution_code',p_envelope->>'institution_name',
    p_envelope->>'institution_type',province_row.org_name,city_row.org_name,'pending',issued_value,issued_value
  );
  INSERT INTO public.direct_institution_onboarding(
    onboarding_id,tenant_id,tenant_public_id,institution_code,institution_name,institution_type,
    administrative_region_id,admin_phone_ciphertext,admin_phone_key_id,phone_digest_key_id,phone_digest,status,created_by
  ) VALUES(
    (p_envelope->>'onboarding_id')::UUID,tenant_value,(p_envelope->>'tenant_public_id')::UUID,
    p_envelope->>'institution_code',p_envelope->>'institution_name',p_envelope->>'institution_type',county_row.id,
    decode(p_envelope->>'admin_phone_ciphertext','base64'),p_envelope->>'admin_phone_key_id',p_envelope->>'phone_digest_key_id',
    p_envelope->>'phone_digest','PENDING_ACTIVATION',actor_row.id
  );
  INSERT INTO public.institution_tenant_origin(
    tenant_id,tenant_public_id,origin_type,direct_onboarding_id
  ) VALUES(
    tenant_value,(p_envelope->>'tenant_public_id')::UUID,'DIRECT_PROVISIONING',
    (p_envelope->>'onboarding_id')::UUID
  );
  PERFORM public.identity_phone_claim_internal_v1(jsonb_build_object(
    'action','RESERVE','claim_id',p_envelope->>'operation_id','claim_kind','DIRECT_ORG_ADMIN',
    'claim_ref',p_envelope->>'onboarding_id','phone_digest_key_id',p_envelope->>'phone_digest_key_id',
    'phone_digest',p_envelope->>'phone_digest','phone_digest_candidates',p_envelope->'phone_digest_candidates',
    'expected_state','NO_ACTIVE_CLAIM'));
  expires_value:=(((issued_value AT TIME ZONE 'Asia/Shanghai')::DATE+30)::TIMESTAMP AT TIME ZONE 'Asia/Shanghai');
  INSERT INTO public.direct_institution_activation_credential(
    credential_id,onboarding_id,credential_digest_key_id,credential_digest,status,issued_at,expires_at
  ) VALUES(
    (p_envelope->>'credential_id')::UUID,(p_envelope->>'onboarding_id')::UUID,
    p_envelope->>'credential_digest_key_id',p_envelope->>'credential_digest','ISSUED',issued_value,expires_value
  );
  response_value:=jsonb_build_object(
    'onboarding_id',p_envelope->>'onboarding_id','tenant_id',p_envelope->>'tenant_public_id',
    'status','PENDING_ACTIVATION','version',1,'credential_delivery_state','ISSUED',
    'credential_id',p_envelope->>'credential_id','credential_issued_at',issued_value,
    'credential_expires_at',expires_value);
  INSERT INTO public.direct_onboarding_audit(
    audit_id,actor_user_id,actor_kind,action,object_id,result,reason_code,evidence_digest
  ) VALUES(
    (p_envelope->>'audit_id')::UUID,actor_row.id,'SUPER_ADMIN','DIRECT_INSTITUTION_CREATE',
    (p_envelope->>'onboarding_id')::UUID,'SUCCESS',p_envelope->>'reason_code',
    p_envelope->>'expected_postimage_digest'
  );
  INSERT INTO public.direct_onboarding_outbox(event_id,event_type,aggregate_id,payload,payload_digest)
  VALUES(
    (p_envelope->>'event_id')::UUID,'DIRECT_INSTITUTION_CREATED',(p_envelope->>'onboarding_id')::UUID,
    jsonb_build_object('onboarding_id',p_envelope->>'onboarding_id'),p_envelope->>'expected_postimage_digest'
  );
  INSERT INTO public.direct_onboarding_receipt(
    receipt_id,actor_scope,operation,idempotency_key,digest_key_id,request_digest,postimage_digest,response_payload
  ) VALUES(
    (p_envelope->>'receipt_id')::UUID,p_envelope->>'actor_scope','CREATE',p_envelope->>'idempotency_key',
    p_envelope->>'digest_key_id',p_envelope->>'request_digest',p_envelope->>'expected_postimage_digest',response_value
  );
  UPDATE public.platform_admin_security_profile
     SET last_accepted_time_step=accepted_step,failed_attempts=0,locked_until=NULL,
         updated_at=clock_timestamp(),profile_version=profile_version+1
   WHERE user_id=profile_row.user_id AND profile_version=profile_row.profile_version;
  RETURN response_value;
END $$;

CREATE FUNCTION public.direct_regenerate_step_up_begin_v1(
  p_actor_user_id BIGINT,p_onboarding_id UUID,p_actor_scope VARCHAR,p_idempotency_key VARCHAR,p_request_digest CHAR
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor_row public."user"%ROWTYPE; profile_row public.platform_admin_security_profile%ROWTYPE;
        root public.direct_institution_onboarding%ROWTYPE; digest_key TEXT; digest_value TEXT;
        active_credential_id UUID;
BEGIN
  IF session_user<>'{review}' THEN RAISE EXCEPTION 'DIRECT_REGENERATE_STEP_UP_FORBIDDEN' USING ERRCODE='42501'; END IF;
  SELECT phone_digest_key_id,phone_digest INTO digest_key,digest_value
    FROM public.direct_institution_onboarding WHERE onboarding_id=p_onboarding_id;
  IF NOT FOUND THEN RETURN NULL; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_actor_scope||E'\\000'||p_idempotency_key,0));
  PERFORM pg_advisory_xact_lock(hashtextextended(digest_key||E'\\000'||digest_value,0));
  SELECT * INTO actor_row FROM public."user" WHERE id=p_actor_user_id FOR SHARE;
  IF NOT FOUND OR actor_row.role::TEXT<>'super_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id IS NOT NULL THEN RETURN NULL; END IF;
  SELECT * INTO profile_row FROM public.platform_admin_security_profile WHERE user_id=p_actor_user_id FOR UPDATE;
  IF NOT FOUND OR NOT profile_row.enabled
     OR (profile_row.locked_until IS NOT NULL AND profile_row.locked_until>clock_timestamp()) THEN RETURN NULL; END IF;
  SELECT * INTO root FROM public.direct_institution_onboarding WHERE onboarding_id=p_onboarding_id FOR UPDATE;
  IF NOT FOUND OR root.status<>'PENDING_ACTIVATION' OR root.phone_digest_key_id<>digest_key
     OR root.phone_digest<>digest_value THEN RETURN NULL; END IF;
  SELECT credential_id INTO active_credential_id
    FROM public.direct_institution_activation_credential
   WHERE onboarding_id=root.onboarding_id AND status='ISSUED' FOR UPDATE;
  IF NOT FOUND OR EXISTS(
    SELECT 1 FROM public.direct_institution_activation_credential
     WHERE onboarding_id=root.onboarding_id AND status='ISSUED'
       AND credential_id<>active_credential_id
  ) THEN RETURN NULL; END IF;
  RETURN jsonb_build_object('actor_user_id',actor_row.id,'profile_secret_ciphertext',replace(encode(profile_row.secret_ciphertext,'base64'),E'\n',''),
    'profile_key_id',profile_row.key_id,'profile_version',profile_row.profile_version,
    'failed_attempts',profile_row.failed_attempts,'locked_until',profile_row.locked_until,
    'last_accepted_time_step',profile_row.last_accepted_time_step,'target_version',root.version,
    'active_credential_id',active_credential_id,
    'request_digest',p_request_digest);
END $$;

CREATE FUNCTION public.direct_regenerate_step_up_failure_v1(
  p_actor_user_id BIGINT,p_onboarding_id UUID,p_actor_scope VARCHAR,p_idempotency_key VARCHAR,
  p_operation_id UUID,p_request_digest CHAR,p_observed_profile_version BIGINT,p_failed_time_step BIGINT,p_failure_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor_row public."user"%ROWTYPE; profile_row public.platform_admin_security_profile%ROWTYPE;
        root public.direct_institution_onboarding%ROWTYPE; digest_key TEXT; digest_value TEXT;
        next_failed SMALLINT; next_locked TIMESTAMPTZ;
BEGIN
  IF session_user<>'{review}' THEN RAISE EXCEPTION 'DIRECT_REGENERATE_STEP_UP_FAILURE_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF get_byte(uuid_send(p_operation_id),6) >> 4 <> 7 OR get_byte(uuid_send(p_failure_id),6) >> 4 <> 7
     OR p_request_digest !~ '^[0-9a-f]{{64}}$' THEN RAISE EXCEPTION 'DIRECT_REGENERATE_STEP_UP_FAILURE_INVALID' USING ERRCODE='22023'; END IF;
  SELECT phone_digest_key_id,phone_digest INTO digest_key,digest_value
    FROM public.direct_institution_onboarding WHERE onboarding_id=p_onboarding_id;
  IF NOT FOUND THEN RETURN NULL; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_actor_scope||E'\\000'||p_idempotency_key,0));
  PERFORM pg_advisory_xact_lock(hashtextextended(digest_key||E'\\000'||digest_value,0));
  SELECT * INTO actor_row FROM public."user" WHERE id=p_actor_user_id FOR SHARE;
  IF NOT FOUND OR actor_row.role::TEXT<>'super_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id IS NOT NULL THEN RETURN NULL; END IF;
  SELECT * INTO profile_row FROM public.platform_admin_security_profile WHERE user_id=p_actor_user_id FOR UPDATE;
  IF NOT FOUND OR NOT profile_row.enabled OR profile_row.profile_version<>p_observed_profile_version THEN RETURN NULL; END IF;
  SELECT * INTO root FROM public.direct_institution_onboarding WHERE onboarding_id=p_onboarding_id FOR UPDATE;
  IF NOT FOUND OR root.status<>'PENDING_ACTIVATION' OR root.phone_digest_key_id<>digest_key
     OR root.phone_digest<>digest_value THEN RETURN NULL; END IF;
  IF EXISTS(SELECT 1 FROM public.direct_onboarding_audit a WHERE a.actor_user_id=p_actor_user_id
    AND a.action='DIRECT_REGENERATE_STEP_UP_FAILURE' AND a.object_id=p_operation_id
    AND a.evidence_digest=p_request_digest) THEN
    RETURN jsonb_build_object('failed_attempts',profile_row.failed_attempts,'locked_until',profile_row.locked_until);
  END IF;
  next_failed:=LEAST(profile_row.failed_attempts+1,5);
  next_locked:=CASE WHEN next_failed>=5 THEN clock_timestamp()+INTERVAL '15 minutes' ELSE profile_row.locked_until END;
  UPDATE public.platform_admin_security_profile SET failed_attempts=next_failed,locked_until=next_locked,
    updated_at=clock_timestamp(),profile_version=profile_version+1 WHERE user_id=profile_row.user_id;
  INSERT INTO public.direct_onboarding_audit(audit_id,actor_user_id,actor_kind,action,object_id,result,reason_code,evidence_digest)
  VALUES(p_failure_id,actor_row.id,'SUPER_ADMIN','DIRECT_REGENERATE_STEP_UP_FAILURE',p_operation_id,
    'DENIED','STEP_UP_FORBIDDEN',p_request_digest);
  RETURN jsonb_build_object('failed_attempts',next_failed,'locked_until',next_locked,'failed_time_step',p_failed_time_step,
    'operation_id',p_operation_id);
END $$;

CREATE FUNCTION public.direct_revoke_step_up_begin_v1(
  p_actor_user_id BIGINT,p_onboarding_id UUID,p_actor_scope VARCHAR,p_idempotency_key VARCHAR,p_request_digest CHAR
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor_row public."user"%ROWTYPE; profile_row public.platform_admin_security_profile%ROWTYPE;
        root public.direct_institution_onboarding%ROWTYPE; digest_key TEXT; digest_value TEXT;
        active_credential_id UUID;
BEGIN
  IF session_user<>'{review}' THEN RAISE EXCEPTION 'DIRECT_REVOKE_STEP_UP_FORBIDDEN' USING ERRCODE='42501'; END IF;
  SELECT phone_digest_key_id,phone_digest INTO digest_key,digest_value
    FROM public.direct_institution_onboarding WHERE onboarding_id=p_onboarding_id;
  IF NOT FOUND THEN RETURN NULL; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_actor_scope||E'\\000'||p_idempotency_key,0));
  PERFORM pg_advisory_xact_lock(hashtextextended(digest_key||E'\\000'||digest_value,0));
  SELECT * INTO actor_row FROM public."user" WHERE id=p_actor_user_id FOR SHARE;
  IF NOT FOUND OR actor_row.role::TEXT<>'super_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id IS NOT NULL THEN RETURN NULL; END IF;
  SELECT * INTO profile_row FROM public.platform_admin_security_profile WHERE user_id=p_actor_user_id FOR UPDATE;
  IF NOT FOUND OR NOT profile_row.enabled
     OR (profile_row.locked_until IS NOT NULL AND profile_row.locked_until>clock_timestamp()) THEN RETURN NULL; END IF;
  SELECT * INTO root FROM public.direct_institution_onboarding WHERE onboarding_id=p_onboarding_id FOR UPDATE;
  IF NOT FOUND OR root.status<>'PENDING_ACTIVATION' OR root.phone_digest_key_id<>digest_key
     OR root.phone_digest<>digest_value THEN RETURN NULL; END IF;
  SELECT credential_id INTO active_credential_id
    FROM public.direct_institution_activation_credential
   WHERE onboarding_id=root.onboarding_id AND status='ISSUED' FOR UPDATE;
  IF NOT FOUND OR EXISTS(
    SELECT 1 FROM public.direct_institution_activation_credential
     WHERE onboarding_id=root.onboarding_id AND status='ISSUED'
       AND credential_id<>active_credential_id
  ) THEN RETURN NULL; END IF;
  RETURN jsonb_build_object('actor_user_id',actor_row.id,'profile_secret_ciphertext',replace(encode(profile_row.secret_ciphertext,'base64'),E'\n',''),
    'profile_key_id',profile_row.key_id,'profile_version',profile_row.profile_version,
    'failed_attempts',profile_row.failed_attempts,'locked_until',profile_row.locked_until,
    'last_accepted_time_step',profile_row.last_accepted_time_step,'target_version',root.version,
    'active_credential_id',active_credential_id,
    'request_digest',p_request_digest);
END $$;

CREATE FUNCTION public.direct_revoke_step_up_failure_v1(
  p_actor_user_id BIGINT,p_onboarding_id UUID,p_actor_scope VARCHAR,p_idempotency_key VARCHAR,
  p_operation_id UUID,p_request_digest CHAR,p_observed_profile_version BIGINT,p_failed_time_step BIGINT,p_failure_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor_row public."user"%ROWTYPE; profile_row public.platform_admin_security_profile%ROWTYPE;
        root public.direct_institution_onboarding%ROWTYPE; digest_key TEXT; digest_value TEXT;
        next_failed SMALLINT; next_locked TIMESTAMPTZ;
BEGIN
  IF session_user<>'{review}' THEN RAISE EXCEPTION 'DIRECT_REVOKE_STEP_UP_FAILURE_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF get_byte(uuid_send(p_operation_id),6) >> 4 <> 7 OR get_byte(uuid_send(p_failure_id),6) >> 4 <> 7
     OR p_request_digest !~ '^[0-9a-f]{{64}}$' THEN RAISE EXCEPTION 'DIRECT_REVOKE_STEP_UP_FAILURE_INVALID' USING ERRCODE='22023'; END IF;
  SELECT phone_digest_key_id,phone_digest INTO digest_key,digest_value
    FROM public.direct_institution_onboarding WHERE onboarding_id=p_onboarding_id;
  IF NOT FOUND THEN RETURN NULL; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_actor_scope||E'\\000'||p_idempotency_key,0));
  PERFORM pg_advisory_xact_lock(hashtextextended(digest_key||E'\\000'||digest_value,0));
  SELECT * INTO actor_row FROM public."user" WHERE id=p_actor_user_id FOR SHARE;
  IF NOT FOUND OR actor_row.role::TEXT<>'super_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id IS NOT NULL THEN RETURN NULL; END IF;
  SELECT * INTO profile_row FROM public.platform_admin_security_profile WHERE user_id=p_actor_user_id FOR UPDATE;
  IF NOT FOUND OR NOT profile_row.enabled OR profile_row.profile_version<>p_observed_profile_version THEN RETURN NULL; END IF;
  SELECT * INTO root FROM public.direct_institution_onboarding WHERE onboarding_id=p_onboarding_id FOR UPDATE;
  IF NOT FOUND OR root.status<>'PENDING_ACTIVATION' OR root.phone_digest_key_id<>digest_key
     OR root.phone_digest<>digest_value THEN RETURN NULL; END IF;
  IF EXISTS(SELECT 1 FROM public.direct_onboarding_audit a WHERE a.actor_user_id=p_actor_user_id
    AND a.action='DIRECT_REVOKE_STEP_UP_FAILURE' AND a.object_id=p_operation_id
    AND a.evidence_digest=p_request_digest) THEN
    RETURN jsonb_build_object('failed_attempts',profile_row.failed_attempts,'locked_until',profile_row.locked_until);
  END IF;
  next_failed:=LEAST(profile_row.failed_attempts+1,5);
  next_locked:=CASE WHEN next_failed>=5 THEN clock_timestamp()+INTERVAL '15 minutes' ELSE profile_row.locked_until END;
  UPDATE public.platform_admin_security_profile SET failed_attempts=next_failed,locked_until=next_locked,
    updated_at=clock_timestamp(),profile_version=profile_version+1 WHERE user_id=profile_row.user_id;
  INSERT INTO public.direct_onboarding_audit(audit_id,actor_user_id,actor_kind,action,object_id,result,reason_code,evidence_digest)
  VALUES(p_failure_id,actor_row.id,'SUPER_ADMIN','DIRECT_REVOKE_STEP_UP_FAILURE',p_operation_id,
    'DENIED','STEP_UP_FORBIDDEN',p_request_digest);
  RETURN jsonb_build_object('failed_attempts',next_failed,'locked_until',next_locked,'failed_time_step',p_failed_time_step,
    'operation_id',p_operation_id);
END $$;

CREATE FUNCTION public.admin_handoff_create_step_up_begin_v1(
  p_actor_user_id BIGINT,p_onboarding_id UUID,p_actor_scope VARCHAR,p_idempotency_key VARCHAR,
  p_request_digest CHAR,p_phone_digest_key_id VARCHAR,p_phone_digest CHAR
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor_row public."user"%ROWTYPE; old_admin public."user"%ROWTYPE;
        profile_row public.platform_admin_security_profile%ROWTYPE;
        root public.direct_institution_onboarding%ROWTYPE; tenant_row public.tenant%ROWTYPE;
        old_user BIGINT; tenant_value BIGINT;
BEGIN
  IF session_user<>'{review}' THEN RAISE EXCEPTION 'HANDOFF_CREATE_STEP_UP_FORBIDDEN' USING ERRCODE='42501'; END IF;
  SELECT activated_user_id,tenant_id INTO old_user,tenant_value
    FROM public.direct_institution_onboarding WHERE onboarding_id=p_onboarding_id;
  IF NOT FOUND OR old_user IS NULL THEN RETURN NULL; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_actor_scope||E'\\000'||p_idempotency_key,0));
  SELECT * INTO actor_row FROM public."user" WHERE id=p_actor_user_id FOR SHARE;
  IF NOT FOUND OR actor_row.role::TEXT<>'super_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id IS NOT NULL THEN RETURN NULL; END IF;
  SELECT * INTO profile_row FROM public.platform_admin_security_profile
   WHERE user_id=p_actor_user_id FOR UPDATE;
  IF NOT FOUND OR NOT profile_row.enabled
     OR (profile_row.locked_until IS NOT NULL AND profile_row.locked_until>clock_timestamp()) THEN RETURN NULL; END IF;
  SELECT * INTO old_admin FROM public."user" WHERE id=old_user FOR SHARE;
  IF NOT FOUND OR old_admin.role::TEXT<>'org_admin' OR old_admin.status::TEXT<>'active'
     OR old_admin.tenant_id<>tenant_value THEN RETURN NULL; END IF;
  SELECT * INTO tenant_row FROM public.tenant WHERE id=tenant_value FOR SHARE;
  IF NOT FOUND OR tenant_row.status::TEXT<>'active' THEN RETURN NULL; END IF;
  SELECT * INTO root FROM public.direct_institution_onboarding WHERE onboarding_id=p_onboarding_id FOR UPDATE;
  IF NOT FOUND OR root.activated_user_id<>old_admin.id OR root.tenant_id<>tenant_row.id
     OR root.status IN ('PENDING_ACTIVATION','REVOKED_BEFORE_ACTIVATION') THEN RETURN NULL; END IF;
  RETURN jsonb_build_object(
    'actor_user_id',actor_row.id,'profile_secret_ciphertext',replace(encode(profile_row.secret_ciphertext,'base64'),E'\n',''),
    'profile_key_id',profile_row.key_id,'profile_version',profile_row.profile_version,
    'failed_attempts',profile_row.failed_attempts,'locked_until',profile_row.locked_until,
    'last_accepted_time_step',profile_row.last_accepted_time_step,'target_version',root.version,
    'onboarding_id',root.onboarding_id,'tenant_id',root.tenant_id,
    'tenant_public_id',root.tenant_public_id,'request_digest',p_request_digest
  );
END $$;

CREATE FUNCTION public.admin_handoff_create_step_up_failure_v1(
  p_actor_user_id BIGINT,p_onboarding_id UUID,p_actor_scope VARCHAR,p_idempotency_key VARCHAR,p_operation_id UUID,
  p_request_digest CHAR,p_observed_profile_version BIGINT,p_failed_time_step BIGINT,p_failure_id UUID,
  p_phone_digest_key_id VARCHAR,p_phone_digest CHAR
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor_row public."user"%ROWTYPE; old_admin public."user"%ROWTYPE;
        profile_row public.platform_admin_security_profile%ROWTYPE; tenant_row public.tenant%ROWTYPE;
        root public.direct_institution_onboarding%ROWTYPE; old_user BIGINT; tenant_value BIGINT;
        next_failed SMALLINT; next_locked TIMESTAMPTZ;
BEGIN
  IF session_user<>'{review}' THEN RAISE EXCEPTION 'HANDOFF_CREATE_STEP_UP_FAILURE_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF get_byte(uuid_send(p_operation_id),6) >> 4 <> 7 OR get_byte(uuid_send(p_failure_id),6) >> 4 <> 7
     OR p_request_digest !~ '^[0-9a-f]{{64}}$' OR p_phone_digest !~ '^[0-9a-f]{{64}}$' THEN
    RAISE EXCEPTION 'HANDOFF_CREATE_STEP_UP_FAILURE_INVALID' USING ERRCODE='22023'; END IF;
  SELECT activated_user_id,tenant_id INTO old_user,tenant_value
    FROM public.direct_institution_onboarding WHERE onboarding_id=p_onboarding_id;
  IF NOT FOUND OR old_user IS NULL THEN RETURN NULL; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_actor_scope||E'\\000'||p_idempotency_key,0));
  SELECT * INTO actor_row FROM public."user" WHERE id=p_actor_user_id FOR SHARE;
  IF NOT FOUND OR actor_row.role::TEXT<>'super_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id IS NOT NULL THEN RETURN NULL; END IF;
  SELECT * INTO profile_row FROM public.platform_admin_security_profile WHERE user_id=p_actor_user_id FOR UPDATE;
  IF NOT FOUND OR NOT profile_row.enabled OR profile_row.profile_version<>p_observed_profile_version THEN RETURN NULL; END IF;
  SELECT * INTO old_admin FROM public."user" WHERE id=old_user FOR SHARE;
  IF NOT FOUND OR old_admin.role::TEXT<>'org_admin' OR old_admin.status::TEXT<>'active'
     OR old_admin.tenant_id<>tenant_value THEN RETURN NULL; END IF;
  SELECT * INTO tenant_row FROM public.tenant WHERE id=tenant_value FOR SHARE;
  IF NOT FOUND OR tenant_row.status::TEXT<>'active' THEN RETURN NULL; END IF;
  SELECT * INTO root FROM public.direct_institution_onboarding WHERE onboarding_id=p_onboarding_id FOR UPDATE;
  IF NOT FOUND OR root.activated_user_id<>old_user OR root.tenant_id<>tenant_value THEN RETURN NULL; END IF;
  IF EXISTS(SELECT 1 FROM public.direct_onboarding_audit a WHERE a.actor_user_id=p_actor_user_id
    AND a.action='ADMIN_HANDOFF_CREATE_STEP_UP_FAILURE' AND a.object_id=p_operation_id
    AND a.evidence_digest=p_request_digest) THEN
    RETURN jsonb_build_object('failed_attempts',profile_row.failed_attempts,'locked_until',profile_row.locked_until);
  END IF;
  next_failed:=LEAST(profile_row.failed_attempts+1,5);
  next_locked:=CASE WHEN next_failed>=5 THEN clock_timestamp()+INTERVAL '15 minutes' ELSE profile_row.locked_until END;
  UPDATE public.platform_admin_security_profile SET failed_attempts=next_failed,locked_until=next_locked,
    updated_at=clock_timestamp(),profile_version=profile_version+1 WHERE user_id=profile_row.user_id;
  INSERT INTO public.direct_onboarding_audit(audit_id,actor_user_id,actor_kind,action,object_id,result,reason_code,evidence_digest)
  VALUES(p_failure_id,actor_row.id,'SUPER_ADMIN','ADMIN_HANDOFF_CREATE_STEP_UP_FAILURE',p_operation_id,
    'DENIED','STEP_UP_FORBIDDEN',p_request_digest);
  RETURN jsonb_build_object('failed_attempts',next_failed,'locked_until',next_locked,'failed_time_step',p_failed_time_step,
    'operation_id',p_operation_id);
END $$;

CREATE FUNCTION public.direct_compliance_decide_step_up_begin_v1(
  p_actor_user_id BIGINT,p_onboarding_id UUID,p_revision_id UUID,p_actor_scope VARCHAR,
  p_idempotency_key VARCHAR,p_request_digest CHAR
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor_row public."user"%ROWTYPE; profile_row public.platform_admin_security_profile%ROWTYPE;
        root public.direct_institution_onboarding%ROWTYPE; revision_row public.direct_institution_compliance_revision%ROWTYPE;
        tenant_row public.tenant%ROWTYPE; digest_key TEXT; digest_value TEXT;
BEGIN
  IF session_user<>'{review}' THEN RAISE EXCEPTION 'DIRECT_COMPLIANCE_DECIDE_STEP_UP_FORBIDDEN' USING ERRCODE='42501'; END IF;
  SELECT phone_digest_key_id,phone_digest INTO digest_key,digest_value
    FROM public.direct_institution_onboarding WHERE onboarding_id=p_onboarding_id;
  IF NOT FOUND THEN RETURN NULL; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_actor_scope||E'\\000'||p_idempotency_key,0));
  PERFORM pg_advisory_xact_lock(hashtextextended(digest_key||E'\\000'||digest_value,0));
  SELECT * INTO actor_row FROM public."user" WHERE id=p_actor_user_id FOR SHARE;
  IF NOT FOUND OR actor_row.role::TEXT<>'super_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id IS NOT NULL THEN RETURN NULL; END IF;
  SELECT * INTO profile_row FROM public.platform_admin_security_profile WHERE user_id=p_actor_user_id FOR UPDATE;
  IF NOT FOUND OR NOT profile_row.enabled
     OR (profile_row.locked_until IS NOT NULL AND profile_row.locked_until>clock_timestamp()) THEN RETURN NULL; END IF;
  SELECT * INTO tenant_row FROM public.tenant
   WHERE id=(SELECT tenant_id FROM public.direct_institution_onboarding WHERE onboarding_id=p_onboarding_id) FOR SHARE;
  IF NOT FOUND OR tenant_row.status<>'active' THEN RETURN NULL; END IF;
  SELECT * INTO root FROM public.direct_institution_onboarding WHERE onboarding_id=p_onboarding_id FOR UPDATE;
  IF NOT FOUND OR root.tenant_id<>tenant_row.id OR root.current_revision_id<>p_revision_id
     OR root.phone_digest_key_id<>digest_key OR root.phone_digest<>digest_value
     OR root.status<>'COMPLIANCE_UNDER_REVIEW' OR root.created_by=actor_row.id THEN RETURN NULL; END IF;
  SELECT * INTO revision_row FROM public.direct_institution_compliance_revision
   WHERE revision_id=p_revision_id AND onboarding_id=p_onboarding_id FOR UPDATE;
  IF NOT FOUND OR revision_row.status NOT IN ('SUBMITTED','UNDER_REVIEW') THEN RETURN NULL; END IF;
  RETURN jsonb_build_object('actor_user_id',actor_row.id,'profile_secret_ciphertext',replace(encode(profile_row.secret_ciphertext,'base64'),E'\n',''),
    'profile_key_id',profile_row.key_id,'profile_version',profile_row.profile_version,
    'failed_attempts',profile_row.failed_attempts,'locked_until',profile_row.locked_until,
    'last_accepted_time_step',profile_row.last_accepted_time_step,'target_version',root.version,
    'tenant_id',root.tenant_id,'tenant_public_id',root.tenant_public_id,
    'onboarding_id',root.onboarding_id,'revision_id',revision_row.revision_id,'revision_no',revision_row.revision_no,
    'institution_name',root.institution_name,'institution_type',root.institution_type,
    'administrative_region_id',root.administrative_region_id,'institution_code',root.institution_code,
    'service_tags',revision_row.service_tags,'correction_fields',revision_row.correction_fields,
    'compliance_payload_ciphertext',replace(encode(revision_row.compliance_payload_ciphertext,'base64'),E'\n',''),
    'compliance_payload_key_id',revision_row.compliance_payload_key_id,
    'compliance_payload_digest_key_id',revision_row.compliance_payload_digest_key_id,
    'compliance_payload_digest',revision_row.compliance_payload_digest,
    'licenses',(SELECT COALESCE(jsonb_agg(jsonb_build_object(
      'license_id',license.license_id,'license_type',license.license_type,'license_no',NULL,
      'private_file_id',license.private_file_id,'valid_from',license.valid_from,'valid_until',license.valid_until)
      ORDER BY license.license_id),'[]'::jsonb)
      FROM public.direct_institution_license license WHERE license.revision_id=revision_row.revision_id),
    'request_digest',p_request_digest);
END $$;

CREATE FUNCTION public.direct_compliance_decide_step_up_failure_v1(
  p_actor_user_id BIGINT,p_onboarding_id UUID,p_revision_id UUID,p_actor_scope VARCHAR,p_idempotency_key VARCHAR,
  p_operation_id UUID,p_request_digest CHAR,p_observed_profile_version BIGINT,p_failed_time_step BIGINT,p_failure_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor_row public."user"%ROWTYPE; profile_row public.platform_admin_security_profile%ROWTYPE;
        root public.direct_institution_onboarding%ROWTYPE; revision_row public.direct_institution_compliance_revision%ROWTYPE;
        tenant_row public.tenant%ROWTYPE; digest_key TEXT; digest_value TEXT;
        next_failed SMALLINT; next_locked TIMESTAMPTZ;
BEGIN
  IF session_user<>'{review}' THEN RAISE EXCEPTION 'DIRECT_COMPLIANCE_DECIDE_STEP_UP_FAILURE_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF get_byte(uuid_send(p_operation_id),6) >> 4 <> 7 OR get_byte(uuid_send(p_failure_id),6) >> 4 <> 7
     OR p_request_digest !~ '^[0-9a-f]{{64}}$' THEN RAISE EXCEPTION 'DIRECT_COMPLIANCE_DECIDE_STEP_UP_FAILURE_INVALID' USING ERRCODE='22023'; END IF;
  SELECT phone_digest_key_id,phone_digest INTO digest_key,digest_value
    FROM public.direct_institution_onboarding WHERE onboarding_id=p_onboarding_id;
  IF NOT FOUND THEN RETURN NULL; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_actor_scope||E'\\000'||p_idempotency_key,0));
  PERFORM pg_advisory_xact_lock(hashtextextended(digest_key||E'\\000'||digest_value,0));
  SELECT * INTO actor_row FROM public."user" WHERE id=p_actor_user_id FOR SHARE;
  IF NOT FOUND OR actor_row.role::TEXT<>'super_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id IS NOT NULL THEN RETURN NULL; END IF;
  SELECT * INTO profile_row FROM public.platform_admin_security_profile WHERE user_id=p_actor_user_id FOR UPDATE;
  IF NOT FOUND OR NOT profile_row.enabled OR profile_row.profile_version<>p_observed_profile_version THEN RETURN NULL; END IF;
  SELECT * INTO tenant_row FROM public.tenant
   WHERE id=(SELECT tenant_id FROM public.direct_institution_onboarding WHERE onboarding_id=p_onboarding_id) FOR SHARE;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT * INTO root FROM public.direct_institution_onboarding WHERE onboarding_id=p_onboarding_id FOR UPDATE;
  IF NOT FOUND OR root.tenant_id<>tenant_row.id OR root.current_revision_id<>p_revision_id
     OR root.phone_digest_key_id<>digest_key OR root.phone_digest<>digest_value THEN RETURN NULL; END IF;
  SELECT * INTO revision_row FROM public.direct_institution_compliance_revision
   WHERE revision_id=p_revision_id AND onboarding_id=p_onboarding_id FOR UPDATE;
  IF NOT FOUND OR revision_row.status NOT IN ('SUBMITTED','UNDER_REVIEW') THEN RETURN NULL; END IF;
  IF EXISTS(SELECT 1 FROM public.direct_onboarding_audit a WHERE a.actor_user_id=p_actor_user_id
    AND a.action='DIRECT_COMPLIANCE_DECIDE_STEP_UP_FAILURE' AND a.object_id=p_operation_id
    AND a.evidence_digest=p_request_digest) THEN
    RETURN jsonb_build_object('failed_attempts',profile_row.failed_attempts,'locked_until',profile_row.locked_until);
  END IF;
  next_failed:=LEAST(profile_row.failed_attempts+1,5);
  next_locked:=CASE WHEN next_failed>=5 THEN clock_timestamp()+INTERVAL '15 minutes' ELSE profile_row.locked_until END;
  UPDATE public.platform_admin_security_profile SET failed_attempts=next_failed,locked_until=next_locked,
    updated_at=clock_timestamp(),profile_version=profile_version+1 WHERE user_id=profile_row.user_id;
  INSERT INTO public.direct_onboarding_audit(audit_id,actor_user_id,actor_kind,action,object_id,result,reason_code,evidence_digest)
  VALUES(p_failure_id,actor_row.id,'SUPER_ADMIN','DIRECT_COMPLIANCE_DECIDE_STEP_UP_FAILURE',p_operation_id,
    'DENIED','STEP_UP_FORBIDDEN',p_request_digest);
  RETURN jsonb_build_object('failed_attempts',next_failed,'locked_until',next_locked,'failed_time_step',p_failed_time_step,
    'operation_id',p_operation_id);
END $$;

CREATE FUNCTION public.admin_handoff_regenerate_step_up_begin_v1(
  p_actor_user_id BIGINT,p_handoff_id UUID,p_actor_scope VARCHAR,p_idempotency_key VARCHAR,p_request_digest CHAR
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor_row public."user"%ROWTYPE; old_admin public."user"%ROWTYPE;
        profile_row public.platform_admin_security_profile%ROWTYPE; tenant_row public.tenant%ROWTYPE;
        root public.direct_institution_onboarding%ROWTYPE; handoff_row public.institution_admin_handoff%ROWTYPE;
        onboarding_value UUID; old_user BIGINT; tenant_value BIGINT; digest_key TEXT; digest_value TEXT;
BEGIN
  IF session_user<>'{review}' THEN RAISE EXCEPTION 'HANDOFF_REGENERATE_STEP_UP_FORBIDDEN' USING ERRCODE='42501'; END IF;
  SELECT h.onboarding_id,h.new_phone_digest_key_id,h.new_phone_digest,r.activated_user_id,r.tenant_id
    INTO onboarding_value,digest_key,digest_value,old_user,tenant_value
    FROM public.institution_admin_handoff h JOIN public.direct_institution_onboarding r ON r.onboarding_id=h.onboarding_id
   WHERE h.handoff_id=p_handoff_id;
  IF NOT FOUND OR old_user IS NULL THEN RETURN NULL; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_actor_scope||E'\\000'||p_idempotency_key,0));
  PERFORM pg_advisory_xact_lock(hashtextextended(digest_key||E'\\000'||digest_value,0));
  SELECT * INTO actor_row FROM public."user" WHERE id=p_actor_user_id FOR SHARE;
  IF NOT FOUND OR actor_row.role::TEXT<>'super_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id IS NOT NULL THEN RETURN NULL; END IF;
  SELECT * INTO profile_row FROM public.platform_admin_security_profile WHERE user_id=p_actor_user_id FOR UPDATE;
  IF NOT FOUND OR NOT profile_row.enabled
     OR (profile_row.locked_until IS NOT NULL AND profile_row.locked_until>clock_timestamp()) THEN RETURN NULL; END IF;
  SELECT * INTO old_admin FROM public."user" WHERE id=old_user FOR SHARE;
  IF NOT FOUND OR old_admin.role::TEXT<>'org_admin' OR old_admin.status::TEXT<>'active'
     OR old_admin.tenant_id<>tenant_value THEN RETURN NULL; END IF;
  SELECT * INTO tenant_row FROM public.tenant WHERE id=tenant_value FOR SHARE;
  IF NOT FOUND OR tenant_row.status::TEXT<>'active' THEN RETURN NULL; END IF;
  SELECT * INTO root FROM public.direct_institution_onboarding WHERE onboarding_id=onboarding_value FOR UPDATE;
  IF NOT FOUND OR root.activated_user_id<>old_user OR root.tenant_id<>tenant_value THEN RETURN NULL; END IF;
  SELECT * INTO handoff_row FROM public.institution_admin_handoff WHERE handoff_id=p_handoff_id FOR UPDATE;
  IF NOT FOUND OR handoff_row.status<>'ISSUED' OR handoff_row.onboarding_id<>root.onboarding_id
     OR handoff_row.new_phone_digest_key_id<>digest_key OR handoff_row.new_phone_digest<>digest_value THEN RETURN NULL; END IF;
  RETURN jsonb_build_object('actor_user_id',actor_row.id,'profile_secret_ciphertext',replace(encode(profile_row.secret_ciphertext,'base64'),E'\n',''),
    'profile_key_id',profile_row.key_id,'profile_version',profile_row.profile_version,
    'failed_attempts',profile_row.failed_attempts,'locked_until',profile_row.locked_until,
    'last_accepted_time_step',profile_row.last_accepted_time_step,'target_version',handoff_row.version,
    'onboarding_id',root.onboarding_id,'handoff_id',handoff_row.handoff_id,
    'tenant_id',root.tenant_id,'tenant_public_id',root.tenant_public_id,
    'active_credential_id',(SELECT credential_id FROM public.institution_admin_handoff_credential
      WHERE handoff_id=handoff_row.handoff_id AND status='ISSUED' ORDER BY issued_at DESC LIMIT 1),
    'request_digest',p_request_digest);
END $$;

CREATE FUNCTION public.admin_handoff_revoke_step_up_begin_v1(
  p_actor_user_id BIGINT,p_handoff_id UUID,p_actor_scope VARCHAR,p_idempotency_key VARCHAR,p_request_digest CHAR
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE preimage JSONB;
BEGIN
  IF session_user<>'{review}' THEN RAISE EXCEPTION 'HANDOFF_REVOKE_STEP_UP_FORBIDDEN' USING ERRCODE='42501'; END IF;
  SELECT public.admin_handoff_regenerate_step_up_begin_v1(
    p_actor_user_id,p_handoff_id,p_actor_scope,p_idempotency_key,p_request_digest) INTO preimage;
  RETURN preimage;
END $$;

CREATE FUNCTION public.admin_handoff_regenerate_step_up_failure_v1(
  p_actor_user_id BIGINT,p_handoff_id UUID,p_actor_scope VARCHAR,p_idempotency_key VARCHAR,p_operation_id UUID,
  p_request_digest CHAR,p_observed_profile_version BIGINT,p_failed_time_step BIGINT,p_failure_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE preimage JSONB; profile_row public.platform_admin_security_profile%ROWTYPE;
        next_failed SMALLINT; next_locked TIMESTAMPTZ;
BEGIN
  IF session_user<>'{review}' THEN RAISE EXCEPTION 'HANDOFF_REGENERATE_STEP_UP_FAILURE_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF get_byte(uuid_send(p_operation_id),6) >> 4 <> 7 OR get_byte(uuid_send(p_failure_id),6) >> 4 <> 7
     OR p_request_digest !~ '^[0-9a-f]{{64}}$' THEN RAISE EXCEPTION 'HANDOFF_REGENERATE_STEP_UP_FAILURE_INVALID' USING ERRCODE='22023'; END IF;
  SELECT public.admin_handoff_regenerate_step_up_begin_v1(
    p_actor_user_id,p_handoff_id,p_actor_scope,p_idempotency_key,p_request_digest) INTO preimage;
  IF preimage IS NULL THEN RETURN NULL; END IF;
  SELECT * INTO profile_row FROM public.platform_admin_security_profile WHERE user_id=p_actor_user_id FOR UPDATE;
  IF profile_row.profile_version<>p_observed_profile_version THEN RETURN NULL; END IF;
  IF EXISTS(SELECT 1 FROM public.direct_onboarding_audit a WHERE a.actor_user_id=p_actor_user_id
    AND a.action='ADMIN_HANDOFF_REGENERATE_STEP_UP_FAILURE' AND a.object_id=p_operation_id
    AND a.evidence_digest=p_request_digest) THEN
    RETURN jsonb_build_object('failed_attempts',profile_row.failed_attempts,'locked_until',profile_row.locked_until);
  END IF;
  next_failed:=LEAST(profile_row.failed_attempts+1,5);
  next_locked:=CASE WHEN next_failed>=5 THEN clock_timestamp()+INTERVAL '15 minutes' ELSE profile_row.locked_until END;
  UPDATE public.platform_admin_security_profile SET failed_attempts=next_failed,locked_until=next_locked,
    updated_at=clock_timestamp(),profile_version=profile_version+1 WHERE user_id=profile_row.user_id;
  INSERT INTO public.direct_onboarding_audit(audit_id,actor_user_id,actor_kind,action,object_id,result,reason_code,evidence_digest)
  VALUES(p_failure_id,p_actor_user_id,'SUPER_ADMIN','ADMIN_HANDOFF_REGENERATE_STEP_UP_FAILURE',p_operation_id,
    'DENIED','STEP_UP_FORBIDDEN',p_request_digest);
  RETURN jsonb_build_object('failed_attempts',next_failed,'locked_until',next_locked,'failed_time_step',p_failed_time_step,
    'operation_id',p_operation_id);
END $$;

CREATE FUNCTION public.admin_handoff_revoke_step_up_failure_v1(
  p_actor_user_id BIGINT,p_handoff_id UUID,p_actor_scope VARCHAR,p_idempotency_key VARCHAR,p_operation_id UUID,
  p_request_digest CHAR,p_observed_profile_version BIGINT,p_failed_time_step BIGINT,p_failure_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE preimage JSONB; profile_row public.platform_admin_security_profile%ROWTYPE;
        next_failed SMALLINT; next_locked TIMESTAMPTZ;
BEGIN
  IF session_user<>'{review}' THEN RAISE EXCEPTION 'HANDOFF_REVOKE_STEP_UP_FAILURE_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF get_byte(uuid_send(p_operation_id),6) >> 4 <> 7 OR get_byte(uuid_send(p_failure_id),6) >> 4 <> 7
     OR p_request_digest !~ '^[0-9a-f]{{64}}$' THEN RAISE EXCEPTION 'HANDOFF_REVOKE_STEP_UP_FAILURE_INVALID' USING ERRCODE='22023'; END IF;
  SELECT public.admin_handoff_revoke_step_up_begin_v1(
    p_actor_user_id,p_handoff_id,p_actor_scope,p_idempotency_key,p_request_digest) INTO preimage;
  IF preimage IS NULL THEN RETURN NULL; END IF;
  SELECT * INTO profile_row FROM public.platform_admin_security_profile WHERE user_id=p_actor_user_id FOR UPDATE;
  IF profile_row.profile_version<>p_observed_profile_version THEN RETURN NULL; END IF;
  IF EXISTS(SELECT 1 FROM public.direct_onboarding_audit a WHERE a.actor_user_id=p_actor_user_id
    AND a.action='ADMIN_HANDOFF_REVOKE_STEP_UP_FAILURE' AND a.object_id=p_operation_id
    AND a.evidence_digest=p_request_digest) THEN
    RETURN jsonb_build_object('failed_attempts',profile_row.failed_attempts,'locked_until',profile_row.locked_until);
  END IF;
  next_failed:=LEAST(profile_row.failed_attempts+1,5);
  next_locked:=CASE WHEN next_failed>=5 THEN clock_timestamp()+INTERVAL '15 minutes' ELSE profile_row.locked_until END;
  UPDATE public.platform_admin_security_profile SET failed_attempts=next_failed,locked_until=next_locked,
    updated_at=clock_timestamp(),profile_version=profile_version+1 WHERE user_id=profile_row.user_id;
  INSERT INTO public.direct_onboarding_audit(audit_id,actor_user_id,actor_kind,action,object_id,result,reason_code,evidence_digest)
  VALUES(p_failure_id,p_actor_user_id,'SUPER_ADMIN','ADMIN_HANDOFF_REVOKE_STEP_UP_FAILURE',p_operation_id,
    'DENIED','STEP_UP_FORBIDDEN',p_request_digest);
  RETURN jsonb_build_object('failed_attempts',next_failed,'locked_until',next_locked,'failed_time_step',p_failed_time_step,
    'operation_id',p_operation_id);
END $$;

CREATE FUNCTION public.step_up_failure_commit_confirm_v1(
  p_failure_id UUID,p_actor_user_id BIGINT,p_operation_id UUID,p_request_digest CHAR
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF session_user<>'{review}' THEN RAISE EXCEPTION 'STEP_UP_FAILURE_CONFIRM_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF EXISTS(SELECT 1 FROM public.direct_onboarding_audit a
    WHERE a.audit_id=p_failure_id AND a.actor_user_id=p_actor_user_id AND a.object_id=p_operation_id
      AND a.result='DENIED' AND a.reason_code='STEP_UP_FORBIDDEN' AND a.evidence_digest=p_request_digest
      AND a.action IN ('DIRECT_CREATE_STEP_UP_FAILURE','DIRECT_REGENERATE_STEP_UP_FAILURE',
        'DIRECT_REVOKE_STEP_UP_FAILURE','DIRECT_COMPLIANCE_DECIDE_STEP_UP_FAILURE',
        'ADMIN_HANDOFF_CREATE_STEP_UP_FAILURE','ADMIN_HANDOFF_REGENERATE_STEP_UP_FAILURE',
        'ADMIN_HANDOFF_REVOKE_STEP_UP_FAILURE')) THEN
    RETURN jsonb_build_object('outcome','COMMITTED');
  END IF;
  IF NOT EXISTS(SELECT 1 FROM public.direct_onboarding_audit a WHERE a.audit_id=p_failure_id) THEN
    RETURN jsonb_build_object('outcome','NOT_COMMITTED');
  END IF;
  RETURN jsonb_build_object('outcome','UNKNOWN');
END $$;

CREATE FUNCTION public.direct_activation_regenerate_v1(p_envelope JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor_row public."user"%ROWTYPE; profile_row public.platform_admin_security_profile%ROWTYPE;
        root public.direct_institution_onboarding%ROWTYPE; old_credential public.direct_institution_activation_credential%ROWTYPE;
        accepted_step BIGINT; current_step BIGINT; now_value TIMESTAMPTZ:=clock_timestamp(); expires_value TIMESTAMPTZ;
        response_value JSONB;
BEGIN
  IF session_user<>'{review}' THEN RAISE EXCEPTION 'DIRECT_REGENERATE_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF jsonb_typeof(p_envelope)<>'object' OR NOT p_envelope ?& ARRAY[
       'operation_id','actor_user_id','actor_role','actor_scope','idempotency_key','request_digest','digest_key_id','expected_version',
       'audit_id','event_id','receipt_id','expected_postimage_digest','accepted_totp_step','onboarding_id',
       'old_credential_id','new_credential_id','new_credential_digest_key_id','new_credential_digest','reason_code']
     OR (p_envelope-ARRAY[
       'operation_id','actor_user_id','actor_role','actor_scope','idempotency_key','request_digest','digest_key_id','expected_version',
       'audit_id','event_id','receipt_id','expected_postimage_digest','accepted_totp_step','onboarding_id',
       'old_credential_id','new_credential_id','new_credential_digest_key_id','new_credential_digest','reason_code'])<>'{{}}'::jsonb THEN
    RAISE EXCEPTION 'DIRECT_REGENERATE_ENVELOPE_INVALID' USING ERRCODE='22023';
  END IF;
  IF jsonb_typeof(p_envelope->'accepted_totp_step')<>'number'
     OR (p_envelope->>'accepted_totp_step')!~'^(0|[1-9][0-9]*)$' THEN
    RAISE EXCEPTION 'DIRECT_REGENERATE_ENVELOPE_INVALID' USING ERRCODE='22023';
  END IF;
  IF (p_envelope->>'accepted_totp_step')::NUMERIC>9223372036854775807 THEN
    RAISE EXCEPTION 'DIRECT_REGENERATE_ENVELOPE_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT * INTO root FROM public.direct_institution_onboarding WHERE onboarding_id=(p_envelope->>'onboarding_id')::UUID;
  IF NOT FOUND THEN RETURN NULL; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended((p_envelope->>'actor_scope')||E'\\000'||(p_envelope->>'idempotency_key'),0));
  PERFORM pg_advisory_xact_lock(hashtextextended(root.phone_digest_key_id||E'\\000'||root.phone_digest,0));
  SELECT * INTO actor_row FROM public."user" WHERE id=(p_envelope->>'actor_user_id')::BIGINT FOR SHARE;
  IF NOT FOUND OR actor_row.role::TEXT<>'super_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id IS NOT NULL OR p_envelope->>'actor_role'<>'super_admin' THEN RETURN NULL; END IF;
  SELECT * INTO profile_row FROM public.platform_admin_security_profile WHERE user_id=actor_row.id FOR UPDATE;
  SELECT * INTO root FROM public.direct_institution_onboarding WHERE onboarding_id=root.onboarding_id FOR UPDATE;
  accepted_step:=(p_envelope->>'accepted_totp_step')::BIGINT;
  current_step:=floor(extract(epoch FROM clock_timestamp())/30)::BIGINT;
  IF NOT FOUND OR NOT profile_row.enabled OR root.status<>'PENDING_ACTIVATION'
     OR root.version<>(p_envelope->>'expected_version')::BIGINT
     OR accepted_step<=COALESCE(profile_row.last_accepted_time_step,-1)
     OR NOT (accepted_step BETWEEN current_step-1 AND current_step+1) THEN RETURN NULL; END IF;
  SELECT * INTO old_credential FROM public.direct_institution_activation_credential
   WHERE credential_id=(p_envelope->>'old_credential_id')::UUID AND onboarding_id=root.onboarding_id FOR UPDATE;
  IF NOT FOUND OR old_credential.status<>'ISSUED' THEN RETURN NULL; END IF;
  UPDATE public.direct_institution_activation_credential SET status='REVOKED',version=version+1
   WHERE credential_id=old_credential.credential_id;
  expires_value:=(((now_value AT TIME ZONE 'Asia/Shanghai')::DATE+30)::TIMESTAMP AT TIME ZONE 'Asia/Shanghai');
  INSERT INTO public.direct_institution_activation_credential(
    credential_id,onboarding_id,credential_digest_key_id,credential_digest,status,issued_at,expires_at
  ) VALUES((p_envelope->>'new_credential_id')::UUID,root.onboarding_id,p_envelope->>'new_credential_digest_key_id',
    p_envelope->>'new_credential_digest','ISSUED',now_value,expires_value);
  UPDATE public.direct_institution_onboarding SET updated_at=now_value,version=version+1 WHERE onboarding_id=root.onboarding_id;
  response_value:=jsonb_build_object(
    'onboarding_id',root.onboarding_id,'tenant_id',root.tenant_public_id,
    'status',root.status,'version',root.version+1,'credential_delivery_state','ISSUED',
    'credential_id',p_envelope->>'new_credential_id','credential_issued_at',now_value,
    'credential_expires_at',expires_value);
  INSERT INTO public.direct_onboarding_audit(audit_id,actor_user_id,actor_kind,action,object_id,result,reason_code,evidence_digest)
  VALUES((p_envelope->>'audit_id')::UUID,actor_row.id,'SUPER_ADMIN','DIRECT_ACTIVATION_REGENERATE',root.onboarding_id,
    'SUCCESS',p_envelope->>'reason_code',p_envelope->>'expected_postimage_digest');
  INSERT INTO public.direct_onboarding_outbox(event_id,event_type,aggregate_id,payload,payload_digest)
  VALUES((p_envelope->>'event_id')::UUID,'DIRECT_ACTIVATION_REGENERATED',root.onboarding_id,
    jsonb_build_object('onboarding_id',root.onboarding_id),p_envelope->>'expected_postimage_digest');
  INSERT INTO public.direct_onboarding_receipt(receipt_id,actor_scope,operation,idempotency_key,digest_key_id,request_digest,postimage_digest,response_payload)
  VALUES((p_envelope->>'receipt_id')::UUID,p_envelope->>'actor_scope','REGENERATE',p_envelope->>'idempotency_key',
    p_envelope->>'digest_key_id',p_envelope->>'request_digest',p_envelope->>'expected_postimage_digest',response_value);
  UPDATE public.platform_admin_security_profile SET last_accepted_time_step=accepted_step,failed_attempts=0,
    locked_until=NULL,updated_at=now_value,profile_version=profile_version+1 WHERE user_id=profile_row.user_id;
  RETURN response_value;
END $$;

CREATE FUNCTION public.direct_institution_revoke_v1(p_envelope JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor_row public."user"%ROWTYPE; profile_row public.platform_admin_security_profile%ROWTYPE;
        root public.direct_institution_onboarding%ROWTYPE; claim public.identity_phone_claim%ROWTYPE;
        credential_row public.direct_institution_activation_credential%ROWTYPE;
        accepted_step BIGINT; current_step BIGINT; now_value TIMESTAMPTZ:=clock_timestamp();
BEGIN
  IF session_user<>'{review}' THEN RAISE EXCEPTION 'DIRECT_REVOKE_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF jsonb_typeof(p_envelope)<>'object' OR NOT p_envelope ?& ARRAY[
       'operation_id','actor_user_id','actor_role','actor_scope','idempotency_key','request_digest','digest_key_id','expected_version',
       'audit_id','event_id','receipt_id','expected_postimage_digest','accepted_totp_step','onboarding_id',
       'active_credential_id','reason_code']
     OR (p_envelope-ARRAY[
       'operation_id','actor_user_id','actor_role','actor_scope','idempotency_key','request_digest','digest_key_id','expected_version',
       'audit_id','event_id','receipt_id','expected_postimage_digest','accepted_totp_step','onboarding_id',
       'active_credential_id','reason_code'])<>'{{}}'::jsonb THEN
    RAISE EXCEPTION 'DIRECT_REVOKE_ENVELOPE_INVALID' USING ERRCODE='22023';
  END IF;
  IF jsonb_typeof(p_envelope->'accepted_totp_step')<>'number'
     OR (p_envelope->>'accepted_totp_step')!~'^(0|[1-9][0-9]*)$' THEN
    RAISE EXCEPTION 'DIRECT_REVOKE_ENVELOPE_INVALID' USING ERRCODE='22023';
  END IF;
  IF (p_envelope->>'accepted_totp_step')::NUMERIC>9223372036854775807 THEN
    RAISE EXCEPTION 'DIRECT_REVOKE_ENVELOPE_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT * INTO root FROM public.direct_institution_onboarding WHERE onboarding_id=(p_envelope->>'onboarding_id')::UUID;
  IF NOT FOUND THEN RETURN NULL; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended((p_envelope->>'actor_scope')||E'\\000'||(p_envelope->>'idempotency_key'),0));
  PERFORM pg_advisory_xact_lock(hashtextextended(root.phone_digest_key_id||E'\\000'||root.phone_digest,0));
  SELECT * INTO actor_row FROM public."user" WHERE id=(p_envelope->>'actor_user_id')::BIGINT FOR SHARE;
  IF NOT FOUND OR actor_row.role::TEXT<>'super_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id IS NOT NULL OR p_envelope->>'actor_role'<>'super_admin' THEN RETURN NULL; END IF;
  SELECT * INTO profile_row FROM public.platform_admin_security_profile WHERE user_id=actor_row.id FOR UPDATE;
  SELECT * INTO root FROM public.direct_institution_onboarding WHERE onboarding_id=root.onboarding_id FOR UPDATE;
  accepted_step:=(p_envelope->>'accepted_totp_step')::BIGINT;
  current_step:=floor(extract(epoch FROM clock_timestamp())/30)::BIGINT;
  IF NOT FOUND OR NOT profile_row.enabled OR root.status<>'PENDING_ACTIVATION'
     OR root.version<>(p_envelope->>'expected_version')::BIGINT
     OR accepted_step<=COALESCE(profile_row.last_accepted_time_step,-1)
     OR NOT (accepted_step BETWEEN current_step-1 AND current_step+1) THEN RETURN NULL; END IF;
  SELECT * INTO credential_row FROM public.direct_institution_activation_credential
   WHERE credential_id=(p_envelope->>'active_credential_id')::UUID AND onboarding_id=root.onboarding_id FOR UPDATE;
  IF NOT FOUND OR credential_row.status<>'ISSUED' THEN RETURN NULL; END IF;
  SELECT * INTO claim FROM public.identity_phone_claim
   WHERE claim_kind='DIRECT_ORG_ADMIN' AND claim_ref=root.onboarding_id FOR UPDATE;
  IF NOT FOUND OR claim.state<>'PENDING' THEN RETURN NULL; END IF;
  UPDATE public.direct_institution_activation_credential SET status='REVOKED',version=version+1
   WHERE credential_id=credential_row.credential_id;
  UPDATE public.identity_phone_claim SET state='RELEASED',updated_at=now_value,version=version+1
   WHERE claim_id=claim.claim_id;
  UPDATE public.direct_institution_onboarding SET status='REVOKED_BEFORE_ACTIVATION',updated_at=now_value,version=version+1
   WHERE onboarding_id=root.onboarding_id;
  INSERT INTO public.direct_onboarding_audit(audit_id,actor_user_id,actor_kind,action,object_id,result,reason_code,evidence_digest)
  VALUES((p_envelope->>'audit_id')::UUID,actor_row.id,'SUPER_ADMIN','DIRECT_INSTITUTION_REVOKE',root.onboarding_id,
    'SUCCESS',p_envelope->>'reason_code',p_envelope->>'expected_postimage_digest');
  INSERT INTO public.direct_onboarding_outbox(event_id,event_type,aggregate_id,payload,payload_digest)
  VALUES((p_envelope->>'event_id')::UUID,'DIRECT_INSTITUTION_REVOKED',root.onboarding_id,
    jsonb_build_object('onboarding_id',root.onboarding_id),p_envelope->>'expected_postimage_digest');
  INSERT INTO public.direct_onboarding_receipt(receipt_id,actor_scope,operation,idempotency_key,digest_key_id,request_digest,postimage_digest)
  VALUES((p_envelope->>'receipt_id')::UUID,p_envelope->>'actor_scope','REVOKE',p_envelope->>'idempotency_key',
    p_envelope->>'digest_key_id',p_envelope->>'request_digest',p_envelope->>'expected_postimage_digest');
  UPDATE public.platform_admin_security_profile SET last_accepted_time_step=accepted_step,failed_attempts=0,
    locked_until=NULL,updated_at=now_value,profile_version=profile_version+1 WHERE user_id=profile_row.user_id;
  RETURN jsonb_build_object('onboarding_id',root.onboarding_id,'tenant_id',root.tenant_public_id,
    'status','REVOKED_BEFORE_ACTIVATION','version',root.version+1);
END $$;

CREATE FUNCTION public.institution_admin_handoff_create_v1(p_envelope JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor_row public."user"%ROWTYPE; old_admin public."user"%ROWTYPE;
        profile_row public.platform_admin_security_profile%ROWTYPE;
        root public.direct_institution_onboarding%ROWTYPE; tenant_row public.tenant%ROWTYPE;
        accepted_step BIGINT; current_step BIGINT; old_user BIGINT; tenant_value BIGINT;
        now_value TIMESTAMPTZ:=clock_timestamp(); expires_value TIMESTAMPTZ; response_value JSONB;
BEGIN
  IF session_user<>'{review}' THEN RAISE EXCEPTION 'HANDOFF_CREATE_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF jsonb_typeof(p_envelope)<>'object' OR NOT p_envelope ?& ARRAY[
       'operation_id','actor_user_id','actor_role','actor_scope','idempotency_key','request_digest','digest_key_id','expected_version',
       'audit_id','event_id','receipt_id','expected_postimage_digest','accepted_totp_step','handoff_id','onboarding_id',
       'new_phone_ciphertext','new_phone_key_id','new_phone_digest_key_id','new_phone_digest','new_phone_digest_candidates','credential_id',
       'credential_digest_key_id','credential_digest','reason_code']
     OR (p_envelope-ARRAY[
       'operation_id','actor_user_id','actor_role','actor_scope','idempotency_key','request_digest','digest_key_id','expected_version',
       'audit_id','event_id','receipt_id','expected_postimage_digest','accepted_totp_step','handoff_id','onboarding_id',
       'new_phone_ciphertext','new_phone_key_id','new_phone_digest_key_id','new_phone_digest','new_phone_digest_candidates','credential_id',
       'credential_digest_key_id','credential_digest','reason_code'])<>'{{}}'::jsonb THEN
    RAISE EXCEPTION 'HANDOFF_CREATE_ENVELOPE_INVALID' USING ERRCODE='22023';
  END IF;
  IF jsonb_typeof(p_envelope->'accepted_totp_step')<>'number'
     OR (p_envelope->>'accepted_totp_step')!~'^(0|[1-9][0-9]*)$' THEN
    RAISE EXCEPTION 'HANDOFF_CREATE_ENVELOPE_INVALID' USING ERRCODE='22023';
  END IF;
  IF (p_envelope->>'accepted_totp_step')::NUMERIC>9223372036854775807 THEN
    RAISE EXCEPTION 'HANDOFF_CREATE_ENVELOPE_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT activated_user_id,tenant_id INTO old_user,tenant_value
    FROM public.direct_institution_onboarding WHERE onboarding_id=(p_envelope->>'onboarding_id')::UUID;
  IF NOT FOUND OR old_user IS NULL THEN RETURN NULL; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended((p_envelope->>'actor_scope')||E'\\000'||(p_envelope->>'idempotency_key'),0));
  SELECT * INTO actor_row FROM public."user" WHERE id=(p_envelope->>'actor_user_id')::BIGINT FOR SHARE;
  IF NOT FOUND OR actor_row.role::TEXT<>'super_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id IS NOT NULL OR p_envelope->>'actor_role'<>'super_admin' THEN RETURN NULL; END IF;
  SELECT * INTO profile_row FROM public.platform_admin_security_profile WHERE user_id=actor_row.id FOR UPDATE;
  accepted_step:=(p_envelope->>'accepted_totp_step')::BIGINT;
  current_step:=floor(extract(epoch FROM clock_timestamp())/30)::BIGINT;
  IF NOT FOUND OR NOT profile_row.enabled
     OR (profile_row.locked_until IS NOT NULL AND profile_row.locked_until>clock_timestamp())
     OR accepted_step<=COALESCE(profile_row.last_accepted_time_step,-1)
     OR NOT (accepted_step BETWEEN current_step-1 AND current_step+1) THEN RETURN NULL; END IF;
  SELECT * INTO old_admin FROM public."user" WHERE id=old_user FOR SHARE;
  IF NOT FOUND OR old_admin.role::TEXT<>'org_admin' OR old_admin.status::TEXT<>'active'
     OR old_admin.tenant_id<>tenant_value THEN RETURN NULL; END IF;
  SELECT * INTO tenant_row FROM public.tenant WHERE id=tenant_value FOR SHARE;
  IF NOT FOUND OR tenant_row.status::TEXT<>'active' THEN RETURN NULL; END IF;
  SELECT * INTO root FROM public.direct_institution_onboarding
   WHERE onboarding_id=(p_envelope->>'onboarding_id')::UUID FOR UPDATE;
  IF NOT FOUND OR root.activated_user_id<>old_admin.id OR root.tenant_id<>tenant_row.id
     OR root.status IN ('PENDING_ACTIVATION','REVOKED_BEFORE_ACTIVATION')
     OR root.version<>(p_envelope->>'expected_version')::BIGINT THEN RETURN NULL; END IF;
  IF EXISTS(SELECT 1 FROM public.institution_admin_handoff
            WHERE onboarding_id=root.onboarding_id AND status='ISSUED') THEN RETURN NULL; END IF;
  expires_value:=now_value+INTERVAL '24 hours';
  INSERT INTO public.institution_admin_handoff(
    handoff_id,onboarding_id,old_user_id,new_phone_ciphertext,new_phone_key_id,new_phone_digest_key_id,
    new_phone_digest,status,created_by,created_at,expires_at
  ) VALUES(
    (p_envelope->>'handoff_id')::UUID,root.onboarding_id,old_admin.id,
    decode(p_envelope->>'new_phone_ciphertext','base64'),p_envelope->>'new_phone_key_id',
    p_envelope->>'new_phone_digest_key_id',p_envelope->>'new_phone_digest','ISSUED',actor_row.id,now_value,expires_value
  );
  INSERT INTO public.institution_admin_handoff_credential(
    credential_id,handoff_id,credential_digest_key_id,credential_digest,status,issued_at,expires_at
  ) VALUES(
    (p_envelope->>'credential_id')::UUID,(p_envelope->>'handoff_id')::UUID,
    p_envelope->>'credential_digest_key_id',p_envelope->>'credential_digest','ISSUED',now_value,expires_value
  );
  PERFORM public.identity_phone_claim_internal_v1(jsonb_build_object(
    'action','RESERVE','claim_id',p_envelope->>'operation_id','claim_kind','ADMIN_HANDOFF',
    'claim_ref',p_envelope->>'handoff_id','phone_digest_key_id',p_envelope->>'new_phone_digest_key_id',
    'phone_digest',p_envelope->>'new_phone_digest','phone_digest_candidates',p_envelope->'new_phone_digest_candidates',
    'expected_state','NO_ACTIVE_CLAIM'));
  UPDATE public.direct_institution_onboarding SET updated_at=now_value,version=version+1
   WHERE onboarding_id=root.onboarding_id;
  response_value:=jsonb_build_object(
    'onboarding_id',root.onboarding_id,'tenant_id',root.tenant_public_id,
    'handoff_id',(p_envelope->>'handoff_id')::UUID,'status','ISSUED','version',1,
    'credential_delivery_state','ISSUED','credential_id',p_envelope->>'credential_id',
    'credential_issued_at',now_value,'credential_expires_at',expires_value);
  INSERT INTO public.direct_onboarding_audit(
    audit_id,actor_user_id,actor_kind,action,object_id,result,reason_code,evidence_digest
  ) VALUES(
    (p_envelope->>'audit_id')::UUID,actor_row.id,'SUPER_ADMIN','ADMIN_HANDOFF_CREATE',
    (p_envelope->>'handoff_id')::UUID,'SUCCESS',p_envelope->>'reason_code',p_envelope->>'expected_postimage_digest'
  );
  INSERT INTO public.direct_onboarding_outbox(event_id,event_type,aggregate_id,payload,payload_digest)
  VALUES(
    (p_envelope->>'event_id')::UUID,'ADMIN_HANDOFF_CREATED',root.onboarding_id,
    jsonb_build_object('onboarding_id',root.onboarding_id,'handoff_id',(p_envelope->>'handoff_id')::UUID),
    p_envelope->>'expected_postimage_digest'
  );
  INSERT INTO public.direct_onboarding_receipt(
    receipt_id,actor_scope,operation,idempotency_key,digest_key_id,request_digest,postimage_digest,response_payload
  ) VALUES(
    (p_envelope->>'receipt_id')::UUID,p_envelope->>'actor_scope','HANDOFF_CREATE',p_envelope->>'idempotency_key',
    p_envelope->>'digest_key_id',p_envelope->>'request_digest',p_envelope->>'expected_postimage_digest',response_value
  );
  UPDATE public.platform_admin_security_profile SET last_accepted_time_step=accepted_step,failed_attempts=0,
    locked_until=NULL,updated_at=now_value,profile_version=profile_version+1 WHERE user_id=profile_row.user_id;
  RETURN response_value;
END $$;

CREATE FUNCTION public.institution_admin_handoff_regenerate_v1(p_envelope JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor_row public."user"%ROWTYPE;
        profile_row public.platform_admin_security_profile%ROWTYPE;
        root public.direct_institution_onboarding%ROWTYPE;
        tenant_row public.tenant%ROWTYPE;
        handoff_row public.institution_admin_handoff%ROWTYPE;
        credential_row public.institution_admin_handoff_credential%ROWTYPE;
        claim_row public.identity_phone_claim%ROWTYPE;
        accepted_step BIGINT; current_step BIGINT;
        now_value TIMESTAMPTZ:=clock_timestamp(); expires_value TIMESTAMPTZ;
        response_value JSONB; phone_key_value VARCHAR(64); phone_digest_value CHAR(64);
BEGIN
  IF session_user<>'{review}' THEN RAISE EXCEPTION 'HANDOFF_REGENERATE_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF jsonb_typeof(p_envelope)<>'object' OR NOT p_envelope ?& ARRAY[
       'operation_id','actor_user_id','actor_role','actor_scope','idempotency_key','request_digest','digest_key_id','expected_version',
       'audit_id','event_id','receipt_id','expected_postimage_digest','accepted_totp_step','handoff_id','onboarding_id',
       'old_credential_id','new_credential_id','new_credential_digest_key_id','new_credential_digest','reason_code']
     OR (p_envelope-ARRAY[
       'operation_id','actor_user_id','actor_role','actor_scope','idempotency_key','request_digest','digest_key_id','expected_version',
       'audit_id','event_id','receipt_id','expected_postimage_digest','accepted_totp_step','handoff_id','onboarding_id',
       'old_credential_id','new_credential_id','new_credential_digest_key_id','new_credential_digest','reason_code'])<>'{{}}'::jsonb THEN
    RAISE EXCEPTION 'HANDOFF_REGENERATE_ENVELOPE_INVALID' USING ERRCODE='22023';
  END IF;
  IF jsonb_typeof(p_envelope->'accepted_totp_step')<>'number'
     OR (p_envelope->>'accepted_totp_step')!~'^(0|[1-9][0-9]*)$' THEN
    RAISE EXCEPTION 'HANDOFF_REGENERATE_ENVELOPE_INVALID' USING ERRCODE='22023';
  END IF;
  IF (p_envelope->>'accepted_totp_step')::NUMERIC>9223372036854775807 THEN
    RAISE EXCEPTION 'HANDOFF_REGENERATE_ENVELOPE_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT new_phone_digest_key_id,new_phone_digest INTO phone_key_value,phone_digest_value
    FROM public.institution_admin_handoff
   WHERE handoff_id=(p_envelope->>'handoff_id')::UUID
     AND onboarding_id=(p_envelope->>'onboarding_id')::UUID;
  IF NOT FOUND THEN RETURN NULL; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended((p_envelope->>'actor_scope')||E'\\000'||(p_envelope->>'idempotency_key'),0));
  PERFORM pg_advisory_xact_lock(hashtextextended(phone_key_value||E'\\000'||phone_digest_value,0));
  SELECT * INTO actor_row FROM public."user" WHERE id=(p_envelope->>'actor_user_id')::BIGINT FOR SHARE;
  IF NOT FOUND OR actor_row.role::TEXT<>'super_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id IS NOT NULL OR p_envelope->>'actor_role'<>'super_admin' THEN RETURN NULL; END IF;
  SELECT * INTO profile_row FROM public.platform_admin_security_profile WHERE user_id=actor_row.id FOR UPDATE;
  accepted_step:=(p_envelope->>'accepted_totp_step')::BIGINT;
  current_step:=floor(extract(epoch FROM clock_timestamp())/30)::BIGINT;
  IF NOT FOUND OR NOT profile_row.enabled
     OR (profile_row.locked_until IS NOT NULL AND profile_row.locked_until>clock_timestamp())
     OR accepted_step<=COALESCE(profile_row.last_accepted_time_step,-1)
     OR NOT (accepted_step BETWEEN current_step-1 AND current_step+1) THEN RETURN NULL; END IF;
  SELECT * INTO tenant_row FROM public.tenant
   WHERE id=(SELECT tenant_id FROM public.direct_institution_onboarding
              WHERE onboarding_id=(p_envelope->>'onboarding_id')::UUID) FOR SHARE;
  IF NOT FOUND OR tenant_row.status::TEXT<>'active' THEN RETURN NULL; END IF;
  SELECT * INTO root FROM public.direct_institution_onboarding
   WHERE onboarding_id=(p_envelope->>'onboarding_id')::UUID FOR UPDATE;
  IF NOT FOUND OR root.tenant_id<>tenant_row.id OR root.status IN ('PENDING_ACTIVATION','REVOKED_BEFORE_ACTIVATION') THEN RETURN NULL; END IF;
  SELECT * INTO handoff_row FROM public.institution_admin_handoff
   WHERE handoff_id=(p_envelope->>'handoff_id')::UUID AND onboarding_id=root.onboarding_id FOR UPDATE;
  IF NOT FOUND OR handoff_row.status<>'ISSUED' OR handoff_row.version<>(p_envelope->>'expected_version')::BIGINT
     OR handoff_row.new_phone_digest_key_id<>phone_key_value OR handoff_row.new_phone_digest<>phone_digest_value THEN RETURN NULL; END IF;
  SELECT * INTO credential_row FROM public.institution_admin_handoff_credential
   WHERE credential_id=(p_envelope->>'old_credential_id')::UUID AND handoff_id=handoff_row.handoff_id FOR UPDATE;
  IF NOT FOUND OR credential_row.status<>'ISSUED' THEN RETURN NULL; END IF;
  SELECT * INTO claim_row FROM public.identity_phone_claim
   WHERE claim_kind='ADMIN_HANDOFF' AND claim_ref=handoff_row.handoff_id FOR UPDATE;
  IF NOT FOUND OR claim_row.state<>'PENDING' OR claim_row.phone_digest_key_id<>phone_key_value
     OR claim_row.phone_digest<>phone_digest_value THEN RETURN NULL; END IF;
  expires_value:=now_value+INTERVAL '24 hours';
  UPDATE public.institution_admin_handoff_credential SET status='REVOKED',version=version+1
   WHERE credential_id=credential_row.credential_id;
  INSERT INTO public.institution_admin_handoff_credential(
    credential_id,handoff_id,credential_digest_key_id,credential_digest,status,issued_at,expires_at
  ) VALUES(
    (p_envelope->>'new_credential_id')::UUID,handoff_row.handoff_id,
    p_envelope->>'new_credential_digest_key_id',p_envelope->>'new_credential_digest','ISSUED',now_value,expires_value
  );
  UPDATE public.institution_admin_handoff SET expires_at=expires_value,version=version+1
   WHERE handoff_id=handoff_row.handoff_id;
  UPDATE public.direct_institution_onboarding SET updated_at=now_value,version=version+1
   WHERE onboarding_id=root.onboarding_id;
  response_value:=jsonb_build_object('onboarding_id',root.onboarding_id,'tenant_id',root.tenant_public_id,
    'handoff_id',handoff_row.handoff_id,'status','ISSUED','version',handoff_row.version+1,
    'credential_delivery_state','ISSUED','credential_id',p_envelope->>'new_credential_id',
    'credential_issued_at',now_value,'credential_expires_at',expires_value);
  INSERT INTO public.direct_onboarding_audit(audit_id,actor_user_id,actor_kind,action,object_id,result,reason_code,evidence_digest)
  VALUES((p_envelope->>'audit_id')::UUID,actor_row.id,'SUPER_ADMIN','ADMIN_HANDOFF_REGENERATE',handoff_row.handoff_id,
    'SUCCESS',p_envelope->>'reason_code',p_envelope->>'expected_postimage_digest');
  INSERT INTO public.direct_onboarding_outbox(event_id,event_type,aggregate_id,payload,payload_digest)
  VALUES((p_envelope->>'event_id')::UUID,'ADMIN_HANDOFF_REGENERATED',root.onboarding_id,
    jsonb_build_object('onboarding_id',root.onboarding_id,'handoff_id',handoff_row.handoff_id),p_envelope->>'expected_postimage_digest');
  INSERT INTO public.direct_onboarding_receipt(receipt_id,actor_scope,operation,idempotency_key,digest_key_id,request_digest,postimage_digest,response_payload)
  VALUES((p_envelope->>'receipt_id')::UUID,p_envelope->>'actor_scope','HANDOFF_REGENERATE',p_envelope->>'idempotency_key',
    p_envelope->>'digest_key_id',p_envelope->>'request_digest',p_envelope->>'expected_postimage_digest',response_value);
  UPDATE public.platform_admin_security_profile SET last_accepted_time_step=accepted_step,failed_attempts=0,
    locked_until=NULL,updated_at=now_value,profile_version=profile_version+1 WHERE user_id=profile_row.user_id;
  RETURN response_value;
END $$;

CREATE FUNCTION public.institution_admin_handoff_revoke_v1(p_envelope JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor_row public."user"%ROWTYPE;
        profile_row public.platform_admin_security_profile%ROWTYPE;
        root public.direct_institution_onboarding%ROWTYPE;
        tenant_row public.tenant%ROWTYPE;
        handoff_row public.institution_admin_handoff%ROWTYPE;
        credential_row public.institution_admin_handoff_credential%ROWTYPE;
        claim_row public.identity_phone_claim%ROWTYPE;
        accepted_step BIGINT; current_step BIGINT;
        now_value TIMESTAMPTZ:=clock_timestamp(); response_value JSONB;
        phone_key_value VARCHAR(64); phone_digest_value CHAR(64);
BEGIN
  IF session_user<>'{review}' THEN RAISE EXCEPTION 'HANDOFF_REVOKE_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF jsonb_typeof(p_envelope)<>'object' OR NOT p_envelope ?& ARRAY[
       'operation_id','actor_user_id','actor_role','actor_scope','idempotency_key','request_digest','digest_key_id','expected_version',
       'audit_id','event_id','receipt_id','expected_postimage_digest','accepted_totp_step','handoff_id','onboarding_id',
       'active_credential_id','new_phone_digest_key_id','new_phone_digest','reason_code']
     OR (p_envelope-ARRAY[
       'operation_id','actor_user_id','actor_role','actor_scope','idempotency_key','request_digest','digest_key_id','expected_version',
       'audit_id','event_id','receipt_id','expected_postimage_digest','accepted_totp_step','handoff_id','onboarding_id',
       'active_credential_id','new_phone_digest_key_id','new_phone_digest','reason_code'])<>'{{}}'::jsonb THEN
    RAISE EXCEPTION 'HANDOFF_REVOKE_ENVELOPE_INVALID' USING ERRCODE='22023';
  END IF;
  IF jsonb_typeof(p_envelope->'accepted_totp_step')<>'number'
     OR (p_envelope->>'accepted_totp_step')!~'^(0|[1-9][0-9]*)$' THEN
    RAISE EXCEPTION 'HANDOFF_REVOKE_ENVELOPE_INVALID' USING ERRCODE='22023';
  END IF;
  IF (p_envelope->>'accepted_totp_step')::NUMERIC>9223372036854775807 THEN
    RAISE EXCEPTION 'HANDOFF_REVOKE_ENVELOPE_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT new_phone_digest_key_id,new_phone_digest INTO phone_key_value,phone_digest_value
    FROM public.institution_admin_handoff
   WHERE handoff_id=(p_envelope->>'handoff_id')::UUID
     AND onboarding_id=(p_envelope->>'onboarding_id')::UUID;
  IF NOT FOUND OR phone_key_value<>p_envelope->>'new_phone_digest_key_id'
     OR phone_digest_value<>p_envelope->>'new_phone_digest' THEN RETURN NULL; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended((p_envelope->>'actor_scope')||E'\\000'||(p_envelope->>'idempotency_key'),0));
  PERFORM pg_advisory_xact_lock(hashtextextended(phone_key_value||E'\\000'||phone_digest_value,0));
  SELECT * INTO actor_row FROM public."user" WHERE id=(p_envelope->>'actor_user_id')::BIGINT FOR SHARE;
  IF NOT FOUND OR actor_row.role::TEXT<>'super_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id IS NOT NULL OR p_envelope->>'actor_role'<>'super_admin' THEN RETURN NULL; END IF;
  SELECT * INTO profile_row FROM public.platform_admin_security_profile WHERE user_id=actor_row.id FOR UPDATE;
  accepted_step:=(p_envelope->>'accepted_totp_step')::BIGINT;
  current_step:=floor(extract(epoch FROM clock_timestamp())/30)::BIGINT;
  IF NOT FOUND OR NOT profile_row.enabled
     OR (profile_row.locked_until IS NOT NULL AND profile_row.locked_until>clock_timestamp())
     OR accepted_step<=COALESCE(profile_row.last_accepted_time_step,-1)
     OR NOT (accepted_step BETWEEN current_step-1 AND current_step+1) THEN RETURN NULL; END IF;
  SELECT * INTO tenant_row FROM public.tenant
   WHERE id=(SELECT tenant_id FROM public.direct_institution_onboarding
              WHERE onboarding_id=(p_envelope->>'onboarding_id')::UUID) FOR SHARE;
  IF NOT FOUND OR tenant_row.status::TEXT<>'active' THEN RETURN NULL; END IF;
  SELECT * INTO root FROM public.direct_institution_onboarding
   WHERE onboarding_id=(p_envelope->>'onboarding_id')::UUID FOR UPDATE;
  IF NOT FOUND OR root.tenant_id<>tenant_row.id OR root.status IN ('PENDING_ACTIVATION','REVOKED_BEFORE_ACTIVATION') THEN RETURN NULL; END IF;
  SELECT * INTO handoff_row FROM public.institution_admin_handoff
   WHERE handoff_id=(p_envelope->>'handoff_id')::UUID AND onboarding_id=root.onboarding_id FOR UPDATE;
  IF NOT FOUND OR handoff_row.status<>'ISSUED' OR handoff_row.version<>(p_envelope->>'expected_version')::BIGINT
     OR handoff_row.new_phone_digest_key_id<>phone_key_value OR handoff_row.new_phone_digest<>phone_digest_value THEN RETURN NULL; END IF;
  SELECT * INTO credential_row FROM public.institution_admin_handoff_credential
   WHERE credential_id=(p_envelope->>'active_credential_id')::UUID AND handoff_id=handoff_row.handoff_id FOR UPDATE;
  IF NOT FOUND OR credential_row.status<>'ISSUED' THEN RETURN NULL; END IF;
  SELECT * INTO claim_row FROM public.identity_phone_claim
   WHERE claim_kind='ADMIN_HANDOFF' AND claim_ref=handoff_row.handoff_id FOR UPDATE;
  IF NOT FOUND OR claim_row.state<>'PENDING' OR claim_row.phone_digest_key_id<>phone_key_value
     OR claim_row.phone_digest<>phone_digest_value THEN RETURN NULL; END IF;
  UPDATE public.institution_admin_handoff_credential SET status='REVOKED',version=version+1
   WHERE credential_id=credential_row.credential_id;
  UPDATE public.identity_phone_claim SET state='RELEASED',updated_at=now_value,version=version+1
   WHERE claim_id=claim_row.claim_id;
  UPDATE public.institution_admin_handoff SET status='REVOKED',version=version+1
   WHERE handoff_id=handoff_row.handoff_id;
  UPDATE public.direct_institution_onboarding SET updated_at=now_value,version=version+1
   WHERE onboarding_id=root.onboarding_id;
  response_value:=jsonb_build_object('onboarding_id',root.onboarding_id,'tenant_id',root.tenant_public_id,
    'handoff_id',handoff_row.handoff_id,'status','REVOKED','version',handoff_row.version+1,
    'credential_delivery_state','REVOKED');
  INSERT INTO public.direct_onboarding_audit(audit_id,actor_user_id,actor_kind,action,object_id,result,reason_code,evidence_digest)
  VALUES((p_envelope->>'audit_id')::UUID,actor_row.id,'SUPER_ADMIN','ADMIN_HANDOFF_REVOKE',handoff_row.handoff_id,
    'SUCCESS',p_envelope->>'reason_code',p_envelope->>'expected_postimage_digest');
  INSERT INTO public.direct_onboarding_outbox(event_id,event_type,aggregate_id,payload,payload_digest)
  VALUES((p_envelope->>'event_id')::UUID,'ADMIN_HANDOFF_REVOKED',root.onboarding_id,
    jsonb_build_object('onboarding_id',root.onboarding_id,'handoff_id',handoff_row.handoff_id),p_envelope->>'expected_postimage_digest');
  INSERT INTO public.direct_onboarding_receipt(receipt_id,actor_scope,operation,idempotency_key,digest_key_id,request_digest,postimage_digest,response_payload)
  VALUES((p_envelope->>'receipt_id')::UUID,p_envelope->>'actor_scope','HANDOFF_REVOKE',p_envelope->>'idempotency_key',
    p_envelope->>'digest_key_id',p_envelope->>'request_digest',p_envelope->>'expected_postimage_digest',response_value);
  UPDATE public.platform_admin_security_profile SET last_accepted_time_step=accepted_step,failed_attempts=0,
    locked_until=NULL,updated_at=now_value,profile_version=profile_version+1 WHERE user_id=profile_row.user_id;
  RETURN response_value;
END $$;

CREATE FUNCTION public.direct_activation_authority_v1(
  p_onboarding_id UUID,p_credential_id UUID,p_credential_digests JSONB
) RETURNS TABLE(tenant_public_id UUID,onboarding_id UUID,credential_id UUID,source_kind VARCHAR,root_version BIGINT,phone_digest_key_id VARCHAR,credential_digest_key_id VARCHAR)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF session_user<>'{onboarding}' THEN
    RAISE EXCEPTION 'DIRECT_ACTIVATION_AUTHORITY_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  IF p_onboarding_id IS NULL OR p_credential_id IS NULL THEN
    RAISE EXCEPTION 'DIRECT_ACTIVATION_AUTHORITY_INVALID' USING ERRCODE='22023';
  END IF;
  RETURN QUERY
  SELECT root.tenant_public_id,root.onboarding_id,credential.credential_id,
         'DIRECT_ACTIVATION'::VARCHAR,root.version,claim.phone_digest_key_id,
         credential.credential_digest_key_id
    FROM public.direct_institution_onboarding root
    JOIN public.direct_institution_activation_credential credential
      ON credential.onboarding_id=root.onboarding_id
     AND credential.credential_id=p_credential_id
    JOIN public.identity_phone_claim claim
      ON claim.claim_kind='DIRECT_ORG_ADMIN'
     AND claim.claim_ref=root.onboarding_id
    JOIN public.tenant tenant_row ON tenant_row.id=root.tenant_id
   WHERE root.onboarding_id=p_onboarding_id
     AND root.status='PENDING_ACTIVATION'
     AND credential.status='ISSUED'
     AND credential.expires_at>clock_timestamp()
     AND credential.credential_digest=public.direct_keyed_digest_candidate_v1(
       p_credential_digests,credential.credential_digest_key_id)
     AND claim.state='PENDING'
     AND tenant_row.status='pending';
END $$;

CREATE FUNCTION public.direct_institution_activate_v1(p_envelope JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE
  root public.direct_institution_onboarding%ROWTYPE;
  claim public.identity_phone_claim%ROWTYPE;
  credential_row public.direct_institution_activation_credential%ROWTYPE;
  new_user BIGINT;
  now_value TIMESTAMPTZ:=clock_timestamp();
  actor_scope_value VARCHAR(128);
  response_value JSONB;
BEGIN
  IF session_user<>'{onboarding}' THEN RAISE EXCEPTION 'DIRECT_ACTIVATION_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF jsonb_typeof(p_envelope)<>'object'
     OR NOT p_envelope ?& ARRAY[
       'operation_id','idempotency_key','request_digest','digest_key_id','expected_version','audit_id','event_id','receipt_id',
       'expected_postimage_digest','credential_id','presented_credential_digests',
       'onboarding_id','phone_digest_key_id','phone_digest','phone',
       'password_hash','totp_ciphertext','totp_key_id','totp_secret_digest','accepted_totp_step']
     OR (p_envelope - ARRAY[
       'operation_id','idempotency_key','request_digest','digest_key_id','expected_version','audit_id','event_id','receipt_id',
       'expected_postimage_digest','credential_id','presented_credential_digests',
       'onboarding_id','phone_digest_key_id','phone_digest','phone',
       'password_hash','totp_ciphertext','totp_key_id','totp_secret_digest','accepted_totp_step'])<>'{{}}'::jsonb
     OR p_envelope->>'phone' !~ '^1[3-9][0-9]{{9}}$' THEN
    RAISE EXCEPTION 'DIRECT_ACTIVATION_ENVELOPE_INVALID' USING ERRCODE='22023';
  END IF;
  IF jsonb_typeof(p_envelope->'accepted_totp_step')<>'number'
     OR (p_envelope->>'accepted_totp_step')!~'^(0|[1-9][0-9]*)$' THEN
    RAISE EXCEPTION 'DIRECT_ACTIVATION_ENVELOPE_INVALID' USING ERRCODE='22023';
  END IF;
  IF (p_envelope->>'accepted_totp_step')::NUMERIC>9223372036854775807 THEN
    RAISE EXCEPTION 'DIRECT_ACTIVATION_ENVELOPE_INVALID' USING ERRCODE='22023';
  END IF;
  actor_scope_value:='CREDENTIAL_HOLDER:ACTIVATE:'||(p_envelope->>'credential_id');
  PERFORM pg_advisory_xact_lock(hashtextextended(
    actor_scope_value||E'\\000'||(p_envelope->>'idempotency_key'),0));
  PERFORM pg_advisory_xact_lock(hashtextextended(
    (p_envelope->>'phone_digest_key_id')||E'\\000'||(p_envelope->>'phone_digest'),0));
  SELECT * INTO root FROM public.direct_institution_onboarding WHERE onboarding_id=(p_envelope->>'onboarding_id')::UUID FOR UPDATE;
  IF NOT FOUND OR root.status<>'PENDING_ACTIVATION' OR root.version<>(p_envelope->>'expected_version')::BIGINT THEN RETURN NULL; END IF;
  SELECT * INTO credential_row FROM public.direct_institution_activation_credential
   WHERE credential_id=(p_envelope->>'credential_id')::UUID AND onboarding_id=root.onboarding_id FOR UPDATE;
  IF NOT FOUND OR credential_row.status<>'ISSUED' OR credential_row.expires_at<=now_value
     OR credential_row.credential_digest<>public.direct_keyed_digest_candidate_v1(
       p_envelope->'presented_credential_digests',credential_row.credential_digest_key_id) THEN RETURN NULL; END IF;
  SELECT * INTO claim FROM public.identity_phone_claim WHERE claim_ref=root.onboarding_id AND claim_kind='DIRECT_ORG_ADMIN' FOR UPDATE;
  IF NOT FOUND OR claim.state<>'PENDING' OR claim.phone_digest_key_id<>p_envelope->>'phone_digest_key_id'
     OR claim.phone_digest<>p_envelope->>'phone_digest' THEN RETURN NULL; END IF;
  PERFORM 1 FROM public.tenant WHERE id=root.tenant_id AND status='pending' FOR UPDATE;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT nextval(pg_get_serial_sequence('public.user','id')) INTO new_user;
  INSERT INTO public."user"(id,phone,password_hash,role,user_status,tenant_id,status,created_at,updated_at)
  VALUES(new_user,p_envelope->>'phone',p_envelope->>'password_hash','org_admin','customer',root.tenant_id,'active',now_value,now_value);
  UPDATE public.identity_phone_claim SET state='BOUND',user_id=new_user,updated_at=now_value,version=version+1
   WHERE claim_id=claim.claim_id;
  UPDATE public.direct_institution_activation_credential
     SET status='CONSUMED',consumed_at=now_value,version=version+1
   WHERE credential_id=credential_row.credential_id;
  INSERT INTO public.direct_institution_admin_account(
    user_id,onboarding_id,activation_credential_id,handoff_credential_id,
    totp_secret_ciphertext,totp_key_id,totp_secret_digest,last_accepted_time_step,activated_at
  ) VALUES(
    new_user,root.onboarding_id,credential_row.credential_id,NULL,
    decode(p_envelope->>'totp_ciphertext','base64'),p_envelope->>'totp_key_id',
    p_envelope->>'totp_secret_digest',(p_envelope->>'accepted_totp_step')::BIGINT,now_value
  );
  UPDATE public.tenant SET status='active',updated_at=now_value WHERE id=root.tenant_id;
  UPDATE public.direct_institution_onboarding SET status='ACTIVE_COMPLIANCE_PENDING',activated_user_id=new_user,activated_at=now_value,compliance_due_at=(((now_value AT TIME ZONE 'Asia/Shanghai')::DATE+30)::TIMESTAMP AT TIME ZONE 'Asia/Shanghai'),updated_at=now_value,version=version+1 WHERE onboarding_id=root.onboarding_id;
  response_value:=jsonb_build_object(
    'onboarding_id',root.onboarding_id,'tenant_id',root.tenant_public_id,
    'institution_code',root.institution_code,'institution_name',root.institution_name,
    'institution_type',root.institution_type,'administrative_region_id',root.administrative_region_id,
    'status','ACTIVE_COMPLIANCE_PENDING',
    'compliance_due_at',(((now_value AT TIME ZONE 'Asia/Shanghai')::DATE+30)::TIMESTAMP AT TIME ZONE 'Asia/Shanghai'),
    'current_revision_id',NULL,'version',root.version+1
  );
  INSERT INTO public.direct_onboarding_audit(
    audit_id,actor_user_id,actor_kind,action,object_id,result,evidence_digest
  ) VALUES(
    (p_envelope->>'audit_id')::UUID,NULL,'CREDENTIAL_HOLDER','DIRECT_INSTITUTION_ACTIVATE',
    root.onboarding_id,'SUCCESS',p_envelope->>'expected_postimage_digest'
  );
  INSERT INTO public.direct_onboarding_outbox(event_id,event_type,aggregate_id,payload,payload_digest)
  VALUES(
    (p_envelope->>'event_id')::UUID,'DIRECT_INSTITUTION_ACTIVATED',root.onboarding_id,
    jsonb_build_object('onboarding_id',root.onboarding_id),p_envelope->>'expected_postimage_digest'
  );
  INSERT INTO public.direct_onboarding_receipt(
    receipt_id,actor_scope,operation,idempotency_key,digest_key_id,request_digest,postimage_digest,response_payload
  ) VALUES(
    (p_envelope->>'receipt_id')::UUID,actor_scope_value,'ACTIVATE',p_envelope->>'idempotency_key',
    p_envelope->>'digest_key_id',p_envelope->>'request_digest',p_envelope->>'expected_postimage_digest',response_value
  );
  RETURN response_value;
END $$;

CREATE FUNCTION public.direct_compliance_save_v1(p_envelope JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE
  actor_row public."user"%ROWTYPE;
  tenant_row public.tenant%ROWTYPE;
  root public.direct_institution_onboarding%ROWTYPE;
  file_row public.private_file%ROWTYPE;
  item JSONB;
  revision_value INTEGER;
  now_value TIMESTAMPTZ:=clock_timestamp();
  response_value JSONB;
BEGIN
  IF session_user<>'{onboarding}' THEN RAISE EXCEPTION 'DIRECT_COMPLIANCE_SAVE_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF jsonb_typeof(p_envelope)<>'object' OR NOT p_envelope ?& ARRAY[
       'operation_id','actor_user_id','actor_role','actor_scope','idempotency_key','request_digest','digest_key_id','expected_version',
       'audit_id','receipt_id','expected_postimage_digest','onboarding_id','revision_id','revision_no',
       'institution_name','institution_type','administrative_region_id','institution_code',
       'compliance_schema_version','compliance_payload_ciphertext','compliance_payload_key_id','compliance_payload_digest_key_id',
       'compliance_payload_digest','unified_social_credit_code_digest_key_id','unified_social_credit_code_digest',
       'license_set_digest_key_id','license_set_digest','service_tags','licenses']
     OR (p_envelope-ARRAY[
       'operation_id','actor_user_id','actor_role','actor_scope','idempotency_key','request_digest','digest_key_id','expected_version',
       'audit_id','receipt_id','expected_postimage_digest','onboarding_id','revision_id','revision_no',
       'institution_name','institution_type','administrative_region_id','institution_code',
       'compliance_schema_version','compliance_payload_ciphertext','compliance_payload_key_id','compliance_payload_digest_key_id',
       'compliance_payload_digest','unified_social_credit_code_digest_key_id','unified_social_credit_code_digest',
       'license_set_digest_key_id','license_set_digest','service_tags','licenses'])<>'{{}}'::jsonb
     OR jsonb_typeof(p_envelope->'service_tags')<>'array'
     OR jsonb_typeof(p_envelope->'licenses')<>'array'
     OR NOT jsonb_array_length(p_envelope->'licenses') BETWEEN 1 AND 10
     OR (p_envelope->>'compliance_schema_version')::SMALLINT<>1
     OR p_envelope->>'compliance_payload_digest' !~ '^[0-9a-f]{{64}}$'
     OR p_envelope->>'unified_social_credit_code_digest' !~ '^[0-9a-f]{{64}}$'
     OR p_envelope->>'license_set_digest' !~ '^[0-9a-f]{{64}}$' THEN
    RAISE EXCEPTION 'DIRECT_COMPLIANCE_SAVE_INVALID' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(
    (p_envelope->>'actor_scope')||E'\\000'||(p_envelope->>'idempotency_key'),0));
  SELECT * INTO actor_row FROM public."user" WHERE id=(p_envelope->>'actor_user_id')::BIGINT FOR SHARE;
  IF NOT FOUND OR actor_row.role::TEXT<>'org_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id IS NULL OR p_envelope->>'actor_role'<>'org_admin' THEN
    RAISE EXCEPTION 'DIRECT_COMPLIANCE_SAVE_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  SELECT * INTO tenant_row FROM public.tenant WHERE id=actor_row.tenant_id FOR SHARE;
  IF NOT FOUND OR tenant_row.status<>'active' THEN RAISE EXCEPTION 'DIRECT_COMPLIANCE_SAVE_FORBIDDEN' USING ERRCODE='42501'; END IF;
  SELECT * INTO root FROM public.direct_institution_onboarding
   WHERE onboarding_id=(p_envelope->>'onboarding_id')::UUID FOR UPDATE;
  IF NOT FOUND OR root.tenant_id<>tenant_row.id OR root.activated_user_id<>actor_row.id
     OR root.version<>(p_envelope->>'expected_version')::BIGINT
     OR root.status NOT IN ('ACTIVE_COMPLIANCE_PENDING','COMPLIANCE_NEEDS_CORRECTION')
     OR root.institution_name<>p_envelope->>'institution_name'
     OR root.institution_type<>p_envelope->>'institution_type'
     OR root.administrative_region_id<>(p_envelope->>'administrative_region_id')::BIGINT
     OR root.institution_code<>p_envelope->>'institution_code' THEN
    RAISE EXCEPTION 'DIRECT_COMPLIANCE_SAVE_CONFLICT' USING ERRCODE='40001';
  END IF;
  SELECT COALESCE(max(revision_no),0)+1 INTO revision_value
    FROM public.direct_institution_compliance_revision WHERE onboarding_id=root.onboarding_id;
  IF revision_value<>(p_envelope->>'revision_no')::INTEGER
     OR EXISTS(
          SELECT 1
          FROM jsonb_array_elements(p_envelope->'licenses') AS license_row(license_value)
          GROUP BY license_value->>'license_id' HAVING count(*)>1)
     OR EXISTS(
          SELECT 1
          FROM jsonb_array_elements(p_envelope->'licenses') AS license_row(license_value)
          GROUP BY license_value->>'private_file_id' HAVING count(*)>1) THEN
    RAISE EXCEPTION 'DIRECT_COMPLIANCE_SAVE_INVALID' USING ERRCODE='22023';
  END IF;
  FOR item IN
    SELECT license_value
    FROM jsonb_array_elements(p_envelope->'licenses') AS license_row(license_value)
    ORDER BY (license_value->>'private_file_id')::UUID LOOP
    IF jsonb_typeof(item)<>'object' OR NOT item ?& ARRAY[
         'license_id','license_type','license_no_ciphertext','license_no_key_id',
         'license_no_digest_key_id','license_no_digest','private_file_id','valid_from','valid_until']
       OR (item-ARRAY['license_id','license_type','license_no_ciphertext','license_no_key_id',
         'license_no_digest_key_id','license_no_digest','private_file_id','valid_from','valid_until'])<>'{{}}'::jsonb
       OR item->>'license_type' NOT IN ('BUSINESS_LICENSE','MEDICAL_INSTITUTION_LICENSE')
       OR (item->>'valid_until')::DATE<(item->>'valid_from')::DATE
       OR (item->>'license_no_ciphertext' IS NULL)<>(item->>'license_no_key_id' IS NULL)
       OR (item->>'license_no_ciphertext' IS NULL)<>(item->>'license_no_digest_key_id' IS NULL)
       OR (item->>'license_no_ciphertext' IS NULL)<>(item->>'license_no_digest' IS NULL) THEN
      RAISE EXCEPTION 'DIRECT_COMPLIANCE_LICENSE_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT * INTO file_row FROM public.private_file
     WHERE file_id=(item->>'private_file_id')::UUID FOR UPDATE;
    IF NOT FOUND OR file_row.status<>'CLEAN' OR file_row.owner_user_id<>actor_row.id
       OR file_row.purpose<>item->>'license_type' OR file_row.bound_application_id IS NOT NULL
       OR file_row.actual_size IS NULL OR file_row.actual_mime_type IS NULL OR file_row.actual_sha256 IS NULL
       OR EXISTS(SELECT 1 FROM public.direct_institution_license existing WHERE existing.private_file_id=file_row.file_id) THEN
      RAISE EXCEPTION 'DIRECT_COMPLIANCE_FILE_INVALID' USING ERRCODE='42501';
    END IF;
  END LOOP;
  INSERT INTO public.direct_institution_compliance_revision(
    revision_id,onboarding_id,revision_no,status,created_operation_id,compliance_schema_version,
    compliance_payload_ciphertext,compliance_payload_key_id,
    compliance_payload_digest_key_id,compliance_payload_digest,unified_social_credit_code_digest_key_id,
    unified_social_credit_code_digest,license_count,license_set_digest_key_id,license_set_digest,
    service_tags,submitted_by,created_at,submitted_at
  ) VALUES(
    (p_envelope->>'revision_id')::UUID,root.onboarding_id,revision_value,'DRAFT',
    (p_envelope->>'operation_id')::UUID,(p_envelope->>'compliance_schema_version')::SMALLINT,
    decode(p_envelope->>'compliance_payload_ciphertext','base64'),p_envelope->>'compliance_payload_key_id',
    p_envelope->>'compliance_payload_digest_key_id',p_envelope->>'compliance_payload_digest',
    p_envelope->>'unified_social_credit_code_digest_key_id',p_envelope->>'unified_social_credit_code_digest',
    jsonb_array_length(p_envelope->'licenses'),p_envelope->>'license_set_digest_key_id',
    p_envelope->>'license_set_digest',
    p_envelope->'service_tags',actor_row.id,now_value,NULL
  );
  INSERT INTO public.direct_institution_license(
    license_id,onboarding_id,revision_id,license_type,license_no_ciphertext,license_no_key_id,
    license_no_digest_key_id,license_no_digest,private_file_id,valid_from,valid_until,status,version,created_at
  ) SELECT (license_value->>'license_id')::UUID,root.onboarding_id,(p_envelope->>'revision_id')::UUID,
      license_value->>'license_type',CASE WHEN license_value->>'license_no_ciphertext' IS NULL THEN NULL ELSE decode(license_value->>'license_no_ciphertext','base64') END,
      license_value->>'license_no_key_id',license_value->>'license_no_digest_key_id',license_value->>'license_no_digest',
      (license_value->>'private_file_id')::UUID,(license_value->>'valid_from')::DATE,(license_value->>'valid_until')::DATE,'DRAFT',1,now_value
    FROM jsonb_array_elements(p_envelope->'licenses') AS license_row(license_value);
  UPDATE public.direct_institution_onboarding SET
    current_revision_id=(p_envelope->>'revision_id')::UUID,updated_at=now_value,version=version+1
   WHERE onboarding_id=root.onboarding_id;
  response_value:=jsonb_build_object('onboarding_id',root.onboarding_id,'tenant_id',root.tenant_public_id,
    'revision_id',(p_envelope->>'revision_id')::UUID,'status',root.status,'version',root.version+1);
  INSERT INTO public.direct_onboarding_audit(audit_id,actor_user_id,actor_kind,action,object_id,result,evidence_digest)
  VALUES((p_envelope->>'audit_id')::UUID,actor_row.id,'ORG_ADMIN','DIRECT_COMPLIANCE_SAVE',root.onboarding_id,
    'SUCCESS',p_envelope->>'expected_postimage_digest');
  INSERT INTO public.direct_onboarding_receipt(
    receipt_id,actor_scope,operation,idempotency_key,digest_key_id,request_digest,postimage_digest,response_payload)
  VALUES((p_envelope->>'receipt_id')::UUID,p_envelope->>'actor_scope','COMPLIANCE_SAVE',
    p_envelope->>'idempotency_key',p_envelope->>'digest_key_id',p_envelope->>'request_digest',p_envelope->>'expected_postimage_digest',response_value);
  RETURN response_value;
END $$;

CREATE FUNCTION public.direct_compliance_submit_v1(p_envelope JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE
  actor_row public."user"%ROWTYPE;
  tenant_row public.tenant%ROWTYPE;
  root public.direct_institution_onboarding%ROWTYPE;
  revision_row public.direct_institution_compliance_revision%ROWTYPE;
  now_value TIMESTAMPTZ:=clock_timestamp();
  response_value JSONB;
BEGIN
  IF session_user<>'{onboarding}' THEN RAISE EXCEPTION 'DIRECT_COMPLIANCE_SUBMIT_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF jsonb_typeof(p_envelope)<>'object' OR NOT p_envelope ?& ARRAY[
       'operation_id','actor_user_id','actor_role','actor_scope','idempotency_key','request_digest','digest_key_id','expected_version',
       'audit_id','event_id','receipt_id','expected_postimage_digest','onboarding_id','revision_id','revision_no',
       'institution_name','institution_type','administrative_region_id','institution_code',
       'compliance_schema_version','compliance_payload_digest_key_id',
       'compliance_payload_digest','unified_social_credit_code_digest_key_id','unified_social_credit_code_digest',
       'license_set_digest_key_id','license_set_digest','service_tags','licenses']
     OR (p_envelope-ARRAY[
       'operation_id','actor_user_id','actor_role','actor_scope','idempotency_key','request_digest','digest_key_id','expected_version',
       'audit_id','event_id','receipt_id','expected_postimage_digest','onboarding_id','revision_id','revision_no',
       'institution_name','institution_type','administrative_region_id','institution_code',
       'compliance_schema_version','compliance_payload_digest_key_id',
       'compliance_payload_digest','unified_social_credit_code_digest_key_id','unified_social_credit_code_digest',
       'license_set_digest_key_id','license_set_digest','service_tags','licenses'])<>'{{}}'::jsonb
     OR jsonb_typeof(p_envelope->'service_tags')<>'array'
     OR jsonb_typeof(p_envelope->'licenses')<>'array'
     OR NOT jsonb_array_length(p_envelope->'licenses') BETWEEN 1 AND 10
     OR (p_envelope->>'compliance_schema_version')::SMALLINT<>1
     OR EXISTS(
       SELECT 1 FROM jsonb_array_elements(p_envelope->'licenses') item
       WHERE jsonb_typeof(item)<>'object' OR NOT item ?& ARRAY[
         'license_id','license_type','license_no_digest_key_id','license_no_digest',
         'private_file_id','valid_from','valid_until']
       OR (item-ARRAY['license_id','license_type','license_no_digest_key_id','license_no_digest',
         'private_file_id','valid_from','valid_until'])<>'{{}}'::jsonb
     ) THEN
    RAISE EXCEPTION 'DIRECT_COMPLIANCE_SUBMIT_INVALID' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(
    (p_envelope->>'actor_scope')||E'\\000'||(p_envelope->>'idempotency_key'),0));
  SELECT * INTO actor_row FROM public."user" WHERE id=(p_envelope->>'actor_user_id')::BIGINT FOR SHARE;
  IF NOT FOUND OR actor_row.role::TEXT<>'org_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id IS NULL OR p_envelope->>'actor_role'<>'org_admin' THEN
    RAISE EXCEPTION 'DIRECT_COMPLIANCE_SUBMIT_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  SELECT * INTO tenant_row FROM public.tenant WHERE id=actor_row.tenant_id FOR SHARE;
  IF NOT FOUND OR tenant_row.status<>'active' THEN
    RAISE EXCEPTION 'DIRECT_COMPLIANCE_SUBMIT_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  SELECT * INTO root FROM public.direct_institution_onboarding
   WHERE onboarding_id=(p_envelope->>'onboarding_id')::UUID FOR UPDATE;
  IF NOT FOUND OR root.tenant_id<>tenant_row.id OR root.activated_user_id<>actor_row.id
     OR root.version<>(p_envelope->>'expected_version')::BIGINT
     OR root.status NOT IN ('ACTIVE_COMPLIANCE_PENDING','COMPLIANCE_NEEDS_CORRECTION')
     OR root.current_revision_id<>(p_envelope->>'revision_id')::UUID
     OR root.institution_name<>p_envelope->>'institution_name'
     OR root.institution_type<>p_envelope->>'institution_type'
     OR root.administrative_region_id<>(p_envelope->>'administrative_region_id')::BIGINT
     OR root.institution_code<>p_envelope->>'institution_code' THEN
    RAISE EXCEPTION 'DIRECT_COMPLIANCE_SUBMIT_CONFLICT' USING ERRCODE='40001';
  END IF;
  SELECT * INTO revision_row FROM public.direct_institution_compliance_revision
   WHERE revision_id=root.current_revision_id AND onboarding_id=root.onboarding_id FOR UPDATE;
  IF NOT FOUND OR revision_row.status<>'DRAFT'
     OR revision_row.revision_no<>(p_envelope->>'revision_no')::INTEGER
     OR revision_row.compliance_schema_version<>(p_envelope->>'compliance_schema_version')::SMALLINT
     OR revision_row.submitted_by<>actor_row.id
     OR revision_row.compliance_payload_digest_key_id<>p_envelope->>'compliance_payload_digest_key_id'
     OR revision_row.compliance_payload_digest<>p_envelope->>'compliance_payload_digest'
     OR revision_row.unified_social_credit_code_digest_key_id<>p_envelope->>'unified_social_credit_code_digest_key_id'
     OR revision_row.unified_social_credit_code_digest<>p_envelope->>'unified_social_credit_code_digest'
     OR revision_row.license_count<>jsonb_array_length(p_envelope->'licenses')
     OR revision_row.license_set_digest_key_id<>p_envelope->>'license_set_digest_key_id'
     OR revision_row.license_set_digest<>p_envelope->>'license_set_digest'
     OR revision_row.service_tags<>p_envelope->'service_tags'
     OR (SELECT count(*) FROM public.direct_institution_license WHERE revision_id=revision_row.revision_id)
        <>jsonb_array_length(p_envelope->'licenses')
     OR EXISTS(
       SELECT 1 FROM jsonb_array_elements(p_envelope->'licenses') item
       LEFT JOIN public.direct_institution_license license
         ON license.revision_id=revision_row.revision_id
        AND license.license_id=(item->>'license_id')::UUID
      WHERE license.license_id IS NULL OR license.status<>'DRAFT'
         OR license.license_type<>item->>'license_type'
         OR license.private_file_id<>(item->>'private_file_id')::UUID
         OR license.valid_from<>(item->>'valid_from')::DATE
         OR license.valid_until<>(item->>'valid_until')::DATE
         OR license.license_no_digest_key_id IS DISTINCT FROM item->>'license_no_digest_key_id'
         OR license.license_no_digest IS DISTINCT FROM item->>'license_no_digest'
     ) THEN
    RAISE EXCEPTION 'DIRECT_COMPLIANCE_SUBMIT_CONFLICT' USING ERRCODE='40001';
  END IF;
  UPDATE public.direct_institution_compliance_revision
     SET status='SUBMITTED',submitted_at=now_value,version=version+1
   WHERE revision_id=revision_row.revision_id;
  UPDATE public.direct_institution_license SET status='SUBMITTED',version=version+1
   WHERE revision_id=revision_row.revision_id AND status='DRAFT';
  UPDATE public.direct_institution_onboarding SET status='COMPLIANCE_UNDER_REVIEW',
    updated_at=now_value,version=version+1 WHERE onboarding_id=root.onboarding_id;
  response_value:=jsonb_build_object('onboarding_id',root.onboarding_id,'tenant_id',root.tenant_public_id,
    'revision_id',revision_row.revision_id,'status','COMPLIANCE_UNDER_REVIEW','version',root.version+1);
  INSERT INTO public.direct_onboarding_audit(audit_id,actor_user_id,actor_kind,action,object_id,result,evidence_digest)
  VALUES((p_envelope->>'audit_id')::UUID,actor_row.id,'ORG_ADMIN','DIRECT_COMPLIANCE_SUBMIT',root.onboarding_id,
    'SUCCESS',p_envelope->>'expected_postimage_digest');
  INSERT INTO public.direct_onboarding_outbox(event_id,event_type,aggregate_id,payload,payload_digest)
  VALUES((p_envelope->>'event_id')::UUID,'DIRECT_COMPLIANCE_SUBMITTED',root.onboarding_id,
    jsonb_build_object('onboarding_id',root.onboarding_id,'revision_id',revision_row.revision_id),
    p_envelope->>'expected_postimage_digest');
  INSERT INTO public.direct_onboarding_receipt(
    receipt_id,actor_scope,operation,idempotency_key,digest_key_id,request_digest,postimage_digest,response_payload)
  VALUES((p_envelope->>'receipt_id')::UUID,p_envelope->>'actor_scope','COMPLIANCE_SUBMIT',
    p_envelope->>'idempotency_key',p_envelope->>'digest_key_id',p_envelope->>'request_digest',p_envelope->>'expected_postimage_digest',response_value);
  RETURN response_value;
END $$;

CREATE FUNCTION public.direct_compliance_decide_v1(p_envelope JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE
  actor_row public."user"%ROWTYPE;
  profile_row public.platform_admin_security_profile%ROWTYPE;
  tenant_row public.tenant%ROWTYPE;
  root public.direct_institution_onboarding%ROWTYPE;
  revision_row public.direct_institution_compliance_revision%ROWTYPE;
  accepted_step BIGINT;
  current_step BIGINT;
  now_value TIMESTAMPTZ:=clock_timestamp();
  response_value JSONB;
  base_keys TEXT[]:=ARRAY[
    'operation_id','actor_user_id','actor_role','actor_scope','idempotency_key','request_digest','digest_key_id','expected_version',
    'audit_id','event_id','receipt_id','expected_postimage_digest','accepted_totp_step','onboarding_id',
    'revision_id','decision_id','decision','reason_code','correction_fields'];
BEGIN
  IF session_user<>'{review}' THEN RAISE EXCEPTION 'DIRECT_COMPLIANCE_DECIDE_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF jsonb_typeof(p_envelope)<>'object' OR NOT p_envelope ?& base_keys
     OR p_envelope->>'decision' NOT IN ('APPROVE','NEEDS_CORRECTION')
     OR jsonb_typeof(p_envelope->'correction_fields')<>'array'
     OR (p_envelope->>'decision'='APPROVE' AND (
          NOT p_envelope ? 'unified_social_credit_code'
          OR length(p_envelope->>'unified_social_credit_code') NOT BETWEEN 1 AND 18
          OR (p_envelope-(base_keys||ARRAY['unified_social_credit_code']))<>'{{}}'::jsonb))
     OR (p_envelope->>'decision'='NEEDS_CORRECTION' AND (
          p_envelope ? 'unified_social_credit_code' OR (p_envelope-base_keys)<>'{{}}'::jsonb)) THEN
    RAISE EXCEPTION 'DIRECT_COMPLIANCE_DECIDE_INVALID' USING ERRCODE='22023';
  END IF;
  IF jsonb_typeof(p_envelope->'accepted_totp_step')<>'number'
     OR (p_envelope->>'accepted_totp_step')!~'^(0|[1-9][0-9]*)$' THEN
    RAISE EXCEPTION 'DIRECT_COMPLIANCE_DECIDE_INVALID' USING ERRCODE='22023';
  END IF;
  IF (p_envelope->>'accepted_totp_step')::NUMERIC>9223372036854775807 THEN
    RAISE EXCEPTION 'DIRECT_COMPLIANCE_DECIDE_INVALID' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(
    (p_envelope->>'actor_scope')||E'\\000'||(p_envelope->>'idempotency_key'),0));
  SELECT * INTO actor_row FROM public."user" WHERE id=(p_envelope->>'actor_user_id')::BIGINT FOR SHARE;
  IF NOT FOUND OR actor_row.role::TEXT<>'super_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id IS NOT NULL OR p_envelope->>'actor_role'<>'super_admin' THEN
    RAISE EXCEPTION 'DIRECT_COMPLIANCE_DECIDE_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  SELECT * INTO profile_row FROM public.platform_admin_security_profile WHERE user_id=actor_row.id FOR UPDATE;
  accepted_step:=(p_envelope->>'accepted_totp_step')::BIGINT;
  current_step:=floor(extract(epoch FROM clock_timestamp())/30)::BIGINT;
  IF NOT FOUND OR NOT profile_row.enabled OR actor_row.id=(SELECT created_by FROM public.direct_institution_onboarding WHERE onboarding_id=(p_envelope->>'onboarding_id')::UUID)
     OR accepted_step<=COALESCE(profile_row.last_accepted_time_step,-1)
     OR NOT (accepted_step BETWEEN current_step-1 AND current_step+1) THEN
    RAISE EXCEPTION 'DIRECT_COMPLIANCE_DECIDE_STEP_UP_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  SELECT tenant.* INTO tenant_row FROM public.tenant tenant
    JOIN public.direct_institution_onboarding direct_row ON direct_row.tenant_id=tenant.id
   WHERE direct_row.onboarding_id=(p_envelope->>'onboarding_id')::UUID FOR SHARE OF tenant;
  IF NOT FOUND THEN RAISE EXCEPTION 'DIRECT_COMPLIANCE_DECIDE_CONFLICT' USING ERRCODE='40001'; END IF;
  SELECT * INTO root FROM public.direct_institution_onboarding
   WHERE onboarding_id=(p_envelope->>'onboarding_id')::UUID FOR UPDATE;
  IF NOT FOUND OR root.tenant_id<>tenant_row.id OR root.current_revision_id<>(p_envelope->>'revision_id')::UUID
     OR root.version<>(p_envelope->>'expected_version')::BIGINT OR root.status<>'COMPLIANCE_UNDER_REVIEW' THEN
    RAISE EXCEPTION 'DIRECT_COMPLIANCE_DECIDE_CONFLICT' USING ERRCODE='40001';
  END IF;
  SELECT * INTO revision_row FROM public.direct_institution_compliance_revision
   WHERE revision_id=(p_envelope->>'revision_id')::UUID AND onboarding_id=root.onboarding_id FOR UPDATE;
  IF NOT FOUND OR revision_row.status NOT IN ('SUBMITTED','UNDER_REVIEW') THEN
    RAISE EXCEPTION 'DIRECT_COMPLIANCE_DECIDE_CONFLICT' USING ERRCODE='40001';
  END IF;
  IF p_envelope->>'decision'='APPROVE' THEN
    IF tenant_row.credit_code IS NOT NULL
       AND tenant_row.credit_code<>p_envelope->>'unified_social_credit_code' THEN
      RAISE EXCEPTION 'DIRECT_CREDIT_CODE_CONFLICT' USING ERRCODE='23505';
    END IF;
    UPDATE public.tenant SET credit_code=p_envelope->>'unified_social_credit_code',updated_at=now_value
     WHERE id=tenant_row.id;
    UPDATE public.direct_institution_license SET status='SUPERSEDED',superseded_at=now_value,version=version+1
     WHERE onboarding_id=root.onboarding_id AND status='CURRENT';
    UPDATE public.direct_institution_license SET status='CURRENT',version=version+1
     WHERE revision_id=revision_row.revision_id AND status='SUBMITTED';
    UPDATE public.direct_institution_compliance_revision SET status='APPROVED',reviewed_by=actor_row.id,
      reviewed_at=now_value,reason_code=p_envelope->>'reason_code',version=version+1
     WHERE revision_id=revision_row.revision_id;
    UPDATE public.direct_institution_onboarding SET status='COMPLIANCE_APPROVED',updated_at=now_value,version=version+1
     WHERE onboarding_id=root.onboarding_id;
    response_value:=jsonb_build_object('onboarding_id',root.onboarding_id,'revision_id',revision_row.revision_id,
      'revision_no',revision_row.revision_no,'status','COMPLIANCE_APPROVED',
      'institution_name',root.institution_name,'institution_type',root.institution_type,
      'administrative_region_id',root.administrative_region_id,'institution_code',root.institution_code,
      'service_tags',revision_row.service_tags,'licenses',(SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'license_id',license.license_id,'license_type',license.license_type,'license_no',NULL,
        'private_file_id',license.private_file_id,'valid_from',license.valid_from,'valid_until',license.valid_until)
        ORDER BY license.license_id),'[]'::jsonb) FROM public.direct_institution_license license
        WHERE license.revision_id=revision_row.revision_id),
      'correction_fields','[]'::jsonb,'version',root.version+1);
  ELSE
    UPDATE public.direct_institution_compliance_revision SET status='NEEDS_CORRECTION',reviewed_by=actor_row.id,
      reviewed_at=now_value,reason_code=p_envelope->>'reason_code',correction_fields=p_envelope->'correction_fields',version=version+1
     WHERE revision_id=revision_row.revision_id;
    UPDATE public.direct_institution_onboarding SET status='COMPLIANCE_NEEDS_CORRECTION',updated_at=now_value,version=version+1
     WHERE onboarding_id=root.onboarding_id;
    response_value:=jsonb_build_object('onboarding_id',root.onboarding_id,'revision_id',revision_row.revision_id,
      'revision_no',revision_row.revision_no,'status','COMPLIANCE_NEEDS_CORRECTION',
      'institution_name',root.institution_name,'institution_type',root.institution_type,
      'administrative_region_id',root.administrative_region_id,'institution_code',root.institution_code,
      'service_tags',revision_row.service_tags,'licenses',(SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'license_id',license.license_id,'license_type',license.license_type,'license_no',NULL,
        'private_file_id',license.private_file_id,'valid_from',license.valid_from,'valid_until',license.valid_until)
        ORDER BY license.license_id),'[]'::jsonb) FROM public.direct_institution_license license
        WHERE license.revision_id=revision_row.revision_id),
      'correction_fields',p_envelope->'correction_fields','version',root.version+1);
  END IF;
  INSERT INTO public.direct_onboarding_audit(audit_id,actor_user_id,actor_kind,action,object_id,result,reason_code,evidence_digest)
  VALUES((p_envelope->>'audit_id')::UUID,actor_row.id,'SUPER_ADMIN','DIRECT_COMPLIANCE_DECIDE',(p_envelope->>'decision_id')::UUID,
    'SUCCESS',p_envelope->>'reason_code',p_envelope->>'expected_postimage_digest');
  INSERT INTO public.direct_onboarding_outbox(event_id,event_type,aggregate_id,payload,payload_digest)
  VALUES((p_envelope->>'event_id')::UUID,'DIRECT_COMPLIANCE_DECIDED',root.onboarding_id,
    jsonb_build_object('onboarding_id',root.onboarding_id,'revision_id',revision_row.revision_id,
      'decision_id',(p_envelope->>'decision_id')::UUID,'status',response_value->>'status'),
    p_envelope->>'expected_postimage_digest');
  INSERT INTO public.direct_onboarding_receipt(
    receipt_id,actor_scope,operation,idempotency_key,digest_key_id,request_digest,postimage_digest,response_payload)
  VALUES((p_envelope->>'receipt_id')::UUID,p_envelope->>'actor_scope','COMPLIANCE_DECIDE',
    p_envelope->>'idempotency_key',p_envelope->>'digest_key_id',p_envelope->>'request_digest',p_envelope->>'expected_postimage_digest',response_value);
  UPDATE public.platform_admin_security_profile SET last_accepted_time_step=accepted_step,failed_attempts=0,
    locked_until=NULL,updated_at=now_value,profile_version=profile_version+1 WHERE user_id=profile_row.user_id;
  RETURN response_value;
END $$;

CREATE FUNCTION public.admin_handoff_activation_authority_v1(
  p_onboarding_id UUID,p_handoff_id UUID,p_credential_id UUID,p_credential_digests JSONB
) RETURNS TABLE(
  tenant_public_id UUID,onboarding_id UUID,handoff_id UUID,credential_id UUID,handoff_version BIGINT,
  phone_digest_key_id VARCHAR,credential_digest_key_id VARCHAR
) LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF session_user<>'{onboarding}' THEN
    RAISE EXCEPTION 'HANDOFF_ACTIVATION_AUTHORITY_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  IF p_onboarding_id IS NULL OR p_handoff_id IS NULL OR p_credential_id IS NULL THEN
    RAISE EXCEPTION 'HANDOFF_ACTIVATION_AUTHORITY_INVALID' USING ERRCODE='22023';
  END IF;
  RETURN QUERY
  SELECT root.tenant_public_id,root.onboarding_id,handoff.handoff_id,
         credential.credential_id,handoff.version,claim.phone_digest_key_id,
         credential.credential_digest_key_id
    FROM public.institution_admin_handoff handoff
    JOIN public.direct_institution_onboarding root
      ON root.onboarding_id=handoff.onboarding_id
    JOIN public.tenant tenant ON tenant.id=root.tenant_id AND tenant.status='active'
    JOIN public.institution_admin_handoff_credential credential
      ON credential.handoff_id=handoff.handoff_id
    JOIN public.identity_phone_claim claim
      ON claim.claim_kind='ADMIN_HANDOFF' AND claim.claim_ref=handoff.handoff_id
   WHERE root.onboarding_id=p_onboarding_id
     AND handoff.handoff_id=p_handoff_id AND handoff.status='ISSUED'
     AND handoff.expires_at>clock_timestamp()
     AND credential.credential_id=p_credential_id AND credential.status='ISSUED'
     AND credential.expires_at>clock_timestamp()
     AND credential.credential_digest=public.direct_keyed_digest_candidate_v1(
       p_credential_digests,credential.credential_digest_key_id)
     AND claim.state='PENDING'
   FOR SHARE OF root,handoff,tenant,credential,claim;
END $$;

CREATE FUNCTION public.admin_handoff_activation_v1(p_envelope JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE
  handoff_row public.institution_admin_handoff%ROWTYPE;
  root public.direct_institution_onboarding%ROWTYPE;
  claim public.identity_phone_claim%ROWTYPE;
  credential_row public.institution_admin_handoff_credential%ROWTYPE;
  old_user BIGINT;
  tenant_value BIGINT;
  new_user BIGINT;
  now_value TIMESTAMPTZ:=clock_timestamp();
  actor_scope_value VARCHAR(128);
  response_value JSONB;
BEGIN
  IF session_user<>'{onboarding}' THEN RAISE EXCEPTION 'ADMIN_HANDOFF_ACTIVATION_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF jsonb_typeof(p_envelope)<>'object'
     OR NOT p_envelope ?& ARRAY[
       'operation_id','idempotency_key','request_digest','digest_key_id','expected_version','audit_id','event_id','receipt_id',
       'expected_postimage_digest','credential_id','presented_credential_digests',
       'handoff_id','onboarding_id','new_phone_digest_key_id',
       'new_phone_digest','new_phone','password_hash','totp_ciphertext','totp_key_id',
       'totp_secret_digest','accepted_totp_step']
     OR (p_envelope - ARRAY[
       'operation_id','idempotency_key','request_digest','digest_key_id','expected_version','audit_id','event_id','receipt_id',
       'expected_postimage_digest','credential_id','presented_credential_digests',
       'handoff_id','onboarding_id','new_phone_digest_key_id',
       'new_phone_digest','new_phone','password_hash','totp_ciphertext','totp_key_id',
       'totp_secret_digest','accepted_totp_step'])<>'{{}}'::jsonb
     OR p_envelope->>'new_phone' !~ '^1[3-9][0-9]{{9}}$' THEN
    RAISE EXCEPTION 'ADMIN_HANDOFF_ENVELOPE_INVALID' USING ERRCODE='22023';
  END IF;
  IF jsonb_typeof(p_envelope->'accepted_totp_step')<>'number'
     OR (p_envelope->>'accepted_totp_step')!~'^(0|[1-9][0-9]*)$' THEN
    RAISE EXCEPTION 'ADMIN_HANDOFF_ENVELOPE_INVALID' USING ERRCODE='22023';
  END IF;
  IF (p_envelope->>'accepted_totp_step')::NUMERIC>9223372036854775807 THEN
    RAISE EXCEPTION 'ADMIN_HANDOFF_ENVELOPE_INVALID' USING ERRCODE='22023';
  END IF;
  actor_scope_value:='CREDENTIAL_HOLDER:HANDOFF_ACTIVATE:'||(p_envelope->>'credential_id');
  PERFORM pg_advisory_xact_lock(hashtextextended(
    actor_scope_value||E'\\000'||(p_envelope->>'idempotency_key'),0));
  PERFORM pg_advisory_xact_lock(hashtextextended(
    (p_envelope->>'new_phone_digest_key_id')||E'\\000'||(p_envelope->>'new_phone_digest'),0));
  SELECT handoff.* INTO handoff_row
    FROM public.institution_admin_handoff handoff
   WHERE handoff.handoff_id=(p_envelope->>'handoff_id')::UUID;
  IF NOT FOUND THEN RETURN NULL; END IF;
  old_user:=handoff_row.old_user_id;
  SELECT * INTO root FROM public.direct_institution_onboarding
   WHERE onboarding_id=handoff_row.onboarding_id;
  IF NOT FOUND OR root.onboarding_id<>(p_envelope->>'onboarding_id')::UUID THEN RETURN NULL; END IF;
  tenant_value:=root.tenant_id;
  PERFORM 1 FROM public."user"
   WHERE id=old_user AND role::TEXT='org_admin' AND status::TEXT='active' AND tenant_id=tenant_value FOR UPDATE;
  IF NOT FOUND THEN RETURN NULL; END IF;
  PERFORM 1 FROM public.tenant WHERE id=tenant_value FOR SHARE;
  SELECT * INTO root FROM public.direct_institution_onboarding
   WHERE onboarding_id=(p_envelope->>'onboarding_id')::UUID FOR UPDATE;
  SELECT * INTO handoff_row FROM public.institution_admin_handoff
   WHERE handoff_id=(p_envelope->>'handoff_id')::UUID FOR UPDATE;
  IF handoff_row.status<>'ISSUED' OR handoff_row.version<>(p_envelope->>'expected_version')::BIGINT
     OR handoff_row.expires_at<=now_value OR handoff_row.old_user_id<>old_user
     OR handoff_row.onboarding_id<>root.onboarding_id THEN RETURN NULL; END IF;
  SELECT * INTO credential_row FROM public.institution_admin_handoff_credential
   WHERE credential_id=(p_envelope->>'credential_id')::UUID AND handoff_id=handoff_row.handoff_id FOR UPDATE;
  IF NOT FOUND OR credential_row.status<>'ISSUED' OR credential_row.expires_at<=now_value
     OR credential_row.credential_digest<>public.direct_keyed_digest_candidate_v1(
       p_envelope->'presented_credential_digests',credential_row.credential_digest_key_id) THEN RETURN NULL; END IF;
  SELECT * INTO claim FROM public.identity_phone_claim
   WHERE claim_kind='ADMIN_HANDOFF' AND claim_ref=handoff_row.handoff_id FOR UPDATE;
  IF NOT FOUND OR claim.state<>'PENDING'
     OR claim.phone_digest_key_id<>p_envelope->>'new_phone_digest_key_id'
     OR claim.phone_digest<>p_envelope->>'new_phone_digest' THEN RETURN NULL; END IF;
  SELECT nextval(pg_get_serial_sequence('public.user','id')) INTO new_user;
  INSERT INTO public."user"(id,phone,password_hash,role,user_status,tenant_id,status,created_at,updated_at)
  VALUES(new_user,p_envelope->>'new_phone',p_envelope->>'password_hash','org_admin','customer',tenant_value,'active',now_value,now_value);
  INSERT INTO public.direct_institution_admin_account(
    user_id,onboarding_id,activation_credential_id,handoff_credential_id,
    totp_secret_ciphertext,totp_key_id,totp_secret_digest,last_accepted_time_step,activated_at
  ) VALUES(
    new_user,root.onboarding_id,NULL,credential_row.credential_id,
    decode(p_envelope->>'totp_ciphertext','base64'),p_envelope->>'totp_key_id',
    p_envelope->>'totp_secret_digest',(p_envelope->>'accepted_totp_step')::BIGINT,now_value
  );
  UPDATE public."user" SET status='disabled',updated_at=now_value WHERE id=old_user;
  UPDATE public.direct_institution_admin_account
     SET totp_enabled=FALSE,version=version+1 WHERE user_id=old_user;
  UPDATE public.identity_phone_claim
     SET state='BOUND',user_id=new_user,updated_at=now_value,version=version+1 WHERE claim_id=claim.claim_id;
  UPDATE public.institution_admin_handoff_credential
     SET status='CONSUMED',consumed_at=now_value,version=version+1
   WHERE credential_id=credential_row.credential_id;
  UPDATE public.institution_admin_handoff
     SET status='ACTIVATED',new_user_id=new_user,activated_at=now_value,version=version+1
   WHERE handoff_id=handoff_row.handoff_id;
  UPDATE public.direct_institution_onboarding
     SET activated_user_id=new_user,updated_at=now_value,version=version+1
   WHERE onboarding_id=root.onboarding_id;
  response_value:=jsonb_build_object(
    'handoff_id',handoff_row.handoff_id,'onboarding_id',root.onboarding_id,
    'tenant_id',root.tenant_public_id,'status','ACTIVATED','version',handoff_row.version+1
  );
  INSERT INTO public.direct_onboarding_audit(
    audit_id,actor_user_id,actor_kind,action,object_id,result,evidence_digest
  ) VALUES(
    (p_envelope->>'audit_id')::UUID,NULL,'CREDENTIAL_HOLDER','ADMIN_HANDOFF_ACTIVATE',
    handoff_row.handoff_id,'SUCCESS',p_envelope->>'expected_postimage_digest'
  );
  INSERT INTO public.direct_onboarding_outbox(event_id,event_type,aggregate_id,payload,payload_digest)
  VALUES(
    (p_envelope->>'event_id')::UUID,'ADMIN_HANDOFF_ACTIVATED',root.onboarding_id,
    jsonb_build_object('onboarding_id',root.onboarding_id,'handoff_id',handoff_row.handoff_id),
    p_envelope->>'expected_postimage_digest'
  );
  INSERT INTO public.direct_onboarding_receipt(
    receipt_id,actor_scope,operation,idempotency_key,digest_key_id,request_digest,postimage_digest,response_payload
  ) VALUES(
    (p_envelope->>'receipt_id')::UUID,actor_scope_value,'HANDOFF_ACTIVATE',p_envelope->>'idempotency_key',
    p_envelope->>'digest_key_id',p_envelope->>'request_digest',p_envelope->>'expected_postimage_digest',response_value
  );
  RETURN response_value;
END $$;

CREATE FUNCTION public.auth_register_member_v2(p_claim_id UUID,p_phone VARCHAR,p_password_hash VARCHAR,p_phone_digest CHAR,p_key_id VARCHAR,p_phone_digest_candidates JSONB)
RETURNS TABLE (
  id BIGINT,
  phone VARCHAR,
  role public.user_role,
  status public.user_status,
  verify_status VARCHAR,
  tenant_id BIGINT,
  created_at TIMESTAMPTZ
) LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE new_user_id BIGINT;
BEGIN
  IF session_user<>'{application}' THEN RAISE EXCEPTION 'AUTH_REGISTER_MEMBER_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF p_claim_id IS NULL OR (get_byte(uuid_send(p_claim_id),6) >> 4)<>7 THEN
    RAISE EXCEPTION 'AUTH_REGISTER_MEMBER_CLAIM_ID_INVALID' USING ERRCODE='22023';
  END IF;
  PERFORM public.identity_phone_claim_internal_v1(jsonb_build_object(
    'action','RESERVE','claim_id',p_claim_id,'claim_kind','MEMBER_ACCOUNT',
    'claim_ref',p_claim_id,'phone_digest_key_id',p_key_id,
    'phone_digest',p_phone_digest,'phone_digest_candidates',p_phone_digest_candidates,
    'expected_state','NO_ACTIVE_CLAIM'));
  SELECT nextval(pg_get_serial_sequence('public.user','id')) INTO new_user_id;
  INSERT INTO public."user"(id,phone,password_hash,role,user_status,status,created_at,updated_at)
  VALUES(new_user_id,p_phone,p_password_hash,'member','customer','active',statement_timestamp(),statement_timestamp());
  UPDATE public.identity_phone_claim SET state='BOUND',user_id=new_user_id,updated_at=statement_timestamp(),version=version+1 WHERE claim_id=p_claim_id;
  RETURN QUERY
  SELECT registered.id,registered.phone,registered.role,registered.status,
         registered.verify_status,registered.tenant_id,registered.created_at
    FROM public."user" AS registered
   WHERE registered.id=new_user_id;
END $$;

CREATE FUNCTION public.direct_review_replay_v1(p_envelope JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE receipt_row public.direct_onboarding_receipt%ROWTYPE; result_value JSONB;
BEGIN
  IF session_user<>'{review}' THEN RAISE EXCEPTION 'DIRECT_REPLAY_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF jsonb_typeof(p_envelope)<>'object'
     OR NOT p_envelope ?& ARRAY['actor_scope','operation','idempotency_key','request_digest','request_digest_candidates']
     OR (p_envelope-ARRAY['actor_scope','operation','idempotency_key','request_digest','request_digest_candidates'])<>'{{}}'::jsonb
     OR p_envelope->>'operation' NOT IN ('CREATE','REGENERATE','REVOKE','COMPLIANCE_DECIDE','HANDOFF_CREATE','HANDOFF_REGENERATE','HANDOFF_REVOKE') THEN
    RAISE EXCEPTION 'DIRECT_REPLAY_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT * INTO receipt_row FROM public.direct_onboarding_receipt
   WHERE actor_scope=p_envelope->>'actor_scope' AND operation=p_envelope->>'operation'
     AND idempotency_key=p_envelope->>'idempotency_key';
  IF NOT FOUND THEN RETURN NULL; END IF;
  IF receipt_row.request_digest<>public.direct_keyed_digest_candidate_v1(
       p_envelope->'request_digest_candidates',receipt_row.digest_key_id) THEN
    RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT' USING ERRCODE='23505';
  END IF;
  result_value:=receipt_row.response_payload;
  IF result_value IS NULL AND p_envelope->>'operation' IN ('REVOKE','HANDOFF_REVOKE') THEN
    result_value:=jsonb_build_object('replayed',true);
  END IF;
  IF result_value IS NOT NULL AND p_envelope->>'operation' IN ('CREATE','REGENERATE','HANDOFF_CREATE','HANDOFF_REGENERATE') THEN
    result_value:=result_value||jsonb_build_object('credential_delivery_state','ALREADY_ISSUED');
  END IF;
  RETURN result_value;
END $$;

CREATE FUNCTION public.direct_review_commit_confirm_v1(p_envelope JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor_row public."user"%ROWTYPE; target_present BOOLEAN:=FALSE; target_ok BOOLEAN:=FALSE;
        receipt_present BOOLEAN; receipt_ok BOOLEAN; audit_present BOOLEAN; audit_ok BOOLEAN;
        outbox_present BOOLEAN; outbox_ok BOOLEAN; root_row public.direct_institution_onboarding%ROWTYPE;
        handoff_row public.institution_admin_handoff%ROWTYPE; credential_ok BOOLEAN:=FALSE;
        claim_ok BOOLEAN:=FALSE; tenant_ok BOOLEAN:=FALSE; revision_ok BOOLEAN:=FALSE;
BEGIN
  IF session_user<>'{review}' THEN RAISE EXCEPTION 'DIRECT_CONFIRM_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF jsonb_typeof(p_envelope)<>'object' OR NOT p_envelope ?& ARRAY[
       'operation_id','actor_user_id','actor_role','actor_scope','idempotency_key','request_digest','digest_key_id','expected_version',
       'audit_id','event_id','receipt_id','expected_postimage_digest','operation','target_id','target_status','target_version',
       'credential_id','credential_digest_key_id','credential_digest',
        'phone_claim_state','tenant_public_id','tenant_status','user_id','revision_id','revision_status','audit_action',
       'audit_evidence_digest','outbox_event_type','outbox_payload_digest','receipt_response_digest']
     OR (p_envelope-ARRAY[
       'operation_id','actor_user_id','actor_role','actor_scope','idempotency_key','request_digest','digest_key_id','expected_version',
       'audit_id','event_id','receipt_id','expected_postimage_digest','operation','target_id','target_status','target_version',
       'credential_id','credential_digest_key_id','credential_digest',
        'phone_claim_state','tenant_id','tenant_public_id','tenant_status','user_id','revision_id','revision_status','audit_action',
       'audit_evidence_digest','outbox_event_type','outbox_payload_digest','receipt_response_digest'])<>'{{}}'::jsonb
     OR p_envelope->>'operation' NOT IN ('CREATE','REGENERATE','REVOKE','COMPLIANCE_DECIDE','HANDOFF_CREATE','HANDOFF_REGENERATE','HANDOFF_REVOKE') THEN
    RAISE EXCEPTION 'DIRECT_CONFIRM_INVALID' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(
    (p_envelope->>'actor_scope')||E'\\000'||(p_envelope->>'idempotency_key'),0));
  SELECT * INTO actor_row FROM public."user" WHERE id=(p_envelope->>'actor_user_id')::BIGINT;
  IF NOT FOUND OR actor_row.role::TEXT<>'super_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id IS NOT NULL OR p_envelope->>'actor_role'<>'super_admin' THEN
    RETURN jsonb_build_object('outcome','UNKNOWN');
  END IF;
  IF p_envelope->>'operation' LIKE 'HANDOFF_%' THEN
    SELECT * INTO handoff_row FROM public.institution_admin_handoff WHERE handoff_id=(p_envelope->>'target_id')::UUID;
    target_present:=FOUND;
    target_ok:=FOUND AND handoff_row.status=p_envelope->>'target_status'
      AND handoff_row.version=(p_envelope->>'target_version')::BIGINT;
  ELSE
    SELECT * INTO root_row FROM public.direct_institution_onboarding WHERE onboarding_id=(p_envelope->>'target_id')::UUID;
    target_present:=FOUND;
    target_ok:=FOUND AND root_row.status=p_envelope->>'target_status'
      AND root_row.version=(p_envelope->>'target_version')::BIGINT;
  END IF;
  SELECT EXISTS(SELECT 1 FROM public.direct_onboarding_receipt r WHERE r.receipt_id=(p_envelope->>'receipt_id')::UUID),
         EXISTS(SELECT 1 FROM public.direct_onboarding_receipt r
          WHERE r.receipt_id=(p_envelope->>'receipt_id')::UUID AND r.actor_scope=p_envelope->>'actor_scope'
            AND r.operation=p_envelope->>'operation' AND r.idempotency_key=p_envelope->>'idempotency_key'
            AND r.digest_key_id=p_envelope->>'digest_key_id' AND r.request_digest=p_envelope->>'request_digest'
            AND r.postimage_digest=p_envelope->>'receipt_response_digest')
    INTO receipt_present,receipt_ok;
  SELECT EXISTS(SELECT 1 FROM public.direct_onboarding_audit a WHERE a.audit_id=(p_envelope->>'audit_id')::UUID),
         EXISTS(SELECT 1 FROM public.direct_onboarding_audit a
          WHERE a.audit_id=(p_envelope->>'audit_id')::UUID AND a.actor_user_id=actor_row.id
            AND a.action=p_envelope->>'audit_action' AND a.evidence_digest=p_envelope->>'audit_evidence_digest'
            AND (p_envelope->>'operation'<>'COMPLIANCE_DECIDE'
              OR a.object_id=(p_envelope->>'operation_id')::UUID))
    INTO audit_present,audit_ok;
  SELECT EXISTS(SELECT 1 FROM public.direct_onboarding_outbox o WHERE o.event_id=(p_envelope->>'event_id')::UUID),
         EXISTS(SELECT 1 FROM public.direct_onboarding_outbox o
          WHERE o.event_id=(p_envelope->>'event_id')::UUID AND o.event_type=p_envelope->>'outbox_event_type'
            AND o.payload_digest=p_envelope->>'outbox_payload_digest'
            AND (p_envelope->>'operation'<>'COMPLIANCE_DECIDE'
              OR o.payload->>'decision_id'=p_envelope->>'operation_id'))
    INTO outbox_present,outbox_ok;
  IF p_envelope->>'credential_id' IS NULL THEN
    credential_ok:=p_envelope->>'credential_digest_key_id' IS NULL AND p_envelope->>'credential_digest' IS NULL;
  ELSIF p_envelope->>'operation' IN ('HANDOFF_CREATE','HANDOFF_REGENERATE') THEN
    SELECT EXISTS(SELECT 1 FROM public.institution_admin_handoff_credential c
      JOIN public.direct_onboarding_receipt r ON r.receipt_id=(p_envelope->>'receipt_id')::UUID
      WHERE c.credential_id=(p_envelope->>'credential_id')::UUID
        AND c.credential_digest_key_id=p_envelope->>'credential_digest_key_id'
        AND c.credential_digest=p_envelope->>'credential_digest'
        AND r.response_payload->>'credential_id'=c.credential_id::TEXT
        AND (r.response_payload->>'credential_issued_at')::TIMESTAMPTZ=c.issued_at
        AND (r.response_payload->>'credential_expires_at')::TIMESTAMPTZ=c.expires_at
        AND c.expires_at=c.issued_at+INTERVAL '24 hours') INTO credential_ok;
  ELSIF p_envelope->>'operation' IN ('CREATE','REGENERATE') THEN
    SELECT EXISTS(SELECT 1 FROM public.direct_institution_activation_credential c
      JOIN public.direct_onboarding_receipt r ON r.receipt_id=(p_envelope->>'receipt_id')::UUID
      WHERE c.credential_id=(p_envelope->>'credential_id')::UUID
        AND c.credential_digest_key_id=p_envelope->>'credential_digest_key_id'
        AND c.credential_digest=p_envelope->>'credential_digest'
        AND r.response_payload->>'credential_id'=c.credential_id::TEXT
        AND (r.response_payload->>'credential_issued_at')::TIMESTAMPTZ=c.issued_at
        AND (r.response_payload->>'credential_expires_at')::TIMESTAMPTZ=c.expires_at
        AND c.expires_at=(((c.issued_at AT TIME ZONE 'Asia/Shanghai')::DATE+30)::TIMESTAMP AT TIME ZONE 'Asia/Shanghai')) INTO credential_ok;
  ELSIF p_envelope->>'operation'='REVOKE' THEN
    SELECT EXISTS(SELECT 1 FROM public.direct_institution_activation_credential c
      WHERE c.credential_id=(p_envelope->>'credential_id')::UUID
        AND c.onboarding_id=(p_envelope->>'target_id')::UUID
        AND c.status='REVOKED'
        AND p_envelope->>'credential_digest_key_id' IS NULL
        AND p_envelope->>'credential_digest' IS NULL) INTO credential_ok;
  ELSIF p_envelope->>'operation' LIKE 'HANDOFF_%' THEN
    SELECT EXISTS(SELECT 1 FROM public.institution_admin_handoff_credential c
      WHERE c.credential_id=(p_envelope->>'credential_id')::UUID
        AND c.credential_digest_key_id=p_envelope->>'credential_digest_key_id'
        AND c.credential_digest=p_envelope->>'credential_digest') INTO credential_ok;
  ELSE
    SELECT EXISTS(SELECT 1 FROM public.direct_institution_activation_credential c
      WHERE c.credential_id=(p_envelope->>'credential_id')::UUID
        AND c.credential_digest_key_id=p_envelope->>'credential_digest_key_id'
        AND c.credential_digest=p_envelope->>'credential_digest') INTO credential_ok;
  END IF;
  IF p_envelope->>'phone_claim_state' IS NULL THEN claim_ok:=TRUE;
  ELSIF p_envelope->>'operation' LIKE 'HANDOFF_%' THEN
    SELECT EXISTS(SELECT 1 FROM public.identity_phone_claim c WHERE c.claim_kind='ADMIN_HANDOFF'
      AND c.claim_ref=(p_envelope->>'target_id')::UUID AND c.state=p_envelope->>'phone_claim_state') INTO claim_ok;
  ELSE
    SELECT EXISTS(SELECT 1 FROM public.identity_phone_claim c WHERE c.claim_kind='DIRECT_ORG_ADMIN'
      AND c.claim_ref=(p_envelope->>'target_id')::UUID AND c.state=p_envelope->>'phone_claim_state') INTO claim_ok;
  END IF;
  IF p_envelope->>'operation' IN ('CREATE','REGENERATE','REVOKE') THEN
    SELECT target_present AND EXISTS(
      SELECT 1 FROM public.tenant t
      JOIN public.institution_tenant_origin origin
        ON origin.tenant_id=t.id
       AND origin.tenant_public_id=(p_envelope->>'tenant_public_id')::UUID
       AND origin.origin_type='DIRECT_PROVISIONING'
       AND origin.direct_onboarding_id=root_row.onboarding_id
      WHERE t.id=root_row.tenant_id
        AND t.status::TEXT=p_envelope->>'tenant_status'
        AND root_row.tenant_public_id=(p_envelope->>'tenant_public_id')::UUID
    ) INTO tenant_ok;
  ELSIF p_envelope->>'tenant_id' IS NULL THEN tenant_ok:=FALSE;
  ELSE SELECT EXISTS(SELECT 1 FROM public.tenant t WHERE t.id=(p_envelope->>'tenant_id')::BIGINT
    AND t.status::TEXT=p_envelope->>'tenant_status') INTO tenant_ok; END IF;
  IF p_envelope->>'revision_id' IS NULL THEN revision_ok:=p_envelope->>'revision_status' IS NULL;
  ELSE SELECT EXISTS(SELECT 1 FROM public.direct_institution_compliance_revision r
    WHERE r.revision_id=(p_envelope->>'revision_id')::UUID AND r.status=p_envelope->>'revision_status') INTO revision_ok; END IF;
  IF target_ok AND receipt_ok AND audit_ok AND outbox_ok AND credential_ok AND claim_ok AND tenant_ok AND revision_ok THEN
    RETURN jsonb_build_object('outcome','COMMITTED','confirmed_postimage_digest',p_envelope->>'expected_postimage_digest');
  END IF;
  IF p_envelope->>'operation'='CREATE' AND NOT target_present
     AND NOT receipt_present AND NOT audit_present AND NOT outbox_present
     AND NOT EXISTS(SELECT 1 FROM public.direct_institution_activation_credential c
       WHERE c.credential_id=(p_envelope->>'credential_id')::UUID)
     AND NOT EXISTS(SELECT 1 FROM public.identity_phone_claim c
       WHERE c.claim_kind='DIRECT_ORG_ADMIN' AND c.claim_ref=(p_envelope->>'target_id')::UUID)
     AND NOT EXISTS(SELECT 1 FROM public.institution_tenant_origin origin
       WHERE origin.tenant_public_id=(p_envelope->>'tenant_public_id')::UUID
          OR origin.direct_onboarding_id=(p_envelope->>'target_id')::UUID) THEN
    RETURN jsonb_build_object('outcome','NOT_COMMITTED','confirmed_postimage_digest',NULL);
  END IF;
  IF NOT receipt_present AND NOT audit_present AND NOT outbox_present AND target_present
     AND ((p_envelope->>'operation' LIKE 'HANDOFF_%' AND handoff_row.version=(p_envelope->>'expected_version')::BIGINT)
       OR (p_envelope->>'operation' NOT LIKE 'HANDOFF_%' AND root_row.version=(p_envelope->>'expected_version')::BIGINT)) THEN
    RETURN jsonb_build_object('outcome','NOT_COMMITTED','confirmed_postimage_digest',NULL);
  END IF;
  RETURN jsonb_build_object('outcome','UNKNOWN','confirmed_postimage_digest',NULL);
END $$;

CREATE FUNCTION public.direct_activation_replay_v1(
  p_credential_id UUID,p_idempotency_key VARCHAR,p_request_digest CHAR(64),p_request_digest_candidates JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE receipt_row public.direct_onboarding_receipt%ROWTYPE;
BEGIN
  IF session_user<>'{onboarding}' THEN RAISE EXCEPTION 'DIRECT_ACTIVATION_REPLAY_FORBIDDEN' USING ERRCODE='42501'; END IF;
  SELECT * INTO receipt_row FROM public.direct_onboarding_receipt
   WHERE actor_scope='CREDENTIAL_HOLDER:ACTIVATE:'||p_credential_id::TEXT
     AND operation='ACTIVATE' AND idempotency_key=p_idempotency_key;
  IF NOT FOUND THEN RETURN NULL; END IF;
  IF receipt_row.request_digest<>public.direct_keyed_digest_candidate_v1(
       p_request_digest_candidates,receipt_row.digest_key_id) THEN
    RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT' USING ERRCODE='23505';
  END IF;
  IF receipt_row.response_payload IS NULL
     OR jsonb_typeof(receipt_row.response_payload)<>'object'
     OR NOT receipt_row.response_payload ?& ARRAY[
       'onboarding_id','tenant_id','institution_code','institution_name','institution_type',
       'administrative_region_id','status','compliance_due_at','current_revision_id','version']
     OR (receipt_row.response_payload-ARRAY[
       'onboarding_id','tenant_id','institution_code','institution_name','institution_type',
       'administrative_region_id','status','compliance_due_at','current_revision_id','version'])<>'{{}}'::jsonb
     OR receipt_row.response_payload->>'status'<>'ACTIVE_COMPLIANCE_PENDING' THEN
    RAISE EXCEPTION 'DIRECT_ACTIVATION_REPLAY_INVALID' USING ERRCODE='22023';
  END IF;
  RETURN receipt_row.response_payload;
END $$;

CREATE FUNCTION public.direct_activation_commit_confirm_v1(p_envelope JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE artifact_count INTEGER; all_match BOOLEAN; root_row public.direct_institution_onboarding%ROWTYPE;
BEGIN
  IF session_user<>'{onboarding}' THEN RAISE EXCEPTION 'DIRECT_ACTIVATION_CONFIRM_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF jsonb_typeof(p_envelope)<>'object' OR NOT p_envelope ?& ARRAY[
       'operation_id','idempotency_key','request_digest','digest_key_id','expected_version','audit_id','event_id','receipt_id',
       'expected_postimage_digest','operation','target_id','target_status','target_version','credential_id',
       'credential_digest_key_id','credential_digest','phone_claim_state',
       'tenant_status','revision_id','revision_status','audit_action','audit_evidence_digest',
       'outbox_event_type','outbox_payload_digest','receipt_response_digest']
     OR (p_envelope-ARRAY[
       'operation_id','idempotency_key','request_digest','digest_key_id','expected_version','audit_id','event_id','receipt_id',
       'expected_postimage_digest','operation','target_id','target_status','target_version','credential_id',
       'credential_digest_key_id','credential_digest','phone_claim_state',
       'tenant_status','revision_id','revision_status','audit_action','audit_evidence_digest',
       'outbox_event_type','outbox_payload_digest','receipt_response_digest'])<>'{{}}'::jsonb
     OR p_envelope->>'operation'<>'ACTIVATE' OR p_envelope->>'revision_id' IS NOT NULL
     OR p_envelope->>'revision_status' IS NOT NULL THEN
    RAISE EXCEPTION 'DIRECT_ACTIVATION_CONFIRM_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT * INTO root_row FROM public.direct_institution_onboarding WHERE onboarding_id=(p_envelope->>'target_id')::UUID;
  SELECT
    (SELECT count(*) FROM public.direct_onboarding_receipt r WHERE r.receipt_id=(p_envelope->>'receipt_id')::UUID)
    +(SELECT count(*) FROM public.direct_onboarding_audit a WHERE a.audit_id=(p_envelope->>'audit_id')::UUID)
    +(SELECT count(*) FROM public.direct_onboarding_outbox o WHERE o.event_id=(p_envelope->>'event_id')::UUID),
    FOUND
      AND root_row.status=p_envelope->>'target_status' AND root_row.version=(p_envelope->>'target_version')::BIGINT
      AND root_row.tenant_id IS NOT NULL AND root_row.activated_user_id IS NOT NULL
      AND EXISTS(SELECT 1 FROM public.tenant t WHERE t.id=root_row.tenant_id AND t.status::TEXT=p_envelope->>'tenant_status')
      AND EXISTS(SELECT 1 FROM public.direct_institution_activation_credential c
        WHERE c.credential_id=(p_envelope->>'credential_id')::UUID AND c.status='CONSUMED'
          AND c.credential_digest_key_id=p_envelope->>'credential_digest_key_id'
          AND c.credential_digest=p_envelope->>'credential_digest'
          AND c.issued_at IS NOT NULL AND c.expires_at>c.issued_at)
      AND EXISTS(SELECT 1 FROM public.identity_phone_claim c WHERE c.claim_kind='DIRECT_ORG_ADMIN'
        AND c.claim_ref=root_row.onboarding_id AND c.state=p_envelope->>'phone_claim_state'
        AND c.user_id=root_row.activated_user_id)
      AND EXISTS(SELECT 1 FROM public.direct_institution_admin_account a
        WHERE a.user_id=root_row.activated_user_id AND a.onboarding_id=root_row.onboarding_id
          AND a.activation_credential_id=(p_envelope->>'credential_id')::UUID)
      AND EXISTS(SELECT 1 FROM public.direct_onboarding_receipt r WHERE r.receipt_id=(p_envelope->>'receipt_id')::UUID
        AND r.actor_scope='CREDENTIAL_HOLDER:ACTIVATE:'||(p_envelope->>'credential_id')
        AND r.operation='ACTIVATE' AND r.idempotency_key=p_envelope->>'idempotency_key'
        AND r.digest_key_id=p_envelope->>'digest_key_id'
        AND r.request_digest=p_envelope->>'request_digest' AND r.postimage_digest=p_envelope->>'receipt_response_digest')
      AND EXISTS(SELECT 1 FROM public.direct_onboarding_audit a WHERE a.audit_id=(p_envelope->>'audit_id')::UUID
        AND a.action=p_envelope->>'audit_action' AND a.evidence_digest=p_envelope->>'audit_evidence_digest')
      AND EXISTS(SELECT 1 FROM public.direct_onboarding_outbox o WHERE o.event_id=(p_envelope->>'event_id')::UUID
        AND o.event_type=p_envelope->>'outbox_event_type' AND o.payload_digest=p_envelope->>'outbox_payload_digest')
    INTO artifact_count,all_match;
  IF all_match THEN RETURN jsonb_build_object('outcome','COMMITTED','confirmed_postimage_digest',p_envelope->>'expected_postimage_digest'); END IF;
  IF artifact_count=0 AND FOUND AND root_row.version=(p_envelope->>'expected_version')::BIGINT THEN
    RETURN jsonb_build_object('outcome','NOT_COMMITTED','confirmed_postimage_digest',NULL);
  END IF;
  RETURN jsonb_build_object('outcome','UNKNOWN','confirmed_postimage_digest',NULL);
END $$;

CREATE FUNCTION public.direct_compliance_save_replay_v1(
  p_actor_user_id BIGINT,p_actor_scope VARCHAR,p_idempotency_key VARCHAR,p_request_digest CHAR(64),p_request_digest_candidates JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor_row public."user"%ROWTYPE; receipt_row public.direct_onboarding_receipt%ROWTYPE;
BEGIN
  IF session_user<>'{onboarding}' THEN RAISE EXCEPTION 'DIRECT_COMPLIANCE_REPLAY_FORBIDDEN' USING ERRCODE='42501'; END IF;
  SELECT * INTO actor_row FROM public."user" WHERE id=p_actor_user_id;
  IF NOT FOUND OR actor_row.role::TEXT<>'org_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id IS NULL THEN RETURN NULL; END IF;
  SELECT * INTO receipt_row FROM public.direct_onboarding_receipt
   WHERE actor_scope=p_actor_scope AND operation='COMPLIANCE_SAVE' AND idempotency_key=p_idempotency_key;
  IF NOT FOUND THEN RETURN NULL; END IF;
  IF receipt_row.request_digest<>public.direct_keyed_digest_candidate_v1(
       p_request_digest_candidates,receipt_row.digest_key_id) THEN
    RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT' USING ERRCODE='23505';
  END IF;
  RETURN receipt_row.response_payload;
END $$;

CREATE FUNCTION public.direct_compliance_save_commit_confirm_v1(p_envelope JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor_row public."user"%ROWTYPE; root_row public.direct_institution_onboarding%ROWTYPE;
        revision_row public.direct_institution_compliance_revision%ROWTYPE;
        artifact_count INTEGER; all_match BOOLEAN;
BEGIN
  IF session_user<>'{onboarding}' THEN RAISE EXCEPTION 'DIRECT_COMPLIANCE_CONFIRM_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF jsonb_typeof(p_envelope)<>'object' OR NOT p_envelope ?& ARRAY[
       'operation_id','actor_user_id','actor_role','actor_scope','idempotency_key','request_digest','digest_key_id','expected_version',
       'audit_id','receipt_id','expected_postimage_digest','operation','onboarding_id','target_status','target_version',
       'tenant_id','tenant_status','revision_id','revision_no','revision_status','compliance_schema_version',
       'compliance_payload_digest_key_id','compliance_payload_digest',
       'unified_social_credit_code_digest_key_id','unified_social_credit_code_digest',
       'license_count','license_set_digest_key_id','license_set_digest','service_tags','licenses',
       'audit_action','audit_evidence_digest','receipt_response_digest']
     OR (p_envelope-ARRAY[
       'operation_id','actor_user_id','actor_role','actor_scope','idempotency_key','request_digest','digest_key_id','expected_version',
       'audit_id','receipt_id','expected_postimage_digest','operation','onboarding_id','target_status','target_version',
       'tenant_id','tenant_status','revision_id','revision_no','revision_status','compliance_schema_version',
       'compliance_payload_digest_key_id','compliance_payload_digest',
       'unified_social_credit_code_digest_key_id','unified_social_credit_code_digest',
       'license_count','license_set_digest_key_id','license_set_digest','service_tags','licenses',
       'audit_action','audit_evidence_digest','receipt_response_digest'])<>'{{}}'::jsonb
     OR p_envelope->>'operation'<>'COMPLIANCE_SAVE'
     OR p_envelope->>'revision_status'<>'DRAFT'
     OR (p_envelope->>'compliance_schema_version')::SMALLINT<>1
     OR jsonb_typeof(p_envelope->'service_tags')<>'array'
     OR jsonb_typeof(p_envelope->'licenses')<>'array'
     OR (p_envelope->>'license_count')::SMALLINT<>jsonb_array_length(p_envelope->'licenses')
     OR p_envelope->>'expected_postimage_digest'<>p_envelope->>'receipt_response_digest'
     OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_envelope->'licenses') item
                GROUP BY item->>'license_id' HAVING count(*)>1) THEN
    RAISE EXCEPTION 'DIRECT_COMPLIANCE_CONFIRM_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT * INTO actor_row FROM public."user" WHERE id=(p_envelope->>'actor_user_id')::BIGINT;
  IF NOT FOUND OR actor_row.role::TEXT<>'org_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id<>(p_envelope->>'tenant_id')::BIGINT OR p_envelope->>'actor_role'<>'org_admin' THEN
    RETURN jsonb_build_object('outcome','UNKNOWN');
  END IF;
  SELECT * INTO root_row FROM public.direct_institution_onboarding
   WHERE onboarding_id=(p_envelope->>'onboarding_id')::UUID;
  SELECT * INTO revision_row FROM public.direct_institution_compliance_revision
   WHERE revision_id=(p_envelope->>'revision_id')::UUID
     AND onboarding_id=(p_envelope->>'onboarding_id')::UUID;
  SELECT
    (SELECT count(*) FROM public.direct_onboarding_receipt r WHERE r.receipt_id=(p_envelope->>'receipt_id')::UUID)
    +(SELECT count(*) FROM public.direct_onboarding_audit a WHERE a.audit_id=(p_envelope->>'audit_id')::UUID),
    root_row.onboarding_id IS NOT NULL AND root_row.version>=(p_envelope->>'target_version')::BIGINT
      AND root_row.tenant_id=(p_envelope->>'tenant_id')::BIGINT
      AND EXISTS(SELECT 1 FROM public.tenant t WHERE t.id=root_row.tenant_id AND t.status::TEXT=p_envelope->>'tenant_status')
      AND revision_row.revision_id=(p_envelope->>'revision_id')::UUID
      AND revision_row.created_operation_id=(p_envelope->>'operation_id')::UUID
      AND revision_row.revision_no=(p_envelope->>'revision_no')::INTEGER
      AND revision_row.compliance_schema_version=(p_envelope->>'compliance_schema_version')::SMALLINT
      AND revision_row.compliance_payload_digest_key_id=p_envelope->>'compliance_payload_digest_key_id'
      AND revision_row.compliance_payload_digest=p_envelope->>'compliance_payload_digest'
      AND revision_row.unified_social_credit_code_digest_key_id=p_envelope->>'unified_social_credit_code_digest_key_id'
      AND revision_row.unified_social_credit_code_digest=p_envelope->>'unified_social_credit_code_digest'
      AND revision_row.license_count=(p_envelope->>'license_count')::SMALLINT
      AND revision_row.license_set_digest_key_id=p_envelope->>'license_set_digest_key_id'
      AND revision_row.license_set_digest=p_envelope->>'license_set_digest'
      AND revision_row.service_tags=p_envelope->'service_tags'
      AND revision_row.submitted_by=actor_row.id
      AND (SELECT count(*) FROM public.direct_institution_license l
            WHERE l.revision_id=revision_row.revision_id)=revision_row.license_count
      AND NOT EXISTS(
        SELECT 1 FROM jsonb_array_elements(p_envelope->'licenses') item
        LEFT JOIN public.direct_institution_license l
          ON l.revision_id=revision_row.revision_id AND l.license_id=(item->>'license_id')::UUID
        WHERE l.license_id IS NULL OR l.license_type<>item->>'license_type'
           OR l.private_file_id<>(item->>'private_file_id')::UUID
           OR l.valid_from<>(item->>'valid_from')::DATE OR l.valid_until<>(item->>'valid_until')::DATE
           OR l.license_no_key_id IS DISTINCT FROM item->>'license_no_key_id'
           OR l.license_no_digest_key_id IS DISTINCT FROM item->>'license_no_digest_key_id'
           OR l.license_no_digest IS DISTINCT FROM item->>'license_no_digest'
           OR encode(l.license_no_ciphertext,'base64') IS DISTINCT FROM item->>'license_no_ciphertext')
      AND EXISTS(SELECT 1 FROM public.direct_onboarding_receipt r WHERE r.receipt_id=(p_envelope->>'receipt_id')::UUID
        AND r.actor_scope=p_envelope->>'actor_scope' AND r.operation='COMPLIANCE_SAVE'
        AND r.idempotency_key=p_envelope->>'idempotency_key' AND r.digest_key_id=p_envelope->>'digest_key_id'
        AND r.request_digest=p_envelope->>'request_digest'
        AND r.postimage_digest=p_envelope->>'receipt_response_digest'
        AND r.response_payload->>'onboarding_id'=p_envelope->>'onboarding_id'
        AND r.response_payload->>'revision_id'=p_envelope->>'revision_id'
        AND r.response_payload->>'status'=p_envelope->>'target_status'
        AND (r.response_payload->>'version')::BIGINT=(p_envelope->>'target_version')::BIGINT)
      AND EXISTS(SELECT 1 FROM public.direct_onboarding_audit a WHERE a.audit_id=(p_envelope->>'audit_id')::UUID
        AND a.actor_user_id=actor_row.id AND a.object_id=root_row.onboarding_id
        AND a.action=p_envelope->>'audit_action'
        AND a.evidence_digest=p_envelope->>'audit_evidence_digest')
    INTO artifact_count,all_match;
  IF all_match THEN RETURN jsonb_build_object('outcome','COMMITTED','confirmed_postimage_digest',p_envelope->>'expected_postimage_digest'); END IF;
  IF artifact_count=0 AND revision_row.revision_id IS NULL
     AND root_row.version=(p_envelope->>'expected_version')::BIGINT THEN
    RETURN jsonb_build_object('outcome','NOT_COMMITTED','confirmed_postimage_digest',NULL);
  END IF;
  RETURN jsonb_build_object('outcome','UNKNOWN','confirmed_postimage_digest',NULL);
END $$;

CREATE FUNCTION public.direct_compliance_submit_replay_v1(
  p_actor_user_id BIGINT,p_actor_scope VARCHAR,p_idempotency_key VARCHAR,p_request_digest CHAR(64),p_request_digest_candidates JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor_row public."user"%ROWTYPE; receipt_row public.direct_onboarding_receipt%ROWTYPE;
BEGIN
  IF session_user<>'{onboarding}' THEN RAISE EXCEPTION 'DIRECT_COMPLIANCE_REPLAY_FORBIDDEN' USING ERRCODE='42501'; END IF;
  SELECT * INTO actor_row FROM public."user" WHERE id=p_actor_user_id;
  IF NOT FOUND OR actor_row.role::TEXT<>'org_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id IS NULL THEN RETURN NULL; END IF;
  SELECT * INTO receipt_row FROM public.direct_onboarding_receipt
   WHERE actor_scope=p_actor_scope AND operation='COMPLIANCE_SUBMIT' AND idempotency_key=p_idempotency_key;
  IF NOT FOUND THEN RETURN NULL; END IF;
  IF receipt_row.request_digest<>public.direct_keyed_digest_candidate_v1(
       p_request_digest_candidates,receipt_row.digest_key_id) THEN
    RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT' USING ERRCODE='23505';
  END IF;
  RETURN receipt_row.response_payload;
END $$;

CREATE FUNCTION public.direct_compliance_submit_commit_confirm_v1(p_envelope JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor_row public."user"%ROWTYPE; root_row public.direct_institution_onboarding%ROWTYPE;
        artifact_count INTEGER; all_match BOOLEAN;
BEGIN
  IF session_user<>'{onboarding}' THEN RAISE EXCEPTION 'DIRECT_COMPLIANCE_CONFIRM_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF jsonb_typeof(p_envelope)<>'object' OR NOT p_envelope ?& ARRAY[
       'operation_id','actor_user_id','actor_role','actor_scope','idempotency_key','request_digest','digest_key_id','expected_version',
       'audit_id','event_id','receipt_id','expected_postimage_digest','operation','target_id','target_status','target_version',
       'credential_id','credential_digest_key_id','credential_digest','credential_issued_at','credential_expires_at',
       'phone_claim_state','tenant_id','tenant_status','user_id','revision_id','revision_status','audit_action',
       'audit_evidence_digest','outbox_event_type','outbox_payload_digest','receipt_response_digest']
     OR (p_envelope-ARRAY[
       'operation_id','actor_user_id','actor_role','actor_scope','idempotency_key','request_digest','digest_key_id','expected_version',
       'audit_id','event_id','receipt_id','expected_postimage_digest','operation','target_id','target_status','target_version',
       'credential_id','credential_digest_key_id','credential_digest','credential_issued_at','credential_expires_at',
       'phone_claim_state','tenant_id','tenant_status','user_id','revision_id','revision_status','audit_action',
       'audit_evidence_digest','outbox_event_type','outbox_payload_digest','receipt_response_digest'])<>'{{}}'::jsonb
     OR p_envelope->>'operation'<>'COMPLIANCE_SUBMIT' OR p_envelope->>'credential_id' IS NOT NULL
     OR p_envelope->>'credential_digest_key_id' IS NOT NULL OR p_envelope->>'credential_digest' IS NOT NULL
     OR p_envelope->>'credential_issued_at' IS NOT NULL OR p_envelope->>'credential_expires_at' IS NOT NULL
     OR p_envelope->>'phone_claim_state' IS NOT NULL OR p_envelope->>'user_id' IS NOT NULL THEN
    RAISE EXCEPTION 'DIRECT_COMPLIANCE_CONFIRM_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT * INTO actor_row FROM public."user" WHERE id=(p_envelope->>'actor_user_id')::BIGINT;
  IF NOT FOUND OR actor_row.role::TEXT<>'org_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id<>(p_envelope->>'tenant_id')::BIGINT OR p_envelope->>'actor_role'<>'org_admin' THEN
    RETURN jsonb_build_object('outcome','UNKNOWN');
  END IF;
  SELECT * INTO root_row FROM public.direct_institution_onboarding WHERE onboarding_id=(p_envelope->>'target_id')::UUID;
  SELECT
    (SELECT count(*) FROM public.direct_onboarding_receipt r WHERE r.receipt_id=(p_envelope->>'receipt_id')::UUID)
    +(SELECT count(*) FROM public.direct_onboarding_audit a WHERE a.audit_id=(p_envelope->>'audit_id')::UUID)
    +(SELECT count(*) FROM public.direct_onboarding_outbox o WHERE o.event_id=(p_envelope->>'event_id')::UUID),
    FOUND AND root_row.status=p_envelope->>'target_status' AND root_row.version=(p_envelope->>'target_version')::BIGINT
      AND root_row.tenant_id=(p_envelope->>'tenant_id')::BIGINT
      AND EXISTS(SELECT 1 FROM public.tenant t WHERE t.id=root_row.tenant_id AND t.status::TEXT=p_envelope->>'tenant_status')
      AND EXISTS(SELECT 1 FROM public.direct_institution_compliance_revision r
        WHERE r.revision_id=(p_envelope->>'revision_id')::UUID AND r.onboarding_id=root_row.onboarding_id
          AND r.status=p_envelope->>'revision_status')
      AND EXISTS(SELECT 1 FROM public.direct_onboarding_receipt r WHERE r.receipt_id=(p_envelope->>'receipt_id')::UUID
        AND r.actor_scope=p_envelope->>'actor_scope' AND r.operation='COMPLIANCE_SUBMIT'
        AND r.idempotency_key=p_envelope->>'idempotency_key' AND r.digest_key_id=p_envelope->>'digest_key_id'
        AND r.request_digest=p_envelope->>'request_digest'
        AND r.postimage_digest=p_envelope->>'receipt_response_digest')
      AND EXISTS(SELECT 1 FROM public.direct_onboarding_audit a WHERE a.audit_id=(p_envelope->>'audit_id')::UUID
        AND a.actor_user_id=actor_row.id AND a.action=p_envelope->>'audit_action'
        AND a.evidence_digest=p_envelope->>'audit_evidence_digest')
      AND EXISTS(SELECT 1 FROM public.direct_onboarding_outbox o WHERE o.event_id=(p_envelope->>'event_id')::UUID
        AND o.event_type=p_envelope->>'outbox_event_type' AND o.payload_digest=p_envelope->>'outbox_payload_digest')
    INTO artifact_count,all_match;
  IF all_match THEN RETURN jsonb_build_object('outcome','COMMITTED','confirmed_postimage_digest',p_envelope->>'expected_postimage_digest'); END IF;
  IF artifact_count=0 AND FOUND AND root_row.version=(p_envelope->>'expected_version')::BIGINT THEN
    RETURN jsonb_build_object('outcome','NOT_COMMITTED','confirmed_postimage_digest',NULL);
  END IF;
  RETURN jsonb_build_object('outcome','UNKNOWN','confirmed_postimage_digest',NULL);
END $$;

CREATE FUNCTION public.admin_handoff_activation_replay_v1(
  p_credential_id UUID,p_idempotency_key VARCHAR,p_request_digest CHAR(64),p_request_digest_candidates JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE receipt_row public.direct_onboarding_receipt%ROWTYPE;
BEGIN
  IF session_user<>'{onboarding}' THEN RAISE EXCEPTION 'HANDOFF_ACTIVATION_REPLAY_FORBIDDEN' USING ERRCODE='42501'; END IF;
  SELECT * INTO receipt_row FROM public.direct_onboarding_receipt
   WHERE actor_scope='CREDENTIAL_HOLDER:HANDOFF_ACTIVATE:'||p_credential_id::TEXT
     AND operation='HANDOFF_ACTIVATE' AND idempotency_key=p_idempotency_key;
  IF NOT FOUND THEN RETURN NULL; END IF;
  IF receipt_row.request_digest<>public.direct_keyed_digest_candidate_v1(
       p_request_digest_candidates,receipt_row.digest_key_id) THEN
    RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT' USING ERRCODE='23505';
  END IF;
  IF receipt_row.response_payload IS NULL
     OR jsonb_typeof(receipt_row.response_payload)<>'object'
     OR NOT receipt_row.response_payload ?& ARRAY[
       'handoff_id','onboarding_id','tenant_id','status','version']
     OR (receipt_row.response_payload-ARRAY[
       'handoff_id','onboarding_id','tenant_id','status','version'])<>'{{}}'::jsonb
     OR receipt_row.response_payload->>'status'<>'ACTIVATED' THEN
    RAISE EXCEPTION 'HANDOFF_ACTIVATION_REPLAY_INVALID' USING ERRCODE='22023';
  END IF;
  RETURN receipt_row.response_payload;
END $$;

CREATE FUNCTION public.admin_handoff_activation_commit_confirm_v1(p_envelope JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE artifact_count INTEGER; all_match BOOLEAN; handoff_row public.institution_admin_handoff%ROWTYPE;
BEGIN
  IF session_user<>'{onboarding}' THEN RAISE EXCEPTION 'HANDOFF_ACTIVATION_CONFIRM_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF jsonb_typeof(p_envelope)<>'object' OR NOT p_envelope ?& ARRAY[
       'operation_id','idempotency_key','request_digest','digest_key_id','expected_version','audit_id','event_id','receipt_id',
       'expected_postimage_digest','operation','target_id','target_status','target_version','credential_id',
       'credential_digest_key_id','credential_digest','credential_issued_at','credential_expires_at','phone_claim_state',
       'tenant_id','tenant_status','user_id','revision_id','revision_status','audit_action','audit_evidence_digest',
       'outbox_event_type','outbox_payload_digest','receipt_response_digest']
     OR (p_envelope-ARRAY[
       'operation_id','idempotency_key','request_digest','digest_key_id','expected_version','audit_id','event_id','receipt_id',
       'expected_postimage_digest','operation','target_id','target_status','target_version','credential_id',
       'credential_digest_key_id','credential_digest','credential_issued_at','credential_expires_at','phone_claim_state',
       'tenant_id','tenant_status','user_id','revision_id','revision_status','audit_action','audit_evidence_digest',
       'outbox_event_type','outbox_payload_digest','receipt_response_digest'])<>'{{}}'::jsonb
     OR p_envelope->>'operation'<>'HANDOFF_ACTIVATE' OR p_envelope->>'revision_id' IS NOT NULL
     OR p_envelope->>'revision_status' IS NOT NULL THEN
    RAISE EXCEPTION 'HANDOFF_ACTIVATION_CONFIRM_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT * INTO handoff_row FROM public.institution_admin_handoff WHERE handoff_id=(p_envelope->>'target_id')::UUID;
  SELECT
    (SELECT count(*) FROM public.direct_onboarding_receipt r WHERE r.receipt_id=(p_envelope->>'receipt_id')::UUID)
    +(SELECT count(*) FROM public.direct_onboarding_audit a WHERE a.audit_id=(p_envelope->>'audit_id')::UUID)
    +(SELECT count(*) FROM public.direct_onboarding_outbox o WHERE o.event_id=(p_envelope->>'event_id')::UUID),
    FOUND AND handoff_row.status=p_envelope->>'target_status' AND handoff_row.version=(p_envelope->>'target_version')::BIGINT
      AND handoff_row.new_user_id=(p_envelope->>'user_id')::BIGINT
      AND EXISTS(SELECT 1 FROM public.direct_institution_onboarding r WHERE r.onboarding_id=handoff_row.onboarding_id
        AND r.tenant_id=(p_envelope->>'tenant_id')::BIGINT AND r.activated_user_id=handoff_row.new_user_id)
      AND EXISTS(SELECT 1 FROM public.tenant t WHERE t.id=(p_envelope->>'tenant_id')::BIGINT
        AND t.status::TEXT=p_envelope->>'tenant_status')
      AND EXISTS(SELECT 1 FROM public.institution_admin_handoff_credential c
        WHERE c.credential_id=(p_envelope->>'credential_id')::UUID AND c.status='CONSUMED'
          AND c.credential_digest_key_id=p_envelope->>'credential_digest_key_id'
          AND c.credential_digest=p_envelope->>'credential_digest'
          AND c.issued_at=(p_envelope->>'credential_issued_at')::TIMESTAMPTZ
          AND c.expires_at=(p_envelope->>'credential_expires_at')::TIMESTAMPTZ)
      AND EXISTS(SELECT 1 FROM public.identity_phone_claim c WHERE c.claim_kind='ADMIN_HANDOFF'
        AND c.claim_ref=handoff_row.handoff_id AND c.state=p_envelope->>'phone_claim_state'
        AND c.user_id=handoff_row.new_user_id)
      AND EXISTS(SELECT 1 FROM public.direct_onboarding_receipt r WHERE r.receipt_id=(p_envelope->>'receipt_id')::UUID
        AND r.actor_scope='CREDENTIAL_HOLDER:HANDOFF_ACTIVATE:'||(p_envelope->>'credential_id')
        AND r.operation='HANDOFF_ACTIVATE' AND r.idempotency_key=p_envelope->>'idempotency_key'
        AND r.digest_key_id=p_envelope->>'digest_key_id'
        AND r.request_digest=p_envelope->>'request_digest' AND r.postimage_digest=p_envelope->>'receipt_response_digest')
      AND EXISTS(SELECT 1 FROM public.direct_onboarding_audit a WHERE a.audit_id=(p_envelope->>'audit_id')::UUID
        AND a.action=p_envelope->>'audit_action' AND a.evidence_digest=p_envelope->>'audit_evidence_digest')
      AND EXISTS(SELECT 1 FROM public.direct_onboarding_outbox o WHERE o.event_id=(p_envelope->>'event_id')::UUID
        AND o.event_type=p_envelope->>'outbox_event_type' AND o.payload_digest=p_envelope->>'outbox_payload_digest')
    INTO artifact_count,all_match;
  IF all_match THEN RETURN jsonb_build_object('outcome','COMMITTED','confirmed_postimage_digest',p_envelope->>'expected_postimage_digest'); END IF;
  IF artifact_count=0 AND FOUND AND handoff_row.version=(p_envelope->>'expected_version')::BIGINT THEN
    RETURN jsonb_build_object('outcome','NOT_COMMITTED','confirmed_postimage_digest',NULL);
  END IF;
  RETURN jsonb_build_object('outcome','UNKNOWN','confirmed_postimage_digest',NULL);
END $$;

CREATE FUNCTION public.direct_institution_read_v1(
  p_actor_user_id BIGINT,p_resource VARCHAR,p_target_id UUID,p_cursor UUID,p_ceiling UUID,
  p_limit SMALLINT,p_status VARCHAR
) RETURNS TABLE(
  onboarding_id UUID,tenant_id UUID,institution_code VARCHAR,institution_name VARCHAR,
  institution_type VARCHAR,administrative_region_id BIGINT,status VARCHAR,compliance_due_at TIMESTAMPTZ,
  current_revision_id UUID,version BIGINT,snapshot_ceiling UUID,has_more BOOLEAN
) LANGUAGE plpgsql SECURITY DEFINER STABLE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor_row public."user"%ROWTYPE;
BEGIN
  IF session_user<>'{reader}' THEN RAISE EXCEPTION 'DIRECT_READ_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF p_resource NOT IN ('LIST','DETAIL') OR p_limit NOT BETWEEN 1 AND 100
     OR (p_resource='DETAIL' AND (p_target_id IS NULL OR p_cursor IS NOT NULL OR p_ceiling IS NOT NULL))
     OR (p_resource='LIST' AND p_target_id IS NOT NULL) THEN
    RAISE EXCEPTION 'DIRECT_READ_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT * INTO actor_row FROM public."user" WHERE id=p_actor_user_id;
  IF NOT FOUND OR actor_row.role::TEXT<>'super_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id IS NOT NULL THEN RETURN; END IF;
  IF p_resource='DETAIL' THEN
    RETURN QUERY SELECT r.onboarding_id,r.tenant_public_id,r.institution_code,r.institution_name,
      r.institution_type,r.administrative_region_id,r.status,r.compliance_due_at,r.current_revision_id,r.version,
      NULL::UUID,false
      FROM public.direct_institution_onboarding r WHERE r.onboarding_id=p_target_id;
  ELSE
    RETURN QUERY WITH snapshot AS (
      SELECT COALESCE(p_ceiling,(SELECT candidate.onboarding_id
        FROM public.direct_institution_onboarding candidate
       WHERE p_status IS NULL OR candidate.status=p_status
       ORDER BY candidate.onboarding_id DESC LIMIT 1)) AS snapshot_ceiling
    )
    SELECT r.onboarding_id,r.tenant_public_id,r.institution_code,r.institution_name,
      r.institution_type,r.administrative_region_id,r.status,r.compliance_due_at,r.current_revision_id,r.version,
      snapshot.snapshot_ceiling,
      EXISTS(SELECT 1 FROM public.direct_institution_onboarding following
        WHERE following.onboarding_id>r.onboarding_id
          AND following.onboarding_id<=snapshot.snapshot_ceiling
          AND (p_status IS NULL OR following.status=p_status))
      FROM public.direct_institution_onboarding r CROSS JOIN snapshot
     WHERE (p_cursor IS NULL OR r.onboarding_id>p_cursor)
       AND (snapshot.snapshot_ceiling IS NULL OR r.onboarding_id<=snapshot.snapshot_ceiling)
       AND (p_status IS NULL OR r.status=p_status)
     ORDER BY r.onboarding_id LIMIT p_limit;
  END IF;
END $$;

CREATE FUNCTION public.direct_compliance_current_v1(p_actor_user_id BIGINT)
RETURNS TABLE(
  onboarding_id UUID,tenant_public_id UUID,revision_id UUID,revision_no INTEGER,status VARCHAR,
  institution_name VARCHAR,institution_type VARCHAR,administrative_region_id BIGINT,
  institution_code VARCHAR,service_tags JSONB,correction_fields JSONB,root_version BIGINT,
  compliance_payload_digest_key_id VARCHAR,compliance_payload_digest CHAR(64),
  unified_social_credit_code_digest_key_id VARCHAR,unified_social_credit_code_digest CHAR(64),
  license_set_digest_key_id VARCHAR,license_set_digest CHAR(64),
  license_id UUID,license_type VARCHAR,license_no_ciphertext BYTEA,license_no_key_id VARCHAR,
  license_no_digest_key_id VARCHAR,license_no_digest CHAR(64),
  private_file_id UUID,valid_from DATE,valid_until DATE
) LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE
  actor_row public."user"%ROWTYPE;
  tenant_row public.tenant%ROWTYPE;
  root_row public.direct_institution_onboarding%ROWTYPE;
BEGIN
  IF session_user<>'{reader}' THEN
    RAISE EXCEPTION 'DIRECT_COMPLIANCE_READ_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  IF p_actor_user_id IS NULL OR p_actor_user_id<1 THEN
    RAISE EXCEPTION 'DIRECT_COMPLIANCE_READ_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT * INTO actor_row FROM public."user" WHERE id=p_actor_user_id FOR SHARE;
  IF NOT FOUND OR actor_row.role::TEXT<>'org_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id IS NULL THEN RETURN; END IF;
  SELECT * INTO tenant_row FROM public.tenant WHERE id=actor_row.tenant_id FOR SHARE;
  IF NOT FOUND OR tenant_row.status<>'active' THEN RETURN; END IF;
  SELECT direct.* INTO root_row
    FROM public.institution_tenant_origin origin
    JOIN public.direct_institution_onboarding direct
      ON origin.origin_type='DIRECT_PROVISIONING'
     AND origin.direct_onboarding_id=direct.onboarding_id
     AND origin.tenant_id=direct.tenant_id
     AND origin.tenant_public_id=direct.tenant_public_id
    JOIN public.direct_institution_admin_account account
      ON account.onboarding_id=direct.onboarding_id
     AND account.user_id=actor_row.id
     AND account.totp_enabled
   WHERE origin.tenant_id=tenant_row.id
     AND direct.activated_user_id=actor_row.id
     AND direct.status NOT IN ('PENDING_ACTIVATION','REVOKED_BEFORE_ACTIVATION')
   FOR SHARE OF direct;
  IF NOT FOUND THEN RETURN; END IF;
  RETURN QUERY
  SELECT current_root.onboarding_id,current_root.tenant_public_id,revision.revision_id,
    revision.revision_no,current_root.status,current_root.institution_name,
    current_root.institution_type,current_root.administrative_region_id,
    current_root.institution_code,COALESCE(revision.service_tags,'[]'::jsonb),
    COALESCE(revision.correction_fields,'[]'::jsonb),current_root.version,
    revision.compliance_payload_digest_key_id,revision.compliance_payload_digest,
    revision.unified_social_credit_code_digest_key_id,revision.unified_social_credit_code_digest,
    revision.license_set_digest_key_id,revision.license_set_digest,
    license.license_id,license.license_type,license.license_no_ciphertext,
    license.license_no_key_id,license.license_no_digest_key_id,license.license_no_digest,
    license.private_file_id,license.valid_from,license.valid_until
  FROM public.direct_institution_onboarding current_root
  LEFT JOIN public.direct_institution_compliance_revision revision
    ON revision.revision_id=current_root.current_revision_id
   AND revision.onboarding_id=current_root.onboarding_id
  LEFT JOIN public.direct_institution_license license
    ON license.revision_id=revision.revision_id
   AND license.onboarding_id=current_root.onboarding_id
   AND license.status<>'SUPERSEDED'
  WHERE current_root.onboarding_id=root_row.onboarding_id
  ORDER BY license.license_id;
END $$;

CREATE FUNCTION public.direct_org_admin_login_v1(p_user_id BIGINT)
RETURNS TABLE(
  source_kind VARCHAR,tenant_public_id UUID,onboarding_id UUID,credential_id UUID,
  totp_secret_ciphertext BYTEA,totp_key_id VARCHAR,totp_enabled BOOLEAN,
  account_version BIGINT,user_status VARCHAR,tenant_status VARCHAR,root_status VARCHAR
) LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE actor_row public."user"%ROWTYPE; tenant_row public.tenant%ROWTYPE;
BEGIN
  IF session_user<>'{application}' THEN
    RAISE EXCEPTION 'DIRECT_ORG_ADMIN_LOGIN_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  IF p_user_id IS NULL OR p_user_id<1 THEN
    RAISE EXCEPTION 'DIRECT_ORG_ADMIN_LOGIN_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT * INTO actor_row FROM public."user" WHERE id=p_user_id FOR SHARE;
  IF NOT FOUND OR actor_row.role::TEXT<>'org_admin' OR actor_row.status::TEXT<>'active'
     OR actor_row.tenant_id IS NULL THEN RETURN; END IF;
  SELECT * INTO tenant_row FROM public.tenant WHERE id=actor_row.tenant_id FOR SHARE;
  IF NOT FOUND OR tenant_row.status<>'active' THEN RETURN; END IF;
  RETURN QUERY
  SELECT CASE WHEN account.activation_credential_id IS NOT NULL
              THEN 'DIRECT_ACTIVATION'::VARCHAR ELSE 'ADMIN_HANDOFF'::VARCHAR END,
    direct.tenant_public_id,direct.onboarding_id,
    COALESCE(account.activation_credential_id,account.handoff_credential_id),
    account.totp_secret_ciphertext,account.totp_key_id,account.totp_enabled,
    account.version,actor_row.status::TEXT::VARCHAR,tenant_row.status::TEXT::VARCHAR,
    direct.status
  FROM public.institution_tenant_origin origin
  JOIN public.direct_institution_onboarding direct
    ON origin.origin_type='DIRECT_PROVISIONING'
   AND origin.direct_onboarding_id=direct.onboarding_id
   AND origin.tenant_id=direct.tenant_id
   AND origin.tenant_public_id=direct.tenant_public_id
  JOIN public.direct_institution_admin_account account
    ON account.onboarding_id=direct.onboarding_id AND account.user_id=actor_row.id
  WHERE origin.tenant_id=tenant_row.id
    AND direct.activated_user_id=actor_row.id
    AND direct.status NOT IN ('PENDING_ACTIVATION','REVOKED_BEFORE_ACTIVATION');
END $$;

CREATE FUNCTION public.direct_outbox_claim_v1(p_ceiling UUID,p_limit SMALLINT,p_worker_id VARCHAR)
RETURNS SETOF JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE event_row public.direct_onboarding_outbox%ROWTYPE; lease_value VARCHAR(128); now_value TIMESTAMPTZ:=clock_timestamp();
BEGIN
  IF session_user<>'{delivery}' THEN RAISE EXCEPTION 'DIRECT_OUTBOX_CLAIM_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF p_limit NOT BETWEEN 1 AND 100 OR p_worker_id IS NULL OR length(p_worker_id) NOT BETWEEN 1 AND 64 THEN
    RAISE EXCEPTION 'DIRECT_OUTBOX_CLAIM_INVALID' USING ERRCODE='22023'; END IF;
  FOR event_row IN SELECT * FROM public.direct_onboarding_outbox o
    WHERE o.event_id<=p_ceiling AND o.status IN ('PENDING','FAILED') AND o.attempts<3
    ORDER BY o.event_id FOR UPDATE SKIP LOCKED LIMIT p_limit
  LOOP
    lease_value:=encode(sha256(convert_to(event_row.event_id::TEXT||chr(31)||p_worker_id||chr(31)||now_value::TEXT,'UTF8')),'hex');
    UPDATE public.direct_onboarding_outbox SET status='PROCESSING',attempts=attempts+1,worker_id=p_worker_id,
      lease_token=lease_value,lease_until=now_value+INTERVAL '5 minutes',version=version+1
     WHERE event_id=event_row.event_id;
    RETURN NEXT jsonb_build_object('event_id',event_row.event_id,'event_type',event_row.event_type,
      'aggregate_id',event_row.aggregate_id,'payload',event_row.payload,'payload_digest',event_row.payload_digest,
      'worker_id',p_worker_id,'lease_token',lease_value,'version',event_row.version+1);
  END LOOP;
END $$;

CREATE FUNCTION public.direct_outbox_consume_v1(
  p_event_id UUID,p_worker_id VARCHAR,p_lease_token VARCHAR,p_expected_version BIGINT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE event_row public.direct_onboarding_outbox%ROWTYPE; now_value TIMESTAMPTZ:=clock_timestamp();
BEGIN
  IF session_user<>'{delivery}' THEN RAISE EXCEPTION 'DIRECT_OUTBOX_CONSUME_FORBIDDEN' USING ERRCODE='42501'; END IF;
  SELECT * INTO event_row FROM public.direct_onboarding_outbox WHERE event_id=p_event_id FOR UPDATE;
  IF NOT FOUND OR event_row.status<>'PROCESSING' OR event_row.worker_id<>p_worker_id
     OR event_row.lease_token<>p_lease_token OR event_row.lease_until<=now_value
     OR event_row.version<>p_expected_version THEN RETURN NULL; END IF;
  UPDATE public.direct_onboarding_outbox SET status='DELIVERED',delivered_at=now_value,
    worker_id=NULL,lease_token=NULL,lease_until=NULL,version=version+1 WHERE event_id=p_event_id;
  RETURN jsonb_build_object('event_id',p_event_id,'status','DELIVERED','version',event_row.version+1);
END $$;

CREATE FUNCTION public.direct_outbox_reopen_v1(
  p_event_id UUID,p_worker_id VARCHAR,p_lease_token VARCHAR,p_expected_version BIGINT,p_reason_code VARCHAR
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE event_row public.direct_onboarding_outbox%ROWTYPE; target_status VARCHAR(16);
BEGIN
  IF session_user<>'{delivery}' THEN RAISE EXCEPTION 'DIRECT_OUTBOX_REOPEN_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF p_reason_code IS NULL OR length(p_reason_code) NOT BETWEEN 1 AND 64 THEN
    RAISE EXCEPTION 'DIRECT_OUTBOX_REOPEN_INVALID' USING ERRCODE='22023'; END IF;
  SELECT * INTO event_row FROM public.direct_onboarding_outbox WHERE event_id=p_event_id FOR UPDATE;
  IF NOT FOUND OR event_row.status<>'PROCESSING' OR event_row.worker_id<>p_worker_id
     OR event_row.lease_token<>p_lease_token OR event_row.version<>p_expected_version THEN RETURN NULL; END IF;
  target_status:=CASE WHEN event_row.attempts>=3 THEN 'FAILED' ELSE 'PENDING' END;
  UPDATE public.direct_onboarding_outbox SET status=target_status,worker_id=NULL,lease_token=NULL,
    lease_until=NULL,version=version+1 WHERE event_id=p_event_id;
  RETURN jsonb_build_object('event_id',p_event_id,'status',target_status,'version',event_row.version+1,
    'reason_code',p_reason_code);
END $$;

CREATE FUNCTION public.direct_recovery_claim_v1(p_limit SMALLINT,p_worker_id VARCHAR)
RETURNS SETOF JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE event_row public.direct_onboarding_outbox%ROWTYPE; target_status VARCHAR(16); now_value TIMESTAMPTZ:=clock_timestamp();
BEGIN
  IF session_user<>'{delivery}' THEN RAISE EXCEPTION 'DIRECT_RECOVERY_CLAIM_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF p_limit NOT BETWEEN 1 AND 100 OR p_worker_id IS NULL OR length(p_worker_id) NOT BETWEEN 1 AND 64 THEN
    RAISE EXCEPTION 'DIRECT_RECOVERY_CLAIM_INVALID' USING ERRCODE='22023'; END IF;
  FOR event_row IN SELECT * FROM public.direct_onboarding_outbox o
    WHERE o.status='PROCESSING' AND o.lease_until<=now_value
    ORDER BY o.event_id FOR UPDATE SKIP LOCKED LIMIT p_limit
  LOOP
    target_status:=CASE WHEN event_row.attempts>=3 THEN 'FAILED' ELSE 'PENDING' END;
    UPDATE public.direct_onboarding_outbox SET status=target_status,worker_id=NULL,lease_token=NULL,
      lease_until=NULL,version=version+1 WHERE event_id=event_row.event_id;
    RETURN NEXT jsonb_build_object('event_id',event_row.event_id,'status',target_status,
      'version',event_row.version+1,'recovered_by',p_worker_id);
  END LOOP;
END $$;

CREATE FUNCTION public.controlled_org_admin_phone_reserve_v1(p_envelope JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF session_user<>'{review}' THEN RAISE EXCEPTION 'CONTROLLED_PHONE_RESERVE_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF jsonb_typeof(p_envelope)<>'object' OR NOT p_envelope ?& ARRAY['claim_id','claim_ref','phone_digest_key_id','phone_digest','phone_digest_candidates','expected_state']
     OR (p_envelope-ARRAY['claim_id','claim_ref','phone_digest_key_id','phone_digest','phone_digest_candidates','expected_state'])<>'{{}}'::jsonb THEN
    RAISE EXCEPTION 'CONTROLLED_PHONE_RESERVE_INVALID' USING ERRCODE='22023';
  END IF;
  RETURN public.identity_phone_claim_internal_v1(p_envelope||jsonb_build_object('action','RESERVE','claim_kind','CONTROLLED_ORG_ADMIN'));
END $$;

CREATE FUNCTION public.controlled_org_admin_phone_bind_v1(p_envelope JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF session_user<>'{onboarding}' THEN RAISE EXCEPTION 'CONTROLLED_PHONE_BIND_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF jsonb_typeof(p_envelope)<>'object' OR NOT p_envelope ?& ARRAY['claim_id','claim_ref','user_id','expected_version']
     OR (p_envelope-ARRAY['claim_id','claim_ref','user_id','expected_version'])<>'{{}}'::jsonb THEN
    RAISE EXCEPTION 'CONTROLLED_PHONE_BIND_INVALID' USING ERRCODE='22023';
  END IF;
  RETURN public.identity_phone_claim_internal_v1(p_envelope||jsonb_build_object('action','BIND','claim_kind','CONTROLLED_ORG_ADMIN'));
END $$;

CREATE FUNCTION public.controlled_org_admin_phone_release_v1(p_envelope JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF session_user<>'{review}' THEN RAISE EXCEPTION 'CONTROLLED_PHONE_RELEASE_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF jsonb_typeof(p_envelope)<>'object' OR NOT p_envelope ?& ARRAY['claim_id','claim_ref','expected_version']
     OR (p_envelope-ARRAY['claim_id','claim_ref','expected_version'])<>'{{}}'::jsonb THEN
    RAISE EXCEPTION 'CONTROLLED_PHONE_RELEASE_INVALID' USING ERRCODE='22023';
  END IF;
  RETURN public.identity_phone_claim_internal_v1(p_envelope||jsonb_build_object('action','RELEASE','claim_kind','CONTROLLED_ORG_ADMIN'));
END $$;

CREATE FUNCTION public.therapist_account_phone_reserve_v1(p_envelope JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF session_user<>'{therapist}' THEN RAISE EXCEPTION 'THERAPIST_PHONE_RESERVE_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF jsonb_typeof(p_envelope)<>'object' OR NOT p_envelope ?& ARRAY['claim_id','claim_ref','phone_digest_key_id','phone_digest','phone_digest_candidates','expected_state']
     OR (p_envelope-ARRAY['claim_id','claim_ref','phone_digest_key_id','phone_digest','phone_digest_candidates','expected_state'])<>'{{}}'::jsonb THEN
    RAISE EXCEPTION 'THERAPIST_PHONE_RESERVE_INVALID' USING ERRCODE='22023';
  END IF;
  RETURN public.identity_phone_claim_internal_v1(p_envelope||jsonb_build_object('action','RESERVE','claim_kind','THERAPIST_ACCOUNT'));
END $$;

CREATE FUNCTION public.therapist_account_phone_bind_v1(p_envelope JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF session_user<>'{therapist}' THEN RAISE EXCEPTION 'THERAPIST_PHONE_BIND_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF jsonb_typeof(p_envelope)<>'object' OR NOT p_envelope ?& ARRAY['claim_id','claim_ref','user_id','expected_version']
     OR (p_envelope-ARRAY['claim_id','claim_ref','user_id','expected_version'])<>'{{}}'::jsonb THEN
    RAISE EXCEPTION 'THERAPIST_PHONE_BIND_INVALID' USING ERRCODE='22023';
  END IF;
  RETURN public.identity_phone_claim_internal_v1(p_envelope||jsonb_build_object('action','BIND','claim_kind','THERAPIST_ACCOUNT'));
END $$;

CREATE FUNCTION public.therapist_account_phone_release_v1(p_envelope JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER VOLATILE PARALLEL UNSAFE
SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF session_user<>'{therapist}' THEN RAISE EXCEPTION 'THERAPIST_PHONE_RELEASE_FORBIDDEN' USING ERRCODE='42501'; END IF;
  IF jsonb_typeof(p_envelope)<>'object' OR NOT p_envelope ?& ARRAY['claim_id','claim_ref','expected_version']
     OR (p_envelope-ARRAY['claim_id','claim_ref','expected_version'])<>'{{}}'::jsonb THEN
    RAISE EXCEPTION 'THERAPIST_PHONE_RELEASE_INVALID' USING ERRCODE='22023';
  END IF;
  RETURN public.identity_phone_claim_internal_v1(p_envelope||jsonb_build_object('action','RELEASE','claim_kind','THERAPIST_ACCOUNT'));
END $$;
"""
    function_script = function_script.replace("E'\\000'", "chr(31)")
    for statement in re.split(r"(?<=END \$\$);", function_script):
        if statement.strip():
            op.execute(sa.text(statement))

    function_grants = (
        ("direct_keyed_digest_candidate_v1(JSONB,VARCHAR)", ()),
        ("institution_tenant_origin_current_v1(BIGINT,UUID)", (
            reader, onboarding, review, application, therapist, therapist_review,
            therapist_reader, member_reader, case_writer,
        )),
        ("institution_controlled_origin_bind_v1(UUID)", (review,)),
        ("direct_institution_create_v1(JSONB)", (review,)),
        ("direct_create_step_up_begin_v1(BIGINT,VARCHAR,VARCHAR,CHAR,VARCHAR,CHAR)", (review,)),
        ("direct_create_step_up_failure_v1(BIGINT,VARCHAR,VARCHAR,UUID,CHAR,BIGINT,BIGINT,UUID,VARCHAR,CHAR)", (review,)),
        ("direct_regenerate_step_up_begin_v1(BIGINT,UUID,VARCHAR,VARCHAR,CHAR)", (review,)),
        ("direct_regenerate_step_up_failure_v1(BIGINT,UUID,VARCHAR,VARCHAR,UUID,CHAR,BIGINT,BIGINT,UUID)", (review,)),
        ("direct_revoke_step_up_begin_v1(BIGINT,UUID,VARCHAR,VARCHAR,CHAR)", (review,)),
        ("direct_revoke_step_up_failure_v1(BIGINT,UUID,VARCHAR,VARCHAR,UUID,CHAR,BIGINT,BIGINT,UUID)", (review,)),
        ("admin_handoff_create_step_up_begin_v1(BIGINT,UUID,VARCHAR,VARCHAR,CHAR,VARCHAR,CHAR)", (review,)),
        ("admin_handoff_create_step_up_failure_v1(BIGINT,UUID,VARCHAR,VARCHAR,UUID,CHAR,BIGINT,BIGINT,UUID,VARCHAR,CHAR)", (review,)),
        ("direct_compliance_decide_step_up_begin_v1(BIGINT,UUID,UUID,VARCHAR,VARCHAR,CHAR)", (review,)),
        ("direct_compliance_decide_step_up_failure_v1(BIGINT,UUID,UUID,VARCHAR,VARCHAR,UUID,CHAR,BIGINT,BIGINT,UUID)", (review,)),
        ("admin_handoff_regenerate_step_up_begin_v1(BIGINT,UUID,VARCHAR,VARCHAR,CHAR)", (review,)),
        ("admin_handoff_regenerate_step_up_failure_v1(BIGINT,UUID,VARCHAR,VARCHAR,UUID,CHAR,BIGINT,BIGINT,UUID)", (review,)),
        ("admin_handoff_revoke_step_up_begin_v1(BIGINT,UUID,VARCHAR,VARCHAR,CHAR)", (review,)),
        ("admin_handoff_revoke_step_up_failure_v1(BIGINT,UUID,VARCHAR,VARCHAR,UUID,CHAR,BIGINT,BIGINT,UUID)", (review,)),
        ("step_up_failure_commit_confirm_v1(UUID,BIGINT,UUID,CHAR)", (review,)),
        ("direct_activation_regenerate_v1(JSONB)", (review,)),
        ("direct_institution_revoke_v1(JSONB)", (review,)),
        ("direct_activation_authority_v1(UUID,UUID,JSONB)", (onboarding,)),
        ("admin_handoff_activation_authority_v1(UUID,UUID,UUID,JSONB)", (onboarding,)),
        ("direct_compliance_save_v1(JSONB)", (onboarding,)),
        ("direct_compliance_submit_v1(JSONB)", (onboarding,)),
        ("direct_compliance_decide_v1(JSONB)", (review,)),
        ("institution_admin_handoff_create_v1(JSONB)", (review,)),
        ("institution_admin_handoff_regenerate_v1(JSONB)", (review,)),
        ("institution_admin_handoff_revoke_v1(JSONB)", (review,)),
        ("direct_institution_activate_v1(JSONB)", (onboarding,)),
        ("admin_handoff_activation_v1(JSONB)", (onboarding,)),
        ("auth_register_member_v2(UUID,VARCHAR,VARCHAR,CHAR,VARCHAR,JSONB)", (application,)),
        ("direct_review_replay_v1(JSONB)", (review,)),
        ("direct_review_commit_confirm_v1(JSONB)", (review,)),
        ("direct_activation_replay_v1(UUID,VARCHAR,CHAR,JSONB)", (onboarding,)),
        ("direct_activation_commit_confirm_v1(JSONB)", (onboarding,)),
        ("direct_compliance_save_replay_v1(BIGINT,VARCHAR,VARCHAR,CHAR,JSONB)", (onboarding,)),
        ("direct_compliance_save_commit_confirm_v1(JSONB)", (onboarding,)),
        ("direct_compliance_submit_replay_v1(BIGINT,VARCHAR,VARCHAR,CHAR,JSONB)", (onboarding,)),
        ("direct_compliance_submit_commit_confirm_v1(JSONB)", (onboarding,)),
        ("admin_handoff_activation_replay_v1(UUID,VARCHAR,CHAR,JSONB)", (onboarding,)),
        ("admin_handoff_activation_commit_confirm_v1(JSONB)", (onboarding,)),
        ("direct_institution_read_v1(BIGINT,VARCHAR,UUID,UUID,UUID,SMALLINT,VARCHAR)", (reader,)),
        ("direct_compliance_current_v1(BIGINT)", (reader,)),
        ("direct_org_admin_login_v1(BIGINT)", (application,)),
        ("direct_outbox_claim_v1(UUID,SMALLINT,VARCHAR)", (delivery,)),
        ("direct_outbox_consume_v1(UUID,VARCHAR,VARCHAR,BIGINT)", (delivery,)),
        ("direct_outbox_reopen_v1(UUID,VARCHAR,VARCHAR,BIGINT,VARCHAR)", (delivery,)),
        ("direct_recovery_claim_v1(SMALLINT,VARCHAR)", (delivery,)),
        ("controlled_org_admin_phone_reserve_v1(JSONB)", (review,)),
        ("controlled_org_admin_phone_bind_v1(JSONB)", (onboarding,)),
        ("controlled_org_admin_phone_release_v1(JSONB)", (review,)),
        ("therapist_account_phone_reserve_v1(JSONB)", (therapist,)),
        ("therapist_account_phone_bind_v1(JSONB)", (therapist,)),
        ("therapist_account_phone_release_v1(JSONB)", (therapist,)),
    )
    for signature, grantees in function_grants:
        op.execute(sa.text(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC"))
        for grantee in grantees:
            op.execute(sa.text(f'GRANT EXECUTE ON FUNCTION public.{signature} TO "{grantee}"'))


def _backfill_controlled_origins() -> None:
    connection = op.get_bind()
    invalid = connection.execute(sa.text("""
SELECT count(*) FROM public.institution_application app
WHERE app.status='APPROVED' AND (app.tenant_internal_id IS NULL OR app.tenant_public_id IS NULL)
""")).scalar_one()
    if invalid:
        raise RuntimeError("DIRECT_ONBOARDING_CONTROLLED_ORIGIN_BACKFILL_INVALID")
    connection.execute(sa.text("""
INSERT INTO public.institution_tenant_origin(tenant_id,tenant_public_id,origin_type,controlled_application_id)
SELECT app.tenant_internal_id,app.tenant_public_id,'CONTROLLED_APPLICATION',app.application_id
FROM public.institution_application app WHERE app.status='APPROVED'
"""))


def _replace_slice3_views() -> None:
    tenant_source = """
      JOIN public.institution_tenant_origin origin ON origin.tenant_id={tenant_column}
      LEFT JOIN public.institution_application app
        ON origin.origin_type='CONTROLLED_APPLICATION'
       AND app.application_id=origin.controlled_application_id
       AND app.status='APPROVED'
      LEFT JOIN public.direct_institution_onboarding direct
        ON origin.origin_type='DIRECT_PROVISIONING'
       AND direct.onboarding_id=origin.direct_onboarding_id
       AND direct.status NOT IN ('PENDING_ACTIVATION','REVOKED_BEFORE_ACTIVATION')
    """
    eligible = """
      WHERE (app.application_id IS NOT NULL OR direct.onboarding_id IS NOT NULL)
    """
    public_id = "COALESCE(app.tenant_public_id,direct.tenant_public_id)"
    op.execute(sa.text(f"""
CREATE OR REPLACE VIEW public.slice3_institution_enrollment_read_v1
WITH (security_barrier=true,security_invoker=false) AS
SELECT invitation.invitation_id,{public_id} AS tenant_public_id,invitation.mode,
  invitation.phone_masked,invitation.expires_at,invitation.status,
  invitation.failed_attempts,invitation.issued_at,invitation.accepted_at,
  invitation.revoked_at,invitation.version
FROM public.member_service_invitation invitation
{tenant_source.format(tenant_column='invitation.tenant_id')}
{eligible}
"""))
    op.execute(sa.text(f"""
CREATE OR REPLACE VIEW public.slice3_family_enrollment_read_v1
WITH (security_barrier=true,security_invoker=false) AS
SELECT enrollment.enrollment_id,{public_id} AS tenant_public_id,
  enrollment.subject_member_id,enrollment.proxy_member_id,enrollment.mode,enrollment.status,
  enrollment.service_scope_tags,enrollment.current_identity_verification_id,
  enrollment.current_assignment_id,enrollment.service_case_id,enrollment.accepted_at,
  enrollment.identity_verified_at,enrollment.case_created_at,enrollment.version,
  CASE WHEN verification.verification_id IS NULL THEN NULL ELSE jsonb_build_object(
    'verification_id',verification.verification_id,'enrollment_id',verification.enrollment_id,
    'member_id',verification.member_id,'current_revision_id',verification.current_revision_id,
    'status',verification.status,'id_masked',revision.id_masked,'submitted_at',verification.submitted_at,
    'institution_checked_at',verification.institution_checked_at,
    'platform_decided_at',verification.platform_decided_at,'reason_codes','[]'::jsonb,
    'version',verification.version) END AS identity,
  CASE WHEN proxy.grant_id IS NULL OR proxy.status<>'ACTIVE'
    OR proxy.valid_from>transaction_timestamp()
    OR (proxy.valid_until IS NOT NULL AND proxy.valid_until<transaction_timestamp())
    OR NOT (proxy.permission_codes @> '["DAILY_VIEW"]'::jsonb)
  THEN NULL ELSE jsonb_build_object(
    'grant_id',proxy.grant_id,'principal_member_id',proxy.principal_member_id,
    'proxy_member_id',proxy.proxy_member_id,'permission_codes',proxy.permission_codes,
    'authorization_document_version_id',proxy.authorization_document_version_id,
    'status',proxy.status,'valid_from',proxy.valid_from,'valid_until',proxy.valid_until,
    'version',proxy.version) END AS proxy,
  COALESCE((SELECT jsonb_agg(jsonb_build_object(
    'consent_record_id',consent.consent_record_id,'enrollment_id',consent.enrollment_id,
    'document_type',consent.document_type,'document_version_id',consent.document_version_id,
    'rendition_id',consent.rendition_id,'locale',rendition.locale,'choice',consent.choice,
    'status',consent.status,'presented_at',consent.presented_at,'accepted_at',consent.accepted_at,
    'withdrawn_at',consent.withdrawn_at,'version',consent.version)
    ORDER BY consent.presented_at,consent.consent_record_id)
    FROM public.consent_record consent JOIN public.consent_document_rendition rendition
      ON rendition.rendition_id=consent.rendition_id
    WHERE consent.enrollment_id=enrollment.enrollment_id),'[]'::jsonb) AS consents,
  CASE WHEN assignment.assignment_id IS NULL THEN NULL ELSE jsonb_build_object(
    'assignment_id',assignment.assignment_id,'enrollment_id',assignment.enrollment_id,
    'tenant_id',{public_id},'subject_member_id',assignment.subject_member_id,
    'therapist_id',assignment.therapist_id,'status',assignment.status,
    'service_scope_tags',assignment.service_scope_tags,'reason_code',assignment.reason_code,
    'service_case_id',assignment.service_case_id,'created_at',assignment.created_at,
    'decided_at',assignment.decided_at,'version',assignment.version) END AS assignment
FROM public.service_enrollment enrollment
{tenant_source.format(tenant_column='enrollment.tenant_id')}
LEFT JOIN public.member_identity_verification verification
  ON verification.verification_id=enrollment.current_identity_verification_id
LEFT JOIN public.member_identity_revision revision
  ON revision.revision_id=verification.current_revision_id
LEFT JOIN public.proxy_grant proxy ON proxy.enrollment_id=enrollment.enrollment_id
LEFT JOIN public.primary_therapist_assignment assignment
  ON assignment.assignment_id=enrollment.current_assignment_id
{eligible}
"""))
    op.execute(sa.text(f"""
CREATE OR REPLACE VIEW public.slice3_therapist_assignment_read_v1
WITH (security_barrier=true,security_invoker=false) AS
SELECT assignment.assignment_id,assignment.enrollment_id,{public_id} AS tenant_public_id,
  assignment.subject_member_id,assignment.therapist_id,assignment.status,
  assignment.service_scope_tags,assignment.reason_code,assignment.service_case_id,
  assignment.created_at,assignment.decided_at,assignment.version,
  revision.id_masked AS subject_masked_label,therapist.display_name AS therapist_display_name
FROM public.primary_therapist_assignment assignment
{tenant_source.format(tenant_column='assignment.tenant_id')}
JOIN public.service_enrollment enrollment ON enrollment.enrollment_id=assignment.enrollment_id
JOIN public.member_identity_verification verification
  ON verification.verification_id=enrollment.current_identity_verification_id
JOIN public.member_identity_revision revision ON revision.revision_id=verification.current_revision_id
JOIN public.therapist_profile therapist ON therapist.therapist_id=assignment.therapist_id
{eligible}
"""))
    op.execute(sa.text(f"""
CREATE OR REPLACE VIEW public.slice3_service_case_read_v1
WITH (security_barrier=true,security_invoker=false) AS
SELECT service_case.case_id,service_case.enrollment_id,service_case.subject_member_id,
  {public_id} AS tenant_public_id,service_case.primary_therapist_id,
  service_case.assignment_id,service_case.status,service_case.service_scope_tags,
  service_case.created_at,service_case.version
FROM public.service_case service_case
{tenant_source.format(tenant_column='service_case.tenant_id')}
{eligible}
"""))


def _restore_slice3_views() -> None:
    op.execute(sa.text("""CREATE OR REPLACE VIEW public.slice3_institution_enrollment_read_v1 WITH (security_barrier=true,security_invoker=false) AS SELECT i.invitation_id,a.tenant_public_id,i.mode,i.phone_masked,i.expires_at,i.status,i.failed_attempts,i.issued_at,i.accepted_at,i.revoked_at,i.version FROM public.member_service_invitation i JOIN public.institution_application a ON a.tenant_internal_id=i.tenant_id AND a.status='APPROVED'"""))
    op.execute(sa.text("""
CREATE OR REPLACE VIEW public.slice3_therapist_assignment_read_v1 WITH (security_barrier=true,security_invoker=false) AS
SELECT p.assignment_id,p.enrollment_id,a.tenant_public_id,p.subject_member_id,p.therapist_id,p.status,p.service_scope_tags,
 p.reason_code,p.service_case_id,p.created_at,p.decided_at,p.version,r.id_masked AS subject_masked_label,
 t.display_name AS therapist_display_name FROM public.primary_therapist_assignment p
JOIN public.institution_application a ON a.tenant_internal_id=p.tenant_id AND a.status='APPROVED'
JOIN public.service_enrollment e ON e.enrollment_id=p.enrollment_id
JOIN public.member_identity_verification v ON v.verification_id=e.current_identity_verification_id
JOIN public.member_identity_revision r ON r.revision_id=v.current_revision_id
JOIN public.therapist_profile t ON t.therapist_id=p.therapist_id
"""))
    op.execute(sa.text("""
CREATE OR REPLACE VIEW public.slice3_service_case_read_v1 WITH (security_barrier=true,security_invoker=false) AS
SELECT c.case_id,c.enrollment_id,c.subject_member_id,a.tenant_public_id,c.primary_therapist_id,c.assignment_id,
 c.status,c.service_scope_tags,c.created_at,c.version FROM public.service_case c
JOIN public.institution_application a ON a.tenant_internal_id=c.tenant_id AND a.status='APPROVED'
"""))
    op.execute(sa.text("""
CREATE OR REPLACE VIEW public.slice3_family_enrollment_read_v1 WITH (security_barrier=true,security_invoker=false) AS
SELECT e.enrollment_id,a.tenant_public_id,e.subject_member_id,e.proxy_member_id,e.mode,e.status,e.service_scope_tags,
 e.current_identity_verification_id,e.current_assignment_id,e.service_case_id,e.accepted_at,e.identity_verified_at,
 e.case_created_at,e.version,
 CASE WHEN v.verification_id IS NULL THEN NULL ELSE jsonb_build_object('verification_id',v.verification_id,
 'enrollment_id',v.enrollment_id,'member_id',v.member_id,'current_revision_id',v.current_revision_id,
 'status',v.status,'id_masked',ir.id_masked,'submitted_at',v.submitted_at,'institution_checked_at',v.institution_checked_at,
 'platform_decided_at',v.platform_decided_at,'reason_codes','[]'::jsonb,'version',v.version) END AS identity,
 CASE WHEN pg.grant_id IS NULL OR pg.status<>'ACTIVE' OR pg.valid_from>transaction_timestamp()
 OR (pg.valid_until IS NOT NULL AND pg.valid_until<transaction_timestamp())
 OR NOT (pg.permission_codes @> '["DAILY_VIEW"]'::jsonb) THEN NULL ELSE jsonb_build_object(
 'grant_id',pg.grant_id,'principal_member_id',pg.principal_member_id,'proxy_member_id',pg.proxy_member_id,
 'permission_codes',pg.permission_codes,'authorization_document_version_id',pg.authorization_document_version_id,
 'status',pg.status,'valid_from',pg.valid_from,'valid_until',pg.valid_until,'version',pg.version) END AS proxy,
 COALESCE((SELECT jsonb_agg(jsonb_build_object('consent_record_id',c.consent_record_id,'enrollment_id',c.enrollment_id,
 'document_type',c.document_type,'document_version_id',c.document_version_id,'rendition_id',c.rendition_id,
 'locale',r.locale,'choice',c.choice,'status',c.status,'presented_at',c.presented_at,'accepted_at',c.accepted_at,
 'withdrawn_at',c.withdrawn_at,'version',c.version) ORDER BY c.presented_at,c.consent_record_id)
 FROM public.consent_record c JOIN public.consent_document_rendition r ON r.rendition_id=c.rendition_id
 WHERE c.enrollment_id=e.enrollment_id),'[]'::jsonb) AS consents,
 CASE WHEN pa.assignment_id IS NULL THEN NULL ELSE jsonb_build_object('assignment_id',pa.assignment_id,
 'enrollment_id',pa.enrollment_id,'tenant_id',a.tenant_public_id,'subject_member_id',pa.subject_member_id,
 'therapist_id',pa.therapist_id,'status',pa.status,'service_scope_tags',pa.service_scope_tags,
 'reason_code',pa.reason_code,'service_case_id',pa.service_case_id,'created_at',pa.created_at,
 'decided_at',pa.decided_at,'version',pa.version) END AS assignment
FROM public.service_enrollment e JOIN public.institution_application a
 ON a.tenant_internal_id=e.tenant_id AND a.status='APPROVED'
LEFT JOIN public.member_identity_verification v ON v.verification_id=e.current_identity_verification_id
LEFT JOIN public.member_identity_revision ir ON ir.revision_id=v.current_revision_id
LEFT JOIN public.proxy_grant pg ON pg.enrollment_id=e.enrollment_id
LEFT JOIN public.primary_therapist_assignment pa ON pa.assignment_id=e.current_assignment_id
"""))


def _replace_private_file_reference(writer: str) -> None:
    op.execute(sa.text(f"""
CREATE OR REPLACE FUNCTION public.a3_private_file_referenced_v1(p_file_id UUID)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $function$
BEGIN
  IF session_user <> '{writer}' THEN
    RAISE EXCEPTION 'A3_PRIVATE_FILE_REFERENCE_FORBIDDEN';
  END IF;
  IF p_file_id IS NULL THEN
    RAISE EXCEPTION 'A3_PRIVATE_FILE_REFERENCE_INVALID';
  END IF;
  RETURN EXISTS(
    SELECT 1 FROM public.private_file file
    WHERE file.file_id=p_file_id AND file.bound_application_id IS NOT NULL
  ) OR EXISTS(
    SELECT 1 FROM public.institution_license license
    WHERE license.private_file_id=p_file_id
  ) OR EXISTS(
    SELECT 1 FROM public.therapist_qualification_attachment attachment
    WHERE attachment.private_file_id=p_file_id
  ) OR EXISTS(
    SELECT 1 FROM public.detection_report_attachment attachment
    WHERE attachment.private_file_id=p_file_id
  ) OR EXISTS(
    SELECT 1 FROM public.personal_data_export_artifact artifact
    WHERE artifact.private_file_id=p_file_id
  ) OR EXISTS(
    SELECT 1 FROM public.personal_data_export_download_access access
    WHERE access.private_file_id=p_file_id
  ) OR EXISTS(
    SELECT 1 FROM public.direct_institution_license license
    WHERE license.private_file_id=p_file_id
  );
END;
$function$
"""))


def _restore_private_file_reference(writer: str) -> None:
    op.execute(sa.text(f"""
CREATE OR REPLACE FUNCTION public.a3_private_file_referenced_v1(p_file_id UUID)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $function$
BEGIN
  IF session_user <> '{writer}' THEN
    RAISE EXCEPTION 'A3_PRIVATE_FILE_REFERENCE_FORBIDDEN';
  END IF;
  IF p_file_id IS NULL THEN
    RAISE EXCEPTION 'A3_PRIVATE_FILE_REFERENCE_INVALID';
  END IF;
  RETURN EXISTS(
    SELECT 1 FROM public.private_file file
    WHERE file.file_id=p_file_id AND file.bound_application_id IS NOT NULL
  ) OR EXISTS(
    SELECT 1 FROM public.institution_license license
    WHERE license.private_file_id=p_file_id
  ) OR EXISTS(
    SELECT 1 FROM public.therapist_qualification_attachment attachment
    WHERE attachment.private_file_id=p_file_id
  ) OR EXISTS(
    SELECT 1 FROM public.detection_report_attachment attachment
    WHERE attachment.private_file_id=p_file_id
  ) OR EXISTS(
    SELECT 1 FROM public.personal_data_export_artifact artifact
    WHERE artifact.private_file_id=p_file_id
  ) OR EXISTS(
    SELECT 1 FROM public.personal_data_export_download_access access
    WHERE access.private_file_id=p_file_id
  );
END;
$function$
"""))


def _replace_slice2_readiness_interfaces() -> None:
    op.execute(sa.text("""
CREATE OR REPLACE VIEW public.institution_readiness_source_v1
WITH (security_barrier=true,security_invoker=false) AS
SELECT tenant.id AS tenant_id,application.tenant_public_id,
 application.application_id,application.version AS application_version,
 application.institution_type,application.draft_payload->'service_tags' AS service_tags,
 license.license_id,license.license_type,license.valid_from,license.valid_until
FROM public.tenant tenant JOIN public.institution_application application
 ON application.tenant_internal_id=tenant.id AND application.status='APPROVED'
JOIN public.institution_license license ON license.application_id=application.application_id
UNION ALL
SELECT tenant.id,direct.tenant_public_id,direct.onboarding_id AS application_id,
 direct.version AS application_version,direct.institution_type,revision.service_tags,
 license.license_id,license.license_type::VARCHAR(48),license.valid_from,license.valid_until
FROM public.tenant tenant JOIN public.direct_institution_onboarding direct
 ON direct.tenant_id=tenant.id AND direct.status='COMPLIANCE_APPROVED'
JOIN public.direct_institution_compliance_revision revision
 ON revision.revision_id=direct.current_revision_id AND revision.status='APPROVED'
JOIN public.direct_institution_license license ON license.revision_id=revision.revision_id
"""))
    op.execute(sa.text("""
CREATE OR REPLACE VIEW public.institution_readiness_guard_v1
WITH (security_barrier=true,security_invoker=false) AS
SELECT tenant.id AS tenant_id,application.tenant_public_id,tenant.status AS tenant_status,
 application.application_id,application.version AS application_version,application.institution_type,
 application.draft_payload->'service_tags' AS service_tags,
 COALESCE((SELECT jsonb_agg(jsonb_build_object('license_id',license.license_id,
   'license_type',license.license_type,'valid_from',license.valid_from,'valid_until',license.valid_until)
   ORDER BY license.license_type COLLATE "C",license.license_id)
   FROM public.institution_license license WHERE license.application_id=application.application_id),'[]'::jsonb) AS license_versions,
 COALESCE((SELECT jsonb_agg(jsonb_build_object('therapist_id',profile.therapist_id,
   'status',profile.status,'version',profile.version,'qualification_version_id',profile.current_qualification_version_id,
   'valid_until',profile.qualification_valid_until) ORDER BY profile.therapist_id)
   FROM public.therapist_profile profile WHERE profile.tenant_id=tenant.id),'[]'::jsonb) AS current_therapist_versions,
 LEAST((SELECT min(profile.qualification_valid_until) FROM public.therapist_profile profile
         WHERE profile.tenant_id=tenant.id AND profile.status='APPROVED_ACTIVE'),
       (SELECT min(license.valid_until) FROM public.institution_license license
         WHERE license.application_id=application.application_id)) AS next_expiry_at
FROM public.tenant tenant JOIN public.institution_application application
 ON application.tenant_internal_id=tenant.id AND application.status='APPROVED'
 AND application.tenant_public_id IS NOT NULL
UNION ALL
SELECT tenant.id,direct.tenant_public_id,tenant.status AS tenant_status,
 direct.onboarding_id AS application_id,direct.version AS application_version,direct.institution_type,
 COALESCE(revision.service_tags,'[]'::jsonb) AS service_tags,
 COALESCE((SELECT jsonb_agg(jsonb_build_object('license_id',license.license_id,
   'license_type',license.license_type,'valid_from',license.valid_from,'valid_until',license.valid_until)
   ORDER BY license.license_type COLLATE "C",license.license_id)
   FROM public.direct_institution_license license
   WHERE license.revision_id=revision.revision_id),'[]'::jsonb) AS license_versions,
 COALESCE((SELECT jsonb_agg(jsonb_build_object('therapist_id',profile.therapist_id,
   'status',profile.status,'version',profile.version,'qualification_version_id',profile.current_qualification_version_id,
   'valid_until',profile.qualification_valid_until) ORDER BY profile.therapist_id)
   FROM public.therapist_profile profile WHERE profile.tenant_id=tenant.id),'[]'::jsonb) AS current_therapist_versions,
 LEAST((SELECT min(profile.qualification_valid_until) FROM public.therapist_profile profile
         WHERE profile.tenant_id=tenant.id AND profile.status='APPROVED_ACTIVE'),
       (SELECT min(license.valid_until) FROM public.direct_institution_license license
         WHERE license.revision_id=revision.revision_id)) AS next_expiry_at
FROM public.tenant tenant JOIN public.direct_institution_onboarding direct
 ON direct.tenant_id=tenant.id AND direct.status NOT IN ('PENDING_ACTIVATION','REVOKED_BEFORE_ACTIVATION')
LEFT JOIN public.direct_institution_compliance_revision revision
 ON revision.revision_id=direct.current_revision_id AND revision.status='APPROVED'
"""))
    op.execute(sa.text("""
CREATE OR REPLACE FUNCTION public.therapist_totp_for_login_v1(p_user_id BIGINT)
RETURNS TABLE(therapist_id UUID,tenant_public_id UUID,totp_secret_ciphertext BYTEA,
 totp_encryption_key_id VARCHAR,totp_enabled BOOLEAN,therapist_status VARCHAR,tenant_id BIGINT)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
SELECT profile.therapist_id,origin.tenant_public_id,profile.totp_secret_ciphertext,
 profile.totp_encryption_key_id,profile.totp_enabled,profile.status,profile.tenant_id
FROM public.therapist_profile profile JOIN public.institution_tenant_origin origin
 ON origin.tenant_id=profile.tenant_id
LEFT JOIN public.institution_application application
 ON origin.origin_type='CONTROLLED_APPLICATION'
 AND application.application_id=origin.controlled_application_id AND application.status='APPROVED'
LEFT JOIN public.direct_institution_onboarding direct
 ON origin.origin_type='DIRECT_PROVISIONING' AND direct.onboarding_id=origin.direct_onboarding_id
 AND direct.status NOT IN ('PENDING_ACTIVATION','REVOKED_BEFORE_ACTIVATION')
WHERE profile.user_id=p_user_id
 AND (application.application_id IS NOT NULL OR direct.onboarding_id IS NOT NULL)
$$
"""))
    op.execute(sa.text("""
CREATE OR REPLACE FUNCTION public.resolve_tenant_admin_delivery_targets_v1(p_event_id UUID)
RETURNS TABLE(recipient_user_id BIGINT,recipient_tenant_id BIGINT)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE event_row public.therapist_workflow_outbox%ROWTYPE; valid_event BOOLEAN:=FALSE;
BEGIN
 SELECT * INTO event_row FROM public.therapist_workflow_outbox WHERE event_id=p_event_id FOR UPDATE;
 IF NOT FOUND OR event_row.status<>'PROCESSING' THEN RETURN; END IF;
 IF event_row.event_type IN ('THERAPIST_INVITED','THERAPIST_INVITATION_REVOKED','THERAPIST_INVITATION_EXPIRED') THEN
   SELECT EXISTS(SELECT 1 FROM public.therapist_invitation invitation
     WHERE invitation.invitation_id=event_row.aggregate_id AND invitation.tenant_id=event_row.tenant_id) INTO valid_event;
 ELSIF event_row.event_type IN ('THERAPIST_ACTIVATED','THERAPIST_EXITED') THEN
   SELECT EXISTS(SELECT 1 FROM public.therapist_profile profile
     WHERE profile.therapist_id=event_row.aggregate_id AND profile.tenant_id=event_row.tenant_id) INTO valid_event;
 ELSIF event_row.event_type='SERVICE_READINESS_RECOMPUTED' THEN
   SELECT EXISTS(SELECT 1 FROM public.institution_tenant_origin origin
     LEFT JOIN public.institution_application application
       ON origin.origin_type='CONTROLLED_APPLICATION' AND application.application_id=origin.controlled_application_id
       AND application.status='APPROVED'
     LEFT JOIN public.direct_institution_onboarding direct
       ON origin.origin_type='DIRECT_PROVISIONING' AND direct.onboarding_id=origin.direct_onboarding_id
       AND direct.status NOT IN ('PENDING_ACTIVATION','REVOKED_BEFORE_ACTIVATION')
     WHERE origin.tenant_id=event_row.tenant_id AND origin.tenant_public_id=event_row.aggregate_id
       AND (application.application_id IS NOT NULL OR direct.onboarding_id IS NOT NULL)) INTO valid_event;
 END IF;
 IF NOT valid_event THEN RETURN; END IF;
 PERFORM 1 FROM public.tenant tenant WHERE tenant.id=event_row.tenant_id AND tenant.status='active' FOR SHARE;
 IF NOT FOUND THEN RETURN; END IF;
 RETURN QUERY SELECT actor.id,event_row.tenant_id FROM public."user" actor
  WHERE actor.tenant_id=event_row.tenant_id AND actor.status='active'
    AND actor.role IN ('org_admin','org_operator') ORDER BY actor.id FOR SHARE OF actor;
END $$
"""))


def _restore_slice2_readiness_interfaces() -> None:
    op.execute(sa.text("""CREATE OR REPLACE VIEW public.institution_readiness_source_v1 WITH (security_barrier=true,security_invoker=false) AS
SELECT t.id AS tenant_id,a.tenant_public_id,a.application_id,a.version AS application_version,
a.institution_type,a.draft_payload->'service_tags' AS service_tags,l.license_id,l.license_type,l.valid_from,l.valid_until
FROM public.tenant t JOIN public.institution_application a ON a.tenant_public_id IS NOT NULL
JOIN public.institution_license l ON l.application_id=a.application_id
WHERE a.status='APPROVED' AND t.id=a.tenant_internal_id"""))
    op.execute(sa.text("""CREATE OR REPLACE VIEW public.institution_readiness_guard_v1 WITH (security_barrier=true,security_invoker=false) AS
SELECT t.id AS tenant_id,a.tenant_public_id,t.status AS tenant_status,a.application_id,a.version AS application_version,
a.institution_type,a.draft_payload->'service_tags' AS service_tags,
COALESCE((SELECT jsonb_agg(jsonb_build_object('license_id',l.license_id,'license_type',l.license_type,'valid_from',l.valid_from,'valid_until',l.valid_until) ORDER BY l.license_type COLLATE "C",l.license_id) FROM public.institution_license l WHERE l.application_id=a.application_id),'[]'::jsonb) AS license_versions,
COALESCE((SELECT jsonb_agg(jsonb_build_object('therapist_id',p.therapist_id,'status',p.status,'version',p.version,'qualification_version_id',p.current_qualification_version_id,'valid_until',p.qualification_valid_until) ORDER BY p.therapist_id) FROM public.therapist_profile p WHERE p.tenant_id=t.id),'[]'::jsonb) AS current_therapist_versions,
LEAST((SELECT min(p.qualification_valid_until) FROM public.therapist_profile p WHERE p.tenant_id=t.id AND p.status='APPROVED_ACTIVE'),(SELECT min(l.valid_until) FROM public.institution_license l WHERE l.application_id=a.application_id)) AS next_expiry_at
FROM public.tenant t JOIN public.institution_application a ON a.tenant_internal_id=t.id AND a.status='APPROVED' AND a.tenant_public_id IS NOT NULL"""))
    op.execute(sa.text("""CREATE OR REPLACE FUNCTION public.therapist_totp_for_login_v1(p_user_id BIGINT)
RETURNS TABLE(therapist_id UUID,tenant_public_id UUID,totp_secret_ciphertext BYTEA,totp_encryption_key_id VARCHAR,totp_enabled BOOLEAN,therapist_status VARCHAR,tenant_id BIGINT)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
SELECT p.therapist_id,a.tenant_public_id,p.totp_secret_ciphertext,p.totp_encryption_key_id,p.totp_enabled,p.status,p.tenant_id
FROM public.therapist_profile p JOIN public.institution_application a ON a.tenant_internal_id=p.tenant_id AND a.status='APPROVED'
WHERE p.user_id=p_user_id$$"""))
    op.execute(sa.text("""CREATE OR REPLACE FUNCTION public.resolve_tenant_admin_delivery_targets_v1(p_event_id UUID)
RETURNS TABLE(recipient_user_id BIGINT,recipient_tenant_id BIGINT)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_event public.therapist_workflow_outbox%ROWTYPE; v_valid BOOLEAN := FALSE;
BEGIN
 SELECT * INTO v_event FROM public.therapist_workflow_outbox WHERE event_id=p_event_id FOR UPDATE;
 IF NOT FOUND OR v_event.status<>'PROCESSING' THEN RETURN; END IF;
 IF v_event.event_type IN ('THERAPIST_INVITED','THERAPIST_INVITATION_REVOKED','THERAPIST_INVITATION_EXPIRED') THEN SELECT EXISTS(SELECT 1 FROM public.therapist_invitation i WHERE i.invitation_id=v_event.aggregate_id AND i.tenant_id=v_event.tenant_id) INTO v_valid;
 ELSIF v_event.event_type IN ('THERAPIST_ACTIVATED','THERAPIST_EXITED') THEN SELECT EXISTS(SELECT 1 FROM public.therapist_profile p WHERE p.therapist_id=v_event.aggregate_id AND p.tenant_id=v_event.tenant_id) INTO v_valid;
 ELSIF v_event.event_type='SERVICE_READINESS_RECOMPUTED' THEN SELECT EXISTS(SELECT 1 FROM public.institution_application a WHERE a.tenant_public_id=v_event.aggregate_id AND a.tenant_internal_id=v_event.tenant_id AND a.status='APPROVED') INTO v_valid;
 END IF;
 IF NOT v_valid THEN RETURN; END IF;
 PERFORM 1 FROM public.tenant t WHERE t.id=v_event.tenant_id AND t.status='active' FOR SHARE;
 IF NOT FOUND THEN RETURN; END IF;
 RETURN QUERY SELECT u.id,v_event.tenant_id FROM public."user" u WHERE u.tenant_id=v_event.tenant_id AND u.status='active' AND u.role IN ('org_admin','org_operator') ORDER BY u.id FOR SHARE OF u;
END$$"""))


def _replace_slice4_authorities(roles: dict[str, str]) -> None:
    writer = roles["health_record_writer"]
    readiness = roles["assessment_readiness_writer"]
    identity = roles["slice4_identity_authority"]

    op.execute(
        f'''CREATE OR REPLACE FUNCTION public.slice4_identity_summary_source_v1(
            value_subject_member_id UUID,
            value_service_case_id UUID
        ) RETURNS TABLE(
            identity_source_kind VARCHAR,
            document_type VARCHAR,
            identity_ciphertext BYTEA,
            identity_nonce BYTEA,
            identity_key_id VARCHAR,
            birth_date_ciphertext BYTEA,
            birth_date_key_id VARCHAR,
            identity_revision_ref UUID,
            source_version BIGINT,
            tenant_public_id UUID,
            evidence_status VARCHAR,
            identity_user_ref BIGINT,
            identity_storage_version BIGINT
        )
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        DECLARE source_kind_value VARCHAR(16); tenant_public_value UUID;
        BEGIN
          IF session_user <> '{identity}' THEN
            RAISE EXCEPTION 'SLICE4_IDENTITY_AUTHORITY_FORBIDDEN';
          END IF;
          SELECT g.source_kind,origin.tenant_public_id
            INTO source_kind_value,tenant_public_value
            FROM public.service_case c
            JOIN public.service_enrollment e ON e.enrollment_id=c.enrollment_id
            JOIN public.institution_tenant_origin origin
              ON origin.tenant_id=e.tenant_id
             AND EXISTS(SELECT 1 FROM public.institution_tenant_origin_current_v1(
               origin.tenant_id,origin.tenant_public_id))
            JOIN identity.identity_subject_claim_registry g
              ON g.member_id=e.subject_member_id
            WHERE c.case_id=value_service_case_id
              AND c.status='PREPARING'
              AND e.subject_member_id=value_subject_member_id
            FOR SHARE OF c,e,origin,g;
          IF NOT FOUND THEN RETURN; END IF;
          IF source_kind_value='P1' THEN
            RETURN QUERY
            SELECT 'P1'::varchar,'PRC_RESIDENT_ID'::varchar,
              s.id_card_ciphertext,s.id_card_nonce,s.encryption_key_id::varchar,
              NULL::bytea,NULL::varchar,s.submission_id,g.source_facts_version,
              tenant_public_value,'VERIFIED'::varchar,s.user_ref,s.version
            FROM identity.identity_subject_claim_registry g
            JOIN public.identity_verification_submission s
              ON s.submission_id=g.p1_submission_id AND s.user_ref=g.user_ref
            JOIN public.identity_verification_decision d
              ON d.decision_ref=g.p1_decision_ref AND d.user_ref=s.user_ref
            WHERE g.member_id=value_subject_member_id AND g.source_kind='P1'
              AND s.status='verified' AND d.outcome='verified'
              AND d.facts_version=g.source_facts_version
              AND NOT EXISTS(SELECT 1 FROM public.identity_verification_decision n
                WHERE n.supersedes_ref=d.decision_ref)
            FOR SHARE OF g,s,d;
          ELSIF source_kind_value='SLICE3' THEN
            RETURN QUERY
            SELECT 'SLICE3'::varchar,r.document_type::varchar,
              r.id_ciphertext,NULL::bytea,r.id_key_id::varchar,
              r.birth_date_ciphertext,r.birth_date_key_id::varchar,r.revision_id,
              g.source_facts_version,tenant_public_value,'VERIFIED'::varchar,
              NULL::bigint,r.revision_no::bigint
            FROM public.service_case c
            JOIN public.member_identity_verification v
              ON v.verification_id=c.identity_verification_id
            JOIN public.member_identity_revision r
              ON r.verification_id=v.verification_id
             AND r.revision_id=v.current_revision_id
            JOIN public.member_identity_review_decision d
              ON d.decision_id=v.platform_decision_id
             AND d.revision_id=r.revision_id
            JOIN identity.identity_subject_claim_registry g
              ON g.member_id=v.member_id
             AND g.slice3_revision_id=r.revision_id
             AND g.slice3_decision_id=d.decision_id
            WHERE c.case_id=value_service_case_id
              AND v.member_id=value_subject_member_id
              AND v.status='APPROVED' AND d.phase='PLATFORM'
              AND d.decision='APPROVED' AND g.source_kind='SLICE3'
            FOR SHARE OF c,v,r,d,g;
          END IF;
        END; $$;'''
    )

    op.execute(
        f'''CREATE OR REPLACE FUNCTION public.slice4_identity_summary_current_v1(
            value_subject_member_id UUID,
            value_service_case_id UUID,
            value_identity_revision_ref UUID,
            value_identity_source_version BIGINT,
            value_tenant_public_id UUID
        ) RETURNS BOOLEAN
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        DECLARE identity_source_kind VARCHAR(16);
        BEGIN
          IF session_user NOT IN ('{writer}','{readiness}') THEN
            RAISE EXCEPTION 'SLICE4_IDENTITY_CURRENTNESS_FORBIDDEN';
          END IF;
          IF value_identity_source_version < 1 THEN RETURN FALSE; END IF;
          PERFORM 1 FROM public.service_case c
            JOIN public.service_enrollment e ON e.enrollment_id=c.enrollment_id
            JOIN public.institution_tenant_origin origin ON origin.tenant_id=e.tenant_id
            WHERE c.case_id=value_service_case_id
              AND c.status='PREPARING'
              AND e.subject_member_id=value_subject_member_id
              AND origin.tenant_public_id=value_tenant_public_id
              AND EXISTS(SELECT 1 FROM public.institution_tenant_origin_current_v1(
                origin.tenant_id,origin.tenant_public_id))
            FOR SHARE OF c,e,origin;
          IF NOT FOUND THEN RETURN FALSE; END IF;
          SELECT g.source_kind INTO identity_source_kind
            FROM identity.identity_subject_claim_registry g
            WHERE g.member_id=value_subject_member_id
              AND g.source_facts_version=value_identity_source_version
              AND ((g.source_kind='P1' AND g.p1_submission_id=value_identity_revision_ref)
                OR (g.source_kind='SLICE3' AND g.slice3_revision_id=value_identity_revision_ref))
            FOR SHARE;
          IF NOT FOUND THEN RETURN FALSE; END IF;
          IF identity_source_kind='P1' THEN
            PERFORM 1 FROM public.identity_verification_submission s
              JOIN identity.identity_subject_claim_registry g
                ON g.p1_submission_id=s.submission_id
              JOIN public.identity_verification_decision d
                ON d.decision_ref=g.p1_decision_ref AND d.user_ref=s.user_ref
              WHERE s.submission_id=value_identity_revision_ref
                AND s.status='verified'
                AND g.member_id=value_subject_member_id
                AND g.source_facts_version=value_identity_source_version
                AND d.facts_version=value_identity_source_version
                AND d.outcome='verified'
                AND NOT EXISTS(SELECT 1 FROM public.identity_verification_decision n WHERE n.supersedes_ref=d.decision_ref)
              FOR SHARE OF s,g,d;
          ELSIF identity_source_kind='SLICE3' THEN
            PERFORM 1 FROM public.member_identity_verification v
              JOIN public.member_identity_revision r ON r.verification_id=v.verification_id
              JOIN public.member_identity_review_decision d
                ON d.decision_id=v.platform_decision_id AND d.revision_id=r.revision_id
              JOIN identity.identity_subject_claim_registry g
                ON g.member_id=v.member_id
               AND g.slice3_revision_id=r.revision_id
               AND g.slice3_decision_id=d.decision_id
              WHERE r.revision_id=value_identity_revision_ref
                AND g.source_facts_version=value_identity_source_version
                AND v.current_revision_id=r.revision_id AND v.status='APPROVED'
                AND d.decision='APPROVED'
                AND d.phase='PLATFORM'
              FOR SHARE OF v,r,d,g;
          ELSE RETURN FALSE;
          END IF;
          IF NOT FOUND THEN RETURN FALSE; END IF;
          RETURN TRUE;
        END; $$;'''
    )

    op.execute(
        f'''CREATE OR REPLACE FUNCTION public.slice4_assessment_assembly_write_v1(
          value_service_case_id UUID, value_requested_assembly_id UUID,
          value_subject_member_id UUID, value_tenant_public_id UUID,
          value_primary_therapist_id UUID, value_profile_revision_id UUID,
          value_policy_version_id UUID, value_projection_version SMALLINT,
          value_rule_version VARCHAR, value_source_snapshot VARCHAR,
          value_source_vector JSONB, value_encrypted_fact_rows JSONB,
          value_readiness_status VARCHAR, value_reason_codes JSONB,
          value_idempotency_key UUID, value_request_digest BYTEA,
          value_expected_postimage_digest BYTEA
        ) RETURNS TABLE(assembly_id UUID,service_case_id UUID,
          readiness_status VARCHAR,pointer_version BIGINT,generated_at TIMESTAMPTZ)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        DECLARE resolved_tenant_id BIGINT; next_pointer_version BIGINT;
          created TIMESTAMPTZ; stored_request BYTEA; stored_postimage BYTEA;
          resolved_identity_ref UUID; resolved_identity_version BIGINT;
          resolved_digest_key VARCHAR; authoritative_currentness JSONB;
        BEGIN
          IF session_user <> '{readiness}'
             OR value_projection_version<2
             OR value_readiness_status NOT IN ('DATA_INSUFFICIENT','DATA_SYNC_PENDING','DISPUTED','ASSESSMENT_READY')
             OR jsonb_typeof(value_source_vector)<>'object'
             OR jsonb_typeof(value_encrypted_fact_rows)<>'array'
             OR jsonb_typeof(value_reason_codes)<>'array'
             OR value_request_digest IS NULL OR value_expected_postimage_digest IS NULL THEN
            RAISE EXCEPTION 'SLICE4_ASSEMBLY_INVALID';
          END IF;
          IF EXISTS(SELECT 1 FROM jsonb_object_keys(value_source_vector) k
                    WHERE k NOT IN ('consent_version_ids','resolved_generation_id',
                      'required_max_fact_id','required_max_status_event_seq','missing_codes',
                      'expired_codes','disputed_codes','source_vector_digest','source_digest',
                      'assembly_digest','digest_key_id','generated_at','audit_id',
                      'event_id','receipt_id'))
             OR (SELECT count(*) FROM jsonb_object_keys(value_source_vector))<>15 THEN
            RAISE EXCEPTION 'SLICE4_ASSEMBLY_INVALID';
          END IF;
          IF EXISTS(
            SELECT 1 FROM jsonb_array_elements(value_encrypted_fact_rows) item
            WHERE jsonb_typeof(item)<>'object'
               OR (SELECT count(*) FROM jsonb_object_keys(item))<>11
               OR EXISTS(SELECT 1 FROM jsonb_object_keys(item) k WHERE k NOT IN
                  ('indicator_code','fact_ref','measured_at','received_at','source_type',
                  'verification_state','status_event_seq','value_ciphertext','value_key_id',
                  'unit','row_digest'))
          ) THEN RAISE EXCEPTION 'SLICE4_ASSEMBLY_INVALID'; END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(value_service_case_id::text,0));
          authoritative_currentness := public.slice4_readiness_currentness_v1(
            value_service_case_id,0);
          IF authoritative_currentness IS NULL
             OR (authoritative_currentness->>'subject_member_id')::uuid<>value_subject_member_id
             OR (authoritative_currentness->>'tenant_public_id')::uuid<>value_tenant_public_id
             OR (authoritative_currentness->>'primary_therapist_id')::uuid<>value_primary_therapist_id
             OR (authoritative_currentness->>'profile_revision_id')::uuid
                  IS DISTINCT FROM value_profile_revision_id
             OR authoritative_currentness->'consent_version_ids'
                  IS DISTINCT FROM value_source_vector->'consent_version_ids'
             OR (authoritative_currentness->'policy'->>'policy_version_id')::uuid
                  IS DISTINCT FROM value_policy_version_id
             OR (value_readiness_status='ASSESSMENT_READY' AND (
                  COALESCE((authoritative_currentness->>'authorization_complete')::boolean,FALSE)=FALSE
                  OR value_profile_revision_id IS NULL OR value_policy_version_id IS NULL
                  OR NOT ((authoritative_currentness->'policy'->'required_profile_sections')
                          <@ (authoritative_currentness->'profile_section_codes'))
                  OR jsonb_array_length(value_encrypted_fact_rows)=0
                  OR jsonb_array_length(value_reason_codes)>0
                  OR jsonb_array_length(value_source_vector->'missing_codes')>0
                  OR jsonb_array_length(value_source_vector->'expired_codes')>0
                  OR jsonb_array_length(value_source_vector->'disputed_codes')>0)) THEN
            RAISE EXCEPTION 'SLICE4_ASSEMBLY_CURRENTNESS_INVALID';
          END IF;
          SELECT c.tenant_id INTO resolved_tenant_id
            FROM public.service_case c
            JOIN public.service_enrollment e ON e.enrollment_id=c.enrollment_id
            JOIN public.institution_tenant_origin origin ON origin.tenant_id=c.tenant_id
            JOIN public.therapist_profile p ON p.therapist_id=c.primary_therapist_id
            WHERE c.case_id=value_service_case_id AND c.subject_member_id=value_subject_member_id
              AND c.primary_therapist_id=value_primary_therapist_id AND c.status='PREPARING'
              AND e.status='CASE_CREATED' AND origin.tenant_public_id=value_tenant_public_id
              AND EXISTS(SELECT 1 FROM public.institution_tenant_origin_current_v1(
                origin.tenant_id,origin.tenant_public_id))
              AND p.status='APPROVED_ACTIVE'
            FOR SHARE OF c,e,origin,p;
          IF NOT FOUND THEN RAISE EXCEPTION 'SLICE4_ASSEMBLY_CURRENTNESS_INVALID'; END IF;
          IF value_profile_revision_id IS NULL THEN
            IF value_readiness_status<>'DATA_INSUFFICIENT'
               OR NOT (value_reason_codes ? 'PROFILE_MISSING_OR_STALE') THEN
              RAISE EXCEPTION 'SLICE4_ASSEMBLY_CURRENTNESS_INVALID';
            END IF;
            resolved_digest_key:=value_source_vector->>'digest_key_id';
          ELSE
            SELECT r.identity_revision_ref,r.identity_source_version,r.digest_key_id
              INTO resolved_identity_ref,resolved_identity_version,resolved_digest_key
              FROM public.health_profile_revision r
              WHERE r.profile_revision_id=value_profile_revision_id
                AND r.subject_member_id=value_subject_member_id
                AND r.tenant_public_id=value_tenant_public_id FOR SHARE;
            IF NOT FOUND OR NOT public.slice4_identity_summary_current_v1(
                 value_subject_member_id,value_service_case_id,resolved_identity_ref,
                 resolved_identity_version,value_tenant_public_id) THEN
              RAISE EXCEPTION 'SLICE4_ASSEMBLY_CURRENTNESS_INVALID';
            END IF;
          END IF;
          IF value_readiness_status='ASSESSMENT_READY' AND NOT EXISTS(
            SELECT 1 FROM public.health_projection_generation g
            JOIN public.health_projection_shadow_run sr
              ON sr.run_id=g.current_shadow_run_id
            JOIN public.assessment_readiness_policy_version p
              ON p.policy_version_id=value_policy_version_id
            WHERE g.id=(value_source_vector->>'resolved_generation_id')::bigint
              AND g.status='READY' AND g.projection_version=value_projection_version
              AND g.shadow_success_count=2
              AND NOT EXISTS(
                SELECT 1 FROM public.health_projection_generation newer
                WHERE newer.projection_version=g.projection_version
                  AND newer.status='READY'
                  AND (newer.generation_no,newer.id)>(g.generation_no,g.id))
              AND g.high_watermark->>'source_snapshot'=value_source_snapshot
              AND (SELECT COALESCE(max(e.max_fact_id),0)
                     FROM public.health_projection_subject_indicator_evidence_v2 e
                     WHERE e.generation_id=g.id AND e.subject_member_id=value_subject_member_id
                       AND e.indicator_code IN (
                         SELECT CASE WHEN jsonb_typeof(x)='string'
                           THEN trim(both '"' from x::text) ELSE x->>'indicator_code' END
                         FROM jsonb_array_elements(p.required_indicators) x
                       ))=(value_source_vector->>'required_max_fact_id')::bigint
              AND (SELECT COALESCE(max(e.max_status_event_seq),0)
                     FROM public.health_projection_subject_indicator_evidence_v2 e
                     WHERE e.generation_id=g.id AND e.subject_member_id=value_subject_member_id
                       AND e.indicator_code IN (
                         SELECT CASE WHEN jsonb_typeof(x)='string'
                           THEN trim(both '"' from x::text) ELSE x->>'indicator_code' END
                         FROM jsonb_array_elements(p.required_indicators) x
                       ))=(value_source_vector->>'required_max_status_event_seq')::bigint
              AND NOT EXISTS(
                SELECT 1 FROM public.health_projection_subject_indicator_evidence_v2 e
                JOIN public.slice4_projection_coverage_source_v2 c
                  ON c.subject_member_id=e.subject_member_id
                 AND c.indicator_code=e.indicator_code
                WHERE e.generation_id=g.id AND e.subject_member_id=value_subject_member_id
                  AND (e.fact_count<>c.fact_count
                    OR e.status_event_count<>c.status_event_count
                    OR e.max_fact_id<>c.max_fact_id
                    OR e.max_status_event_seq<>c.max_status_event_seq
                    OR e.fact_set_digest<>c.fact_set_digest
                    OR e.status_set_digest<>c.status_set_digest))
              AND (SELECT count(*)
                     FROM public.health_projection_subject_indicator_evidence_v2 e
                     WHERE e.generation_id=g.id AND e.subject_member_id=value_subject_member_id
                       AND e.indicator_code IN (
                         SELECT CASE WHEN jsonb_typeof(x)='string'
                           THEN trim(both '"' from x::text) ELSE x->>'indicator_code' END
                         FROM jsonb_array_elements(p.required_indicators) x
                       ))=jsonb_array_length(p.required_indicators)
            FOR SHARE OF g,sr,p
          ) THEN RAISE EXCEPTION 'SLICE4_ASSEMBLY_CURRENTNESS_INVALID'; END IF;
          IF value_readiness_status='ASSESSMENT_READY' THEN
            PERFORM 1
              FROM public.health_projection_subject_indicator_evidence_v2 e
              WHERE e.generation_id=(value_source_vector->>'resolved_generation_id')::bigint
                AND e.subject_member_id=value_subject_member_id
              FOR SHARE OF e;
            IF NOT FOUND THEN RAISE EXCEPTION 'SLICE4_ASSEMBLY_CURRENTNESS_INVALID'; END IF;
            PERFORM 1
              FROM jsonb_array_elements(value_encrypted_fact_rows) item
              JOIN public.health_projection_fact f
                ON f.generation_id=(value_source_vector->>'resolved_generation_id')::bigint
               AND f.fact_ref=(item->>'fact_ref')::uuid
               AND f.subject_member_id=value_subject_member_id
               AND f.status_event_seq=(item->>'status_event_seq')::bigint
              JOIN public.health_fact_status_event se
                ON se.fact_id=f.fact_id AND se.status_event_seq=f.status_event_seq
              FOR SHARE OF f,se;
            IF NOT FOUND THEN RAISE EXCEPTION 'SLICE4_ASSEMBLY_CURRENTNESS_INVALID'; END IF;
          END IF;
          IF value_readiness_status='ASSESSMENT_READY' AND (
            jsonb_array_length(value_encrypted_fact_rows)<>
              jsonb_array_length(authoritative_currentness->'policy'->'required_indicators')
            OR EXISTS(
              SELECT 1 FROM jsonb_array_elements(value_encrypted_fact_rows) item
              WHERE NOT EXISTS(
                SELECT 1 FROM public.health_ready_projection_fact_v2 f
                WHERE f.generation_id=(value_source_vector->>'resolved_generation_id')::bigint
                  AND f.subject_member_id=value_subject_member_id
                  AND f.indicator_code=item->>'indicator_code'
                  AND f.fact_ref=(item->>'fact_ref')::uuid
                  AND f.status_event_seq=(item->>'status_event_seq')::bigint
                  AND f.measured_at=(item->>'measured_at')::timestamptz
                  AND f.received_at=(item->>'received_at')::timestamptz
                  AND f.source_type=item->>'source_type'
                  AND f.verification_state=item->>'verification_state'
                  AND f.unit=item->>'unit'
                  AND NOT EXISTS(
                    SELECT 1 FROM public.health_ready_projection_fact_v2 newer
                    WHERE newer.generation_id=f.generation_id
                      AND newer.subject_member_id=f.subject_member_id
                      AND newer.indicator_code=f.indicator_code
                      AND (newer.measured_at,newer.fact_ref)>(f.measured_at,f.fact_ref)))
            ) OR EXISTS(
              SELECT 1 FROM jsonb_array_elements(
                authoritative_currentness->'policy'->'required_indicators') requirement
              WHERE NOT EXISTS(
                SELECT 1 FROM jsonb_array_elements(value_encrypted_fact_rows) item
                WHERE item->>'indicator_code'=CASE
                  WHEN jsonb_typeof(requirement)='string'
                    THEN trim(both '"' from requirement::text)
                  ELSE requirement->>'indicator_code' END)
            )) THEN RAISE EXCEPTION 'SLICE4_ASSEMBLY_CURRENTNESS_INVALID'; END IF;
          IF value_policy_version_id IS NULL THEN
            IF value_readiness_status<>'DATA_INSUFFICIENT'
               OR NOT (value_reason_codes ? 'POLICY_UNAVAILABLE') THEN
              RAISE EXCEPTION 'SLICE4_ASSEMBLY_POLICY_INVALID';
            END IF;
          ELSE
            PERFORM 1 FROM public.assessment_readiness_policy_version p
              WHERE p.policy_version_id=value_policy_version_id AND p.status='PUBLISHED'
                AND p.professionally_approved AND p.projection_version=value_projection_version
                AND p.rule_version=value_rule_version
                AND p.effective_from<=clock_timestamp()
                AND (p.retired_at IS NULL OR p.retired_at>clock_timestamp()) FOR SHARE;
            IF NOT FOUND THEN RAISE EXCEPTION 'SLICE4_ASSEMBLY_POLICY_INVALID'; END IF;
          END IF;
          SELECT i.request_digest,i.postimage_digest INTO stored_request,stored_postimage
            FROM public.slice4_idempotency i
            WHERE i.operation='WRITE_ASSESSMENT_ASSEMBLY'
              AND i.scope_ref=value_service_case_id AND i.idempotency_key=value_idempotency_key
            FOR SHARE;
          IF FOUND THEN
            IF stored_request IS DISTINCT FROM value_request_digest THEN
              RAISE EXCEPTION 'SLICE4_IDEMPOTENCY_CONFLICT';
            END IF;
            IF stored_postimage IS DISTINCT FROM value_expected_postimage_digest
               OR NOT EXISTS(SELECT 1 FROM public.assessment_input_assembly a
                     WHERE a.assembly_id=value_requested_assembly_id
                       AND a.service_case_id=value_service_case_id)
               OR (SELECT count(*) FROM public.slice4_audit a
                     WHERE a.aggregate_ref=value_requested_assembly_id
                       AND a.event_type='ASSESSMENT_ASSEMBLY_WRITTEN')<>1
               OR (SELECT count(*) FROM public.slice4_outbox o
                     WHERE o.aggregate_ref=value_requested_assembly_id
                       AND o.event_type='ASSESSMENT_ASSEMBLY_WRITTEN')<>1 THEN
              RAISE EXCEPTION 'SLICE4_COMMIT_OUTCOME_UNKNOWN';
            END IF;
            RETURN QUERY SELECT a.assembly_id,a.service_case_id,a.status,p.version,a.generated_at
              FROM public.assessment_input_assembly a
              JOIN public.assessment_readiness_case_pointer p
                ON p.current_assembly_id=a.assembly_id AND p.service_case_id=a.service_case_id
              WHERE a.assembly_id=value_requested_assembly_id;
            RETURN;
          END IF;
          SELECT COALESCE(p.version,0)+1 INTO next_pointer_version
            FROM public.assessment_readiness_case_pointer p
            WHERE p.service_case_id=value_service_case_id FOR UPDATE;
          IF NOT FOUND THEN next_pointer_version:=1; END IF;
          created:=(value_source_vector->>'generated_at')::timestamptz;
          IF created IS NULL THEN RAISE EXCEPTION 'SLICE4_ASSEMBLY_INVALID'; END IF;
          INSERT INTO public.assessment_input_assembly(assembly_id,service_case_id,
            subject_member_id,tenant_id,primary_therapist_id,profile_revision_id,
            consent_version_ids,policy_version_id,projection_version,rule_version,
            resolved_generation_id,required_max_fact_id,required_max_status_event_seq,
            source_snapshot,status,reason_codes,missing_codes,expired_codes,disputed_codes,
            source_vector_digest,source_digest,assembly_digest,digest_key_id,generated_at)
          VALUES(value_requested_assembly_id,value_service_case_id,value_subject_member_id,
            resolved_tenant_id,value_primary_therapist_id,value_profile_revision_id,
            value_source_vector->'consent_version_ids',value_policy_version_id,
            value_projection_version,value_rule_version,
            NULLIF(value_source_vector->>'resolved_generation_id','')::bigint,
            (value_source_vector->>'required_max_fact_id')::bigint,
            (value_source_vector->>'required_max_status_event_seq')::bigint,
            value_source_snapshot,value_readiness_status,value_reason_codes,
            value_source_vector->'missing_codes',value_source_vector->'expired_codes',
            value_source_vector->'disputed_codes',
            decode(value_source_vector->>'source_vector_digest','hex'),
            decode(value_source_vector->>'source_digest','hex'),
            decode(value_source_vector->>'assembly_digest','hex'),
            value_source_vector->>'digest_key_id',created);
          INSERT INTO public.assessment_input_assembly_fact(assembly_id,indicator_code,
            fact_ref,measured_at,received_at,source_type,verification_state,
            value_ciphertext,value_key_id,unit,business_day,row_digest)
          SELECT value_requested_assembly_id,x.indicator_code,x.fact_ref,x.measured_at,
            x.received_at,x.source_type,x.verification_state,decode(x.value_ciphertext,'hex'),
            x.value_key_id,x.unit,(x.measured_at AT TIME ZONE 'Asia/Shanghai')::date,
            decode(x.row_digest,'hex')
          FROM jsonb_to_recordset(value_encrypted_fact_rows) AS x(
            indicator_code varchar,fact_ref uuid,measured_at timestamptz,received_at timestamptz,
            source_type varchar,verification_state varchar,value_ciphertext varchar,
            value_key_id varchar,unit varchar,row_digest varchar)
          ORDER BY x.indicator_code;
          INSERT INTO public.assessment_readiness_case_pointer(service_case_id,
            current_assembly_id,source_vector_digest,version,updated_at)
          VALUES(value_service_case_id,value_requested_assembly_id,
            decode(value_source_vector->>'source_vector_digest','hex'),next_pointer_version,created)
          ON CONFLICT ON CONSTRAINT pk_assessment_readiness_case_pointer DO UPDATE SET
            current_assembly_id=EXCLUDED.current_assembly_id,
            source_vector_digest=EXCLUDED.source_vector_digest,
            version=EXCLUDED.version,updated_at=EXCLUDED.updated_at;
          INSERT INTO public.slice4_audit(audit_id,event_type,aggregate_ref,actor_user_id,
            event_digest,digest_key_id,created_at)
          SELECT (value_source_vector->>'audit_id')::uuid,'ASSESSMENT_ASSEMBLY_WRITTEN',value_requested_assembly_id,
            p.user_id,value_expected_postimage_digest,resolved_digest_key,created
            FROM public.therapist_profile p WHERE p.therapist_id=value_primary_therapist_id;
          INSERT INTO public.slice4_outbox(event_id,aggregate_type,aggregate_ref,event_type,
            payload_digest,payload_json,status,attempts,created_at)
          VALUES((value_source_vector->>'event_id')::uuid,'ASSESSMENT_ASSEMBLY',value_requested_assembly_id,
            'ASSESSMENT_ASSEMBLY_WRITTEN',value_expected_postimage_digest,
            jsonb_build_object('assembly_id',value_requested_assembly_id,
              'service_case_id',value_service_case_id),'PENDING',0,created);
          INSERT INTO public.slice4_idempotency(receipt_id,operation,scope_ref,idempotency_key,
            request_digest,postimage_digest,created_at)
          VALUES((value_source_vector->>'receipt_id')::uuid,'WRITE_ASSESSMENT_ASSEMBLY',value_service_case_id,
            value_idempotency_key,value_request_digest,value_expected_postimage_digest,created);
          RETURN QUERY SELECT value_requested_assembly_id,value_service_case_id,
            value_readiness_status,next_pointer_version,created;
        END; $$;'''
    )


def _replace_slice7_authorities(roles: dict[str, str]) -> None:
    replacements = {
        "__MILESTONE__": roles["slice7_milestone_writer"],
        "__CASE__": roles["slice7_case_writer"],
        "__TRANSFER__": roles["slice7_transfer_writer"],
        "__EXPORT__": roles["slice7_export_worker"],
        "__FAMILY__": roles["slice7_family_reader"],
        "__OVERSIGHT__": roles["slice7_oversight_reader"],
    }
    body = "IF session_user NOT IN ('__MILESTONE__', '__CASE__', '__TRANSFER__', '__EXPORT__', '__FAMILY__', '__OVERSIGHT__') THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; IF value_operation='AUTHORIZE_PROXY_MAJOR' THEN SELECT jsonb_build_object('actor_user_id',u.id,'proxy_grant_id',g.grant_id,'principal_member_id',g.principal_member_id,'proxy_member_id',g.proxy_member_id,'authorization_document_version_id',g.authorization_document_version_id,'witness_decision_id',g.witness_decision_id) INTO result FROM public.\"user\" u JOIN public.proxy_grant g ON g.grant_id=value_target JOIN identity.member p ON p.member_id=g.principal_member_id AND p.status='created' JOIN identity.member x ON x.member_id=g.proxy_member_id AND x.status='created' WHERE u.id=value_actor AND u.role::text=value_role AND u.status='active' AND value_role IN ('super_admin','sys_admin') AND g.status='ACTIVE' AND g.valid_from<=clock_timestamp() AND (g.valid_until IS NULL OR g.valid_until>clock_timestamp()) AND g.authorization_document_version_id IS NOT NULL AND g.witness_decision_id IS NOT NULL FOR SHARE OF u,g,p,x; RETURN result; END IF; IF value_operation='REVOKE_PROXY_MAJOR' THEN SELECT jsonb_build_object('actor_user_id',u.id,'authorization_id',a.authorization_id,'proxy_grant_id',a.proxy_grant_id,'principal_member_id',a.principal_member_id,'proxy_member_id',a.proxy_member_id,'authorization_document_version_id',a.authorization_document_version_id,'witness_decision_id',a.witness_decision_id,'permission_codes',a.permission_codes,'granted_by',a.granted_by,'valid_from',a.valid_from,'valid_until',a.valid_until,'revoked_at',a.revoked_at,'authorization_version',a.version) INTO result FROM public.\"user\" u JOIN public.proxy_major_authorization a ON a.authorization_id=value_target WHERE u.id=value_actor AND u.role::text=value_role AND u.status='active' AND value_role IN ('super_admin','sys_admin') AND NOT EXISTS(SELECT 1 FROM public.proxy_major_authorization newer WHERE newer.authorization_id=a.authorization_id AND newer.version>a.version) AND a.revoked_at IS NULL FOR SHARE OF u,a; RETURN result; END IF; IF value_operation='VALIDATE_CONTINUATION_CASE' THEN SELECT jsonb_build_object('new_service_case_id',c.case_id,'new_enrollment_id',c.enrollment_id,'subject_member_id',c.subject_member_id,'tenant_id',c.tenant_id,'tenant_public_id',ia.tenant_public_id,'case_status',COALESCE((SELECT le.to_status FROM public.service_case_lifecycle_event le WHERE le.service_case_id=c.case_id ORDER BY le.version DESC LIMIT 1),'PLAN_PENDING'),'service_ready',sr.readiness_status='SERVICE_READY','assignment_current',pa.status='ACCEPTED','therapist_current',tp.status='APPROVED_ACTIVE' AND tp.current_qualification_version_id IS NOT NULL AND tp.qualification_valid_until>=CURRENT_DATE AND tp.tenant_id=c.tenant_id AND tp.service_tags @> c.service_scope_tags,'consent_current',NOT EXISTS(SELECT required.document_type FROM unnest(ARRAY['USER_AGREEMENT','PRIVACY_POLICY','HEALTH_DATA_PROCESSING','INSTITUTION_SERVICE','NON_MEDICAL_RISK']) required(document_type) WHERE NOT EXISTS(SELECT 1 FROM public.consent_record cr JOIN public.consent_document_version cd ON cd.document_version_id=cr.document_version_id AND cd.status='PUBLISHED' WHERE cr.enrollment_id=c.enrollment_id AND cr.document_type=required.document_type AND cr.status='ACCEPTED'))) INTO result FROM public.\"user\" u JOIN public.service_case c ON c.case_id=value_target JOIN public.service_enrollment se ON se.enrollment_id=c.enrollment_id AND se.tenant_id=c.tenant_id AND se.subject_member_id=c.subject_member_id AND se.status='CASE_CREATED' AND se.service_case_id=c.case_id JOIN public.primary_therapist_assignment pa ON pa.assignment_id=c.assignment_id AND pa.enrollment_id=c.enrollment_id AND pa.tenant_id=c.tenant_id AND pa.subject_member_id=c.subject_member_id AND pa.therapist_id=c.primary_therapist_id AND pa.status='ACCEPTED' AND pa.service_case_id=c.case_id JOIN public.therapist_profile tp ON tp.therapist_id=c.primary_therapist_id JOIN public.institution_service_readiness sr ON sr.tenant_id=c.tenant_id JOIN public.institution_tenant_origin ia ON ia.tenant_id=c.tenant_id AND EXISTS(SELECT 1 FROM public.institution_tenant_origin_current_v1(ia.tenant_id,ia.tenant_public_id)) WHERE u.id=value_actor AND u.role='org_admin' AND u.status='active' AND u.tenant_id=c.tenant_id AND sr.readiness_status='SERVICE_READY' AND tp.status='APPROVED_ACTIVE' AND tp.current_qualification_version_id IS NOT NULL AND tp.qualification_valid_until>=CURRENT_DATE AND tp.tenant_id=c.tenant_id AND tp.service_tags @> c.service_scope_tags AND NOT EXISTS(SELECT 1 FROM public.health_plan_version hp WHERE hp.service_case_id=c.case_id AND hp.status='ACTIVE') FOR SHARE OF u,c,se,pa,tp,sr,ia; RETURN result; END IF; IF value_operation IN ('START_REVIEW_TRANSFER','ACCEPT_TRANSFER','REJECT_TRANSFER','SOURCE_CLOSE_TRANSFER','CONFIRM_TRANSFER_SCOPE','CANCEL_TRANSFER','COORDINATE_TRANSFER_CLOSE','LINK_CONTINUATION_CASE','READ_TRANSFER','READ_HANDOFF') THEN SELECT jsonb_build_object('service_case_id',c.case_id,'subject_member_id',c.subject_member_id,'tenant_id',c.tenant_id,'tenant_public_id',source_ia.tenant_public_id,'case_status',COALESCE((SELECT le.to_status FROM public.service_case_lifecycle_event le WHERE le.service_case_id=c.case_id ORDER BY le.version DESC LIMIT 1),'PLAN_PENDING'),'service_case_version',c.version,'service_ready',source_sr.readiness_status='SERVICE_READY','primary_therapist_current',pa.status='ACCEPTED' AND tp.status='APPROVED_ACTIVE' AND tp.current_qualification_version_id IS NOT NULL AND tp.qualification_valid_until>=CURRENT_DATE AND tp.tenant_id=c.tenant_id AND tp.service_tags @> c.service_scope_tags,'consent_current',NOT EXISTS(SELECT required.document_type FROM unnest(ARRAY['USER_AGREEMENT','PRIVACY_POLICY','HEALTH_DATA_PROCESSING','INSTITUTION_SERVICE','NON_MEDICAL_RISK']) required(document_type) WHERE NOT EXISTS(SELECT 1 FROM public.consent_record cr JOIN public.consent_document_version cd ON cd.document_version_id=cr.document_version_id AND cd.status='PUBLISHED' WHERE cr.enrollment_id=c.enrollment_id AND cr.document_type=required.document_type AND cr.status='ACCEPTED')),'transfer_target_tenant_id',tr.target_tenant_id,'transfer_requested_scope',tr.requested_scope,'transfer_target_decision',tr.target_decision,'transfer_source_closure_status',tr.source_closure_status,'transfer_scope_confirmed_at',tr.scope_confirmed_at,'transfer_transferred_at',tr.transferred_at,'transfer_status',tr.status,'transfer_version',tr.version,'target_service_ready',target_sr.readiness_status='SERVICE_READY','target_service_tags',COALESCE((SELECT guard.service_tags FROM public.institution_readiness_guard_v1 guard WHERE guard.tenant_id=target_ia.tenant_id AND guard.tenant_public_id=target_ia.tenant_public_id),'[]'::jsonb),'handoff_id',h.handoff_id,'handoff_created_at',h.created_at,'handoff_status',h.status,'handoff_version',h.version) INTO result FROM public.service_transfer_request tr JOIN public.service_case c ON c.case_id=tr.source_service_case_id JOIN public.institution_tenant_origin source_ia ON source_ia.tenant_public_id=tr.source_tenant_id AND source_ia.tenant_id=c.tenant_id AND EXISTS(SELECT 1 FROM public.institution_tenant_origin_current_v1(source_ia.tenant_id,source_ia.tenant_public_id)) JOIN public.institution_service_readiness source_sr ON source_sr.tenant_id=c.tenant_id JOIN public.primary_therapist_assignment pa ON pa.assignment_id=c.assignment_id JOIN public.therapist_profile tp ON tp.therapist_id=c.primary_therapist_id JOIN public.institution_tenant_origin target_ia ON target_ia.tenant_public_id=tr.target_tenant_id AND EXISTS(SELECT 1 FROM public.institution_tenant_origin_current_v1(target_ia.tenant_id,target_ia.tenant_public_id)) JOIN public.institution_service_readiness target_sr ON target_sr.tenant_id=target_ia.tenant_id LEFT JOIN public.service_transfer_continuation_handoff h ON h.transfer_id=tr.transfer_id JOIN public.\"user\" u ON u.id=value_actor AND u.status='active' WHERE tr.transfer_id=value_target AND ((value_operation IN ('START_REVIEW_TRANSFER','ACCEPT_TRANSFER','REJECT_TRANSFER','LINK_CONTINUATION_CASE','READ_HANDOFF') AND u.role='org_admin' AND u.tenant_id=target_ia.tenant_id) OR (value_operation='SOURCE_CLOSE_TRANSFER' AND u.role='org_admin' AND u.tenant_id=c.tenant_id) OR (value_operation='COORDINATE_TRANSFER_CLOSE' AND u.role::text=value_role AND value_role IN ('super_admin','sys_admin')) OR (value_operation IN ('CONFIRM_TRANSFER_SCOPE','CANCEL_TRANSFER') AND u.role='member' AND (EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=c.subject_member_id) OR public.slice7_proxy_major_current_v1(u.id,c.subject_member_id,'SERVICE_TRANSFER') IS NOT NULL)) OR (value_operation='READ_TRANSFER' AND ((u.role IN ('org_admin','org_operator') AND u.tenant_id IN (c.tenant_id,target_ia.tenant_id)) OR u.role::text IN ('super_admin','sys_admin') OR (u.role='member' AND (EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=c.subject_member_id) OR EXISTS(SELECT 1 FROM identity.user_member_self_link l JOIN public.proxy_grant g ON g.proxy_member_id=l.member_id WHERE l.user_ref=u.id AND g.principal_member_id=c.subject_member_id AND g.status='ACTIVE')))))) AND (value_operation IN ('LINK_CONTINUATION_CASE','READ_HANDOFF','READ_TRANSFER') OR (source_sr.readiness_status='SERVICE_READY' AND pa.status='ACCEPTED' AND tp.status='APPROVED_ACTIVE' AND tp.current_qualification_version_id IS NOT NULL AND tp.qualification_valid_until>=CURRENT_DATE AND tp.tenant_id=c.tenant_id AND tp.service_tags @> c.service_scope_tags AND NOT EXISTS(SELECT required.document_type FROM unnest(ARRAY['USER_AGREEMENT','PRIVACY_POLICY','HEALTH_DATA_PROCESSING','INSTITUTION_SERVICE','NON_MEDICAL_RISK']) required(document_type) WHERE NOT EXISTS(SELECT 1 FROM public.consent_record cr JOIN public.consent_document_version cd ON cd.document_version_id=cr.document_version_id AND cd.status='PUBLISHED' WHERE cr.enrollment_id=c.enrollment_id AND cr.document_type=required.document_type AND cr.status='ACCEPTED')) AND COALESCE((SELECT le.to_status FROM public.service_case_lifecycle_event le WHERE le.service_case_id=c.case_id ORDER BY le.version DESC LIMIT 1),'PLAN_PENDING') NOT IN ('COMPLETED','WITHDRAWN_BY_USER','TERMINATED_BY_INSTITUTION','TRANSFERRED','UNABLE_TO_CONTACT','SAFETY_TERMINATED'))) FOR UPDATE OF tr,c; RETURN result; END IF; IF value_operation='CREATE_TRANSFER' THEN SELECT jsonb_build_object('service_case_id',c.case_id,'subject_member_id',c.subject_member_id,'tenant_id',c.tenant_id,'tenant_public_id',ia.tenant_public_id,'case_status',COALESCE((SELECT le.to_status FROM public.service_case_lifecycle_event le WHERE le.service_case_id=c.case_id ORDER BY le.version DESC LIMIT 1),'PLAN_PENDING'),'service_case_version',c.version,'service_ready',sr.readiness_status='SERVICE_READY','primary_therapist_current',pa.status='ACCEPTED' AND tp.status='APPROVED_ACTIVE' AND tp.current_qualification_version_id IS NOT NULL AND tp.qualification_valid_until>=CURRENT_DATE AND tp.tenant_id=c.tenant_id AND tp.service_tags @> c.service_scope_tags,'consent_current',true) INTO result FROM public.service_case c JOIN public.institution_tenant_origin ia ON ia.tenant_id=c.tenant_id AND EXISTS(SELECT 1 FROM public.institution_tenant_origin_current_v1(ia.tenant_id,ia.tenant_public_id)) JOIN public.institution_service_readiness sr ON sr.tenant_id=c.tenant_id JOIN public.primary_therapist_assignment pa ON pa.assignment_id=c.assignment_id JOIN public.therapist_profile tp ON tp.therapist_id=c.primary_therapist_id JOIN public.\"user\" u ON u.id=value_actor AND u.role='member' AND u.status='active' WHERE c.case_id=value_target AND sr.readiness_status='SERVICE_READY' AND pa.status='ACCEPTED' AND tp.status='APPROVED_ACTIVE' AND tp.current_qualification_version_id IS NOT NULL AND tp.qualification_valid_until>=CURRENT_DATE AND tp.tenant_id=c.tenant_id AND tp.service_tags @> c.service_scope_tags AND COALESCE((SELECT le.to_status FROM public.service_case_lifecycle_event le WHERE le.service_case_id=c.case_id ORDER BY le.version DESC LIMIT 1),'PLAN_PENDING') NOT IN ('COMPLETED','WITHDRAWN_BY_USER','TERMINATED_BY_INSTITUTION','TRANSFERRED','UNABLE_TO_CONTACT','SAFETY_TERMINATED') AND (EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=c.subject_member_id) OR public.slice7_proxy_major_current_v1(u.id,c.subject_member_id,'SERVICE_TRANSFER') IS NOT NULL) FOR UPDATE OF c; RETURN result; END IF; IF value_operation='CREATE_EXPORT_FOR_SUBJECT' THEN SELECT CASE WHEN EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=m.member_id) OR public.slice7_proxy_major_current_v1(u.id,m.member_id,'PERSONAL_DATA_EXPORT') IS NOT NULL THEN jsonb_build_object('subject_member_id',m.member_id) ELSE jsonb_build_object('error_code','PROXY_PERMISSION_FORBIDDEN') END INTO result FROM public.\"user\" u JOIN identity.member m ON m.member_id=value_target AND m.status='created' WHERE u.id=value_actor AND u.role='member' AND u.status='active' AND u.tenant_id IS NULL AND (EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=m.member_id) OR EXISTS(SELECT 1 FROM identity.user_member_self_link l JOIN public.proxy_grant g ON g.proxy_member_id=l.member_id WHERE l.user_ref=u.id AND g.principal_member_id=m.member_id AND g.status='ACTIVE' AND g.valid_from<=clock_timestamp() AND (g.valid_until IS NULL OR g.valid_until>clock_timestamp()) AND g.revoked_at IS NULL)) FOR SHARE OF u,m; RETURN result; END IF; IF value_operation='CREATE_EXPORT' THEN SELECT jsonb_build_object('subject_member_id',l.member_id) INTO result FROM public.\"user\" u JOIN identity.user_member_self_link l ON l.user_ref=u.id JOIN identity.member m ON m.member_id=l.member_id AND m.status='created' WHERE u.id=value_actor AND u.role='member' AND u.status='active' AND u.tenant_id IS NULL FOR SHARE OF u,l,m; RETURN result; END IF; IF value_operation IN ('CANCEL_EXPORT','EXPORT_DOWNLOAD_ACCESS','READ_EXPORT') THEN IF value_role='member' THEN SELECT CASE WHEN EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=e.subject_member_id) OR public.slice7_proxy_major_current_v1(u.id,e.subject_member_id,'PERSONAL_DATA_EXPORT') IS NOT NULL THEN jsonb_build_object('subject_member_id',e.subject_member_id,'export_requested_scope',e.requested_scope,'export_status',e.status,'export_requested_at',e.requested_at,'export_ready_at',e.ready_at,'export_expires_at',e.expires_at,'export_downloaded_at',e.downloaded_at,'export_version',e.version,'private_file_id',a.private_file_id,'artifact_digest',encode(a.artifact_digest,'hex')) ELSE jsonb_build_object('error_code','PROXY_PERMISSION_FORBIDDEN') END INTO result FROM public.\"user\" u JOIN public.personal_data_export_request e ON e.export_id=value_target JOIN identity.member m ON m.member_id=e.subject_member_id AND m.status='created' LEFT JOIN public.personal_data_export_artifact a ON a.export_id=e.export_id WHERE u.id=value_actor AND u.role='member' AND u.status='active' AND u.tenant_id IS NULL AND (EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=e.subject_member_id) OR EXISTS(SELECT 1 FROM identity.user_member_self_link l JOIN public.proxy_grant g ON g.proxy_member_id=l.member_id WHERE l.user_ref=u.id AND g.principal_member_id=e.subject_member_id AND g.status='ACTIVE' AND g.valid_from<=clock_timestamp() AND (g.valid_until IS NULL OR g.valid_until>clock_timestamp()) AND g.revoked_at IS NULL)) FOR SHARE OF u,e,m; ELSIF value_role IN ('super_admin','sys_admin') AND value_operation='READ_EXPORT' THEN SELECT jsonb_build_object('subject_member_id',e.subject_member_id,'export_requested_scope',e.requested_scope,'export_status',e.status,'export_requested_at',e.requested_at,'export_ready_at',e.ready_at,'export_expires_at',e.expires_at,'export_downloaded_at',e.downloaded_at,'export_version',e.version) INTO result FROM public.\"user\" u JOIN public.personal_data_export_request e ON e.export_id=value_target WHERE u.id=value_actor AND u.role::text=value_role AND u.status='active' FOR SHARE OF u; END IF; RETURN result; END IF; PERFORM 1 FROM public.\"user\" u WHERE u.id=value_actor AND u.role::text=value_role AND u.status='active' FOR SHARE; IF NOT FOUND THEN RETURN NULL; END IF; PERFORM pg_advisory_xact_lock(hashtextextended(value_target::text,7)); SELECT jsonb_build_object('service_case_id',c.case_id,'subject_member_id',c.subject_member_id,'tenant_id',c.tenant_id,'tenant_public_id',ia.tenant_public_id,'case_status',COALESCE((SELECT e.to_status FROM public.service_case_lifecycle_event e WHERE e.service_case_id=c.case_id ORDER BY e.version DESC LIMIT 1),CASE WHEN sc.schedule_id IS NULL THEN 'PLAN_PENDING' ELSE 'ACTIVE' END),'service_case_version',c.version,'service_ready',sr.readiness_status='SERVICE_READY','primary_therapist_current',pa.status='ACCEPTED' AND current_tp.status='APPROVED_ACTIVE' AND current_tp.current_qualification_version_id IS NOT NULL AND current_tp.qualification_valid_until>=CURRENT_DATE AND current_tp.tenant_id=c.tenant_id AND current_tp.service_tags @> c.service_scope_tags,'consent_current',NOT EXISTS(SELECT required.document_type FROM unnest(ARRAY['USER_AGREEMENT','PRIVACY_POLICY','HEALTH_DATA_PROCESSING','INSTITUTION_SERVICE','NON_MEDICAL_RISK']) required(document_type) WHERE NOT EXISTS(SELECT 1 FROM public.consent_record cr JOIN public.consent_document_version cd ON cd.document_version_id=cr.document_version_id AND cd.status='PUBLISHED' WHERE cr.enrollment_id=c.enrollment_id AND cr.document_type=required.document_type AND cr.status='ACCEPTED')),'high_risk_count',(SELECT count(*) FROM public.high_risk_task h WHERE h.service_case_id=c.case_id AND h.status IN ('OPEN','CLAIMED','ESCALATED')),'active_plan_id',hp.plan_id,'plan_activated_at',hp.updated_at,'schedule_id',sc.schedule_id,'cycle_anchor_at',sc.cycle_anchor_at,'schedule_version',sc.version_no,'milestone_id',m.milestone_id,'milestone_code',m.code,'window_start',m.window_start,'window_end',m.window_end,'milestone_status',m.status,'milestone_version',m.version,'milestones',COALESCE((SELECT jsonb_agg(jsonb_build_object('milestone_id',all_m.milestone_id,'service_case_id',all_m.service_case_id,'code',all_m.code,'window_start',all_m.window_start,'window_end',all_m.window_end,'status',all_m.status,'completed_at',all_m.completed_at,'record_summary',all_m.record_summary,'version',all_m.version) ORDER BY all_m.code) FROM public.service_milestone all_m WHERE all_m.service_case_id=c.case_id),'[]'::jsonb),'latest_assessment_id',(SELECT h.assessment_id FROM public.health_assessment h WHERE h.service_case_id=c.case_id AND h.status='COMPLETED' ORDER BY h.sequence_no DESC LIMIT 1),'closing_assessment_complete',EXISTS(SELECT 1 FROM public.service_closing_assessment ca WHERE ca.service_case_id=c.case_id),'summary_complete',EXISTS(SELECT 1 FROM public.service_summary sx WHERE sx.service_case_id=c.case_id),'summary_acknowledged',EXISTS(SELECT 1 FROM public.service_summary sx JOIN public.service_summary_acknowledgement ack ON ack.summary_id=sx.summary_id WHERE sx.service_case_id=c.case_id),'transfer_target_tenant_id',tr.target_tenant_id,'transfer_requested_scope',tr.requested_scope,'transfer_target_decision',tr.target_decision,'transfer_source_closure_status',tr.source_closure_status,'transfer_scope_confirmed_at',tr.scope_confirmed_at,'transfer_transferred_at',tr.transferred_at,'transfer_status',tr.status,'transfer_version',tr.version,'summary_assessment_id',sm.assessment_id,'summary_content',sm.content,'summary_created_at',sm.created_at,'summary_version',sm.version) INTO result FROM public.service_case c JOIN public.institution_service_readiness sr ON sr.tenant_id=c.tenant_id JOIN public.institution_tenant_origin ia ON ia.tenant_id=c.tenant_id AND EXISTS(SELECT 1 FROM public.institution_tenant_origin_current_v1(ia.tenant_id,ia.tenant_public_id)) LEFT JOIN public.primary_therapist_assignment pa ON pa.assignment_id=c.assignment_id LEFT JOIN public.therapist_profile current_tp ON current_tp.therapist_id=c.primary_therapist_id LEFT JOIN public.health_plan_version hp ON hp.service_case_id=c.case_id AND hp.status='ACTIVE' LEFT JOIN public.service_cycle_schedule sc ON sc.service_case_id=c.case_id AND sc.is_current LEFT JOIN public.service_milestone m ON m.milestone_id=value_target AND m.service_case_id=c.case_id LEFT JOIN public.service_transfer_request tr ON tr.transfer_id=value_target AND tr.source_service_case_id=c.case_id LEFT JOIN public.service_summary sm ON sm.summary_id=value_target AND sm.service_case_id=c.case_id WHERE (c.case_id=value_target OR m.milestone_id IS NOT NULL OR tr.transfer_id IS NOT NULL OR sm.summary_id IS NOT NULL) AND (value_tenant IS NULL OR c.tenant_id=value_tenant OR value_role IN ('super_admin','sys_admin') OR (tr.transfer_id IS NOT NULL AND EXISTS(SELECT 1 FROM public.institution_tenant_origin target_ia WHERE target_ia.tenant_public_id=tr.target_tenant_id AND target_ia.tenant_id=value_tenant AND EXISTS(SELECT 1 FROM public.institution_tenant_origin_current_v1(target_ia.tenant_id,target_ia.tenant_public_id))))) AND (((value_role IN ('org_admin','org_operator')) AND EXISTS(SELECT 1 FROM public.\"user\" u WHERE u.id=value_actor AND u.role::text=value_role AND u.status='active' AND (u.tenant_id=c.tenant_id OR EXISTS(SELECT 1 FROM public.institution_tenant_origin target_ia WHERE tr.transfer_id IS NOT NULL AND target_ia.tenant_public_id=tr.target_tenant_id AND target_ia.tenant_id=u.tenant_id AND EXISTS(SELECT 1 FROM public.institution_tenant_origin_current_v1(target_ia.tenant_id,target_ia.tenant_public_id)))))) OR (value_role='therapist' AND EXISTS(SELECT 1 FROM public.\"user\" u JOIN public.therapist_profile tp ON tp.user_id=u.id WHERE u.id=value_actor AND u.role='therapist' AND u.status='active' AND u.tenant_id=c.tenant_id AND tp.therapist_id=c.primary_therapist_id AND tp.status='APPROVED_ACTIVE' AND tp.current_qualification_version_id IS NOT NULL AND tp.qualification_valid_until>=CURRENT_DATE AND pa.status='ACCEPTED')) OR (value_role='member' AND EXISTS(SELECT 1 FROM public.\"user\" u JOIN identity.user_member_self_link l ON l.user_ref=u.id WHERE u.id=value_actor AND u.role='member' AND u.status='active' AND (l.member_id=c.subject_member_id OR (value_operation='WITHDRAW_CASE' AND public.slice7_proxy_major_current_v1(u.id,c.subject_member_id,'SERVICE_WITHDRAW') IS NOT NULL)))) OR (value_role IN ('super_admin','sys_admin') AND EXISTS(SELECT 1 FROM public.\"user\" u WHERE u.id=value_actor AND u.role::text=value_role AND u.status='active'))) FOR UPDATE OF c; IF result IS NOT NULL AND value_operation NOT LIKE 'READ_%' AND ((result->>'service_ready')::boolean IS NOT TRUE OR (result->>'primary_therapist_current')::boolean IS NOT TRUE OR (result->>'consent_current')::boolean IS NOT TRUE OR result->>'case_status' IN ('COMPLETED','WITHDRAWN_BY_USER','TERMINATED_BY_INSTITUTION','TRANSFERRED','UNABLE_TO_CONTACT','SAFETY_TERMINATED')) THEN RETURN NULL; END IF; RETURN result;"
    for marker, role in replacements.items():
        body = body.replace(marker, role)
    op.execute(sa.text(
        "CREATE OR REPLACE FUNCTION public.slice7_authority_v1(value_operation VARCHAR,value_target UUID,value_actor BIGINT,value_role VARCHAR,value_tenant BIGINT) "
        "RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER "
        "SET search_path=pg_catalog,pg_temp AS $fn$ "
        "DECLARE result JSONB; " + "BEGIN " + body + " END $fn$"
    ))
    body = "IF session_user NOT IN ('__MILESTONE__', '__CASE__', '__TRANSFER__', '__EXPORT__') THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; operation_name:=value->>'operation'; IF operation_name IS NULL OR value->>'request_digest' !~ '^[0-9a-f]{64}$' OR value->>'expected_response_digest' !~ '^[0-9a-f]{64}$' OR jsonb_typeof(value->'response')<>'object' THEN RAISE EXCEPTION 'INVALID_REQUEST'; END IF; IF (operation_name IN ('ACTIVATE_CYCLE','COMPLETE_MILESTONE') AND session_user<>'__MILESTONE__') OR (operation_name='MARK_MISSED' AND session_user<>'__EXPORT__') OR (operation_name IN ('PAUSE_CASE','RESUME_CASE','WITHDRAW_CASE','TERMINATE_CASE','UNABLE_TO_CONTACT','SAFETY_TERMINATE','CREATE_CLOSING_ASSESSMENT','CREATE_SUMMARY','ACK_SUMMARY','COMPLETE_CASE') AND session_user<>'__CASE__') OR (operation_name IN ('CREATE_TRANSFER','CANCEL_TRANSFER','CONFIRM_TRANSFER_SCOPE','START_REVIEW_TRANSFER','ACCEPT_TRANSFER','REJECT_TRANSFER','SOURCE_CLOSE_TRANSFER','COORDINATE_TRANSFER_CLOSE','LINK_CONTINUATION_CASE','AUTHORIZE_PROXY_MAJOR','REVOKE_PROXY_MAJOR','CREATE_EXPORT','CANCEL_EXPORT','EXPORT_DOWNLOAD_ACCESS') AND session_user<>'__TRANSFER__') THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; SELECT request_digest,response_json INTO stored_digest,stored_response FROM public.service_fulfillment_receipt WHERE actor_scope=value->>'actor_scope' AND operation=operation_name AND idempotency_key=value->>'idempotency_key' FOR SHARE; IF stored_response IS NOT NULL THEN IF stored_digest<>decode(value->>'request_digest','hex') THEN RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT'; END IF; RETURN stored_response; END IF; PERFORM pg_advisory_xact_lock(hashtextextended(value->>'target_id',7)); IF operation_name<>'MARK_MISSED' THEN current_authority:=public.slice7_authority_v1(COALESCE(value->>'authority_operation',operation_name),COALESCE((value->>'authority_target_id')::uuid,(value->>'target_id')::uuid),(value->>'actor_user_id')::bigint,value->>'actor_role',(value->>'actor_tenant_id')::bigint); IF current_authority IS NULL OR current_authority<>value->'authority' THEN RAISE EXCEPTION 'CURRENTNESS_FORBIDDEN'; END IF; END IF; IF operation_name='MARK_MISSED' THEN UPDATE public.service_milestone SET status='MISSED',version=version+1 WHERE milestone_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND status IN ('PENDING','DUE') AND window_end<((value->>'occurred_at')::timestamptz AT TIME ZONE 'Asia/Shanghai')::date RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.service_milestone_revision(revision_id,milestone_id,version_no,status,body,body_digest,created_at) VALUES ((value->>'operation_id')::uuid,(value->>'target_id')::uuid,affected,'MISSED',jsonb_build_object('reason_code','MILESTONE_MISSED','worker_id',value->>'worker_id'),decode(value->>'request_digest','hex'),(value->>'occurred_at')::timestamptz); ELSIF operation_name='ACTIVATE_CYCLE' THEN INSERT INTO public.service_cycle_schedule(schedule_id,service_case_id,subject_member_id,tenant_id,active_plan_id,version_no,cycle_anchor_at,is_current,created_at,version) VALUES ((value->>'operation_id')::uuid,(value->>'target_id')::uuid,(current_authority->>'subject_member_id')::uuid,(current_authority->>'tenant_id')::bigint,(value->'response'->>'active_plan_id')::uuid,1,(value->>'activated_at')::timestamptz,true,(value->>'occurred_at')::timestamptz,1); INSERT INTO public.service_milestone(milestone_id,schedule_id,service_case_id,code,window_start,window_end,status,completed_at,record_summary,version) SELECT (value->'milestone_ids'->>code)::uuid,(value->>'operation_id')::uuid,(value->>'target_id')::uuid,code,(value->'windows'->code->>'start')::date,(value->'windows'->code->>'end')::date,CASE code WHEN 'D0' THEN 'DUE' ELSE 'PENDING' END,NULL,NULL,1 FROM unnest(ARRAY['D0','D7','D14','D21','D28']) code; ELSIF operation_name='COMPLETE_MILESTONE' THEN UPDATE public.service_milestone SET status='COMPLETED',completed_at=(value->>'occurred_at')::timestamptz,record_summary=value->'record_summary',version=version+1 WHERE milestone_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND status IN ('PENDING','DUE') RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.service_milestone_revision(revision_id,milestone_id,version_no,status,body,body_digest,created_at) VALUES ((value->>'operation_id')::uuid,(value->>'target_id')::uuid,affected,'COMPLETED',jsonb_build_object('record_summary',value->'record_summary','evidence_refs',value->'evidence_refs'),decode(value->>'request_digest','hex'),(value->>'occurred_at')::timestamptz); IF (SELECT count(*) FROM public.service_milestone m WHERE m.service_case_id=(current_authority->>'service_case_id')::uuid)=5 AND NOT EXISTS(SELECT 1 FROM public.service_milestone m WHERE m.service_case_id=(current_authority->>'service_case_id')::uuid AND m.status<>'COMPLETED') THEN UPDATE public.service_case SET version=version+1 WHERE case_id=(current_authority->>'service_case_id')::uuid AND version=(current_authority->>'service_case_version')::bigint RETURNING version INTO case_version; IF case_version IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.service_case_lifecycle_event(lifecycle_event_id,service_case_id,from_status,to_status,reason_code,occurred_at,version) VALUES ((value->>'lifecycle_event_id')::uuid,(current_authority->>'service_case_id')::uuid,'ACTIVE','CLOSING','MILESTONES_COMPLETED',(value->>'occurred_at')::timestamptz,case_version); END IF; ELSIF operation_name IN ('PAUSE_CASE','RESUME_CASE','WITHDRAW_CASE','TERMINATE_CASE','UNABLE_TO_CONTACT','SAFETY_TERMINATE','COMPLETE_CASE') THEN UPDATE public.service_case SET version=version+1 WHERE case_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.service_case_lifecycle_event(lifecycle_event_id,service_case_id,from_status,to_status,reason_code,occurred_at,version) VALUES ((value->>'operation_id')::uuid,(value->>'target_id')::uuid,COALESCE(value->'authority'->>'case_status','ACTIVE'),value->'response'->>'lifecycle_status',COALESCE(value->>'reason_code',operation_name),(value->>'occurred_at')::timestamptz,affected); ELSIF operation_name='CREATE_CLOSING_ASSESSMENT' THEN UPDATE public.service_case SET version=version+1 WHERE case_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND current_authority->>'case_status'='CLOSING' RETURNING version INTO case_version; IF case_version IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.service_closing_assessment(closing_assessment_id,service_case_id,assessment_id,version_no,final_retest_evidence,created_at,version) SELECT (value->>'operation_id')::uuid,(value->>'target_id')::uuid,(value->>'assessment_id')::uuid,COALESCE(max(version_no),0)+1,value->'final_retest_evidence',(value->>'occurred_at')::timestamptz,1 FROM public.service_closing_assessment WHERE service_case_id=(value->>'target_id')::uuid; ELSIF operation_name='CREATE_SUMMARY' THEN UPDATE public.service_case SET version=version+1 WHERE case_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND current_authority->>'case_status'='CLOSING' RETURNING version INTO case_version; IF case_version IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.service_summary(summary_id,service_case_id,assessment_id,version_no,content,content_digest,created_at,version) SELECT (value->>'operation_id')::uuid,(value->>'target_id')::uuid,(value->>'assessment_id')::uuid,COALESCE(max(version_no),0)+1,jsonb_build_object('final_retest_evidence',value->'final_retest_evidence','milestone_outcomes',value->'milestone_outcomes','safety_follow_up',value->'safety_follow_up','next_step',value->'next_step'),decode(value->>'request_digest','hex'),(value->>'occurred_at')::timestamptz,1 FROM public.service_summary WHERE service_case_id=(value->>'target_id')::uuid; ELSIF operation_name='ACK_SUMMARY' THEN PERFORM 1 FROM public.service_summary s WHERE s.summary_id=(value->>'target_id')::uuid AND s.version=(value->>'expected_version')::bigint FOR UPDATE; IF NOT FOUND THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.service_summary_acknowledgement(acknowledgement_id,summary_id,subject_member_id,actor_user_id,proxy_grant_id,viewed_at,version) VALUES ((value->>'operation_id')::uuid,(value->>'target_id')::uuid,(current_authority->>'subject_member_id')::uuid,(value->>'actor_user_id')::bigint,NULL,(value->>'occurred_at')::timestamptz,1); readiness:=public.slice7_closing_readiness_v1((current_authority->>'service_case_id')::uuid); IF readiness<>'READY_TO_CLOSE' THEN RAISE EXCEPTION 'CLOSURE_PREREQUISITE_MISSING'; END IF; UPDATE public.service_case SET version=version+1 WHERE case_id=(current_authority->>'service_case_id')::uuid AND version=(current_authority->>'service_case_version')::bigint AND current_authority->>'case_status'='CLOSING' RETURNING version INTO case_version; IF case_version IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.service_case_lifecycle_event(lifecycle_event_id,service_case_id,from_status,to_status,reason_code,occurred_at,version) VALUES ((value->>'lifecycle_event_id')::uuid,(current_authority->>'service_case_id')::uuid,'CLOSING','COMPLETED','SUMMARY_ACKNOWLEDGED',(value->>'occurred_at')::timestamptz,case_version); ELSIF operation_name='CREATE_TRANSFER' THEN INSERT INTO public.service_transfer_request(transfer_id,source_service_case_id,source_tenant_id,target_tenant_id,subject_member_id,status,requested_scope,target_decision,source_closure_status,scope_confirmed_at,transferred_at,created_at,version) VALUES ((value->>'operation_id')::uuid,(value->>'target_id')::uuid,(current_authority->>'tenant_public_id')::uuid,(value->>'target_tenant_id')::uuid,(current_authority->>'subject_member_id')::uuid,'REQUESTED_BY_USER',value->'requested_scope',NULL,NULL,NULL,NULL,(value->>'occurred_at')::timestamptz,1); INSERT INTO public.service_transfer_scope_revision(scope_revision_id,transfer_id,version_no,scope,scope_digest,created_at) VALUES ((value->>'receipt_id')::uuid,(value->>'operation_id')::uuid,1,value->'requested_scope',decode(value->>'request_digest','hex'),(value->>'occurred_at')::timestamptz); ELSIF operation_name='START_REVIEW_TRANSFER' THEN UPDATE public.service_transfer_request SET status='NEW_INSTITUTION_REVIEWING',version=version+1 WHERE transfer_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND status='REQUESTED_BY_USER' RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'TRANSFER_STATE_CONFLICT'; END IF; ELSIF operation_name IN ('ACCEPT_TRANSFER','REJECT_TRANSFER') THEN IF operation_name='ACCEPT_TRANSFER' AND ((current_authority->>'target_service_ready')::boolean IS NOT TRUE OR NOT (current_authority->'target_service_tags' ? (value->>'service_label'))) THEN RAISE EXCEPTION 'CURRENTNESS_FORBIDDEN'; END IF; UPDATE public.service_transfer_request SET status=CASE operation_name WHEN 'ACCEPT_TRANSFER' THEN 'ACCEPTED' ELSE 'REJECTED_BY_NEW_INSTITUTION' END,target_decision=value->>'reason_code',version=version+1 WHERE transfer_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND status='NEW_INSTITUTION_REVIEWING' RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'TRANSFER_STATE_CONFLICT'; END IF; ELSIF operation_name='SOURCE_CLOSE_TRANSFER' THEN UPDATE public.service_transfer_request SET status='OLD_INSTITUTION_CLOSING',source_closure_status=value->>'risk_disposition',version=version+1 WHERE transfer_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND status='ACCEPTED' RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'TRANSFER_STATE_CONFLICT'; END IF; ELSIF operation_name='CONFIRM_TRANSFER_SCOPE' THEN UPDATE public.service_transfer_request SET status='USER_SCOPE_CONFIRMED',requested_scope=value->'exact_scope',scope_confirmed_at=(value->>'occurred_at')::timestamptz,version=version+1 WHERE transfer_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND status='OLD_INSTITUTION_CLOSING' AND requested_scope=value->'exact_scope' RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'TRANSFER_STATE_CONFLICT'; END IF; INSERT INTO public.service_transfer_scope_revision(scope_revision_id,transfer_id,version_no,scope,scope_digest,created_at) VALUES ((value->>'operation_id')::uuid,(value->>'target_id')::uuid,affected,value->'exact_scope',decode(value->>'request_digest','hex'),(value->>'occurred_at')::timestamptz); ELSIF operation_name='CANCEL_TRANSFER' THEN UPDATE public.service_transfer_request SET status='CANCELLED_BY_USER',version=version+1 WHERE transfer_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND status IN ('REQUESTED_BY_USER','NEW_INSTITUTION_REVIEWING') RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'TRANSFER_STATE_CONFLICT'; END IF; ELSIF operation_name='COORDINATE_TRANSFER_CLOSE' THEN UPDATE public.service_transfer_request SET status='TRANSFERRED',transferred_at=(value->>'occurred_at')::timestamptz,version=version+1 WHERE transfer_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND status='USER_SCOPE_CONFIRMED' RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'TRANSFER_STATE_CONFLICT'; END IF; UPDATE public.service_case SET version=version+1 WHERE case_id=(current_authority->>'service_case_id')::uuid AND version=(current_authority->>'service_case_version')::bigint RETURNING version INTO case_version; IF case_version IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; UPDATE public.service_enrollment SET status='REVOKED',updated_at=(value->>'occurred_at')::timestamptz,version=version+1 WHERE enrollment_id=(SELECT c.enrollment_id FROM public.service_case c WHERE c.case_id=(current_authority->>'service_case_id')::uuid) AND status='CASE_CREATED'; GET DIAGNOSTICS changed=ROW_COUNT; IF changed<>1 THEN RAISE EXCEPTION 'CURRENTNESS_FORBIDDEN'; END IF; INSERT INTO public.service_case_lifecycle_event(lifecycle_event_id,service_case_id,from_status,to_status,reason_code,occurred_at,version) VALUES ((value->>'lifecycle_event_id')::uuid,(current_authority->>'service_case_id')::uuid,current_authority->>'case_status','TRANSFERRED','SERVICE_TRANSFERRED',(value->>'occurred_at')::timestamptz,case_version); INSERT INTO public.service_transfer_continuation_handoff(handoff_id,transfer_id,source_service_case_id,source_tenant_id,target_tenant_id,subject_member_id,authorized_scope,scope_digest,status,created_at,linked_enrollment_id,linked_service_case_id,linked_at,version) VALUES ((value->>'handoff_id')::uuid,(value->>'target_id')::uuid,(current_authority->>'service_case_id')::uuid,(current_authority->>'tenant_public_id')::uuid,(current_authority->>'transfer_target_tenant_id')::uuid,(current_authority->>'subject_member_id')::uuid,current_authority->'transfer_requested_scope',decode(value->>'handoff_scope_digest','hex'),'PENDING_TARGET_ENROLLMENT',(value->>'occurred_at')::timestamptz,NULL,NULL,NULL,1); ELSIF operation_name='LINK_CONTINUATION_CASE' THEN PERFORM 1 FROM public.service_transfer_continuation_handoff h JOIN public.service_transfer_request tr ON tr.transfer_id=h.transfer_id AND tr.status='TRANSFERRED' JOIN public.institution_tenant_origin target_ia ON target_ia.tenant_public_id=h.target_tenant_id AND EXISTS(SELECT 1 FROM public.institution_tenant_origin_current_v1(target_ia.tenant_id,target_ia.tenant_public_id)) JOIN public.service_case c ON c.case_id=(value->>'new_service_case_id')::uuid AND c.enrollment_id=(value->>'new_enrollment_id')::uuid AND c.subject_member_id=h.subject_member_id AND c.tenant_id=target_ia.tenant_id JOIN public.service_enrollment se ON se.enrollment_id=c.enrollment_id AND se.status='CASE_CREATED' AND se.service_case_id=c.case_id JOIN public.primary_therapist_assignment pa ON pa.assignment_id=c.assignment_id AND pa.enrollment_id=c.enrollment_id AND pa.status='ACCEPTED' AND pa.service_case_id=c.case_id JOIN public.therapist_profile tp ON tp.therapist_id=c.primary_therapist_id AND tp.tenant_id=c.tenant_id AND tp.status='APPROVED_ACTIVE' AND tp.current_qualification_version_id IS NOT NULL AND tp.qualification_valid_until>=CURRENT_DATE AND tp.service_tags @> c.service_scope_tags JOIN public.institution_service_readiness sr ON sr.tenant_id=c.tenant_id AND sr.readiness_status='SERVICE_READY' WHERE h.transfer_id=(value->>'target_id')::uuid AND h.version=(value->>'expected_version')::bigint AND h.status='PENDING_TARGET_ENROLLMENT' AND NOT EXISTS(SELECT 1 FROM public.health_plan_version hp WHERE hp.service_case_id=c.case_id AND hp.status='ACTIVE') AND NOT EXISTS(SELECT required.document_type FROM unnest(ARRAY['USER_AGREEMENT','PRIVACY_POLICY','HEALTH_DATA_PROCESSING','INSTITUTION_SERVICE','NON_MEDICAL_RISK']) required(document_type) WHERE NOT EXISTS(SELECT 1 FROM public.consent_record cr JOIN public.consent_document_version cd ON cd.document_version_id=cr.document_version_id AND cd.status='PUBLISHED' WHERE cr.enrollment_id=c.enrollment_id AND cr.document_type=required.document_type AND cr.status='ACCEPTED')) FOR UPDATE OF h,c,se,pa,tp,sr; IF NOT FOUND THEN RAISE EXCEPTION 'CURRENTNESS_FORBIDDEN'; END IF; UPDATE public.service_transfer_continuation_handoff SET status='CONTINUATION_CASE_LINKED',linked_enrollment_id=(value->>'new_enrollment_id')::uuid,linked_service_case_id=(value->>'new_service_case_id')::uuid,linked_at=(value->>'occurred_at')::timestamptz,version=version+1 WHERE transfer_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND status='PENDING_TARGET_ENROLLMENT' RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'TRANSFER_STATE_CONFLICT'; END IF; ELSIF operation_name='AUTHORIZE_PROXY_MAJOR' THEN IF current_authority->>'authorization_document_version_id'<>value->>'authorization_document_version_id' OR current_authority->>'witness_decision_id'<>value->>'witness_decision_id' OR jsonb_typeof(value->'permission_codes')<>'array' OR jsonb_array_length(value->'permission_codes') NOT BETWEEN 1 AND 4 OR NOT value->'permission_codes' <@ '[\"PLAN_DECISION\",\"SERVICE_WITHDRAW\",\"SERVICE_TRANSFER\",\"PERSONAL_DATA_EXPORT\"]'::jsonb OR (value->>'valid_until') IS NOT NULL AND (value->>'valid_until')::timestamptz<=(value->>'occurred_at')::timestamptz OR EXISTS(SELECT 1 FROM public.proxy_major_authorization a WHERE a.proxy_grant_id=(value->>'target_id')::uuid AND NOT EXISTS(SELECT 1 FROM public.proxy_major_authorization newer WHERE newer.authorization_id=a.authorization_id AND newer.version>a.version) AND a.revoked_at IS NULL) THEN RAISE EXCEPTION 'CURRENTNESS_FORBIDDEN'; END IF; INSERT INTO public.proxy_major_authorization(authorization_revision_id,authorization_id,proxy_grant_id,principal_member_id,proxy_member_id,authorization_document_version_id,witness_decision_id,permission_codes,granted_by,valid_from,valid_until,revoked_at,version) VALUES ((value->>'operation_id')::uuid,(value->>'operation_id')::uuid,(value->>'target_id')::uuid,(current_authority->>'principal_member_id')::uuid,(current_authority->>'proxy_member_id')::uuid,(value->>'authorization_document_version_id')::uuid,(value->>'witness_decision_id')::uuid,value->'permission_codes',(value->>'actor_user_id')::bigint,(value->>'occurred_at')::timestamptz,(value->>'valid_until')::timestamptz,NULL,1); ELSIF operation_name='REVOKE_PROXY_MAJOR' THEN IF (current_authority->>'authorization_version')::bigint<>(value->>'expected_version')::bigint THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.proxy_major_authorization(authorization_revision_id,authorization_id,proxy_grant_id,principal_member_id,proxy_member_id,authorization_document_version_id,witness_decision_id,permission_codes,granted_by,valid_from,valid_until,revoked_at,version) VALUES ((value->>'operation_id')::uuid,(value->>'target_id')::uuid,(current_authority->>'proxy_grant_id')::uuid,(current_authority->>'principal_member_id')::uuid,(current_authority->>'proxy_member_id')::uuid,(current_authority->>'authorization_document_version_id')::uuid,(current_authority->>'witness_decision_id')::uuid,current_authority->'permission_codes',(current_authority->>'granted_by')::bigint,(current_authority->>'valid_from')::timestamptz,(current_authority->>'valid_until')::timestamptz,(value->>'occurred_at')::timestamptz,(current_authority->>'authorization_version')::bigint+1); ELSIF operation_name='CREATE_EXPORT' THEN INSERT INTO public.personal_data_export_request(export_id,subject_member_id,requested_scope,status,requested_by,requested_at,ready_at,expires_at,downloaded_at,version) VALUES ((value->>'operation_id')::uuid,(current_authority->>'subject_member_id')::uuid,value->'requested_scope','REQUESTED',(value->>'actor_user_id')::bigint,(value->>'occurred_at')::timestamptz,NULL,NULL,NULL,1); ELSIF operation_name='CANCEL_EXPORT' THEN UPDATE public.personal_data_export_request SET status='CANCELLED',lease_owner=NULL,lease_until=NULL,version=version+1 WHERE export_id=(value->>'target_id')::uuid AND subject_member_id=(current_authority->>'subject_member_id')::uuid AND version=(value->>'expected_version')::bigint AND status IN ('REQUESTED','GENERATING') RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; ELSIF operation_name='EXPORT_DOWNLOAD_ACCESS' THEN IF current_authority->>'private_file_id' IS NULL OR current_authority->>'artifact_digest' IS NULL THEN RAISE EXCEPTION 'EXPORT_NOT_READY'; END IF; IF (current_authority->>'export_version')::bigint<>(value->>'expected_version')::bigint THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.personal_data_export_download_access(access_id,export_id,private_file_id,actor_user_id,reason_code,expires_at,consumed_at,created_at,version) VALUES ((value->'response'->>'access_id')::uuid,(value->>'target_id')::uuid,(current_authority->>'private_file_id')::uuid,(value->>'actor_user_id')::bigint,value->>'reason',(value->'response'->>'expires_at')::timestamptz,NULL,(value->>'occurred_at')::timestamptz,1); ELSE RAISE EXCEPTION 'INVALID_REQUEST'; END IF; INSERT INTO public.service_fulfillment_audit(audit_id,action,actor_user_id,actor_role,target_id,evidence_digest,occurred_at) VALUES ((value->>'audit_id')::uuid,operation_name,(value->>'actor_user_id')::bigint,value->>'actor_role',(value->>'target_id')::uuid,decode(value->>'request_digest','hex'),(value->>'occurred_at')::timestamptz); INSERT INTO public.service_fulfillment_outbox(event_id,aggregate_ref,event_type,payload_json,payload_digest,status,attempts,lease_owner,lease_until,created_at,delivered_at,version) VALUES ((value->>'event_id')::uuid,(value->>'target_id')::uuid,operation_name,jsonb_build_object('operation',operation_name,'target_id',value->>'target_id'),decode(value->>'request_digest','hex'),'PENDING',0,NULL,NULL,(value->>'occurred_at')::timestamptz,NULL,1); INSERT INTO public.service_fulfillment_receipt(receipt_id,actor_scope,operation,target_id,idempotency_key,request_digest,response_json,postimage_digest,created_at) VALUES ((value->>'receipt_id')::uuid,value->>'actor_scope',operation_name,(value->>'target_id')::uuid,value->>'idempotency_key',decode(value->>'request_digest','hex'),value->'response',decode(value->>'expected_response_digest','hex'),(value->>'occurred_at')::timestamptz); RETURN value->'response';"
    for marker, role in replacements.items():
        body = body.replace(marker, role)
    op.execute(sa.text(
        "CREATE OR REPLACE FUNCTION public.slice7_mutation_v1(value JSONB) "
        "RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER "
        "SET search_path=pg_catalog,pg_temp AS $fn$ "
        "DECLARE operation_name VARCHAR; stored_digest BYTEA; stored_response JSONB; affected BIGINT; changed BIGINT; case_version BIGINT; readiness VARCHAR; current_authority JSONB; " + "BEGIN " + body + " END $fn$"
    ))


def _restore_slice7_authorities(roles: dict[str, str]) -> None:
    replacements = {
        "__MILESTONE__": roles["slice7_milestone_writer"],
        "__CASE__": roles["slice7_case_writer"],
        "__TRANSFER__": roles["slice7_transfer_writer"],
        "__EXPORT__": roles["slice7_export_worker"],
        "__FAMILY__": roles["slice7_family_reader"],
        "__OVERSIGHT__": roles["slice7_oversight_reader"],
    }
    body = "IF session_user NOT IN ('__MILESTONE__', '__CASE__', '__TRANSFER__', '__EXPORT__', '__FAMILY__', '__OVERSIGHT__') THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; IF value_operation='AUTHORIZE_PROXY_MAJOR' THEN SELECT jsonb_build_object('actor_user_id',u.id,'proxy_grant_id',g.grant_id,'principal_member_id',g.principal_member_id,'proxy_member_id',g.proxy_member_id,'authorization_document_version_id',g.authorization_document_version_id,'witness_decision_id',g.witness_decision_id) INTO result FROM public.\"user\" u JOIN public.proxy_grant g ON g.grant_id=value_target JOIN identity.member p ON p.member_id=g.principal_member_id AND p.status='created' JOIN identity.member x ON x.member_id=g.proxy_member_id AND x.status='created' WHERE u.id=value_actor AND u.role::text=value_role AND u.status='active' AND value_role IN ('super_admin','sys_admin') AND g.status='ACTIVE' AND g.valid_from<=clock_timestamp() AND (g.valid_until IS NULL OR g.valid_until>clock_timestamp()) AND g.authorization_document_version_id IS NOT NULL AND g.witness_decision_id IS NOT NULL FOR SHARE OF u,g,p,x; RETURN result; END IF; IF value_operation='REVOKE_PROXY_MAJOR' THEN SELECT jsonb_build_object('actor_user_id',u.id,'authorization_id',a.authorization_id,'proxy_grant_id',a.proxy_grant_id,'principal_member_id',a.principal_member_id,'proxy_member_id',a.proxy_member_id,'authorization_document_version_id',a.authorization_document_version_id,'witness_decision_id',a.witness_decision_id,'permission_codes',a.permission_codes,'granted_by',a.granted_by,'valid_from',a.valid_from,'valid_until',a.valid_until,'revoked_at',a.revoked_at,'authorization_version',a.version) INTO result FROM public.\"user\" u JOIN public.proxy_major_authorization a ON a.authorization_id=value_target WHERE u.id=value_actor AND u.role::text=value_role AND u.status='active' AND value_role IN ('super_admin','sys_admin') AND NOT EXISTS(SELECT 1 FROM public.proxy_major_authorization newer WHERE newer.authorization_id=a.authorization_id AND newer.version>a.version) AND a.revoked_at IS NULL FOR SHARE OF u,a; RETURN result; END IF; IF value_operation='VALIDATE_CONTINUATION_CASE' THEN SELECT jsonb_build_object('new_service_case_id',c.case_id,'new_enrollment_id',c.enrollment_id,'subject_member_id',c.subject_member_id,'tenant_id',c.tenant_id,'tenant_public_id',ia.tenant_public_id,'case_status',COALESCE((SELECT le.to_status FROM public.service_case_lifecycle_event le WHERE le.service_case_id=c.case_id ORDER BY le.version DESC LIMIT 1),'PLAN_PENDING'),'service_ready',sr.readiness_status='SERVICE_READY','assignment_current',pa.status='ACCEPTED','therapist_current',tp.status='APPROVED_ACTIVE' AND tp.current_qualification_version_id IS NOT NULL AND tp.qualification_valid_until>=CURRENT_DATE AND tp.tenant_id=c.tenant_id AND tp.service_tags @> c.service_scope_tags,'consent_current',NOT EXISTS(SELECT required.document_type FROM unnest(ARRAY['USER_AGREEMENT','PRIVACY_POLICY','HEALTH_DATA_PROCESSING','INSTITUTION_SERVICE','NON_MEDICAL_RISK']) required(document_type) WHERE NOT EXISTS(SELECT 1 FROM public.consent_record cr JOIN public.consent_document_version cd ON cd.document_version_id=cr.document_version_id AND cd.status='PUBLISHED' WHERE cr.enrollment_id=c.enrollment_id AND cr.document_type=required.document_type AND cr.status='ACCEPTED'))) INTO result FROM public.\"user\" u JOIN public.service_case c ON c.case_id=value_target JOIN public.service_enrollment se ON se.enrollment_id=c.enrollment_id AND se.tenant_id=c.tenant_id AND se.subject_member_id=c.subject_member_id AND se.status='CASE_CREATED' AND se.service_case_id=c.case_id JOIN public.primary_therapist_assignment pa ON pa.assignment_id=c.assignment_id AND pa.enrollment_id=c.enrollment_id AND pa.tenant_id=c.tenant_id AND pa.subject_member_id=c.subject_member_id AND pa.therapist_id=c.primary_therapist_id AND pa.status='ACCEPTED' AND pa.service_case_id=c.case_id JOIN public.therapist_profile tp ON tp.therapist_id=c.primary_therapist_id JOIN public.institution_service_readiness sr ON sr.tenant_id=c.tenant_id JOIN public.institution_application ia ON ia.tenant_internal_id=c.tenant_id AND ia.status='APPROVED' WHERE u.id=value_actor AND u.role='org_admin' AND u.status='active' AND u.tenant_id=c.tenant_id AND sr.readiness_status='SERVICE_READY' AND tp.status='APPROVED_ACTIVE' AND tp.current_qualification_version_id IS NOT NULL AND tp.qualification_valid_until>=CURRENT_DATE AND tp.tenant_id=c.tenant_id AND tp.service_tags @> c.service_scope_tags AND NOT EXISTS(SELECT 1 FROM public.health_plan_version hp WHERE hp.service_case_id=c.case_id AND hp.status='ACTIVE') FOR SHARE OF u,c,se,pa,tp,sr,ia; RETURN result; END IF; IF value_operation IN ('START_REVIEW_TRANSFER','ACCEPT_TRANSFER','REJECT_TRANSFER','SOURCE_CLOSE_TRANSFER','CONFIRM_TRANSFER_SCOPE','CANCEL_TRANSFER','COORDINATE_TRANSFER_CLOSE','LINK_CONTINUATION_CASE','READ_TRANSFER','READ_HANDOFF') THEN SELECT jsonb_build_object('service_case_id',c.case_id,'subject_member_id',c.subject_member_id,'tenant_id',c.tenant_id,'tenant_public_id',source_ia.tenant_public_id,'case_status',COALESCE((SELECT le.to_status FROM public.service_case_lifecycle_event le WHERE le.service_case_id=c.case_id ORDER BY le.version DESC LIMIT 1),'PLAN_PENDING'),'service_case_version',c.version,'service_ready',source_sr.readiness_status='SERVICE_READY','primary_therapist_current',pa.status='ACCEPTED' AND tp.status='APPROVED_ACTIVE' AND tp.current_qualification_version_id IS NOT NULL AND tp.qualification_valid_until>=CURRENT_DATE AND tp.tenant_id=c.tenant_id AND tp.service_tags @> c.service_scope_tags,'consent_current',NOT EXISTS(SELECT required.document_type FROM unnest(ARRAY['USER_AGREEMENT','PRIVACY_POLICY','HEALTH_DATA_PROCESSING','INSTITUTION_SERVICE','NON_MEDICAL_RISK']) required(document_type) WHERE NOT EXISTS(SELECT 1 FROM public.consent_record cr JOIN public.consent_document_version cd ON cd.document_version_id=cr.document_version_id AND cd.status='PUBLISHED' WHERE cr.enrollment_id=c.enrollment_id AND cr.document_type=required.document_type AND cr.status='ACCEPTED')),'transfer_target_tenant_id',tr.target_tenant_id,'transfer_requested_scope',tr.requested_scope,'transfer_target_decision',tr.target_decision,'transfer_source_closure_status',tr.source_closure_status,'transfer_scope_confirmed_at',tr.scope_confirmed_at,'transfer_transferred_at',tr.transferred_at,'transfer_status',tr.status,'transfer_version',tr.version,'target_service_ready',target_sr.readiness_status='SERVICE_READY','target_service_tags',COALESCE(target_ia.draft_payload->'service_tags','[]'::jsonb),'handoff_id',h.handoff_id,'handoff_created_at',h.created_at,'handoff_status',h.status,'handoff_version',h.version) INTO result FROM public.service_transfer_request tr JOIN public.service_case c ON c.case_id=tr.source_service_case_id JOIN public.institution_application source_ia ON source_ia.tenant_public_id=tr.source_tenant_id AND source_ia.tenant_internal_id=c.tenant_id AND source_ia.status='APPROVED' JOIN public.institution_service_readiness source_sr ON source_sr.tenant_id=c.tenant_id JOIN public.primary_therapist_assignment pa ON pa.assignment_id=c.assignment_id JOIN public.therapist_profile tp ON tp.therapist_id=c.primary_therapist_id JOIN public.institution_application target_ia ON target_ia.tenant_public_id=tr.target_tenant_id AND target_ia.status='APPROVED' JOIN public.institution_service_readiness target_sr ON target_sr.tenant_id=target_ia.tenant_internal_id LEFT JOIN public.service_transfer_continuation_handoff h ON h.transfer_id=tr.transfer_id JOIN public.\"user\" u ON u.id=value_actor AND u.status='active' WHERE tr.transfer_id=value_target AND ((value_operation IN ('START_REVIEW_TRANSFER','ACCEPT_TRANSFER','REJECT_TRANSFER','LINK_CONTINUATION_CASE','READ_HANDOFF') AND u.role='org_admin' AND u.tenant_id=target_ia.tenant_internal_id) OR (value_operation='SOURCE_CLOSE_TRANSFER' AND u.role='org_admin' AND u.tenant_id=c.tenant_id) OR (value_operation='COORDINATE_TRANSFER_CLOSE' AND u.role::text=value_role AND value_role IN ('super_admin','sys_admin')) OR (value_operation IN ('CONFIRM_TRANSFER_SCOPE','CANCEL_TRANSFER') AND u.role='member' AND (EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=c.subject_member_id) OR public.slice7_proxy_major_current_v1(u.id,c.subject_member_id,'SERVICE_TRANSFER') IS NOT NULL)) OR (value_operation='READ_TRANSFER' AND ((u.role IN ('org_admin','org_operator') AND u.tenant_id IN (c.tenant_id,target_ia.tenant_internal_id)) OR u.role::text IN ('super_admin','sys_admin') OR (u.role='member' AND (EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=c.subject_member_id) OR EXISTS(SELECT 1 FROM identity.user_member_self_link l JOIN public.proxy_grant g ON g.proxy_member_id=l.member_id WHERE l.user_ref=u.id AND g.principal_member_id=c.subject_member_id AND g.status='ACTIVE')))))) AND (value_operation IN ('LINK_CONTINUATION_CASE','READ_HANDOFF','READ_TRANSFER') OR (source_sr.readiness_status='SERVICE_READY' AND pa.status='ACCEPTED' AND tp.status='APPROVED_ACTIVE' AND tp.current_qualification_version_id IS NOT NULL AND tp.qualification_valid_until>=CURRENT_DATE AND tp.tenant_id=c.tenant_id AND tp.service_tags @> c.service_scope_tags AND NOT EXISTS(SELECT required.document_type FROM unnest(ARRAY['USER_AGREEMENT','PRIVACY_POLICY','HEALTH_DATA_PROCESSING','INSTITUTION_SERVICE','NON_MEDICAL_RISK']) required(document_type) WHERE NOT EXISTS(SELECT 1 FROM public.consent_record cr JOIN public.consent_document_version cd ON cd.document_version_id=cr.document_version_id AND cd.status='PUBLISHED' WHERE cr.enrollment_id=c.enrollment_id AND cr.document_type=required.document_type AND cr.status='ACCEPTED')) AND COALESCE((SELECT le.to_status FROM public.service_case_lifecycle_event le WHERE le.service_case_id=c.case_id ORDER BY le.version DESC LIMIT 1),'PLAN_PENDING') NOT IN ('COMPLETED','WITHDRAWN_BY_USER','TERMINATED_BY_INSTITUTION','TRANSFERRED','UNABLE_TO_CONTACT','SAFETY_TERMINATED'))) FOR UPDATE OF tr,c; RETURN result; END IF; IF value_operation='CREATE_TRANSFER' THEN SELECT jsonb_build_object('service_case_id',c.case_id,'subject_member_id',c.subject_member_id,'tenant_id',c.tenant_id,'tenant_public_id',ia.tenant_public_id,'case_status',COALESCE((SELECT le.to_status FROM public.service_case_lifecycle_event le WHERE le.service_case_id=c.case_id ORDER BY le.version DESC LIMIT 1),'PLAN_PENDING'),'service_case_version',c.version,'service_ready',sr.readiness_status='SERVICE_READY','primary_therapist_current',pa.status='ACCEPTED' AND tp.status='APPROVED_ACTIVE' AND tp.current_qualification_version_id IS NOT NULL AND tp.qualification_valid_until>=CURRENT_DATE AND tp.tenant_id=c.tenant_id AND tp.service_tags @> c.service_scope_tags,'consent_current',true) INTO result FROM public.service_case c JOIN public.institution_application ia ON ia.tenant_internal_id=c.tenant_id AND ia.status='APPROVED' JOIN public.institution_service_readiness sr ON sr.tenant_id=c.tenant_id JOIN public.primary_therapist_assignment pa ON pa.assignment_id=c.assignment_id JOIN public.therapist_profile tp ON tp.therapist_id=c.primary_therapist_id JOIN public.\"user\" u ON u.id=value_actor AND u.role='member' AND u.status='active' WHERE c.case_id=value_target AND sr.readiness_status='SERVICE_READY' AND pa.status='ACCEPTED' AND tp.status='APPROVED_ACTIVE' AND tp.current_qualification_version_id IS NOT NULL AND tp.qualification_valid_until>=CURRENT_DATE AND tp.tenant_id=c.tenant_id AND tp.service_tags @> c.service_scope_tags AND COALESCE((SELECT le.to_status FROM public.service_case_lifecycle_event le WHERE le.service_case_id=c.case_id ORDER BY le.version DESC LIMIT 1),'PLAN_PENDING') NOT IN ('COMPLETED','WITHDRAWN_BY_USER','TERMINATED_BY_INSTITUTION','TRANSFERRED','UNABLE_TO_CONTACT','SAFETY_TERMINATED') AND (EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=c.subject_member_id) OR public.slice7_proxy_major_current_v1(u.id,c.subject_member_id,'SERVICE_TRANSFER') IS NOT NULL) FOR UPDATE OF c; RETURN result; END IF; IF value_operation='CREATE_EXPORT_FOR_SUBJECT' THEN SELECT CASE WHEN EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=m.member_id) OR public.slice7_proxy_major_current_v1(u.id,m.member_id,'PERSONAL_DATA_EXPORT') IS NOT NULL THEN jsonb_build_object('subject_member_id',m.member_id) ELSE jsonb_build_object('error_code','PROXY_PERMISSION_FORBIDDEN') END INTO result FROM public.\"user\" u JOIN identity.member m ON m.member_id=value_target AND m.status='created' WHERE u.id=value_actor AND u.role='member' AND u.status='active' AND u.tenant_id IS NULL AND (EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=m.member_id) OR EXISTS(SELECT 1 FROM identity.user_member_self_link l JOIN public.proxy_grant g ON g.proxy_member_id=l.member_id WHERE l.user_ref=u.id AND g.principal_member_id=m.member_id AND g.status='ACTIVE' AND g.valid_from<=clock_timestamp() AND (g.valid_until IS NULL OR g.valid_until>clock_timestamp()) AND g.revoked_at IS NULL)) FOR SHARE OF u,m; RETURN result; END IF; IF value_operation='CREATE_EXPORT' THEN SELECT jsonb_build_object('subject_member_id',l.member_id) INTO result FROM public.\"user\" u JOIN identity.user_member_self_link l ON l.user_ref=u.id JOIN identity.member m ON m.member_id=l.member_id AND m.status='created' WHERE u.id=value_actor AND u.role='member' AND u.status='active' AND u.tenant_id IS NULL FOR SHARE OF u,l,m; RETURN result; END IF; IF value_operation IN ('CANCEL_EXPORT','EXPORT_DOWNLOAD_ACCESS','READ_EXPORT') THEN IF value_role='member' THEN SELECT CASE WHEN EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=e.subject_member_id) OR public.slice7_proxy_major_current_v1(u.id,e.subject_member_id,'PERSONAL_DATA_EXPORT') IS NOT NULL THEN jsonb_build_object('subject_member_id',e.subject_member_id,'export_requested_scope',e.requested_scope,'export_status',e.status,'export_requested_at',e.requested_at,'export_ready_at',e.ready_at,'export_expires_at',e.expires_at,'export_downloaded_at',e.downloaded_at,'export_version',e.version,'private_file_id',a.private_file_id,'artifact_digest',encode(a.artifact_digest,'hex')) ELSE jsonb_build_object('error_code','PROXY_PERMISSION_FORBIDDEN') END INTO result FROM public.\"user\" u JOIN public.personal_data_export_request e ON e.export_id=value_target JOIN identity.member m ON m.member_id=e.subject_member_id AND m.status='created' LEFT JOIN public.personal_data_export_artifact a ON a.export_id=e.export_id WHERE u.id=value_actor AND u.role='member' AND u.status='active' AND u.tenant_id IS NULL AND (EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=e.subject_member_id) OR EXISTS(SELECT 1 FROM identity.user_member_self_link l JOIN public.proxy_grant g ON g.proxy_member_id=l.member_id WHERE l.user_ref=u.id AND g.principal_member_id=e.subject_member_id AND g.status='ACTIVE' AND g.valid_from<=clock_timestamp() AND (g.valid_until IS NULL OR g.valid_until>clock_timestamp()) AND g.revoked_at IS NULL)) FOR SHARE OF u,e,m; ELSIF value_role IN ('super_admin','sys_admin') AND value_operation='READ_EXPORT' THEN SELECT jsonb_build_object('subject_member_id',e.subject_member_id,'export_requested_scope',e.requested_scope,'export_status',e.status,'export_requested_at',e.requested_at,'export_ready_at',e.ready_at,'export_expires_at',e.expires_at,'export_downloaded_at',e.downloaded_at,'export_version',e.version) INTO result FROM public.\"user\" u JOIN public.personal_data_export_request e ON e.export_id=value_target WHERE u.id=value_actor AND u.role::text=value_role AND u.status='active' FOR SHARE OF u; END IF; RETURN result; END IF; PERFORM 1 FROM public.\"user\" u WHERE u.id=value_actor AND u.role::text=value_role AND u.status='active' FOR SHARE; IF NOT FOUND THEN RETURN NULL; END IF; PERFORM pg_advisory_xact_lock(hashtextextended(value_target::text,7)); SELECT jsonb_build_object('service_case_id',c.case_id,'subject_member_id',c.subject_member_id,'tenant_id',c.tenant_id,'tenant_public_id',ia.tenant_public_id,'case_status',COALESCE((SELECT e.to_status FROM public.service_case_lifecycle_event e WHERE e.service_case_id=c.case_id ORDER BY e.version DESC LIMIT 1),CASE WHEN sc.schedule_id IS NULL THEN 'PLAN_PENDING' ELSE 'ACTIVE' END),'service_case_version',c.version,'service_ready',sr.readiness_status='SERVICE_READY','primary_therapist_current',pa.status='ACCEPTED' AND current_tp.status='APPROVED_ACTIVE' AND current_tp.current_qualification_version_id IS NOT NULL AND current_tp.qualification_valid_until>=CURRENT_DATE AND current_tp.tenant_id=c.tenant_id AND current_tp.service_tags @> c.service_scope_tags,'consent_current',NOT EXISTS(SELECT required.document_type FROM unnest(ARRAY['USER_AGREEMENT','PRIVACY_POLICY','HEALTH_DATA_PROCESSING','INSTITUTION_SERVICE','NON_MEDICAL_RISK']) required(document_type) WHERE NOT EXISTS(SELECT 1 FROM public.consent_record cr JOIN public.consent_document_version cd ON cd.document_version_id=cr.document_version_id AND cd.status='PUBLISHED' WHERE cr.enrollment_id=c.enrollment_id AND cr.document_type=required.document_type AND cr.status='ACCEPTED')),'high_risk_count',(SELECT count(*) FROM public.high_risk_task h WHERE h.service_case_id=c.case_id AND h.status IN ('OPEN','CLAIMED','ESCALATED')),'active_plan_id',hp.plan_id,'plan_activated_at',hp.updated_at,'schedule_id',sc.schedule_id,'cycle_anchor_at',sc.cycle_anchor_at,'schedule_version',sc.version_no,'milestone_id',m.milestone_id,'milestone_code',m.code,'window_start',m.window_start,'window_end',m.window_end,'milestone_status',m.status,'milestone_version',m.version,'milestones',COALESCE((SELECT jsonb_agg(jsonb_build_object('milestone_id',all_m.milestone_id,'service_case_id',all_m.service_case_id,'code',all_m.code,'window_start',all_m.window_start,'window_end',all_m.window_end,'status',all_m.status,'completed_at',all_m.completed_at,'record_summary',all_m.record_summary,'version',all_m.version) ORDER BY all_m.code) FROM public.service_milestone all_m WHERE all_m.service_case_id=c.case_id),'[]'::jsonb),'latest_assessment_id',(SELECT h.assessment_id FROM public.health_assessment h WHERE h.service_case_id=c.case_id AND h.status='COMPLETED' ORDER BY h.sequence_no DESC LIMIT 1),'closing_assessment_complete',EXISTS(SELECT 1 FROM public.service_closing_assessment ca WHERE ca.service_case_id=c.case_id),'summary_complete',EXISTS(SELECT 1 FROM public.service_summary sx WHERE sx.service_case_id=c.case_id),'summary_acknowledged',EXISTS(SELECT 1 FROM public.service_summary sx JOIN public.service_summary_acknowledgement ack ON ack.summary_id=sx.summary_id WHERE sx.service_case_id=c.case_id),'transfer_target_tenant_id',tr.target_tenant_id,'transfer_requested_scope',tr.requested_scope,'transfer_target_decision',tr.target_decision,'transfer_source_closure_status',tr.source_closure_status,'transfer_scope_confirmed_at',tr.scope_confirmed_at,'transfer_transferred_at',tr.transferred_at,'transfer_status',tr.status,'transfer_version',tr.version,'summary_assessment_id',sm.assessment_id,'summary_content',sm.content,'summary_created_at',sm.created_at,'summary_version',sm.version) INTO result FROM public.service_case c JOIN public.institution_service_readiness sr ON sr.tenant_id=c.tenant_id JOIN public.institution_application ia ON ia.tenant_internal_id=c.tenant_id AND ia.status='APPROVED' LEFT JOIN public.primary_therapist_assignment pa ON pa.assignment_id=c.assignment_id LEFT JOIN public.therapist_profile current_tp ON current_tp.therapist_id=c.primary_therapist_id LEFT JOIN public.health_plan_version hp ON hp.service_case_id=c.case_id AND hp.status='ACTIVE' LEFT JOIN public.service_cycle_schedule sc ON sc.service_case_id=c.case_id AND sc.is_current LEFT JOIN public.service_milestone m ON m.milestone_id=value_target AND m.service_case_id=c.case_id LEFT JOIN public.service_transfer_request tr ON tr.transfer_id=value_target AND tr.source_service_case_id=c.case_id LEFT JOIN public.service_summary sm ON sm.summary_id=value_target AND sm.service_case_id=c.case_id WHERE (c.case_id=value_target OR m.milestone_id IS NOT NULL OR tr.transfer_id IS NOT NULL OR sm.summary_id IS NOT NULL) AND (value_tenant IS NULL OR c.tenant_id=value_tenant OR value_role IN ('super_admin','sys_admin') OR (tr.transfer_id IS NOT NULL AND EXISTS(SELECT 1 FROM public.institution_application target_ia WHERE target_ia.tenant_public_id=tr.target_tenant_id AND target_ia.tenant_internal_id=value_tenant AND target_ia.status='APPROVED'))) AND (((value_role IN ('org_admin','org_operator')) AND EXISTS(SELECT 1 FROM public.\"user\" u WHERE u.id=value_actor AND u.role::text=value_role AND u.status='active' AND (u.tenant_id=c.tenant_id OR EXISTS(SELECT 1 FROM public.institution_application target_ia WHERE tr.transfer_id IS NOT NULL AND target_ia.tenant_public_id=tr.target_tenant_id AND target_ia.tenant_internal_id=u.tenant_id AND target_ia.status='APPROVED')))) OR (value_role='therapist' AND EXISTS(SELECT 1 FROM public.\"user\" u JOIN public.therapist_profile tp ON tp.user_id=u.id WHERE u.id=value_actor AND u.role='therapist' AND u.status='active' AND u.tenant_id=c.tenant_id AND tp.therapist_id=c.primary_therapist_id AND tp.status='APPROVED_ACTIVE' AND tp.current_qualification_version_id IS NOT NULL AND tp.qualification_valid_until>=CURRENT_DATE AND pa.status='ACCEPTED')) OR (value_role='member' AND EXISTS(SELECT 1 FROM public.\"user\" u JOIN identity.user_member_self_link l ON l.user_ref=u.id WHERE u.id=value_actor AND u.role='member' AND u.status='active' AND (l.member_id=c.subject_member_id OR (value_operation='WITHDRAW_CASE' AND public.slice7_proxy_major_current_v1(u.id,c.subject_member_id,'SERVICE_WITHDRAW') IS NOT NULL)))) OR (value_role IN ('super_admin','sys_admin') AND EXISTS(SELECT 1 FROM public.\"user\" u WHERE u.id=value_actor AND u.role::text=value_role AND u.status='active'))) FOR UPDATE OF c; IF result IS NOT NULL AND value_operation NOT LIKE 'READ_%' AND ((result->>'service_ready')::boolean IS NOT TRUE OR (result->>'primary_therapist_current')::boolean IS NOT TRUE OR (result->>'consent_current')::boolean IS NOT TRUE OR result->>'case_status' IN ('COMPLETED','WITHDRAWN_BY_USER','TERMINATED_BY_INSTITUTION','TRANSFERRED','UNABLE_TO_CONTACT','SAFETY_TERMINATED')) THEN RETURN NULL; END IF; RETURN result;"
    for marker, role in replacements.items():
        body = body.replace(marker, role)
    op.execute(sa.text(
        "CREATE OR REPLACE FUNCTION public.slice7_authority_v1(value_operation VARCHAR,value_target UUID,value_actor BIGINT,value_role VARCHAR,value_tenant BIGINT) "
        "RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER "
        "SET search_path=pg_catalog,pg_temp AS $fn$ "
        "DECLARE result JSONB; " + "BEGIN " + body + " END $fn$"
    ))
    body = "IF session_user NOT IN ('__MILESTONE__', '__CASE__', '__TRANSFER__', '__EXPORT__') THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; operation_name:=value->>'operation'; IF operation_name IS NULL OR value->>'request_digest' !~ '^[0-9a-f]{64}$' OR value->>'expected_response_digest' !~ '^[0-9a-f]{64}$' OR jsonb_typeof(value->'response')<>'object' THEN RAISE EXCEPTION 'INVALID_REQUEST'; END IF; IF (operation_name IN ('ACTIVATE_CYCLE','COMPLETE_MILESTONE') AND session_user<>'__MILESTONE__') OR (operation_name='MARK_MISSED' AND session_user<>'__EXPORT__') OR (operation_name IN ('PAUSE_CASE','RESUME_CASE','WITHDRAW_CASE','TERMINATE_CASE','UNABLE_TO_CONTACT','SAFETY_TERMINATE','CREATE_CLOSING_ASSESSMENT','CREATE_SUMMARY','ACK_SUMMARY','COMPLETE_CASE') AND session_user<>'__CASE__') OR (operation_name IN ('CREATE_TRANSFER','CANCEL_TRANSFER','CONFIRM_TRANSFER_SCOPE','START_REVIEW_TRANSFER','ACCEPT_TRANSFER','REJECT_TRANSFER','SOURCE_CLOSE_TRANSFER','COORDINATE_TRANSFER_CLOSE','LINK_CONTINUATION_CASE','AUTHORIZE_PROXY_MAJOR','REVOKE_PROXY_MAJOR','CREATE_EXPORT','CANCEL_EXPORT','EXPORT_DOWNLOAD_ACCESS') AND session_user<>'__TRANSFER__') THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; SELECT request_digest,response_json INTO stored_digest,stored_response FROM public.service_fulfillment_receipt WHERE actor_scope=value->>'actor_scope' AND operation=operation_name AND idempotency_key=value->>'idempotency_key' FOR SHARE; IF stored_response IS NOT NULL THEN IF stored_digest<>decode(value->>'request_digest','hex') THEN RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT'; END IF; RETURN stored_response; END IF; PERFORM pg_advisory_xact_lock(hashtextextended(value->>'target_id',7)); IF operation_name<>'MARK_MISSED' THEN current_authority:=public.slice7_authority_v1(COALESCE(value->>'authority_operation',operation_name),COALESCE((value->>'authority_target_id')::uuid,(value->>'target_id')::uuid),(value->>'actor_user_id')::bigint,value->>'actor_role',(value->>'actor_tenant_id')::bigint); IF current_authority IS NULL OR current_authority<>value->'authority' THEN RAISE EXCEPTION 'CURRENTNESS_FORBIDDEN'; END IF; END IF; IF operation_name='MARK_MISSED' THEN UPDATE public.service_milestone SET status='MISSED',version=version+1 WHERE milestone_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND status IN ('PENDING','DUE') AND window_end<((value->>'occurred_at')::timestamptz AT TIME ZONE 'Asia/Shanghai')::date RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.service_milestone_revision(revision_id,milestone_id,version_no,status,body,body_digest,created_at) VALUES ((value->>'operation_id')::uuid,(value->>'target_id')::uuid,affected,'MISSED',jsonb_build_object('reason_code','MILESTONE_MISSED','worker_id',value->>'worker_id'),decode(value->>'request_digest','hex'),(value->>'occurred_at')::timestamptz); ELSIF operation_name='ACTIVATE_CYCLE' THEN INSERT INTO public.service_cycle_schedule(schedule_id,service_case_id,subject_member_id,tenant_id,active_plan_id,version_no,cycle_anchor_at,is_current,created_at,version) VALUES ((value->>'operation_id')::uuid,(value->>'target_id')::uuid,(current_authority->>'subject_member_id')::uuid,(current_authority->>'tenant_id')::bigint,(value->'response'->>'active_plan_id')::uuid,1,(value->>'activated_at')::timestamptz,true,(value->>'occurred_at')::timestamptz,1); INSERT INTO public.service_milestone(milestone_id,schedule_id,service_case_id,code,window_start,window_end,status,completed_at,record_summary,version) SELECT (value->'milestone_ids'->>code)::uuid,(value->>'operation_id')::uuid,(value->>'target_id')::uuid,code,(value->'windows'->code->>'start')::date,(value->'windows'->code->>'end')::date,CASE code WHEN 'D0' THEN 'DUE' ELSE 'PENDING' END,NULL,NULL,1 FROM unnest(ARRAY['D0','D7','D14','D21','D28']) code; ELSIF operation_name='COMPLETE_MILESTONE' THEN UPDATE public.service_milestone SET status='COMPLETED',completed_at=(value->>'occurred_at')::timestamptz,record_summary=value->'record_summary',version=version+1 WHERE milestone_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND status IN ('PENDING','DUE') RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.service_milestone_revision(revision_id,milestone_id,version_no,status,body,body_digest,created_at) VALUES ((value->>'operation_id')::uuid,(value->>'target_id')::uuid,affected,'COMPLETED',jsonb_build_object('record_summary',value->'record_summary','evidence_refs',value->'evidence_refs'),decode(value->>'request_digest','hex'),(value->>'occurred_at')::timestamptz); IF (SELECT count(*) FROM public.service_milestone m WHERE m.service_case_id=(current_authority->>'service_case_id')::uuid)=5 AND NOT EXISTS(SELECT 1 FROM public.service_milestone m WHERE m.service_case_id=(current_authority->>'service_case_id')::uuid AND m.status<>'COMPLETED') THEN UPDATE public.service_case SET version=version+1 WHERE case_id=(current_authority->>'service_case_id')::uuid AND version=(current_authority->>'service_case_version')::bigint RETURNING version INTO case_version; IF case_version IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.service_case_lifecycle_event(lifecycle_event_id,service_case_id,from_status,to_status,reason_code,occurred_at,version) VALUES ((value->>'lifecycle_event_id')::uuid,(current_authority->>'service_case_id')::uuid,'ACTIVE','CLOSING','MILESTONES_COMPLETED',(value->>'occurred_at')::timestamptz,case_version); END IF; ELSIF operation_name IN ('PAUSE_CASE','RESUME_CASE','WITHDRAW_CASE','TERMINATE_CASE','UNABLE_TO_CONTACT','SAFETY_TERMINATE','COMPLETE_CASE') THEN UPDATE public.service_case SET version=version+1 WHERE case_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.service_case_lifecycle_event(lifecycle_event_id,service_case_id,from_status,to_status,reason_code,occurred_at,version) VALUES ((value->>'operation_id')::uuid,(value->>'target_id')::uuid,COALESCE(value->'authority'->>'case_status','ACTIVE'),value->'response'->>'lifecycle_status',COALESCE(value->>'reason_code',operation_name),(value->>'occurred_at')::timestamptz,affected); ELSIF operation_name='CREATE_CLOSING_ASSESSMENT' THEN UPDATE public.service_case SET version=version+1 WHERE case_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND current_authority->>'case_status'='CLOSING' RETURNING version INTO case_version; IF case_version IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.service_closing_assessment(closing_assessment_id,service_case_id,assessment_id,version_no,final_retest_evidence,created_at,version) SELECT (value->>'operation_id')::uuid,(value->>'target_id')::uuid,(value->>'assessment_id')::uuid,COALESCE(max(version_no),0)+1,value->'final_retest_evidence',(value->>'occurred_at')::timestamptz,1 FROM public.service_closing_assessment WHERE service_case_id=(value->>'target_id')::uuid; ELSIF operation_name='CREATE_SUMMARY' THEN UPDATE public.service_case SET version=version+1 WHERE case_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND current_authority->>'case_status'='CLOSING' RETURNING version INTO case_version; IF case_version IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.service_summary(summary_id,service_case_id,assessment_id,version_no,content,content_digest,created_at,version) SELECT (value->>'operation_id')::uuid,(value->>'target_id')::uuid,(value->>'assessment_id')::uuid,COALESCE(max(version_no),0)+1,jsonb_build_object('final_retest_evidence',value->'final_retest_evidence','milestone_outcomes',value->'milestone_outcomes','safety_follow_up',value->'safety_follow_up','next_step',value->'next_step'),decode(value->>'request_digest','hex'),(value->>'occurred_at')::timestamptz,1 FROM public.service_summary WHERE service_case_id=(value->>'target_id')::uuid; ELSIF operation_name='ACK_SUMMARY' THEN PERFORM 1 FROM public.service_summary s WHERE s.summary_id=(value->>'target_id')::uuid AND s.version=(value->>'expected_version')::bigint FOR UPDATE; IF NOT FOUND THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.service_summary_acknowledgement(acknowledgement_id,summary_id,subject_member_id,actor_user_id,proxy_grant_id,viewed_at,version) VALUES ((value->>'operation_id')::uuid,(value->>'target_id')::uuid,(current_authority->>'subject_member_id')::uuid,(value->>'actor_user_id')::bigint,NULL,(value->>'occurred_at')::timestamptz,1); readiness:=public.slice7_closing_readiness_v1((current_authority->>'service_case_id')::uuid); IF readiness<>'READY_TO_CLOSE' THEN RAISE EXCEPTION 'CLOSURE_PREREQUISITE_MISSING'; END IF; UPDATE public.service_case SET version=version+1 WHERE case_id=(current_authority->>'service_case_id')::uuid AND version=(current_authority->>'service_case_version')::bigint AND current_authority->>'case_status'='CLOSING' RETURNING version INTO case_version; IF case_version IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.service_case_lifecycle_event(lifecycle_event_id,service_case_id,from_status,to_status,reason_code,occurred_at,version) VALUES ((value->>'lifecycle_event_id')::uuid,(current_authority->>'service_case_id')::uuid,'CLOSING','COMPLETED','SUMMARY_ACKNOWLEDGED',(value->>'occurred_at')::timestamptz,case_version); ELSIF operation_name='CREATE_TRANSFER' THEN INSERT INTO public.service_transfer_request(transfer_id,source_service_case_id,source_tenant_id,target_tenant_id,subject_member_id,status,requested_scope,target_decision,source_closure_status,scope_confirmed_at,transferred_at,created_at,version) VALUES ((value->>'operation_id')::uuid,(value->>'target_id')::uuid,(current_authority->>'tenant_public_id')::uuid,(value->>'target_tenant_id')::uuid,(current_authority->>'subject_member_id')::uuid,'REQUESTED_BY_USER',value->'requested_scope',NULL,NULL,NULL,NULL,(value->>'occurred_at')::timestamptz,1); INSERT INTO public.service_transfer_scope_revision(scope_revision_id,transfer_id,version_no,scope,scope_digest,created_at) VALUES ((value->>'receipt_id')::uuid,(value->>'operation_id')::uuid,1,value->'requested_scope',decode(value->>'request_digest','hex'),(value->>'occurred_at')::timestamptz); ELSIF operation_name='START_REVIEW_TRANSFER' THEN UPDATE public.service_transfer_request SET status='NEW_INSTITUTION_REVIEWING',version=version+1 WHERE transfer_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND status='REQUESTED_BY_USER' RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'TRANSFER_STATE_CONFLICT'; END IF; ELSIF operation_name IN ('ACCEPT_TRANSFER','REJECT_TRANSFER') THEN IF operation_name='ACCEPT_TRANSFER' AND ((current_authority->>'target_service_ready')::boolean IS NOT TRUE OR NOT (current_authority->'target_service_tags' ? (value->>'service_label'))) THEN RAISE EXCEPTION 'CURRENTNESS_FORBIDDEN'; END IF; UPDATE public.service_transfer_request SET status=CASE operation_name WHEN 'ACCEPT_TRANSFER' THEN 'ACCEPTED' ELSE 'REJECTED_BY_NEW_INSTITUTION' END,target_decision=value->>'reason_code',version=version+1 WHERE transfer_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND status='NEW_INSTITUTION_REVIEWING' RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'TRANSFER_STATE_CONFLICT'; END IF; ELSIF operation_name='SOURCE_CLOSE_TRANSFER' THEN UPDATE public.service_transfer_request SET status='OLD_INSTITUTION_CLOSING',source_closure_status=value->>'risk_disposition',version=version+1 WHERE transfer_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND status='ACCEPTED' RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'TRANSFER_STATE_CONFLICT'; END IF; ELSIF operation_name='CONFIRM_TRANSFER_SCOPE' THEN UPDATE public.service_transfer_request SET status='USER_SCOPE_CONFIRMED',requested_scope=value->'exact_scope',scope_confirmed_at=(value->>'occurred_at')::timestamptz,version=version+1 WHERE transfer_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND status='OLD_INSTITUTION_CLOSING' AND requested_scope=value->'exact_scope' RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'TRANSFER_STATE_CONFLICT'; END IF; INSERT INTO public.service_transfer_scope_revision(scope_revision_id,transfer_id,version_no,scope,scope_digest,created_at) VALUES ((value->>'operation_id')::uuid,(value->>'target_id')::uuid,affected,value->'exact_scope',decode(value->>'request_digest','hex'),(value->>'occurred_at')::timestamptz); ELSIF operation_name='CANCEL_TRANSFER' THEN UPDATE public.service_transfer_request SET status='CANCELLED_BY_USER',version=version+1 WHERE transfer_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND status IN ('REQUESTED_BY_USER','NEW_INSTITUTION_REVIEWING') RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'TRANSFER_STATE_CONFLICT'; END IF; ELSIF operation_name='COORDINATE_TRANSFER_CLOSE' THEN UPDATE public.service_transfer_request SET status='TRANSFERRED',transferred_at=(value->>'occurred_at')::timestamptz,version=version+1 WHERE transfer_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND status='USER_SCOPE_CONFIRMED' RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'TRANSFER_STATE_CONFLICT'; END IF; UPDATE public.service_case SET version=version+1 WHERE case_id=(current_authority->>'service_case_id')::uuid AND version=(current_authority->>'service_case_version')::bigint RETURNING version INTO case_version; IF case_version IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; UPDATE public.service_enrollment SET status='REVOKED',updated_at=(value->>'occurred_at')::timestamptz,version=version+1 WHERE enrollment_id=(SELECT c.enrollment_id FROM public.service_case c WHERE c.case_id=(current_authority->>'service_case_id')::uuid) AND status='CASE_CREATED'; GET DIAGNOSTICS changed=ROW_COUNT; IF changed<>1 THEN RAISE EXCEPTION 'CURRENTNESS_FORBIDDEN'; END IF; INSERT INTO public.service_case_lifecycle_event(lifecycle_event_id,service_case_id,from_status,to_status,reason_code,occurred_at,version) VALUES ((value->>'lifecycle_event_id')::uuid,(current_authority->>'service_case_id')::uuid,current_authority->>'case_status','TRANSFERRED','SERVICE_TRANSFERRED',(value->>'occurred_at')::timestamptz,case_version); INSERT INTO public.service_transfer_continuation_handoff(handoff_id,transfer_id,source_service_case_id,source_tenant_id,target_tenant_id,subject_member_id,authorized_scope,scope_digest,status,created_at,linked_enrollment_id,linked_service_case_id,linked_at,version) VALUES ((value->>'handoff_id')::uuid,(value->>'target_id')::uuid,(current_authority->>'service_case_id')::uuid,(current_authority->>'tenant_public_id')::uuid,(current_authority->>'transfer_target_tenant_id')::uuid,(current_authority->>'subject_member_id')::uuid,current_authority->'transfer_requested_scope',decode(value->>'handoff_scope_digest','hex'),'PENDING_TARGET_ENROLLMENT',(value->>'occurred_at')::timestamptz,NULL,NULL,NULL,1); ELSIF operation_name='LINK_CONTINUATION_CASE' THEN PERFORM 1 FROM public.service_transfer_continuation_handoff h JOIN public.service_transfer_request tr ON tr.transfer_id=h.transfer_id AND tr.status='TRANSFERRED' JOIN public.institution_application target_ia ON target_ia.tenant_public_id=h.target_tenant_id AND target_ia.status='APPROVED' JOIN public.service_case c ON c.case_id=(value->>'new_service_case_id')::uuid AND c.enrollment_id=(value->>'new_enrollment_id')::uuid AND c.subject_member_id=h.subject_member_id AND c.tenant_id=target_ia.tenant_internal_id JOIN public.service_enrollment se ON se.enrollment_id=c.enrollment_id AND se.status='CASE_CREATED' AND se.service_case_id=c.case_id JOIN public.primary_therapist_assignment pa ON pa.assignment_id=c.assignment_id AND pa.enrollment_id=c.enrollment_id AND pa.status='ACCEPTED' AND pa.service_case_id=c.case_id JOIN public.therapist_profile tp ON tp.therapist_id=c.primary_therapist_id AND tp.tenant_id=c.tenant_id AND tp.status='APPROVED_ACTIVE' AND tp.current_qualification_version_id IS NOT NULL AND tp.qualification_valid_until>=CURRENT_DATE AND tp.service_tags @> c.service_scope_tags JOIN public.institution_service_readiness sr ON sr.tenant_id=c.tenant_id AND sr.readiness_status='SERVICE_READY' WHERE h.transfer_id=(value->>'target_id')::uuid AND h.version=(value->>'expected_version')::bigint AND h.status='PENDING_TARGET_ENROLLMENT' AND NOT EXISTS(SELECT 1 FROM public.health_plan_version hp WHERE hp.service_case_id=c.case_id AND hp.status='ACTIVE') AND NOT EXISTS(SELECT required.document_type FROM unnest(ARRAY['USER_AGREEMENT','PRIVACY_POLICY','HEALTH_DATA_PROCESSING','INSTITUTION_SERVICE','NON_MEDICAL_RISK']) required(document_type) WHERE NOT EXISTS(SELECT 1 FROM public.consent_record cr JOIN public.consent_document_version cd ON cd.document_version_id=cr.document_version_id AND cd.status='PUBLISHED' WHERE cr.enrollment_id=c.enrollment_id AND cr.document_type=required.document_type AND cr.status='ACCEPTED')) FOR UPDATE OF h,c,se,pa,tp,sr; IF NOT FOUND THEN RAISE EXCEPTION 'CURRENTNESS_FORBIDDEN'; END IF; UPDATE public.service_transfer_continuation_handoff SET status='CONTINUATION_CASE_LINKED',linked_enrollment_id=(value->>'new_enrollment_id')::uuid,linked_service_case_id=(value->>'new_service_case_id')::uuid,linked_at=(value->>'occurred_at')::timestamptz,version=version+1 WHERE transfer_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND status='PENDING_TARGET_ENROLLMENT' RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'TRANSFER_STATE_CONFLICT'; END IF; ELSIF operation_name='AUTHORIZE_PROXY_MAJOR' THEN IF current_authority->>'authorization_document_version_id'<>value->>'authorization_document_version_id' OR current_authority->>'witness_decision_id'<>value->>'witness_decision_id' OR jsonb_typeof(value->'permission_codes')<>'array' OR jsonb_array_length(value->'permission_codes') NOT BETWEEN 1 AND 4 OR NOT value->'permission_codes' <@ '[\"PLAN_DECISION\",\"SERVICE_WITHDRAW\",\"SERVICE_TRANSFER\",\"PERSONAL_DATA_EXPORT\"]'::jsonb OR (value->>'valid_until') IS NOT NULL AND (value->>'valid_until')::timestamptz<=(value->>'occurred_at')::timestamptz OR EXISTS(SELECT 1 FROM public.proxy_major_authorization a WHERE a.proxy_grant_id=(value->>'target_id')::uuid AND NOT EXISTS(SELECT 1 FROM public.proxy_major_authorization newer WHERE newer.authorization_id=a.authorization_id AND newer.version>a.version) AND a.revoked_at IS NULL) THEN RAISE EXCEPTION 'CURRENTNESS_FORBIDDEN'; END IF; INSERT INTO public.proxy_major_authorization(authorization_revision_id,authorization_id,proxy_grant_id,principal_member_id,proxy_member_id,authorization_document_version_id,witness_decision_id,permission_codes,granted_by,valid_from,valid_until,revoked_at,version) VALUES ((value->>'operation_id')::uuid,(value->>'operation_id')::uuid,(value->>'target_id')::uuid,(current_authority->>'principal_member_id')::uuid,(current_authority->>'proxy_member_id')::uuid,(value->>'authorization_document_version_id')::uuid,(value->>'witness_decision_id')::uuid,value->'permission_codes',(value->>'actor_user_id')::bigint,(value->>'occurred_at')::timestamptz,(value->>'valid_until')::timestamptz,NULL,1); ELSIF operation_name='REVOKE_PROXY_MAJOR' THEN IF (current_authority->>'authorization_version')::bigint<>(value->>'expected_version')::bigint THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.proxy_major_authorization(authorization_revision_id,authorization_id,proxy_grant_id,principal_member_id,proxy_member_id,authorization_document_version_id,witness_decision_id,permission_codes,granted_by,valid_from,valid_until,revoked_at,version) VALUES ((value->>'operation_id')::uuid,(value->>'target_id')::uuid,(current_authority->>'proxy_grant_id')::uuid,(current_authority->>'principal_member_id')::uuid,(current_authority->>'proxy_member_id')::uuid,(current_authority->>'authorization_document_version_id')::uuid,(current_authority->>'witness_decision_id')::uuid,current_authority->'permission_codes',(current_authority->>'granted_by')::bigint,(current_authority->>'valid_from')::timestamptz,(current_authority->>'valid_until')::timestamptz,(value->>'occurred_at')::timestamptz,(current_authority->>'authorization_version')::bigint+1); ELSIF operation_name='CREATE_EXPORT' THEN INSERT INTO public.personal_data_export_request(export_id,subject_member_id,requested_scope,status,requested_by,requested_at,ready_at,expires_at,downloaded_at,version) VALUES ((value->>'operation_id')::uuid,(current_authority->>'subject_member_id')::uuid,value->'requested_scope','REQUESTED',(value->>'actor_user_id')::bigint,(value->>'occurred_at')::timestamptz,NULL,NULL,NULL,1); ELSIF operation_name='CANCEL_EXPORT' THEN UPDATE public.personal_data_export_request SET status='CANCELLED',lease_owner=NULL,lease_until=NULL,version=version+1 WHERE export_id=(value->>'target_id')::uuid AND subject_member_id=(current_authority->>'subject_member_id')::uuid AND version=(value->>'expected_version')::bigint AND status IN ('REQUESTED','GENERATING') RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; ELSIF operation_name='EXPORT_DOWNLOAD_ACCESS' THEN IF current_authority->>'private_file_id' IS NULL OR current_authority->>'artifact_digest' IS NULL THEN RAISE EXCEPTION 'EXPORT_NOT_READY'; END IF; IF (current_authority->>'export_version')::bigint<>(value->>'expected_version')::bigint THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.personal_data_export_download_access(access_id,export_id,private_file_id,actor_user_id,reason_code,expires_at,consumed_at,created_at,version) VALUES ((value->'response'->>'access_id')::uuid,(value->>'target_id')::uuid,(current_authority->>'private_file_id')::uuid,(value->>'actor_user_id')::bigint,value->>'reason',(value->'response'->>'expires_at')::timestamptz,NULL,(value->>'occurred_at')::timestamptz,1); ELSE RAISE EXCEPTION 'INVALID_REQUEST'; END IF; INSERT INTO public.service_fulfillment_audit(audit_id,action,actor_user_id,actor_role,target_id,evidence_digest,occurred_at) VALUES ((value->>'audit_id')::uuid,operation_name,(value->>'actor_user_id')::bigint,value->>'actor_role',(value->>'target_id')::uuid,decode(value->>'request_digest','hex'),(value->>'occurred_at')::timestamptz); INSERT INTO public.service_fulfillment_outbox(event_id,aggregate_ref,event_type,payload_json,payload_digest,status,attempts,lease_owner,lease_until,created_at,delivered_at,version) VALUES ((value->>'event_id')::uuid,(value->>'target_id')::uuid,operation_name,jsonb_build_object('operation',operation_name,'target_id',value->>'target_id'),decode(value->>'request_digest','hex'),'PENDING',0,NULL,NULL,(value->>'occurred_at')::timestamptz,NULL,1); INSERT INTO public.service_fulfillment_receipt(receipt_id,actor_scope,operation,target_id,idempotency_key,request_digest,response_json,postimage_digest,created_at) VALUES ((value->>'receipt_id')::uuid,value->>'actor_scope',operation_name,(value->>'target_id')::uuid,value->>'idempotency_key',decode(value->>'request_digest','hex'),value->'response',decode(value->>'expected_response_digest','hex'),(value->>'occurred_at')::timestamptz); RETURN value->'response';"
    for marker, role in replacements.items():
        body = body.replace(marker, role)
    op.execute(sa.text(
        "CREATE OR REPLACE FUNCTION public.slice7_mutation_v1(value JSONB) "
        "RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER "
        "SET search_path=pg_catalog,pg_temp AS $fn$ "
        "DECLARE operation_name VARCHAR; stored_digest BYTEA; stored_response JSONB; affected BIGINT; changed BIGINT; case_version BIGINT; readiness VARCHAR; current_authority JSONB; " + "BEGIN " + body + " END $fn$"
    ))


def _replace_slice5_assessment_input(worker: str) -> None:
    op.execute(sa.text(f"""
CREATE OR REPLACE FUNCTION public.slice5_assessment_input_v1(value_assessment_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $fn$
BEGIN
  IF session_user<>'{worker}' THEN RAISE EXCEPTION 'SLICE5_WORKER_FORBIDDEN'; END IF;
  RETURN (SELECT jsonb_build_object('assessment_id',assessment.assessment_id,
    'service_case_id',assessment.service_case_id,'subject_member_id',assessment.subject_member_id,
    'tenant_id',assessment.tenant_id,'tenant_public_id',origin.tenant_public_id,
    'assembly_id',snapshot.assembly_id,'profile_revision_id',snapshot.profile_revision_id,
    'identity_revision_ref',profile.identity_revision_ref,
    'identity_source_version',profile.identity_source_version,
    'snapshot_created_at',snapshot.created_at,
    'profile_ciphertext',encode(profile.payload_ciphertext,'hex'),
    'profile_key_id',profile.payload_key_id,'rule_set_code',rules.rule_set_code,
    'supersedes_assessment_id',assessment.supersedes_assessment_id,
    'superseded_assessment_version',old.version,
    'superseded_dispute_id',old_dispute.dispute_id,
    'superseded_dispute_version',old_dispute.version,
    'facts',COALESCE((SELECT jsonb_agg(jsonb_build_object(
      'indicator_code',fact.indicator_code,'fact_ref',fact.fact_ref,
      'value_ciphertext',encode(fact.value_ciphertext,'hex'),
      'value_key_id',fact.value_key_id,'unit',fact.unit,'source_type',fact.source_type,
      'measurement_context',fact.measurement_context,'measured_at',fact.measured_at)
      ORDER BY fact.indicator_code)
      FROM public.assessment_input_assembly_fact fact
      WHERE fact.assembly_id=snapshot.assembly_id),'[]'::jsonb))
    FROM public.health_assessment assessment
    JOIN public.assessment_input_snapshot snapshot
      ON snapshot.assessment_id=assessment.assessment_id
    JOIN public.health_profile_revision profile
      ON profile.profile_revision_id=snapshot.profile_revision_id
    JOIN public.assessment_rule_set_version rules
      ON rules.rule_set_version_id=assessment.rule_set_version_id
    JOIN public.institution_tenant_origin origin ON origin.tenant_id=assessment.tenant_id
      AND EXISTS(SELECT 1 FROM public.institution_tenant_origin_current_v1(
        origin.tenant_id,origin.tenant_public_id))
    LEFT JOIN public.health_assessment old
      ON old.assessment_id=assessment.supersedes_assessment_id
    LEFT JOIN LATERAL (SELECT dispute.dispute_id,dispute.version
      FROM public.assessment_dispute dispute
      WHERE dispute.assessment_id=old.assessment_id AND dispute.status='OPEN'
      ORDER BY dispute.created_at DESC LIMIT 1) old_dispute ON true
    WHERE assessment.assessment_id=value_assessment_id AND assessment.status='RUNNING'
    FOR SHARE OF assessment,snapshot,profile,rules,origin);
END $fn$
"""))


def _restore_slice5_assessment_input(worker: str) -> None:
    op.execute(sa.text(f"""
CREATE OR REPLACE FUNCTION public.slice5_assessment_input_v1(value_assessment_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $fn$
BEGIN
  IF session_user<>'{worker}' THEN RAISE EXCEPTION 'SLICE5_WORKER_FORBIDDEN'; END IF;
  RETURN (SELECT jsonb_build_object('assessment_id',h.assessment_id,'service_case_id',h.service_case_id,
    'subject_member_id',h.subject_member_id,'tenant_id',h.tenant_id,'tenant_public_id',ia.tenant_public_id,
    'assembly_id',s.assembly_id,'profile_revision_id',s.profile_revision_id,
    'identity_revision_ref',pr.identity_revision_ref,'identity_source_version',pr.identity_source_version,
    'snapshot_created_at',s.created_at,'profile_ciphertext',encode(pr.payload_ciphertext,'hex'),
    'profile_key_id',pr.payload_key_id,'rule_set_code',r.rule_set_code,
    'supersedes_assessment_id',h.supersedes_assessment_id,'superseded_assessment_version',old.version,
    'superseded_dispute_id',old_dispute.dispute_id,'superseded_dispute_version',old_dispute.version,
    'facts',COALESCE((SELECT jsonb_agg(jsonb_build_object('indicator_code',f.indicator_code,
      'fact_ref',f.fact_ref,'value_ciphertext',encode(f.value_ciphertext,'hex'),
      'value_key_id',f.value_key_id,'unit',f.unit,'source_type',f.source_type,
      'measurement_context',f.measurement_context,'measured_at',f.measured_at)
      ORDER BY f.indicator_code) FROM public.assessment_input_assembly_fact f
      WHERE f.assembly_id=s.assembly_id),'[]'::jsonb))
    FROM public.health_assessment h JOIN public.assessment_input_snapshot s ON s.assessment_id=h.assessment_id
    JOIN public.health_profile_revision pr ON pr.profile_revision_id=s.profile_revision_id
    JOIN public.assessment_rule_set_version r ON r.rule_set_version_id=h.rule_set_version_id
    JOIN public.institution_application ia ON ia.tenant_internal_id=h.tenant_id
    LEFT JOIN public.health_assessment old ON old.assessment_id=h.supersedes_assessment_id
    LEFT JOIN LATERAL (SELECT d.dispute_id,d.version FROM public.assessment_dispute d
      WHERE d.assessment_id=old.assessment_id AND d.status='OPEN' ORDER BY d.created_at DESC LIMIT 1) old_dispute ON true
    WHERE h.assessment_id=value_assessment_id AND h.status='RUNNING' FOR SHARE OF h,s,pr,r,ia);
END $fn$
"""))


def _restore_slice4_authorities(roles: dict[str, str]) -> None:
    writer = roles["health_record_writer"]
    readiness = roles["assessment_readiness_writer"]
    identity = roles["slice4_identity_authority"]

    op.execute(
        f'''CREATE OR REPLACE FUNCTION public.slice4_identity_summary_source_v1(
            value_subject_member_id UUID,
            value_service_case_id UUID
        ) RETURNS TABLE(
            identity_source_kind VARCHAR,
            document_type VARCHAR,
            identity_ciphertext BYTEA,
            identity_nonce BYTEA,
            identity_key_id VARCHAR,
            birth_date_ciphertext BYTEA,
            birth_date_key_id VARCHAR,
            identity_revision_ref UUID,
            source_version BIGINT,
            tenant_public_id UUID,
            evidence_status VARCHAR,
            identity_user_ref BIGINT,
            identity_storage_version BIGINT
        )
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        DECLARE source_kind_value VARCHAR(16); tenant_public_value UUID;
        BEGIN
          IF session_user <> '{identity}' THEN
            RAISE EXCEPTION 'SLICE4_IDENTITY_AUTHORITY_FORBIDDEN';
          END IF;
          SELECT g.source_kind,a.tenant_public_id
            INTO source_kind_value,tenant_public_value
            FROM public.service_case c
            JOIN public.service_enrollment e ON e.enrollment_id=c.enrollment_id
            JOIN public.institution_application a
              ON a.tenant_internal_id=e.tenant_id AND a.status='APPROVED'
            JOIN identity.identity_subject_claim_registry g
              ON g.member_id=e.subject_member_id
            WHERE c.case_id=value_service_case_id
              AND c.status='PREPARING'
              AND e.subject_member_id=value_subject_member_id
            FOR SHARE OF c,e,a,g;
          IF NOT FOUND THEN RETURN; END IF;
          IF source_kind_value='P1' THEN
            RETURN QUERY
            SELECT 'P1'::varchar,'PRC_RESIDENT_ID'::varchar,
              s.id_card_ciphertext,s.id_card_nonce,s.encryption_key_id::varchar,
              NULL::bytea,NULL::varchar,s.submission_id,g.source_facts_version,
              tenant_public_value,'VERIFIED'::varchar,s.user_ref,s.version
            FROM identity.identity_subject_claim_registry g
            JOIN public.identity_verification_submission s
              ON s.submission_id=g.p1_submission_id AND s.user_ref=g.user_ref
            JOIN public.identity_verification_decision d
              ON d.decision_ref=g.p1_decision_ref AND d.user_ref=s.user_ref
            WHERE g.member_id=value_subject_member_id AND g.source_kind='P1'
              AND s.status='verified' AND d.outcome='verified'
              AND d.facts_version=g.source_facts_version
              AND NOT EXISTS(SELECT 1 FROM public.identity_verification_decision n
                WHERE n.supersedes_ref=d.decision_ref)
            FOR SHARE OF g,s,d;
          ELSIF source_kind_value='SLICE3' THEN
            RETURN QUERY
            SELECT 'SLICE3'::varchar,r.document_type::varchar,
              r.id_ciphertext,NULL::bytea,r.id_key_id::varchar,
              r.birth_date_ciphertext,r.birth_date_key_id::varchar,r.revision_id,
              g.source_facts_version,tenant_public_value,'VERIFIED'::varchar,
              NULL::bigint,r.revision_no::bigint
            FROM public.service_case c
            JOIN public.member_identity_verification v
              ON v.verification_id=c.identity_verification_id
            JOIN public.member_identity_revision r
              ON r.verification_id=v.verification_id
             AND r.revision_id=v.current_revision_id
            JOIN public.member_identity_review_decision d
              ON d.decision_id=v.platform_decision_id
             AND d.revision_id=r.revision_id
            JOIN identity.identity_subject_claim_registry g
              ON g.member_id=v.member_id
             AND g.slice3_revision_id=r.revision_id
             AND g.slice3_decision_id=d.decision_id
            WHERE c.case_id=value_service_case_id
              AND v.member_id=value_subject_member_id
              AND v.status='APPROVED' AND d.phase='PLATFORM'
              AND d.decision='APPROVED' AND g.source_kind='SLICE3'
            FOR SHARE OF c,v,r,d,g;
          END IF;
        END; $$;'''
    )

    op.execute(
        f'''CREATE OR REPLACE FUNCTION public.slice4_identity_summary_current_v1(
            value_subject_member_id UUID,
            value_service_case_id UUID,
            value_identity_revision_ref UUID,
            value_identity_source_version BIGINT,
            value_tenant_public_id UUID
        ) RETURNS BOOLEAN
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        DECLARE identity_source_kind VARCHAR(16);
        BEGIN
          IF session_user NOT IN ('{writer}','{readiness}') THEN
            RAISE EXCEPTION 'SLICE4_IDENTITY_CURRENTNESS_FORBIDDEN';
          END IF;
          IF value_identity_source_version < 1 THEN RETURN FALSE; END IF;
          PERFORM 1 FROM public.service_case c
            JOIN public.service_enrollment e ON e.enrollment_id=c.enrollment_id
            JOIN public.institution_application a ON a.tenant_internal_id=e.tenant_id
            WHERE c.case_id=value_service_case_id
              AND c.status='PREPARING'
              AND e.subject_member_id=value_subject_member_id
              AND a.tenant_public_id=value_tenant_public_id
              AND a.status='APPROVED'
            FOR SHARE OF c,e,a;
          IF NOT FOUND THEN RETURN FALSE; END IF;
          SELECT g.source_kind INTO identity_source_kind
            FROM identity.identity_subject_claim_registry g
            WHERE g.member_id=value_subject_member_id
              AND g.source_facts_version=value_identity_source_version
              AND ((g.source_kind='P1' AND g.p1_submission_id=value_identity_revision_ref)
                OR (g.source_kind='SLICE3' AND g.slice3_revision_id=value_identity_revision_ref))
            FOR SHARE;
          IF NOT FOUND THEN RETURN FALSE; END IF;
          IF identity_source_kind='P1' THEN
            PERFORM 1 FROM public.identity_verification_submission s
              JOIN identity.identity_subject_claim_registry g
                ON g.p1_submission_id=s.submission_id
              JOIN public.identity_verification_decision d
                ON d.decision_ref=g.p1_decision_ref AND d.user_ref=s.user_ref
              WHERE s.submission_id=value_identity_revision_ref
                AND s.status='verified'
                AND g.member_id=value_subject_member_id
                AND g.source_facts_version=value_identity_source_version
                AND d.facts_version=value_identity_source_version
                AND d.outcome='verified'
                AND NOT EXISTS(SELECT 1 FROM public.identity_verification_decision n WHERE n.supersedes_ref=d.decision_ref)
              FOR SHARE OF s,g,d;
          ELSIF identity_source_kind='SLICE3' THEN
            PERFORM 1 FROM public.member_identity_verification v
              JOIN public.member_identity_revision r ON r.verification_id=v.verification_id
              JOIN public.member_identity_review_decision d
                ON d.decision_id=v.platform_decision_id AND d.revision_id=r.revision_id
              JOIN identity.identity_subject_claim_registry g
                ON g.member_id=v.member_id
               AND g.slice3_revision_id=r.revision_id
               AND g.slice3_decision_id=d.decision_id
              WHERE r.revision_id=value_identity_revision_ref
                AND g.source_facts_version=value_identity_source_version
                AND v.current_revision_id=r.revision_id AND v.status='APPROVED'
                AND d.decision='APPROVED'
                AND d.phase='PLATFORM'
              FOR SHARE OF v,r,d,g;
          ELSE RETURN FALSE;
          END IF;
          IF NOT FOUND THEN RETURN FALSE; END IF;
          RETURN TRUE;
        END; $$;'''
    )

    op.execute(
        f'''CREATE OR REPLACE FUNCTION public.slice4_assessment_assembly_write_v1(
          value_service_case_id UUID, value_requested_assembly_id UUID,
          value_subject_member_id UUID, value_tenant_public_id UUID,
          value_primary_therapist_id UUID, value_profile_revision_id UUID,
          value_policy_version_id UUID, value_projection_version SMALLINT,
          value_rule_version VARCHAR, value_source_snapshot VARCHAR,
          value_source_vector JSONB, value_encrypted_fact_rows JSONB,
          value_readiness_status VARCHAR, value_reason_codes JSONB,
          value_idempotency_key UUID, value_request_digest BYTEA,
          value_expected_postimage_digest BYTEA
        ) RETURNS TABLE(assembly_id UUID,service_case_id UUID,
          readiness_status VARCHAR,pointer_version BIGINT,generated_at TIMESTAMPTZ)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        DECLARE resolved_tenant_id BIGINT; next_pointer_version BIGINT;
          created TIMESTAMPTZ; stored_request BYTEA; stored_postimage BYTEA;
          resolved_identity_ref UUID; resolved_identity_version BIGINT;
          resolved_digest_key VARCHAR; authoritative_currentness JSONB;
        BEGIN
          IF session_user <> '{readiness}'
             OR value_projection_version<2
             OR value_readiness_status NOT IN ('DATA_INSUFFICIENT','DATA_SYNC_PENDING','DISPUTED','ASSESSMENT_READY')
             OR jsonb_typeof(value_source_vector)<>'object'
             OR jsonb_typeof(value_encrypted_fact_rows)<>'array'
             OR jsonb_typeof(value_reason_codes)<>'array'
             OR value_request_digest IS NULL OR value_expected_postimage_digest IS NULL THEN
            RAISE EXCEPTION 'SLICE4_ASSEMBLY_INVALID';
          END IF;
          IF EXISTS(SELECT 1 FROM jsonb_object_keys(value_source_vector) k
                    WHERE k NOT IN ('consent_version_ids','resolved_generation_id',
                      'required_max_fact_id','required_max_status_event_seq','missing_codes',
                      'expired_codes','disputed_codes','source_vector_digest','source_digest',
                      'assembly_digest','digest_key_id','generated_at','audit_id',
                      'event_id','receipt_id'))
             OR (SELECT count(*) FROM jsonb_object_keys(value_source_vector))<>15 THEN
            RAISE EXCEPTION 'SLICE4_ASSEMBLY_INVALID';
          END IF;
          IF EXISTS(
            SELECT 1 FROM jsonb_array_elements(value_encrypted_fact_rows) item
            WHERE jsonb_typeof(item)<>'object'
               OR (SELECT count(*) FROM jsonb_object_keys(item))<>11
               OR EXISTS(SELECT 1 FROM jsonb_object_keys(item) k WHERE k NOT IN
                  ('indicator_code','fact_ref','measured_at','received_at','source_type',
                  'verification_state','status_event_seq','value_ciphertext','value_key_id',
                  'unit','row_digest'))
          ) THEN RAISE EXCEPTION 'SLICE4_ASSEMBLY_INVALID'; END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(value_service_case_id::text,0));
          authoritative_currentness := public.slice4_readiness_currentness_v1(
            value_service_case_id,0);
          IF authoritative_currentness IS NULL
             OR (authoritative_currentness->>'subject_member_id')::uuid<>value_subject_member_id
             OR (authoritative_currentness->>'tenant_public_id')::uuid<>value_tenant_public_id
             OR (authoritative_currentness->>'primary_therapist_id')::uuid<>value_primary_therapist_id
             OR (authoritative_currentness->>'profile_revision_id')::uuid
                  IS DISTINCT FROM value_profile_revision_id
             OR authoritative_currentness->'consent_version_ids'
                  IS DISTINCT FROM value_source_vector->'consent_version_ids'
             OR (authoritative_currentness->'policy'->>'policy_version_id')::uuid
                  IS DISTINCT FROM value_policy_version_id
             OR (value_readiness_status='ASSESSMENT_READY' AND (
                  COALESCE((authoritative_currentness->>'authorization_complete')::boolean,FALSE)=FALSE
                  OR value_profile_revision_id IS NULL OR value_policy_version_id IS NULL
                  OR NOT ((authoritative_currentness->'policy'->'required_profile_sections')
                          <@ (authoritative_currentness->'profile_section_codes'))
                  OR jsonb_array_length(value_encrypted_fact_rows)=0
                  OR jsonb_array_length(value_reason_codes)>0
                  OR jsonb_array_length(value_source_vector->'missing_codes')>0
                  OR jsonb_array_length(value_source_vector->'expired_codes')>0
                  OR jsonb_array_length(value_source_vector->'disputed_codes')>0)) THEN
            RAISE EXCEPTION 'SLICE4_ASSEMBLY_CURRENTNESS_INVALID';
          END IF;
          SELECT c.tenant_id INTO resolved_tenant_id
            FROM public.service_case c
            JOIN public.service_enrollment e ON e.enrollment_id=c.enrollment_id
            JOIN public.institution_application a ON a.tenant_internal_id=c.tenant_id
            JOIN public.therapist_profile p ON p.therapist_id=c.primary_therapist_id
            WHERE c.case_id=value_service_case_id AND c.subject_member_id=value_subject_member_id
              AND c.primary_therapist_id=value_primary_therapist_id AND c.status='PREPARING'
              AND e.status='CASE_CREATED' AND a.tenant_public_id=value_tenant_public_id
              AND a.status='APPROVED' AND p.status='APPROVED_ACTIVE'
            FOR SHARE OF c,e,a,p;
          IF NOT FOUND THEN RAISE EXCEPTION 'SLICE4_ASSEMBLY_CURRENTNESS_INVALID'; END IF;
          IF value_profile_revision_id IS NULL THEN
            IF value_readiness_status<>'DATA_INSUFFICIENT'
               OR NOT (value_reason_codes ? 'PROFILE_MISSING_OR_STALE') THEN
              RAISE EXCEPTION 'SLICE4_ASSEMBLY_CURRENTNESS_INVALID';
            END IF;
            resolved_digest_key:=value_source_vector->>'digest_key_id';
          ELSE
            SELECT r.identity_revision_ref,r.identity_source_version,r.digest_key_id
              INTO resolved_identity_ref,resolved_identity_version,resolved_digest_key
              FROM public.health_profile_revision r
              WHERE r.profile_revision_id=value_profile_revision_id
                AND r.subject_member_id=value_subject_member_id
                AND r.tenant_public_id=value_tenant_public_id FOR SHARE;
            IF NOT FOUND OR NOT public.slice4_identity_summary_current_v1(
                 value_subject_member_id,value_service_case_id,resolved_identity_ref,
                 resolved_identity_version,value_tenant_public_id) THEN
              RAISE EXCEPTION 'SLICE4_ASSEMBLY_CURRENTNESS_INVALID';
            END IF;
          END IF;
          IF value_readiness_status='ASSESSMENT_READY' AND NOT EXISTS(
            SELECT 1 FROM public.health_projection_generation g
            JOIN public.health_projection_shadow_run sr
              ON sr.run_id=g.current_shadow_run_id
            JOIN public.assessment_readiness_policy_version p
              ON p.policy_version_id=value_policy_version_id
            WHERE g.id=(value_source_vector->>'resolved_generation_id')::bigint
              AND g.status='READY' AND g.projection_version=value_projection_version
              AND g.shadow_success_count=2
              AND NOT EXISTS(
                SELECT 1 FROM public.health_projection_generation newer
                WHERE newer.projection_version=g.projection_version
                  AND newer.status='READY'
                  AND (newer.generation_no,newer.id)>(g.generation_no,g.id))
              AND g.high_watermark->>'source_snapshot'=value_source_snapshot
              AND (SELECT COALESCE(max(e.max_fact_id),0)
                     FROM public.health_projection_subject_indicator_evidence_v2 e
                     WHERE e.generation_id=g.id AND e.subject_member_id=value_subject_member_id
                       AND e.indicator_code IN (
                         SELECT CASE WHEN jsonb_typeof(x)='string'
                           THEN trim(both '"' from x::text) ELSE x->>'indicator_code' END
                         FROM jsonb_array_elements(p.required_indicators) x
                       ))=(value_source_vector->>'required_max_fact_id')::bigint
              AND (SELECT COALESCE(max(e.max_status_event_seq),0)
                     FROM public.health_projection_subject_indicator_evidence_v2 e
                     WHERE e.generation_id=g.id AND e.subject_member_id=value_subject_member_id
                       AND e.indicator_code IN (
                         SELECT CASE WHEN jsonb_typeof(x)='string'
                           THEN trim(both '"' from x::text) ELSE x->>'indicator_code' END
                         FROM jsonb_array_elements(p.required_indicators) x
                       ))=(value_source_vector->>'required_max_status_event_seq')::bigint
              AND NOT EXISTS(
                SELECT 1 FROM public.health_projection_subject_indicator_evidence_v2 e
                JOIN public.slice4_projection_coverage_source_v2 c
                  ON c.subject_member_id=e.subject_member_id
                 AND c.indicator_code=e.indicator_code
                WHERE e.generation_id=g.id AND e.subject_member_id=value_subject_member_id
                  AND (e.fact_count<>c.fact_count
                    OR e.status_event_count<>c.status_event_count
                    OR e.max_fact_id<>c.max_fact_id
                    OR e.max_status_event_seq<>c.max_status_event_seq
                    OR e.fact_set_digest<>c.fact_set_digest
                    OR e.status_set_digest<>c.status_set_digest))
              AND (SELECT count(*)
                     FROM public.health_projection_subject_indicator_evidence_v2 e
                     WHERE e.generation_id=g.id AND e.subject_member_id=value_subject_member_id
                       AND e.indicator_code IN (
                         SELECT CASE WHEN jsonb_typeof(x)='string'
                           THEN trim(both '"' from x::text) ELSE x->>'indicator_code' END
                         FROM jsonb_array_elements(p.required_indicators) x
                       ))=jsonb_array_length(p.required_indicators)
            FOR SHARE OF g,sr,p
          ) THEN RAISE EXCEPTION 'SLICE4_ASSEMBLY_CURRENTNESS_INVALID'; END IF;
          IF value_readiness_status='ASSESSMENT_READY' THEN
            PERFORM 1
              FROM public.health_projection_subject_indicator_evidence_v2 e
              WHERE e.generation_id=(value_source_vector->>'resolved_generation_id')::bigint
                AND e.subject_member_id=value_subject_member_id
              FOR SHARE OF e;
            IF NOT FOUND THEN RAISE EXCEPTION 'SLICE4_ASSEMBLY_CURRENTNESS_INVALID'; END IF;
            PERFORM 1
              FROM jsonb_array_elements(value_encrypted_fact_rows) item
              JOIN public.health_projection_fact f
                ON f.generation_id=(value_source_vector->>'resolved_generation_id')::bigint
               AND f.fact_ref=(item->>'fact_ref')::uuid
               AND f.subject_member_id=value_subject_member_id
               AND f.status_event_seq=(item->>'status_event_seq')::bigint
              JOIN public.health_fact_status_event se
                ON se.fact_id=f.fact_id AND se.status_event_seq=f.status_event_seq
              FOR SHARE OF f,se;
            IF NOT FOUND THEN RAISE EXCEPTION 'SLICE4_ASSEMBLY_CURRENTNESS_INVALID'; END IF;
          END IF;
          IF value_readiness_status='ASSESSMENT_READY' AND (
            jsonb_array_length(value_encrypted_fact_rows)<>
              jsonb_array_length(authoritative_currentness->'policy'->'required_indicators')
            OR EXISTS(
              SELECT 1 FROM jsonb_array_elements(value_encrypted_fact_rows) item
              WHERE NOT EXISTS(
                SELECT 1 FROM public.health_ready_projection_fact_v2 f
                WHERE f.generation_id=(value_source_vector->>'resolved_generation_id')::bigint
                  AND f.subject_member_id=value_subject_member_id
                  AND f.indicator_code=item->>'indicator_code'
                  AND f.fact_ref=(item->>'fact_ref')::uuid
                  AND f.status_event_seq=(item->>'status_event_seq')::bigint
                  AND f.measured_at=(item->>'measured_at')::timestamptz
                  AND f.received_at=(item->>'received_at')::timestamptz
                  AND f.source_type=item->>'source_type'
                  AND f.verification_state=item->>'verification_state'
                  AND f.unit=item->>'unit'
                  AND NOT EXISTS(
                    SELECT 1 FROM public.health_ready_projection_fact_v2 newer
                    WHERE newer.generation_id=f.generation_id
                      AND newer.subject_member_id=f.subject_member_id
                      AND newer.indicator_code=f.indicator_code
                      AND (newer.measured_at,newer.fact_ref)>(f.measured_at,f.fact_ref)))
            ) OR EXISTS(
              SELECT 1 FROM jsonb_array_elements(
                authoritative_currentness->'policy'->'required_indicators') requirement
              WHERE NOT EXISTS(
                SELECT 1 FROM jsonb_array_elements(value_encrypted_fact_rows) item
                WHERE item->>'indicator_code'=CASE
                  WHEN jsonb_typeof(requirement)='string'
                    THEN trim(both '"' from requirement::text)
                  ELSE requirement->>'indicator_code' END)
            )) THEN RAISE EXCEPTION 'SLICE4_ASSEMBLY_CURRENTNESS_INVALID'; END IF;
          IF value_policy_version_id IS NULL THEN
            IF value_readiness_status<>'DATA_INSUFFICIENT'
               OR NOT (value_reason_codes ? 'POLICY_UNAVAILABLE') THEN
              RAISE EXCEPTION 'SLICE4_ASSEMBLY_POLICY_INVALID';
            END IF;
          ELSE
            PERFORM 1 FROM public.assessment_readiness_policy_version p
              WHERE p.policy_version_id=value_policy_version_id AND p.status='PUBLISHED'
                AND p.professionally_approved AND p.projection_version=value_projection_version
                AND p.rule_version=value_rule_version
                AND p.effective_from<=clock_timestamp()
                AND (p.retired_at IS NULL OR p.retired_at>clock_timestamp()) FOR SHARE;
            IF NOT FOUND THEN RAISE EXCEPTION 'SLICE4_ASSEMBLY_POLICY_INVALID'; END IF;
          END IF;
          SELECT i.request_digest,i.postimage_digest INTO stored_request,stored_postimage
            FROM public.slice4_idempotency i
            WHERE i.operation='WRITE_ASSESSMENT_ASSEMBLY'
              AND i.scope_ref=value_service_case_id AND i.idempotency_key=value_idempotency_key
            FOR SHARE;
          IF FOUND THEN
            IF stored_request IS DISTINCT FROM value_request_digest THEN
              RAISE EXCEPTION 'SLICE4_IDEMPOTENCY_CONFLICT';
            END IF;
            IF stored_postimage IS DISTINCT FROM value_expected_postimage_digest
               OR NOT EXISTS(SELECT 1 FROM public.assessment_input_assembly a
                     WHERE a.assembly_id=value_requested_assembly_id
                       AND a.service_case_id=value_service_case_id)
               OR (SELECT count(*) FROM public.slice4_audit a
                     WHERE a.aggregate_ref=value_requested_assembly_id
                       AND a.event_type='ASSESSMENT_ASSEMBLY_WRITTEN')<>1
               OR (SELECT count(*) FROM public.slice4_outbox o
                     WHERE o.aggregate_ref=value_requested_assembly_id
                       AND o.event_type='ASSESSMENT_ASSEMBLY_WRITTEN')<>1 THEN
              RAISE EXCEPTION 'SLICE4_COMMIT_OUTCOME_UNKNOWN';
            END IF;
            RETURN QUERY SELECT a.assembly_id,a.service_case_id,a.status,p.version,a.generated_at
              FROM public.assessment_input_assembly a
              JOIN public.assessment_readiness_case_pointer p
                ON p.current_assembly_id=a.assembly_id AND p.service_case_id=a.service_case_id
              WHERE a.assembly_id=value_requested_assembly_id;
            RETURN;
          END IF;
          SELECT COALESCE(p.version,0)+1 INTO next_pointer_version
            FROM public.assessment_readiness_case_pointer p
            WHERE p.service_case_id=value_service_case_id FOR UPDATE;
          IF NOT FOUND THEN next_pointer_version:=1; END IF;
          created:=(value_source_vector->>'generated_at')::timestamptz;
          IF created IS NULL THEN RAISE EXCEPTION 'SLICE4_ASSEMBLY_INVALID'; END IF;
          INSERT INTO public.assessment_input_assembly(assembly_id,service_case_id,
            subject_member_id,tenant_id,primary_therapist_id,profile_revision_id,
            consent_version_ids,policy_version_id,projection_version,rule_version,
            resolved_generation_id,required_max_fact_id,required_max_status_event_seq,
            source_snapshot,status,reason_codes,missing_codes,expired_codes,disputed_codes,
            source_vector_digest,source_digest,assembly_digest,digest_key_id,generated_at)
          VALUES(value_requested_assembly_id,value_service_case_id,value_subject_member_id,
            resolved_tenant_id,value_primary_therapist_id,value_profile_revision_id,
            value_source_vector->'consent_version_ids',value_policy_version_id,
            value_projection_version,value_rule_version,
            NULLIF(value_source_vector->>'resolved_generation_id','')::bigint,
            (value_source_vector->>'required_max_fact_id')::bigint,
            (value_source_vector->>'required_max_status_event_seq')::bigint,
            value_source_snapshot,value_readiness_status,value_reason_codes,
            value_source_vector->'missing_codes',value_source_vector->'expired_codes',
            value_source_vector->'disputed_codes',
            decode(value_source_vector->>'source_vector_digest','hex'),
            decode(value_source_vector->>'source_digest','hex'),
            decode(value_source_vector->>'assembly_digest','hex'),
            value_source_vector->>'digest_key_id',created);
          INSERT INTO public.assessment_input_assembly_fact(assembly_id,indicator_code,
            fact_ref,measured_at,received_at,source_type,verification_state,
            value_ciphertext,value_key_id,unit,business_day,row_digest)
          SELECT value_requested_assembly_id,x.indicator_code,x.fact_ref,x.measured_at,
            x.received_at,x.source_type,x.verification_state,decode(x.value_ciphertext,'hex'),
            x.value_key_id,x.unit,(x.measured_at AT TIME ZONE 'Asia/Shanghai')::date,
            decode(x.row_digest,'hex')
          FROM jsonb_to_recordset(value_encrypted_fact_rows) AS x(
            indicator_code varchar,fact_ref uuid,measured_at timestamptz,received_at timestamptz,
            source_type varchar,verification_state varchar,value_ciphertext varchar,
            value_key_id varchar,unit varchar,row_digest varchar)
          ORDER BY x.indicator_code;
          INSERT INTO public.assessment_readiness_case_pointer(service_case_id,
            current_assembly_id,source_vector_digest,version,updated_at)
          VALUES(value_service_case_id,value_requested_assembly_id,
            decode(value_source_vector->>'source_vector_digest','hex'),next_pointer_version,created)
          ON CONFLICT ON CONSTRAINT pk_assessment_readiness_case_pointer DO UPDATE SET
            current_assembly_id=EXCLUDED.current_assembly_id,
            source_vector_digest=EXCLUDED.source_vector_digest,
            version=EXCLUDED.version,updated_at=EXCLUDED.updated_at;
          INSERT INTO public.slice4_audit(audit_id,event_type,aggregate_ref,actor_user_id,
            event_digest,digest_key_id,created_at)
          SELECT (value_source_vector->>'audit_id')::uuid,'ASSESSMENT_ASSEMBLY_WRITTEN',value_requested_assembly_id,
            p.user_id,value_expected_postimage_digest,resolved_digest_key,created
            FROM public.therapist_profile p WHERE p.therapist_id=value_primary_therapist_id;
          INSERT INTO public.slice4_outbox(event_id,aggregate_type,aggregate_ref,event_type,
            payload_digest,payload_json,status,attempts,created_at)
          VALUES((value_source_vector->>'event_id')::uuid,'ASSESSMENT_ASSEMBLY',value_requested_assembly_id,
            'ASSESSMENT_ASSEMBLY_WRITTEN',value_expected_postimage_digest,
            jsonb_build_object('assembly_id',value_requested_assembly_id,
              'service_case_id',value_service_case_id),'PENDING',0,created);
          INSERT INTO public.slice4_idempotency(receipt_id,operation,scope_ref,idempotency_key,
            request_digest,postimage_digest,created_at)
          VALUES((value_source_vector->>'receipt_id')::uuid,'WRITE_ASSESSMENT_ASSEMBLY',value_service_case_id,
            value_idempotency_key,value_request_digest,value_expected_postimage_digest,created);
          RETURN QUERY SELECT value_requested_assembly_id,value_service_case_id,
            value_readiness_status,next_pointer_version,created;
        END; $$;'''
    )


def _replace_latest_currentness(roles: dict[str, str]) -> None:
    onboarding = roles["therapist_writer"]
    reviewer = roles["therapist_review_writer"]
    reader = roles["therapist_reader"]
    member_reader = roles["member_enrollment_reader"]
    case_writer = roles["member_case_writer"]

    op.execute(sa.text(f"""
CREATE OR REPLACE FUNCTION public.slice2_institution_business_currentness_v1(
  p_actor_user_id BIGINT,p_claimed_tenant_id BIGINT,p_claimed_role TEXT
) RETURNS UUID
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE
  value_role TEXT; value_status TEXT; value_tenant BIGINT;
  value_exited TIMESTAMPTZ; value_deletion TIMESTAMPTZ;
  tenant_status TEXT; public_id UUID;
BEGIN
  IF session_user NOT IN ('{onboarding}','{reader}') THEN
    RAISE EXCEPTION 'SLICE2_INSTITUTION_BUSINESS_CURRENTNESS_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  IF p_actor_user_id IS NULL OR p_claimed_tenant_id IS NULL
     OR p_claimed_role NOT IN ('org_admin','org_operator') THEN RETURN NULL; END IF;
  SELECT actor_user.role::TEXT,actor_user.status::TEXT,actor_user.tenant_id,
         actor_user.exited_at,actor_user.deletion_requested_at
    INTO value_role,value_status,value_tenant,value_exited,value_deletion
    FROM public."user" actor_user WHERE actor_user.id=p_actor_user_id
    FOR SHARE OF actor_user;
  IF NOT FOUND OR value_role<>p_claimed_role OR value_status<>'active'
     OR value_tenant IS DISTINCT FROM p_claimed_tenant_id
     OR value_exited IS NOT NULL OR value_deletion IS NOT NULL THEN RETURN NULL; END IF;
  SELECT tenant_row.status::TEXT INTO tenant_status
    FROM public.tenant tenant_row WHERE tenant_row.id=p_claimed_tenant_id
    FOR SHARE OF tenant_row;
  IF NOT FOUND OR tenant_status<>'active' THEN RETURN NULL; END IF;
  BEGIN
    SELECT origin.tenant_public_id INTO STRICT public_id
      FROM public.institution_tenant_origin origin
      WHERE origin.tenant_id=p_claimed_tenant_id
        AND EXISTS (
          SELECT 1 FROM public.institution_tenant_origin_current_v1(
            origin.tenant_id,origin.tenant_public_id
          )
        );
  EXCEPTION WHEN NO_DATA_FOUND THEN RETURN NULL;
    WHEN TOO_MANY_ROWS THEN
      RAISE EXCEPTION 'SLICE2_INSTITUTION_BUSINESS_CURRENTNESS_AMBIGUOUS' USING ERRCODE='21000';
  END;
  RETURN public_id;
END $$
"""))

    op.execute(sa.text(f"""
CREATE OR REPLACE FUNCTION public.slice2_therapist_activation_currentness_v1(p_invitation_id UUID)
RETURNS TABLE(tenant_id BIGINT,tenant_public_id UUID)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE candidate_tenant BIGINT; locked_tenant BIGINT; tenant_status TEXT; public_id UUID;
BEGIN
  IF session_user <> '{onboarding}' THEN
    RAISE EXCEPTION 'SLICE2_THERAPIST_ACTIVATION_CURRENTNESS_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  SELECT invitation_row.tenant_id INTO candidate_tenant
    FROM public.therapist_invitation invitation_row
    WHERE invitation_row.invitation_id=p_invitation_id;
  IF NOT FOUND THEN RETURN; END IF;
  SELECT tenant_row.status::TEXT INTO tenant_status FROM public.tenant tenant_row
    WHERE tenant_row.id=candidate_tenant FOR SHARE OF tenant_row;
  IF NOT FOUND OR tenant_status<>'active' THEN RETURN; END IF;
  SELECT invitation_row.tenant_id INTO locked_tenant
    FROM public.therapist_invitation invitation_row
    WHERE invitation_row.invitation_id=p_invitation_id
    FOR UPDATE OF invitation_row;
  IF NOT FOUND OR locked_tenant IS DISTINCT FROM candidate_tenant THEN RETURN; END IF;
  BEGIN
    SELECT origin.tenant_public_id INTO STRICT public_id
      FROM public.institution_tenant_origin origin
      WHERE origin.tenant_id=candidate_tenant
        AND EXISTS (
          SELECT 1 FROM public.institution_tenant_origin_current_v1(
            origin.tenant_id,origin.tenant_public_id
          )
        );
  EXCEPTION WHEN NO_DATA_FOUND THEN RETURN;
    WHEN TOO_MANY_ROWS THEN
      RAISE EXCEPTION 'SLICE2_THERAPIST_ACTIVATION_CURRENTNESS_AMBIGUOUS' USING ERRCODE='21000';
  END;
  RETURN QUERY SELECT candidate_tenant,public_id;
END $$
"""))

    op.execute(sa.text(f"""
CREATE OR REPLACE FUNCTION public.slice2_therapist_onboarding_currentness_v1(
  p_actor_user_id BIGINT,p_claimed_tenant_id BIGINT
) RETURNS TABLE(therapist_id UUID,tenant_public_id UUID)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE
  value_role TEXT; value_status TEXT; value_tenant BIGINT;
  value_exited TIMESTAMPTZ; value_deletion TIMESTAMPTZ; tenant_status TEXT;
  profile_id UUID; profile_tenant BIGINT; profile_status TEXT; totp_current BOOLEAN; public_id UUID;
BEGIN
  IF session_user NOT IN ('{onboarding}','{reviewer}','{reader}') THEN
    RAISE EXCEPTION 'SLICE2_THERAPIST_ONBOARDING_CURRENTNESS_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  SELECT actor_user.role::TEXT,actor_user.status::TEXT,actor_user.tenant_id,
         actor_user.exited_at,actor_user.deletion_requested_at
    INTO value_role,value_status,value_tenant,value_exited,value_deletion
    FROM public."user" actor_user WHERE actor_user.id=p_actor_user_id
    FOR SHARE OF actor_user;
  IF NOT FOUND OR value_role<>'therapist' OR value_status<>'active'
     OR value_tenant IS DISTINCT FROM p_claimed_tenant_id
     OR value_exited IS NOT NULL OR value_deletion IS NOT NULL THEN RETURN; END IF;
  SELECT tenant_row.status::TEXT INTO tenant_status FROM public.tenant tenant_row
    WHERE tenant_row.id=p_claimed_tenant_id FOR SHARE OF tenant_row;
  IF NOT FOUND OR tenant_status<>'active' THEN RETURN; END IF;
  IF session_user IN ('{onboarding}','{reviewer}') THEN
    SELECT profile_row.therapist_id,profile_row.tenant_id,profile_row.status,profile_row.totp_enabled
      INTO profile_id,profile_tenant,profile_status,totp_current
      FROM public.therapist_profile profile_row WHERE profile_row.user_id=p_actor_user_id
      FOR UPDATE OF profile_row;
  ELSE
    SELECT profile_row.therapist_id,profile_row.tenant_id,profile_row.status,profile_row.totp_enabled
      INTO profile_id,profile_tenant,profile_status,totp_current
      FROM public.therapist_profile profile_row WHERE profile_row.user_id=p_actor_user_id
      FOR SHARE OF profile_row;
  END IF;
  IF NOT FOUND OR profile_tenant IS DISTINCT FROM p_claimed_tenant_id
     OR profile_status NOT IN ('ACTIVATED','DRAFT','SUBMITTED','UNDER_REVIEW',
       'NEEDS_CORRECTION','RESUBMITTED','APPROVED_ACTIVE','SUSPENDED')
     OR totp_current IS NOT TRUE THEN RETURN; END IF;
  BEGIN
    SELECT origin.tenant_public_id INTO STRICT public_id
      FROM public.institution_tenant_origin origin
      WHERE origin.tenant_id=p_claimed_tenant_id
        AND EXISTS (
          SELECT 1 FROM public.institution_tenant_origin_current_v1(
            origin.tenant_id,origin.tenant_public_id
          )
        );
  EXCEPTION WHEN NO_DATA_FOUND THEN RETURN;
    WHEN TOO_MANY_ROWS THEN
      RAISE EXCEPTION 'SLICE2_THERAPIST_ONBOARDING_CURRENTNESS_AMBIGUOUS' USING ERRCODE='21000';
  END;
  RETURN QUERY SELECT profile_id,public_id;
END $$
"""))

    op.execute(sa.text(f"""
CREATE OR REPLACE FUNCTION public.slice2_therapist_self_exit_currentness_v1(
  p_actor_user_id BIGINT,p_claimed_tenant_id BIGINT,
  p_idempotency_key TEXT,p_request_digest TEXT
) RETURNS TABLE(therapist_id UUID,tenant_public_id UUID)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE
  value_role TEXT; value_status TEXT; value_tenant BIGINT;
  value_exited TIMESTAMPTZ; value_deletion TIMESTAMPTZ; tenant_status TEXT;
  profile_id UUID; profile_tenant BIGINT; profile_status TEXT; profile_version BIGINT;
  profile_exited_at TIMESTAMPTZ; profile_cases INTEGER; totp_current BOOLEAN;
  public_id UUID; actor_scope_value TEXT; receipt_count BIGINT;
  status_count BIGINT; audit_count BIGINT; outbox_count BIGINT;
BEGIN
  IF session_user <> '{reviewer}' THEN
    RAISE EXCEPTION 'SLICE2_THERAPIST_SELF_EXIT_CURRENTNESS_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  IF p_actor_user_id IS NULL OR p_claimed_tenant_id IS NULL
     OR NOT (length(p_idempotency_key) BETWEEN 1 AND 128)
     OR p_request_digest !~ '^[0-9a-f]{{64}}$' THEN RETURN; END IF;
  SELECT actor_user.role::TEXT,actor_user.status::TEXT,actor_user.tenant_id,
         actor_user.exited_at,actor_user.deletion_requested_at
    INTO value_role,value_status,value_tenant,value_exited,value_deletion
    FROM public."user" actor_user WHERE actor_user.id=p_actor_user_id
    FOR SHARE OF actor_user;
  IF NOT FOUND OR value_role<>'therapist' OR value_status<>'active'
     OR value_tenant IS DISTINCT FROM p_claimed_tenant_id
     OR value_exited IS NOT NULL OR value_deletion IS NOT NULL THEN RETURN; END IF;
  SELECT tenant_row.status::TEXT INTO tenant_status FROM public.tenant tenant_row
    WHERE tenant_row.id=p_claimed_tenant_id FOR SHARE OF tenant_row;
  IF NOT FOUND OR tenant_status<>'active' THEN RETURN; END IF;
  SELECT profile_row.therapist_id,profile_row.tenant_id,profile_row.status,
         profile_row.version,profile_row.exited_at,profile_row.active_case_count,
         profile_row.totp_enabled
    INTO profile_id,profile_tenant,profile_status,profile_version,
         profile_exited_at,profile_cases,totp_current
    FROM public.therapist_profile profile_row WHERE profile_row.user_id=p_actor_user_id
    FOR UPDATE OF profile_row;
  IF NOT FOUND OR profile_tenant IS DISTINCT FROM p_claimed_tenant_id
     OR totp_current IS NOT TRUE THEN RETURN; END IF;
  BEGIN
    SELECT origin.tenant_public_id INTO STRICT public_id
      FROM public.institution_tenant_origin origin
      WHERE origin.tenant_id=p_claimed_tenant_id
        AND EXISTS (
          SELECT 1 FROM public.institution_tenant_origin_current_v1(
            origin.tenant_id,origin.tenant_public_id
          )
        );
  EXCEPTION WHEN NO_DATA_FOUND THEN RETURN;
    WHEN TOO_MANY_ROWS THEN
      RAISE EXCEPTION 'SLICE2_THERAPIST_SELF_EXIT_CURRENTNESS_AMBIGUOUS' USING ERRCODE='21000';
  END;
  IF profile_status IN ('APPROVED_ACTIVE','SUSPENDED') THEN
    RETURN QUERY SELECT profile_id,public_id;
    RETURN;
  END IF;
  IF profile_status='EXITED' AND profile_exited_at IS NOT NULL THEN
    IF profile_cases<>0 THEN RETURN; END IF;
  ELSE
    RETURN;
  END IF;

  actor_scope_value := 'actor:' || p_actor_user_id::TEXT || ':therapist:' || profile_id::TEXT;
  SELECT count(*) INTO receipt_count
    FROM public.therapist_workflow_idempotency idempotency_row
    WHERE idempotency_row.actor_scope=actor_scope_value
      AND idempotency_row.operation='EXITED'
      AND idempotency_row.idempotency_key=p_idempotency_key
      AND idempotency_row.request_digest=p_request_digest;
  SELECT count(*) INTO status_count
    FROM public.therapist_status_decision status_row
    WHERE status_row.therapist_id=profile_id
      AND status_row.decision='EXITED'
      AND status_row.actor_kind='USER'
      AND status_row.actor_user_id=p_actor_user_id
      AND status_row.request_digest=p_request_digest
      AND status_row.expected_profile_version+1=profile_version;
  SELECT count(*) INTO audit_count
    FROM public.therapist_workflow_audit audit_row
    JOIN public.therapist_workflow_idempotency idempotency_row
      ON idempotency_row.actor_scope=audit_row.actor_scope
     AND idempotency_row.operation='EXITED'
     AND idempotency_row.idempotency_key=p_idempotency_key
     AND idempotency_row.request_digest=p_request_digest
    WHERE audit_row.actor_scope=actor_scope_value
      AND audit_row.action='THERAPIST_EXITED'
      AND audit_row.object_id=profile_id
      AND audit_row.result='SUCCESS'
      AND audit_row.postimage_digest=idempotency_row.postimage_digest;
  SELECT count(*) INTO outbox_count
    FROM public.therapist_workflow_outbox outbox_row
    WHERE outbox_row.event_type='THERAPIST_EXITED'
      AND outbox_row.aggregate_id=profile_id
      AND outbox_row.tenant_id=p_claimed_tenant_id;
  IF receipt_count<>1 OR status_count<>1 OR audit_count<>1 OR outbox_count<>1 THEN RETURN; END IF;
  RETURN QUERY SELECT profile_id,public_id;
END $$
"""))

    op.execute(sa.text(f"""
CREATE OR REPLACE FUNCTION public.slice3_therapist_service_currentness_v1(
  p_actor_user_id BIGINT,p_claimed_tenant_id BIGINT
) RETURNS TABLE(therapist_id UUID,tenant_public_id UUID)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE
  value_role TEXT; value_status TEXT; value_tenant BIGINT;
  value_exited TIMESTAMPTZ; value_deletion TIMESTAMPTZ; tenant_status TEXT;
  profile_id UUID; profile_tenant BIGINT; profile_status TEXT;
  qualification_id UUID; valid_until DATE; public_id UUID;
BEGIN
  IF session_user NOT IN ('{member_reader}','{case_writer}') THEN
    RAISE EXCEPTION 'SLICE3_THERAPIST_SERVICE_CURRENTNESS_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  SELECT actor_user.role::TEXT,actor_user.status::TEXT,actor_user.tenant_id,
         actor_user.exited_at,actor_user.deletion_requested_at
    INTO value_role,value_status,value_tenant,value_exited,value_deletion
    FROM public."user" actor_user WHERE actor_user.id=p_actor_user_id
    FOR SHARE OF actor_user;
  IF NOT FOUND OR value_role<>'therapist' OR value_status<>'active'
     OR value_tenant IS DISTINCT FROM p_claimed_tenant_id
     OR value_exited IS NOT NULL OR value_deletion IS NOT NULL THEN RETURN; END IF;
  SELECT tenant_row.status::TEXT INTO tenant_status FROM public.tenant tenant_row
    WHERE tenant_row.id=p_claimed_tenant_id FOR SHARE OF tenant_row;
  IF NOT FOUND OR tenant_status<>'active' THEN RETURN; END IF;
  SELECT profile_row.therapist_id,profile_row.tenant_id,profile_row.status,
         profile_row.current_qualification_version_id,profile_row.qualification_valid_until
    INTO profile_id,profile_tenant,profile_status,qualification_id,valid_until
    FROM public.therapist_profile profile_row WHERE profile_row.user_id=p_actor_user_id
    FOR SHARE OF profile_row;
  IF NOT FOUND OR profile_tenant IS DISTINCT FROM p_claimed_tenant_id
     OR profile_status<>'APPROVED_ACTIVE' OR qualification_id IS NULL
     OR valid_until IS NULL
     OR valid_until < (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Shanghai')::DATE THEN RETURN; END IF;
  BEGIN
    SELECT origin.tenant_public_id INTO STRICT public_id
      FROM public.institution_tenant_origin origin
      WHERE origin.tenant_id=p_claimed_tenant_id
        AND EXISTS (
          SELECT 1 FROM public.institution_tenant_origin_current_v1(
            origin.tenant_id,origin.tenant_public_id
          )
        );
  EXCEPTION WHEN NO_DATA_FOUND THEN RETURN;
    WHEN TOO_MANY_ROWS THEN
      RAISE EXCEPTION 'SLICE3_THERAPIST_SERVICE_CURRENTNESS_AMBIGUOUS' USING ERRCODE='21000';
  END;
  RETURN QUERY SELECT profile_id,public_id;
END $$
"""))

    op.execute(sa.text(f"""
CREATE OR REPLACE FUNCTION public.slice2_therapist_review_target_currentness_v1(
  p_actor_user_id BIGINT,p_subject_therapist_id UUID
) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE reviewer_current BOOLEAN; candidate_tenant BIGINT; tenant_status TEXT;
        locked_tenant BIGINT; public_id UUID;
BEGIN
  IF session_user <> '{reviewer}' THEN
    RAISE EXCEPTION 'SLICE2_THERAPIST_REVIEW_TARGET_CURRENTNESS_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  SELECT actor_user.role='super_admin' AND actor_user.status='active'
         AND actor_user.tenant_id IS NULL
         AND actor_user.exited_at IS NULL AND actor_user.deletion_requested_at IS NULL
    INTO reviewer_current FROM public."user" actor_user WHERE actor_user.id=p_actor_user_id
    FOR SHARE OF actor_user;
  IF COALESCE(reviewer_current,false) IS NOT TRUE THEN RETURN false; END IF;
  SELECT profile_row.tenant_id INTO candidate_tenant FROM public.therapist_profile profile_row
    WHERE profile_row.therapist_id=p_subject_therapist_id;
  IF NOT FOUND THEN RETURN false; END IF;
  SELECT tenant_row.status::TEXT INTO tenant_status FROM public.tenant tenant_row
    WHERE tenant_row.id=candidate_tenant FOR SHARE OF tenant_row;
  IF NOT FOUND OR tenant_status<>'active' THEN RETURN false; END IF;
  SELECT profile_row.tenant_id INTO locked_tenant FROM public.therapist_profile profile_row
    WHERE profile_row.therapist_id=p_subject_therapist_id FOR UPDATE OF profile_row;
  IF NOT FOUND OR locked_tenant IS DISTINCT FROM candidate_tenant THEN RETURN false; END IF;
  BEGIN
    SELECT origin.tenant_public_id INTO STRICT public_id
      FROM public.institution_tenant_origin origin
      WHERE origin.tenant_id=candidate_tenant
        AND EXISTS (
          SELECT 1 FROM public.institution_tenant_origin_current_v1(
            origin.tenant_id,origin.tenant_public_id
          )
        );
  EXCEPTION WHEN NO_DATA_FOUND THEN RETURN false;
    WHEN TOO_MANY_ROWS THEN
      RAISE EXCEPTION 'SLICE2_THERAPIST_REVIEW_TARGET_CURRENTNESS_AMBIGUOUS' USING ERRCODE='21000';
  END;
  RETURN true;
END $$
"""))

    op.execute(sa.text(f"""
CREATE OR REPLACE FUNCTION public.slice2_therapist_review_item_currentness_v1(
  p_actor_user_id BIGINT,p_review_item_id UUID
) RETURNS UUID
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE reviewer_current BOOLEAN; candidate_therapist UUID; candidate_tenant BIGINT;
        locked_tenant BIGINT; locked_therapist UUID; tenant_status TEXT; public_id UUID;
BEGIN
  IF session_user <> '{reviewer}' THEN
    RAISE EXCEPTION 'SLICE2_THERAPIST_REVIEW_ITEM_CURRENTNESS_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  SELECT review_item_row.therapist_id,profile_row.tenant_id
    INTO candidate_therapist,candidate_tenant
    FROM public.therapist_review_item review_item_row
    JOIN public.therapist_profile profile_row ON profile_row.therapist_id=review_item_row.therapist_id
    WHERE review_item_row.review_item_id=p_review_item_id;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT actor_user.role='super_admin' AND actor_user.status='active'
         AND actor_user.tenant_id IS NULL
         AND actor_user.exited_at IS NULL AND actor_user.deletion_requested_at IS NULL
    INTO reviewer_current FROM public."user" actor_user WHERE actor_user.id=p_actor_user_id
    FOR SHARE OF actor_user;
  IF COALESCE(reviewer_current,false) IS NOT TRUE THEN RETURN NULL; END IF;
  SELECT tenant_row.status::TEXT INTO tenant_status FROM public.tenant tenant_row
    WHERE tenant_row.id=candidate_tenant FOR SHARE OF tenant_row;
  IF NOT FOUND OR tenant_status<>'active' THEN RETURN NULL; END IF;
  SELECT profile_row.tenant_id INTO locked_tenant FROM public.therapist_profile profile_row
    WHERE profile_row.therapist_id=candidate_therapist FOR UPDATE OF profile_row;
  IF NOT FOUND OR locked_tenant IS DISTINCT FROM candidate_tenant THEN RETURN NULL; END IF;
  SELECT review_item_row.therapist_id INTO locked_therapist
    FROM public.therapist_review_item review_item_row
    WHERE review_item_row.review_item_id=p_review_item_id
    FOR UPDATE OF review_item_row;
  IF NOT FOUND OR locked_therapist IS DISTINCT FROM candidate_therapist THEN RETURN NULL; END IF;
  BEGIN
    SELECT origin.tenant_public_id INTO STRICT public_id
      FROM public.institution_tenant_origin origin
      WHERE origin.tenant_id=candidate_tenant
        AND EXISTS (
          SELECT 1 FROM public.institution_tenant_origin_current_v1(
            origin.tenant_id,origin.tenant_public_id
          )
        );
  EXCEPTION WHEN NO_DATA_FOUND THEN RETURN NULL;
    WHEN TOO_MANY_ROWS THEN
      RAISE EXCEPTION 'SLICE2_THERAPIST_REVIEW_ITEM_CURRENTNESS_AMBIGUOUS' USING ERRCODE='21000';
  END;
  RETURN candidate_therapist;
END $$
"""))


def _restore_latest_currentness(roles: dict[str, str]) -> None:
    onboarding = roles["therapist_writer"]
    reviewer = roles["therapist_review_writer"]
    reader = roles["therapist_reader"]
    member_reader = roles["member_enrollment_reader"]
    case_writer = roles["member_case_writer"]

    op.execute(sa.text(f"""
CREATE OR REPLACE FUNCTION public.slice2_institution_business_currentness_v1(
  p_actor_user_id BIGINT,p_claimed_tenant_id BIGINT,p_claimed_role TEXT
) RETURNS UUID
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE
  value_role TEXT; value_status TEXT; value_tenant BIGINT;
  value_exited TIMESTAMPTZ; value_deletion TIMESTAMPTZ;
  tenant_status TEXT; public_id UUID;
BEGIN
  IF session_user NOT IN ('{onboarding}','{reader}') THEN
    RAISE EXCEPTION 'SLICE2_INSTITUTION_BUSINESS_CURRENTNESS_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  IF p_actor_user_id IS NULL OR p_claimed_tenant_id IS NULL
     OR p_claimed_role NOT IN ('org_admin','org_operator') THEN RETURN NULL; END IF;
  SELECT actor_user.role::TEXT,actor_user.status::TEXT,actor_user.tenant_id,
         actor_user.exited_at,actor_user.deletion_requested_at
    INTO value_role,value_status,value_tenant,value_exited,value_deletion
    FROM public."user" actor_user WHERE actor_user.id=p_actor_user_id
    FOR SHARE OF actor_user;
  IF NOT FOUND OR value_role<>p_claimed_role OR value_status<>'active'
     OR value_tenant IS DISTINCT FROM p_claimed_tenant_id
     OR value_exited IS NOT NULL OR value_deletion IS NOT NULL THEN RETURN NULL; END IF;
  SELECT tenant_row.status::TEXT INTO tenant_status
    FROM public.tenant tenant_row WHERE tenant_row.id=p_claimed_tenant_id
    FOR SHARE OF tenant_row;
  IF NOT FOUND OR tenant_status<>'active' THEN RETURN NULL; END IF;
  BEGIN
    SELECT institution_application.tenant_public_id INTO STRICT public_id
      FROM public.institution_application institution_application
      WHERE institution_application.tenant_internal_id=p_claimed_tenant_id
        AND institution_application.status='APPROVED'
        AND institution_application.tenant_public_id IS NOT NULL;
  EXCEPTION WHEN NO_DATA_FOUND THEN RETURN NULL;
    WHEN TOO_MANY_ROWS THEN
      RAISE EXCEPTION 'SLICE2_INSTITUTION_BUSINESS_CURRENTNESS_AMBIGUOUS' USING ERRCODE='21000';
  END;
  RETURN public_id;
END $$
"""))

    op.execute(sa.text(f"""
CREATE OR REPLACE FUNCTION public.slice2_therapist_activation_currentness_v1(p_invitation_id UUID)
RETURNS TABLE(tenant_id BIGINT,tenant_public_id UUID)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE candidate_tenant BIGINT; locked_tenant BIGINT; tenant_status TEXT; public_id UUID;
BEGIN
  IF session_user <> '{onboarding}' THEN
    RAISE EXCEPTION 'SLICE2_THERAPIST_ACTIVATION_CURRENTNESS_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  SELECT invitation_row.tenant_id INTO candidate_tenant
    FROM public.therapist_invitation invitation_row
    WHERE invitation_row.invitation_id=p_invitation_id;
  IF NOT FOUND THEN RETURN; END IF;
  SELECT tenant_row.status::TEXT INTO tenant_status FROM public.tenant tenant_row
    WHERE tenant_row.id=candidate_tenant FOR SHARE OF tenant_row;
  IF NOT FOUND OR tenant_status<>'active' THEN RETURN; END IF;
  SELECT invitation_row.tenant_id INTO locked_tenant
    FROM public.therapist_invitation invitation_row
    WHERE invitation_row.invitation_id=p_invitation_id
    FOR UPDATE OF invitation_row;
  IF NOT FOUND OR locked_tenant IS DISTINCT FROM candidate_tenant THEN RETURN; END IF;
  BEGIN
    SELECT institution_application.tenant_public_id INTO STRICT public_id
      FROM public.institution_application institution_application
      WHERE institution_application.tenant_internal_id=candidate_tenant
        AND institution_application.status='APPROVED'
        AND institution_application.tenant_public_id IS NOT NULL;
  EXCEPTION WHEN NO_DATA_FOUND THEN RETURN;
    WHEN TOO_MANY_ROWS THEN
      RAISE EXCEPTION 'SLICE2_THERAPIST_ACTIVATION_CURRENTNESS_AMBIGUOUS' USING ERRCODE='21000';
  END;
  RETURN QUERY SELECT candidate_tenant,public_id;
END $$
"""))

    op.execute(sa.text(f"""
CREATE OR REPLACE FUNCTION public.slice2_therapist_onboarding_currentness_v1(
  p_actor_user_id BIGINT,p_claimed_tenant_id BIGINT
) RETURNS TABLE(therapist_id UUID,tenant_public_id UUID)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE
  value_role TEXT; value_status TEXT; value_tenant BIGINT;
  value_exited TIMESTAMPTZ; value_deletion TIMESTAMPTZ; tenant_status TEXT;
  profile_id UUID; profile_tenant BIGINT; profile_status TEXT; totp_current BOOLEAN; public_id UUID;
BEGIN
  IF session_user NOT IN ('{onboarding}','{reviewer}','{reader}') THEN
    RAISE EXCEPTION 'SLICE2_THERAPIST_ONBOARDING_CURRENTNESS_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  SELECT actor_user.role::TEXT,actor_user.status::TEXT,actor_user.tenant_id,
         actor_user.exited_at,actor_user.deletion_requested_at
    INTO value_role,value_status,value_tenant,value_exited,value_deletion
    FROM public."user" actor_user WHERE actor_user.id=p_actor_user_id
    FOR SHARE OF actor_user;
  IF NOT FOUND OR value_role<>'therapist' OR value_status<>'active'
     OR value_tenant IS DISTINCT FROM p_claimed_tenant_id
     OR value_exited IS NOT NULL OR value_deletion IS NOT NULL THEN RETURN; END IF;
  SELECT tenant_row.status::TEXT INTO tenant_status FROM public.tenant tenant_row
    WHERE tenant_row.id=p_claimed_tenant_id FOR SHARE OF tenant_row;
  IF NOT FOUND OR tenant_status<>'active' THEN RETURN; END IF;
  IF session_user IN ('{onboarding}','{reviewer}') THEN
    SELECT profile_row.therapist_id,profile_row.tenant_id,profile_row.status,profile_row.totp_enabled
      INTO profile_id,profile_tenant,profile_status,totp_current
      FROM public.therapist_profile profile_row WHERE profile_row.user_id=p_actor_user_id
      FOR UPDATE OF profile_row;
  ELSE
    SELECT profile_row.therapist_id,profile_row.tenant_id,profile_row.status,profile_row.totp_enabled
      INTO profile_id,profile_tenant,profile_status,totp_current
      FROM public.therapist_profile profile_row WHERE profile_row.user_id=p_actor_user_id
      FOR SHARE OF profile_row;
  END IF;
  IF NOT FOUND OR profile_tenant IS DISTINCT FROM p_claimed_tenant_id
     OR profile_status NOT IN ('ACTIVATED','DRAFT','SUBMITTED','UNDER_REVIEW',
       'NEEDS_CORRECTION','RESUBMITTED','APPROVED_ACTIVE','SUSPENDED')
     OR totp_current IS NOT TRUE THEN RETURN; END IF;
  BEGIN
    SELECT institution_application.tenant_public_id INTO STRICT public_id
      FROM public.institution_application institution_application
      WHERE institution_application.tenant_internal_id=p_claimed_tenant_id
        AND institution_application.status='APPROVED'
        AND institution_application.tenant_public_id IS NOT NULL;
  EXCEPTION WHEN NO_DATA_FOUND THEN RETURN;
    WHEN TOO_MANY_ROWS THEN
      RAISE EXCEPTION 'SLICE2_THERAPIST_ONBOARDING_CURRENTNESS_AMBIGUOUS' USING ERRCODE='21000';
  END;
  RETURN QUERY SELECT profile_id,public_id;
END $$
"""))

    op.execute(sa.text(f"""
CREATE OR REPLACE FUNCTION public.slice2_therapist_self_exit_currentness_v1(
  p_actor_user_id BIGINT,p_claimed_tenant_id BIGINT,
  p_idempotency_key TEXT,p_request_digest TEXT
) RETURNS TABLE(therapist_id UUID,tenant_public_id UUID)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE
  value_role TEXT; value_status TEXT; value_tenant BIGINT;
  value_exited TIMESTAMPTZ; value_deletion TIMESTAMPTZ; tenant_status TEXT;
  profile_id UUID; profile_tenant BIGINT; profile_status TEXT; profile_version BIGINT;
  profile_exited_at TIMESTAMPTZ; profile_cases INTEGER; totp_current BOOLEAN;
  public_id UUID; actor_scope_value TEXT; receipt_count BIGINT;
  status_count BIGINT; audit_count BIGINT; outbox_count BIGINT;
BEGIN
  IF session_user <> '{reviewer}' THEN
    RAISE EXCEPTION 'SLICE2_THERAPIST_SELF_EXIT_CURRENTNESS_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  IF p_actor_user_id IS NULL OR p_claimed_tenant_id IS NULL
     OR NOT (length(p_idempotency_key) BETWEEN 1 AND 128)
     OR p_request_digest !~ '^[0-9a-f]{{64}}$' THEN RETURN; END IF;
  SELECT actor_user.role::TEXT,actor_user.status::TEXT,actor_user.tenant_id,
         actor_user.exited_at,actor_user.deletion_requested_at
    INTO value_role,value_status,value_tenant,value_exited,value_deletion
    FROM public."user" actor_user WHERE actor_user.id=p_actor_user_id
    FOR SHARE OF actor_user;
  IF NOT FOUND OR value_role<>'therapist' OR value_status<>'active'
     OR value_tenant IS DISTINCT FROM p_claimed_tenant_id
     OR value_exited IS NOT NULL OR value_deletion IS NOT NULL THEN RETURN; END IF;
  SELECT tenant_row.status::TEXT INTO tenant_status FROM public.tenant tenant_row
    WHERE tenant_row.id=p_claimed_tenant_id FOR SHARE OF tenant_row;
  IF NOT FOUND OR tenant_status<>'active' THEN RETURN; END IF;
  SELECT profile_row.therapist_id,profile_row.tenant_id,profile_row.status,
         profile_row.version,profile_row.exited_at,profile_row.active_case_count,
         profile_row.totp_enabled
    INTO profile_id,profile_tenant,profile_status,profile_version,
         profile_exited_at,profile_cases,totp_current
    FROM public.therapist_profile profile_row WHERE profile_row.user_id=p_actor_user_id
    FOR UPDATE OF profile_row;
  IF NOT FOUND OR profile_tenant IS DISTINCT FROM p_claimed_tenant_id
     OR totp_current IS NOT TRUE THEN RETURN; END IF;
  BEGIN
    SELECT institution_application.tenant_public_id INTO STRICT public_id
      FROM public.institution_application institution_application
      WHERE institution_application.tenant_internal_id=p_claimed_tenant_id
        AND institution_application.status='APPROVED'
        AND institution_application.tenant_public_id IS NOT NULL;
  EXCEPTION WHEN NO_DATA_FOUND THEN RETURN;
    WHEN TOO_MANY_ROWS THEN
      RAISE EXCEPTION 'SLICE2_THERAPIST_SELF_EXIT_CURRENTNESS_AMBIGUOUS' USING ERRCODE='21000';
  END;
  IF profile_status IN ('APPROVED_ACTIVE','SUSPENDED') THEN
    RETURN QUERY SELECT profile_id,public_id;
    RETURN;
  END IF;
  IF profile_status='EXITED' AND profile_exited_at IS NOT NULL THEN
    IF profile_cases<>0 THEN RETURN; END IF;
  ELSE
    RETURN;
  END IF;

  actor_scope_value := 'actor:' || p_actor_user_id::TEXT || ':therapist:' || profile_id::TEXT;
  SELECT count(*) INTO receipt_count
    FROM public.therapist_workflow_idempotency idempotency_row
    WHERE idempotency_row.actor_scope=actor_scope_value
      AND idempotency_row.operation='EXITED'
      AND idempotency_row.idempotency_key=p_idempotency_key
      AND idempotency_row.request_digest=p_request_digest;
  SELECT count(*) INTO status_count
    FROM public.therapist_status_decision status_row
    WHERE status_row.therapist_id=profile_id
      AND status_row.decision='EXITED'
      AND status_row.actor_kind='USER'
      AND status_row.actor_user_id=p_actor_user_id
      AND status_row.request_digest=p_request_digest
      AND status_row.expected_profile_version+1=profile_version;
  SELECT count(*) INTO audit_count
    FROM public.therapist_workflow_audit audit_row
    JOIN public.therapist_workflow_idempotency idempotency_row
      ON idempotency_row.actor_scope=audit_row.actor_scope
     AND idempotency_row.operation='EXITED'
     AND idempotency_row.idempotency_key=p_idempotency_key
     AND idempotency_row.request_digest=p_request_digest
    WHERE audit_row.actor_scope=actor_scope_value
      AND audit_row.action='THERAPIST_EXITED'
      AND audit_row.object_id=profile_id
      AND audit_row.result='SUCCESS'
      AND audit_row.postimage_digest=idempotency_row.postimage_digest;
  SELECT count(*) INTO outbox_count
    FROM public.therapist_workflow_outbox outbox_row
    WHERE outbox_row.event_type='THERAPIST_EXITED'
      AND outbox_row.aggregate_id=profile_id
      AND outbox_row.tenant_id=p_claimed_tenant_id;
  IF receipt_count<>1 OR status_count<>1 OR audit_count<>1 OR outbox_count<>1 THEN RETURN; END IF;
  RETURN QUERY SELECT profile_id,public_id;
END $$
"""))

    op.execute(sa.text(f"""
CREATE OR REPLACE FUNCTION public.slice3_therapist_service_currentness_v1(
  p_actor_user_id BIGINT,p_claimed_tenant_id BIGINT
) RETURNS TABLE(therapist_id UUID,tenant_public_id UUID)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE
  value_role TEXT; value_status TEXT; value_tenant BIGINT;
  value_exited TIMESTAMPTZ; value_deletion TIMESTAMPTZ; tenant_status TEXT;
  profile_id UUID; profile_tenant BIGINT; profile_status TEXT;
  qualification_id UUID; valid_until DATE; public_id UUID;
BEGIN
  IF session_user NOT IN ('{member_reader}','{case_writer}') THEN
    RAISE EXCEPTION 'SLICE3_THERAPIST_SERVICE_CURRENTNESS_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  SELECT actor_user.role::TEXT,actor_user.status::TEXT,actor_user.tenant_id,
         actor_user.exited_at,actor_user.deletion_requested_at
    INTO value_role,value_status,value_tenant,value_exited,value_deletion
    FROM public."user" actor_user WHERE actor_user.id=p_actor_user_id
    FOR SHARE OF actor_user;
  IF NOT FOUND OR value_role<>'therapist' OR value_status<>'active'
     OR value_tenant IS DISTINCT FROM p_claimed_tenant_id
     OR value_exited IS NOT NULL OR value_deletion IS NOT NULL THEN RETURN; END IF;
  SELECT tenant_row.status::TEXT INTO tenant_status FROM public.tenant tenant_row
    WHERE tenant_row.id=p_claimed_tenant_id FOR SHARE OF tenant_row;
  IF NOT FOUND OR tenant_status<>'active' THEN RETURN; END IF;
  SELECT profile_row.therapist_id,profile_row.tenant_id,profile_row.status,
         profile_row.current_qualification_version_id,profile_row.qualification_valid_until
    INTO profile_id,profile_tenant,profile_status,qualification_id,valid_until
    FROM public.therapist_profile profile_row WHERE profile_row.user_id=p_actor_user_id
    FOR SHARE OF profile_row;
  IF NOT FOUND OR profile_tenant IS DISTINCT FROM p_claimed_tenant_id
     OR profile_status<>'APPROVED_ACTIVE' OR qualification_id IS NULL
     OR valid_until IS NULL
     OR valid_until < (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Shanghai')::DATE THEN RETURN; END IF;
  BEGIN
    SELECT institution_application.tenant_public_id INTO STRICT public_id
      FROM public.institution_application institution_application
      WHERE institution_application.tenant_internal_id=p_claimed_tenant_id
        AND institution_application.status='APPROVED'
        AND institution_application.tenant_public_id IS NOT NULL;
  EXCEPTION WHEN NO_DATA_FOUND THEN RETURN;
    WHEN TOO_MANY_ROWS THEN
      RAISE EXCEPTION 'SLICE3_THERAPIST_SERVICE_CURRENTNESS_AMBIGUOUS' USING ERRCODE='21000';
  END;
  RETURN QUERY SELECT profile_id,public_id;
END $$
"""))

    op.execute(sa.text(f"""
CREATE OR REPLACE FUNCTION public.slice2_therapist_review_target_currentness_v1(
  p_actor_user_id BIGINT,p_subject_therapist_id UUID
) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE reviewer_current BOOLEAN; candidate_tenant BIGINT; tenant_status TEXT;
        locked_tenant BIGINT; public_id UUID;
BEGIN
  IF session_user <> '{reviewer}' THEN
    RAISE EXCEPTION 'SLICE2_THERAPIST_REVIEW_TARGET_CURRENTNESS_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  SELECT actor_user.role='super_admin' AND actor_user.status='active'
         AND actor_user.tenant_id IS NULL
         AND actor_user.exited_at IS NULL AND actor_user.deletion_requested_at IS NULL
    INTO reviewer_current FROM public."user" actor_user WHERE actor_user.id=p_actor_user_id
    FOR SHARE OF actor_user;
  IF COALESCE(reviewer_current,false) IS NOT TRUE THEN RETURN false; END IF;
  SELECT profile_row.tenant_id INTO candidate_tenant FROM public.therapist_profile profile_row
    WHERE profile_row.therapist_id=p_subject_therapist_id;
  IF NOT FOUND THEN RETURN false; END IF;
  SELECT tenant_row.status::TEXT INTO tenant_status FROM public.tenant tenant_row
    WHERE tenant_row.id=candidate_tenant FOR SHARE OF tenant_row;
  IF NOT FOUND OR tenant_status<>'active' THEN RETURN false; END IF;
  SELECT profile_row.tenant_id INTO locked_tenant FROM public.therapist_profile profile_row
    WHERE profile_row.therapist_id=p_subject_therapist_id FOR UPDATE OF profile_row;
  IF NOT FOUND OR locked_tenant IS DISTINCT FROM candidate_tenant THEN RETURN false; END IF;
  BEGIN
    SELECT institution_application.tenant_public_id INTO STRICT public_id
      FROM public.institution_application institution_application
      WHERE institution_application.tenant_internal_id=candidate_tenant
        AND institution_application.status='APPROVED'
        AND institution_application.tenant_public_id IS NOT NULL;
  EXCEPTION WHEN NO_DATA_FOUND THEN RETURN false;
    WHEN TOO_MANY_ROWS THEN
      RAISE EXCEPTION 'SLICE2_THERAPIST_REVIEW_TARGET_CURRENTNESS_AMBIGUOUS' USING ERRCODE='21000';
  END;
  RETURN true;
END $$
"""))

    op.execute(sa.text(f"""
CREATE OR REPLACE FUNCTION public.slice2_therapist_review_item_currentness_v1(
  p_actor_user_id BIGINT,p_review_item_id UUID
) RETURNS UUID
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE reviewer_current BOOLEAN; candidate_therapist UUID; candidate_tenant BIGINT;
        locked_tenant BIGINT; locked_therapist UUID; tenant_status TEXT; public_id UUID;
BEGIN
  IF session_user <> '{reviewer}' THEN
    RAISE EXCEPTION 'SLICE2_THERAPIST_REVIEW_ITEM_CURRENTNESS_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  SELECT review_item_row.therapist_id,profile_row.tenant_id
    INTO candidate_therapist,candidate_tenant
    FROM public.therapist_review_item review_item_row
    JOIN public.therapist_profile profile_row ON profile_row.therapist_id=review_item_row.therapist_id
    WHERE review_item_row.review_item_id=p_review_item_id;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT actor_user.role='super_admin' AND actor_user.status='active'
         AND actor_user.tenant_id IS NULL
         AND actor_user.exited_at IS NULL AND actor_user.deletion_requested_at IS NULL
    INTO reviewer_current FROM public."user" actor_user WHERE actor_user.id=p_actor_user_id
    FOR SHARE OF actor_user;
  IF COALESCE(reviewer_current,false) IS NOT TRUE THEN RETURN NULL; END IF;
  SELECT tenant_row.status::TEXT INTO tenant_status FROM public.tenant tenant_row
    WHERE tenant_row.id=candidate_tenant FOR SHARE OF tenant_row;
  IF NOT FOUND OR tenant_status<>'active' THEN RETURN NULL; END IF;
  SELECT profile_row.tenant_id INTO locked_tenant FROM public.therapist_profile profile_row
    WHERE profile_row.therapist_id=candidate_therapist FOR UPDATE OF profile_row;
  IF NOT FOUND OR locked_tenant IS DISTINCT FROM candidate_tenant THEN RETURN NULL; END IF;
  SELECT review_item_row.therapist_id INTO locked_therapist
    FROM public.therapist_review_item review_item_row
    WHERE review_item_row.review_item_id=p_review_item_id
    FOR UPDATE OF review_item_row;
  IF NOT FOUND OR locked_therapist IS DISTINCT FROM candidate_therapist THEN RETURN NULL; END IF;
  BEGIN
    SELECT institution_application.tenant_public_id INTO STRICT public_id
      FROM public.institution_application institution_application
      WHERE institution_application.tenant_internal_id=candidate_tenant
        AND institution_application.status='APPROVED'
        AND institution_application.tenant_public_id IS NOT NULL;
  EXCEPTION WHEN NO_DATA_FOUND THEN RETURN NULL;
    WHEN TOO_MANY_ROWS THEN
      RAISE EXCEPTION 'SLICE2_THERAPIST_REVIEW_ITEM_CURRENTNESS_AMBIGUOUS' USING ERRCODE='21000';
  END;
  RETURN candidate_therapist;
END $$
"""))


def _replace_profile_tenant_foreign_key() -> None:
    op.drop_constraint(
        "fk_health_profile_revision_tenant",
        "health_profile_revision",
        schema="public",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_health_profile_revision_tenant",
        "health_profile_revision",
        "institution_tenant_origin",
        ["tenant_public_id"],
        ["tenant_public_id"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
    )


def _restore_profile_tenant_foreign_key() -> None:
    op.drop_constraint(
        "fk_health_profile_revision_tenant",
        "health_profile_revision",
        schema="public",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_health_profile_revision_tenant",
        "health_profile_revision",
        "institution_application",
        ["tenant_public_id"],
        ["tenant_public_id"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
    )


def _replace_application_authorities(application_role: str) -> None:
    op.execute(sa.text(f"""
CREATE OR REPLACE FUNCTION public.slice2_institution_identity_authority_v1(value_tenant BIGINT)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE value_public UUID; value_origin TEXT; value_application UUID; value_direct UUID;
        value_tenant_status TEXT;
BEGIN
  IF session_user <> '{application_role}' THEN
    RAISE EXCEPTION 'SLICE2_INSTITUTION_AUTHORITY_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  IF value_tenant IS NULL OR value_tenant <= 0 THEN
    RAISE EXCEPTION 'SLICE2_INSTITUTION_AUTHORITY_INPUT_INVALID' USING ERRCODE='22023';
  END IF;
  BEGIN
    SELECT origin.tenant_public_id,origin.origin_type,origin.controlled_application_id,
           origin.direct_onboarding_id,tenant.status::TEXT
      INTO STRICT value_public,value_origin,value_application,value_direct,value_tenant_status
      FROM public.institution_tenant_origin origin
      JOIN public.tenant tenant ON tenant.id=origin.tenant_id
     WHERE origin.tenant_id=value_tenant
     FOR SHARE OF origin,tenant;
  EXCEPTION WHEN NO_DATA_FOUND THEN RETURN NULL;
    WHEN TOO_MANY_ROWS THEN
      RAISE EXCEPTION 'SLICE2_INSTITUTION_AUTHORITY_AMBIGUOUS' USING ERRCODE='21000';
  END;
  IF value_tenant_status<>'active' THEN RETURN NULL; END IF;
  IF value_origin='CONTROLLED_APPLICATION' THEN
    PERFORM 1 FROM public.institution_application application
     WHERE application.application_id=value_application
       AND application.tenant_internal_id=value_tenant
       AND application.tenant_public_id=value_public
       AND application.status='APPROVED';
  ELSIF value_origin='DIRECT_PROVISIONING' THEN
    PERFORM 1 FROM public.direct_institution_onboarding direct
     WHERE direct.onboarding_id=value_direct AND direct.tenant_id=value_tenant
       AND direct.tenant_public_id=value_public
       AND direct.status NOT IN ('PENDING_ACTIVATION','REVOKED_BEFORE_ACTIVATION')
     FOR SHARE OF direct;
  ELSE
    RETURN NULL;
  END IF;
  IF NOT FOUND THEN RETURN NULL; END IF;
  RETURN value_public;
END $$
"""))
    op.execute(sa.text(f"""
CREATE OR REPLACE FUNCTION public.slice3_institution_currentness_authority_v1(
  actor_user_id BIGINT,claimed_tenant_id BIGINT
) RETURNS TABLE(actor_current BOOLEAN,institution_current BOOLEAN,tenant_public_id UUID)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE SET search_path=pg_catalog,pg_temp AS $$
DECLARE value_role TEXT; value_status TEXT; value_user_tenant BIGINT;
  value_exited_at TIMESTAMPTZ; value_deletion_requested_at TIMESTAMPTZ;
  value_tenant_status TEXT; value_public UUID; value_origin TEXT;
  value_application UUID; value_direct UUID;
BEGIN
  IF session_user <> '{application_role}' THEN
    RAISE EXCEPTION 'SLICE3_INSTITUTION_CURRENTNESS_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  IF actor_user_id IS NULL OR actor_user_id<=0 OR claimed_tenant_id IS NULL OR claimed_tenant_id<=0 THEN
    RAISE EXCEPTION 'SLICE3_INSTITUTION_CURRENTNESS_INPUT_INVALID' USING ERRCODE='22023';
  END IF;
  BEGIN
    SELECT actor.role::TEXT,actor.status::TEXT,actor.tenant_id,actor.exited_at,
           actor.deletion_requested_at
      INTO STRICT value_role,value_status,value_user_tenant,value_exited_at,
                  value_deletion_requested_at
      FROM public."user" actor WHERE actor.id=actor_user_id FOR SHARE OF actor;
  EXCEPTION WHEN NO_DATA_FOUND THEN
    RETURN QUERY SELECT false,false,NULL::UUID; RETURN;
  END;
  IF value_role NOT IN ('org_admin','org_operator') OR value_status<>'active'
     OR value_exited_at IS NOT NULL OR value_deletion_requested_at IS NOT NULL
     OR value_user_tenant IS DISTINCT FROM claimed_tenant_id THEN
    RETURN QUERY SELECT false,false,NULL::UUID; RETURN;
  END IF;
  BEGIN
    SELECT tenant.status::TEXT INTO STRICT value_tenant_status FROM public.tenant tenant
     WHERE tenant.id=claimed_tenant_id FOR SHARE OF tenant;
  EXCEPTION WHEN NO_DATA_FOUND THEN
    RETURN QUERY SELECT false,false,NULL::UUID; RETURN;
  END;
  IF value_tenant_status<>'active' THEN
    RETURN QUERY SELECT false,false,NULL::UUID; RETURN;
  END IF;
  BEGIN
    SELECT origin.tenant_public_id,origin.origin_type,origin.controlled_application_id,
           origin.direct_onboarding_id
      INTO STRICT value_public,value_origin,value_application,value_direct
      FROM public.institution_tenant_origin origin
     WHERE origin.tenant_id=claimed_tenant_id FOR SHARE OF origin;
  EXCEPTION WHEN NO_DATA_FOUND THEN
    RETURN QUERY SELECT true,false,NULL::UUID; RETURN;
    WHEN TOO_MANY_ROWS THEN
      RAISE EXCEPTION 'SLICE3_INSTITUTION_CURRENTNESS_AMBIGUOUS' USING ERRCODE='21000';
  END;
  IF value_origin='CONTROLLED_APPLICATION' THEN
    PERFORM 1 FROM public.institution_application application
     WHERE application.application_id=value_application
       AND application.tenant_internal_id=claimed_tenant_id
       AND application.tenant_public_id=value_public
       AND application.status='APPROVED';
  ELSIF value_origin='DIRECT_PROVISIONING' THEN
    PERFORM 1 FROM public.direct_institution_onboarding direct
     WHERE direct.onboarding_id=value_direct AND direct.tenant_id=claimed_tenant_id
       AND direct.tenant_public_id=value_public
       AND direct.status NOT IN ('PENDING_ACTIVATION','REVOKED_BEFORE_ACTIVATION')
     FOR SHARE OF direct;
  ELSE
    RETURN QUERY SELECT true,false,NULL::UUID; RETURN;
  END IF;
  IF NOT FOUND THEN RETURN QUERY SELECT true,false,NULL::UUID; RETURN; END IF;
  RETURN QUERY SELECT true,true,value_public;
END $$
"""))


def _restore_application_authorities(application_role: str) -> None:
    op.execute(sa.text(f"""
            CREATE OR REPLACE FUNCTION public.slice2_institution_identity_authority_v1(
              value_tenant BIGINT
            ) RETURNS UUID
            LANGUAGE plpgsql SECURITY DEFINER VOLATILE
            SET search_path=pg_catalog,pg_temp AS $$
            DECLARE value_public UUID;
            BEGIN
              IF session_user <> '{application_role}' THEN
                RAISE EXCEPTION 'SLICE2_INSTITUTION_AUTHORITY_FORBIDDEN'
                  USING ERRCODE='42501';
              END IF;
              IF value_tenant IS NULL OR value_tenant <= 0 THEN
                RAISE EXCEPTION 'SLICE2_INSTITUTION_AUTHORITY_INPUT_INVALID'
                  USING ERRCODE='22023';
              END IF;
              BEGIN
                SELECT a.tenant_public_id INTO STRICT value_public
                FROM public.institution_application a
                JOIN public.tenant t ON t.id=a.tenant_internal_id
                WHERE a.tenant_internal_id=value_tenant AND a.status='APPROVED'
                  AND a.tenant_public_id IS NOT NULL AND t.status='active'
                FOR SHARE OF a,t;
              EXCEPTION
                WHEN NO_DATA_FOUND THEN RETURN NULL;
                WHEN TOO_MANY_ROWS THEN
                  RAISE EXCEPTION 'SLICE2_INSTITUTION_AUTHORITY_AMBIGUOUS'
                    USING ERRCODE='21000';
              END;
              RETURN value_public;
            END $$
"""))
    op.execute(sa.text(f"""
            CREATE OR REPLACE FUNCTION public.slice3_institution_currentness_authority_v1(
              actor_user_id BIGINT,
              claimed_tenant_id BIGINT
            )
            RETURNS TABLE (
              actor_current BOOLEAN,
              institution_current BOOLEAN,
              tenant_public_id UUID
            )
            LANGUAGE plpgsql SECURITY DEFINER VOLATILE
            SET search_path=pg_catalog,pg_temp AS $$
            DECLARE
              value_role TEXT;
              value_status TEXT;
              value_user_tenant BIGINT;
              value_exited_at TIMESTAMPTZ;
              value_deletion_requested_at TIMESTAMPTZ;
              value_tenant_status TEXT;
              value_public UUID;
            BEGIN
              IF session_user <> '{application_role}' THEN
                RAISE EXCEPTION 'SLICE3_INSTITUTION_CURRENTNESS_FORBIDDEN'
                  USING ERRCODE='42501';
              END IF;
              IF actor_user_id IS NULL OR actor_user_id <= 0
                OR claimed_tenant_id IS NULL OR claimed_tenant_id <= 0
              THEN
                RAISE EXCEPTION 'SLICE3_INSTITUTION_CURRENTNESS_INPUT_INVALID'
                  USING ERRCODE='22023';
              END IF;

              BEGIN
                SELECT u.role::TEXT,u.status::TEXT,u.tenant_id,
                       u.exited_at,u.deletion_requested_at
                  INTO STRICT value_role,value_status,value_user_tenant,
                              value_exited_at,value_deletion_requested_at
                FROM public."user" u
                WHERE u.id=actor_user_id
                FOR SHARE OF u;
              EXCEPTION
                WHEN NO_DATA_FOUND THEN
                  RETURN QUERY SELECT false,false,NULL::UUID;
                  RETURN;
              END;

              IF value_role NOT IN ('org_admin','org_operator')
                OR value_status <> 'active'
                OR value_exited_at IS NOT NULL
                OR value_deletion_requested_at IS NOT NULL
                OR value_user_tenant IS DISTINCT FROM claimed_tenant_id
              THEN
                RETURN QUERY SELECT false,false,NULL::UUID;
                RETURN;
              END IF;

              BEGIN
                SELECT t.status::TEXT INTO STRICT value_tenant_status
                FROM public.tenant t
                WHERE t.id=claimed_tenant_id
                FOR SHARE OF t;
              EXCEPTION
                WHEN NO_DATA_FOUND THEN
                  RETURN QUERY SELECT false,false,NULL::UUID;
                  RETURN;
              END;
              IF value_tenant_status <> 'active' THEN
                RETURN QUERY SELECT false,false,NULL::UUID;
                RETURN;
              END IF;

              BEGIN
                SELECT a.tenant_public_id INTO STRICT value_public
                FROM public.institution_application a
                WHERE a.tenant_internal_id=claimed_tenant_id
                  AND a.status='APPROVED'
                  AND a.tenant_public_id IS NOT NULL;
              EXCEPTION
                WHEN NO_DATA_FOUND THEN
                  RETURN QUERY SELECT true,false,NULL::UUID;
                  RETURN;
                WHEN TOO_MANY_ROWS THEN
                  RAISE EXCEPTION 'SLICE3_INSTITUTION_CURRENTNESS_AMBIGUOUS'
                    USING ERRCODE='21000';
              END;

              RETURN QUERY SELECT true,true,value_public;
            END $$
"""))


def _replace_private_file_access_snapshot(
    onboarding_reader: str, clinical_reader: str, institution_reader: str
) -> None:
    op.execute(sa.text(f"""
CREATE OR REPLACE FUNCTION public.batch_b_private_file_access_snapshot_v1(
  p_file_id UUID,p_actor_user_id BIGINT,p_context VARCHAR)
RETURNS TABLE(file_id UUID,purpose VARCHAR,owner_user_id BIGINT,status VARCHAR,
  actual_size BIGINT,actual_mime_type VARCHAR,actual_sha256 VARCHAR,
  object_key VARCHAR,bound_application_id UUID,
  qualification_bound BOOLEAN,reviewer_access BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $function$
BEGIN
  IF p_file_id IS NULL OR p_actor_user_id<1 OR p_context IS NULL THEN RETURN; END IF;
  IF (session_user='{onboarding_reader}' AND p_context NOT IN ('OWNER','REVIEWER'))
     OR (session_user='{clinical_reader}' AND p_context NOT IN (
       'FAMILY_AUTHORIZE','FAMILY_CONTENT','THERAPIST_AUTHORIZE',
       'THERAPIST_CONTENT','PLATFORM_AUTHORIZE','PLATFORM_CONTENT'))
     OR (session_user='{institution_reader}' AND p_context NOT IN (
       'INSTITUTION_AUTHORIZE','INSTITUTION_CONTENT'))
     OR session_user NOT IN ('{onboarding_reader}','{clinical_reader}','{institution_reader}') THEN
    RETURN;
  END IF;
  RETURN QUERY
  WITH candidate AS (
    SELECT file.file_id,file.purpose,file.owner_user_id,file.status,file.actual_size,
           file.actual_mime_type,file.actual_sha256,file.object_key,file.bound_application_id
      FROM public.private_file file
     WHERE file.file_id=p_file_id AND file.status='CLEAN'
       AND file.actual_size IS NOT NULL AND file.actual_mime_type IS NOT NULL
       AND file.actual_sha256 IS NOT NULL AND file.object_key IS NOT NULL
       AND length(file.object_key)>0 AND file.purpose<>'PERSONAL_DATA_EXPORT'
  ), relation AS (
    SELECT (
             EXISTS(SELECT 1 FROM public.therapist_qualification_attachment attachment
                     WHERE attachment.private_file_id=p_file_id)
             OR EXISTS(SELECT 1 FROM public.direct_institution_license license
                       WHERE license.private_file_id=p_file_id)
           ) AS qualification_bound,
           (
             EXISTS(
               SELECT 1 FROM public.therapist_qualification_attachment attachment
               JOIN public.therapist_qualification_version qualification
                 ON qualification.qualification_version_id=attachment.qualification_version_id
               JOIN public.therapist_review_item item
                 ON item.therapist_id=qualification.therapist_id
                AND item.revision_id=qualification.profile_revision_id
                AND item.status IN ('QUEUED','UNDER_REVIEW')
               WHERE attachment.private_file_id=p_file_id)
             OR EXISTS(
               SELECT 1 FROM public.direct_institution_license license
               JOIN public.direct_institution_compliance_revision revision
                 ON revision.revision_id=license.revision_id
                AND revision.status IN ('SUBMITTED','UNDER_REVIEW','NEEDS_CORRECTION','APPROVED')
               JOIN public.direct_institution_onboarding direct
                 ON direct.onboarding_id=revision.onboarding_id
                AND direct.current_revision_id=revision.revision_id
               WHERE license.private_file_id=p_file_id)
           ) AS reviewer_access
  )
  SELECT candidate.file_id,candidate.purpose,candidate.owner_user_id,candidate.status,
         candidate.actual_size::BIGINT,candidate.actual_mime_type,candidate.actual_sha256,
         candidate.object_key,candidate.bound_application_id,
         relation.qualification_bound,relation.reviewer_access
    FROM candidate CROSS JOIN relation
   WHERE (
     session_user='{onboarding_reader}' AND candidate.purpose<>'DETECTION_REPORT'
     AND EXISTS(
       SELECT 1 FROM public."user" actor
        WHERE actor.id=p_actor_user_id AND actor.status='active'
          AND ((p_context='OWNER' AND candidate.owner_user_id=p_actor_user_id)
            OR (p_context='REVIEWER' AND actor.role='super_admin'
              AND (candidate.bound_application_id IS NOT NULL OR relation.reviewer_access)))
     )
   ) OR (
     session_user IN ('{clinical_reader}','{institution_reader}')
     AND candidate.purpose='DETECTION_REPORT'
     AND public.slice4_report_file_authority_v1(
       candidate.file_id,NULL,p_actor_user_id,p_context)
   );
END;
$function$
"""))


def _restore_private_file_access_snapshot(
    onboarding_reader: str, clinical_reader: str, institution_reader: str
) -> None:
    op.execute(sa.text(f"""
CREATE OR REPLACE FUNCTION public.batch_b_private_file_access_snapshot_v1(
  p_file_id UUID,p_actor_user_id BIGINT,p_context VARCHAR)
RETURNS TABLE(file_id UUID,purpose VARCHAR,owner_user_id BIGINT,status VARCHAR,
  actual_size BIGINT,actual_mime_type VARCHAR,actual_sha256 VARCHAR,
  object_key VARCHAR,bound_application_id UUID,
  qualification_bound BOOLEAN,reviewer_access BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $function$
BEGIN
  IF p_file_id IS NULL OR p_actor_user_id<1 OR p_context IS NULL THEN RETURN; END IF;
  IF (session_user='{onboarding_reader}' AND p_context NOT IN ('OWNER','REVIEWER'))
     OR (session_user='{clinical_reader}' AND p_context NOT IN ('FAMILY_AUTHORIZE','FAMILY_CONTENT','THERAPIST_AUTHORIZE','THERAPIST_CONTENT','PLATFORM_AUTHORIZE','PLATFORM_CONTENT'))
     OR (session_user='{institution_reader}' AND p_context NOT IN ('INSTITUTION_AUTHORIZE','INSTITUTION_CONTENT'))
     OR session_user NOT IN ('{onboarding_reader}','{clinical_reader}','{institution_reader}') THEN RETURN; END IF;
  RETURN QUERY
  WITH candidate AS (
    SELECT file.file_id,file.purpose,file.owner_user_id,file.status,file.actual_size,
      file.actual_mime_type,file.actual_sha256,file.object_key,file.bound_application_id
    FROM public.private_file file WHERE file.file_id=p_file_id AND file.status='CLEAN'
      AND file.actual_size IS NOT NULL AND file.actual_mime_type IS NOT NULL
      AND file.actual_sha256 IS NOT NULL AND file.object_key IS NOT NULL
      AND length(file.object_key)>0 AND file.purpose<>'PERSONAL_DATA_EXPORT'
  ), relation AS (
    SELECT EXISTS(SELECT 1 FROM public.therapist_qualification_attachment attachment
      WHERE attachment.private_file_id=p_file_id) AS qualification_bound,
      EXISTS(SELECT 1 FROM public.therapist_qualification_attachment attachment
        JOIN public.therapist_qualification_version qualification
          ON qualification.qualification_version_id=attachment.qualification_version_id
        JOIN public.therapist_review_item item ON item.therapist_id=qualification.therapist_id
          AND item.revision_id=qualification.profile_revision_id
          AND item.status IN ('QUEUED','UNDER_REVIEW')
        WHERE attachment.private_file_id=p_file_id) AS reviewer_access
  )
  SELECT candidate.file_id,candidate.purpose,candidate.owner_user_id,candidate.status,
    candidate.actual_size::BIGINT,candidate.actual_mime_type,candidate.actual_sha256,
    candidate.object_key,candidate.bound_application_id,relation.qualification_bound,relation.reviewer_access
  FROM candidate CROSS JOIN relation
  WHERE (session_user='{onboarding_reader}' AND candidate.purpose<>'DETECTION_REPORT'
    AND EXISTS(SELECT 1 FROM public."user" actor WHERE actor.id=p_actor_user_id AND actor.status='active'
      AND ((p_context='OWNER' AND candidate.owner_user_id=p_actor_user_id)
        OR (p_context='REVIEWER' AND actor.role='super_admin'
          AND (candidate.bound_application_id IS NOT NULL OR relation.reviewer_access)))))
    OR (session_user IN ('{clinical_reader}','{institution_reader}')
      AND candidate.purpose='DETECTION_REPORT'
      AND public.slice4_report_file_authority_v1(candidate.file_id,NULL,p_actor_user_id,p_context));
END;
$function$
"""))


def upgrade() -> None:
    roles = _roles()
    _lock()
    phone_claims = _preflight_phone_claims()
    _create_tables()
    _backfill_phone_claims(phone_claims)
    _backfill_controlled_origins()
    _replace_profile_tenant_foreign_key()
    _create_functions(roles)
    op.execute(sa.text(
        'REVOKE EXECUTE ON FUNCTION public.auth_register_member_v1(VARCHAR,VARCHAR) '
        f'FROM "{roles["application"]}"'
    ))
    _replace_application_authorities(roles["application"])
    _replace_latest_currentness(roles)
    _replace_slice4_authorities(roles)
    _replace_slice5_assessment_input(roles["slice5_worker"])
    _replace_slice7_authorities(roles)
    _replace_private_file_access_snapshot(
        roles["reader"], roles["clinical_reader"], roles["institution_reader"]
    )
    _replace_slice2_readiness_interfaces()
    _replace_slice3_views()
    _replace_private_file_reference(roles["private_file_writer"])
    for table in (
        "direct_institution_onboarding", "identity_phone_claim", "institution_tenant_origin",
        "direct_institution_activation_credential", "platform_admin_security_profile",
        "direct_institution_admin_account", "direct_institution_compliance_revision",
        "direct_institution_license", "institution_admin_handoff", "institution_admin_handoff_credential",
        "direct_onboarding_receipt",
        "direct_onboarding_audit", "direct_onboarding_outbox",
    ):
        op.execute(sa.text(f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC"))


def downgrade() -> None:
    roles = _roles()
    _lock()
    connection = op.get_bind()
    nonempty = connection.execute(sa.text("""
SELECT EXISTS(
  SELECT 1 FROM public.direct_institution_onboarding
  UNION ALL SELECT 1 FROM public.identity_phone_claim WHERE claim_kind<>'EXISTING_USER'
  UNION ALL SELECT 1 FROM public.direct_institution_compliance_revision
  UNION ALL SELECT 1 FROM public.institution_admin_handoff
  UNION ALL SELECT 1 FROM public.institution_admin_handoff_credential
  UNION ALL SELECT 1 FROM public.direct_onboarding_receipt
  UNION ALL SELECT 1 FROM public.direct_onboarding_audit
  UNION ALL SELECT 1 FROM public.direct_onboarding_outbox
)
""")).scalar_one()
    if nonempty:
        raise RuntimeError("DIRECT_ONBOARDING_DOWNGRADE_NONEMPTY")
    _restore_private_file_access_snapshot(
        roles["reader"], roles["clinical_reader"], roles["institution_reader"]
    )
    _restore_slice7_authorities(roles)
    _restore_slice5_assessment_input(roles["slice5_worker"])
    _restore_slice4_authorities(roles)
    _restore_latest_currentness(roles)
    _restore_application_authorities(roles["application"])
    _restore_profile_tenant_foreign_key()
    _restore_slice2_readiness_interfaces()
    _restore_private_file_reference(roles["private_file_writer"])
    _restore_slice3_views()
    for signature in (
        "therapist_account_phone_release_v1(JSONB)",
        "therapist_account_phone_bind_v1(JSONB)",
        "therapist_account_phone_reserve_v1(JSONB)",
        "controlled_org_admin_phone_release_v1(JSONB)",
        "controlled_org_admin_phone_bind_v1(JSONB)",
        "controlled_org_admin_phone_reserve_v1(JSONB)",
        "auth_register_member_v2(UUID,VARCHAR,VARCHAR,CHAR,VARCHAR,JSONB)",
        "admin_handoff_activation_replay_v1(UUID,VARCHAR,CHAR,JSONB)",
        "admin_handoff_activation_commit_confirm_v1(JSONB)",
        "direct_recovery_claim_v1(SMALLINT,VARCHAR)",
        "direct_outbox_reopen_v1(UUID,VARCHAR,VARCHAR,BIGINT,VARCHAR)",
        "direct_outbox_consume_v1(UUID,VARCHAR,VARCHAR,BIGINT)",
        "direct_outbox_claim_v1(UUID,SMALLINT,VARCHAR)",
        "direct_org_admin_login_v1(BIGINT)",
        "direct_compliance_current_v1(BIGINT)",
        "direct_institution_read_v1(BIGINT,VARCHAR,UUID,UUID,UUID,SMALLINT,VARCHAR)",
        "direct_compliance_save_replay_v1(BIGINT,VARCHAR,VARCHAR,CHAR,JSONB)",
        "direct_compliance_save_commit_confirm_v1(JSONB)",
        "direct_compliance_submit_replay_v1(BIGINT,VARCHAR,VARCHAR,CHAR,JSONB)",
        "direct_compliance_submit_commit_confirm_v1(JSONB)",
        "direct_activation_replay_v1(UUID,VARCHAR,CHAR,JSONB)",
        "direct_activation_commit_confirm_v1(JSONB)",
        "direct_activation_authority_v1(UUID,UUID,JSONB)",
        "admin_handoff_activation_authority_v1(UUID,UUID,UUID,JSONB)",
        "direct_keyed_digest_candidate_v1(JSONB,VARCHAR)",
        "direct_review_replay_v1(JSONB)",
        "direct_review_commit_confirm_v1(JSONB)",
        "admin_handoff_activation_v1(JSONB)",
        "direct_institution_activate_v1(JSONB)",
        "institution_admin_handoff_create_v1(JSONB)",
        "institution_admin_handoff_revoke_v1(JSONB)",
        "institution_admin_handoff_regenerate_v1(JSONB)",
        "direct_compliance_decide_v1(JSONB)",
        "direct_compliance_submit_v1(JSONB)",
        "direct_compliance_save_v1(JSONB)",
        "direct_institution_revoke_v1(JSONB)",
        "direct_activation_regenerate_v1(JSONB)",
        "direct_institution_create_v1(JSONB)",
        "admin_handoff_create_step_up_begin_v1(BIGINT,UUID,VARCHAR,VARCHAR,CHAR,VARCHAR,CHAR)",
        "admin_handoff_create_step_up_failure_v1(BIGINT,UUID,VARCHAR,VARCHAR,UUID,CHAR,BIGINT,BIGINT,UUID,VARCHAR,CHAR)",
        "admin_handoff_revoke_step_up_begin_v1(BIGINT,UUID,VARCHAR,VARCHAR,CHAR)",
        "admin_handoff_revoke_step_up_failure_v1(BIGINT,UUID,VARCHAR,VARCHAR,UUID,CHAR,BIGINT,BIGINT,UUID)",
        "admin_handoff_regenerate_step_up_begin_v1(BIGINT,UUID,VARCHAR,VARCHAR,CHAR)",
        "admin_handoff_regenerate_step_up_failure_v1(BIGINT,UUID,VARCHAR,VARCHAR,UUID,CHAR,BIGINT,BIGINT,UUID)",
        "step_up_failure_commit_confirm_v1(UUID,BIGINT,UUID,CHAR)",
        "direct_compliance_decide_step_up_begin_v1(BIGINT,UUID,UUID,VARCHAR,VARCHAR,CHAR)",
        "direct_compliance_decide_step_up_failure_v1(BIGINT,UUID,UUID,VARCHAR,VARCHAR,UUID,CHAR,BIGINT,BIGINT,UUID)",
        "direct_revoke_step_up_begin_v1(BIGINT,UUID,VARCHAR,VARCHAR,CHAR)",
        "direct_revoke_step_up_failure_v1(BIGINT,UUID,VARCHAR,VARCHAR,UUID,CHAR,BIGINT,BIGINT,UUID)",
        "direct_regenerate_step_up_begin_v1(BIGINT,UUID,VARCHAR,VARCHAR,CHAR)",
        "direct_regenerate_step_up_failure_v1(BIGINT,UUID,VARCHAR,VARCHAR,UUID,CHAR,BIGINT,BIGINT,UUID)",
        "direct_create_step_up_begin_v1(BIGINT,VARCHAR,VARCHAR,CHAR,VARCHAR,CHAR)",
        "direct_create_step_up_failure_v1(BIGINT,VARCHAR,VARCHAR,UUID,CHAR,BIGINT,BIGINT,UUID,VARCHAR,CHAR)",
        "identity_phone_claim_internal_v1(JSONB)",
        "institution_controlled_origin_bind_v1(UUID)",
        "institution_tenant_origin_current_v1(BIGINT,UUID)",
    ):
        op.execute(sa.text(f"DROP FUNCTION public.{signature}"))
    op.execute(sa.text("ALTER TABLE public.direct_institution_onboarding DROP CONSTRAINT fk_direct_onboarding_current_revision"))
    for table in (
        "direct_onboarding_outbox", "direct_onboarding_audit", "direct_onboarding_receipt",
        "direct_institution_admin_account", "institution_admin_handoff_credential",
        "institution_admin_handoff", "direct_institution_license",
        "direct_institution_compliance_revision",
        "platform_admin_security_profile", "direct_institution_activation_credential",
        "institution_tenant_origin", "identity_phone_claim", "direct_institution_onboarding",
    ):
        op.execute(sa.text(f"DROP TABLE public.{table}"))
    op.execute(sa.text(
        'GRANT EXECUTE ON FUNCTION public.auth_register_member_v1(VARCHAR,VARCHAR) '
        f'TO "{roles["application"]}"'
    ))
