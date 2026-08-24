from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from types import MappingProxyType
from typing import Mapping
from datetime import datetime
from uuid import UUID


MODULE_CODES = (
    "BLOOD_PRESSURE_CARDIOVASCULAR",
    "GLUCOSE_METABOLISM",
    "LIPID_METABOLISM",
    "WEIGHT_ABDOMINAL_OBESITY",
)
RISK_LEVELS = ("NOT_ASSESSED", "WITHIN_RANGE", "ATTENTION", "HIGH_RISK")
RISK_RANK = MappingProxyType({value: index for index, value in enumerate(RISK_LEVELS)})


class HealthAssessmentState(str, Enum):
    DRAFT_SNAPSHOT = "DRAFT_SNAPSHOT"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    UNDER_REVIEW = "UNDER_REVIEW"
    SUPERSEDED = "SUPERSEDED"


class HighRiskTaskState(str, Enum):
    OPEN = "OPEN"
    CLAIMED = "CLAIMED"
    ESCALATED = "ESCALATED"
    REFERRED = "REFERRED"
    RESOLVED = "RESOLVED"


_ASSESSMENT_TRANSITIONS = frozenset(
    {
        (HealthAssessmentState.DRAFT_SNAPSHOT, HealthAssessmentState.RUNNING),
        (HealthAssessmentState.RUNNING, HealthAssessmentState.COMPLETED),
        (HealthAssessmentState.RUNNING, HealthAssessmentState.FAILED),
        (HealthAssessmentState.COMPLETED, HealthAssessmentState.UNDER_REVIEW),
        (HealthAssessmentState.UNDER_REVIEW, HealthAssessmentState.SUPERSEDED),
    }
)
_TASK_TRANSITIONS = frozenset(
    {
        (HighRiskTaskState.OPEN, HighRiskTaskState.CLAIMED),
        (HighRiskTaskState.OPEN, HighRiskTaskState.REFERRED),
        (HighRiskTaskState.OPEN, HighRiskTaskState.RESOLVED),
        (HighRiskTaskState.CLAIMED, HighRiskTaskState.ESCALATED),
        (HighRiskTaskState.CLAIMED, HighRiskTaskState.REFERRED),
        (HighRiskTaskState.CLAIMED, HighRiskTaskState.RESOLVED),
        (HighRiskTaskState.ESCALATED, HighRiskTaskState.REFERRED),
        (HighRiskTaskState.ESCALATED, HighRiskTaskState.RESOLVED),
    }
)


def next_assessment_state(
    current: HealthAssessmentState, target: HealthAssessmentState
) -> HealthAssessmentState:
    if (current, target) not in _ASSESSMENT_TRANSITIONS:
        raise ValueError("STATE_CONFLICT")
    return target


def next_high_risk_task_state(
    current: HighRiskTaskState, target: HighRiskTaskState
) -> HighRiskTaskState:
    if (current, target) not in _TASK_TRANSITIONS:
        raise ValueError("STATE_CONFLICT")
    return target


@dataclass(frozen=True, slots=True)
class AssessmentInputSnapshot:
    snapshot_id: UUID
    assembly_id: UUID
    assembly_digest: str
    rule_set_code: str
    rule_set_digest: str
    required_max_fact_id: int
    required_max_status_event_seq: int
    source_vector_digest: str
    created_at: datetime

    def __post_init__(self) -> None:
        if (
            self.snapshot_id.version != 7
            or self.assembly_id.version != 7
            or self.required_max_fact_id < 0
            or self.required_max_status_event_seq < 0
            or self.created_at.utcoffset() is None
        ):
            raise ValueError("assessment snapshot is invalid")


@dataclass(frozen=True, slots=True)
class AssessmentFact:
    indicator_code: str
    value: Decimal
    unit: str | None
    measurement_context: str | None

    def __post_init__(self) -> None:
        if not self.indicator_code or not self.value.is_finite():
            raise ValueError("assessment fact is invalid")


@dataclass(frozen=True, slots=True)
class AssessmentFacts:
    values: Mapping[str, AssessmentFact]
    measurement_contexts: Mapping[str, str]

    @classmethod
    def from_strings(
        cls,
        values: Mapping[str, str | Decimal | int],
        *,
        units: Mapping[str, str],
        measurement_contexts: Mapping[str, str],
    ) -> "AssessmentFacts":
        facts: dict[str, AssessmentFact] = {}
        for code, raw_value in values.items():
            if type(code) is not str or not code:
                raise ValueError("indicator code is invalid")
            try:
                value = raw_value if type(raw_value) is Decimal else Decimal(str(raw_value))
            except (InvalidOperation, ValueError):
                raise ValueError("assessment fact value is invalid") from None
            context = measurement_contexts.get(code)
            facts[code] = AssessmentFact(
                indicator_code=code,
                value=value,
                unit=units.get(code),
                measurement_context=context,
            )
        return cls(
            values=MappingProxyType(facts),
            measurement_contexts=MappingProxyType(dict(measurement_contexts)),
        )

    def get(self, code: str) -> AssessmentFact | None:
        return self.values.get(code)

    def context_for(self, code: str) -> str | None:
        fact = self.get(code)
        return fact.measurement_context if fact and fact.measurement_context else self.measurement_contexts.get(code)


@dataclass(frozen=True, slots=True)
class EvaluationContext:
    age_years: int
    sex: str
    pregnancy_status: str
    lactation_status: str
    ascvd_risk_profile: str | None
    acute_symptom_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.age_years) is not int or self.age_years < 18:
            raise ValueError("CN_ADULT_BASELINE_V1 requires an adult subject")
        if self.sex not in {"MALE", "FEMALE", "UNKNOWN"}:
            raise ValueError("sex is invalid")


@dataclass(frozen=True, slots=True)
class ModuleEvaluation:
    module_code: str
    risk_level: str
    reason_codes: tuple[str, ...]
    message_codes: tuple[str, ...]
    evidence_codes: tuple[str, ...]
    high_risk_trigger_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.module_code not in MODULE_CODES or self.risk_level not in RISK_LEVELS:
            raise ValueError("module evaluation is invalid")


@dataclass(frozen=True, slots=True)
class AssessmentEvaluation:
    rule_set_code: str
    module_results: tuple[ModuleEvaluation, ...]
    overall_risk: str
    high_risk_trigger_codes: tuple[str, ...]
    _evidence_values: Mapping[str, Decimal]

    def __post_init__(self) -> None:
        if tuple(item.module_code for item in self.module_results) != MODULE_CODES:
            raise ValueError("exactly four ordered module results are required")
        if self.overall_risk not in RISK_LEVELS:
            raise ValueError("overall risk is invalid")

    def by_module(self, module_code: str) -> ModuleEvaluation:
        for item in self.module_results:
            if item.module_code == module_code:
                return item
        raise KeyError(module_code)

    def evidence_value(self, indicator_code: str) -> Decimal:
        return self._evidence_values[indicator_code]
