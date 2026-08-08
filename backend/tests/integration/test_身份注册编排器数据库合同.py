import asyncio
import hashlib
import json
from collections import deque
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.composition.identity_persistence import (
    IdentityPersistenceComposition,
    create_registration_orchestrator,
)
from app.modules.auth.eligibility_evidence import build_p1_projection_digest
from app.modules.auth.registration_outbox import POLICY_VERSION
from app.modules.auth.registration_outbox_worker import (
    ConfirmationStatus,
    DeliveryStatus,
    RegistrationOutboxWorkItem,
)
from app.modules.member.entities import Member
from app.modules.member.value_objects import CreationSource, MemberNo
from tests.integration.conftest import _get_application_database_url


pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 7, 15, 0, tzinfo=UTC)


def _uuid(suffix: int) -> UUID:
    return UUID(f"01890f3e-7b7d-7cc3-88c8-2f5a12d3{suffix:04d}")


class _UuidGenerator:
    def __init__(self, *values):
        self._values = deque(values)

    def generate(self):
        return self._values.popleft()


async def _seed(
    session_factory, *, phone, member_no, base, include_allocation=True
):
    verification_ref = _uuid(base + 1)
    classification_ref = _uuid(base + 2)
    eligibility_ref = _uuid(base + 3)
    allocation_ref = _uuid(base + 4)
    event_ref = _uuid(base + 5)
    async with session_factory() as session:
        user_ref = (
            await session.execute(
                text(
                    'INSERT INTO "user" '
                    "(phone, password_hash, role, status, verify_status, "
                    "created_at, updated_at) VALUES "
                    "(:phone, 'test-only-hash', 'member', 'active', "
                    "'verified', :now, :now) RETURNING id"
                ),
                {"phone": phone, "now": NOW},
            )
        ).scalar_one()
        projection = build_p1_projection_digest(
            user_ref=user_ref,
            role="member",
            status="active",
            verify_status="verified",
            updated_at=NOW,
        )
        await session.execute(
            text(
                "INSERT INTO public.identity_verification_decision "
                "(decision_ref, user_ref, facts_version, verification_epoch, "
                "outcome, evidence_digest, actor_type, actor_ref, decided_at) "
                "VALUES (:ref, :user_ref, 1, 1, 'verified', :digest, "
                "'trusted_provider', 'provider-orchestrator', :now)"
            ),
            {"ref": verification_ref, "user_ref": user_ref, "digest": "c" * 64, "now": NOW},
        )
        await session.execute(
            text(
                "INSERT INTO public.user_account_classification_decision "
                "(decision_ref, user_ref, facts_version, classification_version, "
                "account_class, decision_basis_code, decided_at) VALUES "
                "(:ref, :user_ref, 1, 1, 'natural_person', "
                "'trusted_provisioning', :now)"
            ),
            {"ref": classification_ref, "user_ref": user_ref, "now": NOW},
        )
        await session.execute(
            text(
                "INSERT INTO public.registration_eligibility_decision "
                "(decision_ref, user_ref, facts_version, verification_decision_ref, "
                "classification_decision_ref, policy_version, facts_digest, "
                "p1_projection_digest, decision, reason, decided_at) VALUES "
                "(:ref, :user_ref, 1, :verification_ref, :classification_ref, "
                ":policy, :facts_digest, :projection, 'eligible', 'eligible', :now)"
            ),
            {
                "ref": eligibility_ref,
                "user_ref": user_ref,
                "verification_ref": verification_ref,
                "classification_ref": classification_ref,
                "policy": POLICY_VERSION,
                "facts_digest": "d" * 64,
                "projection": projection,
                "now": NOW,
            },
        )
        if include_allocation:
            await session.execute(
                text(
                    "INSERT INTO identity.member_no_allocation "
                    "(allocation_id, allocation_scope, source_system, source_ref, "
                    "member_no, state, request_ref, version, created_at, updated_at) "
                    "VALUES (:allocation, 'registration_bootstrap', 'p1_user', "
                    ":user_ref, :member_no, 'allocated', :event, 1, :now, :now)"
                ),
                {
                    "allocation": allocation_ref,
                    "user_ref": user_ref,
                    "member_no": member_no,
                    "event": event_ref,
                    "now": NOW,
                },
            )
        await session.commit()
    return user_ref, verification_ref, event_ref


def _item(user_ref, verification_ref, event_ref):
    authority = "e" * 64
    semantic = (
        "identity.registration.verification_verified:v1:"
        f"p1_user:{user_ref}:authority:{authority}"
    )
    canonical = json.dumps(
        {
            "authority_decision_key": authority,
            "event_id": str(event_ref),
            "event_schema_version": 1,
            "event_type": "identity.registration.verification_verified",
            "facts_version": 1,
            "occurred_at": NOW.isoformat(),
            "source_ref": user_ref,
            "source_system": "P1_USER",
            "trace_ref": None,
            "verification_decision_ref": str(verification_ref),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return RegistrationOutboxWorkItem(
        outbox_record_id=event_ref,
        event_id=event_ref,
        semantic_idempotency_key=semantic,
        event_type="identity.registration.verification_verified",
        event_schema_version=1,
        source_system="P1_USER",
        source_ref=user_ref,
        verification_decision_ref=verification_ref,
        authority_decision_key=authority,
        facts_version=1,
        occurred_at=NOW,
        payload_digest=hashlib.sha256(canonical.encode()).hexdigest(),
        trace_ref=None,
        attempt_count=1,
        lease_owner="orchestrator-test",
        lease_generation=1,
        locked_until=NOW + timedelta(seconds=90),
    )


def _orchestrator(session_factory, ids, random_bits=lambda _: 1):
    composition = IdentityPersistenceComposition(
        session_factory=session_factory, clock=lambda: NOW
    )
    return create_registration_orchestrator(
        identity_persistence=composition,
        uuid_generator=_UuidGenerator(*ids),
        random_bits=random_bits,
    )


def test_内部注册编排原子RoundTrip稳定重放并可只读确认(pg_database):
    async def scenario():
        engine = create_async_engine(
            _get_application_database_url(), poolclass=NullPool
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            user_ref, verification_ref, event_ref = await _seed(
                session_factory,
                phone="13910003101",
                member_no="M00000000000000003101",
                base=3100,
            )
            item = _item(user_ref, verification_ref, event_ref)
            first = await _orchestrator(
                session_factory, (_uuid(3110), _uuid(3111), _uuid(3112))
            ).deliver(item)
            replay = await _orchestrator(
                session_factory, (_uuid(3120), _uuid(3121), _uuid(3122))
            ).deliver(item)
            confirmation = await _orchestrator(
                session_factory, (_uuid(3130), _uuid(3131), _uuid(3132))
            ).confirm(item)
            assert first.status is DeliveryStatus.COMPLETED
            assert replay.status is DeliveryStatus.REPLAYED
            assert confirmation is ConfirmationStatus.COMPLETE
            async with session_factory() as session:
                counts = []
                for table in (
                        "member_no_allocation",
                        "member",
                        "user_member_self_link",
                        "registration_bootstrap_record",
                ):
                    counts.append(
                        int(
                            await session.scalar(
                                text(
                                    f"SELECT count(*) FROM identity.{table}"
                                )
                            )
                        )
                    )
            assert counts == [1, 1, 1, 1]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_MemberNo分配冲突时Identity保持零写入(pg_database):
    async def scenario():
        engine = create_async_engine(
            _get_application_database_url(), poolclass=NullPool
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            user_ref, verification_ref, event_ref = await _seed(
                session_factory,
                phone="13910003301",
                member_no="M00000000000000003301",
                base=3300,
                include_allocation=False,
            )
            async with session_factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO identity.member_no_allocation "
                        "(allocation_id, allocation_scope, source_system, source_ref, "
                        "member_no, state, request_ref, version, created_at, updated_at) "
                        "VALUES (:allocation, 'registration_bootstrap', 'p1_user', "
                        ":source_ref, 'M00000000000000000000', 'allocated', "
                        ":event, 1, :now, :now)"
                    ),
                    {
                        "allocation": _uuid(3306),
                        "source_ref": user_ref + 10000,
                        "event": _uuid(3307),
                        "now": NOW,
                    },
                )
                await session.commit()
            result = await _orchestrator(
                session_factory,
                (_uuid(3310), _uuid(3311), _uuid(3312)),
                random_bits=lambda _: 0,
            ).deliver(_item(user_ref, verification_ref, event_ref))
            assert result.status is DeliveryStatus.REVIEW_REQUIRED
            async with session_factory() as session:
                counts = (
                    await session.scalar(
                        text(
                            "SELECT count(*) FROM identity.member "
                            "WHERE member_no = 'M00000000000000000000'"
                        )
                    ),
                    await session.scalar(
                        text(
                            "SELECT count(*) FROM identity.user_member_self_link "
                            "WHERE user_ref = :user_ref"
                        ),
                        {"user_ref": user_ref},
                    ),
                    await session.scalar(
                        text(
                            "SELECT count(*) FROM identity.registration_bootstrap_record "
                            "WHERE user_ref = :user_ref"
                        ),
                        {"user_ref": user_ref},
                    ),
                )
            assert counts == (0, 0, 0)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_Bootstrap最后一步冲突时MemberLinkRecord全部回滚(pg_database):
    async def scenario():
        engine = create_async_engine(
            _get_application_database_url(), poolclass=NullPool
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            first_user, first_verification, first_event = await _seed(
                session_factory,
                phone="13910003401",
                member_no="M00000000000000003401",
                base=3400,
            )
            first = await _orchestrator(
                session_factory, (_uuid(3410), _uuid(3411), _uuid(3412))
            ).deliver(_item(first_user, first_verification, first_event))
            assert first.status is DeliveryStatus.COMPLETED
            async with session_factory() as session:
                occupied_record_id = await session.scalar(
                    text(
                        "SELECT record_id FROM identity.registration_bootstrap_record "
                        "WHERE user_ref = :user_ref"
                    ),
                    {"user_ref": first_user},
                )

            user_ref, verification_ref, event_ref = await _seed(
                session_factory,
                phone="13910003402",
                member_no="M00000000000000003402",
                base=3450,
            )
            result = await _orchestrator(
                session_factory,
                (_uuid(3460), _uuid(3461), occupied_record_id),
            ).deliver(_item(user_ref, verification_ref, event_ref))
            assert result.status is DeliveryStatus.INTERNAL_UNKNOWN
            async with session_factory() as session:
                counts = (
                    await session.scalar(
                        text(
                            "SELECT count(*) FROM identity.member "
                            "WHERE member_no = 'M00000000000000003402'"
                        )
                    ),
                    await session.scalar(
                        text(
                            "SELECT count(*) FROM identity.user_member_self_link "
                            "WHERE user_ref = :user_ref"
                        ),
                        {"user_ref": user_ref},
                    ),
                    await session.scalar(
                        text(
                            "SELECT count(*) FROM identity.registration_bootstrap_record "
                            "WHERE user_ref = :user_ref"
                        ),
                        {"user_ref": user_ref},
                    ),
                )
            assert counts == (0, 0, 0)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_Cancellation退出BootstrapUoW时rollback且新鲜Session不可见(pg_database):
    async def scenario():
        engine = create_async_engine(
            _get_application_database_url(), poolclass=NullPool
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        member_no = "M00000000000000003501"
        composition = IdentityPersistenceComposition(
            session_factory=session_factory, clock=lambda: NOW
        )
        try:
            with pytest.raises(asyncio.CancelledError):
                async with composition.registration_bootstrap_unit_of_work() as uow:
                    await uow.members.add(
                        Member.create(
                            member_id=_uuid(3501),
                            member_no=MemberNo(member_no),
                            creation_source=CreationSource.REGISTRATION,
                        )
                    )
                    raise asyncio.CancelledError
            async with session_factory() as session:
                count = await session.scalar(
                    text(
                        "SELECT count(*) FROM identity.member "
                        "WHERE member_no = :member_no"
                    ),
                    {"member_no": member_no},
                )
            assert count == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())
