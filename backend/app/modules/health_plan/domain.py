from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


MODULE_CODES = (
    "BLOOD_PRESSURE_CARDIOVASCULAR",
    "GLUCOSE_METABOLISM",
    "LIPID_METABOLISM",
    "WEIGHT_ABDOMINAL_OBESITY",
)


class GenerationState(StrEnum):
    REQUESTED = "REQUESTED"
    GENERATING = "GENERATING"
    GENERATION_FAILED = "GENERATION_FAILED"
    IN_REVIEW = "IN_REVIEW"
    NEEDS_CORRECTION = "NEEDS_CORRECTION"
    USER_DECISION_PENDING = "USER_DECISION_PENDING"
    NEEDS_EXPLANATION = "NEEDS_EXPLANATION"
    DECLINED = "DECLINED"
    REJECTED = "REJECTED"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"


class ReviewDecision(StrEnum):
    APPROVED = "APPROVED"
    NEEDS_CORRECTION = "NEEDS_CORRECTION"
    REJECTED = "REJECTED"


class UserDecision(StrEnum):
    ACCEPT = "ACCEPT"
    NEEDS_EXPLANATION = "NEEDS_EXPLANATION"
    DECLINE = "DECLINE"


_STATE_TRANSITIONS: Mapping[tuple[GenerationState, object], GenerationState] = MappingProxyType(
    {
        (GenerationState.REQUESTED, GenerationState.GENERATING): GenerationState.GENERATING,
        (GenerationState.GENERATING, GenerationState.IN_REVIEW): GenerationState.IN_REVIEW,
        (GenerationState.GENERATING, GenerationState.GENERATION_FAILED): GenerationState.GENERATION_FAILED,
        (GenerationState.GENERATION_FAILED, GenerationState.GENERATING): GenerationState.GENERATING,
        (GenerationState.IN_REVIEW, ReviewDecision.APPROVED): GenerationState.USER_DECISION_PENDING,
        (GenerationState.IN_REVIEW, ReviewDecision.NEEDS_CORRECTION): GenerationState.NEEDS_CORRECTION,
        (GenerationState.IN_REVIEW, ReviewDecision.REJECTED): GenerationState.REJECTED,
        (GenerationState.NEEDS_CORRECTION, GenerationState.GENERATING): GenerationState.GENERATING,
        (GenerationState.USER_DECISION_PENDING, UserDecision.ACCEPT): GenerationState.ACTIVE,
        (
            GenerationState.USER_DECISION_PENDING,
            UserDecision.NEEDS_EXPLANATION,
        ): GenerationState.NEEDS_EXPLANATION,
        (GenerationState.USER_DECISION_PENDING, UserDecision.DECLINE): GenerationState.DECLINED,
        (
            GenerationState.NEEDS_EXPLANATION,
            GenerationState.USER_DECISION_PENDING,
        ): GenerationState.USER_DECISION_PENDING,
        (GenerationState.ACTIVE, GenerationState.SUPERSEDED): GenerationState.SUPERSEDED,
    }
)


def next_generation_state(current: GenerationState, event: object) -> GenerationState:
    try:
        return _STATE_TRANSITIONS[(current, event)]
    except KeyError:
        raise ValueError("STATE_CONFLICT") from None


@dataclass(frozen=True, slots=True)
class EligibilityFacts:
    tenant_service_ready: bool
    service_case_current: bool
    consent_current: bool
    primary_therapist_current: bool
    assembly_ready: bool
    assessment_completed: bool
    assessment_disputed: bool
    assessment_superseded: bool
    high_risk_blocking: bool
    published_template_available: bool
    active_generation_exists: bool
    active_plan_conflict: bool


@dataclass(frozen=True, slots=True)
class EligibilityResult:
    eligible: bool
    blocking_codes: tuple[str, ...]


_BLOCKING_RULES = (
    ("tenant_service_ready", False, "TENANT_NOT_SERVICE_READY"),
    ("service_case_current", False, "SERVICE_CASE_NOT_CURRENT"),
    ("consent_current", False, "CONSENT_NOT_CURRENT"),
    ("primary_therapist_current", False, "PRIMARY_THERAPIST_NOT_CURRENT"),
    ("assembly_ready", False, "ASSESSMENT_INPUT_NOT_READY"),
    ("assessment_completed", False, "ASSESSMENT_NOT_COMPLETED"),
    ("assessment_disputed", True, "ASSESSMENT_DISPUTED"),
    ("assessment_superseded", True, "ASSESSMENT_SUPERSEDED"),
    ("high_risk_blocking", True, "HIGH_RISK_BLOCKING"),
    ("published_template_available", False, "TEMPLATE_NOT_AVAILABLE"),
    ("active_generation_exists", True, "ACTIVE_GENERATION_EXISTS"),
    ("active_plan_conflict", True, "ACTIVE_PLAN_CONFLICT"),
)


def eligibility_result(facts: EligibilityFacts) -> EligibilityResult:
    blocking = tuple(
        code for name, blocked_value, code in _BLOCKING_RULES if getattr(facts, name) is blocked_value
    )
    return EligibilityResult(eligible=not blocking, blocking_codes=blocking)


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if type(value) not in (tuple, list) or any(type(item) is not str or not item for item in value):
        raise ValueError(f"{field} is invalid")
    return tuple(value)


def deterministic_plan_content(
    template: Mapping[str, object], module_results: Mapping[str, str]
) -> Mapping[str, object]:
    applicable_modules = _string_tuple(template.get("applicable_modules"), "applicable_modules")
    if any(module not in MODULE_CODES for module in applicable_modules):
        raise ValueError("applicable_modules is invalid")
    goals_by_module = template.get("goals_by_module")
    if type(goals_by_module) is not dict:
        raise ValueError("goals_by_module is invalid")

    selected_modules = tuple(
        module
        for module in applicable_modules
        if module_results.get(module) not in {None, "NOT_ASSESSED"}
    )
    goals: list[str] = []
    for module in selected_modules:
        goals.extend(_string_tuple(goals_by_module.get(module), f"goals_by_module.{module}"))

    content = {
        "template_code": str(template["template_code"]),
        "template_version": int(template["template_version"]),
        "module_summaries": tuple(
            {"module_code": module, "risk_level": module_results[module]}
            for module in selected_modules
        ),
        "goals": tuple(goals),
        "stages": _string_tuple(template.get("stage_codes"), "stage_codes"),
        "milestones": _string_tuple(template.get("milestone_codes"), "milestone_codes"),
        "sop_items": _string_tuple(template.get("sop_codes"), "sop_codes"),
        "contraindication_codes": _string_tuple(
            template.get("contraindication_codes"), "contraindication_codes"
        ),
        "user_message_codes": _string_tuple(
            template.get("user_message_codes"), "user_message_codes"
        ),
        "therapist_action_codes": _string_tuple(
            template.get("therapist_action_codes"), "therapist_action_codes"
        ),
    }
    return MappingProxyType(content)
