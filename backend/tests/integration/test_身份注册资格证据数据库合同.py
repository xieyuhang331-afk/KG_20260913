import asyncio
import os
from datetime import datetime, timezone
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.modules.auth.eligibility_evidence import (
    IdentityVerificationEvidence,
    RegistrationEligibilityDecisionEvidence,
    RegistrationEligibilityDecisionService,
    RegistrationEligibilityEvidenceConflict,
    RegistrationEligibilityEvidenceInconsistent,
    UserAccountClassificationEvidence,
)
from app.modules.auth.eligibility_evidence_repository import (
    SqlAlchemyEligibilityDecisionEvidenceReader,
    SqlAlchemyRegistrationEligibilityEvidenceStore,
    SqlAlchemyRegistrationEligibilityFactsReader,
)
from app.modules.member.application.registration_eligibility import (
    AccountClass,
    EligibilityDecision,
    EligibilityReason,
    VerificationOutcome,
)


pytestmark = pytest.mark.integration

VERIFICATION_REF = UUID("01890f3e-7b7d-7cc3-98c8-2f5a12d21234")
CLASSIFICATION_REF = UUID("01890f3e-7b7d-7cc3-a8c8-2f5a12d25678")
ELIGIBILITY_REF = UUID("01890f3e-7b7d-7cc3-b8c8-2f5a12d29876")
NOW = datetime(2026, 8, 7, 0, 0, tzinfo=timezone.utc)


class _UuidGenerator:
    def generate(self):
        return ELIGIBILITY_REF


class _FixedUuidGenerator:
    def __init__(self, value):
        self._value = value

    def generate(self):
        return self._value


async def _execute_contract():
    application_url = os.environ["KG_TEST_DATABASE_URL"]
    readonly_url = os.environ["KG_TEST_READONLY_DATABASE_URL"]
    app_engine = create_async_engine(application_url, poolclass=NullPool)
    readonly_engine = create_async_engine(readonly_url, poolclass=NullPool)
    factory = async_sessionmaker(app_engine, expire_on_commit=False)
    try:
        async with factory() as session:
            user_ref = (
                await session.execute(
                    text(
                        'INSERT INTO "user" '
                        "(phone, password_hash, role, status, verify_status, "
                        "created_at, updated_at) VALUES "
                        "('13900000071', 'not-a-real-secret', 'member', "
                        "'active', 'verified', now(), now()) RETURNING id"
                    )
                )
            ).scalar_one()
            store = SqlAlchemyRegistrationEligibilityEvidenceStore(session)
            await store.add_verification(
                IdentityVerificationEvidence(
                    decision_ref=VERIFICATION_REF,
                    user_ref=user_ref,
                    facts_version=1,
                    verification_epoch=1,
                    outcome=VerificationOutcome.VERIFIED,
                    evidence_digest="a" * 64,
                    actor_type="trusted_provider",
                    actor_ref="provider-1",
                    decided_at=NOW,
                )
            )
            await store.add_classification(
                UserAccountClassificationEvidence(
                    decision_ref=CLASSIFICATION_REF,
                    user_ref=user_ref,
                    facts_version=1,
                    classification_version=1,
                    account_class=AccountClass.NATURAL_PERSON,
                    decision_basis_code="trusted_provisioning",
                    decided_at=NOW,
                )
            )
            service = RegistrationEligibilityDecisionService(
                facts_reader=SqlAlchemyRegistrationEligibilityFactsReader(
                    session
                ),
                evidence_store=store,
                uuid_generator=_UuidGenerator(),
                clock=lambda: NOW,
            )
            first = await service.decide(
                user_ref=user_ref, policy_version="v1"
            )
            second = await service.decide(
                user_ref=user_ref, policy_version="v1"
            )
            await session.commit()

            assert first.decision is EligibilityDecision.ELIGIBLE
            assert first.decision_ref == second.decision_ref
            proof = await SqlAlchemyEligibilityDecisionEvidenceReader(
                session
            ).get_current_eligible(
                decision_ref=first.decision_ref,
                user_ref=user_ref,
            )
            assert proof.user_ref == user_ref
            assert proof.facts_version == 1

            for table_name in (
                "identity_verification_decision",
                "user_account_classification_decision",
                "registration_eligibility_decision",
            ):
                privileges = (
                    await session.execute(
                        text(
                            "SELECT has_table_privilege(current_user, "
                            f"'public.{table_name}', 'SELECT'), "
                            "has_table_privilege(current_user, "
                            f"'public.{table_name}', 'INSERT'), "
                            "has_table_privilege(current_user, "
                            f"'public.{table_name}', 'UPDATE'), "
                            "has_table_privilege(current_user, "
                            f"'public.{table_name}', 'DELETE')"
                        )
                    )
                ).one()
                assert tuple(privileges) == (True, True, False, False)

        async with readonly_engine.connect() as connection:
            privileges = (
                await connection.execute(
                    text(
                        "SELECT has_table_privilege(current_user, "
                        "'public.registration_eligibility_decision', "
                        "'SELECT'), has_table_privilege(current_user, "
                        "'public.registration_eligibility_decision', "
                        "'INSERT,UPDATE,DELETE')"
                    )
                )
            ).one()
            assert tuple(privileges) == (True, False)
            count = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM "
                        "public.registration_eligibility_decision"
                    )
                )
            ).scalar_one()
            assert count == 1
    finally:
        await app_engine.dispose()
        await readonly_engine.dispose()


def test_注册资格证据真实数据库权限重放与currentness(pg_database):
    revision = pg_database.fetch_value(
        "SELECT version_num FROM alembic_version"
    )
    assert revision == "20260914_0046"
    asyncio.run(_execute_contract())


async def _seed_eligible_decision(
    phone,
    *,
    verification_ref=VERIFICATION_REF,
    classification_ref=CLASSIFICATION_REF,
    eligibility_ref=ELIGIBILITY_REF,
):
    application_url = os.environ["KG_TEST_DATABASE_URL"]
    engine = create_async_engine(application_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            user_ref = (
                await session.execute(
                    text(
                        'INSERT INTO "user" '
                        "(phone, password_hash, role, status, verify_status, "
                        "created_at, updated_at) VALUES "
                        "(:phone, 'not-a-real-secret', 'member', "
                        "'active', 'verified', now(), now()) RETURNING id"
                    ),
                    {"phone": phone},
                )
            ).scalar_one()
            store = SqlAlchemyRegistrationEligibilityEvidenceStore(session)
            await store.add_verification(
                IdentityVerificationEvidence(
                    decision_ref=verification_ref,
                    user_ref=user_ref,
                    facts_version=1,
                    verification_epoch=1,
                    outcome=VerificationOutcome.VERIFIED,
                    evidence_digest="a" * 64,
                    actor_type="trusted_provider",
                    actor_ref="provider-1",
                    decided_at=NOW,
                )
            )
            await store.add_classification(
                UserAccountClassificationEvidence(
                    decision_ref=classification_ref,
                    user_ref=user_ref,
                    facts_version=1,
                    classification_version=1,
                    account_class=AccountClass.NATURAL_PERSON,
                    decision_basis_code="trusted_provisioning",
                    decided_at=NOW,
                )
            )
            service = RegistrationEligibilityDecisionService(
                facts_reader=SqlAlchemyRegistrationEligibilityFactsReader(
                    session
                ),
                evidence_store=store,
                uuid_generator=_FixedUuidGenerator(eligibility_ref),
                clock=lambda: NOW,
            )
            decision = await service.decide(
                user_ref=user_ref, policy_version="v1"
            )
            await session.commit()
            return user_ref, decision.decision_ref
    finally:
        await engine.dispose()


def test_资格proof对P1当前状态漂移fail_closed(pg_database):
    async def scenario():
        application_url = os.environ["KG_TEST_DATABASE_URL"]
        user_ref, decision_ref = await _seed_eligible_decision(
            "13900000072",
            verification_ref=UUID(
                "01890f3e-7b7d-7cc3-98c8-2f5a12d22301"
            ),
            classification_ref=UUID(
                "01890f3e-7b7d-7cc3-a8c8-2f5a12d22302"
            ),
            eligibility_ref=UUID(
                "01890f3e-7b7d-7cc3-b8c8-2f5a12d22303"
            ),
        )
        engine = create_async_engine(application_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                await session.execute(
                    text(
                        'UPDATE "user" SET status = \'disabled\', '
                        "updated_at = now() WHERE id = :user_ref"
                    ),
                    {"user_ref": user_ref},
                )
                await session.commit()
            async with factory() as session:
                try:
                    await SqlAlchemyEligibilityDecisionEvidenceReader(
                        session
                    ).get_current_eligible(
                        decision_ref=decision_ref,
                        user_ref=user_ref,
                    )
                except RegistrationEligibilityEvidenceInconsistent:
                    pass
                else:
                    pytest.fail(
                        "Drifted P1 current state must fail closed"
                    )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("column", "drift_value", "restore_value", "suffix"),
    (
        ("role", "therapist", "member", "73"),
        ("status", "disabled", "active", "74"),
        ("verify_status", "failed", "verified", "75"),
    ),
)
def test_资格proof绑定P1投影且漂移恢复后仍失效(
    pg_database, column, drift_value, restore_value, suffix
):
    async def scenario():
        application_url = os.environ["KG_TEST_DATABASE_URL"]
        user_ref, decision_ref = await _seed_eligible_decision(
            f"139000000{suffix}",
            verification_ref=UUID(
                f"01890f3e-7b7d-7cc3-98c8-2f5a12d2{suffix}01"
            ),
            classification_ref=UUID(
                f"01890f3e-7b7d-7cc3-a8c8-2f5a12d2{suffix}02"
            ),
            eligibility_ref=UUID(
                f"01890f3e-7b7d-7cc3-b8c8-2f5a12d2{suffix}03"
            ),
        )
        engine = create_async_engine(application_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async def assert_fail_closed():
            async with factory() as session:
                try:
                    await SqlAlchemyEligibilityDecisionEvidenceReader(
                        session
                    ).get_current_eligible(
                        decision_ref=decision_ref,
                        user_ref=user_ref,
                    )
                except RegistrationEligibilityEvidenceInconsistent:
                    return
                pytest.fail(
                    "P1 projection drift and restore must invalidate old proof"
                )

        try:
            async with factory() as session:
                await session.execute(
                    text(
                        f'UPDATE "user" SET {column} = :value, '
                        "updated_at = updated_at + interval '1 second' "
                        "WHERE id = :user_ref"
                    ),
                    {"value": drift_value, "user_ref": user_ref},
                )
                await session.commit()
            await assert_fail_closed()

            async with factory() as session:
                await session.execute(
                    text(
                        f'UPDATE "user" SET {column} = :value, '
                        "updated_at = updated_at + interval '1 second' "
                        "WHERE id = :user_ref"
                    ),
                    {"value": restore_value, "user_ref": user_ref},
                )
                await session.commit()
            await assert_fail_closed()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


class _TwoPartyBarrier:
    def __init__(self):
        self._arrivals = 0
        self._lock = asyncio.Lock()
        self._released = asyncio.Event()

    async def wait(self):
        async with self._lock:
            self._arrivals += 1
            if self._arrivals == 2:
                self._released.set()
        await self._released.wait()


class _LatestReadBarrierSession:
    def __init__(self, session, barrier):
        self._session = session
        self._barrier = barrier
        self._waited = False

    async def execute(self, statement, *args, **kwargs):
        result = await self._session.execute(statement, *args, **kwargs)
        if not self._waited:
            self._waited = True
            await self._barrier.wait()
        return result

    def add(self, model):
        self._session.add(model)

    async def flush(self):
        await self._session.flush()


@pytest.mark.parametrize(
    "chain",
    (
        "verification",
        "classification",
    ),
)
def test_两个并发writer竞争同一predecessor只有一个成功(
    pg_database, chain
):
    async def scenario():
        application_url = os.environ["KG_TEST_DATABASE_URL"]
        suffix = "21" if chain == "verification" else "22"
        verification_ref = UUID(
            f"01890f3e-7b7d-7cc3-98c8-2f5a12d2{suffix}01"
        )
        classification_ref = UUID(
            f"01890f3e-7b7d-7cc3-a8c8-2f5a12d2{suffix}02"
        )
        eligibility_ref = UUID(
            f"01890f3e-7b7d-7cc3-b8c8-2f5a12d2{suffix}03"
        )
        replay_ref = UUID(
            f"01890f3e-7b7d-7cc3-b8c8-2f5a12d2{suffix}07"
        )
        user_ref, _ = await _seed_eligible_decision(
            f"139000000{suffix}",
            verification_ref=verification_ref,
            classification_ref=classification_ref,
            eligibility_ref=eligibility_ref,
        )
        engine = create_async_engine(application_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        barrier = _TwoPartyBarrier()
        candidate_refs = (
            UUID(f"01890f3e-7b7d-7cc3-88c8-2f5a12d2{suffix}04"),
            UUID(f"01890f3e-7b7d-7cc3-88c8-2f5a12d2{suffix}05"),
        )

        async def writer(index):
            async with factory() as session:
                store = SqlAlchemyRegistrationEligibilityEvidenceStore(
                    _LatestReadBarrierSession(session, barrier)
                )
                facts_version = index + 2
                try:
                    if chain == "verification":
                        await store.add_verification(
                            IdentityVerificationEvidence(
                                decision_ref=candidate_refs[index],
                                user_ref=user_ref,
                                facts_version=facts_version,
                                verification_epoch=facts_version,
                                outcome=VerificationOutcome.VERIFIED,
                                evidence_digest=str(index + 2) * 64,
                                actor_type="trusted_provider",
                                actor_ref="provider-1",
                                supersedes_ref=verification_ref,
                                decided_at=NOW,
                            )
                        )
                    else:
                        await store.add_classification(
                            UserAccountClassificationEvidence(
                                decision_ref=candidate_refs[index],
                                user_ref=user_ref,
                                facts_version=facts_version,
                                classification_version=facts_version,
                                account_class=AccountClass.NATURAL_PERSON,
                                decision_basis_code="trusted_provisioning",
                                supersedes_ref=classification_ref,
                                decided_at=NOW,
                            )
                        )
                    await session.commit()
                    return "committed", candidate_refs[index]
                except RegistrationEligibilityEvidenceConflict as exc:
                    assert exc.__cause__ is None
                    assert exc.__context__ is None
                    await session.rollback()
                    return "conflict", str(exc)

        try:
            outcomes = await asyncio.gather(writer(0), writer(1))
            committed = [value for state, value in outcomes if state == "committed"]
            conflicts = [value for state, value in outcomes if state == "conflict"]
            if len(committed) != 1 or conflicts != [
                "eligibility evidence conflicts with persisted state"
            ]:
                pytest.fail(
                    "Concurrent supersedes writers must produce exactly one successor"
                )

            table_name = (
                "identity_verification_decision"
                if chain == "verification"
                else "user_account_classification_decision"
            )
            parent_ref = (
                verification_ref
                if chain == "verification"
                else classification_ref
            )
            async with factory() as session:
                rows = (
                    await session.execute(
                        text(
                            f"SELECT decision_ref, supersedes_ref, facts_version "
                            f"FROM public.{table_name} "
                            "WHERE user_ref = :user_ref ORDER BY facts_version"
                        ),
                        {"user_ref": user_ref},
                    )
                ).all()
                successors = [row for row in rows if row.supersedes_ref == parent_ref]
                assert len(successors) == 1
                assert successors[0].decision_ref == committed[0]
                assert rows[0].decision_ref == parent_ref
                assert rows[0].supersedes_ref is None

                winner_facts_version = successors[0].facts_version
                store = SqlAlchemyRegistrationEligibilityEvidenceStore(session)
                if chain == "verification":
                    await store.add_classification(
                        UserAccountClassificationEvidence(
                            decision_ref=UUID(
                                f"01890f3e-7b7d-7cc3-a8c8-2f5a12d2{suffix}06"
                            ),
                            user_ref=user_ref,
                            facts_version=winner_facts_version,
                            classification_version=winner_facts_version,
                            account_class=AccountClass.NATURAL_PERSON,
                            decision_basis_code="trusted_provisioning",
                            supersedes_ref=classification_ref,
                            decided_at=NOW,
                        )
                    )
                else:
                    await store.add_verification(
                        IdentityVerificationEvidence(
                            decision_ref=UUID(
                                f"01890f3e-7b7d-7cc3-98c8-2f5a12d2{suffix}06"
                            ),
                            user_ref=user_ref,
                            facts_version=winner_facts_version,
                            verification_epoch=winner_facts_version,
                            outcome=VerificationOutcome.VERIFIED,
                            evidence_digest="c" * 64,
                            actor_type="trusted_provider",
                            actor_ref="provider-1",
                            supersedes_ref=verification_ref,
                            decided_at=NOW,
                        )
                    )
                service = RegistrationEligibilityDecisionService(
                    facts_reader=SqlAlchemyRegistrationEligibilityFactsReader(
                        session
                    ),
                    evidence_store=store,
                    uuid_generator=_FixedUuidGenerator(replay_ref),
                    clock=lambda: NOW,
                )
                first = await service.decide(
                    user_ref=user_ref, policy_version="v1"
                )
                second = await service.decide(
                    user_ref=user_ref, policy_version="v1"
                )
                assert first.decision_ref == second.decision_ref
                decision_count = (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM "
                            "public.registration_eligibility_decision "
                            "WHERE user_ref = :user_ref AND "
                            "facts_version = :facts_version AND "
                            "policy_version = 'v1'"
                        ),
                        {
                            "user_ref": user_ref,
                            "facts_version": winner_facts_version,
                        },
                    )
                ).scalar_one()
                assert decision_count == 1
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    (
        "case_suffix",
        "verification_outcome",
        "account_class",
        "decision_reason",
        "user_status",
    ),
    (
        (
            "31",
            VerificationOutcome.FAILED,
            AccountClass.NATURAL_PERSON,
            EligibilityReason.ELIGIBLE,
            "active",
        ),
        (
            "32",
            VerificationOutcome.VERIFIED,
            AccountClass.STAFF,
            EligibilityReason.ELIGIBLE,
            "active",
        ),
        (
            "33",
            VerificationOutcome.VERIFIED,
            AccountClass.NATURAL_PERSON,
            EligibilityReason.IDENTITY_NOT_VERIFIED,
            "active",
        ),
        (
            "34",
            VerificationOutcome.VERIFIED,
            AccountClass.NATURAL_PERSON,
            EligibilityReason.ELIGIBLE,
            "disabled",
        ),
    ),
    ids=(
        "verification-failed",
        "classification-forbidden",
        "reason-inconsistent",
        "p1-state-drift",
    ),
)
def test_资格proof完整权威语义复核fail_closed(
    pg_database,
    case_suffix,
    verification_outcome,
    account_class,
    decision_reason,
    user_status,
):
    async def scenario():
        application_url = os.environ["KG_TEST_DATABASE_URL"]
        engine = create_async_engine(application_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        verification_ref = UUID(
            f"01890f3e-7b7d-7cc3-98c8-2f5a12d2{case_suffix}01"
        )
        classification_ref = UUID(
            f"01890f3e-7b7d-7cc3-a8c8-2f5a12d2{case_suffix}02"
        )
        decision_ref = UUID(
            f"01890f3e-7b7d-7cc3-b8c8-2f5a12d2{case_suffix}03"
        )
        try:
            async with factory() as session:
                user_ref = (
                    await session.execute(
                        text(
                            'INSERT INTO "user" '
                            "(phone, password_hash, role, status, "
                            "verify_status, created_at, updated_at) VALUES "
                            "(:phone, 'not-a-real-secret', 'member', "
                            ":status, 'verified', now(), now()) RETURNING id"
                        ),
                        {
                            "phone": f"139000000{case_suffix}",
                            "status": user_status,
                        },
                    )
                ).scalar_one()
                store = SqlAlchemyRegistrationEligibilityEvidenceStore(
                    session
                )
                await store.add_verification(
                    IdentityVerificationEvidence(
                        decision_ref=verification_ref,
                        user_ref=user_ref,
                        facts_version=1,
                        verification_epoch=1,
                        outcome=verification_outcome,
                        evidence_digest="d" * 64,
                        actor_type="trusted_provider",
                        actor_ref="provider-1",
                        decided_at=NOW,
                    )
                )
                await store.add_classification(
                    UserAccountClassificationEvidence(
                        decision_ref=classification_ref,
                        user_ref=user_ref,
                        facts_version=1,
                        classification_version=1,
                        account_class=account_class,
                        decision_basis_code="trusted_provisioning",
                        decided_at=NOW,
                    )
                )
                await store.add_decision(
                    RegistrationEligibilityDecisionEvidence(
                        decision_ref=decision_ref,
                        user_ref=user_ref,
                        facts_version=1,
                        verification_decision_ref=verification_ref,
                        classification_decision_ref=classification_ref,
                        policy_version="v1",
                        facts_digest="e" * 64,
                        p1_projection_digest="f" * 64,
                        decision=EligibilityDecision.ELIGIBLE,
                        reason=decision_reason,
                        decided_at=NOW,
                    )
                )
                await session.commit()

            async with factory() as session:
                try:
                    await SqlAlchemyEligibilityDecisionEvidenceReader(
                        session
                    ).get_current_eligible(
                        decision_ref=decision_ref,
                        user_ref=user_ref,
                    )
                except RegistrationEligibilityEvidenceInconsistent:
                    pass
                else:
                    pytest.fail(
                        "Untrusted eligibility semantics must fail closed"
                    )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
