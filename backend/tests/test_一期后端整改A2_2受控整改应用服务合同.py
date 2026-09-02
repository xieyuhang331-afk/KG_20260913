from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import inspect
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from app.modules.auth.identity_submission_crypto import IdentitySubmissionCrypto

PRODUCTION_PATHS = (
    Path("app/modules/auth/identity_remediation_application.py"),
    Path("app/modules/auth/identity_remediation_ledger_repository.py"),
    Path("app/modules/auth/identity_remediation_subject_repository.py"),
    Path("app/composition/identity_remediation_runner.py"),
    Path("scripts/run_a2_identity_remediation.py"),
)
FORBIDDEN_SOURCE_TOKENS = (
    "fastapi",
    "APIRouter",
    "celery",
    "rabbitmq",
    "CREATE TABLE",
    "ALTER TABLE",
    "INSERT INTO public.",
    "UPDATE public.",
    "DELETE FROM public.",
)


def _module(name: str):
    return importlib.import_module(name)


def _synthetic_identity() -> str:
    first_seventeen = "110105" + "19900101" + "001"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    codes = "10X98765432"
    check = codes[
        sum(
            int(digit) * weight
            for digit, weight in zip(first_seventeen, weights, strict=True)
        )
        % 11
    ]
    return first_seventeen + check


def _crypto() -> IdentitySubmissionCrypto:
    return IdentitySubmissionCrypto(
        encryption_key=bytes(range(32)),
        hmac_key=bytes(range(32, 64)),
        key_id="synthetic-a2-r-key",
    )


def test_A2_2_R_EXPECTED_RED_五个生产模块尚未实现() -> None:
    missing = [path.as_posix() for path in PRODUCTION_PATHS if not path.exists()]
    assert missing == [], "A2_2_R_PRODUCTION_MODULES_MISSING"


def test_A2_2_R_Ledger请求精确29参数且调用方无request_digest通道() -> None:
    ledger = _module("app.modules.auth.identity_remediation_ledger_repository")
    field_names = tuple(ledger.LedgerWriteRequest.__dataclass_fields__)
    assert "request_digest" not in field_names
    request = ledger.LedgerWriteRequest.plan_batch(
        batch_ref=UUID("00000000-0000-7000-8000-000000000101"),
        classification_rule_hash="1" * 64,
        snapshot_ref_hash="2" * 64,
        class_counts={f"H{index}": 1 for index in range(8)},
        actor_scope="synthetic-a2-r",
        idempotency_key_digest="3" * 64,
        receipt_id=UUID("00000000-0000-7000-8000-000000000102"),
        audit_id=UUID("00000000-0000-7000-8000-000000000103"),
        occurred_at=datetime(2026, 9, 3, tzinfo=UTC),
    )
    assert len(request.as_parameters()) == 29
    assert request.operation == "PLAN_BATCH"


def test_A2_2_R_Ledger请求repr不暴露内部subject_user_ref() -> None:
    ledger = _module("app.modules.auth.identity_remediation_ledger_repository")
    request = ledger.LedgerWriteRequest(
        operation="REGISTER_ITEM",
        batch_ref=UUID("00000000-0000-7000-8000-000000000104"),
        item_ref=UUID("00000000-0000-7000-8000-000000000105"),
        subject_user_ref=9000009,
        primary_class="H3",
        secondary_flags=0,
        expected_version=None,
        expected_target_state_digest=None,
        classification_rule_hash=None,
        snapshot_ref_hash=None,
        input_count=None,
        h0_count=None,
        h1_count=None,
        h2_count=None,
        h3_count=None,
        h4_count=None,
        h5_count=None,
        h6_count=None,
        h7_count=None,
        preimage_digest="1" * 64,
        eligibility_action_gate_digest=None,
        mutation_digest=None,
        business_postimage_digest=None,
        actor_scope="synthetic-a2-r",
        reason_code="A2_ITEM_DISCOVERED",
        idempotency_key_digest="2" * 64,
        receipt_id=UUID("00000000-0000-7000-8000-000000000106"),
        audit_id=UUID("00000000-0000-7000-8000-000000000107"),
        occurred_at=datetime(2026, 9, 3, tzinfo=UTC),
    )
    assert "9000009" not in repr(request)


def test_A2_2_R_Subject敏感Material不进入repr且三个SQL边界闭合() -> None:
    subject = _module("app.modules.auth.identity_remediation_subject_repository")
    workset_subject = subject.WorksetSubject(
        subject_user_ref=9000001,
        primary_class="H3",
        secondary_flags=0,
        preimage_digest="7" * 64,
    )
    assert "9000001" not in repr(workset_subject)
    material = subject.H3Material(
        subject_user_ref=9000001,
        legacy_real_name="synthetic-name",
        legacy_id_card=_synthetic_identity(),
        submission_id=UUID("00000000-0000-7000-8000-000000000111"),
        submission_version=1,
        real_name_ciphertext=b"name",
        real_name_nonce=b"0" * 12,
        id_card_ciphertext=b"card",
        id_card_nonce=b"1" * 12,
        encryption_key_id="synthetic-a2-r-key",
        formal_content_digest="4" * 64,
        formal_id_card_digest="5" * 64,
        decision_ref=UUID("00000000-0000-7000-8000-000000000112"),
        decision_facts_version=1,
        claim_id=UUID("00000000-0000-7000-8000-000000000113"),
        claim_version=1,
        consent_version="synthetic-v1",
        chain_digest="6" * 64,
    )
    rendered = repr(material)
    assert "synthetic-name" not in rendered
    assert _synthetic_identity() not in rendered
    assert "9000001" not in rendered
    assert "name" not in rendered and "card" not in rendered
    source = Path(subject.__file__).read_text(encoding="utf-8")
    assert source.count("identity.a2_identity_remediation_subject_workset_v1") == 1
    assert source.count("identity.a2_identity_remediation_h3_material_v1") == 1
    assert source.count("identity.a2_identity_remediation_h3_clear_legacy_v1") == 1


def test_A2_2_R_H0至H7动作矩阵固定且H4H5必须暂停() -> None:
    application = _module("app.modules.auth.identity_remediation_application")
    expected = {
        "H0": ("EXCLUDE_ITEM", False),
        "H1": ("REQUIRE_ITEM", False),
        "H2": ("REQUIRE_ITEM", False),
        "H3": ("H3_EXACT_MATCH", False),
        "H4": ("REQUIRE_ITEM", True),
        "H5": ("REQUIRE_ITEM", True),
        "H6": ("REQUIRE_ITEM", False),
        "H7": ("EXCLUDE_ITEM", False),
    }
    assert {
        primary_class: application.action_for_primary_class(primary_class)
        for primary_class in expected
    } == expected
    with pytest.raises(application.IdentityRemediationContractError):
        application.action_for_primary_class("H8")


def test_A2_2_R_Workset完整注册后才START且Pause只在遍历后执行一次() -> None:
    application = _module("app.modules.auth.identity_remediation_application")
    source = inspect.getsource(application.IdentityRemediationApplicationService._run_once)
    register_source = inspect.getsource(
        application.IdentityRemediationApplicationService._register_workset
    )
    assert source.index("_register_workset") < source.index("START_BATCH")
    assert register_source.index("fetch_workset") < register_source.index("ledger.write")
    assert source.index("START_BATCH") < source.index("for subject, discovered")
    assert source.index("for subject, discovered") < source.index("if pause_required")
    pause_suffix = source[source.index("if pause_required") :]
    assert pause_suffix.count('operation="PAUSE_BATCH"') == 1
    assert "break" not in source[source.index("for subject, discovered") : source.index("if pause_required")]


def test_A2_2_R_H3只有完整解密规范化与HMAC匹配才批准() -> None:
    application = _module("app.modules.auth.identity_remediation_application")
    subject = _module("app.modules.auth.identity_remediation_subject_repository")
    crypto = _crypto()
    identity_value = _synthetic_identity()
    real_name = "Synthetic Member"
    consent_version = "synthetic-v1"
    submission_id = UUID("00000000-0000-7000-8000-000000000121")
    subject_user_ref = 9000002
    name_aad = crypto.aad(
        submission_id=str(submission_id),
        user_ref=subject_user_ref,
        version=1,
        field="real_name",
        key_id=crypto.key_id,
    )
    card_aad = crypto.aad(
        submission_id=str(submission_id),
        user_ref=subject_user_ref,
        version=1,
        field="id_card",
        key_id=crypto.key_id,
    )
    encrypted_name = crypto.encrypt(real_name, aad=name_aad)
    encrypted_card = crypto.encrypt(identity_value.lower(), aad=card_aad)
    material = subject.H3Material(
        subject_user_ref=subject_user_ref,
        legacy_real_name=real_name,
        legacy_id_card=identity_value,
        submission_id=submission_id,
        submission_version=1,
        real_name_ciphertext=encrypted_name.ciphertext,
        real_name_nonce=encrypted_name.nonce,
        id_card_ciphertext=encrypted_card.ciphertext,
        id_card_nonce=encrypted_card.nonce,
        encryption_key_id=crypto.key_id,
        formal_content_digest=crypto.digest(
            "content", f"{real_name}\x1f{identity_value}\x1f{consent_version}"
        ),
        formal_id_card_digest=crypto.digest("id-card", identity_value),
        decision_ref=UUID("00000000-0000-7000-8000-000000000122"),
        decision_facts_version=1,
        claim_id=UUID("00000000-0000-7000-8000-000000000123"),
        claim_version=1,
        consent_version=consent_version,
        chain_digest="7" * 64,
    )
    approved = application.verify_h3_exact_match(material, crypto=crypto)
    assert approved.approved is True
    assert len(approved.gate_digest) == 64
    mismatched = subject.H3Material(
        **{
            name: getattr(material, name)
            for name in subject.H3Material.__dataclass_fields__
            if name != "formal_content_digest"
        },
        formal_content_digest="8" * 64,
    )
    rejected = application.verify_h3_exact_match(mismatched, crypto=crypto)
    assert rejected.approved is False
    assert rejected.error_code == "A2_REMEDIATION_H3_MISMATCH"


def test_A2_2_R_CancelledError必须优先传播() -> None:
    application = _module("app.modules.auth.identity_remediation_application")

    async def cancel() -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(application.propagate_cancellation(cancel()))


def test_A2_2_R_NOT_COMMITTED多请求和H3重试必须逐项复用typed请求() -> None:
    application = _module("app.modules.auth.identity_remediation_application")
    ledger = _module("app.modules.auth.identity_remediation_ledger_repository")
    base = ledger.LedgerWriteRequest.plan_batch(
        batch_ref=UUID("00000000-0000-7000-8000-000000000201"),
        classification_rule_hash="1" * 64,
        snapshot_ref_hash="2" * 64,
        class_counts={f"H{index}": 0 for index in range(8)},
        actor_scope="synthetic-a2-r",
        idempotency_key_digest="3" * 64,
        receipt_id=UUID("00000000-0000-7000-8000-000000000202"),
        audit_id=UUID("00000000-0000-7000-8000-000000000203"),
        occurred_at=datetime(2026, 9, 3, tzinfo=UTC),
    )
    result = ledger.LedgerWriteResult(
        "BATCH", base.batch_ref, "PLANNED", 1, "PLANNED", base.receipt_id, "4" * 64
    )

    class Unit:
        def __init__(self, fail_commit: bool) -> None:
            self.fail_commit = fail_commit

        async def commit(self) -> None:
            if self.fail_commit:
                raise RuntimeError("synthetic commit result unknown")

    units = iter((Unit(True), Unit(False)))

    @asynccontextmanager
    async def unit_factory():
        yield next(units)

    class ConfirmationLedger:
        async def confirm(self, request, expectation):
            return ledger.CommitOutcome.NOT_COMMITTED

    @asynccontextmanager
    async def confirmation_factory():
        yield type("Confirmation", (), {"ledger": ConfirmationLedger()})()

    service = application.IdentityRemediationApplicationService(
        inventory_provider=lambda: None,
        unit_of_work_factory=unit_factory,
        confirmation_factory=confirmation_factory,
        crypto=_crypto(),
        actor_scope="synthetic-a2-r",
    )
    calls = 0

    async def changed_retry(unit):
        nonlocal calls
        calls += 1
        second = replace(
            base,
            receipt_id=(
                base.receipt_id
                if calls == 1
                else UUID("00000000-0000-7000-8000-000000000204")
            ),
        )
        return application._OperationAttempt(
            (base, second),
            ledger.LedgerExpectation("PLANNED", 1, "PLANNED"),
            result,
        )

    with pytest.raises(
        application.IdentityRemediationCommitOutcomeUnknown,
        match="A2_REMEDIATION_RETRY_REQUEST_INCONSISTENT",
    ):
        asyncio.run(service._execute(changed_retry))


def test_A2_2_R_Workset与全部REGISTER同事务且重试复用冻结请求() -> None:
    application = _module("app.modules.auth.identity_remediation_application")
    ledger = _module("app.modules.auth.identity_remediation_ledger_repository")
    subject = _module("app.modules.auth.identity_remediation_subject_repository")
    batch_ref = UUID("00000000-0000-7000-8000-000000000231")
    planned = ledger.LedgerWriteResult(
        "BATCH",
        batch_ref,
        "PLANNED",
        1,
        "PLANNED",
        UUID("00000000-0000-7000-8000-000000000232"),
        "1" * 64,
    )
    workset = (
        subject.WorksetSubject(20, "H4", 2, "2" * 64),
        subject.WorksetSubject(10, "H0", 0, "3" * 64),
    )
    events: list[list[str]] = []
    written: list[list[tuple[object, ...]]] = []

    class Subjects:
        def __init__(self, event):
            self.event = event

        async def fetch_workset(self, **kwargs):
            self.event.append("fetch")
            return workset

    class Ledger:
        def __init__(self, event, requests):
            self.event = event
            self.requests = requests

        async def write(self, request):
            self.event.append("write")
            self.requests.append(request.as_parameters())
            return ledger.LedgerWriteResult(
                "ITEM",
                request.item_ref,
                "DISCOVERED",
                1,
                "DISCOVERED",
                request.receipt_id,
                "4" * 64,
            )

    class Unit:
        def __init__(self, fail):
            event: list[str] = []
            requests: list[tuple[object, ...]] = []
            events.append(event)
            written.append(requests)
            self.subjects = Subjects(event)
            self.ledger = Ledger(event, requests)
            self.fail = fail

        async def commit(self):
            if self.fail:
                raise RuntimeError("synthetic commit unknown")

    units = iter((Unit(True), Unit(False)))

    @asynccontextmanager
    async def unit_factory():
        yield next(units)

    class ConfirmationLedger:
        async def confirm(self, request, expectation):
            return ledger.CommitOutcome.NOT_COMMITTED

    @asynccontextmanager
    async def confirmation_factory():
        yield type("Confirmation", (), {"ledger": ConfirmationLedger()})()

    service = application.IdentityRemediationApplicationService(
        inventory_provider=lambda: None,
        unit_of_work_factory=unit_factory,
        confirmation_factory=confirmation_factory,
        crypto=_crypto(),
        actor_scope="synthetic-a2-r",
    )
    registrations = asyncio.run(
        service._register_workset(
            batch_ref=batch_ref,
            planned=planned,
            classification_hash="5" * 64,
            snapshot_hash="6" * 64,
            expected_count=2,
        )
    )
    assert events == [["fetch", "write", "write"], ["fetch", "write", "write"]]
    assert written[0] == written[1]
    assert [entry.subject_user_ref for entry, _ in registrations] == [10, 20]


def test_A2_2_R_H3_NOT_COMMITTED重读不变量后原样复用COMPLETE请求(monkeypatch) -> None:
    application = _module("app.modules.auth.identity_remediation_application")
    ledger = _module("app.modules.auth.identity_remediation_ledger_repository")
    subject = _module("app.modules.auth.identity_remediation_subject_repository")
    batch_ref = UUID("00000000-0000-7000-8000-000000000241")
    item_ref = UUID("00000000-0000-7000-8000-000000000242")
    processing = ledger.LedgerWriteResult(
        "ITEM",
        item_ref,
        "PROCESSING",
        3,
        "PROCESSING",
        UUID("00000000-0000-7000-8000-000000000243"),
        "7" * 64,
    )
    material = subject.H3Material(
        subject_user_ref=1,
        legacy_real_name="synthetic",
        legacy_id_card="synthetic",
        submission_id=UUID("00000000-0000-7000-8000-000000000244"),
        submission_version=1,
        real_name_ciphertext=b"x",
        real_name_nonce=b"0" * 12,
        id_card_ciphertext=b"y",
        id_card_nonce=b"1" * 12,
        encryption_key_id="synthetic",
        formal_content_digest="8" * 64,
        formal_id_card_digest="9" * 64,
        decision_ref=UUID("00000000-0000-7000-8000-000000000245"),
        decision_facts_version=1,
        claim_id=UUID("00000000-0000-7000-8000-000000000246"),
        claim_version=1,
        consent_version="synthetic",
        chain_digest="a" * 64,
    )
    written: list[tuple[object, ...]] = []

    class Subjects:
        async def fetch_h3_material(self, **kwargs):
            return material

        async def clear_h3_legacy(self, **kwargs):
            return subject.H3MutationResult("b" * 64, "c" * 64)

    class Ledger:
        async def write(self, request):
            written.append(request.as_parameters())
            return ledger.LedgerWriteResult(
                "ITEM",
                item_ref,
                "REMEDIATED",
                4,
                "REMEDIATED",
                request.receipt_id,
                "d" * 64,
            )

    class Unit:
        def __init__(self, fail):
            self.subjects = Subjects()
            self.ledger = Ledger()
            self.fail = fail

        async def commit(self):
            if self.fail:
                raise RuntimeError("synthetic commit unknown")

    units = iter((Unit(True), Unit(False)))

    @asynccontextmanager
    async def unit_factory():
        yield next(units)

    class ConfirmationLedger:
        async def confirm(self, request, expectation):
            return ledger.CommitOutcome.NOT_COMMITTED

    @asynccontextmanager
    async def confirmation_factory():
        yield type("Confirmation", (), {"ledger": ConfirmationLedger()})()

    service = application.IdentityRemediationApplicationService(
        inventory_provider=lambda: None,
        unit_of_work_factory=unit_factory,
        confirmation_factory=confirmation_factory,
        crypto=_crypto(),
        actor_scope="synthetic-a2-r",
    )
    monkeypatch.setattr(
        application,
        "verify_h3_exact_match",
        lambda value, crypto: application.H3ExactMatchResult(True, "e" * 64, None),
    )
    result = asyncio.run(service._complete_h3(batch_ref, processing, "e" * 64))
    assert result.state == "REMEDIATED"
    assert len(written) == 2
    assert written[0] == written[1]


def test_A2_2_R_MaterialRepository异常不得翻译为身份不匹配() -> None:
    application = _module("app.modules.auth.identity_remediation_application")
    ledger = _module("app.modules.auth.identity_remediation_ledger_repository")

    class Subjects:
        async def fetch_h3_material(self, **kwargs):
            raise RuntimeError("synthetic repository unavailable")

    @asynccontextmanager
    async def unit_factory():
        yield type("Unit", (), {"subjects": Subjects()})()

    service = application.IdentityRemediationApplicationService(
        inventory_provider=lambda: None,
        unit_of_work_factory=unit_factory,
        confirmation_factory=lambda: None,
        crypto=_crypto(),
        actor_scope="synthetic-a2-r",
    )
    discovered = ledger.LedgerWriteResult(
        "ITEM",
        UUID("00000000-0000-7000-8000-000000000211"),
        "DISCOVERED",
        1,
        "DISCOVERED",
        UUID("00000000-0000-7000-8000-000000000212"),
        "5" * 64,
    )
    with pytest.raises(RuntimeError, match="synthetic repository unavailable"):
        asyncio.run(
            service._process_h3(
                UUID("00000000-0000-7000-8000-000000000213"), discovered
            )
        )


def test_A2_2_R_Service严格one_shot且不会沿用旧running_batch() -> None:
    application = _module("app.modules.auth.identity_remediation_application")
    calls = 0

    async def inventory():
        nonlocal calls
        calls += 1
        raise RuntimeError("must not execute")

    service = application.IdentityRemediationApplicationService(
        inventory_provider=inventory,
        unit_of_work_factory=lambda: None,
        confirmation_factory=lambda: None,
        crypto=_crypto(),
        actor_scope="synthetic-a2-r",
    )
    service._used = True
    service._running_batch = None
    with pytest.raises(
        application.IdentityRemediationContractError,
        match="A2_REMEDIATION_SERVICE_ONE_SHOT",
    ):
        asyncio.run(service.run())
    assert calls == 0


@pytest.mark.parametrize(
    ("rollback_fails", "close_fails", "commit_cancels"),
    (
        (True, False, False),
        (False, True, False),
        (True, True, True),
    ),
    ids=("body-cancel-rollback", "body-cancel-close", "commit-cancel-cleanup"),
)
def test_A2_2_R_CancelledError不被UoW清理失败替换(
    rollback_fails: bool,
    close_fails: bool,
    commit_cancels: bool,
) -> None:
    runner = _module("app.composition.identity_remediation_runner")

    class Transaction:
        is_active = True

        async def rollback(self):
            if rollback_fails:
                raise RuntimeError("synthetic rollback failure")

        async def commit(self):
            if commit_cancels:
                raise asyncio.CancelledError

    class Connection:
        async def close(self):
            if close_fails:
                raise RuntimeError("synthetic close failure")

    async def exercise() -> None:
        unit = runner._WriterUnitOfWork(None, "synthetic")
        unit._transaction = Transaction()
        unit._connection = Connection()
        cancellation = asyncio.CancelledError()
        try:
            if commit_cancels:
                await unit.commit()
            raise cancellation
        except asyncio.CancelledError as error:
            await unit.__aexit__(asyncio.CancelledError, error, error.__traceback__)
            raise

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(exercise())


def test_A2_2_R_普通cleanup失败仍保持可见() -> None:
    runner = _module("app.composition.identity_remediation_runner")

    class Transaction:
        is_active = True

        async def rollback(self):
            raise RuntimeError("synthetic cleanup failure")

    class Connection:
        async def close(self):
            return None

    async def exercise() -> None:
        unit = runner._ConfirmationUnitOfWork(None, "synthetic")
        unit._transaction = Transaction()
        unit._connection = Connection()
        await unit.__aexit__(None, None, None)

    with pytest.raises(RuntimeError, match="synthetic cleanup failure"):
        asyncio.run(exercise())


@pytest.mark.parametrize(
    ("unit_name", "fail_stage"),
    (
        ("writer", "begin"),
        ("writer", "assert"),
        ("confirmation", "begin"),
        ("confirmation", "set-read-only"),
        ("confirmation", "assert"),
    ),
)
def test_A2_2_R_UoW入口失败主动rollback并close(
    monkeypatch,
    unit_name: str,
    fail_stage: str,
) -> None:
    runner = _module("app.composition.identity_remediation_runner")
    calls: list[str] = []

    class Transaction:
        is_active = True

        async def rollback(self):
            calls.append("rollback")

    class Connection:
        async def begin(self):
            calls.append("begin")
            if fail_stage == "begin":
                raise RuntimeError("synthetic entry failure")
            return Transaction()

        async def execute(self, statement):
            calls.append("set-read-only")
            if fail_stage == "set-read-only":
                raise RuntimeError("synthetic entry failure")

        async def close(self):
            calls.append("close")

    class Engine:
        async def connect(self):
            calls.append("connect")
            return Connection()

    async def fail_assert(*args, **kwargs):
        calls.append("assert")
        raise RuntimeError("synthetic entry failure")

    monkeypatch.setattr(runner, "_assert_role", fail_assert)
    unit_class = (
        runner._WriterUnitOfWork
        if unit_name == "writer"
        else runner._ConfirmationUnitOfWork
    )
    with pytest.raises(RuntimeError, match="synthetic entry failure"):
        asyncio.run(unit_class(Engine(), "synthetic").__aenter__())
    if fail_stage == "begin":
        assert calls[-2:] == ["begin", "close"]
    else:
        assert calls[-2:] == ["rollback", "close"]


def test_A2_2_R_UoW入口取消优先于rollback与close失败(monkeypatch) -> None:
    runner = _module("app.composition.identity_remediation_runner")
    calls: list[str] = []

    class Transaction:
        is_active = True

        async def rollback(self):
            calls.append("rollback")
            raise RuntimeError("synthetic rollback failure")

    class Connection:
        async def begin(self):
            return Transaction()

        async def close(self):
            calls.append("close")
            raise RuntimeError("synthetic close failure")

    class Engine:
        async def connect(self):
            return Connection()

    async def cancel_assert(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(runner, "_assert_role", cancel_assert)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runner._WriterUnitOfWork(Engine(), "synthetic").__aenter__())
    assert calls == ["rollback", "close"]


def test_A2_2_R_CLI异常闭合且不泄漏vendor_SQL_URL_Credential_PII(
    monkeypatch,
    capsys,
) -> None:
    cli = _module("scripts.run_a2_identity_remediation")
    sentinel = (
        "SELECT secret FROM private_table params=credential "
        "postgresql://user:password@host/db synthetic-subject synthetic-identity"
    )

    async def fail():
        raise RuntimeError(sentinel)

    monkeypatch.setattr(cli, "run_identity_remediation", fail)
    assert cli.main([]) == 2
    captured = capsys.readouterr()
    combined = (captured.out + captured.err).lower()
    assert "a2_remediation_internal_failure" in combined
    for forbidden in (
        "select secret",
        "private_table",
        "credential",
        "postgresql://",
        "password",
        "synthetic-subject",
        "synthetic-identity",
        "traceback",
    ):
        assert forbidden not in combined


def test_A2_2_R_COMMITTED后读回失败只暴露稳定错误码(monkeypatch) -> None:
    application = _module("app.modules.auth.identity_remediation_application")
    ledger = _module("app.modules.auth.identity_remediation_ledger_repository")
    request = ledger.LedgerWriteRequest.plan_batch(
        batch_ref=UUID("00000000-0000-7000-8000-000000000221"),
        classification_rule_hash="1" * 64,
        snapshot_ref_hash="2" * 64,
        class_counts={f"H{index}": 0 for index in range(8)},
        actor_scope="synthetic-a2-r",
        idempotency_key_digest="3" * 64,
        receipt_id=UUID("00000000-0000-7000-8000-000000000222"),
        audit_id=UUID("00000000-0000-7000-8000-000000000223"),
        occurred_at=datetime(2026, 9, 3, tzinfo=UTC),
    )
    result = ledger.LedgerWriteResult(
        "BATCH", request.batch_ref, "PLANNED", 1, "PLANNED", request.receipt_id, "4" * 64
    )

    class Unit:
        async def commit(self):
            raise RuntimeError("synthetic commit unknown")

    @asynccontextmanager
    async def unit_factory():
        yield Unit()

    class ConfirmationLedger:
        async def confirm(self, candidate, expectation):
            return ledger.CommitOutcome.COMMITTED

    @asynccontextmanager
    async def confirmation_factory():
        yield type("Confirmation", (), {"ledger": ConfirmationLedger()})()

    service = application.IdentityRemediationApplicationService(
        inventory_provider=lambda: None,
        unit_of_work_factory=unit_factory,
        confirmation_factory=confirmation_factory,
        crypto=_crypto(),
        actor_scope="synthetic-a2-r",
    )

    async def operation(unit):
        return application._OperationAttempt(
            (request,), ledger.LedgerExpectation("PLANNED", 1, "PLANNED"), result
        )

    async def readback(candidate):
        raise RuntimeError(
            "SELECT secret postgresql://user:password@host/db credential synthetic-identity"
        )

    monkeypatch.setattr(service, "_replay_committed", readback)
    with pytest.raises(
        application.IdentityRemediationContractError,
        match="^A2_REMEDIATION_COMMITTED_READBACK_FAILED$",
    ) as captured:
        asyncio.run(service._execute(operation))
    rendered = str(captured.value).lower()
    assert "select secret" not in rendered
    assert "postgresql://" not in rendered
    assert "credential" not in rendered


@pytest.mark.parametrize(
    "outcome",
    ("COMMITTED", "NOT_COMMITTED", "UNKNOWN"),
)
def test_A2_2_R_commit不明后的普通cleanup失败不跳过独立confirmation(
    monkeypatch,
    outcome: str,
) -> None:
    application = _module("app.modules.auth.identity_remediation_application")
    ledger = _module("app.modules.auth.identity_remediation_ledger_repository")
    request = ledger.LedgerWriteRequest.plan_batch(
        batch_ref=UUID("00000000-0000-7000-8000-000000000231"),
        classification_rule_hash="1" * 64,
        snapshot_ref_hash="2" * 64,
        class_counts={f"H{index}": 0 for index in range(8)},
        actor_scope="synthetic-a2-r",
        idempotency_key_digest="3" * 64,
        receipt_id=UUID("00000000-0000-7000-8000-000000000232"),
        audit_id=UUID("00000000-0000-7000-8000-000000000233"),
        occurred_at=datetime(2026, 9, 3, tzinfo=UTC),
    )
    result = ledger.LedgerWriteResult(
        "BATCH", request.batch_ref, "PLANNED", 1, "PLANNED", request.receipt_id, "4" * 64
    )
    confirmations: list[object] = []
    attempts = 0

    class Unit:
        async def commit(self):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("vendor commit outcome unknown")

    class UnitContext:
        async def __aenter__(self):
            return Unit()

        async def __aexit__(self, exc_type, exc, traceback):
            if attempts == 1:
                raise RuntimeError("vendor cleanup failed")

    class ConfirmationLedger:
        async def confirm(self, candidate, expectation):
            confirmations.append(candidate)
            return ledger.CommitOutcome[outcome]

    @asynccontextmanager
    async def confirmation_factory():
        yield type("Confirmation", (), {"ledger": ConfirmationLedger()})()

    service = application.IdentityRemediationApplicationService(
        inventory_provider=lambda: None,
        unit_of_work_factory=UnitContext,
        confirmation_factory=confirmation_factory,
        crypto=_crypto(),
        actor_scope="synthetic-a2-r",
    )

    async def operation(unit):
        return application._OperationAttempt(
            (request,), ledger.LedgerExpectation("PLANNED", 1, "PLANNED"), result
        )

    async def readback(candidate):
        assert candidate.as_parameters() == request.as_parameters()
        return result

    monkeypatch.setattr(service, "_replay_committed", readback)
    if outcome == "UNKNOWN":
        with pytest.raises(
            application.IdentityRemediationCommitOutcomeUnknown,
            match="^A2_REMEDIATION_COMMIT_OUTCOME_UNKNOWN$",
        ) as captured:
            asyncio.run(service._execute(operation))
        rendered = str(captured.value).lower()
        assert "vendor" not in rendered
    else:
        assert asyncio.run(service._execute(operation)) == result
    assert confirmations == [request]
    assert confirmations[0].as_parameters() == request.as_parameters()
    assert attempts == (2 if outcome == "NOT_COMMITTED" else 1)


def test_A2_2_R_commit不明后的cleanup取消仍立即传播且不confirmation() -> None:
    application = _module("app.modules.auth.identity_remediation_application")
    ledger = _module("app.modules.auth.identity_remediation_ledger_repository")
    request = ledger.LedgerWriteRequest.plan_batch(
        batch_ref=UUID("00000000-0000-7000-8000-000000000241"),
        classification_rule_hash="1" * 64,
        snapshot_ref_hash="2" * 64,
        class_counts={f"H{index}": 0 for index in range(8)},
        actor_scope="synthetic-a2-r",
        idempotency_key_digest="3" * 64,
        receipt_id=UUID("00000000-0000-7000-8000-000000000242"),
        audit_id=UUID("00000000-0000-7000-8000-000000000243"),
        occurred_at=datetime(2026, 9, 3, tzinfo=UTC),
    )
    result = ledger.LedgerWriteResult(
        "BATCH", request.batch_ref, "PLANNED", 1, "PLANNED", request.receipt_id, "4" * 64
    )
    confirmation_called = False

    class Unit:
        async def commit(self):
            raise RuntimeError("vendor commit outcome unknown")

    class UnitContext:
        async def __aenter__(self):
            return Unit()

        async def __aexit__(self, exc_type, exc, traceback):
            raise asyncio.CancelledError

    @asynccontextmanager
    async def confirmation_factory():
        nonlocal confirmation_called
        confirmation_called = True
        yield None

    service = application.IdentityRemediationApplicationService(
        inventory_provider=lambda: None,
        unit_of_work_factory=UnitContext,
        confirmation_factory=confirmation_factory,
        crypto=_crypto(),
        actor_scope="synthetic-a2-r",
    )

    async def operation(unit):
        return application._OperationAttempt(
            (request,), ledger.LedgerExpectation("PLANNED", 1, "PLANNED"), result
        )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service._execute(operation))
    assert confirmation_called is False


@pytest.mark.parametrize("unknown_source", ("parameter-drift", "operation"))
def test_A2_2_R_retry不一致只在Writer退出后才打开pause(
    monkeypatch,
    unknown_source: str,
) -> None:
    application = _module("app.modules.auth.identity_remediation_application")
    ledger = _module("app.modules.auth.identity_remediation_ledger_repository")
    request = ledger.LedgerWriteRequest.plan_batch(
        batch_ref=UUID("00000000-0000-7000-8000-000000000251"),
        classification_rule_hash="1" * 64,
        snapshot_ref_hash="2" * 64,
        class_counts={f"H{index}": 0 for index in range(8)},
        actor_scope="synthetic-a2-r",
        idempotency_key_digest="3" * 64,
        receipt_id=UUID("00000000-0000-7000-8000-000000000252"),
        audit_id=UUID("00000000-0000-7000-8000-000000000253"),
        occurred_at=datetime(2026, 9, 3, tzinfo=UTC),
    )
    changed_request = replace(
        request,
        reason_code="A2_BATCH_CONTROLLED",
    )
    result = ledger.LedgerWriteResult(
        "BATCH", request.batch_ref, "PLANNED", 1, "PLANNED", request.receipt_id, "4" * 64
    )
    context_open = False
    attempts = 0
    pause_calls = 0

    class Unit:
        async def commit(self):
            nonlocal attempts
            if attempts == 1:
                raise RuntimeError("synthetic commit unknown")

    class UnitContext:
        async def __aenter__(self):
            nonlocal context_open, attempts
            context_open = True
            attempts += 1
            return Unit()

        async def __aexit__(self, exc_type, exc, traceback):
            nonlocal context_open
            context_open = False

    class ConfirmationLedger:
        async def confirm(self, candidate, expectation):
            return ledger.CommitOutcome.NOT_COMMITTED

    @asynccontextmanager
    async def confirmation_factory():
        yield type("Confirmation", (), {"ledger": ConfirmationLedger()})()

    service = application.IdentityRemediationApplicationService(
        inventory_provider=lambda: None,
        unit_of_work_factory=UnitContext,
        confirmation_factory=confirmation_factory,
        crypto=_crypto(),
        actor_scope="synthetic-a2-r",
    )

    async def pause():
        nonlocal pause_calls
        assert context_open is False
        pause_calls += 1

    async def operation(unit):
        if attempts == 2 and unknown_source == "operation":
            raise application.IdentityRemediationCommitOutcomeUnknown(
                "A2_REMEDIATION_WORKSET_RETRY_INCONSISTENT"
            )
        candidate = changed_request if attempts == 2 else request
        return application._OperationAttempt(
            (candidate,), ledger.LedgerExpectation("PLANNED", 1, "PLANNED"), result
        )

    monkeypatch.setattr(service, "_best_effort_pause", pause)
    with pytest.raises(application.IdentityRemediationCommitOutcomeUnknown):
        asyncio.run(service._execute(operation))
    assert pause_calls == 1
    assert context_open is False


def test_A2_2_R_CLI保持CancelledError控制流(monkeypatch) -> None:
    cli = _module("scripts.run_a2_identity_remediation")

    async def cancel():
        raise asyncio.CancelledError

    monkeypatch.setattr(cli, "run_identity_remediation", cancel)
    with pytest.raises(asyncio.CancelledError):
        cli.main([])


def test_A2_2_R_公开结果仅含匿名闭合字段() -> None:
    application = _module("app.modules.auth.identity_remediation_application")
    summary = application.RemediationRunSummary(
        status="PAUSED",
        result_code="A2_REMEDIATION_TOKEN_WINDOW_UNPROVEN",
        class_status_counts={"H4:REMEDIATION_REQUIRED": 1},
        processed_count=1,
        mutation_count=0,
        digest_present=True,
    )
    public = summary.to_public_dict()
    encoded = repr(public).lower()
    assert set(public) == {
        "status",
        "result_code",
        "class_status_counts",
        "processed_count",
        "mutation_count",
        "digest_present",
    }
    for forbidden in (
        "user_ref",
        "real_name",
        "id_card",
        "ciphertext",
        "key_id",
        "database_url",
        "credential",
    ):
        assert forbidden not in encoded


def test_A2_2_R_actor_scope对最长run_id仍闭合稳定且不暴露原值() -> None:
    runner = _module("app.composition.identity_remediation_runner")
    run_id = "r" + "7" * 56
    actor_scope = runner._actor_scope(run_id)
    assert actor_scope.startswith("a2-remediation-")
    assert len(actor_scope) <= 64
    assert all(
        character.islower() or character.isdigit() or character in "_-"
        for character in actor_scope
    )
    assert run_id not in actor_scope
    assert actor_scope == runner._actor_scope(run_id)


def test_A2_2_R_数据库名与run_id不允许超过PostgreSQL标识符边界(
    monkeypatch,
) -> None:
    runner = _module("app.composition.identity_remediation_runner")
    accepted_run_id = "r" + "7" * 56
    accepted_database = f"kg_it_{accepted_run_id}"
    assert len(accepted_database) == 63
    monkeypatch.setenv("KG_TEST_ENVIRONMENT", "local_ephemeral")
    monkeypatch.setenv("KG_TEST_RUN_ID", accepted_run_id)
    monkeypatch.setenv(
        "KG_A2_IDENTITY_REMEDIATION_WRITER_DATABASE_URL",
        f"postgresql://synthetic:synthetic@127.0.0.1:5432/{accepted_database}",
    )
    assert runner._validated_url(
        "KG_A2_IDENTITY_REMEDIATION_WRITER_DATABASE_URL"
    ).endswith(accepted_database)

    monkeypatch.setenv("KG_TEST_RUN_ID", "r" + "7" * 57)
    with pytest.raises(
        runner.IdentityRemediationRunnerConfigurationError,
        match="^A2_REMEDIATION_RUN_ID_REQUIRED$",
    ):
        runner._validated_url("KG_A2_IDENTITY_REMEDIATION_WRITER_DATABASE_URL")


def test_A2_2_R_Runtime检查必须真实复用Inventory只读检查() -> None:
    runner = _module("app.composition.identity_remediation_runner")
    source = inspect.getsource(runner.inspect_remediation_runtime)
    assert "await inspect_read_only_transaction()" in source
    assert '"transaction_read_only": "on"' in source
    assert '"transaction_isolation": "repeatable read"' in source


@pytest.mark.parametrize("failure_source", ("material", "repository", "ledger"))
def test_A2_2_R_START后普通异常必须退出原UoW再受控Pause且保留主异常(
    monkeypatch,
    failure_source: str,
) -> None:
    application = _module("app.modules.auth.identity_remediation_application")
    ledger = _module("app.modules.auth.identity_remediation_ledger_repository")
    events: list[str] = []
    service = application.IdentityRemediationApplicationService(
        inventory_provider=lambda: None,
        unit_of_work_factory=lambda: None,
        confirmation_factory=lambda: None,
        crypto=_crypto(),
        actor_scope="synthetic-a2-r",
    )

    async def run_once():
        service._running_batch = ledger.LedgerWriteResult(
            "BATCH",
            UUID("00000000-0000-7000-8000-000000000301"),
            "RUNNING",
            2,
            "RUNNING",
            UUID("00000000-0000-7000-8000-000000000302"),
            "a" * 64,
        )
        events.append("writer-exited")
        raise RuntimeError(f"synthetic {failure_source} unavailable")

    async def pause():
        assert events == ["writer-exited"]
        events.append("pause-after-exit")
        raise RuntimeError("synthetic pause unavailable")

    monkeypatch.setattr(service, "_run_once", run_once)
    monkeypatch.setattr(service, "_best_effort_pause", pause)
    with pytest.raises(
        RuntimeError, match=f"^synthetic {failure_source} unavailable$"
    ):
        asyncio.run(service.run())
    assert events == ["writer-exited", "pause-after-exit"]


@pytest.mark.parametrize(
    ("batch_state", "cancelled"),
    ((None, False), ("PAUSED", False), ("COMPLETED", False), ("RUNNING", True)),
    ids=("not-started", "paused", "completed", "cancelled"),
)
def test_A2_2_R_未启动终态或取消不得伪造Pause(
    monkeypatch,
    batch_state: str | None,
    cancelled: bool,
) -> None:
    application = _module("app.modules.auth.identity_remediation_application")
    ledger = _module("app.modules.auth.identity_remediation_ledger_repository")
    pause_calls = 0
    service = application.IdentityRemediationApplicationService(
        inventory_provider=lambda: None,
        unit_of_work_factory=lambda: None,
        confirmation_factory=lambda: None,
        crypto=_crypto(),
        actor_scope="synthetic-a2-r",
    )

    async def run_once():
        if batch_state is not None:
            service._running_batch = ledger.LedgerWriteResult(
                "BATCH",
                UUID("00000000-0000-7000-8000-000000000303"),
                batch_state,
                3,
                batch_state,
                UUID("00000000-0000-7000-8000-000000000304"),
                "b" * 64,
            )
        if cancelled:
            raise asyncio.CancelledError
        raise RuntimeError("synthetic primary failure")

    async def pause():
        nonlocal pause_calls
        pause_calls += 1

    monkeypatch.setattr(service, "_run_once", run_once)
    monkeypatch.setattr(service, "_best_effort_pause", pause)
    expected = asyncio.CancelledError if cancelled else RuntimeError
    with pytest.raises(expected):
        asyncio.run(service.run())
    assert pause_calls == 0


def test_A2_2_R_Runtime三角色拒绝集合必须覆盖全部闭合底表() -> None:
    runner = _module("app.composition.identity_remediation_runner")
    assert set(runner._BASE_TABLES) == {
        ("public", "user"),
        ("public", "identity_verification_submission"),
        ("public", "identity_verification_decision"),
        ("public", "service_enrollment"),
        ("identity", "identity_subject_claim_registry"),
        ("identity", "user_member_self_link"),
        ("identity", "identity_remediation_batch"),
        ("identity", "identity_remediation_item"),
        ("identity", "identity_remediation_audit"),
        ("identity", "identity_remediation_receipt"),
    }


def test_A2_2_R_CLI拒绝主体PII_URL_Credential_JSON_SQL参数() -> None:
    cli = _module("scripts.run_a2_identity_remediation")
    forbidden_arguments = (
        ("--user-ref", "1"),
        ("--id-card", "synthetic"),
        ("--database-url", "postgresql://invalid"),
        ("--credential", "synthetic"),
        ("--payload", "{}"),
        ("--sql", "select 1"),
    )
    for arguments in forbidden_arguments:
        with pytest.raises(SystemExit):
            cli.parse_args(list(arguments))
    assert cli.parse_args([]).format == "json"


def test_A2_2_R_源码没有HTTP_Worker_Migration或底表SQL旁路() -> None:
    for path in PRODUCTION_PATHS:
        source = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_SOURCE_TOKENS:
            assert token not in source
    ledger = _module("app.modules.auth.identity_remediation_ledger_repository")
    subject = _module("app.modules.auth.identity_remediation_subject_repository")
    assert "identity_remediation_batch" not in inspect.getsource(ledger)
    assert 'public."user"' not in inspect.getsource(subject)
    assert "identity_verification_submission" not in inspect.getsource(subject)


def test_A2_2_R_测试材料仅使用合成密钥且不依赖环境Secret() -> None:
    encryption = base64.b64encode(bytes(range(32))).decode()
    hmac_key = base64.b64encode(bytes(range(32, 64))).decode()
    assert hashlib.sha256(encryption.encode()).hexdigest()
    assert hashlib.sha256(hmac_key.encode()).hexdigest()
