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


pytestmark = pytest.mark.integration

USER_REF = 82301
CLASSIFICATION_REF = UUID("01890f3e-7b7d-7cc3-88c8-2f5a12d28230")
VERIFICATION_REF = UUID("01890f3e-7b7d-7cc3-88c8-2f5a12d28231")
EVENT_ID = UUID("01890f3e-7b7d-7cc3-98c8-2f5a12d28232")
OUTBOX_REF = UUID("01890f3e-7b7d-7cc3-a8c8-2f5a12d28233")
NOW = datetime(2026, 8, 8, tzinfo=timezone.utc)


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
