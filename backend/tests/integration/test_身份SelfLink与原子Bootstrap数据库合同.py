import asyncio
import os
from collections import deque
from datetime import datetime, timezone
from uuid import UUID

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.composition.identity_persistence import (
    IdentityPersistenceComposition,
    create_registration_identity_bootstrap_service,
)
from app.modules.auth.eligibility_evidence import build_p1_projection_digest
from app.modules.member.application.registration_bootstrap import (
    RegistrationIdentityBootstrapCommand,
    RegistrationIdentityBootstrapConflict,
    RegistrationIdentityBootstrapFailed,
)
from tests.integration.conftest import (
    _get_application_database_url,
    _get_readonly_database_url,
    _to_asyncpg_dsn,
)


pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)


class _UuidGenerator:
    def __init__(self, *values):
        self.values = deque(values)

    def generate(self):
        return self.values.popleft()


def _uuid(suffix: int) -> UUID:
    return UUID(f"01890f3e-7b7d-7cc3-88c8-2f5a12d2{suffix:04d}")


async def _seed_proofs(session_factory, *, phone, member_no, base):
    verification_ref = _uuid(base + 1)
    classification_ref = _uuid(base + 2)
    eligibility_ref = _uuid(base + 3)
    allocation_ref = _uuid(base + 4)
    request_ref = _uuid(base + 5)
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
        projection_digest = build_p1_projection_digest(
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
                "'trusted_provider', 'provider-1', :now)"
            ),
            {
                "ref": verification_ref,
                "user_ref": user_ref,
                "digest": "a" * 64,
                "now": NOW,
            },
        )
        await session.execute(
            text(
                "INSERT INTO public.user_account_classification_decision "
                "(decision_ref, user_ref, facts_version, "
                "classification_version, account_class, decision_basis_code, "
                "decided_at) VALUES (:ref, :user_ref, 1, 1, "
                "'natural_person', 'trusted_provisioning', :now)"
            ),
            {
                "ref": classification_ref,
                "user_ref": user_ref,
                "now": NOW,
            },
        )
        await session.execute(
            text(
                "INSERT INTO public.registration_eligibility_decision "
                "(decision_ref, user_ref, facts_version, "
                "verification_decision_ref, classification_decision_ref, "
                "policy_version, facts_digest, p1_projection_digest, "
                "decision, reason, decided_at) VALUES "
                "(:ref, :user_ref, 1, :verification_ref, "
                ":classification_ref, 'v1', :facts_digest, "
                ":projection_digest, 'eligible', 'eligible', :now)"
            ),
            {
                "ref": eligibility_ref,
                "user_ref": user_ref,
                "verification_ref": verification_ref,
                "classification_ref": classification_ref,
                "facts_digest": "b" * 64,
                "projection_digest": projection_digest,
                "now": NOW,
            },
        )
        await session.execute(
            text(
                "INSERT INTO identity.member_no_allocation "
                "(allocation_id, allocation_scope, source_system, source_ref, "
                "member_no, state, request_ref, version, created_at, updated_at) "
                "VALUES (:allocation_ref, 'registration_bootstrap', "
                "'p1_user', :user_ref, :member_no, 'allocated', "
                ":request_ref, 1, :now, :now)"
            ),
            {
                "allocation_ref": allocation_ref,
                "user_ref": user_ref,
                "member_no": member_no,
                "request_ref": request_ref,
                "now": NOW,
            },
        )
        await session.commit()
    return user_ref, eligibility_ref, allocation_ref


def _command(user_ref, eligibility_ref, allocation_ref, event_ref):
    return RegistrationIdentityBootstrapCommand(
        user_ref=user_ref,
        registration_event_id=event_ref,
        eligibility_decision_ref=eligibility_ref,
        member_no_allocation_ref=allocation_ref,
    )


def _service(session_factory, ids):
    composition = IdentityPersistenceComposition(
        session_factory=session_factory,
        clock=lambda: NOW,
    )
    return create_registration_identity_bootstrap_service(
        identity_persistence=composition,
        uuid_generator=_UuidGenerator(*ids),
    )


async def _counts(session_factory, user_ref):
    async with session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM identity.member AS m "
                    "JOIN identity.user_member_self_link AS l "
                    "ON l.member_id = m.member_id WHERE l.user_ref = :user_ref) "
                    "AS members, "
                    "(SELECT count(*) FROM identity.user_member_self_link "
                    "WHERE user_ref = :user_ref) AS links, "
                    "(SELECT count(*) FROM identity.registration_bootstrap_record "
                    "WHERE user_ref = :user_ref) AS records"
                ),
                {"user_ref": user_ref},
            )
        ).one()
        return tuple(row)


def test_Application与Readonly角色权限精确且Migration为Owner(pg_database):
    async def privileges(database_url):
        connection = await asyncpg.connect(_to_asyncpg_dsn(database_url))
        try:
            return await connection.fetchrow(
                "SELECT current_user AS role_name, "
                "has_schema_privilege(current_user, 'identity', 'USAGE') AS usage, "
                "has_schema_privilege(current_user, 'identity', 'CREATE') AS create_schema, "
                "has_table_privilege(current_user, 'identity.user_member_self_link', 'SELECT') AS link_select, "
                "has_table_privilege(current_user, 'identity.user_member_self_link', 'INSERT') AS link_insert, "
                "has_table_privilege(current_user, 'identity.user_member_self_link', 'UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS link_forbidden, "
                "has_table_privilege(current_user, 'identity.registration_bootstrap_record', 'SELECT') AS record_select, "
                "has_table_privilege(current_user, 'identity.registration_bootstrap_record', 'INSERT') AS record_insert, "
                "has_table_privilege(current_user, 'identity.registration_bootstrap_record', 'UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS record_forbidden"
            )
        finally:
            await connection.close()

    async def scenario():
        application = await privileges(_get_application_database_url())
        readonly = await privileges(_get_readonly_database_url())
        assert application["usage"] is True
        assert application["create_schema"] is False
        assert application["link_select"] is True
        assert application["link_insert"] is True
        assert application["link_forbidden"] is False
        assert application["record_select"] is True
        assert application["record_insert"] is True
        assert application["record_forbidden"] is False
        assert readonly["usage"] is True
        assert readonly["create_schema"] is False
        assert readonly["link_select"] is True
        assert readonly["link_insert"] is False
        assert readonly["link_forbidden"] is False
        assert readonly["record_select"] is True
        assert readonly["record_insert"] is False
        assert readonly["record_forbidden"] is False

    asyncio.run(scenario())


def test_MemberSelfLinkBootstrap同事务RoundTrip并稳定重放(pg_database):
    async def scenario():
        engine = create_async_engine(
            _get_application_database_url(), poolclass=NullPool
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            user_ref, eligibility_ref, allocation_ref = await _seed_proofs(
                session_factory,
                phone="13910000101",
                member_no="M00000000000000000101",
                base=100,
            )
            command = _command(
                user_ref, eligibility_ref, allocation_ref, _uuid(106)
            )
            first = await _service(
                session_factory, (_uuid(107), _uuid(108), _uuid(109))
            ).execute(command)
            replay = await _service(
                session_factory, (_uuid(110), _uuid(111), _uuid(112))
            ).execute(
                _command(
                    user_ref, eligibility_ref, allocation_ref, _uuid(113)
                )
            )
            assert first.replayed is False
            assert replay.replayed is True
            assert replay.member_id == first.member_id
            assert replay.self_link_id == first.self_link_id
            assert replay.bootstrap_record_id == first.bootstrap_record_id
            assert await _counts(session_factory, user_ref) == (1, 1, 1)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_并发同一P1Source只有一个完整赢家且无半成品(pg_database):
    async def scenario():
        engine = create_async_engine(
            _get_application_database_url(), poolclass=NullPool
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            user_ref, eligibility_ref, allocation_ref = await _seed_proofs(
                session_factory,
                phone="13910000201",
                member_no="M00000000000000000201",
                base=200,
            )
            command = _command(
                user_ref, eligibility_ref, allocation_ref, _uuid(206)
            )
            outcomes = await asyncio.gather(
                _service(
                    session_factory, (_uuid(207), _uuid(208), _uuid(209))
                ).execute(command),
                _service(
                    session_factory, (_uuid(210), _uuid(211), _uuid(212))
                ).execute(command),
                return_exceptions=True,
            )
            assert not [
                item for item in outcomes if isinstance(item, Exception)
            ]
            assert {item.replayed for item in outcomes} == {False, True}
            assert len({item.member_id for item in outcomes}) == 1
            assert len({item.self_link_id for item in outcomes}) == 1
            assert len({item.bootstrap_record_id for item in outcomes}) == 1
            assert await _counts(session_factory, user_ref) == (1, 1, 1)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_SelfLink写入失败回滚Member且不留下Bootstrap半成品(pg_database):
    async def scenario():
        engine = create_async_engine(
            _get_application_database_url(), poolclass=NullPool
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        occupied_link_id = _uuid(309)
        try:
            async with session_factory() as session:
                other_user = (
                    await session.execute(
                        text(
                            'INSERT INTO "user" (phone, password_hash, role, '
                            "status, verify_status, created_at, updated_at) VALUES "
                            "('13910000300', 'test-only-hash', 'member', "
                            "'active', 'verified', :now, :now) RETURNING id"
                        ),
                        {"now": NOW},
                    )
                ).scalar_one()
                await session.execute(
                    text(
                        "INSERT INTO identity.member "
                        "(member_id, member_no, creation_source, status, "
                        "version, created_at, updated_at) VALUES "
                        "(:member_id, 'M00000000000000000300', "
                        "'registration', 'created', 0, :now, :now)"
                    ),
                    {"member_id": _uuid(307), "now": NOW},
                )
                await session.execute(
                    text(
                        "INSERT INTO identity.user_member_self_link "
                        "(link_id, user_ref, member_id, source, "
                        "eligibility_decision_ref, establishment_basis, "
                        "establishment_record_ref, created_at) VALUES "
                        "(:link_id, :user_ref, :member_id, "
                        "'REGISTRATION_VERIFIED', :proof_ref, "
                        "'REGISTRATION_VERIFIED_BOOTSTRAP', "
                        ":record_ref, :now)"
                    ),
                    {
                        "link_id": occupied_link_id,
                        "user_ref": other_user,
                        "member_id": _uuid(307),
                        "proof_ref": _uuid(308),
                        "record_ref": _uuid(306),
                        "now": NOW,
                    },
                )
                await session.commit()
            user_ref, eligibility_ref, allocation_ref = await _seed_proofs(
                session_factory,
                phone="13910000301",
                member_no="M00000000000000000301",
                base=320,
            )
            service = _service(
                session_factory, (_uuid(327), occupied_link_id, _uuid(329))
            )
            with pytest.raises(RegistrationIdentityBootstrapFailed):
                await service.execute(
                    _command(
                        user_ref, eligibility_ref, allocation_ref, _uuid(326)
                    )
                )
            assert await _counts(session_factory, user_ref) == (0, 0, 0)
        finally:
            await engine.dispose()

    asyncio.run(scenario())
