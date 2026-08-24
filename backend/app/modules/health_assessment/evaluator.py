from __future__ import annotations

from decimal import Decimal
from types import MappingProxyType

from .domain import (
    AssessmentEvaluation,
    AssessmentFacts,
    EvaluationContext,
    MODULE_CODES,
    ModuleEvaluation,
    RISK_RANK,
)


RULE_SET_CODE = "CN_ADULT_BASELINE_V1"
INCLUDED_RULE_IDS = (
    "BP01", "BP02", "BP03", "BP04", "BP05", "BP06", "BP08",
    "GL01", "GL02", "GL03", "GL04", "GL05", "GL06", "GL07", "GL08", "GL09", "GL12",
    "LP01", "LP02", "LP03", "LP04", "LP05", "LP06", "LP07", "LP08", "LP09", "LP13", "LP14",
    "BD01", "BD02", "BD03", "BD07", "BD08", "BD09", "CM01",
)
DEFERRED_RULE_IDS = (
    "BP07", "BP09", "BP10", "BP11", "BP12", "BP13",
    "GL10", "GL11", "GL13", "GL14", "GL15", "GL16", "GL17", "GL18", "GL19", "GL20", "GL21",
    "LP10", "LP11", "LP12", "BD04", "BD05", "BD06",
)

STANDARD_UNITS = MappingProxyType(
    {
        "systolic_bp": "mmHg",
        "diastolic_bp": "mmHg",
        "fasting_glucose": "mmol/L",
        "postprandial_glucose_2h": "mmol/L",
        "random_glucose": "mmol/L",
        "hba1c": "%",
        "total_cholesterol": "mmol/L",
        "triglycerides": "mmol/L",
        "ldl_cholesterol": "mmol/L",
        "hdl_cholesterol": "mmol/L",
        "height": "cm",
        "weight": "kg",
        "waist": "cm",
        "bmi": "kg/m2",
    }
)
_SEVERE_GLUCOSE_SYMPTOMS = frozenset(
    {"REQUIRES_ASSISTANCE", "ALTERED_CONSCIOUSNESS", "UNSAFE_SWALLOWING"}
)


def _module(
    code: str,
    risk: str,
    reasons: list[str],
    evidence: list[str],
    *,
    triggers: list[str] | None = None,
) -> ModuleEvaluation:
    unique_reasons = tuple(dict.fromkeys(reasons))
    trigger_codes = tuple(dict.fromkeys(triggers or ()))
    return ModuleEvaluation(
        module_code=code,
        risk_level=risk,
        reason_codes=unique_reasons,
        message_codes=tuple(f"{code}_{item}" for item in unique_reasons),
        evidence_codes=tuple(dict.fromkeys(evidence)),
        high_risk_trigger_codes=trigger_codes,
    )


def _valid(facts: AssessmentFacts, code: str) -> bool:
    fact = facts.get(code)
    return bool(fact is not None and fact.unit == STANDARD_UNITS[code])


def _blood_pressure(facts: AssessmentFacts) -> ModuleEvaluation:
    code = MODULE_CODES[0]
    sbp = facts.get("systolic_bp")
    dbp = facts.get("diastolic_bp")
    if sbp is None and dbp is None:
        return _module(code, "NOT_ASSESSED", ["BP_INPUT_MISSING"], [])
    if not ((sbp is None or _valid(facts, "systolic_bp")) and (dbp is None or _valid(facts, "diastolic_bp"))):
        return _module(code, "NOT_ASSESSED", ["UNIT_INVALID"], [])
    context = facts.measurement_contexts.get("blood_pressure")
    if context is None:
        return _module(code, "NOT_ASSESSED", ["BP_MEASUREMENT_CONTEXT_MISSING"], [])
    evidence = [item for item, fact in (("systolic_bp", sbp), ("diastolic_bp", dbp)) if fact]
    sbp_value = sbp.value if sbp else None
    dbp_value = dbp.value if dbp else None
    if context == "OFFICE":
        if (sbp_value is not None and sbp_value >= Decimal("180")) or (
            dbp_value is not None and dbp_value >= Decimal("110")
        ):
            return _module(code, "HIGH_RISK", ["BP_SEVERE_VALUE"], evidence, triggers=["HR-BP-SEVERE"])
        if (sbp_value is not None and sbp_value >= Decimal("120")) or (
            dbp_value is not None and dbp_value >= Decimal("80")
        ):
            reasons = ["BP_ATTENTION"]
            if (sbp_value is not None and sbp_value >= Decimal("140")) or (
                dbp_value is not None and dbp_value >= Decimal("90")
            ):
                reasons.append("BP_DIAGNOSTIC_REFERENCE_REACHED")
            return _module(code, "ATTENTION", reasons, evidence)
        if sbp_value is not None and dbp_value is not None:
            return _module(code, "WITHIN_RANGE", ["BP_WITHIN_RANGE"], evidence)
        return _module(code, "NOT_ASSESSED", ["BP_PAIRED_MEASUREMENT_MISSING"], evidence)
    thresholds = {
        "HOME_AVERAGE": (Decimal("135"), Decimal("85")),
        "ABPM_24H_AVERAGE": (Decimal("130"), Decimal("80")),
        "ABPM_DAY_AVERAGE": (Decimal("135"), Decimal("85")),
        "ABPM_NIGHT_AVERAGE": (Decimal("120"), Decimal("70")),
    }
    threshold = thresholds.get(context)
    if threshold is None or sbp_value is None or dbp_value is None:
        return _module(code, "NOT_ASSESSED", ["BP_MEASUREMENT_CONTEXT_INVALID"], evidence)
    risk = "ATTENTION" if sbp_value >= threshold[0] or dbp_value >= threshold[1] else "WITHIN_RANGE"
    reason = "BP_DIAGNOSTIC_REFERENCE_REACHED" if risk == "ATTENTION" else "BP_WITHIN_RANGE"
    return _module(code, risk, [reason], evidence)


def _glucose(facts: AssessmentFacts, context: EvaluationContext) -> ModuleEvaluation:
    code = MODULE_CODES[1]
    reasons: list[str] = []
    evidence: list[str] = []
    risk = "NOT_ASSESSED"
    triggers: list[str] = []
    if set(context.acute_symptom_codes) & _SEVERE_GLUCOSE_SYMPTOMS:
        return _module(code, "HIGH_RISK", ["GLUCOSE_SEVERE_SYMPTOM"], [], triggers=["HR-GLUCOSE-SYMPTOM"])
    specifications = (
        ("fasting_glucose", "FASTING_VENOUS", Decimal("6.1"), Decimal("7.0")),
        ("postprandial_glucose_2h", "OGTT_2H_VENOUS", Decimal("7.8"), Decimal("11.1")),
    )
    for fact_code, required_context, attention, diagnostic in specifications:
        fact = facts.get(fact_code)
        if fact is None:
            continue
        evidence.append(fact_code)
        if not _valid(facts, fact_code):
            reasons.append("UNIT_INVALID")
            continue
        if facts.context_for(fact_code) != required_context:
            reasons.append("GLUCOSE_MEASUREMENT_CONTEXT_MISSING")
            continue
        if fact.value < Decimal("3.0"):
            risk = "HIGH_RISK"
            reasons.append("GLUCOSE_SEVERE_LOW")
            triggers.append("HR-GLUCOSE-LOW")
        elif fact.value < Decimal("3.9"):
            risk = max((risk, "ATTENTION"), key=RISK_RANK.__getitem__)
            reasons.append("GLUCOSE_LOW_ATTENTION")
        elif fact.value < attention:
            risk = max((risk, "WITHIN_RANGE"), key=RISK_RANK.__getitem__)
            reasons.append("GLUCOSE_WITHIN_RANGE")
        else:
            risk = max((risk, "ATTENTION"), key=RISK_RANK.__getitem__)
            reasons.append("GLUCOSE_DIAGNOSTIC_REFERENCE_REACHED" if fact.value >= diagnostic else "GLUCOSE_ATTENTION")
    hba1c = facts.get("hba1c")
    if hba1c is not None:
        evidence.append("hba1c")
        if not _valid(facts, "hba1c"):
            reasons.append("UNIT_INVALID")
        elif facts.context_for("hba1c") != "LAB":
            reasons.append("GLUCOSE_MEASUREMENT_CONTEXT_MISSING")
        elif hba1c.value < Decimal("5.7"):
            risk = max((risk, "WITHIN_RANGE"), key=RISK_RANK.__getitem__)
            reasons.append("HBA1C_WITHIN_RANGE")
        else:
            risk = max((risk, "ATTENTION"), key=RISK_RANK.__getitem__)
            reasons.append("HBA1C_DIAGNOSTIC_REFERENCE_REACHED" if hba1c.value >= Decimal("6.5") else "HBA1C_ATTENTION")
    if not evidence and risk == "NOT_ASSESSED":
        reasons.append("GLUCOSE_INPUT_MISSING")
    elif risk == "NOT_ASSESSED" and not reasons:
        reasons.append("GLUCOSE_MEASUREMENT_CONTEXT_MISSING")
    return _module(code, risk, reasons, evidence, triggers=triggers)


def _lipids(facts: AssessmentFacts, context: EvaluationContext) -> ModuleEvaluation:
    code = MODULE_CODES[2]
    lipid_codes = ("total_cholesterol", "triglycerides", "ldl_cholesterol", "hdl_cholesterol")
    present = [item for item in lipid_codes if facts.get(item) is not None]
    if not present:
        return _module(code, "NOT_ASSESSED", ["LIPID_INPUT_MISSING"], [])
    if facts.measurement_contexts.get("lipids") != "FASTING_LAB":
        return _module(code, "NOT_ASSESSED", ["LIPID_MEASUREMENT_CONTEXT_MISSING"], present)
    reasons: list[str] = []
    assessed = 0
    risk = "NOT_ASSESSED"
    for fact_code in present:
        fact = facts.get(fact_code)
        if not _valid(facts, fact_code):
            reasons.append("UNIT_INVALID")
            continue
        if fact_code == "ldl_cholesterol" and context.ascvd_risk_profile is None:
            reasons.append("ASCVD_RISK_PROFILE_MISSING")
            continue
        assessed += 1
        item_risk = "WITHIN_RANGE"
        if fact_code == "total_cholesterol" and fact.value >= Decimal("5.2"):
            item_risk = "ATTENTION"
        elif fact_code == "triglycerides" and fact.value >= Decimal("1.7"):
            item_risk = "ATTENTION"
        elif fact_code == "hdl_cholesterol" and fact.value < Decimal("1.0"):
            item_risk = "ATTENTION"
        elif fact_code == "ldl_cholesterol":
            targets = {
                "LOW": Decimal("3.4"),
                "HIGH": Decimal("2.6"),
                "VERY_HIGH": Decimal("1.8"),
                "ULTRA_HIGH": Decimal("1.4"),
            }
            target = targets.get(context.ascvd_risk_profile or "")
            if target is None:
                reasons.append("ASCVD_RISK_PROFILE_INVALID")
                assessed -= 1
                continue
            if fact.value >= target:
                item_risk = "ATTENTION"
        risk = max((risk, item_risk), key=RISK_RANK.__getitem__)
        reasons.append(f"{fact_code.upper()}_{'ATTENTION' if item_risk == 'ATTENTION' else 'WITHIN_RANGE'}")
    if assessed == 0:
        risk = "NOT_ASSESSED"
    return _module(code, risk, reasons, present)


def _body(facts: AssessmentFacts, context: EvaluationContext) -> ModuleEvaluation:
    code = MODULE_CODES[3]
    if context.pregnancy_status == "PREGNANT" or context.lactation_status == "LACTATING":
        return _module(code, "NOT_ASSESSED", ["PREGNANCY_OR_LACTATION_EXCLUDED"], [])
    reasons: list[str] = []
    evidence: list[str] = []
    risk = "NOT_ASSESSED"
    bmi = facts.get("bmi")
    if bmi is not None:
        if _valid(facts, "bmi"):
            evidence.append("bmi")
        else:
            reasons.append("UNIT_INVALID")
            bmi = None
    if bmi is None:
        height, weight = facts.get("height"), facts.get("weight")
        if height is not None or weight is not None:
            if height is None or weight is None or not _valid(facts, "height") or not _valid(facts, "weight"):
                reasons.append("HEIGHT_WEIGHT_INCOMPLETE")
            elif height.value <= 0 or weight.value <= 0:
                reasons.append("HEIGHT_WEIGHT_INVALID")
            else:
                bmi = type(height)("bmi", weight.value / ((height.value / Decimal("100")) ** 2), "kg/m2", None)
                evidence.extend(("height", "weight", "bmi"))
    if bmi is not None:
        if Decimal("18.5") <= bmi.value < Decimal("24.0"):
            risk = "WITHIN_RANGE"
            reasons.append("BMI_WITHIN_RANGE")
        else:
            risk = "ATTENTION"
            reasons.append("BMI_ATTENTION")
    waist = facts.get("waist")
    if waist is not None:
        evidence.append("waist")
        if not _valid(facts, "waist"):
            reasons.append("UNIT_INVALID")
        elif context.sex not in {"MALE", "FEMALE"}:
            reasons.append("SEX_CONTEXT_MISSING")
        else:
            threshold = Decimal("85") if context.sex == "MALE" else Decimal("80")
            item_risk = "ATTENTION" if waist.value >= threshold else "WITHIN_RANGE"
            risk = max((risk, item_risk), key=RISK_RANK.__getitem__)
            reasons.append("WAIST_ATTENTION" if item_risk == "ATTENTION" else "WAIST_WITHIN_RANGE")
    if not evidence and not reasons:
        reasons.append("BODY_INPUT_MISSING")
    return _module(code, risk, reasons, evidence)


def evaluate_cn_adult_baseline_v1(
    facts: AssessmentFacts,
    context: EvaluationContext,
) -> AssessmentEvaluation:
    modules = (
        _blood_pressure(facts),
        _glucose(facts, context),
        _lipids(facts, context),
        _body(facts, context),
    )
    overall = max((item.risk_level for item in modules), key=RISK_RANK.__getitem__)
    triggers = tuple(
        dict.fromkeys(trigger for item in modules for trigger in item.high_risk_trigger_codes)
    )
    return AssessmentEvaluation(
        rule_set_code=RULE_SET_CODE,
        module_results=modules,
        overall_risk=overall,
        high_risk_trigger_codes=triggers,
        _evidence_values=MappingProxyType({code: item.value for code, item in facts.values.items()}),
    )
