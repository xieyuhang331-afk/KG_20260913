from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.health_assessment.domain import AssessmentFacts, EvaluationContext
from app.modules.health_assessment.evaluator import (
    INCLUDED_RULE_IDS,
    DEFERRED_RULE_IDS,
    evaluate_cn_adult_baseline_v1,
)


INDEPENDENT_INCLUDED_RULE_IDS = {
    "BP01", "BP02", "BP03", "BP04", "BP05", "BP06", "BP08",
    "GL01", "GL02", "GL03", "GL04", "GL05", "GL06", "GL07", "GL08", "GL09", "GL12",
    "LP01", "LP02", "LP03", "LP04", "LP05", "LP06", "LP07", "LP08", "LP09", "LP13", "LP14",
    "BD01", "BD02", "BD03", "BD07", "BD08", "BD09", "CM01",
}
INDEPENDENT_DEFERRED_RULE_IDS = {
    "BP07", "BP09", "BP10", "BP11", "BP12", "BP13",
    "GL10", "GL11", "GL13", "GL14", "GL15", "GL16", "GL17", "GL18", "GL19", "GL20", "GL21",
    "LP10", "LP11", "LP12", "BD04", "BD05", "BD06",
}


def _context(**overrides) -> EvaluationContext:
    values = {
        "age_years": 45,
        "sex": "MALE",
        "pregnancy_status": "NOT_APPLICABLE",
        "lactation_status": "NOT_APPLICABLE",
        "ascvd_risk_profile": "LOW",
        "acute_symptom_codes": (),
    }
    values.update(overrides)
    return EvaluationContext(**values)


def _evaluate(values: dict[str, str], *, contexts=None, units=None, context=None):
    standard_units = {
        "systolic_bp": "mmHg", "diastolic_bp": "mmHg",
        "fasting_glucose": "mmol/L", "postprandial_glucose_2h": "mmol/L",
        "random_glucose": "mmol/L", "hba1c": "%",
        "total_cholesterol": "mmol/L", "triglycerides": "mmol/L",
        "ldl_cholesterol": "mmol/L", "hdl_cholesterol": "mmol/L",
        "height": "cm", "weight": "kg", "waist": "cm", "bmi": "kg/m2",
    }
    standard_units.update(units or {})
    facts = AssessmentFacts.from_strings(
        values,
        units=standard_units,
        measurement_contexts=contexts or {},
    )
    return evaluate_cn_adult_baseline_v1(facts, context or _context())


def test_35项纳入和23项延期目录必须精确且互斥():
    assert set(INCLUDED_RULE_IDS) == INDEPENDENT_INCLUDED_RULE_IDS
    assert set(DEFERRED_RULE_IDS) == INDEPENDENT_DEFERRED_RULE_IDS
    assert len(INCLUDED_RULE_IDS) == 35
    assert len(DEFERRED_RULE_IDS) == 23
    assert not set(INCLUDED_RULE_IDS) & set(DEFERRED_RULE_IDS)


@pytest.mark.parametrize(
    ("values", "contexts", "module", "expected"),
    [
        ({"systolic_bp": "119", "diastolic_bp": "79"}, {"blood_pressure": "OFFICE"}, "BLOOD_PRESSURE_CARDIOVASCULAR", "WITHIN_RANGE"),
        ({"systolic_bp": "120", "diastolic_bp": "79"}, {"blood_pressure": "OFFICE"}, "BLOOD_PRESSURE_CARDIOVASCULAR", "ATTENTION"),
        ({"systolic_bp": "140", "diastolic_bp": "79"}, {"blood_pressure": "OFFICE"}, "BLOOD_PRESSURE_CARDIOVASCULAR", "ATTENTION"),
        ({"systolic_bp": "160", "diastolic_bp": "80"}, {"blood_pressure": "OFFICE"}, "BLOOD_PRESSURE_CARDIOVASCULAR", "ATTENTION"),
        ({"systolic_bp": "180", "diastolic_bp": "80"}, {"blood_pressure": "OFFICE"}, "BLOOD_PRESSURE_CARDIOVASCULAR", "HIGH_RISK"),
        ({"fasting_glucose": "2.9"}, {"fasting_glucose": "FASTING_VENOUS"}, "GLUCOSE_METABOLISM", "HIGH_RISK"),
        ({"fasting_glucose": "3.0"}, {"fasting_glucose": "FASTING_VENOUS"}, "GLUCOSE_METABOLISM", "ATTENTION"),
        ({"fasting_glucose": "3.9"}, {"fasting_glucose": "FASTING_VENOUS"}, "GLUCOSE_METABOLISM", "WITHIN_RANGE"),
        ({"fasting_glucose": "6.1"}, {"fasting_glucose": "FASTING_VENOUS"}, "GLUCOSE_METABOLISM", "ATTENTION"),
        ({"fasting_glucose": "7.0"}, {"fasting_glucose": "FASTING_VENOUS"}, "GLUCOSE_METABOLISM", "ATTENTION"),
        ({"postprandial_glucose_2h": "7.7"}, {"postprandial_glucose_2h": "OGTT_2H_VENOUS"}, "GLUCOSE_METABOLISM", "WITHIN_RANGE"),
        ({"postprandial_glucose_2h": "7.8"}, {"postprandial_glucose_2h": "OGTT_2H_VENOUS"}, "GLUCOSE_METABOLISM", "ATTENTION"),
        ({"hba1c": "5.6"}, {"hba1c": "LAB"}, "GLUCOSE_METABOLISM", "WITHIN_RANGE"),
        ({"hba1c": "5.7"}, {"hba1c": "LAB"}, "GLUCOSE_METABOLISM", "ATTENTION"),
        ({"total_cholesterol": "5.1"}, {"lipids": "FASTING_LAB"}, "LIPID_METABOLISM", "WITHIN_RANGE"),
        ({"total_cholesterol": "5.2"}, {"lipids": "FASTING_LAB"}, "LIPID_METABOLISM", "ATTENTION"),
        ({"triglycerides": "1.7"}, {"lipids": "FASTING_LAB"}, "LIPID_METABOLISM", "ATTENTION"),
        ({"ldl_cholesterol": "3.4"}, {"lipids": "FASTING_LAB"}, "LIPID_METABOLISM", "ATTENTION"),
        ({"bmi": "18.4"}, {}, "WEIGHT_ABDOMINAL_OBESITY", "ATTENTION"),
        ({"bmi": "18.5"}, {}, "WEIGHT_ABDOMINAL_OBESITY", "WITHIN_RANGE"),
        ({"bmi": "28.0"}, {}, "WEIGHT_ABDOMINAL_OBESITY", "ATTENTION"),
    ],
)
def test_21组GoldenCase作为独立正式期望(values, contexts, module, expected):
    result = _evaluate(values, contexts=contexts)
    assert result.by_module(module).risk_level == expected


def test_规则使用未舍入Decimal并精确处理半开区间():
    below = _evaluate({"bmi": "23.999999"})
    boundary = _evaluate({"bmi": "24.000000"})
    above = _evaluate({"bmi": "24.000001"})
    assert below.by_module("WEIGHT_ABDOMINAL_OBESITY").risk_level == "WITHIN_RANGE"
    assert boundary.by_module("WEIGHT_ABDOMINAL_OBESITY").risk_level == "ATTENTION"
    assert above.by_module("WEIGHT_ABDOMINAL_OBESITY").risk_level == "ATTENTION"
    assert below.evidence_value("bmi") == Decimal("23.999999")


def test_单位或测量场景缺失必须fail_closed为NOT_ASSESSED():
    wrong_unit = _evaluate(
        {"fasting_glucose": "5.2"},
        contexts={"fasting_glucose": "FASTING_VENOUS"},
        units={"fasting_glucose": "mg/dL"},
    )
    missing_context = _evaluate({"fasting_glucose": "5.2"})
    assert wrong_unit.by_module("GLUCOSE_METABOLISM").risk_level == "NOT_ASSESSED"
    assert "UNIT_INVALID" in wrong_unit.by_module("GLUCOSE_METABOLISM").reason_codes
    assert missing_context.by_module("GLUCOSE_METABOLISM").risk_level == "NOT_ASSESSED"
    assert "GLUCOSE_MEASUREMENT_CONTEXT_MISSING" in missing_context.by_module("GLUCOSE_METABOLISM").reason_codes


def test_ASCVD资料缺失不得把LDL声明为正常():
    result = _evaluate(
        {"ldl_cholesterol": "2.5"},
        contexts={"lipids": "FASTING_LAB"},
        context=_context(ascvd_risk_profile=None),
    )
    module = result.by_module("LIPID_METABOLISM")
    assert module.risk_level == "NOT_ASSESSED"
    assert "ASCVD_RISK_PROFILE_MISSING" in module.reason_codes


def test_孕期或哺乳期BMI腰围规则必须NOT_ASSESSED():
    result = _evaluate(
        {"bmi": "29", "waist": "95"},
        context=_context(sex="FEMALE", pregnancy_status="PREGNANT", lactation_status="NOT_LACTATING"),
    )
    module = result.by_module("WEIGHT_ABDOMINAL_OBESITY")
    assert module.risk_level == "NOT_ASSESSED"
    assert "PREGNANCY_OR_LACTATION_EXCLUDED" in module.reason_codes


def test_四模块固定输出且总体取最高风险():
    result = _evaluate(
        {
            "systolic_bp": "180",
            "diastolic_bp": "70",
            "fasting_glucose": "5.2",
            "total_cholesterol": "5.1",
            "bmi": "22",
        },
        contexts={
            "blood_pressure": "OFFICE",
            "fasting_glucose": "FASTING_VENOUS",
            "lipids": "FASTING_LAB",
        },
    )
    assert tuple(item.module_code for item in result.module_results) == (
        "BLOOD_PRESSURE_CARDIOVASCULAR",
        "GLUCOSE_METABOLISM",
        "LIPID_METABOLISM",
        "WEIGHT_ABDOMINAL_OBESITY",
    )
    assert result.overall_risk == "HIGH_RISK"
    assert result.high_risk_trigger_codes == ("HR-BP-SEVERE",)


def test_延期规则不得存在于执行目录或产生高风险():
    result = _evaluate(
        {"systolic_bp": "160", "diastolic_bp": "80", "fasting_glucose": "22.3"},
        contexts={"blood_pressure": "OFFICE"},
    )
    assert result.by_module("BLOOD_PRESSURE_CARDIOVASCULAR").risk_level == "ATTENTION"
    assert "SB-BP-160" not in result.high_risk_trigger_codes
    assert "HR-GLUCOSE-22.2" not in result.high_risk_trigger_codes
