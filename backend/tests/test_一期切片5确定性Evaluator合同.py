from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.modules.health_assessment.service import (
    _module_measurement_contexts,
    build_current_evaluation_context,
)


def _identity(**overrides):
    values = {
        "gender": "FEMALE",
        "birth_date": date(1990, 8, 24),
        "source_version": 7,
        "evidence_status": "VERIFIED",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _profile(**overrides):
    values = {
        "pregnancy_status": "NOT_PREGNANT",
        "lactation_status": "NOT_LACTATING",
        "symptoms": [],
    }
    values.update(overrides)
    return values


def test_年龄必须从current身份出生日期和冻结快照时点精确计算():
    before_birthday = build_current_evaluation_context(
        _identity(),
        _profile(),
        as_of=datetime(2026, 8, 23, 15, 59, tzinfo=timezone.utc),
    )
    on_birthday = build_current_evaluation_context(
        _identity(),
        _profile(),
        as_of=datetime(2026, 8, 23, 16, 0, tzinfo=timezone.utc),
    )
    assert before_birthday.age_years == 35
    assert on_birthday.age_years == 36
    assert before_birthday.sex == "FEMALE"


def test_身份摘要非current已验证证据必须fail_closed():
    with pytest.raises(RuntimeError, match="RULE_EVALUATION_UNAVAILABLE"):
        build_current_evaluation_context(
            _identity(evidence_status="UNKNOWN"),
            _profile(),
            as_of=datetime(2026, 8, 23, tzinfo=timezone.utc),
        )


def test_未满18岁不得进入成人规则集():
    with pytest.raises(RuntimeError, match="RULE_EVALUATION_UNAVAILABLE"):
        build_current_evaluation_context(
            _identity(birth_date=date(2010, 1, 1)),
            _profile(),
            as_of=datetime(2026, 8, 23, tzinfo=timezone.utc),
        )


def test_冻结Profile中的严重低血糖症状使用批准代码且不读取自由文本():
    context = build_current_evaluation_context(
        _identity(),
        _profile(
            symptoms=[
                {"code": "altered_consciousness", "severity": "SEVERE", "note": "synthetic"},
                {"code": "unrelated", "severity": "SEVERE", "note": "synthetic"},
            ]
        ),
        as_of=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )
    assert context.acute_symptom_codes == ("ALTERED_CONSCIOUSNESS",)
    assert context.ascvd_risk_profile is None


def test_孕哺状态直接来自冻结Profile且未知值不推断():
    context = build_current_evaluation_context(
        _identity(gender="MALE"),
        _profile(pregnancy_status="UNKNOWN", lactation_status="UNKNOWN"),
        as_of=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )
    assert context.pregnancy_status == "UNKNOWN"
    assert context.lactation_status == "UNKNOWN"


def test_血压仅在场景和测量时点均一致时形成模块场景():
    measured = datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)
    valid = _module_measurement_contexts(
        {"systolic_bp", "diastolic_bp"},
        {"systolic_bp": "OFFICE", "diastolic_bp": "OFFICE"},
        {"systolic_bp": measured, "diastolic_bp": measured},
    )
    assert valid["blood_pressure"] == "OFFICE"

    for contexts, times in (
        (
            {"systolic_bp": "OFFICE", "diastolic_bp": "HOME_AVERAGE"},
            {"systolic_bp": measured, "diastolic_bp": measured},
        ),
        (
            {"systolic_bp": "OFFICE", "diastolic_bp": "OFFICE"},
            {
                "systolic_bp": measured,
                "diastolic_bp": datetime(2026, 8, 23, 8, 1, tzinfo=timezone.utc),
            },
        ),
        ({"systolic_bp": "OFFICE"}, {"systolic_bp": measured}),
    ):
        assert "blood_pressure" not in _module_measurement_contexts(
            {"systolic_bp", "diastolic_bp"}, contexts, times
        )


def test_血脂仅在全部在场事实均明确为FASTING_LAB时形成模块场景():
    valid = _module_measurement_contexts(
        {"total_cholesterol", "triglycerides", "ldl_cholesterol"},
        {
            "total_cholesterol": "FASTING_LAB",
            "triglycerides": "FASTING_LAB",
            "ldl_cholesterol": "FASTING_LAB",
        },
        {},
    )
    assert valid["lipids"] == "FASTING_LAB"

    conflicting = _module_measurement_contexts(
        {"total_cholesterol", "triglycerides"},
        {"total_cholesterol": "FASTING_LAB", "triglycerides": "LAB"},
        {},
    )
    assert "lipids" not in conflicting
    missing = _module_measurement_contexts(
        {"total_cholesterol", "triglycerides"},
        {"total_cholesterol": "FASTING_LAB"},
        {},
    )
    assert "lipids" not in missing
