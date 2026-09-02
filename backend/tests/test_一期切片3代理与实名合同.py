from datetime import date
import base64
import json
from pathlib import Path
from uuid import UUID

import pytest

from app.modules.member_enrollment.identity_authority import (
    parse_prc_resident_identity_birth_date,
    verified_adult_on,
)
from app.modules.member_enrollment.service import MemberEnrollmentSecrets
from app.modules.member_enrollment.schemas import PlatformIdentityDecisionRequest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "app/migrations/versions/20260818_0022_phase1_slice3_member_proxy_consent_service_case.py"


def test_身份证日期与校验位有效时计算成年() -> None:
    birth_date = parse_prc_resident_identity_birth_date("11010519491231002X")
    assert birth_date == date(1949, 12, 31)
    assert verified_adult_on(birth_date, date(2026, 8, 17)) is True


@pytest.mark.parametrize(
    "value",
    [
        "110105194912310021",
        "１１０１０５１９４９１２３１００２Ｘ",
        "11010520250230002X",
        "",
    ],
    ids=["invalid-checksum", "full-width", "invalid-birth-date", "empty"],
)
def test_身份证非法格式日期或校验位fail_closed(value: str) -> None:
    with pytest.raises(ValueError, match="IDENTITY_DOCUMENT_INVALID"):
        parse_prc_resident_identity_birth_date(value)


def test_未满十八岁不能成为代理() -> None:
    assert verified_adult_on(date(2010, 8, 18), date(2026, 8, 17)) is False


def test_pii与回放密文分别按精确aad往返且错误对象拒绝() -> None:
    secrets = object.__new__(MemberEnrollmentSecrets)
    secrets.pii_key_id = "pii-v1"
    secrets.pii_keys = {"pii-v1": b"p" * 32}
    secrets.replay_key_id = "replay-v1"
    secrets.replay_keys = {"replay-v1": b"r" * 32}
    tenant_id = UUID("0198f1c0-0000-7000-8000-000000000001")
    object_id = UUID("0198f1c0-0000-7000-8000-000000000002")

    ciphertext, key_id = secrets.encrypt(
        "opaque-value", field="identity-real-name",
        tenant_public_id=tenant_id, object_id=object_id,
    )
    assert secrets.decrypt(
        ciphertext, key_id, field="identity-real-name",
        tenant_public_id=tenant_id, object_id=object_id,
    ) == "opaque-value"
    with pytest.raises(RuntimeError, match="MEMBER_ENROLLMENT_DEPENDENCY_UNAVAILABLE"):
        secrets.decrypt(
            ciphertext, key_id, field="identity-real-name",
            tenant_public_id=tenant_id,
            object_id=UUID("0198f1c0-0000-7000-8000-000000000003"),
        )

    replay, replay_key = secrets.encrypt_replay(
        {"result": "ok"}, actor_scope="user:1:platform",
        operation="CONSENT_DOCUMENT_CREATE", target_id=object_id, key="idem-key-1",
    )
    assert secrets.decrypt_replay(
        replay, replay_key, actor_scope="user:1:platform",
        operation="CONSENT_DOCUMENT_CREATE", target_id=object_id, key="idem-key-1",
    ) == {"result": "ok"}


def test_P1与Slice3同身份双writer及轮换只一owner() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'sa.Column("fingerprint_key_id",sa.String(64),nullable=False)' in source
    assert "INSERT INTO identity.identity_claim_algorithm_state" in source
    assert "identity_verification_submission" in source
    assert "CREATE CONSTRAINT TRIGGER trg_slice3_p1_identity_claim_registry" in source
    assert "id_card_digest" in source
    assert "source_kind='P1'" in source
    assert "SLICE3_IDENTITY_FINGERPRINT_ALGORITHM_MISMATCH" in source
    assert "pg_advisory_xact_lock" in source


def test_十一域HMAC_AAD与golden_vector拒绝跨边界移植(monkeypatch) -> None:
    rings = {
        "PII": b"a" * 32,
        "LOOKUP": b"b" * 32,
        "CODE": b"c" * 32,
        "REPLAY": b"d" * 32,
        "DELIVERY": b"e" * 32,
        "COORDINATION": b"f" * 32,
        "REQUEST_DIGEST": b"g" * 32,
        "AUDIT_DIGEST": b"h" * 32,
        "OUTBOX_DIGEST": b"i" * 32,
        "CONSENT_DIGEST": b"j" * 32,
    }
    for name, material in rings.items():
        monkeypatch.setenv(f"KG_MEMBER_ENROLLMENT_{name}_CURRENT_KEY_ID", f"{name.lower()}-v1")
        monkeypatch.setenv(
            f"KG_MEMBER_ENROLLMENT_{name}_KEYRING_JSON",
            json.dumps({f"{name.lower()}-v1": base64.b64encode(material).decode()}),
        )
    monkeypatch.setenv("KG_IDENTITY_PII_KEY_ID", "p1-identity-v1")
    monkeypatch.setenv("KG_IDENTITY_PII_HMAC_KEY_B64", base64.b64encode(b"k" * 32).decode())

    secrets = MemberEnrollmentSecrets()
    tenant_id = UUID("0198f1c0-0000-7000-8000-000000000001")
    object_id = UUID("0198f1c0-0000-7000-8000-000000000002")
    ciphertext, key_id = secrets.encrypt(
        "opaque-value", field="identity-number",
        tenant_public_id=tenant_id, object_id=object_id,
    )
    assert key_id == "pii-v1"
    assert secrets.decrypt(
        ciphertext, key_id, field="identity-number",
        tenant_public_id=tenant_id, object_id=object_id,
    ) == "opaque-value"
    # Domain separator is followed by one NUL byte, then canonical JSON bytes.
    assert secrets.request_digest({"value": "low-entropy"}) == "1a4d6c2955cd692eb2382fda64a427d3f8b95dfbfee5820a0b4df258d32d7367"
    assert secrets.audit_digest({"status": "ACCEPTED"}) == "8ef1f0ae0083c22910cb6d2b525c7c7f05b83420f45e4f70e83672a857ce6a69"
    assert secrets.outbox_digest({"event": "MEMBER_ENROLLMENT_ACCEPTED"}) == "b9b51ca6ba9116415f1a6c8e02178734cf4a2177ea5b850ac4feefd2e69434f2"
    assert secrets.consent_digest({"version": "v1"}) == "1c67e497212e86012fa069a4a8cfaf4af15549dc95aaf66c2e3cec024928d9b6"
    assert secrets.identity_fingerprint("11010519491231002X")[0] == "p1-identity-v1"

    with pytest.raises(RuntimeError, match="MEMBER_ENROLLMENT_DEPENDENCY_UNAVAILABLE"):
        secrets.decrypt(
            ciphertext, "pii-v2", field="identity-number",
            tenant_public_id=tenant_id, object_id=object_id,
        )


def test_step_up失败预算_nonce一次性与decision消费() -> None:
    import inspect

    from app.modules.member_enrollment import api, repository, service

    access_source = inspect.getsource(service.MemberEnrollmentService.access_identity_pii)
    api_source = Path(api.__file__).read_text(encoding="utf-8")
    repository_source = inspect.getsource(repository.MemberEnrollmentRepository)
    migration_source = (
        Path(__file__).resolve().parents[1]
        / "app/migrations/versions/20260818_0022_phase1_slice3_member_proxy_consent_service_case.py"
    ).read_text(encoding="utf-8")

    assert "step_up_access_id" not in PlatformIdentityDecisionRequest.model_fields
    for value in (
        "password_valid",
        "access_token_digest",
        "reviewer_step_up_budget",
        "add_pii_access",
        "reviewer_pii",
        'status="ISSUED"',
    ):
        assert value in access_source

    for value in (
        "slice3_reviewer_step_up_budget_v1",
        "slice3_reviewer_pii_v1",
        "credential_proof_digest",
    ):
        assert value in repository_source
        assert value in migration_source
    assert "IDENTITY_PII_STEP_UP_FAILED" in migration_source
    assert "IDENTITY_PII_STEP_UP_RATE_LIMITED" in migration_source
    assert "status='CONSUMED'" in migration_source
    assert "FOR UPDATE" in migration_source
    assert "authorization" in api_source.lower()
    assert "password_valid=" in api_source


def test_identity_review_verification_advisory_boundary早于所有行锁() -> None:
    import inspect

    from app.modules.member_enrollment import ports, repository, service

    port_source = inspect.getsource(ports.MemberEnrollmentRepositoryPort)
    repository_source = inspect.getsource(
        repository.MemberEnrollmentRepository.lock_identity_review_boundary
    )
    assert "lock_identity_review_boundary" in port_source
    assert "slice3-identity-review-boundary" in repository_source
    assert "pg_advisory_xact_lock" in repository_source
    assert "hashlib.sha256" in repository_source
    assert "operation" not in repository_source
    assert "idempotency" not in repository_source
    assert "actor_scope" not in repository_source

    for entrypoint in (
        service.MemberEnrollmentService.access_identity_pii,
        service.MemberEnrollmentService.platform_identity_decide,
    ):
        source = inspect.getsource(entrypoint)
        boundary = source.index("lock_identity_review_boundary")
        authority = source.index("reviewer_claim_is_current")
        operation = source.index("lock_operation")
        assert boundary < authority < operation


def test_F2_step_up结果变体与credential_proof一次性边界() -> None:
    from app.modules.member_enrollment import api

    api_source = Path(api.__file__).read_text(encoding="utf-8")
    migration_source = MIGRATION.read_text(encoding="utf-8")

    assert api._STATUS["STEP_UP_FORBIDDEN"] == 403
    assert api._STATUS["STEP_UP_RATE_LIMITED"] == 429
    assert 'result_variant' in api_source
    assert 'IDENTITY_PII_STEP_UP_RATE_LIMITED' in api_source
    assert "preimage_digest=proof_marker" in migration_source
    assert "STEP_UP_REPLAYED" in migration_source
    assert "CASE WHEN variant='RATE_LIMITED'" in migration_source
    assert "failures+1>=5" in migration_source


def test_空集并发分配slot1与slot2() -> None:
    from app.modules.member_enrollment import repository, service

    repository_source = Path(repository.__file__).read_text(encoding="utf-8")
    service_source = Path(service.__file__).read_text(encoding="utf-8")
    assert "lock_proxy_slot_boundary" in repository_source
    assert service_source.index("lock_proxy_slot_boundary") < service_source.index(
        "active_proxy_slots"
    )
