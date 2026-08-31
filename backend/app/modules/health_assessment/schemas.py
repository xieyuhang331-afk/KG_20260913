from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


def _uuid_v7(value: UUID) -> UUID:
    if not isinstance(value, UUID):
        raise ValueError("UUID_V7_REQUIRED")
    normalized = UUID(int=value.int)
    if normalized.version != 7:
        raise ValueError("UUID_V7_REQUIRED")
    return normalized


UuidV7 = Annotated[UUID, AfterValidator(_uuid_v7)]
ExpectedVersion = Annotated[int, Field(ge=1, le=2**63 - 1)]
RiskLevel = Literal["NOT_ASSESSED", "WITHIN_RANGE", "ATTENTION", "HIGH_RISK"]
AssessmentStatus = Literal[
    "DRAFT_SNAPSHOT", "RUNNING", "COMPLETED", "FAILED", "UNDER_REVIEW", "SUPERSEDED"
]
HighRiskTaskStatus = Literal["OPEN", "CLAIMED", "ESCALATED", "REFERRED", "RESOLVED"]
ModuleCode = Literal[
    "BLOOD_PRESSURE_CARDIOVASCULAR",
    "GLUCOSE_METABOLISM",
    "LIPID_METABOLISM",
    "WEIGHT_ABDOMINAL_OBESITY",
]
IncludedRuleId = Literal[
    "BP01", "BP02", "BP03", "BP04", "BP05", "BP06", "BP08",
    "GL01", "GL02", "GL03", "GL04", "GL05", "GL06", "GL07", "GL08", "GL09", "GL12",
    "LP01", "LP02", "LP03", "LP04", "LP05", "LP06", "LP07", "LP08", "LP09", "LP13", "LP14",
    "BD01", "BD02", "BD03", "BD07", "BD08", "BD09", "CM01",
]
DeferredRuleId = Literal[
    "BP07", "BP09", "BP10", "BP11", "BP12", "BP13",
    "GL10", "GL11", "GL13", "GL14", "GL15", "GL16", "GL17", "GL18", "GL19", "GL20", "GL21",
    "LP10", "LP11", "LP12", "BD04", "BD05", "BD06",
]
GoldenCaseRef = Literal[
    "GC-BP-SBP", "GC-BP-DBP", "GC-BP-BLOCK", "GC-BP-RED", "GC-BP-TREND", "GC-BP-AGE",
    "GC-GL-FBG", "GC-GL-POST2H", "GC-GL-HBA1C", "GC-GL-BLOCK", "GC-GL-RED-HIGH",
    "GC-GL-RED-LOW", "GC-GL-FBG-TREND", "GC-GL-POST-TREND", "GC-GL-HBA1C-TREND",
    "GC-LP-TC", "GC-LP-TG", "GC-LP-LDL", "GC-LP-LDL-TREND", "GC-BD-BMI", "GC-BD-BMI-TREND",
]
MedicalUnit = Literal["mmHg", "mmol/L", "%", "cm", "kg", "kg/m2"]
RuleInputCode = Literal[
    "systolic_bp", "diastolic_bp", "fasting_glucose", "postprandial_glucose_2h", "random_glucose",
    "hba1c", "total_cholesterol", "triglycerides", "ldl_cholesterol", "hdl_cholesterol",
    "height", "weight", "waist", "bmi", "sex", "pregnancy_status", "lactation_status",
    "ascvd_risk_profile", "acute_symptom_codes", "current_signed_facts",
]
MeasurementContext = Literal[
    "OFFICE", "HOME_AVERAGE", "ABPM_24H_AVERAGE", "ABPM_DAY_AVERAGE", "ABPM_NIGHT_AVERAGE",
    "FASTING_VENOUS", "OGTT_2H_VENOUS", "LAB", "FASTING_LAB", "CURRENT_CONTEXT",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssessmentStartRequest(StrictModel):
    expected_case_version: ExpectedVersion


class AssessmentDisputeRequest(StrictModel):
    expected_version: ExpectedVersion
    reason_code: Literal["DATA_INACCURATE", "CONTEXT_INCOMPLETE", "RESULT_NOT_UNDERSTOOD"]


class HighRiskTaskActionRequest(StrictModel):
    expected_version: ExpectedVersion
    action_code: Literal["CLAIM", "ESCALATE", "REFER", "RESOLVE"]
    contact_outcome_code: Literal["CONTACTED", "UNABLE_TO_CONTACT", "NOT_REQUIRED"] | None = None
    advice_code: Literal[
        "PROMPT_ARTIFICIAL_REVIEW",
        "PROMPT_MEDICAL_CONTACT",
        "PROMPT_EMERGENCY_IF_ACUTE",
        "PROMPT_LOW_GLUCOSE_SAFE_INTAKE",
    ] | None = None
    reason_code: Literal[
        "CLAIMED_FOR_REVIEW",
        "SAFETY_STATE_UNCONFIRMED",
        "REFERRED_TO_MEDICAL_RESPONSIBLE_PERSON",
        "CURRENT_REASSESSMENT_NON_HIGH_RISK",
    ] | None = None
    occurred_at: AwareDatetime

    @model_validator(mode="after")
    def evidence_required_for_closure(self):
        if self.action_code in {"REFER", "RESOLVE"} and (
            self.contact_outcome_code is None
            or self.advice_code is None
            or self.reason_code is None
        ):
            raise ValueError("HIGH_RISK_ACTION_INVALID")
        return self


class ApprovedRuleDefinitionDTO(StrictModel):
    rule_id: IncludedRuleId
    module_code: ModuleCode
    definition_code: IncludedRuleId
    input_codes: tuple[RuleInputCode, ...]
    unit: MedicalUnit | None
    measurement_contexts: tuple[MeasurementContext, ...]
    risk_levels: tuple[RiskLevel, ...]
    golden_case_refs: tuple[GoldenCaseRef, ...]
    high_risk_trigger_codes: tuple[
        Literal["HR-BP-SEVERE", "HR-GLUCOSE-LOW", "HR-GLUCOSE-SYMPTOM"], ...
    ]

    @model_validator(mode="after")
    def exact_approved_definition(self):
        expected = _APPROVED_RULE_CATALOG.get(self.rule_id)
        if expected is None or self.model_dump() != expected:
            raise ValueError("APPROVED_RULE_DEFINITION_REQUIRED")
        return self


class DeferredRuleReferenceDTO(StrictModel):
    rule_id: DeferredRuleId
    module_code: ModuleCode
    enabled: Literal[False]
    reason_code: Literal["DEFERRED_NOT_IN_V1"]

    @model_validator(mode="after")
    def exact_deferred_definition(self):
        if _DEFERRED_RULE_MODULES.get(self.rule_id) != self.module_code:
            raise ValueError("DEFERRED_RULE_DEFINITION_REQUIRED")
        return self


class MedicalRuleModuleV1DTO(StrictModel):
    module_code: ModuleCode
    included_rules: tuple[ApprovedRuleDefinitionDTO, ...]
    deferred_rules: tuple[DeferredRuleReferenceDTO, ...]
    golden_case_refs: tuple[GoldenCaseRef, ...]

    @model_validator(mode="after")
    def exact_module_catalog(self):
        expected = _APPROVED_MODULE_CATALOG.get(self.module_code)
        actual = {
            "included_rule_ids": tuple(item.rule_id for item in self.included_rules),
            "deferred_rule_ids": tuple(item.rule_id for item in self.deferred_rules),
            "golden_case_refs": self.golden_case_refs,
        }
        if expected is None or actual != expected:
            raise ValueError("APPROVED_MODULE_CATALOG_REQUIRED")
        return self


class MedicalRulePayloadV1DTO(StrictModel):
    schema_version: Literal["SLICE5_MEDICAL_RULE_PAYLOAD_V1"]
    rule_set_code: Literal["CN_ADULT_BASELINE_V1"]
    modules: tuple[MedicalRuleModuleV1DTO, ...]

    @model_validator(mode="after")
    def exact_payload_catalog(self):
        if tuple(item.module_code for item in self.modules) != tuple(_APPROVED_MODULE_CATALOG):
            raise ValueError("APPROVED_RULE_PAYLOAD_REQUIRED")
        return self


def _rule(
    rule_id: IncludedRuleId,
    module_code: ModuleCode,
    input_codes: tuple[RuleInputCode, ...],
    unit: MedicalUnit | None,
    measurement_contexts: tuple[MeasurementContext, ...],
    risk_levels: tuple[RiskLevel, ...],
    golden_case_refs: tuple[GoldenCaseRef, ...],
    high_risk_trigger_codes: tuple[str, ...] = (),
) -> dict:
    return {
        "rule_id": rule_id,
        "module_code": module_code,
        "definition_code": rule_id,
        "input_codes": input_codes,
        "unit": unit,
        "measurement_contexts": measurement_contexts,
        "risk_levels": risk_levels,
        "golden_case_refs": golden_case_refs,
        "high_risk_trigger_codes": high_risk_trigger_codes,
    }


_BP = "BLOOD_PRESSURE_CARDIOVASCULAR"
_GL = "GLUCOSE_METABOLISM"
_LP = "LIPID_METABOLISM"
_BD = "WEIGHT_ABDOMINAL_OBESITY"
_APPROVED_RULE_CATALOG = {
    item["rule_id"]: item
    for item in (
        _rule("BP01", _BP, ("systolic_bp", "diastolic_bp"), "mmHg", ("OFFICE",), ("WITHIN_RANGE",), ("GC-BP-SBP", "GC-BP-DBP")),
        _rule("BP02", _BP, ("systolic_bp", "diastolic_bp"), "mmHg", ("OFFICE",), ("ATTENTION",), ("GC-BP-SBP", "GC-BP-DBP")),
        _rule("BP03", _BP, ("systolic_bp", "diastolic_bp"), "mmHg", ("OFFICE",), ("ATTENTION",), ("GC-BP-SBP", "GC-BP-DBP")),
        _rule("BP04", _BP, ("systolic_bp", "diastolic_bp"), "mmHg", ("OFFICE",), ("WITHIN_RANGE",), ("GC-BP-SBP", "GC-BP-DBP")),
        _rule("BP05", _BP, ("systolic_bp", "diastolic_bp"), "mmHg", ("OFFICE",), ("ATTENTION",), ("GC-BP-SBP", "GC-BP-DBP")),
        _rule("BP06", _BP, ("systolic_bp", "diastolic_bp"), "mmHg", ("OFFICE",), ("ATTENTION",), ("GC-BP-SBP", "GC-BP-DBP")),
        _rule("BP08", _BP, ("systolic_bp", "diastolic_bp"), "mmHg", ("OFFICE",), ("HIGH_RISK",), ("GC-BP-RED",), ("HR-BP-SEVERE",)),
        _rule("GL01", _GL, ("fasting_glucose",), "mmol/L", ("FASTING_VENOUS",), ("WITHIN_RANGE",), ("GC-GL-FBG",)),
        _rule("GL02", _GL, ("fasting_glucose",), "mmol/L", ("FASTING_VENOUS",), ("ATTENTION",), ("GC-GL-FBG",)),
        _rule("GL03", _GL, ("fasting_glucose",), "mmol/L", ("FASTING_VENOUS",), ("ATTENTION",), ("GC-GL-FBG",)),
        _rule("GL04", _GL, ("postprandial_glucose_2h",), "mmol/L", ("OGTT_2H_VENOUS",), ("WITHIN_RANGE",), ("GC-GL-POST2H",)),
        _rule("GL05", _GL, ("postprandial_glucose_2h",), "mmol/L", ("OGTT_2H_VENOUS",), ("ATTENTION",), ("GC-GL-POST2H",)),
        _rule("GL06", _GL, ("postprandial_glucose_2h",), "mmol/L", ("OGTT_2H_VENOUS",), ("ATTENTION",), ("GC-GL-POST2H",)),
        _rule("GL07", _GL, ("hba1c",), "%", ("LAB",), ("WITHIN_RANGE",), ("GC-GL-HBA1C",)),
        _rule("GL08", _GL, ("hba1c",), "%", ("LAB",), ("ATTENTION",), ("GC-GL-HBA1C",)),
        _rule("GL09", _GL, ("hba1c",), "%", ("LAB",), ("ATTENTION",), ("GC-GL-HBA1C",)),
        _rule("GL12", _GL, ("fasting_glucose", "postprandial_glucose_2h", "random_glucose", "acute_symptom_codes"), "mmol/L", ("CURRENT_CONTEXT",), ("ATTENTION", "HIGH_RISK"), ("GC-GL-RED-LOW",), ("HR-GLUCOSE-LOW", "HR-GLUCOSE-SYMPTOM")),
        _rule("LP01", _LP, ("total_cholesterol",), "mmol/L", ("FASTING_LAB",), ("WITHIN_RANGE",), ("GC-LP-TC",)),
        _rule("LP02", _LP, ("total_cholesterol",), "mmol/L", ("FASTING_LAB",), ("ATTENTION",), ("GC-LP-TC",)),
        _rule("LP03", _LP, ("total_cholesterol",), "mmol/L", ("FASTING_LAB",), ("ATTENTION",), ("GC-LP-TC",)),
        _rule("LP04", _LP, ("triglycerides",), "mmol/L", ("FASTING_LAB",), ("WITHIN_RANGE",), ("GC-LP-TG",)),
        _rule("LP05", _LP, ("triglycerides",), "mmol/L", ("FASTING_LAB",), ("ATTENTION",), ("GC-LP-TG",)),
        _rule("LP06", _LP, ("triglycerides",), "mmol/L", ("FASTING_LAB",), ("ATTENTION",), ("GC-LP-TG",)),
        _rule("LP07", _LP, ("ldl_cholesterol", "ascvd_risk_profile"), "mmol/L", ("FASTING_LAB",), ("WITHIN_RANGE",), ("GC-LP-LDL",)),
        _rule("LP08", _LP, ("ldl_cholesterol", "ascvd_risk_profile"), "mmol/L", ("FASTING_LAB",), ("ATTENTION",), ("GC-LP-LDL",)),
        _rule("LP09", _LP, ("ldl_cholesterol", "ascvd_risk_profile"), "mmol/L", ("FASTING_LAB",), ("ATTENTION",), ("GC-LP-LDL",)),
        _rule("LP13", _LP, ("hdl_cholesterol",), "mmol/L", ("FASTING_LAB",), ("ATTENTION",), ("GC-LP-TC",)),
        _rule("LP14", _LP, ("total_cholesterol", "triglycerides", "ldl_cholesterol", "hdl_cholesterol"), "mmol/L", ("FASTING_LAB",), ("NOT_ASSESSED", "WITHIN_RANGE", "ATTENTION"), ("GC-LP-TC", "GC-LP-TG", "GC-LP-LDL")),
        _rule("BD01", _BD, ("bmi",), "kg/m2", ("CURRENT_CONTEXT",), ("WITHIN_RANGE", "ATTENTION"), ("GC-BD-BMI",)),
        _rule("BD02", _BD, ("bmi",), "kg/m2", ("CURRENT_CONTEXT",), ("ATTENTION",), ("GC-BD-BMI",)),
        _rule("BD03", _BD, ("bmi",), "kg/m2", ("CURRENT_CONTEXT",), ("ATTENTION",), ("GC-BD-BMI",)),
        _rule("BD07", _BD, ("waist", "sex"), "cm", ("CURRENT_CONTEXT",), ("WITHIN_RANGE", "ATTENTION"), ("GC-BD-BMI",)),
        _rule("BD08", _BD, ("height", "weight", "bmi"), "kg/m2", ("CURRENT_CONTEXT",), ("NOT_ASSESSED", "WITHIN_RANGE", "ATTENTION"), ("GC-BD-BMI",)),
        _rule("BD09", _BD, ("pregnancy_status", "lactation_status"), None, ("CURRENT_CONTEXT",), ("NOT_ASSESSED",), ("GC-BD-BMI",)),
        _rule("CM01", _BD, ("current_signed_facts",), None, ("CURRENT_CONTEXT",), ("NOT_ASSESSED", "WITHIN_RANGE", "ATTENTION", "HIGH_RISK"), ("GC-BP-AGE",)),
    )
}
_DEFERRED_RULE_MODULES = {
    **{rule_id: _BP for rule_id in ("BP07", "BP09", "BP10", "BP11", "BP12", "BP13")},
    **{rule_id: _GL for rule_id in ("GL10", "GL11", "GL13", "GL14", "GL15", "GL16", "GL17", "GL18", "GL19", "GL20", "GL21")},
    **{rule_id: _LP for rule_id in ("LP10", "LP11", "LP12")},
    **{rule_id: _BD for rule_id in ("BD04", "BD05", "BD06")},
}
_APPROVED_MODULE_CATALOG = {
    _BP: {
        "included_rule_ids": ("BP01", "BP02", "BP03", "BP04", "BP05", "BP06", "BP08"),
        "deferred_rule_ids": ("BP07", "BP09", "BP10", "BP11", "BP12", "BP13"),
        "golden_case_refs": ("GC-BP-SBP", "GC-BP-DBP", "GC-BP-BLOCK", "GC-BP-RED", "GC-BP-TREND", "GC-BP-AGE"),
    },
    _GL: {
        "included_rule_ids": ("GL01", "GL02", "GL03", "GL04", "GL05", "GL06", "GL07", "GL08", "GL09", "GL12"),
        "deferred_rule_ids": ("GL10", "GL11", "GL13", "GL14", "GL15", "GL16", "GL17", "GL18", "GL19", "GL20", "GL21"),
        "golden_case_refs": ("GC-GL-FBG", "GC-GL-POST2H", "GC-GL-HBA1C", "GC-GL-BLOCK", "GC-GL-RED-HIGH", "GC-GL-RED-LOW", "GC-GL-FBG-TREND", "GC-GL-POST-TREND", "GC-GL-HBA1C-TREND"),
    },
    _LP: {
        "included_rule_ids": ("LP01", "LP02", "LP03", "LP04", "LP05", "LP06", "LP07", "LP08", "LP09", "LP13", "LP14"),
        "deferred_rule_ids": ("LP10", "LP11", "LP12"),
        "golden_case_refs": ("GC-LP-TC", "GC-LP-TG", "GC-LP-LDL", "GC-LP-LDL-TREND"),
    },
    _BD: {
        "included_rule_ids": ("BD01", "BD02", "BD03", "BD07", "BD08", "BD09", "CM01"),
        "deferred_rule_ids": ("BD04", "BD05", "BD06"),
        "golden_case_refs": ("GC-BD-BMI", "GC-BD-BMI-TREND"),
    },
}


def approved_medical_rule_payload_v1() -> MedicalRulePayloadV1DTO:
    modules = []
    for module_code, catalog in _APPROVED_MODULE_CATALOG.items():
        modules.append(
            {
                "module_code": module_code,
                "included_rules": [_APPROVED_RULE_CATALOG[item] for item in catalog["included_rule_ids"]],
                "deferred_rules": [
                    {
                        "rule_id": item,
                        "module_code": module_code,
                        "enabled": False,
                        "reason_code": "DEFERRED_NOT_IN_V1",
                    }
                    for item in catalog["deferred_rule_ids"]
                ],
                "golden_case_refs": catalog["golden_case_refs"],
            }
        )
    return MedicalRulePayloadV1DTO(
        schema_version="SLICE5_MEDICAL_RULE_PAYLOAD_V1",
        rule_set_code="CN_ADULT_BASELINE_V1",
        modules=modules,
    )


class RuleSetCreateRequest(StrictModel):
    rule_set_code: Literal["CN_ADULT_BASELINE_V1"]
    version_no: int = Field(ge=1, le=2**63 - 1)
    typed_rule_payload: MedicalRulePayloadV1DTO
    medical_content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_evidence_ref: str = Field(min_length=1, max_length=256)


class VersionRequest(StrictModel):
    expected_version: ExpectedVersion


class RuleReviewRequest(VersionRequest):
    decision: Literal["APPROVE", "NEEDS_CORRECTION"]
    reason_code: Literal[
        "MEDICAL_CONTENT_APPROVED",
        "RULE_CONTENT_CORRECTION_REQUIRED",
        "MEDICAL_EVIDENCE_CORRECTION_REQUIRED",
        "GOLDEN_CASE_CORRECTION_REQUIRED",
        "HIGH_RISK_SAFETY_CORRECTION_REQUIRED",
    ]

    @model_validator(mode="after")
    def reason_matches_decision(self):
        if (self.decision == "APPROVE") != (self.reason_code == "MEDICAL_CONTENT_APPROVED"):
            raise ValueError("RULE_REVIEW_REASON_MISMATCH")
        return self


class RulePublishRequest(VersionRequest):
    operation: Literal["PUBLISH"]
    reason_code: Literal["DOUBLE_SIGNED_BASELINE_RELEASE"]
    effective_from: AwareDatetime


class RuleGovernanceRequest(VersionRequest):
    operation: Literal["SUSPEND", "RESUME", "RETIRE"]
    reason_code: Literal[
        "MEDICAL_SAFETY_REVIEW_REQUIRED",
        "APPROVAL_EVIDENCE_INVALIDATED",
        "RULE_IMPLEMENTATION_DEFECT_CONFIRMED",
        "MEDICAL_SAFETY_REVIEW_CLEARED",
        "APPROVAL_EVIDENCE_REVALIDATED",
        "RULE_IMPLEMENTATION_DEFECT_REMEDIATED",
        "SUPERSEDED_BY_APPROVED_VERSION",
        "BASELINE_WITHDRAWN",
    ]

    @model_validator(mode="after")
    def reason_matches_operation(self):
        allowed = {
            "SUSPEND": {
                "MEDICAL_SAFETY_REVIEW_REQUIRED",
                "APPROVAL_EVIDENCE_INVALIDATED",
                "RULE_IMPLEMENTATION_DEFECT_CONFIRMED",
            },
            "RESUME": {
                "MEDICAL_SAFETY_REVIEW_CLEARED",
                "APPROVAL_EVIDENCE_REVALIDATED",
                "RULE_IMPLEMENTATION_DEFECT_REMEDIATED",
            },
            "RETIRE": {"SUPERSEDED_BY_APPROVED_VERSION", "BASELINE_WITHDRAWN"},
        }
        if self.reason_code not in allowed[self.operation]:
            raise ValueError("RULE_GOVERNANCE_REASON_MISMATCH")
        return self


class RuleSetDraftUpdateRequest(StrictModel):
    expected_version: ExpectedVersion
    typed_rule_payload: MedicalRulePayloadV1DTO
    medical_content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_evidence_ref: str = Field(min_length=1, max_length=256)


class ModuleResultDTO(StrictModel):
    module_code: ModuleCode
    risk_level: RiskLevel
    reason_codes: tuple[str, ...]
    evidence_items: tuple[str, ...]
    message_codes: tuple[str, ...]


class InputEvidenceDTO(StrictModel):
    assembly_ref: UuidV7
    profile_revision_ref: UuidV7
    data_as_of: AwareDatetime
    source_types: tuple[Literal["STORE", "REPORT", "APP"], ...]
    watermark_status: Literal["CURRENT", "STALE"]


class AssessmentSummaryDTO(StrictModel):
    assessment_id: UuidV7
    service_case_id: UuidV7
    sequence_no: int = Field(ge=1)
    status: AssessmentStatus
    overall_risk: RiskLevel | None = None
    rule_version: str
    input_snapshot_ref: UuidV7
    supersedes_assessment_id: UuidV7 | None = None
    initiated_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    version: ExpectedVersion


class AssessmentDetailDTO(AssessmentSummaryDTO):
    module_results: tuple[ModuleResultDTO, ...]
    input_evidence: InputEvidenceDTO
    dispute_status: Literal["OPEN", "RESOLVED"] | None = None
    high_risk_task_ref: UuidV7 | None = None


class AssessmentPageDTO(StrictModel):
    items: tuple[AssessmentSummaryDTO, ...]
    next_cursor: str | None = None


class BlockingDTO(StrictModel):
    ordinary_plan: bool
    case_completion: bool


class PublicUserRefDTO(StrictModel):
    public_user_ref: str = Field(min_length=16, max_length=128, pattern=r"^usr_[A-Za-z0-9_-]+$")
    display_name: str = Field(min_length=1, max_length=50)
    role_label: Literal[
        "EXPERT", "MEDICAL_REVIEWER", "THERAPIST", "PLATFORM_GOVERNANCE",
        "INSTITUTION_ADMIN", "INSTITUTION_OPERATOR",
    ]


class HighRiskTaskDTO(StrictModel):
    task_id: UuidV7
    assessment_id: UuidV7
    service_case_id: UuidV7
    status: HighRiskTaskStatus
    reason_module_codes: tuple[ModuleCode, ...]
    assignee_ref: PublicUserRefDTO | None = None
    due_at: AwareDatetime
    last_action_at: AwareDatetime | None = None
    blocking: BlockingDTO
    version: ExpectedVersion
    created_at: AwareDatetime
    closed_at: AwareDatetime | None = None


class HighRiskTaskPageDTO(StrictModel):
    items: tuple[HighRiskTaskDTO, ...]
    next_cursor: str | None = None


class DisputeDTO(StrictModel):
    dispute_id: UuidV7
    assessment_id: UuidV7
    status: Literal["OPEN", "RESOLVED"]
    reason_code: str
    created_at: AwareDatetime
    version: ExpectedVersion


class RuleSetVersionDTO(StrictModel):
    rule_set_version_id: UuidV7
    version_no: int = Field(ge=1)
    status: Literal["DRAFT", "IN_REVIEW", "NEEDS_CORRECTION", "PUBLISHED", "SUSPENDED", "RETIRED"]
    module_metadata: tuple[ModuleCode, ...]
    author_ref: PublicUserRefDTO
    reviewer_ref: PublicUserRefDTO | None = None
    approval_state: Literal["PENDING", "APPROVED", "NEEDS_CORRECTION"]
    effective_from: AwareDatetime | None = None
    suspended_at: AwareDatetime | None = None
    retired_at: AwareDatetime | None = None
    version: ExpectedVersion


class RuleSetVersionDetailDTO(RuleSetVersionDTO):
    rule_set_code: str
    typed_rule_payload: MedicalRulePayloadV1DTO
    approval_evidence_ref: str | None = None


class RuleSetPageDTO(StrictModel):
    items: tuple[RuleSetVersionDTO, ...]
    next_cursor: str | None = None
