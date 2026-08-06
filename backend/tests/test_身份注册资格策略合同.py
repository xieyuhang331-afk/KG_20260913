from dataclasses import FrozenInstanceError, replace
from uuid import UUID

import pytest

from app.modules.member.application.registration_eligibility import (
    AccountClass,
    EligibilityDecision,
    EligibilityReason,
    RegistrationAccountStatus,
    RegistrationEligibilityPolicy,
    RegistrationUserRole,
    TrustedRegistrationEligibilityFacts,
    VerificationOutcome,
    VerificationStatus,
)


VERIFICATION_REF = UUID("01890f3e-7b7d-7cc3-98c8-2f5a12d21234")
CLASSIFICATION_REF = UUID("01890f3e-7b7d-7cc3-a8c8-2f5a12d25678")
NON_V7_REF = UUID("12345678-1234-4234-8234-123456789abc")


def _facts(**changes):
    facts = TrustedRegistrationEligibilityFacts(
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
        classification_version=4,
        classification_is_current=True,
    )
    return replace(facts, **changes)


def _assert_result(facts, decision, reason):
    result = RegistrationEligibilityPolicy.evaluate(facts)

    assert result.decision is decision
    assert result.reason is reason


def test_注册资格策略尚未实现() -> None:
    _assert_result(
        _facts(),
        EligibilityDecision.ELIGIBLE,
        EligibilityReason.ELIGIBLE,
    )


def test_用户不存在优先于其余事实完整性():
    _assert_result(
        TrustedRegistrationEligibilityFacts(
            user_ref=7,
            exists=False,
            facts_version=1,
        ),
        EligibilityDecision.INELIGIBLE,
        EligibilityReason.USER_NOT_FOUND,
    )


@pytest.mark.parametrize(
    "facts",
    [
        object(),
        _facts(user_ref=True),
        _facts(user_ref=0),
        _facts(exists=1),
        _facts(facts_version=True),
        _facts(facts_version=0),
        _facts(role="member"),
        _facts(status="active"),
        _facts(verify_status="verified"),
    ],
)
def test_信封与基础事实严格拒绝隐式类型转换(facts):
    _assert_result(
        facts,
        EligibilityDecision.INDETERMINATE,
        EligibilityReason.UNSUPPORTED_FACTS,
    )


@pytest.mark.parametrize("field", ["role", "status", "verify_status"])
def test_存在用户的基础事实缺失时不确定(field):
    _assert_result(
        _facts(**{field: None}),
        EligibilityDecision.INDETERMINATE,
        EligibilityReason.FACTS_UNAVAILABLE,
    )


def test_非成员角色不合格():
    _assert_result(
        _facts(role=RegistrationUserRole.OTHER),
        EligibilityDecision.INELIGIBLE,
        EligibilityReason.ROLE_NOT_MEMBER,
    )


@pytest.mark.parametrize(
    "status",
    [RegistrationAccountStatus.DISABLED, RegistrationAccountStatus.SUSPENDED],
)
def test_非活跃账号不合格(status):
    _assert_result(
        _facts(status=status),
        EligibilityDecision.INELIGIBLE,
        EligibilityReason.ACCOUNT_NOT_ACTIVE,
    )


@pytest.mark.parametrize(
    "status",
    [
        VerificationStatus.SUBMITTED,
        VerificationStatus.UNVERIFIED,
        VerificationStatus.FAILED,
    ],
)
def test_当前投影未verified时不合格(status):
    _assert_result(
        _facts(verify_status=status),
        EligibilityDecision.INELIGIBLE,
        EligibilityReason.IDENTITY_NOT_VERIFIED,
    )


@pytest.mark.parametrize(
    "field",
    [
        "verification_decision_ref",
        "verification_subject_user_ref",
        "verification_outcome",
        "verification_epoch",
        "verification_is_current",
    ],
)
def test_verified投影缺少不可变决定事实时不确定(field):
    _assert_result(
        _facts(**{field: None}),
        EligibilityDecision.INDETERMINATE,
        EligibilityReason.VERIFICATION_FACTS_INCONSISTENT,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"verification_decision_ref": "not-a-uuid"},
        {"verification_decision_ref": NON_V7_REF},
        {"verification_subject_user_ref": True},
        {"verification_outcome": "verified"},
        {"verification_epoch": True},
        {"verification_epoch": 0},
        {"verification_is_current": 1},
    ],
)
def test_不可变审核事实类型严格(changes):
    _assert_result(
        _facts(**changes),
        EligibilityDecision.INDETERMINATE,
        EligibilityReason.UNSUPPORTED_FACTS,
    )


@pytest.mark.parametrize(
    "outcome", [VerificationOutcome.FAILED, VerificationOutcome.UNKNOWN]
)
def test_verified投影与审核结果不一致时不确定(outcome):
    _assert_result(
        _facts(verification_outcome=outcome),
        EligibilityDecision.INDETERMINATE,
        EligibilityReason.VERIFICATION_FACTS_INCONSISTENT,
    )


def test_审核主体错绑时不确定():
    _assert_result(
        _facts(verification_subject_user_ref=8),
        EligibilityDecision.INDETERMINATE,
        EligibilityReason.VERIFICATION_FACTS_INCONSISTENT,
    )


def test_审核决定已过期时不确定():
    _assert_result(
        _facts(verification_is_current=False),
        EligibilityDecision.INDETERMINATE,
        EligibilityReason.FACTS_STALE,
    )


def test_account_class缺失时不确定():
    _assert_result(
        _facts(account_class=None),
        EligibilityDecision.INDETERMINATE,
        EligibilityReason.ACCOUNT_CLASS_UNKNOWN,
    )


@pytest.mark.parametrize(
    "account_class",
    [
        AccountClass.STAFF,
        AccountClass.SERVICE,
        AccountClass.TEST,
        AccountClass.AUTOMATION,
    ],
)
def test_禁止账号分类不合格(account_class):
    _assert_result(
        _facts(account_class=account_class),
        EligibilityDecision.INELIGIBLE,
        EligibilityReason.ACCOUNT_CLASS_FORBIDDEN,
    )


def test_明确UNKNOWN账号分类仍不确定():
    _assert_result(
        _facts(account_class=AccountClass.UNKNOWN),
        EligibilityDecision.INDETERMINATE,
        EligibilityReason.ACCOUNT_CLASS_UNKNOWN,
    )


@pytest.mark.parametrize(
    "field",
    [
        "classification_decision_ref",
        "classification_subject_user_ref",
        "classification_version",
        "classification_is_current",
    ],
)
def test_分类决定事实缺失时不确定(field):
    _assert_result(
        _facts(**{field: None}),
        EligibilityDecision.INDETERMINATE,
        EligibilityReason.CLASSIFICATION_FACTS_INCONSISTENT,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"account_class": "natural_person"},
        {"classification_decision_ref": "not-a-uuid"},
        {"classification_decision_ref": NON_V7_REF},
        {"classification_subject_user_ref": True},
        {"classification_version": True},
        {"classification_version": 0},
        {"classification_is_current": 1},
    ],
)
def test_分类决定事实类型严格(changes):
    _assert_result(
        _facts(**changes),
        EligibilityDecision.INDETERMINATE,
        EligibilityReason.UNSUPPORTED_FACTS,
    )


def test_分类主体错绑时不确定():
    _assert_result(
        _facts(classification_subject_user_ref=8),
        EligibilityDecision.INDETERMINATE,
        EligibilityReason.CLASSIFICATION_FACTS_INCONSISTENT,
    )


def test_分类决定已过期时不确定():
    _assert_result(
        _facts(classification_is_current=False),
        EligibilityDecision.INDETERMINATE,
        EligibilityReason.FACTS_STALE,
    )


def test_输入与结果均不可变且无动态属性():
    facts = _facts()
    result = RegistrationEligibilityPolicy.evaluate(facts)

    assert not hasattr(facts, "__dict__")
    assert not hasattr(result, "__dict__")
    with pytest.raises((FrozenInstanceError, AttributeError)):
        facts.user_ref = 9
    with pytest.raises((FrozenInstanceError, AttributeError)):
        result.reason = EligibilityReason.FACTS_STALE


@pytest.mark.parametrize(
    ("facts", "decision", "reason"),
    [
        (
            _facts(user_ref=True, exists=False),
            EligibilityDecision.INDETERMINATE,
            EligibilityReason.UNSUPPORTED_FACTS,
        ),
        (
            _facts(exists=False, role=None, status="active"),
            EligibilityDecision.INELIGIBLE,
            EligibilityReason.USER_NOT_FOUND,
        ),
        (
            _facts(role=None, status="active"),
            EligibilityDecision.INDETERMINATE,
            EligibilityReason.FACTS_UNAVAILABLE,
        ),
        (
            _facts(role="member", status=RegistrationAccountStatus.DISABLED),
            EligibilityDecision.INDETERMINATE,
            EligibilityReason.UNSUPPORTED_FACTS,
        ),
        (
            _facts(
                role=RegistrationUserRole.OTHER,
                status=RegistrationAccountStatus.DISABLED,
            ),
            EligibilityDecision.INELIGIBLE,
            EligibilityReason.ROLE_NOT_MEMBER,
        ),
        (
            _facts(
                status=RegistrationAccountStatus.DISABLED,
                verify_status=VerificationStatus.SUBMITTED,
            ),
            EligibilityDecision.INELIGIBLE,
            EligibilityReason.ACCOUNT_NOT_ACTIVE,
        ),
        (
            _facts(
                verify_status=VerificationStatus.SUBMITTED,
                verification_is_current=False,
            ),
            EligibilityDecision.INELIGIBLE,
            EligibilityReason.IDENTITY_NOT_VERIFIED,
        ),
        (
            _facts(
                verification_is_current=False,
                classification_is_current=False,
            ),
            EligibilityDecision.INDETERMINATE,
            EligibilityReason.FACTS_STALE,
        ),
        (
            _facts(
                classification_is_current=False,
                account_class=AccountClass.STAFF,
            ),
            EligibilityDecision.INDETERMINATE,
            EligibilityReason.FACTS_STALE,
        ),
        (
            _facts(
                classification_subject_user_ref=8,
                account_class=AccountClass.STAFF,
            ),
            EligibilityDecision.INDETERMINATE,
            EligibilityReason.CLASSIFICATION_FACTS_INCONSISTENT,
        ),
    ],
)
def test_冲突事实固定完整判定优先级(facts, decision, reason):
    _assert_result(facts, decision, reason)
