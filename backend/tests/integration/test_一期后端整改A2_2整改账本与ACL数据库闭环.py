from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

from tests.integration.conftest import _build_alembic_config, _get_test_database_url

pytestmark = pytest.mark.integration

WRITER_REGPROCEDURE = (
    "identity.a2_identity_remediation_ledger_v1("
    "varchar,uuid,uuid,bigint,varchar,smallint,bigint,varchar,varchar,varchar,"
    "bigint,bigint,bigint,bigint,bigint,bigint,bigint,bigint,bigint,"
    "varchar,varchar,varchar,varchar,varchar,varchar,varchar,uuid,uuid,"
    "timestamp with time zone)"
)
CONFIRM_REGPROCEDURE = (
    "identity.a2_identity_remediation_confirm_v1("
    "varchar,uuid,uuid,bigint,varchar,smallint,bigint,varchar,varchar,varchar,"
    "bigint,bigint,bigint,bigint,bigint,bigint,bigint,bigint,bigint,"
    "varchar,varchar,varchar,varchar,varchar,varchar,varchar,uuid,uuid,"
    "timestamp with time zone,varchar,bigint,varchar)"
)
WRITER_SQL = (
    "SELECT * FROM identity.a2_identity_remediation_ledger_v1("
    + ",".join(f"${index}" for index in range(1, 30))
    + ")"
)
CONFIRM_SQL = (
    "SELECT identity.a2_identity_remediation_confirm_v1("
    + ",".join(f"${index}" for index in range(1, 33))
    + ") AS outcome"
)
TABLES = (
    "identity.identity_remediation_batch",
    "identity.identity_remediation_item",
    "identity.identity_remediation_receipt",
    "identity.identity_remediation_audit",
)


def _writer_args(
    operation: str,
    batch_ref: UUID,
    *,
    item_ref: UUID | None = None,
    subject_user_ref: int | None = None,
    primary_class: str | None = None,
    secondary_flags: int | None = None,
    expected_version: int | None = None,
    expected_target_state_digest: str | None = None,
    classification_rule_hash: str | None = None,
    snapshot_ref_hash: str | None = None,
    counts: tuple[int, ...] | None = None,
    preimage_digest: str | None = None,
    eligibility_action_gate_digest: str | None = None,
    mutation_digest: str | None = None,
    business_postimage_digest: str | None = None,
    actor_scope: str = "platform-remediation-operator",
    reason_code: str = "A2_APPROVED_REMEDIATION",
    idempotency_key_digest: str | None = None,
    receipt_id: UUID | None = None,
    audit_id: UUID | None = None,
    occurred_at: datetime | None = None,
) -> tuple[object, ...]:
    count_values = counts or (None,) * 9
    assert len(count_values) == 9
    return (
        operation,
        batch_ref,
        item_ref,
        subject_user_ref,
        primary_class,
        secondary_flags,
        expected_version,
        expected_target_state_digest,
        classification_rule_hash,
        snapshot_ref_hash,
        *count_values,
        preimage_digest,
        eligibility_action_gate_digest,
        mutation_digest,
        business_postimage_digest,
        actor_scope,
        reason_code,
        idempotency_key_digest or uuid4().hex * 2,
        receipt_id or uuid4(),
        audit_id or uuid4(),
        occurred_at or datetime(2026, 9, 2, tzinfo=UTC),
    )


def _write(database, args: tuple[object, ...]) -> dict[str, object]:
    rows = database.fetch_rows(WRITER_SQL, *args)
    assert len(rows) == 1
    return rows[0]


def _confirm_args(
    write_args: tuple[object, ...], result: dict[str, object]
) -> tuple[object, ...]:
    return (*write_args, result["state"], result["version"], result["result_code"])


def _confirm(database, args: tuple[object, ...]) -> str:
    rows = database.fetch_rows(CONFIRM_SQL, *args)
    assert len(rows) == 1
    return str(rows[0]["outcome"])


def _plan(
    database,
    *,
    batch_ref: UUID,
    snapshot: str,
    counts: tuple[int, ...],
    idem: str | None = None,
) -> tuple[tuple[object, ...], dict[str, object]]:
    args = _writer_args(
        "PLAN_BATCH",
        batch_ref,
        classification_rule_hash="4" * 64,
        snapshot_ref_hash=snapshot,
        counts=counts,
        reason_code="A2_BATCH_PLANNED",
        idempotency_key_digest=idem,
    )
    return args, _write(database, args)


def _register(
    database,
    *,
    batch_ref: UUID,
    item_ref: UUID,
    subject_ref: int,
    primary_class: str,
    idem: str | None = None,
) -> tuple[tuple[object, ...], dict[str, object]]:
    args = _writer_args(
        "REGISTER_ITEM",
        batch_ref,
        item_ref=item_ref,
        subject_user_ref=subject_ref,
        primary_class=primary_class,
        secondary_flags=0,
        preimage_digest=uuid4().hex * 2,
        reason_code="A2_ITEM_DISCOVERED",
        idempotency_key_digest=idem,
    )
    return args, _write(database, args)


def _transition(
    database,
    operation: str,
    batch_ref: UUID,
    previous: dict[str, object],
    *,
    item_ref: UUID | None = None,
    reason_code: str = "A2_BATCH_CONTROLLED",
    eligibility_digest: str | None = None,
    mutation_digest: str | None = None,
    business_postimage_digest: str | None = None,
    idem: str | None = None,
) -> tuple[tuple[object, ...], dict[str, object]]:
    args = _writer_args(
        operation,
        batch_ref,
        item_ref=item_ref,
        expected_version=int(previous["version"]),
        expected_target_state_digest=str(previous["state_digest"]),
        eligibility_action_gate_digest=eligibility_digest,
        mutation_digest=mutation_digest,
        business_postimage_digest=business_postimage_digest,
        reason_code=reason_code,
        idempotency_key_digest=idem,
    )
    return args, _write(database, args)


def _assert_error(database, args: tuple[object, ...], code: str) -> None:
    with pytest.raises(asyncpg.RaiseError, match=f"^{code}$"):
        _write(database, args)


def test_A2_2_L账本幂等与完整前后像确认(
    pg_database,
    a2_identity_remediation_writer_database,
    a2_identity_remediation_confirmation_database,
) -> None:
    writer = a2_identity_remediation_writer_database
    confirmation_db = a2_identity_remediation_confirmation_database
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260915_0048"
    batch_ref = uuid4()
    plan_args, planned = _plan(
        writer,
        batch_ref=batch_ref,
        snapshot="5" * 64,
        counts=(0, 0, 0, 0, 0, 0, 0, 0, 0),
    )
    assert _write(writer, plan_args) == planned
    assert planned["state"] == "PLANNED"
    assert len(str(planned["state_digest"])) == 64

    conflict = list(plan_args)
    conflict[1] = uuid4()
    _assert_error(writer, tuple(conflict), "A2_REMEDIATION_IDEMPOTENCY_CONFLICT")

    confirmation = _confirm_args(plan_args, planned)
    assert _confirm(confirmation_db, confirmation) == "COMMITTED"
    receipt_id = plan_args[26]
    audit_id = plan_args[27]
    pg_database.execute(
        "UPDATE identity.identity_remediation_receipt "
        f"SET result_code='FAILED' WHERE receipt_id='{receipt_id}'::uuid"
    )
    assert _confirm(confirmation_db, confirmation) == "UNKNOWN"
    pg_database.execute(
        "UPDATE identity.identity_remediation_receipt "
        f"SET result_code='PLANNED' WHERE receipt_id='{receipt_id}'::uuid"
    )
    pg_database.execute(
        "UPDATE identity.identity_remediation_audit "
        f"SET reason_code='A2_EXCLUDED' WHERE audit_id='{audit_id}'::uuid"
    )
    assert _confirm(confirmation_db, confirmation) == "UNKNOWN"
    pg_database.execute(
        "UPDATE identity.identity_remediation_audit "
        f"SET reason_code='A2_BATCH_PLANNED' WHERE audit_id='{audit_id}'::uuid"
    )

    batch_mutations = (
        ("classification_rule_hash=repeat('6',64)", "classification_rule_hash=repeat('4',64)"),
        ("snapshot_ref_hash=repeat('7',64)", "snapshot_ref_hash=repeat('5',64)"),
        ("input_count=1,h0_count=1", "input_count=0,h0_count=0"),
        ("input_count=1,h1_count=1", "input_count=0,h1_count=0"),
        ("input_count=1,h2_count=1", "input_count=0,h2_count=0"),
        ("input_count=1,h3_count=1", "input_count=0,h3_count=0"),
        ("input_count=1,h4_count=1", "input_count=0,h4_count=0"),
        ("input_count=1,h5_count=1", "input_count=0,h5_count=0"),
        ("input_count=1,h6_count=1", "input_count=0,h6_count=0"),
        ("input_count=1,h7_count=1", "input_count=0,h7_count=0"),
        ("status='FAILED'", "status='PLANNED'"),
        ("version=2", "version=1"),
        ("initiated_by_public_ref='alternate-operator'", "initiated_by_public_ref='platform-remediation-operator'"),
        ("started_at='2026-09-02T01:00:00Z'", "started_at=NULL"),
        ("completed_at='2026-09-02T02:00:00Z'", "completed_at=NULL"),
        ("created_at='2026-09-02T04:00:00Z'", "created_at='2026-09-02T00:00:00Z'"),
        ("updated_at='2026-09-02T03:00:00Z'", "updated_at='2026-09-02T00:00:00Z'"),
    )
    for mutation, restore in batch_mutations:
        pg_database.execute(
            f"UPDATE identity.identity_remediation_batch SET {mutation} "
            f"WHERE batch_ref='{batch_ref}'::uuid"
        )
        assert _confirm(confirmation_db, confirmation) == "UNKNOWN"
        pg_database.execute(
            f"UPDATE identity.identity_remediation_batch SET {restore} "
            f"WHERE batch_ref='{batch_ref}'::uuid"
        )

    unexecuted = _writer_args(
        "START_BATCH",
        batch_ref,
        expected_version=1,
        expected_target_state_digest=str(planned["state_digest"]),
        reason_code="A2_BATCH_CONTROLLED",
    )
    assert _confirm(
        confirmation_db, (*unexecuted, "RUNNING", 2, "RUNNING")
    ) == "NOT_COMMITTED"
    wrong_preimage = list((*unexecuted, "RUNNING", 2, "RUNNING"))
    wrong_preimage[7] = "f" * 64
    assert _confirm(confirmation_db, tuple(wrong_preimage)) == "UNKNOWN"

    absent_plan = _writer_args(
        "PLAN_BATCH",
        uuid4(),
        classification_rule_hash="1" * 64,
        snapshot_ref_hash="2" * 64,
        counts=(0, 0, 0, 0, 0, 0, 0, 0, 0),
        reason_code="A2_BATCH_PLANNED",
    )
    assert _confirm(
        confirmation_db, (*absent_plan, "PLANNED", 1, "PLANNED")
    ) == "NOT_COMMITTED"


def test_A2_2_L数据库强制H0到H7动作父状态及manifest(
    a2_identity_remediation_writer_database,
) -> None:
    database = a2_identity_remediation_writer_database
    batch_ref = uuid4()
    _, planned = _plan(
        database,
        batch_ref=batch_ref,
        snapshot="1" * 64,
        counts=(8, 1, 1, 1, 1, 1, 1, 1, 1),
    )
    items: dict[str, tuple[UUID, dict[str, object]]] = {}
    for offset, primary_class in enumerate(("H0", "H1", "H2", "H3", "H4", "H5", "H6", "H7")):
        item_ref = uuid4()
        _, registered = _register(
            database,
            batch_ref=batch_ref,
            item_ref=item_ref,
            subject_ref=8810000 + offset,
            primary_class=primary_class,
        )
        items[primary_class] = (item_ref, registered)

    premature = _writer_args(
        "MARK_ITEM_READY",
        batch_ref,
        item_ref=items["H3"][0],
        expected_version=1,
        expected_target_state_digest=str(items["H3"][1]["state_digest"]),
        eligibility_action_gate_digest="3" * 64,
        reason_code="A2_CANONICAL_MATCH_APPROVED",
    )
    _assert_error(database, premature, "A2_REMEDIATION_ILLEGAL_TRANSITION")
    _, running = _transition(database, "START_BATCH", batch_ref, planned)

    invalid_h2 = _writer_args(
        "MARK_ITEM_READY",
        batch_ref,
        item_ref=items["H2"][0],
        expected_version=1,
        expected_target_state_digest=str(items["H2"][1]["state_digest"]),
        eligibility_action_gate_digest="5" * 64,
        reason_code="A2_CANONICAL_MATCH_APPROVED",
    )
    _assert_error(database, invalid_h2, "A2_REMEDIATION_ILLEGAL_TRANSITION")

    _, paused = _transition(database, "PAUSE_BATCH", batch_ref, running)
    paused_item = _writer_args(
        "MARK_ITEM_READY",
        batch_ref,
        item_ref=items["H3"][0],
        expected_version=1,
        expected_target_state_digest=str(items["H3"][1]["state_digest"]),
        eligibility_action_gate_digest="6" * 64,
        reason_code="A2_CANONICAL_MATCH_APPROVED",
    )
    _assert_error(database, paused_item, "A2_REMEDIATION_ILLEGAL_TRANSITION")
    _, running = _transition(database, "RESUME_BATCH", batch_ref, paused)

    terminal: dict[str, dict[str, object]] = {}
    for primary_class in ("H0", "H7"):
        item_ref, previous = items[primary_class]
        _, terminal[primary_class] = _transition(
            database,
            "EXCLUDE_ITEM",
            batch_ref,
            previous,
            item_ref=item_ref,
            reason_code="A2_EXCLUDED",
        )
    for primary_class in ("H1", "H2", "H6"):
        item_ref, previous = items[primary_class]
        _, terminal[primary_class] = _transition(
            database,
            "REQUIRE_ITEM",
            batch_ref,
            previous,
            item_ref=item_ref,
            reason_code="A2_HUMAN_REVIEW_REQUIRED",
        )
    for primary_class, reason in (
        ("H3", "A2_CANONICAL_MATCH_APPROVED"),
        ("H4", "A2_TOKEN_WINDOW_PROVED"),
    ):
        item_ref, previous = items[primary_class]
        _, ready = _transition(
            database,
            "MARK_ITEM_READY",
            batch_ref,
            previous,
            item_ref=item_ref,
            reason_code=reason,
            eligibility_digest=uuid4().hex * 2,
        )
        _, processing = _transition(
            database,
            "START_ITEM",
            batch_ref,
            ready,
            item_ref=item_ref,
            reason_code="A2_APPROVED_REMEDIATION",
        )
        _, terminal[primary_class] = _transition(
            database,
            "COMPLETE_ITEM",
            batch_ref,
            processing,
            item_ref=item_ref,
            reason_code="A2_APPROVED_REMEDIATION",
            mutation_digest=uuid4().hex * 2,
            business_postimage_digest=uuid4().hex * 2,
        )
    h5_ref, h5_previous = items["H5"]
    _, terminal["H5"] = _transition(
        database,
        "REQUIRE_ITEM",
        batch_ref,
        h5_previous,
        item_ref=h5_ref,
        reason_code="A2_HUMAN_REVIEW_REQUIRED",
    )
    _, completed = _transition(database, "COMPLETE_BATCH", batch_ref, running)
    assert completed["state"] == "COMPLETED"

    blocked = _writer_args(
        "START_ITEM",
        batch_ref,
        item_ref=items["H3"][0],
        expected_version=int(terminal["H3"]["version"]),
        expected_target_state_digest=str(terminal["H3"]["state_digest"]),
        reason_code="A2_APPROVED_REMEDIATION",
    )
    _assert_error(database, blocked, "A2_REMEDIATION_ILLEGAL_TRANSITION")


def test_A2_2_L_Item任一非键持久字段漂移均确认UNKNOWN(
    pg_database,
    a2_identity_remediation_writer_database,
    a2_identity_remediation_confirmation_database,
) -> None:
    writer = a2_identity_remediation_writer_database
    confirmation_db = a2_identity_remediation_confirmation_database
    batch_ref = uuid4()
    _, _ = _plan(
        writer,
        batch_ref=batch_ref,
        snapshot="c" * 64,
        counts=(1, 0, 0, 0, 1, 0, 0, 0, 0),
    )
    item_ref = uuid4()
    register_args, registered = _register(
        writer,
        batch_ref=batch_ref,
        item_ref=item_ref,
        subject_ref=8830001,
        primary_class="H3",
    )
    confirmation = _confirm_args(register_args, registered)
    assert _confirm(confirmation_db, confirmation) == "COMMITTED"
    original_preimage = str(register_args[19])
    mutations = (
        ("subject_user_ref=8830002", "subject_user_ref=8830001"),
        ("primary_class='H4'", "primary_class='H3'"),
        ("secondary_flags=1", "secondary_flags=0"),
        ("preimage_digest=repeat('d',64)", f"preimage_digest='{original_preimage}'"),
        ("eligibility_action_gate_digest=repeat('e',64)", "eligibility_action_gate_digest=NULL"),
        ("mutation_digest=repeat('e',64)", "mutation_digest=NULL"),
        ("postimage_digest=repeat('e',64)", "postimage_digest=NULL"),
        ("status='EXCLUDED'", "status='DISCOVERED'"),
        ("attempt_count=1", "attempt_count=0"),
        ("version=2", "version=1"),
        ("last_reason_code='A2_EXCLUDED'", "last_reason_code='A2_ITEM_DISCOVERED'"),
        ("created_at='2026-09-02T04:00:00Z'", "created_at='2026-09-02T00:00:00Z'"),
        ("updated_at='2026-09-02T05:00:00Z'", "updated_at='2026-09-02T00:00:00Z'"),
    )
    for mutation, restore in mutations:
        pg_database.execute(
            f"UPDATE identity.identity_remediation_item SET {mutation} "
            f"WHERE item_ref='{item_ref}'::uuid"
        )
        assert _confirm(confirmation_db, confirmation) == "UNKNOWN"
        pg_database.execute(
            f"UPDATE identity.identity_remediation_item SET {restore} "
            f"WHERE item_ref='{item_ref}'::uuid"
        )


def test_A2_2_L_manifest失败终态snapshot与跨Batch幂等并发(
    a2_identity_remediation_writer_database,
) -> None:
    database = a2_identity_remediation_writer_database
    batch_ref = uuid4()
    _, planned = _plan(
        database,
        batch_ref=batch_ref,
        snapshot="6" * 64,
        counts=(1, 0, 0, 0, 1, 0, 0, 0, 0),
    )
    incomplete_start = _writer_args(
        "START_BATCH",
        batch_ref,
        expected_version=1,
        expected_target_state_digest=str(planned["state_digest"]),
        reason_code="A2_BATCH_CONTROLLED",
    )
    _assert_error(database, incomplete_start, "A2_REMEDIATION_MANIFEST_MISMATCH")
    item_ref = uuid4()
    _, item = _register(
        database,
        batch_ref=batch_ref,
        item_ref=item_ref,
        subject_ref=8820001,
        primary_class="H3",
    )
    _, running = _transition(database, "START_BATCH", batch_ref, planned)
    incomplete_complete = _writer_args(
        "COMPLETE_BATCH",
        batch_ref,
        expected_version=2,
        expected_target_state_digest=str(running["state_digest"]),
        reason_code="A2_BATCH_CONTROLLED",
    )
    _assert_error(database, incomplete_complete, "A2_REMEDIATION_MANIFEST_MISMATCH")
    _, ready = _transition(
        database,
        "MARK_ITEM_READY",
        batch_ref,
        item,
        item_ref=item_ref,
        reason_code="A2_CANONICAL_MATCH_APPROVED",
        eligibility_digest="3" * 64,
    )
    _, processing = _transition(
        database,
        "START_ITEM",
        batch_ref,
        ready,
        item_ref=item_ref,
        reason_code="A2_APPROVED_REMEDIATION",
    )
    _, failed_item = _transition(
        database,
        "FAIL_ITEM",
        batch_ref,
        processing,
        item_ref=item_ref,
        reason_code="A2_TERMINAL_FAILURE",
    )
    assert failed_item["state"] == "FAILED_TERMINAL"
    _assert_error(database, incomplete_complete, "A2_REMEDIATION_MANIFEST_MISMATCH")
    _, failed_batch = _transition(
        database,
        "FAIL_BATCH",
        batch_ref,
        running,
        reason_code="A2_TERMINAL_FAILURE",
    )
    assert failed_batch["state"] == "FAILED"

    duplicate_snapshot = _writer_args(
        "PLAN_BATCH",
        uuid4(),
        classification_rule_hash="7" * 64,
        snapshot_ref_hash="6" * 64,
        counts=(0, 0, 0, 0, 0, 0, 0, 0, 0),
        reason_code="A2_BATCH_PLANNED",
    )
    _assert_error(database, duplicate_snapshot, "A2_REMEDIATION_SNAPSHOT_CONFLICT")

    shared_idem = "8" * 64
    left = _writer_args(
        "PLAN_BATCH",
        uuid4(),
        classification_rule_hash="9" * 64,
        snapshot_ref_hash="a" * 64,
        counts=(0, 0, 0, 0, 0, 0, 0, 0, 0),
        reason_code="A2_BATCH_PLANNED",
        idempotency_key_digest=shared_idem,
    )
    right = _writer_args(
        "PLAN_BATCH",
        uuid4(),
        classification_rule_hash="9" * 64,
        snapshot_ref_hash="b" * 64,
        counts=(0, 0, 0, 0, 0, 0, 0, 0, 0),
        reason_code="A2_BATCH_PLANNED",
        idempotency_key_digest=shared_idem,
    )

    async def race() -> list[object]:
        async def invoke(args: tuple[object, ...]) -> object:
            connection = await asyncpg.connect(database.database_url)
            try:
                rows = await connection.fetch(WRITER_SQL, *args)
                return dict(rows[0])
            except Exception as error:  # exact stable database result asserted below
                return error
            finally:
                await connection.close()

        return await asyncio.gather(invoke(left), invoke(right))

    outcomes = asyncio.run(race())
    assert sum(isinstance(value, dict) for value in outcomes) == 1
    failures = [value for value in outcomes if isinstance(value, Exception)]
    assert len(failures) == 1
    assert str(failures[0]) == "A2_REMEDIATION_IDEMPOTENCY_CONFLICT"


def test_A2_2_L强类型入口拒绝敏感形态且不回显原值(
    a2_identity_remediation_writer_database,
) -> None:
    valid_plan = _writer_args(
        "PLAN_BATCH",
        uuid4(),
        classification_rule_hash="1" * 64,
        snapshot_ref_hash="2" * 64,
        counts=(0, 0, 0, 0, 0, 0, 0, 0, 0),
        reason_code="A2_BATCH_PLANNED",
    )
    unsafe_cases = (
        (0, "UNKNOWN_OPERATION"),
        (23, "13800000000"),
        (23, "person@example.invalid"),
        (23, "x" * 65),
        (24, "UNBOUNDED_REASON"),
        (25, "not-a-digest"),
    )
    for index, unsafe_value in unsafe_cases:
        invalid = list(valid_plan)
        invalid[index] = unsafe_value
        invalid[26] = uuid4()
        invalid[27] = uuid4()
        with pytest.raises(asyncpg.RaiseError, match="^A2_REMEDIATION_INPUT_INVALID$") as error:
            _write(a2_identity_remediation_writer_database, tuple(invalid))
        assert unsafe_value not in str(error.value)


def test_A2_2_L专用角色与六身份ACL闭合(
    pg_database,
    application_database,
    readonly_database,
    a2_identity_inventory_database,
    a2_identity_remediation_writer_database,
    a2_identity_remediation_confirmation_database,
) -> None:
    role_databases = (
        application_database,
        readonly_database,
        a2_identity_inventory_database,
        a2_identity_remediation_writer_database,
        a2_identity_remediation_confirmation_database,
    )
    assert len({db.fetch_value("SELECT current_user") for db in role_databases}) == 5
    for role_database in role_databases:
        for table in TABLES:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                role_database.fetch_value(f"SELECT count(*) FROM {table}")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                role_database.execute(f"DELETE FROM {table} WHERE false")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            role_database.execute("CREATE TABLE identity.a2_forbidden(value integer)")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        a2_identity_remediation_writer_database.fetch_rows(CONFIRM_SQL, *(None,) * 32)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        a2_identity_remediation_confirmation_database.fetch_rows(WRITER_SQL, *(None,) * 29)
    for function in (WRITER_REGPROCEDURE, CONFIRM_REGPROCEDURE):
        assert pg_database.fetch_value(
            f"SELECT has_function_privilege('public', '{function}', 'EXECUTE')"
        ) is False
    assert pg_database.fetch_value(
        "SELECT count(*) FROM pg_proc procedure "
        "JOIN pg_namespace namespace ON namespace.oid=procedure.pronamespace "
        "WHERE namespace.nspname='identity' "
        "AND procedure.proname='a2_identity_remediation_ledger_v1'"
    ) == 1
    assert pg_database.fetch_value(
        "SELECT count(*) FROM pg_proc procedure "
        "JOIN pg_namespace namespace ON namespace.oid=procedure.pronamespace "
        "WHERE namespace.nspname='identity' "
        "AND procedure.proname='a2_identity_remediation_confirm_v1'"
    ) == 1
    assert pg_database.fetch_value(
        "SELECT to_regprocedure('identity.a2_identity_remediation_ledger_v1("
        "varchar,uuid,uuid,bigint,varchar,smallint,bigint,varchar,varchar,"
        "bigint,bigint,bigint,bigint,bigint,bigint,bigint,bigint,bigint,"
        "varchar,varchar,varchar,varchar,varchar,varchar,varchar,uuid,uuid,"
        "timestamp with time zone)')"
    ) is None
    assert pg_database.fetch_value(
        "SELECT to_regprocedure('identity.a2_identity_remediation_confirm_v1("
        "varchar,varchar,varchar,varchar,uuid,uuid,varchar,uuid,uuid,varchar,"
        "bigint,varchar,timestamp with time zone)')"
    ) is None


def test_A2_2_L_upgrade_downgrade_reupgrade只影响0035对象(
    pg_database,
    a2_identity_remediation_writer_database,
) -> None:
    config = _build_alembic_config(_get_test_database_url())
    before = {
        "users": pg_database.fetch_value('SELECT count(*) FROM public."user"'),
        "enrollments": pg_database.fetch_value(
            "SELECT count(*) FROM public.service_enrollment"
        ),
    }
    command.downgrade(config, "20260901_0034")
    try:
        assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260901_0034"
        for table in TABLES:
            assert pg_database.fetch_value(f"SELECT to_regclass('{table}')") is None
        with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.UndefinedFunctionError)):
            a2_identity_remediation_writer_database.fetch_rows(WRITER_SQL, *(None,) * 29)
    finally:
        command.upgrade(config, "head")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260915_0048"
    for table in TABLES:
        assert pg_database.fetch_value(f"SELECT to_regclass('{table}')") is not None
    assert {
        "users": pg_database.fetch_value('SELECT count(*) FROM public."user"'),
        "enrollments": pg_database.fetch_value(
            "SELECT count(*) FROM public.service_enrollment"
        ),
    } == before
