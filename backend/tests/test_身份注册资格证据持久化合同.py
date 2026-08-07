import asyncio
import traceback
from datetime import datetime, timezone
from importlib.util import find_spec
from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.auth.eligibility_evidence import (
    IdentityVerificationEvidence,
    InvalidRegistrationEligibilityEvidence,
    RegistrationEligibilityDecisionEvidence,
    RegistrationEligibilityDecisionService,
    RegistrationEligibilityEvidenceConflict,
    RegistrationEligibilityFactsSnapshot,
    RegistrationEligibilityEvidenceInconsistent,
    RegistrationEligibilityEvidenceUnavailable,
    UserAccountClassificationEvidence,
)
from app.modules.auth.eligibility_evidence_repository import (
    SqlAlchemyRegistrationEligibilityEvidenceStore,
)
from app.modules.member.application.registration_eligibility import (
    AccountClass,
    EligibilityDecision,
    EligibilityReason,
    RegistrationAccountStatus,
    RegistrationUserRole,
    TrustedRegistrationEligibilityFacts,
    VerificationOutcome,
    VerificationStatus,
)


def test_注册资格证据持久化尚未实现():
    modules = (
        "app.modules.auth.eligibility_evidence",
        "app.modules.auth.eligibility_evidence_models",
        "app.modules.auth.eligibility_evidence_repository",
    )
    assert all(find_spec(module) is not None for module in modules), (
        "Registration eligibility evidence persistence is not implemented"
    )


VERIFICATION_REF = UUID("01890f3e-7b7d-7cc3-98c8-2f5a12d21234")
CLASSIFICATION_REF = UUID("01890f3e-7b7d-7cc3-a8c8-2f5a12d25678")
DECISION_REF = UUID("01890f3e-7b7d-7cc3-b8c8-2f5a12d29876")
NOW = datetime(2026, 8, 7, 0, 0, tzinfo=timezone.utc)
P1_PROJECTION_DIGEST = "f" * 64


def _snapshot(facts):
    return RegistrationEligibilityFactsSnapshot(
        facts=facts,
        p1_projection_digest=P1_PROJECTION_DIGEST,
    )


def test_证据对象严格绑定主体版本与不可变引用():
    verification = IdentityVerificationEvidence(
        decision_ref=VERIFICATION_REF,
        user_ref=7,
        facts_version=3,
        verification_epoch=2,
        outcome=VerificationOutcome.VERIFIED,
        evidence_digest="a" * 64,
        actor_type="trusted_provider",
        actor_ref="provider-1",
        decided_at=NOW,
    )
    classification = UserAccountClassificationEvidence(
        decision_ref=CLASSIFICATION_REF,
        user_ref=7,
        facts_version=3,
        classification_version=2,
        account_class=AccountClass.NATURAL_PERSON,
        decision_basis_code="trusted_provisioning",
        decided_at=NOW,
    )

    assert verification.user_ref == classification.user_ref == 7
    assert verification.facts_version == classification.facts_version == 3
    with pytest.raises(InvalidRegistrationEligibilityEvidence):
        IdentityVerificationEvidence(
            decision_ref=VERIFICATION_REF,
            user_ref=True,
            facts_version=3,
            verification_epoch=2,
            outcome=VerificationOutcome.VERIFIED,
            evidence_digest="a" * 64,
            actor_type="trusted_provider",
            actor_ref="provider-1",
            decided_at=NOW,
        )


class _FactsReader:
    async def get_current(self, user_ref):
        assert user_ref == 7
        return _snapshot(TrustedRegistrationEligibilityFacts(
            user_ref=7,
            exists=True,
            facts_version=3,
            role=RegistrationUserRole.MEMBER,
            status=RegistrationAccountStatus.ACTIVE,
            verify_status=VerificationStatus.VERIFIED,
            verification_decision_ref=VERIFICATION_REF,
            verification_subject_user_ref=7,
            verification_outcome=VerificationOutcome.VERIFIED,
            verification_epoch=2,
            verification_is_current=True,
            account_class=AccountClass.NATURAL_PERSON,
            classification_decision_ref=CLASSIFICATION_REF,
            classification_subject_user_ref=7,
            classification_version=2,
            classification_is_current=True,
        ))


class _Store:
    def __init__(self):
        self.decision = None
        self.adds = 0

    async def get_decision_by_key(self, **key):
        assert key == {
            "user_ref": 7,
            "facts_version": 3,
            "policy_version": "v1",
        }
        return self.decision

    async def add_decision(self, evidence):
        self.adds += 1
        self.decision = evidence


class _UuidGenerator:
    def generate(self):
        return DECISION_REF


def test_资格决定以canonical_key稳定重放且不复制PII():
    async def scenario():
        store = _Store()
        service = RegistrationEligibilityDecisionService(
            facts_reader=_FactsReader(),
            evidence_store=store,
            uuid_generator=_UuidGenerator(),
            clock=lambda: NOW,
        )
        first = await service.decide(user_ref=7, policy_version="v1")
        second = await service.decide(user_ref=7, policy_version="v1")

        assert first is second
        assert first.decision is EligibilityDecision.ELIGIBLE
        assert first.verification_decision_ref == VERIFICATION_REF
        assert first.classification_decision_ref == CLASSIFICATION_REF
        assert store.adds == 1
        assert len(first.facts_digest) == 64
        assert not hasattr(first, "phone")
        assert not hasattr(first, "id_card")

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("field_name", "stale_value"),
    (
        ("p1_projection_digest", "e" * 64),
        (
            "verification_decision_ref",
            UUID("01890f3e-7b7d-7cc3-88c8-2f5a12d21235"),
        ),
        (
            "classification_decision_ref",
            UUID("01890f3e-7b7d-7cc3-88c8-2f5a12d25679"),
        ),
        ("decision", EligibilityDecision.INELIGIBLE),
        ("reason", EligibilityReason.ACCOUNT_NOT_ACTIVE),
    ),
)
def test_canonical_key命中但资格快照不一致必须fail_closed(
    field_name, stale_value
):
    from dataclasses import replace

    current = RegistrationEligibilityDecisionEvidence(
        decision_ref=DECISION_REF,
        user_ref=7,
        facts_version=3,
        verification_decision_ref=VERIFICATION_REF,
        classification_decision_ref=CLASSIFICATION_REF,
        policy_version="v1",
        facts_digest="b" * 64,
        p1_projection_digest=P1_PROJECTION_DIGEST,
        decision=EligibilityDecision.ELIGIBLE,
        reason=EligibilityReason.ELIGIBLE,
        decided_at=NOW,
    )
    stale = replace(current, **{field_name: stale_value})

    class ReplayStore:
        def __init__(self):
            self.lookups = 0
            self.adds = 0

        async def get_decision_by_key(self, **key):
            self.lookups += 1
            return stale

        async def add_decision(self, evidence):
            self.adds += 1

    class ZeroCallUuidGenerator:
        calls = 0

        def generate(self):
            self.calls += 1
            return DECISION_REF

    class ZeroCallClock:
        calls = 0

        def __call__(self):
            self.calls += 1
            return NOW

    async def scenario():
        store = ReplayStore()
        uuid_generator = ZeroCallUuidGenerator()
        clock = ZeroCallClock()
        service = RegistrationEligibilityDecisionService(
            facts_reader=_FactsReader(),
            evidence_store=store,
            uuid_generator=uuid_generator,
            clock=clock,
        )
        try:
            await service.decide(user_ref=7, policy_version="v1")
        except RegistrationEligibilityEvidenceInconsistent:
            pass
        else:
            pytest.fail(
                "Stale canonical evidence must fail closed instead of replaying"
            )
        assert store.lookups == 1
        assert store.adds == 0
        assert uuid_generator.calls == 0
        assert clock.calls == 0

    asyncio.run(scenario())


def test_canonical_evidence全部一致时稳定重放且零副作用():
    current = RegistrationEligibilityDecisionEvidence(
        decision_ref=DECISION_REF,
        user_ref=7,
        facts_version=3,
        verification_decision_ref=VERIFICATION_REF,
        classification_decision_ref=CLASSIFICATION_REF,
        policy_version="v1",
        facts_digest="b" * 64,
        p1_projection_digest=P1_PROJECTION_DIGEST,
        decision=EligibilityDecision.ELIGIBLE,
        reason=EligibilityReason.ELIGIBLE,
        decided_at=NOW,
    )

    class ReplayStore:
        adds = 0

        async def get_decision_by_key(self, **key):
            return current

        async def add_decision(self, evidence):
            self.adds += 1

    class ForbiddenCollaborator:
        calls = 0

        def __call__(self):
            self.calls += 1
            pytest.fail("Stable replay must not call UUID generator or clock")

        def generate(self):
            return self()

    async def scenario():
        store = ReplayStore()
        uuid_generator = ForbiddenCollaborator()
        clock = ForbiddenCollaborator()
        result = await RegistrationEligibilityDecisionService(
            facts_reader=_FactsReader(),
            evidence_store=store,
            uuid_generator=uuid_generator,
            clock=clock,
        ).decide(user_ref=7, policy_version="v1")
        assert result is current
        assert store.adds == 0
        assert uuid_generator.calls == 0
        assert clock.calls == 0

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("verification_subject", "classification_subject"),
    ((8, 7), (7, 8), (8, 9)),
)
def test_内层主体错绑必须零副作用fail_closed(
    verification_subject, classification_subject
):
    class InnerMismatchFactsReader:
        async def get_current(self, user_ref):
            assert user_ref == 7
            return _snapshot(TrustedRegistrationEligibilityFacts(
                user_ref=7,
                exists=True,
                facts_version=3,
                role=RegistrationUserRole.MEMBER,
                status=RegistrationAccountStatus.ACTIVE,
                verify_status=VerificationStatus.VERIFIED,
                verification_decision_ref=VERIFICATION_REF,
                verification_subject_user_ref=verification_subject,
                verification_outcome=VerificationOutcome.VERIFIED,
                verification_epoch=2,
                verification_is_current=True,
                account_class=AccountClass.NATURAL_PERSON,
                classification_decision_ref=CLASSIFICATION_REF,
                classification_subject_user_ref=classification_subject,
                classification_version=2,
                classification_is_current=True,
            ))

    class ZeroCallStore:
        calls = 0

        async def get_decision_by_key(self, **key):
            self.calls += 1
            return None

        async def add_decision(self, evidence):
            self.calls += 1

    class ZeroCallUuidGenerator:
        calls = 0

        def generate(self):
            self.calls += 1
            return DECISION_REF

    class ZeroCallClock:
        calls = 0

        def __call__(self):
            self.calls += 1
            return NOW

    async def scenario():
        store = ZeroCallStore()
        uuid_generator = ZeroCallUuidGenerator()
        clock = ZeroCallClock()
        service = RegistrationEligibilityDecisionService(
            facts_reader=InnerMismatchFactsReader(),
            evidence_store=store,
            uuid_generator=uuid_generator,
            clock=clock,
        )
        try:
            await service.decide(user_ref=7, policy_version="v1")
        except RegistrationEligibilityEvidenceInconsistent:
            pass
        else:
            pytest.fail(
                "Nested subject mismatch must fail closed before evidence"
            )
        assert store.calls == 0
        assert uuid_generator.calls == 0
        assert clock.calls == 0

    asyncio.run(scenario())


def test_跨主体facts绑定必须在持久化前fail_closed():
    class CrossSubjectFactsReader:
        async def get_current(self, user_ref):
            assert user_ref == 7
            return _snapshot(TrustedRegistrationEligibilityFacts(
                user_ref=8,
                exists=True,
                facts_version=3,
                role=RegistrationUserRole.MEMBER,
                status=RegistrationAccountStatus.ACTIVE,
                verify_status=VerificationStatus.VERIFIED,
                verification_decision_ref=VERIFICATION_REF,
                verification_subject_user_ref=8,
                verification_outcome=VerificationOutcome.VERIFIED,
                verification_epoch=2,
                verification_is_current=True,
                account_class=AccountClass.NATURAL_PERSON,
                classification_decision_ref=CLASSIFICATION_REF,
                classification_subject_user_ref=8,
                classification_version=2,
                classification_is_current=True,
            ))

    class RejectWritesStore:
        calls = 0

        async def get_decision_by_key(self, **key):
            self.calls += 1
            return None

        async def add_decision(self, evidence):
            self.calls += 1

    async def scenario():
        store = RejectWritesStore()
        service = RegistrationEligibilityDecisionService(
            facts_reader=CrossSubjectFactsReader(),
            evidence_store=store,
            uuid_generator=_UuidGenerator(),
            clock=lambda: NOW,
        )
        try:
            await service.decide(user_ref=7, policy_version="v1")
        except RegistrationEligibilityEvidenceInconsistent:
            pass
        else:
            pytest.fail(
                "Cross-subject facts must fail closed before persistence"
            )
        assert store.calls == 0

    asyncio.run(scenario())


def test_未知IntegrityError只能映射generic():
    class DriverError(Exception):
        def __init__(
            self,
            message,
            *,
            sqlstate=None,
            constraint_name=None,
            category=None,
        ):
            super().__init__(message)
            self.sqlstate = sqlstate
            self.constraint_name = constraint_name
            self.category = category

    class FailingSession:
        def __init__(self, original):
            self.original = original

        def add(self, model):
            self.model = model

        async def flush(self):
            raise IntegrityError(
                "INSERT INTO secret_table VALUES (:phone)",
                {"phone": "13900000000", "password": "credential"},
                self.original,
            )

    evidence = RegistrationEligibilityDecisionEvidence(
        decision_ref=DECISION_REF,
        user_ref=7,
        facts_version=3,
        verification_decision_ref=VERIFICATION_REF,
        classification_decision_ref=CLASSIFICATION_REF,
        policy_version="v1",
        facts_digest="b" * 64,
        p1_projection_digest=P1_PROJECTION_DIGEST,
        decision=EligibilityDecision.ELIGIBLE,
        reason=EligibilityReason.ELIGIBLE,
        decided_at=NOW,
    )

    async def add_with(original):
        store = SqlAlchemyRegistrationEligibilityEvidenceStore(
            FailingSession(original)
        )
        await store.add_decision(evidence)

    async def scenario():
        known_constraints = (
            "uq_identity_verification_user_epoch",
            "uq_identity_verification_user_facts",
            "uq_identity_verification_single_successor",
            "uq_account_classification_user_version",
            "uq_account_classification_user_facts",
            "uq_account_classification_single_successor",
            "uq_registration_eligibility_canonical_key",
        )
        for constraint_name in known_constraints:
            try:
                await add_with(
                    DriverError(
                        "trusted unique violation",
                        sqlstate="23505",
                        constraint_name=constraint_name,
                    )
                )
            except RegistrationEligibilityEvidenceConflict:
                pass
            else:
                pytest.fail(
                    "Known unique constraints must map to conflict"
                )

        negative_cases = (
            DriverError(
                "wrong sqlstate",
                sqlstate="23514",
                constraint_name="uq_registration_eligibility_canonical_key",
            ),
            DriverError("missing constraint", sqlstate="23505"),
            DriverError(
                "unknown constraint",
                sqlstate="23505",
                constraint_name="uq_unknown",
            ),
            DriverError(
                "forged category",
                sqlstate="23514",
                constraint_name="ck_unknown_integrity",
                category="unique",
            ),
            DriverError(
                "23505 uq_registration_eligibility_canonical_key"
            ),
        )
        for original in negative_cases:
            try:
                await add_with(original)
            except RegistrationEligibilityEvidenceUnavailable as exc:
                assert str(exc) == (
                    "eligibility evidence persistence is unavailable"
                )
                public_surface = "\n".join(
                    (
                        str(exc),
                        repr(exc),
                        repr(exc.__cause__),
                        repr(exc.__context__),
                        "".join(
                            traceback.format_exception(
                                type(exc), exc, exc.__traceback__
                            )
                        ),
                    )
                ).lower()
                forbidden = (
                    "insert into secret_table",
                    "params",
                    "13900000000",
                    "password",
                    "credential",
                    "postgresql+asyncpg://",
                    "phone",
                )
                if (
                    exc.__cause__ is not None
                    or exc.__context__ is not None
                    or any(value in public_surface for value in forbidden)
                ):
                    pytest.fail(
                        "Generic IntegrityError must sever unsafe vendor exception chain"
                    )
            except RegistrationEligibilityEvidenceConflict:
                pytest.fail("Unknown IntegrityError must remain generic")
            else:
                pytest.fail("Unknown IntegrityError must remain generic")

        wrapper = DriverError("wrapper")
        wrapper.__cause__ = DriverError(
            "supported driver cause",
            sqlstate="23505",
            constraint_name="uq_registration_eligibility_canonical_key",
        )
        try:
            await add_with(wrapper)
        except RegistrationEligibilityEvidenceConflict:
            pass
        else:
            pytest.fail("Supported driver cause must map known conflict")

    asyncio.run(scenario())


def _assert_public_exception_chain_is_safe(exc, expected_message):
    assert str(exc) == expected_message
    public_surface = "\n".join(
        (
            str(exc),
            repr(exc),
            repr(exc.__cause__),
            repr(exc.__context__),
            "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ),
        )
    ).lower()
    forbidden = (
        "select secret_phone",
        "insert into secret_table",
        "13900000000",
        "password",
        "credential",
        "postgresql+asyncpg://",
        "params",
    )
    if (
        exc.__cause__ is not None
        or exc.__context__ is not None
        or any(value in public_surface for value in forbidden)
    ):
        pytest.fail(
            "All persistence boundaries must sever unsafe vendor exception chains"
        )


@pytest.mark.parametrize(
    "boundary",
    (
        "decision_lookup",
        "verification_lookup",
        "classification_lookup",
        "facts_reader",
        "proof_reader",
        "known_conflict",
    ),
)
def test_全部持久化边界必须切断供应商异常链(boundary):
    from app.modules.auth.eligibility_evidence_repository import (
        SqlAlchemyEligibilityDecisionEvidenceReader,
        SqlAlchemyRegistrationEligibilityFactsReader,
    )

    class DriverError(Exception):
        sqlstate = "23505"
        constraint_name = "uq_registration_eligibility_canonical_key"

    class SensitiveSession:
        def add(self, model):
            self.model = model

        async def flush(self):
            raise IntegrityError(
                "INSERT INTO secret_table VALUES (:phone)",
                {"phone": "13900000000", "password": "credential"},
                DriverError("credential postgresql+asyncpg://private-target"),
            )

        async def execute(self, statement):
            raise RuntimeError(
                "SELECT secret_phone params=13900000000 "
                "password=credential postgresql+asyncpg://private-target"
            )

    evidence = RegistrationEligibilityDecisionEvidence(
        decision_ref=DECISION_REF,
        user_ref=7,
        facts_version=3,
        verification_decision_ref=VERIFICATION_REF,
        classification_decision_ref=CLASSIFICATION_REF,
        policy_version="v1",
        facts_digest="b" * 64,
        p1_projection_digest=P1_PROJECTION_DIGEST,
        decision=EligibilityDecision.ELIGIBLE,
        reason=EligibilityReason.ELIGIBLE,
        decided_at=NOW,
    )

    async def scenario():
        session = SensitiveSession()
        if boundary == "decision_lookup":
            operation = SqlAlchemyRegistrationEligibilityEvidenceStore(
                session
            ).get_decision_by_key(
                user_ref=7, facts_version=3, policy_version="v1"
            )
            expected_type = RegistrationEligibilityEvidenceUnavailable
            expected_message = "eligibility evidence persistence is unavailable"
        elif boundary == "verification_lookup":
            operation = SqlAlchemyRegistrationEligibilityEvidenceStore(
                session
            )._latest_verification(7)
            expected_type = RegistrationEligibilityEvidenceUnavailable
            expected_message = "eligibility evidence persistence is unavailable"
        elif boundary == "classification_lookup":
            operation = SqlAlchemyRegistrationEligibilityEvidenceStore(
                session
            )._latest_classification(7)
            expected_type = RegistrationEligibilityEvidenceUnavailable
            expected_message = "eligibility evidence persistence is unavailable"
        elif boundary == "facts_reader":
            operation = SqlAlchemyRegistrationEligibilityFactsReader(
                session
            ).get_current(7)
            expected_type = RegistrationEligibilityEvidenceUnavailable
            expected_message = "registration eligibility facts are unavailable"
        elif boundary == "proof_reader":
            operation = SqlAlchemyEligibilityDecisionEvidenceReader(
                session
            ).get_current_eligible(decision_ref=DECISION_REF, user_ref=7)
            expected_type = RegistrationEligibilityEvidenceUnavailable
            expected_message = "eligibility decision evidence is unavailable"
        else:
            operation = SqlAlchemyRegistrationEligibilityEvidenceStore(
                session
            ).add_decision(evidence)
            expected_type = RegistrationEligibilityEvidenceConflict
            expected_message = "eligibility evidence conflicts with persisted state"

        try:
            await operation
        except expected_type as exc:
            _assert_public_exception_chain_is_safe(exc, expected_message)
        else:
            pytest.fail(
                "Persistence boundary must return a safe public exception"
            )

    asyncio.run(scenario())


def test_资格Evidence必须绑定P1投影摘要():
    from dataclasses import fields

    from app.modules.auth.eligibility_evidence_models import (
        RegistrationEligibilityDecisionEvidenceOrmModel,
    )

    domain_fields = {
        field.name for field in fields(RegistrationEligibilityDecisionEvidence)
    }
    orm_columns = {
        column.name
        for column in RegistrationEligibilityDecisionEvidenceOrmModel.__table__.columns
    }
    assert "p1_projection_digest" in domain_fields, (
        "Eligibility evidence must bind the P1 projection digest"
    )
    assert "p1_projection_digest" in orm_columns, (
        "Eligibility evidence ORM must persist the P1 projection digest"
    )
    column = RegistrationEligibilityDecisionEvidenceOrmModel.__table__.c[
        "p1_projection_digest"
    ]
    assert column.nullable is False
    assert column.type.length == 64
    constraint_names = {
        constraint.name
        for constraint in RegistrationEligibilityDecisionEvidenceOrmModel.__table__.constraints
    }
    assert any(
        name.endswith(
            "registration_eligibility_p1_projection_digest_sha256"
        )
        for name in constraint_names
    )


def test_P1投影摘要稳定且任一权威字段或版本变化都会改变():
    from datetime import timedelta

    from app.modules.auth.eligibility_evidence import (
        build_p1_projection_digest,
    )

    baseline = {
        "user_ref": 7,
        "role": "member",
        "status": "active",
        "verify_status": "verified",
        "updated_at": NOW,
    }
    first = build_p1_projection_digest(**baseline)
    assert first == build_p1_projection_digest(**baseline)
    assert len(first) == 64
    mutations = (
        {"role": "therapist"},
        {"status": "disabled"},
        {"verify_status": "failed"},
        {"updated_at": NOW + timedelta(microseconds=1)},
    )
    for mutation in mutations:
        candidate = {**baseline, **mutation}
        assert build_p1_projection_digest(**candidate) != first
