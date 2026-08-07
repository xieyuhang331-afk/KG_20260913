import asyncio
import os
from datetime import datetime, timezone
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.modules.auth.registration_outbox import (
    P1VerificationTransitionCommand,
    P1VerificationTransitionWriter,
)
from app.modules.auth.registration_outbox_repository import (
    SqlAlchemyP1VerificationTransitionUnitOfWork,
)
from app.modules.auth.eligibility_evidence import build_p1_projection_digest
from app.modules.auth.registration_outbox_worker import (
    DeliveryStatus,
    RegistrationOutboxDeliveryResult,
    RegistrationOutboxDispatcher,
    RegistrationOutboxReconciler,
)
from app.modules.auth.registration_outbox_worker_repository import (
    SqlAlchemyRegistrationOutboxWorkerUnitOfWork,
)


pytestmark = pytest.mark.integration

USER_REF = 82301
CLASSIFICATION_REF = UUID("01890f3e-7b7d-7cc3-88c8-2f5a12d28230")
VERIFICATION_REF = UUID("01890f3e-7b7d-7cc3-88c8-2f5a12d28231")
EVENT_ID = UUID("01890f3e-7b7d-7cc3-98c8-2f5a12d28232")
OUTBOX_REF = UUID("01890f3e-7b7d-7cc3-a8c8-2f5a12d28233")
NOW = datetime(2026, 8, 8, tzinfo=timezone.utc)
RECONCILE_USER_REF = 82302
RECONCILE_CLASSIFICATION_REF = UUID(
    "01890f3e-7b7d-7cc3-88c8-2f5a12d29230"
)
RECONCILE_VERIFICATION_REF = UUID(
    "01890f3e-7b7d-7cc3-88c8-2f5a12d29231"
)
RECONCILE_EVENT_ID = UUID(
    "01890f3e-7b7d-7cc3-98c8-2f5a12d29232"
)
RECONCILE_ELIGIBILITY_REF = UUID(
    "01890f3e-7b7d-7cc3-a8c8-2f5a12d29233"
)
OTHER_POLICY_USER_REF = 82303
OTHER_POLICY_CLASSIFICATION_REF = UUID(
    "01890f3e-7b7d-7cc3-88c8-2f5a12d29330"
)
OTHER_POLICY_VERIFICATION_REF = UUID(
    "01890f3e-7b7d-7cc3-88c8-2f5a12d29331"
)
OTHER_POLICY_EVENT_ID = UUID(
    "01890f3e-7b7d-7cc3-98c8-2f5a12d29332"
)
OTHER_POLICY_ELIGIBILITY_REF = UUID(
    "01890f3e-7b7d-7cc3-a8c8-2f5a12d29333"
)


class _UuidGenerator:
    def __init__(self):
        self._values = iter(
            (
                VERIFICATION_REF,
                EVENT_ID,
                UUID("01890f3e-7b7d-7cc3-b8c8-2f5a12d28234"),
                OUTBOX_REF,
            )
        )

    def generate(self):
        return next(self._values)


async def _with_connection(database_url, operation):
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            return await operation(connection)
    finally:
        await engine.dispose()


async def _permission_and_round_trip_contract():
    writer_url = os.environ["KG_TEST_VERIFICATION_WRITER_DATABASE_URL"]
    worker_url = os.environ["KG_TEST_DELIVERY_WORKER_DATABASE_URL"]
    audit_url = os.environ["KG_TEST_OUTBOX_AUDIT_DATABASE_URL"]
    application_url = os.environ["KG_TEST_DATABASE_URL"]
    readonly_url = os.environ["KG_TEST_READONLY_DATABASE_URL"]

    async def writer_permissions(connection):
        assert (
            await connection.execute(text("SELECT current_user"))
        ).scalar_one() == os.environ["KG_TEST_VERIFICATION_WRITER_ROLE"]
        privileges = (
            await connection.execute(
                text(
                    "SELECT has_table_privilege(current_user, "
                    "'public.registration_verified_outbox', 'SELECT'), "
                    "has_table_privilege(current_user, "
                    "'public.registration_verified_outbox', 'INSERT'), "
                    "has_table_privilege(current_user, "
                    "'public.registration_verified_outbox', 'DELETE'), "
                    "has_column_privilege(current_user, "
                    "'public.registration_verified_outbox', 'status', 'UPDATE')"
                )
            )
        ).one()
        assert tuple(privileges) == (True, True, False, False)
    await _with_connection(writer_url, writer_permissions)

    async def writer_cannot_update(connection):
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "UPDATE public.registration_verified_outbox "
                    "SET status = 'retry' WHERE outbox_record_id = :outbox_ref"
                ),
                {"outbox_ref": OUTBOX_REF},
            )

    await _with_connection(writer_url, writer_cannot_update)

    async def audit_read(connection):
        assert (
            await connection.execute(text("SELECT current_user"))
        ).scalar_one() == os.environ["KG_TEST_OUTBOX_AUDIT_ROLE"]
        row = (
            await connection.execute(
                text(
                    "SELECT event_id, status, attempt_count, payload_digest "
                    "FROM public.registration_verified_outbox "
                    "WHERE outbox_record_id = :outbox_ref"
                ),
                {"outbox_ref": OUTBOX_REF},
            )
        ).one()
        assert tuple(row[:3]) == (EVENT_ID, "pending", 0)
        assert len(row.payload_digest) == 64
        privileges = (
            await connection.execute(
                text(
                    "SELECT has_table_privilege(current_user, "
                    "'public.registration_verified_outbox', 'SELECT'), "
                    "has_table_privilege(current_user, "
                    "'public.registration_verified_outbox', 'INSERT,UPDATE,DELETE')"
                )
            )
        ).one()
        assert tuple(privileges) == (True, False)

    await _with_connection(audit_url, audit_read)

    async def audit_cannot_insert(connection):
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "INSERT INTO public.registration_verified_outbox "
                    "(outbox_record_id) VALUES (:outbox_ref)"
                ),
                {"outbox_ref": UUID("01890f3e-7b7d-7cc3-b8c8-2f5a12d28234")},
            )

    await _with_connection(audit_url, audit_cannot_insert)

    async def worker_update(connection):
        assert (
            await connection.execute(text("SELECT current_user"))
        ).scalar_one() == os.environ["KG_TEST_DELIVERY_WORKER_ROLE"]
        privileges = (
            await connection.execute(
                text(
                    "SELECT has_table_privilege(current_user, "
                    "'public.registration_verified_outbox', 'SELECT'), "
                    "has_table_privilege(current_user, "
                    "'public.registration_verified_outbox', 'INSERT'), "
                    "has_table_privilege(current_user, "
                    "'public.registration_verified_outbox', 'DELETE'), "
                    "has_column_privilege(current_user, "
                    "'public.registration_verified_outbox', 'status', 'UPDATE'), "
                    "has_column_privilege(current_user, "
                    "'public.registration_verified_outbox', 'payload_digest', 'UPDATE')"
                )
            )
        ).one()
        assert tuple(privileges) == (True, True, False, True, False)
        await connection.execute(
            text(
                "UPDATE public.registration_verified_outbox SET "
                "status = 'processing', lease_owner = 'worker-1', "
                "locked_until = now() + interval '1 minute', "
                "lease_generation = 1, attempt_count = 1, updated_at = now() "
                "WHERE outbox_record_id = :outbox_ref"
            ),
            {"outbox_ref": OUTBOX_REF},
        )

    await _with_connection(worker_url, worker_update)

    async def worker_cannot_change_immutable_envelope(connection):
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "UPDATE public.registration_verified_outbox "
                    "SET payload_digest = :payload_digest "
                    "WHERE outbox_record_id = :outbox_ref"
                ),
                {"payload_digest": "d" * 64, "outbox_ref": OUTBOX_REF},
            )

    await _with_connection(
        worker_url, worker_cannot_change_immutable_envelope
    )

    async def forbidden_runtime_role(connection):
        privileges = (
            await connection.execute(
                text(
                    "SELECT has_table_privilege(current_user, "
                    "'public.registration_verified_outbox', 'SELECT'), "
                    "has_table_privilege(current_user, "
                    "'public.registration_verified_outbox', 'INSERT,UPDATE,DELETE')"
                )
            )
        ).one()
        assert tuple(privileges) == (False, False)

    await _with_connection(application_url, forbidden_runtime_role)
    await _with_connection(readonly_url, forbidden_runtime_role)


async def _production_writer_round_trip():
    application_url = os.environ["KG_TEST_DATABASE_URL"]
    writer_url = os.environ["KG_TEST_VERIFICATION_WRITER_DATABASE_URL"]

    async def seed(connection):
        await connection.execute(
            text(
                'INSERT INTO public."user" '
                "(id, phone, password_hash, role, status, verify_status, "
                "created_at, updated_at) VALUES "
                "(:user_ref, '13900082301', 'not-a-secret', 'member', "
                "'active', 'pending', :now, :now)"
            ),
            {"user_ref": USER_REF, "now": NOW},
        )
        await connection.execute(
            text(
                "INSERT INTO public.user_account_classification_decision "
                "(decision_ref, user_ref, facts_version, classification_version, "
                "account_class, decision_basis_code, decided_at) VALUES "
                "(:decision_ref, :user_ref, 11, 4, 'natural_person', "
                "'trusted_provisioning', :now)"
            ),
            {
                "decision_ref": CLASSIFICATION_REF,
                "user_ref": USER_REF,
                "now": NOW,
            },
        )
        await connection.execute(
            text(
                'INSERT INTO public."user" '
                "(id, phone, password_hash, role, status, verify_status, "
                "created_at, updated_at) VALUES "
                "(:user_ref, '13900082303', 'not-a-secret', 'member', "
                "'active', 'verified', :now, :now)"
            ),
            {"user_ref": OTHER_POLICY_USER_REF, "now": NOW},
        )
        await connection.execute(
            text(
                "INSERT INTO public.user_account_classification_decision "
                "(decision_ref, user_ref, facts_version, classification_version, "
                "account_class, decision_basis_code, decided_at) VALUES "
                "(:decision_ref, :user_ref, 13, 1, 'natural_person', "
                "'trusted_provisioning', :now)"
            ),
            {
                "decision_ref": OTHER_POLICY_CLASSIFICATION_REF,
                "user_ref": OTHER_POLICY_USER_REF,
                "now": NOW,
            },
        )

    await _with_connection(application_url, seed)

    engine = create_async_engine(writer_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    command = P1VerificationTransitionCommand(
        authority="P1_IDENTITY_REVIEW",
        case_ref="review-case-82301-v4",
        decision_version=4,
        target_facts_version=11,
        user_ref=USER_REF,
        verification_epoch=4,
        outcome="verified",
        evidence_digest="verification-evidence-digest-v4",
        actor_type="platform_reviewer",
        actor_ref="reviewer-ref-17",
        decided_at=NOW,
    )
    writer = P1VerificationTransitionWriter(
        unit_of_work_factory=lambda: SqlAlchemyP1VerificationTransitionUnitOfWork(
            session_factory
        ),
        uuid_generator=_UuidGenerator(),
    )
    try:
        created = await writer.execute(command)
        replayed = await writer.execute(command)
    finally:
        await engine.dispose()

    assert created.replayed is False
    assert replayed.replayed is True
    assert created.verification_decision_ref == VERIFICATION_REF
    assert replayed.registration_event_id == EVENT_ID


def test_注册持久发件箱权限矩阵与真实往返(pg_database):
    asyncio.run(_production_writer_round_trip())
    asyncio.run(_permission_and_round_trip_contract())


async def _r2_worker_round_trip_contract():
    worker_url = os.environ["KG_TEST_DELIVERY_WORKER_DATABASE_URL"]
    application_url = os.environ["KG_TEST_DATABASE_URL"]
    writer_url = os.environ["KG_TEST_VERIFICATION_WRITER_DATABASE_URL"]
    worker_engine = create_async_engine(worker_url, poolclass=NullPool)
    worker_sessions = async_sessionmaker(
        worker_engine, expire_on_commit=False
    )

    async def reset_for_claim(connection):
        await connection.execute(
            text(
                "UPDATE public.registration_verified_outbox SET "
                "status='pending', available_at=now(), attempt_count=0, "
                "lease_owner=NULL, locked_until=NULL, lease_generation=1, "
                "last_error_category=NULL, last_error_code=NULL, "
                "last_error_digest=NULL, delivered_at=NULL, updated_at=now() "
                "WHERE event_id=:event_id"
            ),
            {"event_id": EVENT_ID},
        )

    await _with_connection(worker_url, reset_for_claim)

    async def claim_once(owner):
        async with SqlAlchemyRegistrationOutboxWorkerUnitOfWork(
            worker_sessions
        ) as unit_of_work:
            items = await unit_of_work.claim(
                lease_owner=owner, limit=1, lease_seconds=90
            )
            await unit_of_work.commit()
            return items

    first, second = await asyncio.gather(
        claim_once("worker-r2-a"), claim_once("worker-r2-b")
    )
    winners = tuple(first) + tuple(second)
    assert len(winners) == 1
    winner = winners[0]

    async with SqlAlchemyRegistrationOutboxWorkerUnitOfWork(
        worker_sessions
    ) as unit_of_work:
        assert await unit_of_work.transition(
            event_id=winner.event_id,
            lease_owner=winner.lease_owner,
            lease_generation=winner.lease_generation,
            target_status="retry",
            error_category="RETRYABLE",
            error_code="ORCHESTRATOR_RETRYABLE",
            error_digest="e" * 64,
            retry_delay_seconds=0,
        )
        await unit_of_work.commit()

    async def completed(_):
        return RegistrationOutboxDeliveryResult(DeliveryStatus.COMPLETED)

    async def not_needed(_):
        raise AssertionError("stable result must not be confirmed")

    summary = await RegistrationOutboxDispatcher(
        unit_of_work_factory=lambda: (
            SqlAlchemyRegistrationOutboxWorkerUnitOfWork(worker_sessions)
        ),
        orchestrator=completed,
        outcome_confirmer=not_needed,
    ).run_once(lease_owner="worker-r2-delivery", limit=1)
    assert summary.delivered == 1

    async def verify_delivered(connection):
        row = (
            await connection.execute(
                text(
                    "SELECT status, attempt_count, lease_owner, "
                    "locked_until, delivered_at FROM "
                    "public.registration_verified_outbox "
                    "WHERE event_id=:event_id"
                ),
                {"event_id": EVENT_ID},
            )
        ).one()
        assert row.status == "delivered"
        assert row.attempt_count == 2
        assert row.lease_owner is None
        assert row.locked_until is None
        assert row.delivered_at is not None

    await _with_connection(worker_url, verify_delivered)

    async def seed_source(connection):
        await connection.execute(
            text(
                'INSERT INTO public."user" '
                "(id, phone, password_hash, role, status, verify_status, "
                "created_at, updated_at) VALUES "
                "(:user_ref, '13900082302', 'not-a-secret', 'member', "
                "'active', 'verified', :now, :now)"
            ),
            {"user_ref": RECONCILE_USER_REF, "now": NOW},
        )
        await connection.execute(
            text(
                "INSERT INTO public.user_account_classification_decision "
                "(decision_ref, user_ref, facts_version, classification_version, "
                "account_class, decision_basis_code, decided_at) VALUES "
                "(:decision_ref, :user_ref, 12, 1, 'natural_person', "
                "'trusted_provisioning', :now)"
            ),
            {
                "decision_ref": RECONCILE_CLASSIFICATION_REF,
                "user_ref": RECONCILE_USER_REF,
                "now": NOW,
            },
        )

    await _with_connection(application_url, seed_source)
    projection_digest = build_p1_projection_digest(
        user_ref=RECONCILE_USER_REF,
        role="member",
        status="active",
        verify_status="verified",
        updated_at=NOW,
    )

    async def seed_evidence(connection):
        await connection.execute(
            text(
                "INSERT INTO public.identity_verification_decision "
                "(decision_ref, user_ref, facts_version, verification_epoch, "
                "outcome, evidence_digest, actor_type, actor_ref, decided_at, "
                "authority_decision_key, registration_event_id) VALUES "
                "(:decision_ref, :user_ref, 12, 1, 'verified', 'evidence', "
                "'platform_reviewer', 'reviewer-r2', :now, :authority, :event_id)"
            ),
            {
                "decision_ref": RECONCILE_VERIFICATION_REF,
                "user_ref": RECONCILE_USER_REF,
                "now": NOW,
                "authority": "c" * 64,
                "event_id": RECONCILE_EVENT_ID,
            },
        )
        await connection.execute(
            text(
                "INSERT INTO public.registration_eligibility_decision "
                "(decision_ref, user_ref, facts_version, "
                "verification_decision_ref, classification_decision_ref, "
                "policy_version, facts_digest, p1_projection_digest, "
                "decision, reason, decided_at) VALUES "
                "(:decision_ref, :user_ref, 12, :verification_ref, "
                ":classification_ref, 'registration-eligibility-v1', "
                "'facts', :projection, 'eligible', 'eligible', :now)"
            ),
            {
                "decision_ref": RECONCILE_ELIGIBILITY_REF,
                "user_ref": RECONCILE_USER_REF,
                "verification_ref": RECONCILE_VERIFICATION_REF,
                "classification_ref": RECONCILE_CLASSIFICATION_REF,
                "projection": projection_digest,
                "now": NOW,
            },
        )
        other_projection_digest = build_p1_projection_digest(
            user_ref=OTHER_POLICY_USER_REF,
            role="member",
            status="active",
            verify_status="verified",
            updated_at=NOW,
        )
        await connection.execute(
            text(
                "INSERT INTO public.identity_verification_decision "
                "(decision_ref, user_ref, facts_version, verification_epoch, "
                "outcome, evidence_digest, actor_type, actor_ref, decided_at, "
                "authority_decision_key, registration_event_id) VALUES "
                "(:decision_ref, :user_ref, 13, 1, 'verified', 'evidence', "
                "'platform_reviewer', 'reviewer-legacy', :now, :authority, :event_id)"
            ),
            {
                "decision_ref": OTHER_POLICY_VERIFICATION_REF,
                "user_ref": OTHER_POLICY_USER_REF,
                "now": NOW,
                "authority": "d" * 64,
                "event_id": OTHER_POLICY_EVENT_ID,
            },
        )
        await connection.execute(
            text(
                "INSERT INTO public.registration_eligibility_decision "
                "(decision_ref, user_ref, facts_version, "
                "verification_decision_ref, classification_decision_ref, "
                "policy_version, facts_digest, p1_projection_digest, "
                "decision, reason, decided_at) VALUES "
                "(:decision_ref, :user_ref, 13, :verification_ref, "
                ":classification_ref, 'registration-eligibility-legacy', "
                "'facts', :projection, 'eligible', 'eligible', :now)"
            ),
            {
                "decision_ref": OTHER_POLICY_ELIGIBILITY_REF,
                "user_ref": OTHER_POLICY_USER_REF,
                "verification_ref": OTHER_POLICY_VERIFICATION_REF,
                "classification_ref": OTHER_POLICY_CLASSIFICATION_REF,
                "projection": other_projection_digest,
                "now": NOW,
            },
        )

    await _with_connection(writer_url, seed_evidence)
    reconciled = await RegistrationOutboxReconciler(
        unit_of_work_factory=lambda: (
            SqlAlchemyRegistrationOutboxWorkerUnitOfWork(worker_sessions)
        )
    ).run_once(limit=10)
    assert reconciled == 1

    async def verify_reconciled(connection):
        row = (
            await connection.execute(
                text(
                    "SELECT outbox_record_id, event_id, status, source_ref "
                    "FROM public.registration_verified_outbox "
                    "WHERE event_id=:event_id"
                ),
                {"event_id": RECONCILE_EVENT_ID},
            )
        ).one()
        assert row.outbox_record_id == RECONCILE_EVENT_ID
        assert row.status == "pending"
        assert row.source_ref == RECONCILE_USER_REF

    await _with_connection(worker_url, verify_reconciled)
    await worker_engine.dispose()


def test_注册持久发件箱R2权限并发恢复与对账真实往返(pg_database):
    asyncio.run(_r2_worker_round_trip_contract())
