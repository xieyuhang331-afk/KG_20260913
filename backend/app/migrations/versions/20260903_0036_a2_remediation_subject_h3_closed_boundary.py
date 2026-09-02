"""Add the A2.2-RP subject workset and H3 closed mutation boundary.

Revision ID: 20260903_0036
Revises: 20260902_0035
"""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import make_url

revision = "20260903_0036"
down_revision = "20260902_0035"
branch_labels = None
depends_on = None

_WRITER_ROLE_ENV = "KG_A2_IDENTITY_REMEDIATION_WRITER_ROLE"
_WRITER_DATABASE_URL_ENV = "KG_A2_IDENTITY_REMEDIATION_WRITER_DATABASE_URL"
_CLASSIFICATION_RULE_HASH = (
    "9801dbb22c45cb679fdae43dd72dbf804e7f5cb4b0095d28f9d572f3d6757bc5"
)
_WORKSET_FUNCTION = (
    "identity.a2_identity_remediation_subject_workset_v1("
    "uuid,bigint,varchar,varchar,varchar)"
)
_MATERIAL_FUNCTION = (
    "identity.a2_identity_remediation_h3_material_v1("
    "uuid,uuid,bigint,varchar)"
)
_MUTATION_FUNCTION = (
    "identity.a2_identity_remediation_h3_clear_legacy_v1("
    "uuid,uuid,bigint,varchar,varchar,varchar,varchar)"
)


def _configuration_error() -> None:
    raise RuntimeError(
        "A2 identity remediation writer role configuration is invalid"
    ) from None


def _writer_role() -> str:
    role = os.getenv(_WRITER_ROLE_ENV, "").strip()
    raw_url = os.getenv(_WRITER_DATABASE_URL_ENV, "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", role) or not raw_url:
        _configuration_error()
    try:
        url = make_url(raw_url)
    except Exception:
        _configuration_error()
    if (
        url.drivername != "postgresql+asyncpg"
        or url.username != role
        or not url.password
        or not url.database
    ):
        _configuration_error()

    connection = op.get_bind()
    current_user = str(
        connection.execute(sa.text("SELECT current_user")).scalar_one()
    )
    if current_user == role:
        _configuration_error()
    row = connection.execute(
        sa.text(
            "SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolinherit,"
            "rolreplication,rolbypassrls FROM pg_roles WHERE rolname=:role"
        ),
        {"role": role},
    ).mappings().one_or_none()
    if row is None or row["rolcanlogin"] is not True:
        _configuration_error()
    if any(
        row[name]
        for name in (
            "rolsuper",
            "rolcreatedb",
            "rolcreaterole",
            "rolinherit",
            "rolreplication",
            "rolbypassrls",
        )
    ):
        _configuration_error()
    has_membership = connection.execute(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM pg_auth_members membership "
            "JOIN pg_roles member_role ON member_role.oid=membership.member "
            "JOIN pg_roles granted_role ON granted_role.oid=membership.roleid "
            "WHERE member_role.rolname=:role OR granted_role.rolname=:role)"
        ),
        {"role": role},
    ).scalar_one()
    if has_membership:
        _configuration_error()
    return role


def _facts_cte() -> str:
    return """
      facts AS (
        SELECT
          u.id::BIGINT AS subject_user_ref,
          u.role::TEXT AS role,
          (u.real_name IS NOT NULL OR u.id_card IS NOT NULL) AS legacy_pii_present,
          CASE
            WHEN u.verify_status IS NULL THEN NULL
            ELSE (
              u.verify_status = 'verified'
              OR EXISTS(
                SELECT 1 FROM public.identity_verification_submission submission
                WHERE submission.user_ref=u.id
              )
              OR EXISTS(
                SELECT 1 FROM public.identity_verification_decision decision
                WHERE decision.user_ref=u.id
              )
              OR EXISTS(
                SELECT 1 FROM identity.identity_subject_claim_registry claim
                WHERE claim.user_ref=u.id AND claim.source_kind='P1'
              )
            )
          END AS identity_authority_signal,
          CASE
            WHEN u.verify_status IS NULL THEN NULL
            ELSE EXISTS(
              SELECT 1
              FROM public.identity_verification_submission submission
              JOIN identity.identity_subject_claim_registry claim
                ON claim.user_ref=submission.user_ref
               AND claim.source_kind='P1'
               AND claim.p1_submission_id=submission.submission_id
              JOIN public.identity_verification_decision decision
                ON decision.user_ref=submission.user_ref
               AND decision.decision_ref=claim.p1_decision_ref
               AND decision.outcome='verified'
              WHERE submission.user_ref=u.id AND submission.status='verified'
            )
          END AS formal_chain_complete,
          (u.tenant_id IS NOT NULL) AS tenant_present,
          CASE
            WHEN u.role::TEXT<>'member' THEN TRUE
            WHEN u.tenant_id IS NULL THEN TRUE
            WHEN links.self_link_count IS NULL THEN FALSE
            ELSE TRUE
          END AS tenant_relation_known,
          links.self_link_count,
          enrollments.enrollment_count,
          enrollments.current_enrollment_count,
          enrollments.tenant_matches_unique_current,
          enrollments.enrollment_scope_complete
        FROM public."user" u
        LEFT JOIN LATERAL (
          SELECT count(*)::INTEGER AS self_link_count,
                 min(link.member_id::TEXT) AS member_key
          FROM identity.user_member_self_link link
          WHERE link.user_ref=u.id
        ) links ON TRUE
        LEFT JOIN LATERAL (
          SELECT
            count(*)::INTEGER AS enrollment_count,
            count(*) FILTER (
              WHERE enrollment.status IN (
                'ACCEPTED','IDENTITY_SUBMITTED','INSTITUTION_CHECKED',
                'PLATFORM_REVIEWING','NEEDS_CORRECTION','RESUBMITTED',
                'IDENTITY_VERIFIED','CONSENT_PENDING','THERAPIST_PENDING',
                'CASE_CREATED'
              )
            )::INTEGER AS current_enrollment_count,
            coalesce(bool_and(enrollment.tenant_id=u.tenant_id) FILTER (
              WHERE enrollment.status IN (
                'ACCEPTED','IDENTITY_SUBMITTED','INSTITUTION_CHECKED',
                'PLATFORM_REVIEWING','NEEDS_CORRECTION','RESUBMITTED',
                'IDENTITY_VERIFIED','CONSENT_PENDING','THERAPIST_PENDING',
                'CASE_CREATED'
              )
            ),FALSE) AS tenant_matches_unique_current,
            coalesce(bool_and(
              (enrollment.status='ACCEPTED'
               AND enrollment.current_identity_verification_id IS NULL
               AND enrollment.current_assignment_id IS NULL
               AND enrollment.service_case_id IS NULL)
              OR (enrollment.status IN (
                    'IDENTITY_SUBMITTED','INSTITUTION_CHECKED',
                    'PLATFORM_REVIEWING','NEEDS_CORRECTION','RESUBMITTED',
                    'IDENTITY_VERIFIED','CONSENT_PENDING'
                  )
                  AND enrollment.current_identity_verification_id IS NOT NULL
                  AND enrollment.current_assignment_id IS NULL
                  AND enrollment.service_case_id IS NULL)
              OR (enrollment.status='THERAPIST_PENDING'
                  AND enrollment.current_identity_verification_id IS NOT NULL
                  AND enrollment.current_assignment_id IS NOT NULL
                  AND enrollment.service_case_id IS NULL)
              OR (enrollment.status='CASE_CREATED'
                  AND enrollment.current_identity_verification_id IS NOT NULL
                  AND enrollment.current_assignment_id IS NOT NULL
                  AND enrollment.service_case_id IS NOT NULL)
            ) FILTER (
              WHERE enrollment.status IN (
                'ACCEPTED','IDENTITY_SUBMITTED','INSTITUTION_CHECKED',
                'PLATFORM_REVIEWING','NEEDS_CORRECTION','RESUBMITTED',
                'IDENTITY_VERIFIED','CONSENT_PENDING','THERAPIST_PENDING',
                'CASE_CREATED'
              )
            ),FALSE) AS enrollment_scope_complete
          FROM public.service_enrollment enrollment
          WHERE enrollment.subject_member_id::TEXT=links.member_key
        ) enrollments ON links.member_key IS NOT NULL
      ),
      classified AS (
        SELECT facts.*,
          CASE
            WHEN role<>'member' THEN 'H7'
            WHEN identity_authority_signal IS NULL
              OR formal_chain_complete IS NULL
              OR (identity_authority_signal IS TRUE
                  AND formal_chain_complete IS NOT TRUE) THEN 'H2'
            WHEN formal_chain_complete IS TRUE AND legacy_pii_present THEN 'H3'
            WHEN legacy_pii_present AND identity_authority_signal IS FALSE THEN 'H1'
            WHEN tenant_present IS NULL OR tenant_relation_known IS NOT TRUE
              OR (tenant_present IS TRUE AND (
                self_link_count<>1
                OR enrollment_count IS NULL
                OR current_enrollment_count IS NULL
                OR (enrollment_count>0 AND current_enrollment_count<>1)
                OR (current_enrollment_count=1 AND (
                  tenant_matches_unique_current IS NOT TRUE
                  OR enrollment_scope_complete IS NOT TRUE
                ))
              )) THEN 'H6'
            WHEN tenant_present IS TRUE AND tenant_relation_known IS TRUE
              AND self_link_count=1 AND enrollment_count=0 THEN 'H4'
            WHEN tenant_present IS TRUE AND tenant_relation_known IS TRUE
              AND self_link_count=1 AND enrollment_count>=1
              AND current_enrollment_count=1
              AND tenant_matches_unique_current IS TRUE
              AND enrollment_scope_complete IS TRUE THEN 'H5'
            WHEN identity_authority_signal IS NOT NULL
              AND formal_chain_complete IS NOT NULL
              AND tenant_present IS FALSE
              AND tenant_relation_known IS TRUE
              AND legacy_pii_present IS FALSE THEN 'H0'
            ELSE NULL
          END::VARCHAR AS primary_class,
          ((CASE WHEN legacy_pii_present OR identity_authority_signal IS TRUE
                 THEN 1 ELSE 0 END)
           + (CASE WHEN tenant_present IS TRUE THEN 2 ELSE 0 END)
           + (CASE WHEN identity_authority_signal IS NULL
                        OR formal_chain_complete IS NULL THEN 4 ELSE 0 END)
           + (CASE WHEN tenant_present IS NULL OR tenant_relation_known IS NOT TRUE
                        OR (tenant_present IS TRUE AND (
                          self_link_count IS NULL OR enrollment_count IS NULL
                          OR current_enrollment_count IS NULL
                          OR tenant_matches_unique_current IS NULL
                          OR enrollment_scope_complete IS NULL
                        )) THEN 8 ELSE 0 END))::SMALLINT AS secondary_flags
        FROM facts
      )
    """


def _length_prefixed_field(tag: str, value_sql: str) -> str:
    return (
        f"(CASE WHEN {value_sql} IS NULL "
        f"THEN convert_to('{tag}=N;', 'UTF8') "
        f"ELSE convert_to('{tag}=V' || "
        f"octet_length(convert_to(({value_sql})::TEXT, 'UTF8'))::TEXT || ':', "
        f"'UTF8') || convert_to(({value_sql})::TEXT, 'UTF8') || "
        "convert_to(';', 'UTF8') END)"
    )


def _workset_preimage_expression() -> str:
    fields = (
        ("subject_user_ref", "classified.subject_user_ref"),
        ("role", "classified.role"),
        ("legacy_pii_present", "classified.legacy_pii_present"),
        ("identity_authority_signal", "classified.identity_authority_signal"),
        ("formal_chain_complete", "classified.formal_chain_complete"),
        ("tenant_present", "classified.tenant_present"),
        ("tenant_relation_known", "classified.tenant_relation_known"),
        ("self_link_count", "classified.self_link_count"),
        ("enrollment_count", "classified.enrollment_count"),
        ("current_enrollment_count", "classified.current_enrollment_count"),
        (
            "tenant_matches_unique_current",
            "classified.tenant_matches_unique_current",
        ),
        ("enrollment_scope_complete", "classified.enrollment_scope_complete"),
        ("primary_class", "classified.primary_class"),
        ("secondary_flags", "classified.secondary_flags"),
    )
    return " || ".join(
        ("convert_to('A2_RP_WORKSET_PREIMAGE_V1;', 'UTF8')",)
        + tuple(_length_prefixed_field(tag, value) for tag, value in fields)
    )


def _create_workset_function(role: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION identity.a2_identity_remediation_subject_workset_v1(
          value_batch_ref UUID,
          expected_batch_version BIGINT,
          expected_batch_state_digest VARCHAR,
          expected_classification_rule_hash VARCHAR,
          expected_snapshot_ref_hash VARCHAR
        )
        RETURNS TABLE(
          subject_user_ref BIGINT,
          primary_class VARCHAR,
          secondary_flags SMALLINT,
          preimage_digest VARCHAR
        )
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        PARALLEL RESTRICTED
        SET search_path = pg_catalog, pg_temp
        AS $fn$
        DECLARE
          batch identity.identity_remediation_batch%ROWTYPE;
          work_row RECORD;
          emitted_count BIGINT:=0;
        BEGIN
          IF session_user<>'{role}' THEN
            RAISE EXCEPTION 'A2_REMEDIATION_ROLE_MISMATCH';
          END IF;
          IF value_batch_ref IS NULL OR expected_batch_version IS NULL
             OR expected_batch_version<1
             OR expected_batch_state_digest !~ '^[0-9a-f]{{64}}$'
             OR expected_classification_rule_hash !~ '^[0-9a-f]{{64}}$'
             OR expected_snapshot_ref_hash !~ '^[0-9a-f]{{64}}$' THEN
            RAISE EXCEPTION 'A2_REMEDIATION_INPUT_INVALID';
          END IF;
          SELECT * INTO batch
          FROM identity.identity_remediation_batch target
          WHERE target.batch_ref=value_batch_ref
          FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'A2_REMEDIATION_BATCH_NOT_FOUND';
          END IF;
          IF batch.version<>expected_batch_version
             OR batch.state_digest<>expected_batch_state_digest THEN
            RAISE EXCEPTION 'A2_REMEDIATION_STALE_VERSION';
          END IF;
          IF batch.status<>'PLANNED' THEN
            RAISE EXCEPTION 'A2_REMEDIATION_ILLEGAL_TRANSITION';
          END IF;
          IF lower(expected_classification_rule_hash)<>'{_CLASSIFICATION_RULE_HASH}'
             OR batch.classification_rule_hash<>lower(expected_classification_rule_hash)
             OR batch.snapshot_ref_hash<>expected_snapshot_ref_hash THEN
            RAISE EXCEPTION 'A2_REMEDIATION_MANIFEST_MISMATCH';
          END IF;

          FOR work_row IN
            WITH {_facts_cte()},
            manifest AS (
              SELECT count(*)::BIGINT AS input_count,
                     count(*) FILTER (WHERE classified.primary_class='H0')::BIGINT AS h0_count,
                     count(*) FILTER (WHERE classified.primary_class='H1')::BIGINT AS h1_count,
                     count(*) FILTER (WHERE classified.primary_class='H2')::BIGINT AS h2_count,
                     count(*) FILTER (WHERE classified.primary_class='H3')::BIGINT AS h3_count,
                     count(*) FILTER (WHERE classified.primary_class='H4')::BIGINT AS h4_count,
                     count(*) FILTER (WHERE classified.primary_class='H5')::BIGINT AS h5_count,
                     count(*) FILTER (WHERE classified.primary_class='H6')::BIGINT AS h6_count,
                     count(*) FILTER (WHERE classified.primary_class='H7')::BIGINT AS h7_count,
                     count(*) FILTER (WHERE classified.primary_class IS NULL)::BIGINT AS unclassified_count
              FROM classified
            ),
            validation AS (
              SELECT (
                manifest.unclassified_count=0
                AND manifest.input_count=batch.input_count
                AND manifest.h0_count=batch.h0_count
                AND manifest.h1_count=batch.h1_count
                AND manifest.h2_count=batch.h2_count
                AND manifest.h3_count=batch.h3_count
                AND manifest.h4_count=batch.h4_count
                AND manifest.h5_count=batch.h5_count
                AND manifest.h6_count=batch.h6_count
                AND manifest.h7_count=batch.h7_count
              ) AS manifest_valid
              FROM manifest
            )
            SELECT classified.subject_user_ref AS emitted_subject_user_ref,
                   classified.primary_class AS emitted_primary_class,
                   classified.secondary_flags AS emitted_secondary_flags,
                   encode(sha256({_workset_preimage_expression()}),'hex')::VARCHAR
                     AS emitted_preimage_digest,
                   validation.manifest_valid
            FROM classified CROSS JOIN validation
            WHERE validation.manifest_valid
            UNION ALL
            SELECT NULL::BIGINT,NULL::VARCHAR,NULL::SMALLINT,NULL::VARCHAR,FALSE
            FROM validation WHERE NOT validation.manifest_valid
            ORDER BY emitted_subject_user_ref NULLS LAST
          LOOP
            IF work_row.manifest_valid IS NOT TRUE THEN
              RAISE EXCEPTION 'A2_REMEDIATION_MANIFEST_MISMATCH';
            END IF;
            subject_user_ref:=work_row.emitted_subject_user_ref;
            primary_class:=work_row.emitted_primary_class;
            secondary_flags:=work_row.emitted_secondary_flags;
            preimage_digest:=work_row.emitted_preimage_digest;
            emitted_count:=emitted_count+1;
            RETURN NEXT;
          END LOOP;
          IF emitted_count<>batch.input_count THEN
            RAISE EXCEPTION 'A2_REMEDIATION_MANIFEST_MISMATCH';
          END IF;
          RETURN;
        END
        $fn$
        """
    )


def _create_material_function(role: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION identity.a2_identity_remediation_h3_material_v1(
          value_batch_ref UUID,
          value_item_ref UUID,
          expected_item_version BIGINT,
          expected_item_state_digest VARCHAR
        )
        RETURNS TABLE(
          subject_user_ref BIGINT,
          legacy_real_name VARCHAR,
          legacy_id_card VARCHAR,
          submission_id UUID,
          submission_version BIGINT,
          real_name_ciphertext BYTEA,
          real_name_nonce BYTEA,
          id_card_ciphertext BYTEA,
          id_card_nonce BYTEA,
          encryption_key_id VARCHAR,
          formal_content_digest VARCHAR,
          formal_id_card_digest VARCHAR,
          decision_ref UUID,
          decision_facts_version BIGINT,
          claim_id UUID,
          claim_version BIGINT,
          consent_version VARCHAR,
          chain_digest VARCHAR
        )
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        PARALLEL RESTRICTED
        SET search_path = pg_catalog, pg_temp
        AS $fn$
        DECLARE
          batch identity.identity_remediation_batch%ROWTYPE;
          item identity.identity_remediation_item%ROWTYPE;
          legacy RECORD;
          submission RECORD;
          decision RECORD;
          claim RECORD;
          row_count BIGINT;
          calculated_chain_digest VARCHAR;
        BEGIN
          IF session_user<>'{role}' THEN
            RAISE EXCEPTION 'A2_REMEDIATION_ROLE_MISMATCH';
          END IF;
          IF value_batch_ref IS NULL OR value_item_ref IS NULL
             OR expected_item_version IS NULL OR expected_item_version<1
             OR expected_item_state_digest !~ '^[0-9a-f]{{64}}$' THEN
            RAISE EXCEPTION 'A2_REMEDIATION_INPUT_INVALID';
          END IF;
          SELECT * INTO batch
          FROM identity.identity_remediation_batch target
          WHERE target.batch_ref=value_batch_ref
          FOR UPDATE;
          IF NOT FOUND OR batch.status<>'RUNNING' THEN
            RAISE EXCEPTION 'A2_REMEDIATION_ILLEGAL_TRANSITION';
          END IF;
          SELECT * INTO item
          FROM identity.identity_remediation_item target
          WHERE target.item_ref=value_item_ref AND target.batch_ref=value_batch_ref
          FOR UPDATE;
          IF NOT FOUND OR item.primary_class<>'H3'
             OR item.status NOT IN ('DISCOVERED','PROCESSING') THEN
            RAISE EXCEPTION 'A2_REMEDIATION_H3_CHAIN_INVALID';
          END IF;
          IF item.version<>expected_item_version
             OR item.state_digest<>expected_item_state_digest THEN
            RAISE EXCEPTION 'A2_REMEDIATION_STALE_VERSION';
          END IF;

          SELECT u.id,u.role::TEXT,u.real_name,u.id_card,u.tenant_id,u.verify_status
          INTO legacy
          FROM public."user" u
          WHERE u.id=item.subject_user_ref
          FOR UPDATE;
          IF NOT FOUND OR legacy.role<>'member' OR legacy.verify_status<>'verified'
             OR legacy.real_name IS NULL OR legacy.id_card IS NULL THEN
            RAISE EXCEPTION 'A2_REMEDIATION_H3_CHAIN_INVALID';
          END IF;

          SELECT count(*) INTO row_count
          FROM public.identity_verification_submission s
          WHERE s.user_ref=item.subject_user_ref AND s.status='verified';
          IF row_count<>1 THEN
            RAISE EXCEPTION 'A2_REMEDIATION_H3_CHAIN_INVALID';
          END IF;
          SELECT s.* INTO submission
          FROM public.identity_verification_submission s
          WHERE s.user_ref=item.subject_user_ref AND s.status='verified'
          FOR UPDATE;

          SELECT count(*) INTO row_count
          FROM public.identity_verification_decision d
          WHERE d.user_ref=item.subject_user_ref AND d.outcome='verified'
            AND NOT EXISTS(
              SELECT 1 FROM public.identity_verification_decision successor
              WHERE successor.supersedes_ref=d.decision_ref
            );
          IF row_count<>1 THEN
            RAISE EXCEPTION 'A2_REMEDIATION_H3_CHAIN_INVALID';
          END IF;
          SELECT d.* INTO decision
          FROM public.identity_verification_decision d
          WHERE d.user_ref=item.subject_user_ref AND d.outcome='verified'
            AND NOT EXISTS(
              SELECT 1 FROM public.identity_verification_decision successor
              WHERE successor.supersedes_ref=d.decision_ref
            )
          FOR UPDATE;

          SELECT count(*) INTO row_count
          FROM identity.identity_subject_claim_registry c
          WHERE c.user_ref=item.subject_user_ref AND c.source_kind='P1';
          IF row_count<>1 THEN
            RAISE EXCEPTION 'A2_REMEDIATION_H3_CHAIN_INVALID';
          END IF;
          SELECT c.* INTO claim
          FROM identity.identity_subject_claim_registry c
          WHERE c.user_ref=item.subject_user_ref AND c.source_kind='P1'
          FOR UPDATE;
          IF claim.p1_submission_id IS DISTINCT FROM submission.submission_id
             OR claim.p1_decision_ref IS DISTINCT FROM decision.decision_ref
             OR claim.source_facts_version IS DISTINCT FROM decision.facts_version
             OR decision.facts_version IS DISTINCT FROM submission.version
             OR claim.source_evidence_digest IS DISTINCT FROM decision.evidence_digest
             OR submission.evidence_digest IS DISTINCT FROM decision.evidence_digest
             OR claim.identity_fingerprint IS DISTINCT FROM submission.id_card_digest
             OR claim.fingerprint_key_id IS DISTINCT FROM submission.encryption_key_id
             OR submission.encryption_key_id !~ '^[A-Za-z0-9._:-]{{1,64}}$'
             OR octet_length(submission.real_name_ciphertext)=0
             OR octet_length(submission.id_card_ciphertext)=0
             OR octet_length(submission.real_name_nonce)<>12
             OR octet_length(submission.id_card_nonce)<>12
             OR submission.content_digest !~ '^[0-9a-f]{{64}}$'
             OR submission.id_card_digest !~ '^[0-9a-f]{{64}}$'
             OR submission.consent_version IS NULL
             OR char_length(submission.consent_version) NOT BETWEEN 1 AND 64 THEN
            RAISE EXCEPTION 'A2_REMEDIATION_H3_CHAIN_INVALID';
          END IF;

          calculated_chain_digest:=encode(sha256(convert_to(concat_ws('|',
            'A2_RP_H3_CHAIN_V1',value_batch_ref::TEXT,value_item_ref::TEXT,
            item.subject_user_ref::TEXT,item.status,item.version::TEXT,item.state_digest,
            submission.submission_id::TEXT,submission.version::TEXT,
            encode(submission.real_name_ciphertext,'hex'),encode(submission.real_name_nonce,'hex'),
            encode(submission.id_card_ciphertext,'hex'),encode(submission.id_card_nonce,'hex'),
            submission.encryption_key_id,submission.content_digest,submission.id_card_digest,
            submission.consent_version,decision.decision_ref::TEXT,decision.facts_version::TEXT,
            decision.evidence_digest,claim.claim_id::TEXT,claim.version::TEXT,
            claim.source_facts_version::TEXT,claim.source_evidence_digest,
            claim.identity_fingerprint,claim.fingerprint_key_id,
            legacy.real_name,legacy.id_card,coalesce(legacy.tenant_id::TEXT,'NULL')
          ),'UTF8')),'hex');

          RETURN QUERY SELECT
            item.subject_user_ref,legacy.real_name::VARCHAR,legacy.id_card::VARCHAR,
            submission.submission_id,submission.version::BIGINT,
            submission.real_name_ciphertext,submission.real_name_nonce,
            submission.id_card_ciphertext,submission.id_card_nonce,
            submission.encryption_key_id::VARCHAR,submission.content_digest::VARCHAR,
            submission.id_card_digest::VARCHAR,decision.decision_ref,
            decision.facts_version::BIGINT,claim.claim_id,claim.version::BIGINT,
            submission.consent_version::VARCHAR,calculated_chain_digest;
        END
        $fn$
        """
    )


def _create_mutation_function(role: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION identity.a2_identity_remediation_h3_clear_legacy_v1(
          value_batch_ref UUID,
          value_item_ref UUID,
          expected_item_version BIGINT,
          expected_item_state_digest VARCHAR,
          expected_chain_digest VARCHAR,
          exact_match_gate_digest VARCHAR,
          reason_code VARCHAR
        )
        RETURNS TABLE(mutation_digest VARCHAR,postimage_digest VARCHAR)
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        PARALLEL RESTRICTED
        SET search_path = pg_catalog, pg_temp
        AS $fn$
        DECLARE
          item identity.identity_remediation_item%ROWTYPE;
          material RECORD;
          calculated_mutation_digest VARCHAR;
          calculated_postimage_digest VARCHAR;
        BEGIN
          IF session_user<>'{role}' THEN
            RAISE EXCEPTION 'A2_REMEDIATION_ROLE_MISMATCH';
          END IF;
          IF expected_chain_digest !~ '^[0-9a-f]{{64}}$'
             OR exact_match_gate_digest !~ '^[0-9a-f]{{64}}$'
             OR reason_code<>'A2_CANONICAL_MATCH_APPROVED' THEN
            RAISE EXCEPTION 'A2_REMEDIATION_INPUT_INVALID';
          END IF;
          SELECT * INTO item
          FROM identity.identity_remediation_item target
          WHERE target.item_ref=value_item_ref AND target.batch_ref=value_batch_ref
          FOR UPDATE;
          IF NOT FOUND OR item.primary_class<>'H3' OR item.status<>'PROCESSING'
             OR item.version<>expected_item_version
             OR item.state_digest<>expected_item_state_digest
             OR item.eligibility_action_gate_digest<>exact_match_gate_digest THEN
            RAISE EXCEPTION 'A2_REMEDIATION_H3_CHAIN_INVALID';
          END IF;
          SELECT * INTO material
          FROM identity.a2_identity_remediation_h3_material_v1(
            value_batch_ref,value_item_ref,expected_item_version,
            expected_item_state_digest
          );
          IF material.chain_digest<>expected_chain_digest THEN
            RAISE EXCEPTION 'A2_REMEDIATION_H3_CHAIN_INVALID';
          END IF;

          UPDATE public."user"
          SET real_name=NULL, id_card=NULL
          WHERE id=item.subject_user_ref
            AND real_name=material.legacy_real_name
            AND id_card=material.legacy_id_card;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'A2_REMEDIATION_H3_CHAIN_INVALID';
          END IF;

          calculated_mutation_digest:=encode(sha256(convert_to(concat_ws('|',
            'A2_RP_H3_MUTATION_V1',value_batch_ref::TEXT,value_item_ref::TEXT,
            item.subject_user_ref::TEXT,expected_chain_digest,
            exact_match_gate_digest,reason_code
          ),'UTF8')),'hex');
          calculated_postimage_digest:=encode(sha256(convert_to(concat_ws('|',
            'A2_RP_H3_POSTIMAGE_V1',value_batch_ref::TEXT,value_item_ref::TEXT,
            item.subject_user_ref::TEXT,'legacy_real_name=NULL','legacy_id_card=NULL',
            material.submission_id::TEXT,material.submission_version::TEXT,
            material.decision_ref::TEXT,material.decision_facts_version::TEXT,
            material.claim_id::TEXT,material.claim_version::TEXT,
            material.consent_version,expected_chain_digest
          ),'UTF8')),'hex');
          RETURN QUERY SELECT calculated_mutation_digest,calculated_postimage_digest;
        END
        $fn$
        """
    )


def upgrade() -> None:
    role = _writer_role()
    quoted_role = f'"{role}"'
    _create_workset_function(role)
    _create_material_function(role)
    _create_mutation_function(role)
    for function in (_WORKSET_FUNCTION, _MATERIAL_FUNCTION, _MUTATION_FUNCTION):
        op.execute(
            f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC, {quoted_role}"
        )
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO {quoted_role}")
    op.execute(f"REVOKE CREATE ON SCHEMA public, identity FROM {quoted_role}")


def downgrade() -> None:
    role = _writer_role()
    quoted_role = f'"{role}"'
    for function in (_MUTATION_FUNCTION, _MATERIAL_FUNCTION, _WORKSET_FUNCTION):
        op.execute(f"REVOKE EXECUTE ON FUNCTION {function} FROM {quoted_role}")
        op.execute(f"DROP FUNCTION {function}")
