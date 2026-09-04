"""Close the Batch B private-file upload and one-time access boundaries.

Revision ID: 20260904_0038
Revises: 20260904_0037
"""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url

revision = "20260904_0038"
down_revision = "20260904_0037"
branch_labels = None
depends_on = None

_ACCESS_ROLE_ENV = "KG_PRIVATE_FILE_ACCESS_WRITER_ROLE"
_ACCESS_URL_ENV = "KG_PRIVATE_FILE_ACCESS_WRITER_DATABASE_URL"
_FILE_WRITER_ENV = "KG_PRIVATE_FILE_WRITER_ROLE"
_FILE_WRITER_URL_ENV = "KG_PRIVATE_FILE_WRITER_DATABASE_URL"
_ONBOARDING_READER_ENV = "KG_INSTITUTION_ONBOARDING_READER_ROLE"
_ONBOARDING_READER_URL_ENV = "KG_INSTITUTION_ONBOARDING_READER_DATABASE_URL"
_CLINICAL_READER_ENV = "KG_SLICE4_CLINICAL_READER_ROLE"
_CLINICAL_READER_URL_ENV = "KG_SLICE4_CLINICAL_READER_DATABASE_URL"
_INSTITUTION_READER_ENV = "KG_SLICE4_INSTITUTION_READER_ROLE"
_INSTITUTION_READER_URL_ENV = "KG_SLICE4_INSTITUTION_READER_DATABASE_URL"
_ISSUE_FUNCTION = (
    "public.batch_b_private_file_access_issue_v1"
    "(uuid,uuid,bigint,varchar,varchar,varchar,varchar,varchar,timestamptz,timestamptz)"
)
_CONSUME_FUNCTION = (
    "public.batch_b_private_file_access_consume_v1"
    "(uuid,uuid,bigint,varchar,varchar,timestamptz,bigint)"
)
_CONFIRM_FUNCTION = (
    "public.batch_b_private_file_access_confirm_v1"
    "(uuid,uuid,bigint,varchar,varchar,timestamptz,bigint)"
)
_SNAPSHOT_FUNCTION = (
    "public.batch_b_private_file_access_snapshot_v1(uuid,bigint,varchar)"
)
_UPLOAD_COLUMNS = (
    "upload_lease_token",
    "upload_lease_until",
    "upload_operation_ref_digest",
    "upload_version",
)


def _configuration_error() -> None:
    raise RuntimeError("Batch B private file role configuration is invalid") from None


def _validated_roles() -> tuple[str, str, str, str, str]:
    identities = (
        (_ACCESS_ROLE_ENV, _ACCESS_URL_ENV),
        (_FILE_WRITER_ENV, _FILE_WRITER_URL_ENV),
        (_ONBOARDING_READER_ENV, _ONBOARDING_READER_URL_ENV),
        (_CLINICAL_READER_ENV, _CLINICAL_READER_URL_ENV),
        (_INSTITUTION_READER_ENV, _INSTITUTION_READER_URL_ENV),
    )
    roles: list[str] = []
    targets: set[tuple[str | None, int | None, str | None]] = set()
    for role_env, url_env in identities:
        role = os.getenv(role_env, "").strip()
        raw_url = os.getenv(url_env, "").strip()
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
        roles.append(role)
        targets.add((url.host, url.port, url.database))
    if len(set(roles)) != len(identities) or len(targets) != 1:
        _configuration_error()

    connection = op.get_bind()
    if str(connection.execute(sa.text("SELECT current_user")).scalar_one()) in roles:
        _configuration_error()
    rows = connection.execute(
        sa.text(
            "SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,"
            "rolinherit,rolreplication,rolbypassrls FROM pg_roles "
            "WHERE rolname=ANY(:roles)"
        ),
        {"roles": roles},
    ).mappings().all()
    unsafe = (
        "rolsuper",
        "rolcreatedb",
        "rolcreaterole",
        "rolinherit",
        "rolreplication",
        "rolbypassrls",
    )
    if (
        len(rows) != len(identities)
        or any(row["rolcanlogin"] is not True for row in rows)
        or any(row[name] for row in rows for name in unsafe)
    ):
        _configuration_error()
    memberships = connection.execute(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM pg_auth_members membership "
            "JOIN pg_roles member_role ON member_role.oid=membership.member "
            "JOIN pg_roles granted_role ON granted_role.oid=membership.roleid "
            "WHERE member_role.rolname=ANY(:roles) "
            "OR granted_role.rolname=ANY(:roles))"
        ),
        {"roles": roles},
    ).scalar_one()
    if memberships:
        _configuration_error()
    return roles[0], roles[1], roles[2], roles[3], roles[4]


def upgrade() -> None:
    (
        access_writer,
        file_writer,
        onboarding_reader,
        clinical_reader,
        institution_reader,
    ) = _validated_roles()
    op.add_column(
        "private_file",
        sa.Column("upload_lease_token", postgresql.UUID(as_uuid=False)),
        schema="public",
    )
    op.add_column(
        "private_file",
        sa.Column("upload_lease_until", sa.DateTime(timezone=True)),
        schema="public",
    )
    op.add_column(
        "private_file",
        sa.Column("upload_operation_ref_digest", sa.String(64)),
        schema="public",
    )
    op.add_column(
        "private_file",
        sa.Column("upload_version", sa.BigInteger(), nullable=False, server_default="1"),
        schema="public",
    )
    op.create_check_constraint(
        "ck_private_file_upload_version",
        "private_file",
        "upload_version>=1",
        schema="public",
    )
    op.create_check_constraint(
        "ck_private_file_upload_lease_pair",
        "private_file",
        "(upload_lease_token IS NULL)=(upload_lease_until IS NULL)",
        schema="public",
    )
    op.create_check_constraint(
        "ck_private_file_upload_evidence",
        "private_file",
        "((actual_size IS NULL AND actual_mime_type IS NULL AND actual_sha256 IS NULL) "
        "OR (actual_size IS NOT NULL AND actual_mime_type IS NOT NULL "
        "AND actual_sha256 IS NOT NULL AND upload_lease_token IS NULL "
        "AND upload_lease_until IS NULL AND upload_operation_ref_digest IS NULL))",
        schema="public",
    )
    op.create_check_constraint(
        "ck_private_file_upload_lease_state",
        "private_file",
        "(upload_lease_token IS NULL OR (status='UPLOAD_INITIATED' "
        "AND actual_size IS NULL AND actual_mime_type IS NULL AND actual_sha256 IS NULL "
        "AND upload_operation_ref_digest ~ '^[0-9a-f]{64}$'))",
        schema="public",
    )

    op.create_table(
        "private_file_download_access",
        sa.Column("access_id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("private_file_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("access_scope", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("credential_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("authority_digest", sa.String(64), nullable=False),
        sa.Column("content_evidence_digest", sa.String(64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(
            ["private_file_id"], ["public.private_file.file_id"],
            name="fk_private_file_download_access_file",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["public.user.id"],
            name="fk_private_file_download_access_actor",
        ),
        sa.CheckConstraint(
            "access_scope IN ('OWNER','REVIEWER','REPORT')",
            name="ck_private_file_download_access_scope",
        ),
        sa.CheckConstraint(
            "reason_code IN ('OWNER_DOWNLOAD','INSTITUTION_REVIEW','DETECTION_REPORT')",
            name="ck_private_file_download_access_reason",
        ),
        sa.CheckConstraint(
            "credential_digest ~ '^[0-9a-f]{64}$' AND "
            "authority_digest ~ '^[0-9a-f]{64}$' AND "
            "content_evidence_digest ~ '^[0-9a-f]{64}$'",
            name="ck_private_file_download_access_digests",
        ),
        sa.CheckConstraint(
            "expires_at>issued_at AND expires_at<=issued_at+interval '300 seconds'",
            name="ck_private_file_download_access_expiry",
        ),
        sa.CheckConstraint(
            "version>=1 AND (consumed_at IS NULL OR "
            "(consumed_at>=issued_at AND consumed_at<=expires_at))",
            name="ck_private_file_download_access_state",
        ),
        schema="public",
    )
    op.create_index(
        "ix_private_file_download_access_expiry",
        "private_file_download_access",
        ["private_file_id", "actor_user_id", "expires_at"],
        schema="public",
    )

    op.execute(
        f"""
CREATE FUNCTION public.batch_b_private_file_access_issue_v1(
  p_access_id UUID,p_private_file_id UUID,p_actor_user_id BIGINT,
  p_access_scope VARCHAR,p_reason_code VARCHAR,p_credential_digest VARCHAR,
  p_authority_digest VARCHAR,p_content_evidence_digest VARCHAR,
  p_issued_at TIMESTAMPTZ,p_expires_at TIMESTAMPTZ)
RETURNS TABLE(result_code VARCHAR,access_version BIGINT,result_digest VARCHAR)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog
AS $function$
DECLARE stored public.private_file_download_access%ROWTYPE;
DECLARE file_row public.private_file%ROWTYPE;
DECLARE calculated_content_digest VARCHAR;
BEGIN
  IF session_user<>'{access_writer}' THEN
    RAISE EXCEPTION 'BATCH_B_PRIVATE_FILE_ACCESS_FORBIDDEN';
  END IF;
  IF p_access_id IS NULL OR p_private_file_id IS NULL OR p_actor_user_id<1
     OR p_access_scope NOT IN ('OWNER','REVIEWER','REPORT')
     OR p_reason_code NOT IN ('OWNER_DOWNLOAD','INSTITUTION_REVIEW','DETECTION_REPORT')
     OR (p_access_scope='OWNER' AND p_reason_code<>'OWNER_DOWNLOAD')
     OR (p_access_scope='REVIEWER' AND p_reason_code<>'INSTITUTION_REVIEW')
     OR (p_access_scope='REPORT' AND p_reason_code<>'DETECTION_REPORT')
     OR p_credential_digest!~'^[0-9a-f]{{64}}$'
     OR p_authority_digest!~'^[0-9a-f]{{64}}$'
     OR p_content_evidence_digest!~'^[0-9a-f]{{64}}$'
     OR p_issued_at IS NULL OR p_expires_at IS NULL
     OR p_expires_at<=p_issued_at OR p_expires_at>p_issued_at+interval '300 seconds' THEN
    RAISE EXCEPTION 'BATCH_B_PRIVATE_FILE_ACCESS_INPUT_INVALID';
  END IF;
  SELECT * INTO file_row FROM public.private_file file
   WHERE file.file_id=p_private_file_id FOR SHARE;
  IF NOT FOUND OR file_row.status<>'CLEAN' OR file_row.actual_size IS NULL
     OR file_row.actual_mime_type IS NULL OR file_row.actual_sha256 IS NULL THEN
    RAISE EXCEPTION 'BATCH_B_PRIVATE_FILE_NOT_AVAILABLE';
  END IF;
  calculated_content_digest:=encode(sha256(convert_to(
    'BATCH_B_PRIVATE_FILE_CONTENT_V1;'||file_row.file_id::text||';'||
    file_row.actual_size::text||';'||file_row.actual_mime_type||';'||
    file_row.actual_sha256,'UTF8')),'hex');
  IF calculated_content_digest<>p_content_evidence_digest THEN
    RAISE EXCEPTION 'BATCH_B_PRIVATE_FILE_EVIDENCE_MISMATCH';
  END IF;
  SELECT * INTO stored FROM public.private_file_download_access access
   WHERE access.access_id=p_access_id FOR UPDATE;
  IF FOUND THEN
    IF stored.private_file_id<>p_private_file_id OR stored.actor_user_id<>p_actor_user_id
       OR stored.access_scope<>p_access_scope OR stored.reason_code<>p_reason_code
       OR stored.credential_digest<>p_credential_digest
       OR stored.authority_digest<>p_authority_digest
       OR stored.content_evidence_digest<>p_content_evidence_digest
       OR stored.issued_at<>p_issued_at OR stored.expires_at<>p_expires_at THEN
      RAISE EXCEPTION 'BATCH_B_PRIVATE_FILE_ACCESS_CONFLICT';
    END IF;
    RETURN QUERY SELECT 'ISSUED'::VARCHAR,stored.version,
      encode(sha256(convert_to('BATCH_B_ACCESS_RESULT_V1;'||stored.access_id::text||';'||stored.version::text,'UTF8')),'hex')::VARCHAR;
    RETURN;
  END IF;
  INSERT INTO public.private_file_download_access(
    access_id,private_file_id,actor_user_id,access_scope,reason_code,
    credential_digest,authority_digest,content_evidence_digest,
    issued_at,expires_at,consumed_at,version)
  VALUES(p_access_id,p_private_file_id,p_actor_user_id,p_access_scope,p_reason_code,
    p_credential_digest,p_authority_digest,p_content_evidence_digest,
    p_issued_at,p_expires_at,NULL,1)
  RETURNING * INTO stored;
  RETURN QUERY SELECT 'ISSUED'::VARCHAR,stored.version,
    encode(sha256(convert_to('BATCH_B_ACCESS_RESULT_V1;'||stored.access_id::text||';'||stored.version::text,'UTF8')),'hex')::VARCHAR;
END;
$function$
"""
    )

    op.execute(
        f"""
CREATE FUNCTION public.batch_b_private_file_access_snapshot_v1(
  p_file_id UUID,p_actor_user_id BIGINT,p_context VARCHAR)
RETURNS TABLE(
  file_id UUID,purpose VARCHAR,owner_user_id BIGINT,status VARCHAR,
  actual_size BIGINT,actual_mime_type VARCHAR,actual_sha256 VARCHAR,
  object_key VARCHAR,bound_application_id UUID,
  qualification_bound BOOLEAN,reviewer_access BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog
AS $function$
BEGIN
  IF p_file_id IS NULL OR p_actor_user_id<1 OR p_context IS NULL THEN
    RETURN;
  END IF;
  IF (session_user='{onboarding_reader}' AND p_context NOT IN ('OWNER','REVIEWER'))
     OR (session_user='{clinical_reader}' AND p_context NOT IN (
       'FAMILY_AUTHORIZE','FAMILY_CONTENT','THERAPIST_AUTHORIZE',
       'THERAPIST_CONTENT','PLATFORM_AUTHORIZE','PLATFORM_CONTENT'))
     OR (session_user='{institution_reader}' AND p_context NOT IN (
       'INSTITUTION_AUTHORIZE','INSTITUTION_CONTENT'))
     OR session_user NOT IN (
       '{onboarding_reader}','{clinical_reader}','{institution_reader}') THEN
    RETURN;
  END IF;
  RETURN QUERY
  WITH candidate AS (
    SELECT f.file_id,f.purpose,f.owner_user_id,f.status,f.actual_size,
           f.actual_mime_type,f.actual_sha256,f.object_key,
           f.bound_application_id
      FROM public.private_file AS f
     WHERE f.file_id=p_file_id
       AND f.status='CLEAN'
       AND f.actual_size IS NOT NULL
       AND f.actual_mime_type IS NOT NULL
       AND f.actual_sha256 IS NOT NULL
       AND f.object_key IS NOT NULL
       AND length(f.object_key)>0
       AND f.purpose<>'PERSONAL_DATA_EXPORT'
  ), relation AS (
    SELECT EXISTS(
             SELECT 1 FROM public.therapist_qualification_attachment AS a
              WHERE a.private_file_id=p_file_id
           ) AS qualification_bound,
           EXISTS(
             SELECT 1
               FROM public.therapist_qualification_attachment AS a
               JOIN public.therapist_qualification_version AS q
                 ON q.qualification_version_id=a.qualification_version_id
               JOIN public.therapist_review_item AS i
                 ON i.therapist_id=q.therapist_id
                AND i.revision_id=q.profile_revision_id
                AND i.status IN ('QUEUED','UNDER_REVIEW')
              WHERE a.private_file_id=p_file_id
           ) AS reviewer_access
  )
  SELECT c.file_id,c.purpose,c.owner_user_id,c.status,c.actual_size::BIGINT,
         c.actual_mime_type,c.actual_sha256,c.object_key,
         c.bound_application_id,r.qualification_bound,r.reviewer_access
    FROM candidate AS c CROSS JOIN relation AS r
   WHERE (
     session_user='{onboarding_reader}'
     AND c.purpose<>'DETECTION_REPORT'
     AND EXISTS(
       SELECT 1 FROM public."user" AS actor
        WHERE actor.id=p_actor_user_id AND actor.status='active'
          AND (
            (p_context='OWNER' AND c.owner_user_id=p_actor_user_id)
            OR (p_context='REVIEWER' AND actor.role='super_admin'
                AND (c.bound_application_id IS NOT NULL OR r.reviewer_access))
          )
     )
   ) OR (
     session_user IN ('{clinical_reader}','{institution_reader}')
     AND c.purpose='DETECTION_REPORT'
     AND public.slice4_report_file_authority_v1(
       c.file_id,NULL,p_actor_user_id,p_context)
   );
END;
$function$
"""
    )
    op.execute(
        f"""
CREATE FUNCTION public.batch_b_private_file_access_consume_v1(
  p_access_id UUID,p_private_file_id UUID,p_actor_user_id BIGINT,
  p_credential_digest VARCHAR,p_authority_digest VARCHAR,
  p_consumed_at TIMESTAMPTZ,p_expected_version BIGINT)
RETURNS TABLE(result_code VARCHAR,access_version BIGINT,result_digest VARCHAR)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog
AS $function$
DECLARE stored public.private_file_download_access%ROWTYPE;
DECLARE file_row public.private_file%ROWTYPE;
DECLARE calculated_content_digest VARCHAR;
BEGIN
  IF session_user<>'{access_writer}' THEN
    RAISE EXCEPTION 'BATCH_B_PRIVATE_FILE_ACCESS_FORBIDDEN';
  END IF;
  IF p_access_id IS NULL OR p_private_file_id IS NULL OR p_actor_user_id<1
     OR p_credential_digest!~'^[0-9a-f]{{64}}$'
     OR p_authority_digest!~'^[0-9a-f]{{64}}$'
     OR p_consumed_at IS NULL OR p_expected_version<1 THEN
    RAISE EXCEPTION 'BATCH_B_PRIVATE_FILE_ACCESS_INPUT_INVALID';
  END IF;
  SELECT * INTO stored FROM public.private_file_download_access access
   WHERE access.access_id=p_access_id FOR UPDATE;
  IF NOT FOUND OR stored.private_file_id<>p_private_file_id
     OR stored.actor_user_id<>p_actor_user_id
     OR stored.credential_digest<>p_credential_digest
     OR stored.authority_digest<>p_authority_digest THEN
    RAISE EXCEPTION 'BATCH_B_PRIVATE_FILE_ACCESS_INVALID';
  END IF;
  IF stored.consumed_at IS NOT NULL THEN
    IF stored.consumed_at=p_consumed_at AND stored.version=p_expected_version+1 THEN
      RETURN QUERY SELECT 'CONSUMED'::VARCHAR,stored.version,
        encode(sha256(convert_to('BATCH_B_ACCESS_RESULT_V1;'||stored.access_id::text||';'||stored.version::text,'UTF8')),'hex')::VARCHAR;
      RETURN;
    END IF;
    RAISE EXCEPTION 'BATCH_B_PRIVATE_FILE_ACCESS_ALREADY_CONSUMED';
  END IF;
  IF stored.version<>p_expected_version OR p_consumed_at<stored.issued_at
     OR p_consumed_at>stored.expires_at THEN
    RAISE EXCEPTION 'BATCH_B_PRIVATE_FILE_ACCESS_INVALID';
  END IF;
  SELECT * INTO file_row FROM public.private_file file
   WHERE file.file_id=p_private_file_id FOR SHARE;
  IF NOT FOUND OR file_row.status<>'CLEAN' OR file_row.actual_size IS NULL
     OR file_row.actual_mime_type IS NULL OR file_row.actual_sha256 IS NULL THEN
    RAISE EXCEPTION 'BATCH_B_PRIVATE_FILE_NOT_AVAILABLE';
  END IF;
  calculated_content_digest:=encode(sha256(convert_to(
    'BATCH_B_PRIVATE_FILE_CONTENT_V1;'||file_row.file_id::text||';'||
    file_row.actual_size::text||';'||file_row.actual_mime_type||';'||
    file_row.actual_sha256,'UTF8')),'hex');
  IF calculated_content_digest<>stored.content_evidence_digest THEN
    RAISE EXCEPTION 'BATCH_B_PRIVATE_FILE_EVIDENCE_MISMATCH';
  END IF;
  UPDATE public.private_file_download_access access
     SET consumed_at=p_consumed_at,version=access.version+1
   WHERE access.access_id=p_access_id RETURNING * INTO stored;
  RETURN QUERY SELECT 'CONSUMED'::VARCHAR,stored.version,
    encode(sha256(convert_to('BATCH_B_ACCESS_RESULT_V1;'||stored.access_id::text||';'||stored.version::text,'UTF8')),'hex')::VARCHAR;
END;
$function$
"""
    )
    op.execute(
        f"""
CREATE FUNCTION public.batch_b_private_file_access_confirm_v1(
  p_access_id UUID,p_private_file_id UUID,p_actor_user_id BIGINT,
  p_credential_digest VARCHAR,p_authority_digest VARCHAR,
  p_consumed_at TIMESTAMPTZ,p_expected_version BIGINT)
RETURNS TABLE(result_code VARCHAR,access_version BIGINT,result_digest VARCHAR)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $function$
DECLARE stored public.private_file_download_access%ROWTYPE;
BEGIN
  IF session_user<>'{access_writer}' THEN
    RAISE EXCEPTION 'BATCH_B_PRIVATE_FILE_ACCESS_FORBIDDEN';
  END IF;
  IF p_access_id IS NULL OR p_private_file_id IS NULL OR p_actor_user_id<1
     OR p_credential_digest!~'^[0-9a-f]{{64}}$'
     OR p_authority_digest!~'^[0-9a-f]{{64}}$'
     OR p_consumed_at IS NULL OR p_expected_version<1 THEN
    RETURN QUERY SELECT 'UNKNOWN'::VARCHAR,0::BIGINT,
      encode(sha256(convert_to('BATCH_B_ACCESS_CONFIRM_UNKNOWN_V1','UTF8')),'hex')::VARCHAR;
    RETURN;
  END IF;
  SELECT * INTO stored FROM public.private_file_download_access access
   WHERE access.access_id=p_access_id;
  IF NOT FOUND THEN
    RETURN QUERY SELECT 'UNKNOWN'::VARCHAR,0::BIGINT,
      encode(sha256(convert_to('BATCH_B_ACCESS_CONFIRM_UNKNOWN_V1','UTF8')),'hex')::VARCHAR;
  ELSIF stored.private_file_id<>p_private_file_id OR stored.actor_user_id<>p_actor_user_id
     OR stored.credential_digest<>p_credential_digest
     OR stored.authority_digest<>p_authority_digest THEN
    RETURN QUERY SELECT 'UNKNOWN'::VARCHAR,stored.version,
      encode(sha256(convert_to('BATCH_B_ACCESS_CONFIRM_UNKNOWN_V1','UTF8')),'hex')::VARCHAR;
  ELSIF stored.consumed_at=p_consumed_at AND stored.version=p_expected_version+1 THEN
    RETURN QUERY SELECT 'COMMITTED'::VARCHAR,stored.version,
      encode(sha256(convert_to('BATCH_B_ACCESS_RESULT_V1;'||stored.access_id::text||';'||stored.version::text,'UTF8')),'hex')::VARCHAR;
  ELSIF stored.consumed_at IS NULL AND stored.version=p_expected_version THEN
    RETURN QUERY SELECT 'NOT_COMMITTED'::VARCHAR,stored.version,
      encode(sha256(convert_to('BATCH_B_ACCESS_RESULT_V1;'||stored.access_id::text||';'||stored.version::text,'UTF8')),'hex')::VARCHAR;
  ELSE
    RETURN QUERY SELECT 'UNKNOWN'::VARCHAR,stored.version,
      encode(sha256(convert_to('BATCH_B_ACCESS_CONFIRM_UNKNOWN_V1','UTF8')),'hex')::VARCHAR;
  END IF;
END;
$function$
"""
    )

    for signature in (_ISSUE_FUNCTION, _CONSUME_FUNCTION, _CONFIRM_FUNCTION):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f'GRANT EXECUTE ON FUNCTION {signature} TO "{access_writer}"')
    op.execute(f"REVOKE ALL ON FUNCTION {_SNAPSHOT_FUNCTION} FROM PUBLIC")
    op.execute(
        f'GRANT EXECUTE ON FUNCTION {_SNAPSHOT_FUNCTION} TO '
        f'"{onboarding_reader}","{clinical_reader}","{institution_reader}"'
    )
    op.execute(f'GRANT USAGE ON SCHEMA public TO "{access_writer}"')
    rendered = ",".join(f'"{name}"' for name in _UPLOAD_COLUMNS)
    op.execute(
        f'GRANT SELECT ({rendered}),INSERT ({rendered}),UPDATE ({rendered}) '
        f'ON TABLE public.private_file TO "{file_writer}"'
    )


def downgrade() -> None:
    (
        access_writer,
        file_writer,
        onboarding_reader,
        clinical_reader,
        institution_reader,
    ) = _validated_roles()
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE public.private_file IN ACCESS EXCLUSIVE MODE"))
    connection.execute(
        sa.text("LOCK TABLE public.private_file_download_access IN ACCESS EXCLUSIVE MODE")
    )
    blocked = connection.execute(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM public.private_file WHERE "
            "upload_lease_token IS NOT NULL OR upload_operation_ref_digest IS NOT NULL) "
            "OR EXISTS(SELECT 1 FROM public.private_file_download_access WHERE "
            "consumed_at IS NULL AND expires_at>clock_timestamp())"
        )
    ).scalar_one()
    if blocked:
        raise RuntimeError("BATCH_B_PRIVATE_FILE_DOWNGRADE_NOT_QUIESCENT") from None
    rendered = ",".join(f'"{name}"' for name in _UPLOAD_COLUMNS)
    op.execute(
        f'REVOKE SELECT ({rendered}),INSERT ({rendered}),UPDATE ({rendered}) '
        f'ON TABLE public.private_file FROM "{file_writer}"'
    )
    op.execute(
        f'REVOKE EXECUTE ON FUNCTION {_SNAPSHOT_FUNCTION} FROM '
        f'"{onboarding_reader}","{clinical_reader}","{institution_reader}"'
    )
    op.execute(f"REVOKE ALL ON FUNCTION {_SNAPSHOT_FUNCTION} FROM PUBLIC")
    op.execute(f"DROP FUNCTION {_SNAPSHOT_FUNCTION}")
    for signature in (_CONFIRM_FUNCTION, _CONSUME_FUNCTION, _ISSUE_FUNCTION):
        op.execute(f'REVOKE EXECUTE ON FUNCTION {signature} FROM "{access_writer}"')
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"DROP FUNCTION {signature}")
    op.execute(f'REVOKE USAGE ON SCHEMA public FROM "{access_writer}"')
    op.drop_index(
        "ix_private_file_download_access_expiry",
        table_name="private_file_download_access",
        schema="public",
    )
    op.drop_table("private_file_download_access", schema="public")
    for constraint in (
        "ck_private_file_upload_lease_state",
        "ck_private_file_upload_evidence",
        "ck_private_file_upload_lease_pair",
        "ck_private_file_upload_version",
    ):
        op.drop_constraint(constraint, "private_file", schema="public", type_="check")
    for column in reversed(_UPLOAD_COLUMNS):
        op.drop_column("private_file", column, schema="public")
