from __future__ import annotations

import asyncio
import copy
import hashlib
import os
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest


pytestmark = pytest.mark.integration


SLICE3_TABLES = (
    "member_service_invitation",
    "service_enrollment",
    "controlled_member_bootstrap",
    "member_identity_verification",
    "member_identity_revision",
    "member_identity_review_decision",
    "member_identity_pii_access",
    "proxy_grant",
    "consent_document_version",
    "consent_document_rendition",
    "consent_record",
    "primary_therapist_assignment",
    "service_case",
    "member_enrollment_idempotency",
    "member_enrollment_audit",
    "member_enrollment_outbox",
    "member_enrollment_delivery",
)
SLICE3_VIEWS = (
    "slice3_institution_enrollment_read_v1",
    "slice3_family_enrollment_read_v1",
    "slice3_platform_identity_review_read_v1",
    "slice3_therapist_assignment_read_v1",
    "slice3_service_case_read_v1",
)


def _roles() -> tuple[str, ...]:
    return tuple(
        os.environ[name]
        for name in (
            "KG_TEST_MEMBER_ENROLLMENT_WRITER_ROLE",
            "KG_TEST_MEMBER_IDENTITY_REVIEW_WRITER_ROLE",
            "KG_TEST_MEMBER_CASE_WRITER_ROLE",
            "KG_TEST_MEMBER_WORKFLOW_WORKER_ROLE",
            "KG_TEST_MEMBER_ENROLLMENT_READER_ROLE",
        )
    )


def _synthetic_prc_identity(birth_date: str = "19800101") -> str:
    first_seventeen = "110101" + birth_date + "001"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    check_codes = "10X98765432"
    return first_seventeen + check_codes[
        sum(int(value) * weight for value, weight in zip(first_seventeen, weights)) % 11
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("microsecond", (0, 100000, 120000, 123000, 123456))
async def test_PostgreSQL_to_jsonb_timestamp与MutationPlan固定样本一致(
    pg_database, microsecond: int
) -> None:
    from app.modules.member_enrollment.repository import MemberEnrollmentRepository

    value = datetime(2026, 8, 20, 12, 34, 56, microsecond, tzinfo=timezone.utc)
    actual = await pg_database._fetch_value(
        "SELECT to_jsonb($1::timestamptz)#>>'{}'", value
    )
    assert MemberEnrollmentRepository._json_value(value) == actual


def test_0022对象与五身份最小权限(
    pg_database,
    member_enrollment_writer_database,
    member_identity_review_writer_database,
    member_case_writer_database,
    member_workflow_worker_database,
    member_enrollment_reader_database,
):
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260913_0045"
    assert pg_database.fetch_value(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=ANY($$%s$$::text[])"
        % ("{" + ",".join(SLICE3_TABLES) + "}")
    ) == len(SLICE3_TABLES)
    assert pg_database.fetch_value(
        "SELECT count(*) FROM information_schema.views "
        "WHERE table_schema='public' AND table_name=ANY($$%s$$::text[])"
        % ("{" + ",".join(SLICE3_VIEWS) + "}")
    ) == len(SLICE3_VIEWS)

    connected = (
        member_enrollment_writer_database.fetch_value("SELECT current_user"),
        member_identity_review_writer_database.fetch_value("SELECT current_user"),
        member_case_writer_database.fetch_value("SELECT current_user"),
        member_workflow_worker_database.fetch_value("SELECT current_user"),
        member_enrollment_reader_database.fetch_value("SELECT current_user"),
    )
    assert connected == _roles()
    assert len(set(connected)) == 5

    enrollment, review, case, worker, reader = connected
    assert pg_database.fetch_value(
        f"SELECT has_column_privilege('{enrollment}','public.member_service_invitation','phone_ciphertext','INSERT')"
    )
    assert not pg_database.fetch_value(
        f"SELECT has_table_privilege('{enrollment}','public.member_enrollment_delivery','SELECT')"
    )
    assert pg_database.fetch_value(
        f"SELECT has_column_privilege('{review}','public.member_identity_review_decision','decision','INSERT')"
    )
    assert not pg_database.fetch_value(
        f"SELECT has_column_privilege('{review}','public.service_case','status','UPDATE')"
    )
    assert pg_database.fetch_value(
        f"SELECT has_column_privilege('{case}','public.service_case','status','INSERT')"
    )
    assert not pg_database.fetch_value(
        f"SELECT has_column_privilege('{case}','public.member_identity_revision','real_name_ciphertext','SELECT')"
    )
    assert pg_database.fetch_value(
        f"SELECT has_column_privilege('{worker}','public.member_enrollment_outbox','status','UPDATE')"
    )
    assert not pg_database.fetch_value(
        f"SELECT has_table_privilege('{worker}','public.member_identity_revision','SELECT')"
    )
    for view in (
        "slice3_institution_enrollment_read_v1",
        "slice3_family_enrollment_read_v1",
        "slice3_therapist_assignment_read_v1",
        "slice3_service_case_read_v1",
    ):
        assert pg_database.fetch_value(
            f"SELECT has_table_privilege('{reader}','public.{view}','SELECT')"
        )
    assert not pg_database.fetch_value(
        f"SELECT has_table_privilege('{reader}','public.slice3_platform_identity_review_read_v1','SELECT')"
    )
    for table in SLICE3_TABLES:
        assert not pg_database.fetch_value(
            f"SELECT has_table_privilege('{reader}','public.{table}','SELECT')"
        )
        assert not pg_database.fetch_value(
            f"SELECT has_table_privilege('public','public.{table}','SELECT')"
        )


def test_F1受限集合快照真实五身份正反合同(
    pg_database,
    member_enrollment_writer_database,
    member_identity_review_writer_database,
    member_case_writer_database,
    member_workflow_worker_database,
    member_enrollment_reader_database,
):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from app.core.database import dispose_slice3_runtime, get_slice3_session_factory
    from app.modules.member_enrollment import models
    from app.modules.member_enrollment.repository import MemberEnrollmentRepository

    async def exercise() -> None:
        allowed = (
            ("enrollment_writer", models.MemberIdentityRevisionModel, "verification_id", "revision_id"),
            ("enrollment_writer", models.MemberIdentityReviewDecisionModel, "verification_id", "decision_id"),
            ("enrollment_writer", models.ConsentRecordModel, "enrollment_id", "consent_record_id"),
            ("identity_review_writer", models.MemberIdentityReviewDecisionModel, "verification_id", "decision_id"),
            ("identity_review_writer", models.MemberIdentityPiiAccessModel, "verification_id", "access_id"),
            ("identity_review_writer", models.IdentitySubjectClaimRegistryModel, "slice3_revision_id", "claim_id"),
            ("identity_review_writer", models.ConsentDocumentRenditionModel, "document_version_id", "rendition_id"),
        )
        for kind, model, scope_field, key_field in allowed:
            factory = await get_slice3_session_factory(kind)
            async with factory() as session:
                session.info["slice3-mutation-plan"] = {"collections": []}
                repository = MemberEnrollmentRepository(session)
                scope_value = uuid4()
                added_key = uuid4()
                await repository._record_expected_collection(
                    model,
                    scope_field=scope_field,
                    scope_value=scope_value,
                    key_field=key_field,
                    added_key=added_key,
                )
                collection = session.info["slice3-mutation-plan"]["collections"]
                assert collection == [{
                    "table": model.__table__.name,
                    "scope": {scope_field: str(scope_value)},
                    "key_field": key_field,
                    "pre_keys": [],
                    "post_keys": [str(added_key)],
                }]
                await session.rollback()

        for kind, family, scope in (
            ("enrollment_writer", "PII_ACCESS", {"verification_id": str(uuid4())}),
            ("identity_review_writer", "IDENTITY_REVISION", {"verification_id": str(uuid4())}),
        ):
            factory = await get_slice3_session_factory(kind)
            async with factory() as session:
                with pytest.raises(DBAPIError) as denied:
                    await session.execute(text(
                        "SELECT public.slice3_collection_snapshot_v1("
                        ":family,CAST(:scope AS jsonb))"
                    ), {"family": family, "scope": json.dumps(scope)})
                assert getattr(denied.value.orig, "sqlstate", None) == "42501"
                await session.rollback()

        factory = await get_slice3_session_factory("enrollment_writer")
        for scope in ({}, {"verification_id": str(uuid4()), "extra": str(uuid4())}, {"unknown": str(uuid4())}):
            async with factory() as session:
                with pytest.raises(DBAPIError) as invalid:
                    await session.execute(text(
                        "SELECT public.slice3_collection_snapshot_v1("
                        "'IDENTITY_REVISION',CAST(:scope AS jsonb))"
                    ), {"scope": json.dumps(scope)})
                assert getattr(invalid.value.orig, "sqlstate", None) == "22023"
                await session.rollback()

        for kind in ("case_writer", "workflow_worker", "reader"):
            factory = await get_slice3_session_factory(kind)
            async with factory() as session:
                with pytest.raises(DBAPIError) as denied:
                    await session.execute(text(
                        "SELECT public.slice3_collection_snapshot_v1("
                        "'IDENTITY_REVISION',CAST(:scope AS jsonb))"
                    ), {"scope": json.dumps({"verification_id": str(uuid4())})})
                assert getattr(denied.value.orig, "sqlstate", None) == "42501"
                await session.rollback()
        for kind in (
            "enrollment_writer",
            "identity_review_writer",
            "case_writer",
            "workflow_worker",
            "reader",
        ):
            await dispose_slice3_runtime(kind)

    asyncio.run(exercise())

    signature = "public.slice3_collection_snapshot_v1(character varying,jsonb)"
    enrollment, review, case, worker, reader = _roles()
    assert pg_database.fetch_value(
        f"SELECT has_function_privilege('{enrollment}','{signature}','EXECUTE')"
    )
    assert pg_database.fetch_value(
        f"SELECT has_function_privilege('{review}','{signature}','EXECUTE')"
    )
    for role in (case, worker, reader, "public"):
        assert not pg_database.fetch_value(
            f"SELECT has_function_privilege('{role}','{signature}','EXECUTE')"
        )

    for role in _roles():
        for table in (
            "member_identity_revision",
            "member_identity_review_decision",
            "member_identity_pii_access",
            "consent_document_rendition",
            "consent_record",
        ):
            assert not pg_database.fetch_value(
                f"SELECT has_table_privilege('{role}','public.{table}','UPDATE')"
            )
    for role in _roles():
        assert not pg_database.fetch_value(
            f"SELECT has_table_privilege('{role}',"
            "'identity.identity_subject_claim_registry','UPDATE')"
        )


@pytest.mark.asyncio
async def test_F1集合快照父边界双连接串行与回滚重试(pg_database):
    del pg_database
    from app.core.database import dispose_slice3_runtime, get_slice3_session_factory
    from app.modules.member_enrollment import models
    from app.modules.member_enrollment.repository import MemberEnrollmentRepository

    factory = await get_slice3_session_factory("enrollment_writer")
    scope = uuid4()
    first_key, second_key = uuid4(), uuid4()
    async with factory() as first, factory() as second:
        first.info["slice3-mutation-plan"] = {"collections": []}
        second.info["slice3-mutation-plan"] = {"collections": []}
        await MemberEnrollmentRepository(first)._record_expected_collection(
            models.MemberIdentityRevisionModel,
            scope_field="verification_id",
            scope_value=scope,
            key_field="revision_id",
            added_key=first_key,
        )
        waiting = asyncio.create_task(
            MemberEnrollmentRepository(second)._record_expected_collection(
                models.MemberIdentityRevisionModel,
                scope_field="verification_id",
                scope_value=scope,
                key_field="revision_id",
                added_key=second_key,
            )
        )
        await asyncio.sleep(0.2)
        assert not waiting.done()
        await first.rollback()
        await asyncio.wait_for(waiting, timeout=2)
        assert second.info["slice3-mutation-plan"]["collections"][0]["post_keys"] == [
            str(second_key)
        ]
        await second.rollback()
    await dispose_slice3_runtime("enrollment_writer")


def test_R1全局实名singleton与逐身份摘要guard真实闭环(
    pg_database,
    member_enrollment_writer_database,
    member_identity_review_writer_database,
    member_case_writer_database,
    member_workflow_worker_database,
    member_enrollment_reader_database,
):
    from app.modules.member_enrollment.service import MemberEnrollmentSecrets

    assert pg_database.fetch_value(
        "SELECT count(*) FROM identity.identity_claim_algorithm_state "
        "WHERE singleton=1 AND version=1 AND "
        "fingerprint_domain='SLICE3_IDENTITY_FINGERPRINT_P1_V1'"
    ) == 1


    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.slice3_digest_algorithm_state "
        "WHERE singleton=1 AND version=1"
    ) == 1
    for role in _roles():
        assert not pg_database.fetch_value(
            f"SELECT has_table_privilege('{role}',"
            "'public.slice3_digest_algorithm_state','SELECT')"
        )

    secrets = MemberEnrollmentSecrets()
    for database, kind in (
        (member_enrollment_writer_database, "enrollment_writer"),
        (member_identity_review_writer_database, "review_writer"),
        (member_case_writer_database, "case_writer"),
        (member_workflow_worker_database, "workflow_worker"),
    ):
        payload = json.dumps(
            secrets.digest_guard_payload(kind), sort_keys=True, separators=(",", ":")
        ).replace("'", "''")
        assert database.fetch_value(
            "SELECT public.slice3_digest_algorithm_guard_v1("
            f"'{payload}'::jsonb)"
        ) is True
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        member_enrollment_reader_database.fetch_value(
            "SELECT public.slice3_digest_algorithm_guard_v1('{}'::jsonb)"
        )

    first_user, second_user = 97001, 97002
    first_decision, second_decision = uuid4(), uuid4()
    first_submission, second_submission = uuid4(), uuid4()
    fingerprint = "f" * 64
    fingerprint_key_id = secrets.fingerprint_key_id.replace("'", "''")
    pg_database.execute(
        'INSERT INTO public."user" '
        "(id,phone,password_hash,role,status,verify_status,created_at,updated_at) VALUES "
        f"({first_user},'13'||'7'||repeat('1',8),'synthetic','member','active','verified',now(),now()),"
        f"({second_user},'13'||'8'||repeat('2',8),'synthetic','member','active','verified',now(),now());"
        "INSERT INTO public.identity_verification_decision "
        "(decision_ref,user_ref,facts_version,verification_epoch,outcome,evidence_digest,"
        "actor_type,actor_ref,decided_at) VALUES "
        f"('{first_decision}',{first_user},1,1,'verified',repeat('a',64),'trusted_provider','synthetic',now()),"
        f"('{second_decision}',{second_user},1,1,'verified',repeat('b',64),'trusted_provider','synthetic',now());"
        "INSERT INTO public.identity_verification_submission "
        "(submission_id,user_ref,version,status,real_name_ciphertext,real_name_nonce,"
        "id_card_ciphertext,id_card_nonce,id_card_masked,encryption_key_id,content_digest,"
        "id_card_digest,idempotency_key_digest,consent_version,submitted_at,decided_at,"
        "reviewed_by,decision_basis_code,evidence_digest) VALUES "
        f"('{first_submission}',{first_user},1,'verified',decode('01','hex'),"
        "decode('000000000000000000000001','hex'),decode('02','hex'),"
        f"decode('000000000000000000000002','hex'),'110101********1234','{fingerprint_key_id}',"
        f"repeat('c',64),'{fingerprint}',repeat('d',64),'identity-consent-v1',now(),now(),"
        f"{first_user},'APPROVED_OFFLINE_IDENTITY_CHECK',repeat('e',64))"
    )
    assert pg_database.fetch_value(
        "SELECT source_kind FROM identity.identity_subject_claim_registry "
        f"WHERE identity_fingerprint='{fingerprint}'"
    ) == "P1"
    with pytest.raises(asyncpg.RaiseError, match="SLICE3_IDENTITY_ALREADY_CLAIMED"):
        pg_database.execute(
            "INSERT INTO public.identity_verification_submission "
            "(submission_id,user_ref,version,status,real_name_ciphertext,real_name_nonce,"
            "id_card_ciphertext,id_card_nonce,id_card_masked,encryption_key_id,content_digest,"
            "id_card_digest,idempotency_key_digest,consent_version,submitted_at,decided_at,"
            "reviewed_by,decision_basis_code,evidence_digest) VALUES "
            f"('{second_submission}',{second_user},1,'verified',decode('03','hex'),"
            "decode('000000000000000000000003','hex'),decode('04','hex'),"
            f"decode('000000000000000000000004','hex'),'110101********5678','{fingerprint_key_id}',"
            f"repeat('1',64),'{fingerprint}',repeat('2',64),'identity-consent-v1',now(),now(),"
            f"{second_user},'APPROVED_OFFLINE_IDENTITY_CHECK',repeat('3',64))"
        )
    assert pg_database.fetch_value(
        "SELECT count(*) FROM identity.identity_subject_claim_registry "
        f"WHERE identity_fingerprint='{fingerprint}'"
    ) == 1


def test_R3纯P1派生Registry可安全降级并再次升级(pg_database):
    from alembic import command
    from tests.integration.conftest import _build_alembic_config

    user_id = 97201
    decision_id = uuid4()
    submission_id = uuid4()
    pg_database.execute(
        'INSERT INTO public."user" '
        "(id,phone,password_hash,role,status,verify_status,created_at,updated_at) VALUES "
        f"({user_id},'13'||'4'||repeat('0',8),'synthetic','member','active','verified',now(),now());"
        "INSERT INTO public.identity_verification_decision("
        "decision_ref,user_ref,facts_version,verification_epoch,outcome,evidence_digest,"
        "actor_type,actor_ref,decided_at) VALUES ("
        f"'{decision_id}',{user_id},1,1,'verified',repeat('a',64),'trusted_provider','synthetic',now());"
        "INSERT INTO public.identity_verification_submission("
        "submission_id,user_ref,version,status,real_name_ciphertext,real_name_nonce,"
        "id_card_ciphertext,id_card_nonce,id_card_masked,encryption_key_id,content_digest,"
        "id_card_digest,idempotency_key_digest,consent_version,submitted_at,decided_at,"
        "reviewed_by,decision_basis_code,evidence_digest) VALUES ("
        f"'{submission_id}',{user_id},1,'verified',decode('01','hex'),"
        "decode('000000000000000000000001','hex'),decode('02','hex'),"
        "decode('000000000000000000000002','hex'),'110101********1234',"
        f"'{os.environ['KG_IDENTITY_PII_KEY_ID']}',repeat('b',64),repeat('c',64),"
        "repeat('d',64),'identity-consent-v1',now(),now(),"
        f"{user_id},'APPROVED_OFFLINE_IDENTITY_CHECK',repeat('e',64))"
    )
    assert pg_database.fetch_value(
        "SELECT count(*) FROM identity.identity_subject_claim_registry "
        f"WHERE source_kind='P1' AND user_ref={user_id} "
        f"AND p1_submission_id='{submission_id}' AND p1_decision_ref='{decision_id}'"
    ) == 1
    signature = "public.slice3_collection_snapshot_v1(character varying,jsonb)"
    assert pg_database.fetch_value(f"SELECT to_regprocedure('{signature}') IS NOT NULL")
    config = _build_alembic_config(os.environ["KG_TEST_MIGRATION_DATABASE_URL"])
    command.downgrade(config, "20260817_0021")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260817_0021"
    assert pg_database.fetch_value(f"SELECT to_regprocedure('{signature}') IS NULL")
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.identity_verification_submission WHERE submission_id='{submission_id}'"
    ) == 1
    command.upgrade(config, "20260818_0022")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260818_0022"
    assert pg_database.fetch_value(f"SELECT to_regprocedure('{signature}') IS NOT NULL")
    assert pg_database.fetch_value(
        "SELECT count(*) FROM identity.identity_subject_claim_registry "
        f"WHERE source_kind='P1' AND user_ref={user_id} "
        f"AND p1_submission_id='{submission_id}' AND p1_decision_ref='{decision_id}'"
    ) == 1
    command.upgrade(config, "head")
    assert pg_database.fetch_value(
        "SELECT version_num FROM alembic_version"
    ) == "20260913_0045"


def test_F1非空降级保留revision函数ACL与业务数据(pg_database):
    from alembic import command
    from tests.integration.conftest import _build_alembic_config

    document_id = uuid4()
    pg_database.execute(
        "INSERT INTO public.consent_document_version("
        "document_version_id,document_type,semantic_version,status,"
        "requires_reconsent,manifest_digest,created_at,version) VALUES ("
        f"'{document_id}','USER_AGREEMENT','f1-nonempty','DRAFT',true,"
        "repeat('a',64),now(),1)"
    )
    signature = "public.slice3_collection_snapshot_v1(character varying,jsonb)"
    config = _build_alembic_config(os.environ["KG_TEST_MIGRATION_DATABASE_URL"])
    command.downgrade(config, "20260818_0022")
    with pytest.raises(RuntimeError, match="Slice 3 downgrade requires empty module tables"):
        command.downgrade(config, "20260817_0021")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260818_0022"
    assert pg_database.fetch_value(f"SELECT to_regprocedure('{signature}') IS NOT NULL")
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.consent_document_version "
        f"WHERE document_version_id='{document_id}'"
    ) == 1
    pg_database.execute(
        f"DELETE FROM public.consent_document_version WHERE document_version_id='{document_id}'"
    )
    command.upgrade(config, "head")
    assert pg_database.fetch_value(
        "SELECT version_num FROM alembic_version"
    ) == "20260913_0045"


@pytest.mark.asyncio
async def test_R2三身份完整后像确认与部分提交UNKNOWN真实闭环(
    pg_database,
    member_enrollment_writer_database,
    member_identity_review_writer_database,
    member_case_writer_database,
    member_enrollment_reader_database,
):
    target_id = uuid4()
    request_id = uuid4()
    request_digest = "4" * 64
    response_digest = "5" * 64

    def planned_rows(operation: str, action: str, event: str):
        table_keys = {
            "INVITATION_CREATE": (
                ("member_service_invitation", {"invitation_id": str(target_id)}),
            ),
            "IDENTITY_REVIEW_CLAIM": (
                ("member_identity_verification", {"verification_id": str(target_id)}),
            ),
            "ASSIGNMENT_ACCEPT": (
                ("service_enrollment", {"enrollment_id": str(target_id)}),
                ("primary_therapist_assignment", {"assignment_id": str(target_id)}),
                ("service_case", {"case_id": str(target_id)}),
                ("therapist_profile", {"therapist_id": str(target_id)}),
            ),
        }[operation]
        rows = [
            {"table": table, "key": key, "value": {}}
            for table, key in table_keys
        ]
        rows.append(
            {
                "table": "member_enrollment_audit",
                "key": {
                    "request_id": str(request_id),
                    "action": action,
                    "object_id": str(target_id),
                },
                "value": {},
            }
        )
        if event:
            rows.append(
                {
                    "table": "member_enrollment_outbox",
                    "key": {"event_id": str(target_id)},
                    "value": {"payload": {"request_id": str(request_id)}},
                }
            )
        pre_rows = [
            {"table": row["table"], "key": row["key"], "value": None}
            for row in rows
        ]
        return pre_rows, rows

    async def sealed_expected(database, function_name, actor_scope, operation, key, value):
        value["receipt"]["expected_confirmed_digest"] = None
        rendered = json.dumps(value, sort_keys=True, separators=(",", ":"))
        digest = await database._fetch_value(
            f"SELECT expected_confirmed_digest FROM public.{function_name.replace('_confirm_', '_expected_')}("
            "$1::varchar,$2::varchar,$3::uuid,$4::varchar,$5::char(64),$6::jsonb)",
            actor_scope,
            operation,
            target_id,
            key,
            request_digest,
            rendered,
        )
        value["receipt"]["expected_confirmed_digest"] = digest
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    calls = (
        (
            member_enrollment_writer_database,
            "slice3_enrollment_mutation_confirm_v1",
            "user:97011:tenant:97012",
            "INVITATION_CREATE",
        ),
        (
            member_identity_review_writer_database,
            "slice3_identity_review_mutation_confirm_v1",
            "user:97013:platform",
            "IDENTITY_REVIEW_CLAIM",
        ),
        (
            member_case_writer_database,
            "slice3_case_mutation_confirm_v1",
            "user:97014:tenant:97012",
            "ASSIGNMENT_ACCEPT",
        ),
    )
    for database, function_name, actor_scope, operation in calls:
        action, event = {
            "INVITATION_CREATE": ("MEMBER_INVITATION_CREATED", "MEMBER_INVITATION_CREATED"),
            "IDENTITY_REVIEW_CLAIM": ("IDENTITY_REVIEW_CLAIMED", ""),
            "ASSIGNMENT_ACCEPT": (
                "SERVICE_CASE_PREPARING_CREATED", "SERVICE_CASE_PREPARING_CREATED"
            ),
        }[operation]
        pre_rows, post_rows = planned_rows(operation, action, event)
        expected_value = {
            "operation": operation,
            "target_id": str(target_id),
            "request_id": str(request_id),
                "preimage": {
                    "request_digest": request_digest,
                    "rows": pre_rows,
                    "collections": [],
                },
                "postimage": {"rows": post_rows, "collections": []},
            "audit": {"action": action, "expected_count": 1},
            "outbox": {"event_type": event, "expected_count": 0 if not event else 1},
            "receipt": {
                "expected_count": 1,
                "response_digest": response_digest,
                "expected_confirmed_digest": None,
            },
        }
        expected = await sealed_expected(
            database, function_name, actor_scope, operation, "r2-no-write", expected_value
        )
        outcome = await database._fetch_value(
            f"SELECT outcome FROM public.{function_name}("
            "$1::varchar,$2::varchar,$3::uuid,$4::varchar,$5::char(64),$6::jsonb)",
            actor_scope,
            operation,
            target_id,
            "r2-no-write",
            request_digest,
            expected,
        )
        assert outcome == "NOT_COMMITTED"

    actor_scope = "user:97011:tenant:97012"
    pre_rows, post_rows = planned_rows(
        "INVITATION_CREATE",
        "MEMBER_INVITATION_CREATED",
        "MEMBER_INVITATION_CREATED",
    )
    expected_value = {
        "operation": "INVITATION_CREATE",
        "target_id": str(target_id),
        "request_id": str(request_id),
        "preimage": {
            "request_digest": request_digest,
            "rows": pre_rows,
            "collections": [],
        },
        "postimage": {"rows": post_rows, "collections": []},
        "audit": {"action": "MEMBER_INVITATION_CREATED", "expected_count": 1},
        "outbox": {
            "event_type": "MEMBER_INVITATION_CREATED",
            "expected_count": 1,
        },
        "receipt": {
            "expected_count": 1,
            "response_digest": response_digest,
            "expected_confirmed_digest": None,
        },
    }
    expected = await sealed_expected(
        member_enrollment_writer_database,
        "slice3_enrollment_mutation_confirm_v1",
        actor_scope,
        "INVITATION_CREATE",
        "r2-partial",
        expected_value,
    )
    await pg_database._execute(
        "INSERT INTO public.member_enrollment_idempotency("
        "actor_scope,operation,target_id,idempotency_key,request_digest,"
        "response_ciphertext,response_key_id,postimage_digest,created_at) "
        f"VALUES('{actor_scope}','INVITATION_CREATE','{target_id}',"
        f"'r2-partial','{request_digest}',decode('01','hex'),'synthetic-k1',"
        f"'{response_digest}',now())"
    )
    outcome = await member_enrollment_writer_database._fetch_value(
        "SELECT outcome FROM public.slice3_enrollment_mutation_confirm_v1("
        "$1::varchar,$2::varchar,$3::uuid,$4::varchar,$5::char(64),$6::jsonb)",
        actor_scope,
        "INVITATION_CREATE",
        target_id,
        "r2-partial",
        request_digest,
        expected,
    )
    assert outcome == "UNKNOWN"
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await member_enrollment_reader_database._fetch_value(
            "SELECT outcome FROM public.slice3_enrollment_mutation_confirm_v1("
            "$1::varchar,$2::varchar,$3::uuid,$4::varchar,$5::char(64),$6::jsonb)",
            actor_scope,
            "INVITATION_CREATE",
            target_id,
            "r2-partial",
            request_digest,
            expected,
        )
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await member_identity_review_writer_database._fetch_value(
            "SELECT outcome FROM public.slice3_enrollment_mutation_confirm_v1("
            "$1::varchar,$2::varchar,$3::uuid,$4::varchar,$5::char(64),$6::jsonb)",
            actor_scope,
            "INVITATION_CREATE",
            target_id,
            "r2-partial",
            request_digest,
            expected,
        )


@pytest.mark.asyncio
async def test_SELF邀请接受使用真实Writer并可由Reader读取(
    pg_database,
    member_enrollment_writer_database,
    member_identity_review_writer_database,
    member_case_writer_database,
    member_enrollment_reader_database,
    real_db_client,
):
    from app.core.database import dispose_slice3_runtime, get_slice3_session_factory
    from app.core.security import CurrentUser, create_access_token
    from app.modules.member_enrollment.domain import MemberEnrollmentConflict
    from app.modules.member_enrollment.repository import MemberEnrollmentRepository
    from app.modules.member_enrollment.schemas import (
        AcceptEnrollmentRequest,
        ConsentRenditionInput,
        CreateAssignmentRequest,
        CreateConsentDocumentRequest,
        CreateMemberInvitationRequest,
        IdentitySubmissionRequest,
        InstitutionIdentityCheckRequest,
        PiiAccessRequest,
        PlatformIdentityDecisionRequest,
        PublishConsentDocumentRequest,
        RecordConsentRequest,
    )
    from app.modules.member_enrollment.service import (
        MemberEnrollmentSecrets,
        MemberEnrollmentService,
        MutationContext,
        reviewer_credential_proof,
    )
    from app.core.uuid_generator import Uuid7Generator

    tenant_id = 96001
    user_id = 96002
    uuid7 = Uuid7Generator()
    member_id = uuid7.generate()
    tenant_public_id = uuid7.generate()
    institution_invitation_id = uuid4()
    institution_application_id = uuid4()
    await pg_database._execute(
        "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) "
        "VALUES (96003,NULL,'Slice3 county','SLICE3-COUNTY','county','active',1);"
        "INSERT INTO public.tenant(id,org_id,tenant_code,name,type,province,city,status,created_at,updated_at) "
        f"VALUES ({tenant_id},96003,'SLICE3-TENANT','Slice3 disposable','store','test','test','active',now(),now());"
        "INSERT INTO public.\"user\"(id,phone,password_hash,role,status,tenant_id) "
        f"VALUES ({user_id},'13' || '7' || repeat('0',8),'test-only','member','active',NULL),"
        f"({user_id + 2},'13' || '6' || repeat('0',8),'test-only','org_admin','active',{tenant_id}),"
        f"({user_id + 3},'13' || '5' || repeat('0',8),'test-only','super_admin','active',NULL);"
        "INSERT INTO identity.member(member_id,member_no,creation_source,status,version,created_at,updated_at) "
        f"VALUES ('{member_id}','M0123456789ABCDEFGHJK','registration','created',1,now(),now());"
        "INSERT INTO public.institution_invitation("
        "invitation_id,institution_name,institution_type,applicant_phone_ciphertext,applicant_phone_digest,"
        "pilot_batch_code,administrative_region_id,code_digest,status,failed_attempts,expires_at,issued_by,issued_at,activated_at,version) VALUES ("
        f"'{institution_invitation_id}','Slice3 disposable','HEALTH_STORE',decode('00','hex'),repeat('a',64),"
        f"'SLICE3',96003,repeat('b',64),'ACTIVATED',0,now()+interval '1 day',{user_id},now(),now(),1);"
        "INSERT INTO public.institution_application("
        "application_id,invitation_id,applicant_user_id,institution_type,status,draft_payload,correction_fields,"
        "current_revision_no,tenant_internal_id,tenant_public_id,service_ready,created_at,updated_at,submitted_at,reviewed_at,version) VALUES ("
        f"'{institution_application_id}','{institution_invitation_id}',{user_id},'HEALTH_STORE','APPROVED',"
        f"'{{\"service_tags\":[\"GLUCOSE_METABOLISM\"]}}'::jsonb,'[]'::jsonb,1,{tenant_id},"
        f"'{tenant_public_id}',false,now(),now(),now(),now(),3);"
        "INSERT INTO public.institution_tenant_origin(tenant_id,tenant_public_id,origin_type,"
        "controlled_application_id) VALUES ("
        f"{tenant_id},'{tenant_public_id}','CONTROLLED_APPLICATION','{institution_application_id}');"
        "INSERT INTO public.institution_service_readiness("
        "tenant_id,readiness_status,reason_codes,qualified_therapist_count,computed_at,"
        "evidence_version,input_digest,result_digest,source_versions,next_expiry_at,version) VALUES ("
        f"{tenant_id},'SERVICE_READY',ARRAY[]::text[],1,now(),1,repeat('c',64),repeat('d',64),"
        "'{}'::jsonb,current_date+30,1)"
    )
    actor = CurrentUser(id=user_id, role="member", tenant_id=tenant_id)
    phone = "13" + "7" + ("0" * 8)
    factory = await get_slice3_session_factory("enrollment_writer")
    secrets = MemberEnrollmentSecrets()
    try:
        async with factory() as session:
            session.info["slice3-mutation-plan"] = {"pre_rows": [], "rows": []}
            service = MemberEnrollmentService(
                MemberEnrollmentRepository(session), secrets_port=secrets
            )
            create_context = MutationContext(
                actor=actor,
                tenant_id=tenant_id,
                tenant_public_id=tenant_public_id,
                idempotency_key="slice3-self-create",
                request_id=uuid4(),
            )
            invitation_id, short_code = await service.create_invitation(
                create_context,
                CreateMemberInvitationRequest(mode="SELF", phone=phone),
            )
            plan = session.info.pop("slice3-mutation-plan")
            assert {row["table"] for row in plan["rows"]} == {
                "member_service_invitation",
                "member_enrollment_audit",
                "member_enrollment_outbox",
            }
            request_value = {
                "target_id": None,
                "payload": {"mode": "SELF", "phone": phone},
            }
            request_digest = secrets.request_digest(request_value)
            safe_result = {"invitation_id": str(invitation_id), "status": "INVITED"}
            response_digest = secrets.audit_digest(
                {"operation": "INVITATION_CREATE", "result": safe_result}
            )
            response_ciphertext, response_key_id = secrets.encrypt_replay(
                safe_result,
                actor_scope=create_context.actor_scope,
                operation="INVITATION_CREATE",
                target_id=invitation_id,
                key=create_context.idempotency_key,
            )
            receipt_created_at = datetime.now(timezone.utc)
            expected = {
                "operation": "INVITATION_CREATE",
                "target_id": str(invitation_id),
                "request_id": str(create_context.request_id),
                    "preimage": {
                        "request_digest": request_digest,
                        "rows": plan["pre_rows"],
                        "collections": [],
                    },
                    "postimage": {"rows": plan["rows"], "collections": []},
                "audit": {
                    "action": "MEMBER_INVITATION_CREATED",
                    "expected_count": 1,
                },
                "outbox": {
                    "event_type": "MEMBER_INVITATION_CREATED",
                    "expected_count": 1,
                },
                "receipt": {
                    "expected_count": 1,
                    "response_digest": response_digest,
                    "actor_scope": create_context.actor_scope,
                    "operation": "INVITATION_CREATE",
                    "target_id": str(invitation_id),
                    "idempotency_key": create_context.idempotency_key,
                    "request_digest": request_digest,
                    "response_key_id": response_key_id,
                    "response_ciphertext_sha256": hashlib.sha256(
                        response_ciphertext
                    ).hexdigest(),
                    "created_at": receipt_created_at,
                    "expected_confirmed_digest": None,
                },
            }
            repository = MemberEnrollmentRepository(session)
            expected_digest = await repository.expected_mutation_postimage(
                "enrollment_writer",
                actor_scope=create_context.actor_scope,
                operation="INVITATION_CREATE",
                target_id=invitation_id,
                idempotency_key=create_context.idempotency_key,
                request_digest=request_digest,
                expected_postimage=expected,
            )
            expected["receipt"]["expected_confirmed_digest"] = expected_digest
            await repository.record_idempotency(
                create_context.actor_scope,
                "INVITATION_CREATE",
                invitation_id,
                create_context.idempotency_key,
                request_digest,
                response_ciphertext,
                response_key_id,
                response_digest,
                receipt_created_at,
            )
            await session.commit()

        for planned_row in plan["rows"]:
            rendered_value = json.dumps(
                planned_row["value"], sort_keys=True, separators=(",", ":")
            )
            if planned_row["table"] == "member_service_invitation":
                matches = await pg_database._fetch_value(
                    "SELECT to_jsonb(x)=$1::jsonb FROM public.member_service_invitation x "
                    "WHERE x.invitation_id=$2",
                    rendered_value,
                    invitation_id,
                )
            elif planned_row["table"] == "member_enrollment_audit":
                matches = await pg_database._fetch_value(
                    "SELECT to_jsonb(x)-'audit_id'=$1::jsonb "
                    "FROM public.member_enrollment_audit x "
                    "WHERE x.request_id=$2 AND x.action=$3 AND x.object_id=$4",
                    rendered_value,
                    create_context.request_id,
                    "MEMBER_INVITATION_CREATED",
                    invitation_id,
                )
            else:
                matches = await pg_database._fetch_value(
                    "SELECT to_jsonb(x)=$1::jsonb FROM public.member_enrollment_outbox x "
                    "WHERE x.event_id=$2",
                    rendered_value,
                    UUID(planned_row["key"]["event_id"]),
                )
            assert matches is True, planned_row["table"]

        receipt_checks = await pg_database._fetch_rows(
            "SELECT "
            "actor_scope=$1 AS actor_scope_ok, operation=$2 AS operation_ok, "
            "target_id=$3 AS target_id_ok, idempotency_key=$4 AS key_ok, "
            "request_digest=$5 AS request_digest_ok, postimage_digest=$6 AS response_digest_ok, "
            "response_key_id=$7 AS response_key_ok, "
            "encode(sha256(response_ciphertext),'hex')=$8 AS ciphertext_digest_ok, "
            "created_at=$9 AS created_at_ok "
            "FROM public.member_enrollment_idempotency "
            "WHERE actor_scope=$1 AND operation=$2 AND target_id=$3 "
            "AND idempotency_key=$4 AND request_digest=$5",
            create_context.actor_scope,
            "INVITATION_CREATE",
            invitation_id,
            create_context.idempotency_key,
            request_digest,
            response_digest,
            response_key_id,
            hashlib.sha256(response_ciphertext).hexdigest(),
            receipt_created_at,
        )
        assert len(receipt_checks) == 1
        assert all(receipt_checks[0].values()), {
            key: value for key, value in receipt_checks[0].items() if not value
        }

        async with factory() as session:
            confirmed = await MemberEnrollmentRepository(session).confirm_mutation_outcome(
                "enrollment_writer",
                actor_scope=create_context.actor_scope,
                operation="INVITATION_CREATE",
                target_id=invitation_id,
                idempotency_key=create_context.idempotency_key,
                request_digest=request_digest,
                expected_postimage=expected,
            )
            assert confirmed["outcome"] == "COMMITTED"
            assert confirmed["confirmed_postimage_digest"] == expected_digest
            await session.rollback()

        missing_row = copy.deepcopy(expected)
        missing_row["preimage"]["rows"] = [
            row
            for row in missing_row["preimage"]["rows"]
            if row["table"] != "member_service_invitation"
        ]
        missing_row["postimage"]["rows"] = [
            row
            for row in missing_row["postimage"]["rows"]
            if row["table"] != "member_service_invitation"
        ]
        missing_row["receipt"]["expected_confirmed_digest"] = None
        with pytest.raises(
            asyncpg.RaiseError, match="^SLICE3_MUTATION_EXPECTED_INVALID$"
        ):
            await member_enrollment_writer_database._fetch_value(
                "SELECT expected_confirmed_digest FROM "
                "public.slice3_enrollment_mutation_expected_v1("
                "$1,$2,$3,$4,$5,$6::jsonb)",
                create_context.actor_scope,
                "INVITATION_CREATE",
                invitation_id,
                create_context.idempotency_key,
                request_digest,
                json.dumps(
                    MemberEnrollmentRepository._json_value(missing_row),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )

        tampered = copy.deepcopy(expected)
        invitation_row = next(
            row
            for row in tampered["postimage"]["rows"]
            if row["table"] == "member_service_invitation"
        )
        invitation_row["value"]["version"] += 1
        tampered["receipt"]["expected_confirmed_digest"] = None
        tampered_digest = await member_enrollment_writer_database._fetch_value(
            "SELECT expected_confirmed_digest FROM "
            "public.slice3_enrollment_mutation_expected_v1("
            "$1,$2,$3,$4,$5,$6::jsonb)",
            create_context.actor_scope,
            "INVITATION_CREATE",
            invitation_id,
            create_context.idempotency_key,
            request_digest,
            json.dumps(
                MemberEnrollmentRepository._json_value(tampered),
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        tampered["receipt"]["expected_confirmed_digest"] = tampered_digest
        assert await member_enrollment_writer_database._fetch_value(
            "SELECT outcome FROM public.slice3_enrollment_mutation_confirm_v1("
            "$1,$2,$3,$4,$5,$6::jsonb)",
            create_context.actor_scope,
            "INVITATION_CREATE",
            invitation_id,
            create_context.idempotency_key,
            request_digest,
            json.dumps(
                MemberEnrollmentRepository._json_value(tampered),
                sort_keys=True,
                separators=(",", ":"),
            ),
        ) == "UNKNOWN"

        extra_event_id = uuid4()
        await pg_database._execute(
            "INSERT INTO public.member_enrollment_outbox("
            "event_id,event_type,aggregate_id,tenant_id,payload,payload_digest,status,attempts,"
            "processing_at,lease_owner,delivered_at,failed_at,created_at,version) VALUES ("
            f"'{extra_event_id}','MEMBER_INVITATION_CREATED','{invitation_id}',{tenant_id},"
            f"'{{\"v\":1,\"event_id\":\"{extra_event_id}\",\"aggregate_id\":\"{invitation_id}\","
            f"\"request_id\":\"{create_context.request_id}\"}}'::jsonb,repeat('9',64),"
            "'PENDING',0,NULL,NULL,NULL,NULL,now(),1)"
        )
        assert await member_enrollment_writer_database._fetch_value(
            "SELECT outcome FROM public.slice3_enrollment_mutation_confirm_v1("
            "$1,$2,$3,$4,$5,$6::jsonb)",
            create_context.actor_scope,
            "INVITATION_CREATE",
            invitation_id,
            create_context.idempotency_key,
            request_digest,
            json.dumps(
                MemberEnrollmentRepository._json_value(expected),
                sort_keys=True,
                separators=(",", ":"),
            ),
        ) == "UNKNOWN"
        await pg_database._execute(
            f"DELETE FROM public.member_enrollment_outbox WHERE event_id='{extra_event_id}'"
        )

        async with factory() as session:
            service = MemberEnrollmentService(
                MemberEnrollmentRepository(session), secrets_port=secrets
            )
            accept_context = MutationContext(
                actor=actor,
                tenant_id=tenant_id,
                tenant_public_id=tenant_public_id,
                idempotency_key="slice3-self-accept",
                request_id=uuid4(),
            )
            enrollment_id, subject_member_id = await service.accept_invitation(
                accept_context,
                AcceptEnrollmentRequest(
                    invitation_id=invitation_id,
                    phone=phone,
                    short_code=short_code,
                ),
                actor_member_id=member_id,
            )
            await session.commit()

        async with factory() as session:
            service = MemberEnrollmentService(
                MemberEnrollmentRepository(session), secrets_port=secrets
            )
            family_context = MutationContext(
                actor=actor,
                tenant_id=tenant_id,
                tenant_public_id=tenant_public_id,
                idempotency_key="slice3-self-identity",
                request_id=uuid4(),
            )
            verification_id, revision_id = await service.submit_identity(
                family_context,
                enrollment_id,
                IdentitySubmissionRequest(
                    document_type="PRC_RESIDENT_ID",
                    real_name="Synthetic Member",
                    id_number=_synthetic_prc_identity(),
                    expected_version=1,
                ),
                submitted_by_member_id=member_id,
            )
            await session.commit()

        async with factory() as session:
            service = MemberEnrollmentService(
                MemberEnrollmentRepository(session), secrets_port=secrets
            )
            institution_context = MutationContext(
                actor=CurrentUser(
                    id=user_id + 2, role="org_admin", tenant_id=tenant_id
                ),
                tenant_id=tenant_id,
                tenant_public_id=tenant_public_id,
                idempotency_key="slice3-self-institution-check",
                request_id=uuid4(),
            )
            await service.institution_identity_check(
                institution_context,
                verification_id,
                InstitutionIdentityCheckRequest(
                    revision_id=revision_id,
                    decision="CHECKED",
                    attestation_code="OFFLINE_IDENTITY_CHECKED",
                    expected_version=1,
                ),
            )
            await session.commit()

        review_factory = await get_slice3_session_factory("identity_review_writer")
        async with review_factory() as session:
            service = MemberEnrollmentService(
                MemberEnrollmentRepository(session), secrets_port=secrets
            )
            platform_context = MutationContext(
                actor=CurrentUser(id=user_id + 3, role="super_admin"),
                tenant_id=tenant_id,
                tenant_public_id=tenant_public_id,
                idempotency_key="slice3-self-platform-claim",
                request_id=uuid4(),
                platform_scope=True,
            )
            await service.claim_identity_review(
                platform_context, verification_id, expected_version=2
            )
            await session.commit()
        reviewer_currentness = secrets.audit_digest({
            "id": user_id + 3,
            "role": "super_admin",
            "status": "active",
            "tenant_id": None,
            "version": 1,
        })
        reviewer_token = secrets.request_digest({
            "authorization": "synthetic-disposable-token",
            "reviewer_user_id": user_id + 3,
        })

        async def access_pii(
            service,
            context,
            request,
            *,
            password_valid: bool,
            target_verification_id: UUID = verification_id,
            target_revision_id: UUID = revision_id,
            fixed_proof: tuple[dict[str, object], str] | None = None,
        ):
            reviewer = (await pg_database._fetch_rows(
                'SELECT updated_at FROM public."user" WHERE id=$1',
                user_id + 3,
            ))[0]
            if fixed_proof is None:
                issued_at = datetime.now(timezone.utc)
                request_digest = secrets.request_digest({
                    "verification_id": str(target_verification_id),
                    "reason_code": request.reason_code,
                    "idempotency_key": context.idempotency_key,
                })
                proof_values = {
                    "proof_version": 1,
                    "reviewer_user_id": user_id + 3,
                    "user_version": 1,
                    "user_updated_at": reviewer["updated_at"],
                    "verification_id": target_verification_id,
                    "current_revision_id": target_revision_id,
                    "actor_scope": context.actor_scope,
                    "idempotency_key": context.idempotency_key,
                    "request_id": context.request_id,
                    "request_digest": request_digest,
                    "access_token_digest": reviewer_token,
                    "currentness_digest": reviewer_currentness,
                    "reason_code": request.reason_code,
                    "password_valid": password_valid,
                    "proof_issued_at": issued_at,
                    "proof_expires_at": issued_at + timedelta(seconds=15),
                }
                credential_proof_digest = reviewer_credential_proof(
                    "test-only", proof_values
                )
            else:
                proof_values, credential_proof_digest = fixed_proof
                request_digest = str(proof_values["request_digest"])
            return await service.access_identity_pii(
                context,
                target_verification_id,
                request,
                currentness_digest=reviewer_currentness,
                access_token_digest=reviewer_token,
                password_valid=password_valid,
                request_digest=request_digest,
                proof_values=proof_values,
                credential_proof_digest=credential_proof_digest,
            )

        async with review_factory() as session:
            service = MemberEnrollmentService(
                MemberEnrollmentRepository(session), secrets_port=secrets
            )
            access_context = MutationContext(
                actor=CurrentUser(id=user_id + 3, role="super_admin"),
                tenant_id=tenant_id,
                tenant_public_id=tenant_public_id,
                idempotency_key="slice3-self-platform-pii-access",
                request_id=uuid4(),
                platform_scope=True,
            )
            pii_result = await access_pii(
                service,
                access_context,
                PiiAccessRequest(
                    current_password="synthetic",
                    reason_code="PLATFORM_IDENTITY_REVIEW",
                ),
                password_valid=True,
            )
            assert pii_result["access_id"] is not None
            await session.commit()
        async with review_factory() as session:
            service = MemberEnrollmentService(
                MemberEnrollmentRepository(session), secrets_port=secrets
            )
            platform_context = MutationContext(
                actor=CurrentUser(id=user_id + 3, role="super_admin"),
                tenant_id=tenant_id,
                tenant_public_id=tenant_public_id,
                idempotency_key="slice3-self-platform-decision",
                request_id=uuid4(),
                platform_scope=True,
            )
            await service.platform_identity_decide(
                platform_context,
                verification_id,
                PlatformIdentityDecisionRequest(
                    revision_id=revision_id,
                    decision="APPROVED",
                    expected_version=3,
                ),
                source_member_id=member_id,
                enrollment_mode="SELF",
                access_token_digest=reviewer_token,
                currentness_digest=reviewer_currentness,
            )
            await session.commit()

        async with review_factory() as session:
            completed_service = MemberEnrollmentService(
                MemberEnrollmentRepository(session), secrets_port=secrets
            )
            with pytest.raises(
                MemberEnrollmentConflict, match="^STEP_UP_FORBIDDEN$"
            ):
                await access_pii(
                    completed_service,
                    MutationContext(
                        actor=CurrentUser(id=user_id + 3, role="super_admin"),
                        tenant_id=tenant_id,
                        tenant_public_id=tenant_public_id,
                        idempotency_key="slice3-completed-review-pii-denied",
                        request_id=uuid4(),
                        platform_scope=True,
                    ),
                    PiiAccessRequest(
                        current_password="invalid-synthetic",
                        reason_code="PLATFORM_IDENTITY_REVIEW",
                    ),
                    password_valid=False,
                )
            await session.rollback()

        budget_member_id = uuid4()
        budget_user_id = user_id + 10
        budget_phone = "18" + str(budget_user_id).zfill(9)
        await pg_database._execute(
            "INSERT INTO public.\"user\"(id,phone,password_hash,role,status,tenant_id) "
            f"VALUES ({budget_user_id},'{budget_phone}','test-only','member','active',NULL);"
            "INSERT INTO identity.member("
            "member_id,member_no,creation_source,status,version,created_at,updated_at) "
            f"VALUES ('{budget_member_id}','M9123456789ABCDEFGHJK','registration',"
            "'created',1,now(),now())"
        )
        budget_actor = CurrentUser(
            id=budget_user_id, role="member", tenant_id=tenant_id
        )
        async with factory() as session:
            budget_service = MemberEnrollmentService(
                MemberEnrollmentRepository(session), secrets_port=secrets
            )
            budget_invitation_id, budget_short_code = (
                await budget_service.create_invitation(
                    MutationContext(
                        actor=CurrentUser(
                            id=user_id + 2, role="org_admin", tenant_id=tenant_id
                        ),
                        tenant_id=tenant_id,
                        tenant_public_id=tenant_public_id,
                        idempotency_key="slice3-budget-create",
                        request_id=uuid4(),
                    ),
                    CreateMemberInvitationRequest(mode="SELF", phone=budget_phone),
                )
            )
            await session.commit()
        async with factory() as session:
            budget_service = MemberEnrollmentService(
                MemberEnrollmentRepository(session), secrets_port=secrets
            )
            budget_enrollment_id, _ = await budget_service.accept_invitation(
                MutationContext(
                    actor=budget_actor,
                    tenant_id=tenant_id,
                    tenant_public_id=tenant_public_id,
                    idempotency_key="slice3-budget-accept",
                    request_id=uuid4(),
                ),
                AcceptEnrollmentRequest(
                    invitation_id=budget_invitation_id,
                    phone=budget_phone,
                    short_code=budget_short_code,
                ),
                actor_member_id=budget_member_id,
            )
            await session.commit()
        async with factory() as session:
            budget_service = MemberEnrollmentService(
                MemberEnrollmentRepository(session), secrets_port=secrets
            )
            budget_verification_id, budget_revision_id = (
                await budget_service.submit_identity(
                    MutationContext(
                        actor=budget_actor,
                        tenant_id=tenant_id,
                        tenant_public_id=tenant_public_id,
                        idempotency_key="slice3-budget-identity",
                        request_id=uuid4(),
                    ),
                    budget_enrollment_id,
                    IdentitySubmissionRequest(
                        document_type="PRC_RESIDENT_ID",
                        real_name="Synthetic Budget Member",
                        id_number=_synthetic_prc_identity("19810101"),
                        expected_version=1,
                    ),
                    submitted_by_member_id=budget_member_id,
                )
            )
            await session.commit()
        async with factory() as session:
            budget_service = MemberEnrollmentService(
                MemberEnrollmentRepository(session), secrets_port=secrets
            )
            await budget_service.institution_identity_check(
                MutationContext(
                    actor=CurrentUser(
                        id=user_id + 2, role="org_admin", tenant_id=tenant_id
                    ),
                    tenant_id=tenant_id,
                    tenant_public_id=tenant_public_id,
                    idempotency_key="slice3-budget-institution-check",
                    request_id=uuid4(),
                ),
                budget_verification_id,
                InstitutionIdentityCheckRequest(
                    revision_id=budget_revision_id,
                    decision="CHECKED",
                    attestation_code="OFFLINE_IDENTITY_CHECKED",
                    expected_version=1,
                ),
            )
            await session.commit()
        async with review_factory() as session:
            budget_service = MemberEnrollmentService(
                MemberEnrollmentRepository(session), secrets_port=secrets
            )
            await budget_service.claim_identity_review(
                MutationContext(
                    actor=CurrentUser(id=user_id + 3, role="super_admin"),
                    tenant_id=tenant_id,
                    tenant_public_id=tenant_public_id,
                    idempotency_key="slice3-budget-platform-claim",
                    request_id=uuid4(),
                    platform_scope=True,
                ),
                budget_verification_id,
                expected_version=2,
            )
            await session.commit()

        budget_outbox_before = await pg_database._fetch_value(
            "SELECT count(*) FROM public.member_enrollment_outbox "
            f"WHERE aggregate_id='{budget_verification_id}'"
        )
        budget_receipts_before = await pg_database._fetch_value(
            "SELECT count(*) FROM public.member_enrollment_idempotency "
            f"WHERE target_id='{budget_verification_id}'"
        )

        for attempt in range(1, 6):
            async with review_factory() as session:
                service = MemberEnrollmentService(
                    MemberEnrollmentRepository(session), secrets_port=secrets
                )
                failure = await access_pii(
                    service,
                    MutationContext(
                        actor=CurrentUser(id=user_id + 3, role="super_admin"),
                        tenant_id=tenant_id,
                        tenant_public_id=tenant_public_id,
                        idempotency_key=f"slice3-step-up-failure-{attempt}",
                        request_id=uuid4(),
                        platform_scope=True,
                    ),
                    PiiAccessRequest(
                        current_password="invalid-synthetic",
                        reason_code="PLATFORM_IDENTITY_REVIEW",
                    ),
                    password_valid=False,
                    target_verification_id=budget_verification_id,
                    target_revision_id=budget_revision_id,
                )
                assert failure["error_code"] == (
                    "STEP_UP_RATE_LIMITED" if attempt == 5 else "STEP_UP_FORBIDDEN"
                )
                await session.commit()
        async with review_factory() as session:
            service = MemberEnrollmentService(
                MemberEnrollmentRepository(session), secrets_port=secrets
            )
            limited = await access_pii(
                service,
                MutationContext(
                    actor=CurrentUser(id=user_id + 3, role="super_admin"),
                    tenant_id=tenant_id,
                    tenant_public_id=tenant_public_id,
                    idempotency_key="slice3-step-up-after-budget",
                    request_id=uuid4(),
                    platform_scope=True,
                ),
                PiiAccessRequest(
                    current_password="synthetic",
                    reason_code="PLATFORM_IDENTITY_REVIEW",
                ),
                password_valid=True,
                target_verification_id=budget_verification_id,
                target_revision_id=budget_revision_id,
            )
            assert limited == {"error_code": "STEP_UP_RATE_LIMITED"}
            await session.commit()

        assert await pg_database._fetch_value(
            "SELECT count(*) FROM public.member_enrollment_audit "
            f"WHERE object_id='{budget_verification_id}' "
            "AND action='IDENTITY_PII_STEP_UP_FAILED'"
        ) == 4
        assert await pg_database._fetch_value(
            "SELECT count(*) FROM public.member_enrollment_audit "
            f"WHERE object_id='{budget_verification_id}' "
            "AND action='IDENTITY_PII_STEP_UP_RATE_LIMITED'"
        ) == 2
        assert await pg_database._fetch_value(
            "SELECT count(*) FROM public.member_identity_pii_access "
            f"WHERE verification_id='{budget_verification_id}'"
        ) == 0

        replay_context = MutationContext(
            actor=CurrentUser(id=user_id + 3, role="super_admin"),
            tenant_id=tenant_id,
            tenant_public_id=tenant_public_id,
            idempotency_key="slice3-step-up-proof-replay",
            request_id=uuid4(),
            platform_scope=True,
        )
        replay_request = PiiAccessRequest(
            current_password="invalid-synthetic",
            reason_code="PLATFORM_IDENTITY_REVIEW",
        )
        reviewer = (await pg_database._fetch_rows(
            'SELECT updated_at FROM public."user" WHERE id=$1', user_id + 3
        ))[0]
        issued_at = datetime.now(timezone.utc)
        replay_request_digest = secrets.request_digest({
            "verification_id": str(budget_verification_id),
            "reason_code": replay_request.reason_code,
            "idempotency_key": replay_context.idempotency_key,
        })
        replay_values: dict[str, object] = {
            "proof_version": 1,
            "reviewer_user_id": user_id + 3,
            "user_version": 1,
            "user_updated_at": reviewer["updated_at"],
            "verification_id": budget_verification_id,
            "current_revision_id": budget_revision_id,
            "actor_scope": replay_context.actor_scope,
            "idempotency_key": replay_context.idempotency_key,
            "request_id": replay_context.request_id,
            "request_digest": replay_request_digest,
            "access_token_digest": reviewer_token,
            "currentness_digest": reviewer_currentness,
            "reason_code": replay_request.reason_code,
            "password_valid": False,
            "proof_issued_at": issued_at,
            "proof_expires_at": issued_at + timedelta(seconds=15),
        }
        fixed_proof = (
            replay_values,
            reviewer_credential_proof("test-only", replay_values),
        )
        async with review_factory() as session:
            service = MemberEnrollmentService(
                MemberEnrollmentRepository(session), secrets_port=secrets
            )
            first = await access_pii(
                service,
                replay_context,
                replay_request,
                password_valid=False,
                target_verification_id=budget_verification_id,
                target_revision_id=budget_revision_id,
                fixed_proof=fixed_proof,
            )
            assert first == {"error_code": "STEP_UP_RATE_LIMITED"}
            await session.commit()
        before_replay = await pg_database._fetch_value(
            "SELECT count(*) FROM public.member_enrollment_audit "
            f"WHERE object_id='{budget_verification_id}'"
        )
        async with review_factory() as session:
            service = MemberEnrollmentService(
                MemberEnrollmentRepository(session), secrets_port=secrets
            )
            replayed = await access_pii(
                service,
                replay_context,
                replay_request,
                password_valid=False,
                target_verification_id=budget_verification_id,
                target_revision_id=budget_revision_id,
                fixed_proof=fixed_proof,
            )
            assert replayed == {"error_code": "STEP_UP_REPLAYED"}
            await session.commit()
        assert await pg_database._fetch_value(
            "SELECT count(*) FROM public.member_enrollment_audit "
            f"WHERE object_id='{budget_verification_id}'"
        ) == before_replay
        assert await pg_database._fetch_value(
            "SELECT count(*) FROM public.member_enrollment_outbox "
            f"WHERE aggregate_id='{budget_verification_id}'"
        ) == budget_outbox_before
        assert await pg_database._fetch_value(
            "SELECT count(*) FROM public.member_enrollment_idempotency "
            f"WHERE target_id='{budget_verification_id}'"
        ) == budget_receipts_before

        document_types = (
            "USER_AGREEMENT",
            "PRIVACY_POLICY",
            "HEALTH_DATA_PROCESSING",
            "INSTITUTION_SERVICE",
            "NON_MEDICAL_RISK",
        )
        published_documents: list[tuple[str, UUID, UUID]] = []
        for index, document_type in enumerate(document_types, start=1):
            async with review_factory() as session:
                repository = MemberEnrollmentRepository(session)
                service = MemberEnrollmentService(repository, secrets_port=secrets)
                platform_context = MutationContext(
                    actor=CurrentUser(id=user_id + 3, role="super_admin"),
                    tenant_id=tenant_id,
                    tenant_public_id=tenant_public_id,
                    idempotency_key=f"slice3-self-document-{index}",
                    request_id=uuid4(),
                    platform_scope=True,
                )
                document_id = await service.create_consent_document(
                    platform_context,
                    CreateConsentDocumentRequest(
                        document_type=document_type,
                        semantic_version="1.0",
                        requires_reconsent=True,
                        renditions=(
                            ConsentRenditionInput(
                                locale="zh-CN",
                                title=f"Slice3 document {index}",
                                body=f"Slice3 controlled consent body {index}",
                            ),
                        ),
                    ),
                )
                await service.publish_consent_document(
                    platform_context,
                    document_id,
                    PublishConsentDocumentRequest(
                        expected_version=1,
                        effective_at=datetime.now(timezone.utc),
                    ),
                )
                renditions = await repository.document_renditions(document_id)
                published_documents.append(
                    (document_type, document_id, renditions[0]["rendition_id"])
                )
                await session.commit()

        for index, (document_type, document_id, rendition_id) in enumerate(
            published_documents, start=1
        ):
            async with factory() as session:
                service = MemberEnrollmentService(
                    MemberEnrollmentRepository(session), secrets_port=secrets
                )
                await service.record_consent(
                    MutationContext(
                        actor=actor,
                        tenant_id=tenant_id,
                        tenant_public_id=tenant_public_id,
                        idempotency_key=f"slice3-self-consent-{index}",
                        request_id=uuid4(),
                    ),
                    enrollment_id,
                    RecordConsentRequest(
                        document_version_id=UUID(str(document_id)),
                        rendition_id=UUID(str(rendition_id)),
                        choice="ACCEPTED",
                        purpose_codes=("ACCOUNT_AND_SERVICE_ONBOARDING",),
                        expected_version=3 + index,
                    ),
                    subject_member_id=member_id,
                    proxy_member_id=None,
                    document_type=document_type,
                    locale="zh-CN",
                )
                await session.commit()

        async with review_factory() as session:
            repository = MemberEnrollmentRepository(session)
            service = MemberEnrollmentService(repository, secrets_port=secrets)
            document_context = MutationContext(
                actor=CurrentUser(id=user_id + 3, role="super_admin"),
                tenant_id=None,
                tenant_public_id=tenant_public_id,
                idempotency_key="slice3-self-document-reconsent",
                request_id=uuid4(),
                platform_scope=True,
            )
            replacement_document_id = await service.create_consent_document(
                document_context,
                CreateConsentDocumentRequest(
                    document_type="USER_AGREEMENT",
                    semantic_version="2.0",
                    requires_reconsent=True,
                    renditions=(
                        ConsentRenditionInput(
                            locale="zh-CN",
                            title="Slice3 replacement agreement",
                            body="Slice3 replacement controlled consent body",
                        ),
                    ),
                ),
            )
            await service.publish_consent_document(
                document_context,
                replacement_document_id,
                PublishConsentDocumentRequest(
                    expected_version=1,
                    effective_at=datetime.now(timezone.utc),
                ),
            )
            replacement_rendition_id = (
                await repository.document_renditions(replacement_document_id)
            )[0]["rendition_id"]
            await session.commit()
        async with factory() as session:
            service = MemberEnrollmentService(
                MemberEnrollmentRepository(session), secrets_port=secrets
            )
            await service.record_consent(
                MutationContext(
                    actor=actor,
                    tenant_id=tenant_id,
                    tenant_public_id=tenant_public_id,
                    idempotency_key="slice3-self-consent-replacement",
                    request_id=uuid4(),
                ),
                enrollment_id,
                RecordConsentRequest(
                    document_version_id=replacement_document_id,
                    rendition_id=UUID(str(replacement_rendition_id)),
                    choice="ACCEPTED",
                    purpose_codes=("ACCOUNT_AND_SERVICE_ONBOARDING",),
                    expected_version=9,
                ),
                subject_member_id=member_id,
                proxy_member_id=None,
                document_type="USER_AGREEMENT",
                locale="zh-CN",
            )
            await session.commit()

        therapist_invitation_id = uuid7.generate()
        therapist_id = uuid7.generate()
        therapist_revision_id = uuid7.generate()
        qualification_id = uuid7.generate()
        qualification_file_id = uuid7.generate()
        therapist_user_id = user_id + 4
        await pg_database._execute(
            "INSERT INTO public.therapist_invitation("
            "invitation_id,tenant_id,phone_ciphertext,phone_encryption_key_id,"
            "phone_digest,phone_digest_key_id,phone_masked,code_digest,code_digest_key_id,"
            "expires_at,status,failed_attempts,issued_by,issued_at,activated_at,version) VALUES ("
            f"'{therapist_invitation_id}',{tenant_id},decode('00','hex'),'k1',repeat('1',64),'k1',"
            f"'*******0000',repeat('2',64),'k1',now()+interval '1 day','ACTIVATED',0,{user_id + 2},now(),now(),1);"
            "INSERT INTO public.\"user\"(id,phone,password_hash,role,status,tenant_id) VALUES ("
            f"{therapist_user_id},'15' || '0' || repeat('0',8),'test-only','therapist','active',{tenant_id});"
            "INSERT INTO public.therapist_profile("
            "therapist_id,user_id,tenant_id,invitation_id,status,capacity_limit,active_case_count,"
            "current_revision_no,totp_secret_ciphertext,totp_encryption_key_id,totp_enabled,"
            "activated_at,created_at,updated_at,version) VALUES ("
            f"'{therapist_id}',{therapist_user_id},{tenant_id},'{therapist_invitation_id}',"
            "'DRAFT',30,0,0,decode('00','hex'),'k1',true,now(),now(),now(),1);"
            "INSERT INTO public.private_file("
            "file_id,purpose,owner_user_id,declared_size,declared_mime_type,declared_sha256,"
            "actual_size,actual_mime_type,actual_sha256,object_key,status,bound_application_id,"
            "created_at,expires_at,scanned_at,bound_at) VALUES ("
            f"'{qualification_file_id}','THERAPIST_QUALIFICATION',{therapist_user_id},1,"
            f"'application/pdf',repeat('3',64),1,'application/pdf',repeat('3',64),"
            f"'slice3/qualification/{qualification_file_id}','CLEAN',NULL,now(),"
            "now()+interval '1 day',now(),now());"
            "INSERT INTO public.therapist_profile_revision("
            "revision_id,therapist_id,revision_no,profile_snapshot,input_digest,created_at) VALUES ("
            f"'{therapist_revision_id}','{therapist_id}',1,'{{\"v\":1}}'::jsonb,repeat('4',64),now());"
            "INSERT INTO public.therapist_qualification_version("
            "qualification_version_id,therapist_id,profile_revision_id,previous_version_id,"
            "qualification_type,certificate_no_ciphertext,certificate_encryption_key_id,"
            "certificate_no_digest,certificate_digest_key_id,certificate_no_masked,issuer_name,"
            "valid_from,valid_until,attachment_count,version_no,created_at) VALUES ("
            f"'{qualification_id}','{therapist_id}','{therapist_revision_id}',NULL,"
            "'METABOLIC_HEALTH_PRACTICE',decode('00','hex'),'k1',repeat('5',64),'k1',"
            "'****0001','Slice3 issuer',current_date-30,current_date+30,1,1,now());"
            "INSERT INTO public.therapist_profile_revision_qualification("
            "therapist_id,revision_id,qualification_version_id,position) VALUES ("
            f"'{therapist_id}','{therapist_revision_id}','{qualification_id}',1);"
            "INSERT INTO public.therapist_qualification_attachment("
            "qualification_version_id,slot,private_file_id,created_at) VALUES ("
            f"'{qualification_id}',1,'{qualification_file_id}',now());"
            "UPDATE public.therapist_profile SET "
            "real_name_ciphertext=decode('00','hex'),real_name_encryption_key_id='k1',"
            "real_name_digest=repeat('6',64),real_name_digest_key_id='k1',"
            "display_name='Slice3 therapist',practice_summary='Controlled metabolic service',"
            "service_tags='[\"GLUCOSE_METABOLISM\"]'::jsonb,status='APPROVED_ACTIVE',"
            f"current_revision_no=1,current_qualification_version_id='{qualification_id}',"
            "qualification_valid_until=current_date+30,submitted_at=now(),reviewed_at=now(),"
            f"updated_at=now() WHERE therapist_id='{therapist_id}'"
        )

        guard_sql = (
            "SELECT public.slice3_assignment_candidate_guard_v1("
            f"'{therapist_id}'::uuid,$1::bigint,$2::jsonb)"
        )
        assert await member_enrollment_writer_database._fetch_value(
            guard_sql, tenant_id, '["GLUCOSE_METABOLISM"]'
        ) is True
        await pg_database._execute(
            "UPDATE public.therapist_profile SET active_case_count=capacity_limit "
            f"WHERE therapist_id='{therapist_id}'"
        )
        assert await member_enrollment_writer_database._fetch_value(
            guard_sql, tenant_id, '["GLUCOSE_METABOLISM"]'
        ) is False
        await pg_database._execute(
            "UPDATE public.therapist_profile SET active_case_count=capacity_limit-1 "
            f"WHERE therapist_id='{therapist_id}'"
        )
        assert await member_enrollment_writer_database._fetch_value(
            guard_sql, tenant_id, '["GLUCOSE_METABOLISM"]'
        ) is True
        await pg_database._execute(
            "UPDATE public.therapist_profile SET active_case_count=0 "
            f"WHERE therapist_id='{therapist_id}'"
        )
        assert await member_enrollment_writer_database._fetch_value(
            guard_sql, tenant_id + 1, '["GLUCOSE_METABOLISM"]'
        ) is False
        assert await member_enrollment_writer_database._fetch_value(
            guard_sql, tenant_id, '["HYPERTENSION"]'
        ) is False
        with pytest.raises(Exception):
            await member_enrollment_writer_database._fetch_value(
                f"SELECT therapist_id FROM public.therapist_profile "
                f"WHERE therapist_id='{therapist_id}'"
            )
        with pytest.raises(Exception):
            await member_identity_review_writer_database._fetch_value(
                guard_sql, tenant_id, '["GLUCOSE_METABOLISM"]'
            )
        signature = "public.slice3_assignment_candidate_guard_v1(uuid,bigint,jsonb)"
        enrollment_role, review_role, case_role, worker_role, reader_role = _roles()
        assert await pg_database._fetch_value(
            f"SELECT has_function_privilege('{enrollment_role}','{signature}','EXECUTE')"
        )
        for denied_role in (review_role, case_role, worker_role, reader_role, "public"):
            assert not await pg_database._fetch_value(
                f"SELECT has_function_privilege('{denied_role}','{signature}','EXECUTE')"
            )

        await pg_database._execute(
            "UPDATE public.therapist_profile SET status='SUSPENDED',"
            "suspended_at=now(),suspension_reason_code='COMPLIANCE',version=version+1 "
            f"WHERE therapist_id='{therapist_id}'"
        )
        assert await member_enrollment_writer_database._fetch_value(
            guard_sql, tenant_id, '["GLUCOSE_METABOLISM"]'
        ) is False
        await pg_database._execute(
            "UPDATE public.therapist_profile SET status='APPROVED_ACTIVE',"
            "resumed_at=suspended_at+interval '1 second',suspension_reason_code=NULL,"
            f"version=version+1 WHERE therapist_id='{therapist_id}'"
        )
        await pg_database._execute(
            "UPDATE public.therapist_profile SET status='EXITED',exited_at=now(),"
            f"version=version+1 WHERE therapist_id='{therapist_id}'"
        )
        assert await member_enrollment_writer_database._fetch_value(
            guard_sql, tenant_id, '["GLUCOSE_METABOLISM"]'
        ) is False
        await pg_database._execute(
            "UPDATE public.therapist_profile SET status='APPROVED_ACTIVE',exited_at=NULL,"
            f"version=version+1 WHERE therapist_id='{therapist_id}'"
        )
        await pg_database._execute(
            "UPDATE public.therapist_profile SET qualification_valid_until=current_date-1,"
            f"version=version+1 WHERE therapist_id='{therapist_id}'"
        )
        assert await member_enrollment_writer_database._fetch_value(
            guard_sql, tenant_id, '["GLUCOSE_METABOLISM"]'
        ) is False
        await pg_database._execute(
            "UPDATE public.therapist_profile SET qualification_valid_until=current_date+30,"
            f"version=version+1 WHERE therapist_id='{therapist_id}'"
        )
        await pg_database._execute(
            "UPDATE public.therapist_profile SET status='DRAFT',current_revision_no=0,"
            "current_qualification_version_id=NULL,qualification_valid_until=NULL,"
            "submitted_at=NULL,reviewed_at=NULL,suspended_at=NULL,resumed_at=NULL,"
            f"version=version+1 WHERE therapist_id='{therapist_id}'"
        )
        assert await member_enrollment_writer_database._fetch_value(
            guard_sql, tenant_id, '["GLUCOSE_METABOLISM"]'
        ) is False
        await pg_database._execute(
            "UPDATE public.therapist_profile SET status='APPROVED_ACTIVE',current_revision_no=1,"
            f"current_qualification_version_id='{qualification_id}',"
            "qualification_valid_until=current_date+30,submitted_at=now(),reviewed_at=now(),"
            f"version=version+1 WHERE therapist_id='{therapist_id}'"
        )

        writer_connection = await asyncpg.connect(
            member_enrollment_writer_database.database_url
        )
        owner_connection = await asyncpg.connect(pg_database.database_url)
        transaction = writer_connection.transaction()
        try:
            await transaction.start()
            assert await writer_connection.fetchval(
                "SELECT public.slice3_assignment_candidate_guard_v1($1,$2,$3::jsonb)",
                therapist_id,
                tenant_id,
                '["GLUCOSE_METABOLISM"]',
            ) is True
            await owner_connection.execute("SET lock_timeout='250ms'")
            with pytest.raises(Exception) as blocked:
                await owner_connection.execute(
                    "UPDATE public.therapist_profile SET status='SUSPENDED' "
                    "WHERE therapist_id=$1",
                    therapist_id,
                )
            assert getattr(blocked.value, "sqlstate", None) in {"55P03", "57014"}
        finally:
            await transaction.rollback()
            await writer_connection.close()
            await owner_connection.close()

        async with factory() as session:
            service = MemberEnrollmentService(
                MemberEnrollmentRepository(session), secrets_port=secrets
            )
            assignment_id = await service.create_assignment(
                MutationContext(
                    actor=CurrentUser(
                        id=user_id + 2, role="org_admin", tenant_id=tenant_id
                    ),
                    tenant_id=tenant_id,
                    tenant_public_id=tenant_public_id,
                    idempotency_key="slice3-self-assignment",
                    request_id=uuid4(),
                ),
                enrollment_id,
                CreateAssignmentRequest(
                    therapist_id=therapist_id,
                    service_scope_tags=("GLUCOSE_METABOLISM",),
                    expected_version=10,
                ),
            )
            await session.commit()

        function_sql = (
            "public.slice3_case_enrollment_preimage_authority_v1("
            "$1::uuid,$2::uuid,$3::uuid,$4::bigint)"
        )
        expected_columns = await pg_database._fetch_column(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='service_enrollment' "
            "ORDER BY column_name"
        )
        actual_rows = await member_case_writer_database._fetch_rows(
            f"SELECT key FROM jsonb_object_keys({function_sql}) AS keys(key) "
            "ORDER BY key",
            assignment_id,
            enrollment_id,
            therapist_id,
            therapist_user_id,
        )
        actual_columns = [row["key"] for row in actual_rows]
        assert actual_columns == expected_columns
        for arguments in (
            (assignment_id, enrollment_id, therapist_id, therapist_user_id + 999),
            (assignment_id, enrollment_id, uuid4(), therapist_user_id),
            (uuid4(), enrollment_id, therapist_id, therapist_user_id),
            (assignment_id, uuid4(), therapist_id, therapist_user_id),
        ):
            assert await member_case_writer_database._fetch_value(
                f"SELECT {function_sql}", *arguments
            ) is None

        therapist_token = create_access_token({
            "sub": str(therapist_user_id),
            "role": "therapist",
            "tenant_id": tenant_id,
        })
        authorization = {"Authorization": f"Bearer {therapist_token}"}
        listed = real_db_client.get(
            "/api/v1/therapist/primary-assignments",
            headers=authorization,
            params={"status": "PENDING_ACCEPTANCE"},
        )
        assert listed.status_code == 200
        assert len(listed.json()["items"]) == 1
        assert listed.json()["items"][0]["assignment_id"] == str(assignment_id)
        assert "subject_masked_label" not in listed.json()["items"][0]
        assert "therapist_display_name" not in listed.json()["items"][0]

        accepted = real_db_client.post(
            f"/api/v1/therapist/primary-assignments/{assignment_id}/accept",
            headers={
                **authorization,
                "Idempotency-Key": "slice3-self-assignment-accept-http",
            },
            json={"expected_version": 1},
        )
        assert accepted.status_code == 201, accepted.json()
        assert accepted.json()["status"] == "PREPARING"
        assert accepted.json()["assignment_id"] == str(assignment_id)
        case_id = UUID(accepted.json()["case_id"])
        replay = real_db_client.post(
            f"/api/v1/therapist/primary-assignments/{assignment_id}/accept",
            headers={
                **authorization,
                "Idempotency-Key": "slice3-self-assignment-accept-http",
            },
            json={"expected_version": 1},
        )
        assert replay.status_code == 201
        assert replay.json() == accepted.json()
    finally:
        await dispose_slice3_runtime("enrollment_writer")
        await dispose_slice3_runtime("identity_review_writer")
        await dispose_slice3_runtime("case_writer")

    assert subject_member_id == member_id
    row = await member_enrollment_reader_database._fetch_rows(
        "SELECT enrollment_id,subject_member_id,status,mode FROM "
        "public.slice3_family_enrollment_read_v1 WHERE enrollment_id=$1",
        enrollment_id,
    )
    assert row == [
        {
            "enrollment_id": enrollment_id,
            "subject_member_id": member_id,
            "status": "CASE_CREATED",
            "mode": "SELF",
        }
    ]
    detail = await member_enrollment_reader_database._fetch_rows(
        "SELECT identity,proxy,consents,assignment FROM "
        "public.slice3_family_enrollment_read_v1 WHERE enrollment_id=$1",
        enrollment_id,
    )
    assert len(detail) == 1
    identity_value = json.loads(detail[0]["identity"])
    consent_values = json.loads(detail[0]["consents"])
    assignment_value = json.loads(detail[0]["assignment"])
    assert identity_value["verification_id"] == str(verification_id)
    assert detail[0]["proxy"] is None
    assert len(consent_values) == 6
    assert sum(item["status"] == "SUPERSEDED" for item in consent_values) == 1
    assert assignment_value["assignment_id"] == str(assignment_id)
    assert await pg_database._fetch_value(
        f"SELECT status FROM public.member_identity_verification WHERE verification_id='{verification_id}'"
    ) == "VERIFIED"
    assert await pg_database._fetch_value(
        f"SELECT count(*) FROM identity.identity_subject_claim_registry WHERE member_id='{member_id}'"
    ) == 1
    assert await pg_database._fetch_value(
        f"SELECT count(*) FROM public.consent_record WHERE enrollment_id='{enrollment_id}' "
        "AND status='ACCEPTED'"
    ) == 5
    assert await pg_database._fetch_value(
        f"SELECT count(*) FROM public.consent_record WHERE enrollment_id='{enrollment_id}' "
        "AND status='SUPERSEDED'"
    ) == 1
    assert await pg_database._fetch_value(
        f"SELECT status FROM public.service_case WHERE case_id='{case_id}'"
    ) == "PREPARING"
    assignment_state = (await pg_database._fetch_rows(
        "SELECT status,service_case_id FROM public.primary_therapist_assignment "
        "WHERE assignment_id=$1",
        assignment_id,
    ))[0]
    assert assignment_state == {
        "status": "ACCEPTED",
        "service_case_id": case_id,
    }
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.service_case WHERE assignment_id=$1",
        assignment_id,
    ) == 1
    assert await pg_database._fetch_value(
        f"SELECT active_case_count FROM public.therapist_profile WHERE therapist_id='{therapist_id}'"
    ) == 1


@pytest.mark.asyncio
async def test_R3邀请过期只转换一次并固化安全事件(pg_database):
    from app.core.database import dispose_slice3_runtime, get_slice3_session_factory
    from app.modules.member_enrollment.repository import MemberEnrollmentRepository
    from app.modules.member_enrollment.service import MemberEnrollmentSecrets, MemberEnrollmentService

    tenant_id = 96201
    issuer_id = 96202
    invitation_id = uuid4()
    await pg_database._execute(
        "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) "
        "VALUES (96203,NULL,'Slice3 expiry county','SLICE3-EXPIRY','county','active',1);"
        "INSERT INTO public.tenant(id,org_id,tenant_code,name,type,province,city,status,created_at,updated_at) "
        f"VALUES ({tenant_id},96203,'SLICE3-EXPIRY-TENANT','Slice3 expiry','store','test','test','active',now(),now());"
        "INSERT INTO public.\"user\"(id,phone,password_hash,role,status,tenant_id) "
        f"VALUES ({issuer_id},'13'||'9'||repeat('0',8),'test-only','org_admin','active',{tenant_id});"
        "INSERT INTO public.member_service_invitation("
        "invitation_id,tenant_id,mode,phone_ciphertext,phone_key_id,phone_digest,"
        "phone_digest_key_id,phone_masked,code_digest,code_key_id,status,failed_attempts,"
        "expires_at,issued_by,issued_at,version) VALUES ("
        f"'{invitation_id}',{tenant_id},'SELF',decode('00','hex'),'k1',repeat('1',64),"
        f"'k1','*******0000',repeat('2',64),'k1','INVITED',0,now()-interval '1 minute',"
        f"{issuer_id},now()-interval '1 day',1)"
    )
    factory = await get_slice3_session_factory("enrollment_writer")
    try:
        async with factory() as session:
            service = MemberEnrollmentService(
                MemberEnrollmentRepository(session), secrets_port=MemberEnrollmentSecrets()
            )
            assert await service.expire_invitations() == 1
            await session.commit()
        async with factory() as session:
            service = MemberEnrollmentService(
                MemberEnrollmentRepository(session), secrets_port=MemberEnrollmentSecrets()
            )
            assert await service.expire_invitations() == 0
            await session.rollback()
    finally:
        await dispose_slice3_runtime("enrollment_writer")
    assert await pg_database._fetch_value(
        f"SELECT status FROM public.member_service_invitation WHERE invitation_id='{invitation_id}'"
    ) == "EXPIRED"
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.member_enrollment_audit "
        f"WHERE object_id='{invitation_id}' AND action='MEMBER_INVITATION_EXPIRED'"
    ) == 1
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.member_enrollment_outbox "
        f"WHERE aggregate_id='{invitation_id}' AND event_type='MEMBER_INVITATION_EXPIRED'"
    ) == 1


@pytest.mark.asyncio
async def test_PROXY老人仅两个槽位且只创建受控Member(pg_database):
    from app.core.database import dispose_slice3_runtime, get_slice3_session_factory
    from app.core.security import CurrentUser
    from app.modules.member_enrollment.domain import MemberEnrollmentConflict
    from app.modules.member_enrollment.repository import MemberEnrollmentRepository
    from app.modules.member_enrollment.schemas import (
        AcceptEnrollmentRequest,
        CreateMemberInvitationRequest,
    )
    from app.modules.member_enrollment.service import (
        MemberEnrollmentSecrets,
        MemberEnrollmentService,
        MutationContext,
    )

    tenant_id = 96101
    user_id = 96102
    proxy_member_id = uuid4()
    tenant_public_id = uuid4()
    institution_invitation_id = uuid4()
    institution_application_id = uuid4()
    await pg_database._execute(
        "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) "
        "VALUES (96103,NULL,'Slice3 proxy county','SLICE3-PROXY-COUNTY','county','active',1);"
        "INSERT INTO public.tenant(id,org_id,tenant_code,name,type,province,city,status,created_at,updated_at) "
        f"VALUES ({tenant_id},96103,'SLICE3-PROXY-TENANT','Slice3 proxy','store','test','test','active',now(),now());"
        "INSERT INTO public.\"user\"(id,phone,password_hash,role,status,tenant_id) "
        f"VALUES ({user_id},'13' || '8' || repeat('0',8),'test-only','member','active',NULL);"
        "INSERT INTO identity.member(member_id,member_no,creation_source,status,version,created_at,updated_at) "
        f"VALUES ('{proxy_member_id}','M1123456789ABCDEFGHJK','registration','created',1,now(),now());"
        "INSERT INTO public.institution_invitation("
        "invitation_id,institution_name,institution_type,applicant_phone_ciphertext,applicant_phone_digest,"
        "pilot_batch_code,administrative_region_id,code_digest,status,failed_attempts,expires_at,issued_by,issued_at,activated_at,version) VALUES ("
        f"'{institution_invitation_id}','Slice3 proxy','HEALTH_STORE',decode('00','hex'),repeat('e',64),"
        f"'SLICE3',96103,repeat('f',64),'ACTIVATED',0,now()+interval '1 day',{user_id},now(),now(),1);"
        "INSERT INTO public.institution_application("
        "application_id,invitation_id,applicant_user_id,institution_type,status,draft_payload,correction_fields,"
        "current_revision_no,tenant_internal_id,tenant_public_id,service_ready,created_at,updated_at,submitted_at,reviewed_at,version) VALUES ("
        f"'{institution_application_id}','{institution_invitation_id}',{user_id},'HEALTH_STORE','APPROVED',"
        f"'{{\"service_tags\":[\"GLUCOSE_METABOLISM\"]}}'::jsonb,'[]'::jsonb,1,{tenant_id},"
        f"'{tenant_public_id}',false,now(),now(),now(),now(),3);"
        "INSERT INTO public.institution_tenant_origin(tenant_id,tenant_public_id,origin_type,"
        "controlled_application_id) VALUES ("
        f"{tenant_id},'{tenant_public_id}','CONTROLLED_APPLICATION','{institution_application_id}');"
        "INSERT INTO public.institution_service_readiness("
        "tenant_id,readiness_status,reason_codes,qualified_therapist_count,computed_at,"
        "evidence_version,input_digest,result_digest,source_versions,next_expiry_at,version) VALUES ("
        f"{tenant_id},'SERVICE_READY',ARRAY[]::text[],1,now(),1,repeat('1',64),repeat('2',64),"
        "'{}'::jsonb,current_date+30,1)"
    )
    actor = CurrentUser(id=user_id, role="member")
    factory = await get_slice3_session_factory("enrollment_writer")
    secrets = MemberEnrollmentSecrets()
    try:
        pending: list[tuple[int, UUID, str, str]] = []
        for index in range(1, 4):
            phone = "138" + f"{index:08d}"
            async with factory() as session:
                service = MemberEnrollmentService(
                    MemberEnrollmentRepository(session), secrets_port=secrets
                )
                context = MutationContext(
                    actor=actor,
                    tenant_id=tenant_id,
                    tenant_public_id=tenant_public_id,
                    idempotency_key=f"slice3-proxy-create-{index}",
                    request_id=uuid4(),
                )
                invitation_id, short_code = await service.create_invitation(
                    context,
                    CreateMemberInvitationRequest(mode="PROXY_ELDER", phone=phone),
                )
                await session.commit()
            pending.append((index, invitation_id, phone, short_code))

        async def accept(index: int, invitation_id: UUID, phone: str, short_code: str):
            async with factory() as session:
                service = MemberEnrollmentService(
                    MemberEnrollmentRepository(session), secrets_port=secrets
                )
                context = MutationContext(
                    actor=actor,
                    tenant_id=tenant_id,
                    tenant_public_id=tenant_public_id,
                    idempotency_key=f"slice3-proxy-accept-{index}",
                    request_id=uuid4(),
                )
                request = AcceptEnrollmentRequest(
                    invitation_id=invitation_id,
                    phone=phone,
                    short_code=short_code,
                )
                await service.accept_invitation(
                    context,
                    request,
                    actor_member_id=proxy_member_id,
                    adult_eligible=True,
                )
                await session.commit()

        await asyncio.gather(*(accept(*item) for item in pending[:2]))
        with pytest.raises(MemberEnrollmentConflict, match="PROXY_LIMIT_REACHED"):
            await accept(*pending[2])
    finally:
        await dispose_slice3_runtime("enrollment_writer")

    assert await pg_database._fetch_column(
        f"SELECT slot_no FROM public.proxy_grant WHERE proxy_member_id='{proxy_member_id}' ORDER BY slot_no"
    ) == [1, 2]
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM identity.member "
        "WHERE creation_source='controlled_proxy_enrollment' AND status='created'"
    ) == 2
    assert await pg_database._fetch_value(
        "SELECT bool_and(member_no ~ '^M[0123456789ABCDEFGHJKMNPQRSTVWXYZ]{20}$') "
        "FROM identity.member WHERE creation_source='controlled_proxy_enrollment'"
    )


def test_A1代理撤权过期permission与Case接受currentness统一拒绝():
    import inspect
    from app.modules.member_enrollment import api, repository, service

    service_source = inspect.getsource(service)
    repository_source = inspect.getsource(repository)
    migration = (
        Path(__file__).resolve().parents[2]
        / "app/migrations/versions/20260818_0022_phase1_slice3_member_proxy_consent_service_case.py"
    ).read_text(encoding="utf-8")
    assert "IDENTITY_SUBMIT" in service_source
    assert "CONSENT_ACCEPT" in service_source
    assert "DAILY_VIEW" in service_source
    assert "DAILY_INPUT" in service_source
    assert "REPORT_UPLOAD" in service_source
    assert "proxy_authorized" in migration
    assert "slice3_assignment_subject_v1" in repository_source
    assert "require_proxy_permission" in service_source
    assert "PROFILE_VIEW" not in service_source
    assert "CONSENT_MANAGE" not in service_source
    assert "SERVICE_CASE_VIEW" not in service_source
    assert "VITALS_SUPPORT" not in service_source
    assert "INVALID_CURSOR" in inspect.getsource(api._cursor_id)


def test_A3_PII函数只消费成功StepUp且commitUnknown不泄露不重放():
    migration = (
        Path(__file__).resolve().parents[2]
        / "app/migrations/versions/20260818_0022_phase1_slice3_member_proxy_consent_service_case.py"
    ).read_text(encoding="utf-8")
    assert "slice3_reviewer_step_up_budget_v1" in migration
    assert "value_password_valid BOOLEAN" in migration
    assert "value_credential_proof_digest CHAR(64)" in migration
    assert "proof_expires_at-proof_issued_at" in migration.replace(" ", "")
    assert "FOR UPDATE" in migration
    assert "IDENTITY_PII_STEP_UP_RATE_LIMITED" in migration
    assert "pg_catalog.sha256" in migration


def test_B1跨EnrollmentTenantSubjectRevision指针全部由复合FK拒绝():
    migration = (
        Path(__file__).resolve().parents[2]
        / "app/migrations/versions/20260818_0022_phase1_slice3_member_proxy_consent_service_case.py"
    ).read_text(encoding="utf-8").replace(" ", "")
    required = (
        "fk_service_enrollment_current_identity_scope",
        "fk_service_enrollment_current_assignment_scope",
        "fk_service_case_enrollment_scope",
        "fk_service_case_identity_revision_scope",
        "fk_service_case_current_inputs_scope",
        "fk_service_case_assignment_scope",
    )
    for name in required:
        assert name in migration
    assert "member_identity_verification(verification_id,current_revision_id)" in migration


def test_B2_receiptAuditOutbox存在但Aggregate任一字段错误仍为UNKNOWN():
    migration = (
        Path(__file__).resolve().parents[2]
        / "app/migrations/versions/20260818_0022_phase1_slice3_member_proxy_consent_service_case.py"
    ).read_text(encoding="utf-8")
    assert "expected_confirmed_digest IS NULL" not in migration
    for token in (
        "INVITATION_CREATE", "ENROLLMENT_ACCEPT", "IDENTITY_SUBMIT",
        "IDENTITY_REVIEW_DECIDE", "CONSENT_RECORD", "ASSIGNMENT_ACCEPT",
        "IDENTITY_PII_STEP_UP_FAILED", "IDENTITY_PII_STEP_UP_RATE_LIMITED",
    ):
        assert token in migration
