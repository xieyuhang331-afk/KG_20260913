import asyncio
from datetime import datetime, timezone
from importlib import import_module

import pytest


EXPECTED_RED = "Manual identity review authority decision is not implemented"
DECIDED_AT = datetime(2026, 8, 8, 8, 0, tzinfo=timezone.utc)


def _module():
    try:
        return import_module(
            "app.modules.auth.identity_verification_authority"
        )
    except (ImportError, AttributeError):
        pytest.fail(EXPECTED_RED)


def _decision(**changes):
    module = _module()
    values = {
        "authority_source": "manual_review",
        "authority_decision_id": "manual-review-decision-1042-v7",
        "reviewer_subject_id": 17,
        "reviewer_role": "super_admin",
        "reviewer_is_active": True,
        "reviewer_tenant_scope": None,
        "reviewer_org_scope": "platform",
        "user_ref": 1042,
        "subject_tenant_id": None,
        "subject_org_id": None,
        "subject_binding_started": False,
        "outcome": "verified",
        "facts_version": 19,
        "currentness_version": 7,
        "verification_epoch": 11,
        "predecessor_currentness_version": 6,
        "predecessor_verification_epoch": 10,
        "decided_at": DECIDED_AT,
        "evidence_digest": "a" * 64,
        "correlation_id": "registration-correlation-1042",
        "is_current": True,
        "revocation_reference": None,
    }
    values.update(changes)
    return module.ManualIdentityReviewAuthorityDecision(**values)


def test_人工身份审核权威决定尚未实现():
    decision = _decision()
    assert decision.authority_source == "manual_review"
    assert decision.reviewer_role == "super_admin"
    assert decision.currentness_version == 7
    assert decision.verification_epoch == 11


@pytest.mark.parametrize(
    "changes",
    [
        {"authority_source": "provider_callback"},
        {"reviewer_role": "province_admin"},
        {"reviewer_role": "city_admin"},
        {"reviewer_role": "institution_admin"},
        {"reviewer_role": "user"},
        {"reviewer_is_active": False},
        {"reviewer_tenant_scope": 3},
        {"reviewer_org_scope": "institution"},
        {"subject_tenant_id": 3},
        {"subject_org_id": 9},
        {"subject_binding_started": True},
        {"reviewer_subject_id": 1042},
        {"outcome": "rejected"},
        {"is_current": False},
        {"revocation_reference": "revocation-1"},
    ],
)
def test_人工审核权限和当前性边界全部fail_closed(changes):
    module = _module()
    with pytest.raises(module.InvalidIdentityVerificationAuthorityDecision):
        _decision(**changes)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reviewer_subject_id", True),
        ("reviewer_subject_id", 0),
        ("user_ref", True),
        ("user_ref", 0),
        ("facts_version", 0),
        ("currentness_version", 0),
        ("verification_epoch", 0),
        ("predecessor_currentness_version", 0),
        ("predecessor_verification_epoch", 0),
        ("decided_at", datetime(2026, 8, 8, 8, 0)),
        ("evidence_digest", "not-a-digest"),
    ],
)
def test_人工审核决定拒绝模糊类型和非规范证据(field, value):
    module = _module()
    with pytest.raises(module.InvalidIdentityVerificationAuthorityDecision):
        _decision(**{field: value})


def test_人工审核决定不可变且版本语义彼此独立():
    decision = _decision(
        currentness_version=5,
        verification_epoch=13,
        predecessor_currentness_version=4,
        predecessor_verification_epoch=12,
    )
    assert decision.currentness_version == 5
    assert decision.verification_epoch == 13
    with pytest.raises((AttributeError, TypeError)):
        decision.outcome = "rejected"


@pytest.mark.parametrize(
    "changes",
    [
        {
            "currentness_version": 7,
            "predecessor_currentness_version": 7,
        },
        {
            "verification_epoch": 11,
            "predecessor_verification_epoch": 11,
        },
        {"predecessor_currentness_version": None},
        {"predecessor_verification_epoch": None},
    ],
)
def test_人工审核决定要求两个Authority版本分别严格递增(changes):
    module = _module()
    with pytest.raises(module.InvalidIdentityVerificationAuthorityDecision):
        _decision(**changes)


def test_首个Authority决定允许两个前任版本同时为空():
    decision = _decision(
        currentness_version=1,
        verification_epoch=1,
        predecessor_currentness_version=None,
        predecessor_verification_epoch=None,
    )
    assert decision.predecessor_currentness_version is None
    assert decision.predecessor_verification_epoch is None


def test_Authority_Port只按决定标识读取当前可信快照():
    module = _module()

    class Port:
        async def load_current_decision(self, authority_decision_id):
            assert authority_decision_id == "manual-review-decision-1042-v7"
            return _decision()

    assert isinstance(Port(), module.IdentityVerificationAuthorityPort)


def test_Authority_Port同一决定标识重复读取保持稳定():
    module = _module()
    expected = _decision()

    class Port:
        async def load_current_decision(self, authority_decision_id):
            assert authority_decision_id == expected.authority_decision_id
            return expected

    async def exercise():
        port = Port()
        first = await port.load_current_decision(
            expected.authority_decision_id
        )
        replay = await port.load_current_decision(
            expected.authority_decision_id
        )
        return first, replay

    first, replay = asyncio.run(exercise())
    assert first == replay
    assert first.currentness_version == replay.currentness_version
    assert first.verification_epoch == replay.verification_epoch
