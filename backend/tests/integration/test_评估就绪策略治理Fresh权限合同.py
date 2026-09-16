from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from asyncpg import CheckViolationError, InsufficientPrivilegeError, RaiseError

from app.core.uuid_generator import Uuid7Generator
from app.tasks import slice4_health_data_tasks
from tests.integration.conftest import (
    PgDatabase,
    _build_alembic_config,
    _get_test_database_url,
)

pytestmark = pytest.mark.integration
BACKEND = Path(__file__).resolve().parents[2]
MIGRATION = BACKEND / "app" / "migrations" / "versions" / "20260916_0049_评估就绪策略治理闭合边界.py"


def _run_value(database: PgDatabase, sql: str, *args):
    return asyncio.run(database._fetch_value(sql, *args))


def _run_execute(database: PgDatabase, sql: str, *args) -> None:
    async def execute() -> None:
        connection = await asyncpg.connect(database.database_url)
        try:
            await connection.execute(sql, *args)
        finally:
            await connection.close()

    asyncio.run(execute())


def _confirm(database: PgDatabase, payload: dict) -> dict:
    value = _run_value(
        database,
        "SELECT public.readiness_policy_governance_confirm_v1($1::jsonb)",
        json.dumps(payload, separators=(",", ":"), sort_keys=True),
    )
    return json.loads(value) if isinstance(value, str) else value


def _rewrite_receipt_postimage(database: PgDatabase, receipt_id: UUID) -> None:
    components = _run_value(
        database,
        """
        WITH receipt AS (
          SELECT i.*,convert_from(i.response_ciphertext,'UTF8')::jsonb AS response
          FROM public.slice4_idempotency i WHERE i.receipt_id=$1
        ), audit AS (
          SELECT jsonb_build_array(
            a.audit_id::text,a.event_type,a.aggregate_ref::text,a.actor_user_id,
            encode(a.event_digest,'hex'),a.digest_key_id,
            to_char(a.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
          ) AS snapshot
          FROM public.slice4_audit a,receipt r
          WHERE a.audit_id=(r.response->>'_audit_id')::uuid
        ), targets AS (
          SELECT COALESCE(jsonb_agg(jsonb_build_array(
            o.event_id::text,o.aggregate_type,o.aggregate_ref::text,o.event_type,
            encode(o.payload_digest,'hex'),o.payload_json,
            to_char(o.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
          ) ORDER BY o.event_id),'[]'::jsonb) AS snapshot
          FROM public.slice4_outbox o,receipt r
          WHERE o.payload_json->>'operation_receipt_id'=r.response->>'operation_receipt_id'
        )
        SELECT jsonb_build_array(
          (r.response->'_policy_postimage')::text,
          jsonb_build_array(
            r.receipt_id::text,r.operation,r.scope_ref::text,r.idempotency_key::text,
            encode(r.request_digest,'hex'),r.response_key_id,
            to_char(r.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
          )::text,
          a.snapshot::text,(r.response-'postimage_digest')::text,t.snapshot::text
        )
        FROM receipt r CROSS JOIN audit a CROSS JOIN targets t
        """,
        receipt_id,
    )
    values = json.loads(components) if isinstance(components, str) else components
    labels = ("policy", "receipt", "audit", "response", "targets")
    material = "ASSESSMENT_READINESS_POLICY_POSTIMAGE_V1;" + "".join(
        f"{label}=V{len(value.encode('utf-8'))}:{value};"
        for label, value in zip(labels, values, strict=True)
    )
    digest = hashlib.sha256(material.encode()).hexdigest()
    _run_execute(
        database,
        """
        UPDATE public.slice4_idempotency
        SET postimage_digest=decode($2,'hex'),
            response_ciphertext=convert_to(
              jsonb_set(convert_from(response_ciphertext,'UTF8')::jsonb,
                        '{postimage_digest}',to_jsonb($2::text))::text,'UTF8')
        WHERE receipt_id=$1
        """,
        receipt_id,
        digest,
    )


def _payload(
    *, operation: str, actor: int, actor_role: str, policy_id: UUID,
    ordinal: int, expected_version: int | None = None,
    version_no: int | None = None, reason_code: str | None = None,
) -> dict:
    content = {
        "required_profile_sections": ["BASE_PROFILE"],
        "required_indicators": [{"indicator_code": "SYNTHETIC_SIGNAL", "max_age_days": 30}],
        "allowed_states": ["VERIFIED"],
        "projection_version": 2,
        "rule_version": "synthetic-v1",
    }
    policy_digest = hashlib.sha256(
        json.dumps(content, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    has_content = operation in {"CREATE", "UPDATE_DRAFT"}
    return {
        "actor_user_id": actor,
        "actor_role": actor_role,
        "policy_version_id": str(policy_id),
        "expected_version": expected_version,
        "version_no": version_no,
        "content": content if has_content else None,
        "policy_digest": policy_digest if has_content else None,
        "approval_evidence_ref": "synthetic-approval-package-v1" if has_content else None,
        "approval_package_digest": "a" * 64 if has_content else None,
        "reason_code": reason_code,
        "idempotency_key": str(UUID(int=ordinal + 1000)),
        "request_digest": f"{ordinal:064x}",
        "digest_key_id": "synthetic-k1",
        "operation_receipt_id": str(UUID(int=ordinal + 2000)),
        "audit_id": str(UUID(int=ordinal + 3000)),
        "occurred_at": "2026-09-16T00:00:00+00:00",
    }


def _govern(database: PgDatabase, operation: str, payload: dict) -> dict:
    value = _run_value(
        database,
        "SELECT public.readiness_policy_governance_v1($1,$2::jsonb)",
        operation,
        json.dumps(payload, separators=(",", ":"), sort_keys=True),
    )
    return json.loads(value) if isinstance(value, str) else value


def _preimage_digest(snapshot: object) -> str:
    # PostgreSQL jsonb::text uses a space after array commas; this is an
    # independent reference for the frozen database byte grammar.
    encoded = json.dumps(snapshot, ensure_ascii=True).encode()
    material = (
        b"ASSESSMENT_READINESS_POLICY_PREIMAGE_V1;policy=V"
        + str(len(encoded)).encode()
        + b":"
        + encoded
        + b";"
    )
    return hashlib.sha256(material).hexdigest()


def _governance_row_counts(database: PgDatabase, policy_id: UUID) -> tuple[int, int, int, int]:
    return (
        database.fetch_value(
            "SELECT count(*) FROM public.assessment_readiness_policy_version "
            f"WHERE policy_version_id='{policy_id}'"
        ),
        database.fetch_value(
            f"SELECT count(*) FROM public.slice4_idempotency WHERE scope_ref='{policy_id}'"
        ),
        database.fetch_value(
            f"SELECT count(*) FROM public.slice4_audit WHERE aggregate_ref='{policy_id}'"
        ),
        database.fetch_value(
            f"SELECT count(*) FROM public.slice4_outbox WHERE aggregate_ref='{policy_id}'"
        ),
    )


def test_0049对象单一Head与闭合ACL(pg_database, slice5_rule_governance_writer_database) -> None:
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260916_0049"
    role = os.environ["KG_TEST_SLICE5_RULE_GOVERNANCE_WRITER_ROLE"]
    for signature in (
        "public.readiness_policy_governance_v1(character varying,jsonb)",
        "public.readiness_policy_governance_confirm_v1(jsonb)",
    ):
        assert _run_value(pg_database, "SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature)
    assert _run_value(
        pg_database,
        "SELECT has_table_privilege($1,'public.readiness_policy_governance_read_v1','SELECT')",
        role,
    )
    for table in (
        "assessment_readiness_policy_version", "slice4_idempotency", "slice4_audit", "slice4_outbox"
    ):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert not _run_value(
                pg_database, "SELECT has_table_privilege($1,$2,$3)",
                role, f"public.{table}", privilege,
            )
    assert slice5_rule_governance_writer_database.fetch_value("SELECT current_user") == role


def test_0049历史三态写入兼容且治理状态仍由数据库强约束(pg_database) -> None:
    legacy_id, invalid_id = uuid4(), uuid4()
    common = (
        "required_profile_sections,required_indicators,allowed_states,projection_version,"
        "rule_version,professionally_approved,approved_by,approved_at,effective_from,"
        "retired_at,policy_digest,digest_key_id,created_at"
    )
    try:
        pg_database.execute(
            "INSERT INTO public.assessment_readiness_policy_version("
            f"policy_version_id,version_no,status,{common}) VALUES ("
            f"'{legacy_id}',9964001,'PUBLISHED','[]'::jsonb,'[]'::jsonb,"
            "'[\"VERIFIED\"]'::jsonb,2,'legacy-synthetic-v1',true,9964001,now(),now(),"
            "NULL,decode(repeat('a',64),'hex'),'legacy-k1',now())"
        )
        assert pg_database.fetch_value(
            "SELECT status='PUBLISHED' FROM public.readiness_policy_governance_read_v1 "
            f"WHERE policy_version_id='{legacy_id}'"
        )
        with pytest.raises(CheckViolationError):
            pg_database.execute(
                "INSERT INTO public.assessment_readiness_policy_version("
                f"policy_version_id,version_no,status,{common},author_user_id) VALUES ("
                f"'{invalid_id}',9964002,'APPROVED','[]'::jsonb,'[]'::jsonb,"
                "'[\"VERIFIED\"]'::jsonb,2,'governed-invalid-v1',true,9964001,now(),now(),"
                "NULL,decode(repeat('b',64),'hex'),'synthetic-k1',now(),9964001)"
            )
    finally:
        pg_database.execute(
            "DELETE FROM public.assessment_readiness_policy_version "
            f"WHERE policy_version_id IN ('{legacy_id}','{invalid_id}')"
        )


def test_作者审核管理员显式状态机幂等与持久传播收据(
    pg_database, slice5_rule_governance_writer_database
) -> None:
    author, reviewer, admin = 9964101, 9964102, 9964103
    policy_id = uuid4()
    pg_database.execute(
        'INSERT INTO public."user"(id,phone,password_hash,role,status,created_at,updated_at) VALUES '
        f"({author},'10009964101','synthetic','expert','active',now(),now()),"
        f"({reviewer},'10009964102','synthetic','expert','active',now(),now()),"
        f"({admin},'10009964103','synthetic','sys_admin','active',now(),now())"
    )
    operations = (
        ("CREATE", _payload(operation="CREATE", actor=author, actor_role="expert", policy_id=policy_id, ordinal=1, version_no=99641), "DRAFT"),
        ("SUBMIT", _payload(operation="SUBMIT", actor=author, actor_role="expert", policy_id=policy_id, ordinal=2, expected_version=1), "IN_REVIEW"),
        ("REVIEW_APPROVE", _payload(operation="REVIEW_APPROVE", actor=reviewer, actor_role="expert", policy_id=policy_id, ordinal=3, expected_version=2, reason_code="PROFESSIONAL_POLICY_APPROVED"), "APPROVED"),
    )
    try:
        for operation, payload, expected_status in operations:
            result = _govern(slice5_rule_governance_writer_database, operation, payload)
            assert result["status"] == expected_status
            assert result["operation_receipt_id"] == payload["operation_receipt_id"]
            assert len(result["target_set_digest"]) == 64
            if operation == "CREATE":
                assert _govern(slice5_rule_governance_writer_database, operation, payload) == result
                assert pg_database.fetch_value(
                    "SELECT author_user_id="
                    f"{author} FROM public.assessment_readiness_policy_version "
                    f"WHERE policy_version_id='{policy_id}'"
                )
        assert pg_database.fetch_value(
            f"SELECT status='APPROVED' AND NOT professionally_approved AND approved_by IS NULL "
            f"AND approved_at IS NULL AND author_user_id={author} AND reviewer_user_id={reviewer} "
            f"AND row_version=3 FROM public.assessment_readiness_policy_version WHERE policy_version_id='{policy_id}'"
        )
        assert pg_database.fetch_value(
            f"SELECT count(*) FROM public.slice4_idempotency WHERE scope_ref='{policy_id}' "
            "AND operation LIKE 'READINESS_POLICY_%'"
        ) == 3
        assert pg_database.fetch_value(
            f"SELECT count(*) FROM public.slice4_audit WHERE aggregate_ref='{policy_id}' "
            "AND event_type LIKE 'READINESS_POLICY_%'"
        ) == 3
    finally:
        pg_database.execute(
            f"DELETE FROM public.slice4_outbox WHERE aggregate_ref='{policy_id}';"
            f"DELETE FROM public.slice4_audit WHERE aggregate_ref='{policy_id}';"
            f"DELETE FROM public.slice4_idempotency WHERE scope_ref='{policy_id}';"
            f"DELETE FROM public.assessment_readiness_policy_version WHERE policy_version_id='{policy_id}';"
            f'DELETE FROM public."user" WHERE id IN ({author},{reviewer},{admin})'
        )


def test_CREATE跨随机target重放必须返回首次对象且可确认后像(
    pg_database, slice5_rule_governance_writer_database
) -> None:
    author = 99_641_011
    first_policy_id, retry_policy_id, conflicting_policy_id = uuid4(), uuid4(), uuid4()
    first = _payload(
        operation="CREATE",
        actor=author,
        actor_role="expert",
        policy_id=first_policy_id,
        ordinal=8,
        version_no=996411,
    )
    replay = dict(first)
    replay.update(
        policy_version_id=str(retry_policy_id),
        operation_receipt_id=str(uuid4()),
        audit_id=str(uuid4()),
    )
    conflicting = dict(replay)
    conflicting.update(
        policy_version_id=str(conflicting_policy_id),
        request_digest="f" * 64,
    )
    try:
        pg_database.execute(
            'INSERT INTO public."user"(id,phone,password_hash,role,status,created_at,updated_at) '
            f"VALUES ({author},'10099641011','synthetic','expert','active',now(),now())"
        )
        absent_preimage = ["ABSENT", str(first_policy_id)]
        not_committed_payload = {
            **first,
            "operation": "CREATE",
            "expected_status": "DRAFT",
            "policy_preimage": absent_preimage,
            "preimage_digest": _preimage_digest(absent_preimage),
            "postimage_digest": "0" * 64,
        }
        not_committed = _run_value(
            slice5_rule_governance_writer_database,
            "SELECT public.readiness_policy_governance_confirm_v1($1::jsonb)",
            json.dumps(not_committed_payload, separators=(",", ":"), sort_keys=True),
        )
        not_committed = (
            json.loads(not_committed) if isinstance(not_committed, str) else not_committed
        )
        assert not_committed == {"outcome": "NOT_COMMITTED"}

        drifted_preimage_payload = dict(not_committed_payload)
        drifted_preimage_payload["preimage_digest"] = "f" * 64
        drifted_preimage = _run_value(
            slice5_rule_governance_writer_database,
            "SELECT public.readiness_policy_governance_confirm_v1($1::jsonb)",
            json.dumps(drifted_preimage_payload, separators=(",", ":"), sort_keys=True),
        )
        drifted_preimage = (
            json.loads(drifted_preimage)
            if isinstance(drifted_preimage, str)
            else drifted_preimage
        )
        assert drifted_preimage == {"outcome": "UNKNOWN"}

        first_result = _govern(slice5_rule_governance_writer_database, "CREATE", first)
        replay_result = _govern(slice5_rule_governance_writer_database, "CREATE", replay)

        assert replay_result == first_result
        assert replay_result["policy_version_id"] == str(first_policy_id)
        assert pg_database.fetch_value(
            "SELECT count(*) FROM public.assessment_readiness_policy_version "
            f"WHERE policy_version_id IN ('{first_policy_id}','{retry_policy_id}',"
            f"'{conflicting_policy_id}')"
        ) == 1
        with pytest.raises(RaiseError, match="IDEMPOTENCY_CONFLICT"):
            _govern(slice5_rule_governance_writer_database, "CREATE", conflicting)

        confirmation = {
            **replay,
            "operation": "CREATE",
            "expected_status": "DRAFT",
            "policy_preimage": first_result["_policy_preimage"],
            "preimage_digest": first_result["preimage_digest"],
            "postimage_digest": first_result["postimage_digest"],
        }
        confirmed = _run_value(
            slice5_rule_governance_writer_database,
            "SELECT public.readiness_policy_governance_confirm_v1($1::jsonb)",
            json.dumps(confirmation, separators=(",", ":"), sort_keys=True),
        )
        confirmed = json.loads(confirmed) if isinstance(confirmed, str) else confirmed
        assert confirmed == {"outcome": "COMMITTED"}
    finally:
        pg_database.execute(
            "DELETE FROM public.slice4_outbox "
            f"WHERE aggregate_ref IN ('{first_policy_id}','{retry_policy_id}',"
            f"'{conflicting_policy_id}');"
            "DELETE FROM public.slice4_audit "
            f"WHERE aggregate_ref IN ('{first_policy_id}','{retry_policy_id}',"
            f"'{conflicting_policy_id}');"
            "DELETE FROM public.slice4_idempotency "
            f"WHERE scope_ref IN ('{first_policy_id}','{retry_policy_id}',"
            f"'{conflicting_policy_id}');"
            "DELETE FROM public.assessment_readiness_policy_version "
            f"WHERE policy_version_id IN ('{first_policy_id}','{retry_policy_id}',"
            f"'{conflicting_policy_id}');"
            f'DELETE FROM public."user" WHERE id={author}'
        )


@pytest.mark.parametrize(
    "evidence_ref,ordinal",
    (
        ("synthetic-package-v1", 31),
        ("slice5-synthetic-medical-double-sign", 32),
        ("random-format-valid-package-ref", 33),
    ),
    ids=("synthetic", "slice5", "random"),
)
def test_批准包权威缺失时内部审核可运行但发布恢复无副作用拒绝(
    pg_database,
    slice5_rule_governance_writer_database,
    evidence_ref: str,
    ordinal: int,
) -> None:
    author, reviewer, admin = 99_641_100 + ordinal, 99_641_200 + ordinal, 99_641_300 + ordinal
    policy_id = uuid4()
    create = _payload(
        operation="CREATE",
        actor=author,
        actor_role="expert",
        policy_id=policy_id,
        ordinal=ordinal * 10,
        version_no=996_500 + ordinal,
    )
    create["approval_evidence_ref"] = evidence_ref
    submit = _payload(
        operation="SUBMIT",
        actor=author,
        actor_role="expert",
        policy_id=policy_id,
        ordinal=ordinal * 10 + 1,
        expected_version=1,
    )
    approve = _payload(
        operation="REVIEW_APPROVE",
        actor=reviewer,
        actor_role="expert",
        policy_id=policy_id,
        ordinal=ordinal * 10 + 2,
        expected_version=2,
        reason_code="PROFESSIONAL_POLICY_APPROVED",
    )
    publish = _payload(
        operation="PUBLISH",
        actor=admin,
        actor_role="sys_admin",
        policy_id=policy_id,
        ordinal=ordinal * 10 + 3,
        expected_version=3,
        reason_code="APPROVED_POLICY_RELEASE",
    )
    resume = _payload(
        operation="RESUME",
        actor=admin,
        actor_role="sys_admin",
        policy_id=policy_id,
        ordinal=ordinal * 10 + 4,
        expected_version=3,
        reason_code="POLICY_SAFETY_REVIEW_CLEARED",
    )
    try:
        pg_database.execute(
            'INSERT INTO public."user"(id,phone,password_hash,role,status,created_at,updated_at) VALUES '
            f"({author},'100{author}','synthetic','expert','active',now(),now()),"
            f"({reviewer},'100{reviewer}','synthetic','expert','active',now(),now()),"
            f"({admin},'100{admin}','synthetic','sys_admin','active',now(),now())"
        )
        _govern(slice5_rule_governance_writer_database, "CREATE", create)
        _govern(slice5_rule_governance_writer_database, "SUBMIT", submit)
        result = _govern(slice5_rule_governance_writer_database, "REVIEW_APPROVE", approve)
        assert result["status"] == "APPROVED"
        assert pg_database.fetch_value(
            "SELECT NOT professionally_approved AND approved_by IS NULL AND approved_at IS NULL "
            f"FROM public.assessment_readiness_policy_version WHERE policy_version_id='{policy_id}'"
        )
        before = pg_database.fetch_value(
            "SELECT jsonb_build_array("
            f"(SELECT count(*) FROM public.assessment_readiness_policy_version WHERE policy_version_id='{policy_id}'),"
            f"(SELECT count(*) FROM public.slice4_idempotency WHERE scope_ref='{policy_id}'),"
            f"(SELECT count(*) FROM public.slice4_audit WHERE aggregate_ref='{policy_id}'),"
            f"(SELECT count(*) FROM public.slice4_outbox WHERE aggregate_ref='{policy_id}'))"
        )
        for operation, payload in (("PUBLISH", publish), ("RESUME", resume)):
            with pytest.raises(RaiseError, match="POLICY_MEDICAL_APPROVAL_REQUIRED"):
                _govern(slice5_rule_governance_writer_database, operation, payload)
        after = pg_database.fetch_value(
            "SELECT jsonb_build_array("
            f"(SELECT count(*) FROM public.assessment_readiness_policy_version WHERE policy_version_id='{policy_id}'),"
            f"(SELECT count(*) FROM public.slice4_idempotency WHERE scope_ref='{policy_id}'),"
            f"(SELECT count(*) FROM public.slice4_audit WHERE aggregate_ref='{policy_id}'),"
            f"(SELECT count(*) FROM public.slice4_outbox WHERE aggregate_ref='{policy_id}'))"
        )
        assert after == before
        assert pg_database.fetch_value(
            "SELECT status='APPROVED' AND row_version=3 "
            f"FROM public.assessment_readiness_policy_version WHERE policy_version_id='{policy_id}'"
        )
    finally:
        pg_database.execute(
            f"DELETE FROM public.slice4_outbox WHERE aggregate_ref='{policy_id}';"
            f"DELETE FROM public.slice4_audit WHERE aggregate_ref='{policy_id}';"
            f"DELETE FROM public.slice4_idempotency WHERE scope_ref='{policy_id}';"
            f"DELETE FROM public.assessment_readiness_policy_version WHERE policy_version_id='{policy_id}';"
            f'DELETE FROM public."user" WHERE id IN ({author},{reviewer},{admin})'
        )


def test_旧PUBLISHED策略可由管理员安全停用与退役(
    pg_database, slice5_rule_governance_writer_database
) -> None:
    admin, policy_id = 99_641_390, uuid4()
    common = (
        "required_profile_sections,required_indicators,allowed_states,projection_version,"
        "rule_version,professionally_approved,approved_by,approved_at,effective_from,"
        "retired_at,policy_digest,digest_key_id,created_at"
    )
    suspend = _payload(
        operation="SUSPEND",
        actor=admin,
        actor_role="sys_admin",
        policy_id=policy_id,
        ordinal=391,
        expected_version=1,
        reason_code="POLICY_SAFETY_REVIEW_REQUIRED",
    )
    retire = _payload(
        operation="RETIRE",
        actor=admin,
        actor_role="sys_admin",
        policy_id=policy_id,
        ordinal=392,
        expected_version=2,
        reason_code="POLICY_WITHDRAWN",
    )
    try:
        pg_database.execute(
            'INSERT INTO public."user"(id,phone,password_hash,role,status,created_at,updated_at) '
            f"VALUES ({admin},'100{admin}','synthetic','sys_admin','active',now(),now());"
            "INSERT INTO public.assessment_readiness_policy_version("
            f"policy_version_id,version_no,status,{common}) VALUES ("
            f"'{policy_id}',9965390,'PUBLISHED','[]'::jsonb,'[]'::jsonb,"
            "'[\"VERIFIED\"]'::jsonb,2,'legacy-synthetic-v1',true,9965390,now(),now(),"
            "NULL,decode(repeat('a',64),'hex'),'synthetic-k1',now())"
        )
        assert _govern(slice5_rule_governance_writer_database, "SUSPEND", suspend)["status"] == "SUSPENDED"
        assert _govern(slice5_rule_governance_writer_database, "RETIRE", retire)["status"] == "RETIRED"
    finally:
        pg_database.execute(
            f"DELETE FROM public.slice4_outbox WHERE aggregate_ref='{policy_id}';"
            f"DELETE FROM public.slice4_audit WHERE aggregate_ref='{policy_id}';"
            f"DELETE FROM public.slice4_idempotency WHERE scope_ref='{policy_id}';"
            f"DELETE FROM public.assessment_readiness_policy_version WHERE policy_version_id='{policy_id}';"
            f'DELETE FROM public."user" WHERE id={admin}'
        )


def test_作者不得自审且无关角色无法调用(
    pg_database, slice5_rule_governance_writer_database, application_database
) -> None:
    author, policy_id = 9964201, uuid4()
    pg_database.execute(
        'INSERT INTO public."user"(id,phone,password_hash,role,status,created_at,updated_at) VALUES '
        f"({author},'10009964201','synthetic','expert','active',now(),now())"
    )
    create = _payload(operation="CREATE", actor=author, actor_role="expert", policy_id=policy_id, ordinal=11, version_no=99642)
    submit = _payload(operation="SUBMIT", actor=author, actor_role="expert", policy_id=policy_id, ordinal=12, expected_version=1)
    review = _payload(operation="REVIEW_APPROVE", actor=author, actor_role="expert", policy_id=policy_id, ordinal=13, expected_version=2, reason_code="PROFESSIONAL_POLICY_APPROVED")
    try:
        _govern(slice5_rule_governance_writer_database, "CREATE", create)
        _govern(slice5_rule_governance_writer_database, "SUBMIT", submit)
        with pytest.raises(RaiseError, match="READINESS_POLICY_STATE_CONFLICT"):
            _govern(slice5_rule_governance_writer_database, "REVIEW_APPROVE", review)
        with pytest.raises(InsufficientPrivilegeError):
            _govern(application_database, "CREATE", create)
    finally:
        pg_database.execute(
            f"DELETE FROM public.slice4_audit WHERE aggregate_ref='{policy_id}';"
            f"DELETE FROM public.slice4_idempotency WHERE scope_ref='{policy_id}';"
            f"DELETE FROM public.assessment_readiness_policy_version WHERE policy_version_id='{policy_id}';"
            f'DELETE FROM public."user" WHERE id={author}'
        )


@pytest.mark.parametrize(
    "currentness_column,actor,ordinal",
    (
        ("exited_at", 99_642_211, 211),
        ("deletion_requested_at", 99_642_212, 212),
    ),
    ids=("exited", "deletion-requested"),
)
def test_I2_writer执行前拒绝已退出或待删除账号且治理事实零写(
    pg_database,
    slice5_rule_governance_writer_database,
    currentness_column: str,
    actor: int,
    ordinal: int,
) -> None:
    policy_id = uuid4()
    pg_database.execute(
        'INSERT INTO public."user"(id,phone,password_hash,role,status,created_at,updated_at,'
        f"{currentness_column}) VALUES ({actor},'100{actor}','synthetic','expert','active',"
        "now(),now(),now())"
    )
    payload = _payload(
        operation="CREATE",
        actor=actor,
        actor_role="expert",
        policy_id=policy_id,
        ordinal=ordinal,
        version_no=99_642_200 + ordinal,
    )
    try:
        with pytest.raises(RaiseError, match="READINESS_POLICY_GOVERNANCE_FORBIDDEN"):
            _govern(slice5_rule_governance_writer_database, "CREATE", payload)
        assert _governance_row_counts(pg_database, policy_id) == (0, 0, 0, 0)
    finally:
        pg_database.execute(
            f"DELETE FROM public.slice4_outbox WHERE aggregate_ref='{policy_id}';"
            f"DELETE FROM public.slice4_audit WHERE aggregate_ref='{policy_id}';"
            f"DELETE FROM public.slice4_idempotency WHERE scope_ref='{policy_id}';"
            "DELETE FROM public.assessment_readiness_policy_version "
            f"WHERE policy_version_id='{policy_id}';"
            f'DELETE FROM public."user" WHERE id={actor}'
        )


def test_I2_confirmation只核验历史提交证据不机械套用当前账号状态(
    pg_database, slice5_rule_governance_writer_database
) -> None:
    actor, policy_id = 99_642_213, uuid4()
    payload = _payload(
        operation="CREATE",
        actor=actor,
        actor_role="expert",
        policy_id=policy_id,
        ordinal=213,
        version_no=99_642_413,
    )
    try:
        pg_database.execute(
            'INSERT INTO public."user"(id,phone,password_hash,role,status,created_at,updated_at) '
            f"VALUES ({actor},'100{actor}','synthetic','expert','active',now(),now())"
        )
        result = _govern(slice5_rule_governance_writer_database, "CREATE", payload)
        pg_database.execute(
            f'UPDATE public."user" SET exited_at=now() WHERE id={actor}'
        )
        confirmation = {
            **payload,
            "operation": "CREATE",
            "expected_status": "DRAFT",
            "policy_preimage": result["_policy_preimage"],
            "preimage_digest": result["preimage_digest"],
            "postimage_digest": result["postimage_digest"],
        }
        assert _confirm(slice5_rule_governance_writer_database, confirmation) == {
            "outcome": "COMMITTED"
        }
    finally:
        pg_database.execute(
            f"DELETE FROM public.slice4_outbox WHERE aggregate_ref='{policy_id}';"
            f"DELETE FROM public.slice4_audit WHERE aggregate_ref='{policy_id}';"
            f"DELETE FROM public.slice4_idempotency WHERE scope_ref='{policy_id}';"
            "DELETE FROM public.assessment_readiness_policy_version "
            f"WHERE policy_version_id='{policy_id}';"
            f'DELETE FROM public."user" WHERE id={actor}'
        )


@pytest.mark.parametrize(
    "case_name",
    ("expected-version-missing", "expected-version-null", "reason-null", "legacy-author-null"),
)
def test_I3_writer必填值NULL与legacy无作者草稿必须fail_closed零写(
    pg_database, slice5_rule_governance_writer_database, case_name: str
) -> None:
    actor = {
        "expected-version-missing": 99_642_221,
        "expected-version-null": 99_642_222,
        "reason-null": 99_642_223,
        "legacy-author-null": 99_642_224,
    }[case_name]
    policy_id = uuid4()
    role = "sys_admin" if case_name == "reason-null" else "expert"
    operation = "SUSPEND" if case_name == "reason-null" else "SUBMIT"
    ordinal = actor % 1000
    payload = _payload(
        operation=operation,
        actor=actor,
        actor_role=role,
        policy_id=policy_id,
        ordinal=ordinal,
        expected_version=1,
        reason_code=None,
    )
    if case_name == "expected-version-missing":
        payload.pop("expected_version")
    elif case_name == "expected-version-null":
        payload["expected_version"] = None
    status = "PUBLISHED" if case_name == "reason-null" else "DRAFT"
    try:
        pg_database.execute(
            'INSERT INTO public."user"(id,phone,password_hash,role,status,created_at,updated_at) '
            f"VALUES ({actor},'100{actor}','synthetic','{role}','active',now(),now())"
        )
        pg_database.execute(
            "INSERT INTO public.assessment_readiness_policy_version("
            "policy_version_id,version_no,status,required_profile_sections,required_indicators,"
            "allowed_states,projection_version,rule_version,professionally_approved,approved_by,"
            "approved_at,effective_from,retired_at,policy_digest,digest_key_id,created_at) VALUES ("
            f"'{policy_id}',{99_642_500 + ordinal},'{status}','[]'::jsonb,'[]'::jsonb,"
            "'[\"VERIFIED\"]'::jsonb,2,'legacy-i3-v1',true,NULL,NULL,now(),NULL,"
            "decode(repeat('a',64),'hex'),'legacy-k1',now())"
        )
        before = _governance_row_counts(pg_database, policy_id)
        with pytest.raises(
            RaiseError,
            match=(
                "READINESS_POLICY_INVALID|READINESS_POLICY_REASON_INVALID|"
                "READINESS_POLICY_STATE_CONFLICT"
            ),
        ):
            _govern(slice5_rule_governance_writer_database, operation, payload)
        assert _governance_row_counts(pg_database, policy_id) == before
        assert _run_value(
            pg_database,
            "SELECT status=$2 AND row_version=1 FROM "
            "public.assessment_readiness_policy_version WHERE policy_version_id=$1",
            policy_id,
            status,
        )
    finally:
        pg_database.execute(
            f"DELETE FROM public.slice4_outbox WHERE aggregate_ref='{policy_id}';"
            f"DELETE FROM public.slice4_audit WHERE aggregate_ref='{policy_id}';"
            f"DELETE FROM public.slice4_idempotency WHERE scope_ref='{policy_id}';"
            "DELETE FROM public.assessment_readiness_policy_version "
            f"WHERE policy_version_id='{policy_id}';"
            f'DELETE FROM public."user" WHERE id={actor}'
        )


@pytest.mark.parametrize(
    "field_name",
    ("operation_receipt_id", "approval_package_digest"),
    ids=("receipt-id-null", "create-approval-digest-null"),
)
def test_I3_writer其余受控必填字段JSON_null必须稳定拒绝零写(
    pg_database,
    slice5_rule_governance_writer_database,
    field_name: str,
) -> None:
    actor, policy_id = 99_642_225, uuid4()
    payload = _payload(
        operation="CREATE",
        actor=actor,
        actor_role="expert",
        policy_id=policy_id,
        ordinal=225 if field_name == "operation_receipt_id" else 226,
        version_no=99_642_725 if field_name == "operation_receipt_id" else 99_642_726,
    )
    payload[field_name] = None
    try:
        pg_database.execute(
            'INSERT INTO public."user"(id,phone,password_hash,role,status,created_at,updated_at) '
            f"VALUES ({actor},'100{actor}','synthetic','expert','active',now(),now())"
        )
        with pytest.raises(RaiseError, match="READINESS_POLICY_INVALID"):
            _govern(slice5_rule_governance_writer_database, "CREATE", payload)
        assert _governance_row_counts(pg_database, policy_id) == (0, 0, 0, 0)
    finally:
        pg_database.execute(
            f"DELETE FROM public.slice4_outbox WHERE aggregate_ref='{policy_id}';"
            f"DELETE FROM public.slice4_audit WHERE aggregate_ref='{policy_id}';"
            f"DELETE FROM public.slice4_idempotency WHERE scope_ref='{policy_id}';"
            "DELETE FROM public.assessment_readiness_policy_version "
            f"WHERE policy_version_id='{policy_id}';"
            f'DELETE FROM public."user" WHERE id={actor}'
        )


def test_I4未发布APPROVED不得直接退役且全部治理事实零写(
    pg_database, slice5_rule_governance_writer_database
) -> None:
    author, reviewer, admin = 99_642_231, 99_642_232, 99_642_233
    policy_id = uuid4()
    operations = (
        (
            "CREATE",
            _payload(
                operation="CREATE",
                actor=author,
                actor_role="expert",
                policy_id=policy_id,
                ordinal=231,
                version_no=99_642_731,
            ),
        ),
        (
            "SUBMIT",
            _payload(
                operation="SUBMIT",
                actor=author,
                actor_role="expert",
                policy_id=policy_id,
                ordinal=232,
                expected_version=1,
            ),
        ),
        (
            "REVIEW_APPROVE",
            _payload(
                operation="REVIEW_APPROVE",
                actor=reviewer,
                actor_role="expert",
                policy_id=policy_id,
                ordinal=233,
                expected_version=2,
                reason_code="PROFESSIONAL_POLICY_APPROVED",
            ),
        ),
    )
    retire = _payload(
        operation="RETIRE",
        actor=admin,
        actor_role="sys_admin",
        policy_id=policy_id,
        ordinal=234,
        expected_version=3,
        reason_code="POLICY_WITHDRAWN",
    )
    try:
        pg_database.execute(
            'INSERT INTO public."user"(id,phone,password_hash,role,status,created_at,updated_at) VALUES '
            f"({author},'100{author}','synthetic','expert','active',now(),now()),"
            f"({reviewer},'100{reviewer}','synthetic','expert','active',now(),now()),"
            f"({admin},'100{admin}','synthetic','sys_admin','active',now(),now())"
        )
        for operation, payload in operations:
            _govern(slice5_rule_governance_writer_database, operation, payload)
        before = _governance_row_counts(pg_database, policy_id)
        with pytest.raises(RaiseError, match="READINESS_POLICY_STATE_CONFLICT"):
            _govern(slice5_rule_governance_writer_database, "RETIRE", retire)
        assert _governance_row_counts(pg_database, policy_id) == before
        assert _run_value(
            pg_database,
            "SELECT status='APPROVED' AND NOT professionally_approved AND row_version=3 "
            "FROM public.assessment_readiness_policy_version WHERE policy_version_id=$1",
            policy_id,
        )
    finally:
        pg_database.execute(
            f"DELETE FROM public.slice4_outbox WHERE aggregate_ref='{policy_id}';"
            f"DELETE FROM public.slice4_audit WHERE aggregate_ref='{policy_id}';"
            f"DELETE FROM public.slice4_idempotency WHERE scope_ref='{policy_id}';"
            "DELETE FROM public.assessment_readiness_policy_version "
            f"WHERE policy_version_id='{policy_id}';"
            f'DELETE FROM public."user" WHERE id IN ({author},{reviewer},{admin})'
        )


def test_confirmation历史链拒绝NEEDS_CORRECTION直接SUBMIT伪造后像(
    pg_database, slice5_rule_governance_writer_database
) -> None:
    author, reviewer = 99_642_241, 99_642_242
    legal_policy, forged_policy = uuid4(), uuid4()

    def run_until_updated(policy_id: UUID, ordinal: int) -> tuple[dict, dict, dict]:
        create = _payload(
            operation="CREATE",
            actor=author,
            actor_role="expert",
            policy_id=policy_id,
            ordinal=ordinal,
            version_no=99_642_800 + ordinal,
        )
        submit = _payload(
            operation="SUBMIT",
            actor=author,
            actor_role="expert",
            policy_id=policy_id,
            ordinal=ordinal + 1,
            expected_version=1,
        )
        correction = _payload(
            operation="REVIEW_CORRECTION",
            actor=reviewer,
            actor_role="expert",
            policy_id=policy_id,
            ordinal=ordinal + 2,
            expected_version=2,
            reason_code="POLICY_CONTENT_CORRECTION_REQUIRED",
        )
        update = _payload(
            operation="UPDATE_DRAFT",
            actor=author,
            actor_role="expert",
            policy_id=policy_id,
            ordinal=ordinal + 3,
            expected_version=3,
        )
        _govern(slice5_rule_governance_writer_database, "CREATE", create)
        _govern(slice5_rule_governance_writer_database, "SUBMIT", submit)
        correction_result = _govern(
            slice5_rule_governance_writer_database, "REVIEW_CORRECTION", correction
        )
        update_result = _govern(
            slice5_rule_governance_writer_database, "UPDATE_DRAFT", update
        )
        confirmation = {
            **correction,
            "operation": "REVIEW_CORRECTION",
            "expected_status": "NEEDS_CORRECTION",
            "policy_preimage": correction_result["_policy_preimage"],
            "preimage_digest": correction_result["preimage_digest"],
            "postimage_digest": correction_result["postimage_digest"],
        }
        return confirmation, update, update_result

    try:
        pg_database.execute(
            'INSERT INTO public."user"(id,phone,password_hash,role,status,created_at,updated_at) VALUES '
            f"({author},'100{author}','synthetic','expert','active',now(),now()),"
            f"({reviewer},'100{reviewer}','synthetic','expert','active',now(),now())"
        )

        legal_confirmation, _, _ = run_until_updated(legal_policy, 241)
        legal_submit = _payload(
            operation="SUBMIT",
            actor=author,
            actor_role="expert",
            policy_id=legal_policy,
            ordinal=245,
            expected_version=4,
        )
        _govern(slice5_rule_governance_writer_database, "SUBMIT", legal_submit)
        assert _confirm(slice5_rule_governance_writer_database, legal_confirmation) == {
            "outcome": "COMMITTED"
        }

        forged_confirmation, _, forged_update_result = run_until_updated(
            forged_policy, 251
        )
        forged_receipt = UUID(forged_update_result["operation_receipt_id"])
        forged_audit = UUID(forged_update_result["_audit_id"])
        _run_execute(
            pg_database,
            "UPDATE public.assessment_readiness_policy_version "
            "SET status='IN_REVIEW' WHERE policy_version_id=$1",
            forged_policy,
        )
        _run_execute(
            pg_database,
            "UPDATE public.slice4_idempotency SET operation='READINESS_POLICY_SUBMIT', "
            "response_ciphertext=convert_to(jsonb_set(jsonb_set("
            "convert_from(response_ciphertext,'UTF8')::jsonb,'{status}',"
            "to_jsonb('IN_REVIEW'::text)),'{_policy_postimage,2}',"
            "to_jsonb('IN_REVIEW'::text))::text,'UTF8') WHERE receipt_id=$1",
            forged_receipt,
        )
        _run_execute(
            pg_database,
            "UPDATE public.slice4_audit SET event_type='READINESS_POLICY_SUBMIT' "
            "WHERE audit_id=$1",
            forged_audit,
        )
        _rewrite_receipt_postimage(pg_database, forged_receipt)
        assert _confirm(slice5_rule_governance_writer_database, forged_confirmation) == {
            "outcome": "UNKNOWN"
        }
    finally:
        pg_database.execute(
            f"DELETE FROM public.slice4_outbox WHERE aggregate_ref IN ('{legal_policy}','{forged_policy}');"
            f"DELETE FROM public.slice4_audit WHERE aggregate_ref IN ('{legal_policy}','{forged_policy}');"
            f"DELETE FROM public.slice4_idempotency WHERE scope_ref IN ('{legal_policy}','{forged_policy}');"
            "DELETE FROM public.assessment_readiness_policy_version "
            f"WHERE policy_version_id IN ('{legal_policy}','{forged_policy}');"
            f'DELETE FROM public."user" WHERE id IN ({author},{reviewer})'
        )


def test_嵌套非法内容必须稳定拒绝且治理表零写(
    pg_database, slice5_rule_governance_writer_database
) -> None:
    author, policy_id = 9964251, uuid4()
    pg_database.execute(
        'INSERT INTO public."user"(id,phone,password_hash,role,status,created_at,updated_at) VALUES '
        f"({author},'10009964251','synthetic','expert','active',now(),now())"
    )
    payload = _payload(
        operation="CREATE",
        actor=author,
        actor_role="expert",
        policy_id=policy_id,
        ordinal=15,
        version_no=996425,
    )
    payload["content"]["required_indicators"] = [
        {"indicator_code": "SYNTHETIC_SIGNAL", "max_age_days": 0, "unknown": True}
    ]
    try:
        with pytest.raises(RaiseError, match="READINESS_POLICY_INVALID"):
            _govern(slice5_rule_governance_writer_database, "CREATE", payload)
        assert pg_database.fetch_value(
            f"SELECT count(*) FROM public.assessment_readiness_policy_version "
            f"WHERE policy_version_id='{policy_id}'"
        ) == 0
        assert pg_database.fetch_value(
            f"SELECT count(*) FROM public.slice4_idempotency WHERE scope_ref='{policy_id}'"
        ) == 0
        assert pg_database.fetch_value(
            f"SELECT count(*) FROM public.slice4_audit WHERE aggregate_ref='{policy_id}'"
        ) == 0
    finally:
        pg_database.execute(f'DELETE FROM public."user" WHERE id={author}')


def test_大批量传播目标冻结重放确认与重复状态切换(
    pg_database, slice5_rule_governance_writer_database
) -> None:
    seed_module = importlib.import_module(
        "tests.integration.test_一期切片4ProfileRoot、IdentitySummary与Runtime权限数据库合同"
    )
    generated = Uuid7Generator()

    async def seed_cases() -> None:
        member_id = generated.generate()
        await seed_module._seed_case(
            pg_database,
            ordinal=500,
            mode="SELF",
            tenant_id=9_970_000,
            tenant_public_id=generated.generate(),
            actor_user_id=99_700_000,
            actor_member_id=member_id,
            subject_member_id=member_id,
            proxy_member_id=None,
        )

    asyncio.run(seed_cases())
    therapist_id = pg_database.fetch_value(
        "SELECT therapist_id FROM public.therapist_profile WHERE tenant_id=9970000"
    )
    issuer_id = 99_700_200
    for index in range(1, 101):
        member_id = generated.generate()
        invitation_id = generated.generate()
        enrollment_id = generated.generate()
        verification_id = generated.generate()
        revision_id = generated.generate()
        assignment_id = generated.generate()
        case_id = generated.generate()
        digest = hashlib.sha256(f"governance-bulk-{index}".encode()).hexdigest()
        pg_database.execute(
            "BEGIN; SET CONSTRAINTS ALL DEFERRED;"
            "INSERT INTO identity.member(member_id,member_no,creation_source,status,version,created_at,updated_at) VALUES ("
            f"'{member_id}','RG{index:019d}','registration','created',1,now(),now());"
            "INSERT INTO public.member_service_invitation(invitation_id,tenant_id,mode,phone_ciphertext,"
            "phone_key_id,phone_digest,phone_digest_key_id,phone_masked,code_digest,code_key_id,status,"
            "failed_attempts,expires_at,issued_by,issued_at,accepted_at,revoked_at,version) VALUES ("
            f"'{invitation_id}',9970000,'SELF',decode('00','hex'),'k1','{digest}','k1',"
            f"'*******0000','{hashlib.sha256(('code-' + str(index)).encode()).hexdigest()}',"
            f"'k1','ACCEPTED',0,now()+interval '1 day',{issuer_id},now(),now(),NULL,1);"
            "INSERT INTO public.service_enrollment(enrollment_id,invitation_id,tenant_id,subject_member_id,"
            "proxy_member_id,mode,status,service_scope_tags,current_identity_verification_id,current_assignment_id,"
            "service_case_id,accepted_at,identity_verified_at,case_created_at,created_at,updated_at,version) VALUES ("
            f"'{enrollment_id}','{invitation_id}',9970000,'{member_id}',NULL,'SELF','CASE_CREATED',"
            "'[\"GLUCOSE_METABOLISM\"]'::jsonb,NULL,NULL,NULL,"
            "now(),now(),now(),now(),now(),10);"
            "INSERT INTO public.member_identity_verification(verification_id,enrollment_id,member_id,"
            "current_revision_id,status,institution_decision_id,platform_decision_id,submitted_at,"
            "institution_checked_at,platform_decided_at,version) VALUES ("
            f"'{verification_id}','{enrollment_id}','{member_id}','{revision_id}','APPROVED',NULL,NULL,"
            "now(),now(),now(),3);"
            "INSERT INTO public.member_identity_revision(revision_id,verification_id,revision_no,document_type,"
            "real_name_ciphertext,real_name_key_id,id_ciphertext,id_key_id,birth_date_ciphertext,birth_date_key_id,"
            "id_masked,identity_fingerprint,fingerprint_key_id,input_digest,submitted_by_member_id,created_at) VALUES ("
            f"'{revision_id}','{verification_id}',1,'PRC_RESIDENT_ID',decode('00','hex'),'k1',decode('01','hex'),"
            f"'k1',decode('02','hex'),'k1','**************0000','{digest}','k1','{digest}','{member_id}',now());"
            "INSERT INTO public.primary_therapist_assignment(assignment_id,enrollment_id,tenant_id,subject_member_id,"
            "therapist_id,status,service_scope_tags,reason_code,service_case_id,created_by,created_at,decided_at,version) VALUES ("
            f"'{assignment_id}','{enrollment_id}',9970000,'{member_id}','{therapist_id}','ACCEPTED',"
            f"'[\"GLUCOSE_METABOLISM\"]'::jsonb,NULL,'{case_id}',{issuer_id},now(),now(),2);"
            "INSERT INTO public.service_case(case_id,enrollment_id,subject_member_id,tenant_id,primary_therapist_id,"
            "assignment_id,status,identity_verification_id,identity_revision_id,consent_set_digest,"
            "readiness_evidence_version,readiness_result_digest,service_scope_tags,created_at,updated_at,version) VALUES ("
            f"'{case_id}','{enrollment_id}','{member_id}',9970000,'{therapist_id}','{assignment_id}',"
            f"'PREPARING','{verification_id}','{revision_id}','{digest}',1,'{digest}',"
            "'[\"GLUCOSE_METABOLISM\"]'::jsonb,now(),now(),1);"
            "UPDATE public.service_enrollment SET current_identity_verification_id="
            f"'{verification_id}',current_assignment_id='{assignment_id}',service_case_id='{case_id}' "
            f"WHERE enrollment_id='{enrollment_id}';COMMIT;"
        )
    author, reviewer, admin = 99_710_001, 99_710_002, 99_710_003
    policy_id = uuid4()
    pg_database.execute(
        'INSERT INTO public."user"(id,phone,password_hash,role,status,created_at,updated_at) VALUES '
        f"({author},'10099710001','synthetic','expert','active',now(),now()),"
        f"({reviewer},'10099710002','synthetic','expert','active',now(),now()),"
        f"({admin},'10099710003','synthetic','sys_admin','active',now(),now())"
    )
    pg_database.execute(
        "INSERT INTO public.assessment_readiness_policy_version("
        "policy_version_id,version_no,status,required_profile_sections,required_indicators,"
        "allowed_states,projection_version,rule_version,professionally_approved,approved_by,"
        "approved_at,effective_from,retired_at,policy_digest,digest_key_id,created_at) VALUES ("
        f"'{policy_id}',99710,'PUBLISHED','[]'::jsonb,'[]'::jsonb,'[\"VERIFIED\"]'::jsonb,"
        "2,'legacy-synthetic-v1',true,NULL,NULL,now(),NULL,decode(repeat('a',64),'hex'),"
        "'legacy-k1',now())"
    )
    steps = (
        ("SUSPEND", admin, "sys_admin", 105, 1, None, "POLICY_SAFETY_REVIEW_REQUIRED"),
        ("RETIRE", admin, "sys_admin", 106, 2, None, "POLICY_WITHDRAWN"),
    )
    results: list[tuple[str, dict, dict]] = []
    try:
        for operation, actor, role, ordinal, expected, version_no, reason in steps:
            payload = _payload(
                operation=operation,
                actor=actor,
                actor_role=role,
                policy_id=policy_id,
                ordinal=ordinal,
                expected_version=expected,
                version_no=version_no,
                reason_code=reason,
            )
            result = _govern(slice5_rule_governance_writer_database, operation, payload)
            results.append((operation, payload, result))

        propagation = results
        assert all(result["target_count"] == 101 for _, _, result in propagation)
        assert len({result["operation_receipt_id"] for _, _, result in propagation}) == 2
        assert pg_database.fetch_value(
            f"SELECT count(*) FROM public.slice4_outbox WHERE aggregate_ref='{policy_id}' "
            "AND event_type='READINESS_POLICY_CHANGED'"
        ) == 202
        last_operation, last_payload, last_result = results[-1]
        assert _govern(slice5_rule_governance_writer_database, last_operation, last_payload) == last_result
        assert pg_database.fetch_value(
            f"SELECT count(*) FROM public.slice4_outbox WHERE aggregate_ref='{policy_id}'"
        ) == 202

        conflicting = dict(results[0][1])
        conflicting["request_digest"] = "f" * 64
        with pytest.raises(RaiseError, match="IDEMPOTENCY_CONFLICT"):
            _govern(slice5_rule_governance_writer_database, "SUSPEND", conflicting)

        receipt = last_result["operation_receipt_id"]
        pg_database.execute(
            "UPDATE public.slice4_outbox SET status='DELIVERED',attempts=1,delivered_at=now(),"
            "lease_owner=NULL,lease_until=NULL "
            f"WHERE payload_json->>'operation_receipt_id'='{receipt}'"
        )
        confirmation = {
            **last_payload,
            "operation": last_operation,
            "expected_status": "RETIRED",
            "policy_preimage": last_result["_policy_preimage"],
            "preimage_digest": last_result["preimage_digest"],
            "postimage_digest": last_result["postimage_digest"],
        }
        confirmed = _run_value(
            slice5_rule_governance_writer_database,
            "SELECT public.readiness_policy_governance_confirm_v1($1::jsonb)",
            json.dumps(confirmation, separators=(",", ":"), sort_keys=True),
        )
        confirmed = json.loads(confirmed) if isinstance(confirmed, str) else confirmed
        assert confirmed == {"outcome": "COMMITTED"}

        historical_operation, historical_payload, historical_result = results[0]
        historical_confirmation = {
            **historical_payload,
            "operation": historical_operation,
            "expected_status": historical_result["status"],
            "policy_preimage": historical_result["_policy_preimage"],
            "preimage_digest": historical_result["preimage_digest"],
            "postimage_digest": historical_result["postimage_digest"],
        }
        historical_confirmed = _run_value(
            slice5_rule_governance_writer_database,
            "SELECT public.readiness_policy_governance_confirm_v1($1::jsonb)",
            json.dumps(historical_confirmation, separators=(",", ":"), sort_keys=True),
        )
        historical_confirmed = (
            json.loads(historical_confirmed)
            if isinstance(historical_confirmed, str)
            else historical_confirmed
        )
        assert historical_confirmed == {"outcome": "COMMITTED"}

        pg_database.execute(
            "UPDATE public.assessment_readiness_policy_version "
            "SET rule_version='untraced-tamper-v1',row_version=row_version+1 "
            f"WHERE policy_version_id='{policy_id}'"
        )
        untraced_later_drift = _run_value(
            slice5_rule_governance_writer_database,
            "SELECT public.readiness_policy_governance_confirm_v1($1::jsonb)",
            json.dumps(historical_confirmation, separators=(",", ":"), sort_keys=True),
        )
        untraced_later_drift = (
            json.loads(untraced_later_drift)
            if isinstance(untraced_later_drift, str)
            else untraced_later_drift
        )
        assert untraced_later_drift == {"outcome": "UNKNOWN"}
        pg_database.execute(
            "UPDATE public.assessment_readiness_policy_version "
            "SET rule_version='legacy-synthetic-v1',row_version=row_version-1 "
            f"WHERE policy_version_id='{policy_id}'"
        )

        later_receipt_id = UUID(last_result["operation_receipt_id"])
        later_audit_id = UUID(last_result["_audit_id"])
        receipt_row = asyncio.run(
            pg_database._fetch_rows(
                "SELECT receipt_id,operation,scope_ref,idempotency_key,request_digest,"
                "postimage_digest,response_ciphertext,response_key_id,created_at "
                "FROM public.slice4_idempotency WHERE receipt_id=$1",
                later_receipt_id,
            )
        )[0]
        _run_execute(
            pg_database,
            "DELETE FROM public.slice4_idempotency WHERE receipt_id=$1",
            later_receipt_id,
        )
        assert _confirm(slice5_rule_governance_writer_database, historical_confirmation) == {
            "outcome": "UNKNOWN"
        }
        _run_execute(
            pg_database,
            "INSERT INTO public.slice4_idempotency("
            "receipt_id,operation,scope_ref,idempotency_key,request_digest,postimage_digest,"
            "response_ciphertext,response_key_id,created_at) "
            "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)",
            *receipt_row.values(),
        )

        duplicate_receipt_id, duplicate_key = uuid4(), uuid4()
        _run_execute(
            pg_database,
            "INSERT INTO public.slice4_idempotency("
            "receipt_id,operation,scope_ref,idempotency_key,request_digest,postimage_digest,"
            "response_ciphertext,response_key_id,created_at) "
            "SELECT $2,operation,scope_ref,$3,request_digest,postimage_digest,"
            "response_ciphertext,response_key_id,created_at "
            "FROM public.slice4_idempotency WHERE receipt_id=$1",
            later_receipt_id,
            duplicate_receipt_id,
            duplicate_key,
        )
        assert _confirm(slice5_rule_governance_writer_database, historical_confirmation) == {
            "outcome": "UNKNOWN"
        }
        _run_execute(
            pg_database,
            "DELETE FROM public.slice4_idempotency WHERE receipt_id=$1",
            duplicate_receipt_id,
        )

        _run_execute(
            pg_database,
            "UPDATE public.slice4_idempotency SET operation='READINESS_POLICY_SUBMIT' "
            "WHERE receipt_id=$1",
            later_receipt_id,
        )
        _run_execute(
            pg_database,
            "UPDATE public.slice4_audit SET event_type='READINESS_POLICY_SUBMIT' "
            "WHERE audit_id=$1",
            later_audit_id,
        )
        _rewrite_receipt_postimage(pg_database, later_receipt_id)
        assert _confirm(slice5_rule_governance_writer_database, historical_confirmation) == {
            "outcome": "UNKNOWN"
        }
        _run_execute(
            pg_database,
            "UPDATE public.slice4_idempotency SET operation='READINESS_POLICY_RETIRE' "
            "WHERE receipt_id=$1",
            later_receipt_id,
        )
        _run_execute(
            pg_database,
            "UPDATE public.slice4_audit SET event_type='READINESS_POLICY_RETIRE' "
            "WHERE audit_id=$1",
            later_audit_id,
        )
        _rewrite_receipt_postimage(pg_database, later_receipt_id)
        assert _confirm(slice5_rule_governance_writer_database, historical_confirmation) == {
            "outcome": "COMMITTED"
        }

        _run_execute(
            pg_database,
            "UPDATE public.slice4_idempotency SET response_key_id='tampered-k1' "
            "WHERE receipt_id=$1",
            later_receipt_id,
        )
        assert _confirm(slice5_rule_governance_writer_database, historical_confirmation) == {
            "outcome": "UNKNOWN"
        }
        _run_execute(
            pg_database,
            "UPDATE public.slice4_idempotency SET response_key_id='synthetic-k1' "
            "WHERE receipt_id=$1",
            later_receipt_id,
        )
        _run_execute(
            pg_database,
            "UPDATE public.slice4_audit SET digest_key_id='tampered-k1' WHERE audit_id=$1",
            later_audit_id,
        )
        assert _confirm(slice5_rule_governance_writer_database, historical_confirmation) == {
            "outcome": "UNKNOWN"
        }
        _run_execute(
            pg_database,
            "UPDATE public.slice4_audit SET digest_key_id='synthetic-k1' WHERE audit_id=$1",
            later_audit_id,
        )
        later_target_id = _run_value(
            pg_database,
            "SELECT event_id FROM public.slice4_outbox "
            "WHERE payload_json->>'operation_receipt_id'=$1 ORDER BY event_id LIMIT 1",
            str(later_receipt_id),
        )
        _run_execute(
            pg_database,
            "UPDATE public.slice4_outbox SET aggregate_type='TAMPERED' WHERE event_id=$1",
            later_target_id,
        )
        assert _confirm(slice5_rule_governance_writer_database, historical_confirmation) == {
            "outcome": "UNKNOWN"
        }
        _run_execute(
            pg_database,
            "UPDATE public.slice4_outbox SET aggregate_type='READINESS_POLICY' WHERE event_id=$1",
            later_target_id,
        )

        original_response = _run_value(
            pg_database,
            "SELECT response_ciphertext FROM public.slice4_idempotency WHERE receipt_id=$1",
            later_receipt_id,
        )
        for missing_expression in (
            "convert_from($2::bytea,'UTF8')::jsonb-'_policy_postimage'",
            "jsonb_set(convert_from($2::bytea,'UTF8')::jsonb,"
            "'{_policy_postimage}','null'::jsonb)",
        ):
            _run_execute(
                pg_database,
                "UPDATE public.slice4_idempotency SET response_ciphertext=convert_to(("
                + missing_expression
                + ")::text,'UTF8') WHERE receipt_id=$1",
                later_receipt_id,
                original_response,
            )
            assert _confirm(slice5_rule_governance_writer_database, historical_confirmation) == {
                "outcome": "UNKNOWN"
            }
        _run_execute(
            pg_database,
            "UPDATE public.slice4_idempotency SET response_ciphertext=$2 WHERE receipt_id=$1",
            later_receipt_id,
            original_response,
        )
        assert _confirm(slice5_rule_governance_writer_database, historical_confirmation) == {
            "outcome": "COMMITTED"
        }

        audit_id = last_result["_audit_id"]
        pg_database.execute(
            "UPDATE public.slice4_audit SET digest_key_id='tampered-k1' "
            f"WHERE audit_id='{audit_id}'"
        )
        audit_drift = _run_value(
            slice5_rule_governance_writer_database,
            "SELECT public.readiness_policy_governance_confirm_v1($1::jsonb)",
            json.dumps(confirmation, separators=(",", ":"), sort_keys=True),
        )
        audit_drift = json.loads(audit_drift) if isinstance(audit_drift, str) else audit_drift
        assert audit_drift == {"outcome": "UNKNOWN"}
        pg_database.execute(
            "UPDATE public.slice4_audit SET digest_key_id='synthetic-k1' "
            f"WHERE audit_id='{audit_id}'"
        )

        original_rule = pg_database.fetch_value(
            "SELECT rule_version FROM public.assessment_readiness_policy_version "
            f"WHERE policy_version_id='{policy_id}'"
        )
        pg_database.execute(
            "UPDATE public.assessment_readiness_policy_version SET rule_version='tampered-v1' "
            f"WHERE policy_version_id='{policy_id}'"
        )
        drifted = _run_value(
            slice5_rule_governance_writer_database,
            "SELECT public.readiness_policy_governance_confirm_v1($1::jsonb)",
            json.dumps(confirmation, separators=(",", ":"), sort_keys=True),
        )
        drifted = json.loads(drifted) if isinstance(drifted, str) else drifted
        assert drifted == {"outcome": "UNKNOWN"}
        pg_database.execute(
            "UPDATE public.assessment_readiness_policy_version "
            f"SET rule_version='{original_rule}' WHERE policy_version_id='{policy_id}'"
        )

        pg_database.execute(
            "UPDATE public.slice4_idempotency SET response_ciphertext=convert_to("
            "jsonb_set(convert_from(response_ciphertext,'UTF8')::jsonb,'{target_count}',"
            "to_jsonb(999999))::text,'UTF8') "
            f"WHERE receipt_id='{receipt}'"
        )
        receipt_drift = _run_value(
            slice5_rule_governance_writer_database,
            "SELECT public.readiness_policy_governance_confirm_v1($1::jsonb)",
            json.dumps(confirmation, separators=(",", ":"), sort_keys=True),
        )
        receipt_drift = json.loads(receipt_drift) if isinstance(receipt_drift, str) else receipt_drift
        assert receipt_drift == {"outcome": "UNKNOWN"}
        pg_database.execute(
            "UPDATE public.slice4_idempotency SET response_ciphertext=convert_to("
            f"'{json.dumps(last_result, separators=(',', ':'), sort_keys=True)}'::jsonb::text,'UTF8') "
            f"WHERE receipt_id='{receipt}'"
        )

        target_event = pg_database.fetch_value(
            "SELECT event_id FROM public.slice4_outbox "
            f"WHERE payload_json->>'operation_receipt_id'='{receipt}' ORDER BY event_id LIMIT 1"
        )
        pg_database.execute(f"DELETE FROM public.slice4_outbox WHERE event_id='{target_event}'")
        missing_target = _run_value(
            slice5_rule_governance_writer_database,
            "SELECT public.readiness_policy_governance_confirm_v1($1::jsonb)",
            json.dumps(confirmation, separators=(",", ":"), sort_keys=True),
        )
        missing_target = json.loads(missing_target) if isinstance(missing_target, str) else missing_target
        assert missing_target == {"outcome": "UNKNOWN"}

        pg_database.execute(
            "UPDATE public.slice4_outbox SET status='FAILED',attempts=3 "
            f"WHERE event_id=(SELECT event_id FROM public.slice4_outbox WHERE aggregate_ref='{policy_id}' "
            "AND status='PENDING' ORDER BY event_id LIMIT 1)"
        )
        claimed = asyncio.run(slice4_health_data_tasks._claim_one())
        assert claimed is not None
        assert pg_database.fetch_value(
            f"SELECT count(*) FROM public.slice4_outbox WHERE aggregate_ref='{policy_id}' AND status='FAILED'"
        ) == 1
    finally:
        pg_database.execute(
            f"DELETE FROM public.slice4_delivery WHERE event_id IN "
            f"(SELECT event_id FROM public.slice4_outbox WHERE aggregate_ref='{policy_id}');"
            f"DELETE FROM public.slice4_outbox WHERE aggregate_ref='{policy_id}';"
            f"DELETE FROM public.slice4_audit WHERE aggregate_ref='{policy_id}';"
            f"DELETE FROM public.slice4_idempotency WHERE scope_ref='{policy_id}';"
            f"DELETE FROM public.assessment_readiness_policy_version WHERE policy_version_id='{policy_id}';"
            f'DELETE FROM public."user" WHERE id IN ({author},{reviewer},{admin})'
        )


def test_真实consumer接受READINESS_POLICY_CHANGED且不需新增分支(pg_database, monkeypatch) -> None:
    event_id, policy_id, case_id = uuid4(), uuid4(), uuid4()
    observed: list[UUID] = []

    async def recompute(value: UUID) -> dict:
        observed.append(value)
        return {"status": "DATA_SYNC_PENDING"}

    monkeypatch.setattr(slice4_health_data_tasks, "_recompute", recompute)
    pg_database.execute(
        "INSERT INTO public.slice4_outbox(event_id,aggregate_type,aggregate_ref,event_type,"
        "payload_digest,payload_json,status,attempts,lease_owner,lease_until,created_at,delivered_at) VALUES ("
        f"'{event_id}','READINESS_POLICY','{policy_id}','READINESS_POLICY_CHANGED',"
        "decode(repeat('a',64),'hex'),"
        f"'{{\"operation_receipt_id\":\"{uuid4()}\",\"service_case_id\":\"{case_id}\","
        f"\"policy_version_id\":\"{policy_id}\",\"operation\":\"PUBLISH\"}}'::jsonb,"
        f"'PROCESSING',1,'synthetic-worker',now()+interval '1 minute',now(),NULL)"
    )
    try:
        assert asyncio.run(slice4_health_data_tasks._consume(event_id)) == "DELIVERED"
        assert observed == [case_id]
        assert pg_database.fetch_value(
            f"SELECT status='DELIVERED' FROM public.slice4_outbox WHERE event_id='{event_id}'"
        )
    finally:
        pg_database.execute(
            f"DELETE FROM public.slice4_delivery WHERE event_id='{event_id}';"
            f"DELETE FROM public.slice4_outbox WHERE event_id='{event_id}'"
        )


def test_downgrade有治理历史时在任何DDL前fail_closed(
    pg_database, slice5_rule_governance_writer_database
) -> None:
    author, policy_id = 9964301, uuid4()
    pg_database.execute(
        'INSERT INTO public."user"(id,phone,password_hash,role,status,created_at,updated_at) VALUES '
        f"({author},'10009964301','synthetic','expert','active',now(),now())"
    )
    payload = _payload(operation="CREATE", actor=author, actor_role="expert", policy_id=policy_id, ordinal=21, version_no=99643)
    _govern(slice5_rule_governance_writer_database, "CREATE", payload)
    before = pg_database.fetch_value(
        "SELECT pg_get_functiondef('public.readiness_policy_governance_v1(character varying,jsonb)'::regprocedure)"
    )
    try:
        with pytest.raises(RuntimeError, match="ASSESSMENT_READINESS_GOVERNANCE_DOWNGRADE_BLOCKED"):
            command.downgrade(_build_alembic_config(_get_test_database_url()), "20260915_0048")
        assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260916_0049"
        assert pg_database.fetch_value(
            "SELECT pg_get_functiondef('public.readiness_policy_governance_v1(character varying,jsonb)'::regprocedure)"
        ) == before
    finally:
        pg_database.execute(
            f"DELETE FROM public.slice4_audit WHERE aggregate_ref='{policy_id}';"
            f"DELETE FROM public.slice4_idempotency WHERE scope_ref='{policy_id}';"
            f"DELETE FROM public.assessment_readiness_policy_version WHERE policy_version_id='{policy_id}';"
            f'DELETE FROM public."user" WHERE id={author}'
        )


def test_Z_空静态治理数据允许0049降级重升级(pg_database) -> None:
    config = _build_alembic_config(_get_test_database_url())
    command.downgrade(config, "20260915_0048")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260915_0048"
    assert pg_database.fetch_value(
        "SELECT to_regprocedure('public.readiness_policy_governance_v1(character varying,jsonb)') IS NULL"
    )
    command.upgrade(config, "head")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260916_0049"
