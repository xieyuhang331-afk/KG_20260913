from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


JsonProfileValue = dict[str, Any] | list[Any] | None


MemberProfileGender = Literal["male", "female"]
MemberProfileBloodType = Literal["A", "B", "AB", "O", "UNKNOWN"]
MemberProfileState = Literal["NOT_CREATED", "INCOMPLETE", "COMPLETE"]
MemberProfileOutcome = Literal["CREATED", "UPDATED", "REPLAYED"]


class MemberSelfHealthProfileWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gender: MemberProfileGender
    birth_date: date
    height: Decimal = Field(..., gt=0, max_digits=5, decimal_places=1)
    weight: Decimal = Field(..., gt=0, max_digits=5, decimal_places=1)
    blood_type: MemberProfileBloodType | None = None
    expected_version: datetime | None = None

    @field_validator("birth_date")
    @classmethod
    def birth_date_must_not_be_in_the_future(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("birth_date must not be in the future")
        return value


class MemberSelfHealthProfileData(BaseModel):
    gender: MemberProfileGender
    birth_date: date
    height: Decimal | None
    weight: Decimal | None
    blood_type: MemberProfileBloodType | None


class MemberSelfHealthProfileResult(BaseModel):
    state: MemberProfileState
    version: datetime | None
    profile: MemberSelfHealthProfileData | None
    bmi: Decimal | None
    outcome: MemberProfileOutcome | None = None


class HealthProfileCreateRequest(BaseModel):
    gender: str = Field(..., min_length=1, max_length=5)
    birth_date: date
    height: Decimal | None = Field(default=None, max_digits=5, decimal_places=1)
    weight: Decimal | None = Field(default=None, max_digits=5, decimal_places=1)
    blood_type: str | None = Field(default=None, max_length=5)
    medical_history: JsonProfileValue = None
    allergy_history: JsonProfileValue = None
    family_history: JsonProfileValue = None
    smoking: str | None = Field(default=None, max_length=10)
    drinking: str | None = Field(default=None, max_length=10)
    symptoms: JsonProfileValue = None
    sleep_quality: str | None = Field(default=None, max_length=50)
    bowel_urination: str | None = Field(default=None, max_length=100)


class HealthProfileResponse(BaseModel):
    id: int
    user_id: int
    gender: str
    birth_date: date
    height: Decimal | None
    weight: Decimal | None
    blood_type: str | None
    medical_history: JsonProfileValue
    allergy_history: JsonProfileValue
    family_history: JsonProfileValue
    smoking: str | None
    drinking: str | None
    symptoms: JsonProfileValue
    sleep_quality: str | None
    bowel_urination: str | None
    created_at: datetime
    updated_at: datetime


class HealthIdentitySummaryDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gender: Literal["MALE", "FEMALE", "UNKNOWN"]
    birth_date: date
    identity_revision_ref: UUID
    source_version: int = Field(ge=1)
    tenant_public_id: UUID
    evidence_status: Literal["VERIFIED"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MedicalHistoryItem(_StrictModel):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9._-]+$")
    onset_date: date | None = None
    resolved_date: date | None = None
    status: Literal["ACTIVE", "RESOLVED", "UNKNOWN"]
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.onset_date and self.resolved_date and self.resolved_date < self.onset_date:
            raise ValueError("resolved_date precedes onset_date")
        return self


class AllergyItem(_StrictModel):
    allergen_type: Literal["MEDICATION", "FOOD", "ENVIRONMENT", "OTHER"]
    allergen_name: str = Field(min_length=1, max_length=128)
    reaction: str | None = Field(default=None, max_length=256)
    severity: Literal["MILD", "MODERATE", "SEVERE", "UNKNOWN"]
    status: Literal["ACTIVE", "RESOLVED", "UNKNOWN"]


class MedicationItem(_StrictModel):
    name: str = Field(min_length=1, max_length=128)
    dose: Decimal = Field(gt=0, max_digits=10, decimal_places=3)
    unit: str = Field(min_length=1, max_length=32)
    route: Literal["ORAL", "INJECTION", "TOPICAL", "INHALATION", "OTHER"]
    frequency: str = Field(min_length=1, max_length=64)
    start_date: date | None = None
    end_date: date | None = None
    status: Literal["ACTIVE", "STOPPED", "UNKNOWN"]

    @model_validator(mode="after")
    def validate_dates(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date precedes start_date")
        return self


class SymptomItem(_StrictModel):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9._-]+$")
    severity: Literal["MILD", "MODERATE", "SEVERE", "UNKNOWN"]
    onset_at: datetime | None = None
    note: str | None = Field(default=None, max_length=500)

    @field_validator("onset_at")
    @classmethod
    def aware_onset(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("onset_at must be timezone-aware")
        return value


class FormalHealthProfileSnapshotRequest(_StrictModel):
    medical_history: list[MedicalHistoryItem] = Field(default_factory=list, max_length=100)
    allergies: list[AllergyItem] = Field(default_factory=list, max_length=100)
    medications: list[MedicationItem] = Field(default_factory=list, max_length=100)
    symptoms: list[SymptomItem] = Field(default_factory=list, max_length=100)
    pregnancy_status: Literal["PREGNANT", "NOT_PREGNANT", "UNKNOWN", "NOT_APPLICABLE"]
    pregnancy_week: int | None = Field(default=None, ge=1, le=45)
    lactation_status: Literal["LACTATING", "NOT_LACTATING", "UNKNOWN", "NOT_APPLICABLE"]
    reconfirmed_at: datetime
    source_type: Literal["APP", "STORE"]
    expected_version: int = Field(ge=0)

    @field_validator("reconfirmed_at")
    @classmethod
    def aware_reconfirmation(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reconfirmed_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def pregnancy_truth(self):
        if (self.pregnancy_status == "PREGNANT") != (self.pregnancy_week is not None):
            raise ValueError("pregnancy truth is invalid")
        return self


class FormalHealthFactWriteItem(_StrictModel):
    indicator_code: Literal[
        "height", "weight", "waist", "systolic_bp", "diastolic_bp",
        "heart_rate", "fasting_glucose", "postprandial_glucose_2h", "hba1c",
        "total_cholesterol", "triglyceride", "hdl_c", "ldl_c",
    ]
    value: Decimal
    unit: str = Field(min_length=1, max_length=32)
    measured_at: datetime
    source_type: Literal["APP", "REPORT", "STORE"]
    report_id: UUID | None = None
    measurement_context: Literal[
        "OFFICE", "HOME_AVERAGE", "ABPM_24H_AVERAGE",
        "ABPM_DAY_AVERAGE", "ABPM_NIGHT_AVERAGE",
        "FASTING_VENOUS", "OGTT_2H_VENOUS", "LAB", "FASTING_LAB",
    ] | None = None

    @field_validator("value")
    @classmethod
    def finite_value(cls, value):
        if not value.is_finite():
            raise ValueError("value must be finite")
        return value

    @field_validator("measured_at")
    @classmethod
    def aware_measurement(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("measured_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def report_binding(self):
        units = {
            "height": "cm", "weight": "kg", "waist": "cm",
            "systolic_bp": "mmHg", "diastolic_bp": "mmHg",
            "heart_rate": "bpm", "fasting_glucose": "mmol/L", "hba1c": "%",
            "postprandial_glucose_2h": "mmol/L", "total_cholesterol": "mmol/L",
            "triglyceride": "mmol/L", "hdl_c": "mmol/L", "ldl_c": "mmol/L",
        }
        if self.unit != units[self.indicator_code]:
            raise ValueError("indicator unit is invalid")
        if (self.source_type == "REPORT") != (self.report_id is not None):
            raise ValueError("report binding is invalid")
        allowed = {
            "systolic_bp": {"OFFICE", "HOME_AVERAGE", "ABPM_24H_AVERAGE", "ABPM_DAY_AVERAGE", "ABPM_NIGHT_AVERAGE"},
            "diastolic_bp": {"OFFICE", "HOME_AVERAGE", "ABPM_24H_AVERAGE", "ABPM_DAY_AVERAGE", "ABPM_NIGHT_AVERAGE"},
            "fasting_glucose": {"FASTING_VENOUS"},
            "postprandial_glucose_2h": {"OGTT_2H_VENOUS"},
            "hba1c": {"LAB"},
            "total_cholesterol": {"FASTING_LAB"},
            "triglyceride": {"FASTING_LAB"},
            "hdl_c": {"FASTING_LAB"},
            "ldl_c": {"FASTING_LAB"},
        }
        if self.measurement_context is not None and self.measurement_context not in allowed.get(self.indicator_code, set()):
            raise ValueError("measurement context is invalid")
        return self


class FormalDetectionReportCreateRequest(_StrictModel):
    report_type: Literal["LAB_REPORT", "IMAGING_REPORT", "PHYSICAL_EXAM", "OTHER"]
    measured_at: datetime
    source_type: Literal["APP", "STORE"]
    file_ids: list[UUID] = Field(min_length=1, max_length=10)

    @field_validator("measured_at")
    @classmethod
    def aware_report_time(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("measured_at must be timezone-aware")
        return value

    @field_validator("file_ids")
    @classmethod
    def unique_uuid7_files(cls, value):
        if len(set(value)) != len(value) or any(item.version != 7 for item in value):
            raise ValueError("file_ids must be unique UUIDv7 values")
        return value


class HealthProfileDTO(_StrictModel):
    subject_ref: UUID
    profile_id: UUID
    revision_id: UUID
    revision_no: int = Field(ge=1)
    version: int = Field(ge=1)
    identity_summary: HealthIdentitySummaryDTO
    medical_history: list[MedicalHistoryItem]
    allergies: list[AllergyItem]
    medications: list[MedicationItem]
    symptoms: list[SymptomItem]
    pregnancy_status: Literal["PREGNANT", "NOT_PREGNANT", "UNKNOWN", "NOT_APPLICABLE"]
    pregnancy_week: int | None
    lactation_status: Literal["LACTATING", "NOT_LACTATING", "UNKNOWN", "NOT_APPLICABLE"]
    reconfirmed_at: datetime
    height_cm: Decimal | None = None
    weight_kg: Decimal | None = None
    waist_cm: Decimal | None = None
    bmi: Decimal | None = None
    updated_at: datetime


class DetectionReportAttachmentDTO(_StrictModel):
    file_id: UUID
    mime_type: str
    size: int = Field(ge=1, le=10 * 1024 * 1024)
    status: Literal["CLEAN"]


class DetectionReportDTO(_StrictModel):
    report_id: UUID
    subject_ref: UUID
    report_type: str
    measured_at: datetime
    received_at: datetime
    status: str
    source: Literal["APP", "STORE"]
    attachment_count: int = Field(ge=1, le=10)
    structured_indicator_codes: tuple[str, ...] = ()
    supersedes_report_id: UUID | None = None
    version: int = Field(ge=1)
    created_at: datetime


class DetectionReportDetailDTO(DetectionReportDTO):
    attachments: list[DetectionReportAttachmentDTO]


class DetectionReportPageDTO(_StrictModel):
    items: list[DetectionReportDTO]
    next_cursor: str | None


class HealthFactDTO(_StrictModel):
    fact_ref: UUID
    indicator_code: str
    value: Decimal
    unit: str
    measured_at: datetime
    received_at: datetime
    source: Literal["APP", "REPORT", "STORE"]
    verification_state: Literal["SELF_REPORTED", "VERIFIED", "UNKNOWN", "DISPUTED"]


class HealthFactBatchRequest(_StrictModel):
    items: list[FormalHealthFactWriteItem] = Field(min_length=1, max_length=50)

    @field_validator("items")
    @classmethod
    def unique_indicators(cls, value):
        codes = [item.indicator_code for item in value]
        if len(set(codes)) != len(codes):
            raise ValueError("indicator codes must be unique")
        return value


class HealthFactBatchDTO(_StrictModel):
    items: list[HealthFactDTO]


class HealthFactCorrectionRequest(_StrictModel):
    indicator_code: str
    value: Decimal
    unit: str
    measured_at: datetime
    measurement_context: Literal[
        "OFFICE", "HOME_AVERAGE", "ABPM_24H_AVERAGE",
        "ABPM_DAY_AVERAGE", "ABPM_NIGHT_AVERAGE",
        "FASTING_VENOUS", "OGTT_2H_VENOUS", "LAB", "FASTING_LAB",
    ] | None = None
    reason_code: Literal["DATA_ENTRY_ERROR", "SOURCE_CORRECTION", "MEMBER_CORRECTION"]

    @model_validator(mode="after")
    def measurement_context_binding(self):
        allowed = {
            "systolic_bp": {"OFFICE", "HOME_AVERAGE", "ABPM_24H_AVERAGE", "ABPM_DAY_AVERAGE", "ABPM_NIGHT_AVERAGE"},
            "diastolic_bp": {"OFFICE", "HOME_AVERAGE", "ABPM_24H_AVERAGE", "ABPM_DAY_AVERAGE", "ABPM_NIGHT_AVERAGE"},
            "fasting_glucose": {"FASTING_VENOUS"},
            "postprandial_glucose_2h": {"OGTT_2H_VENOUS"},
            "hba1c": {"LAB"},
            "total_cholesterol": {"FASTING_LAB"},
            "triglyceride": {"FASTING_LAB"},
            "hdl_c": {"FASTING_LAB"},
            "ldl_c": {"FASTING_LAB"},
        }
        if self.measurement_context is not None and self.measurement_context not in allowed.get(self.indicator_code, set()):
            raise ValueError("measurement context is invalid")
        return self


class HealthFactStateRequest(_StrictModel):
    expected_version: int = Field(ge=1)
    reason_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9_]+$")


class HealthIndicatorPageDTO(_StrictModel):
    items: list[HealthFactDTO]
    next_cursor: str | None


class HealthIndicatorLatestDTO(_StrictModel):
    items: list[HealthFactDTO]


class HealthIndicatorTrendPointDTO(_StrictModel):
    measured_at: datetime
    value: Decimal


class HealthIndicatorTrendDTO(_StrictModel):
    indicator_code: str
    unit: str
    points: list[HealthIndicatorTrendPointDTO]
    next_cursor: str | None


class InstitutionHealthRecordDTO(_StrictModel):
    case_id: UUID
    profile_completion_status: str
    missing_section_codes: tuple[str, ...]
    indicator_codes: tuple[str, ...]
    indicator_states: dict[str, str]
    report_metadata_count: int = Field(ge=0)
    readiness_status: str
    updated_at: datetime


class ErrorEnvelopeDTO(_StrictModel):
    code: str
    message: Literal["request rejected"] = "request rejected"


HealthIndicatorSource = Literal["APP", "STORE", "DEVICE", "REPORT"]


class HealthIndicatorCreateItem(BaseModel):
    indicator_type: str = Field(..., min_length=1, max_length=30)
    value: Decimal = Field(..., max_digits=10, decimal_places=2)
    unit: str = Field(..., min_length=1, max_length=10)
    source: HealthIndicatorSource
    recorded_at: datetime
    batch_id: str | None = Field(default=None, max_length=36)


class HealthIndicatorBatchCreateRequest(BaseModel):
    indicators: list[HealthIndicatorCreateItem] = Field(..., min_length=1)


class HealthIndicatorResponse(BaseModel):
    id: int
    batch_id: str | None
    indicator_type: str
    value: Decimal
    unit: str
    source: HealthIndicatorSource
    recorded_at: datetime
    created_at: datetime


class MemberSelfHealthIndicatorItem(BaseModel):
    id: int
    batch_id: str | None
    indicator_type: str
    value: Decimal
    unit: str
    source: HealthIndicatorSource
    recorded_at: datetime


class MemberSelfHealthIndicatorPage(BaseModel):
    state: Literal["EMPTY", "AVAILABLE"]
    items: list[MemberSelfHealthIndicatorItem]
    next_cursor: str | None


class MemberSelfHealthIndicatorLatest(BaseModel):
    state: Literal["EMPTY", "AVAILABLE"]
    items: list[MemberSelfHealthIndicatorItem]


DetectionReportType = Literal["initial_screening", "store_retest", "home_self_test"]
DetectionReportViewStatus = Literal["unread", "read"]


class DetectionReportMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    indicator_code: str = Field(..., min_length=1, max_length=64)
    value: Decimal
    unit: str = Field(..., min_length=1, max_length=32)
    measured_at: datetime | None = None


class DetectionReportStoredData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: list[DetectionReportMetric]


class MemberSelfDetectionReportListItem(BaseModel):
    report_id: int
    report_type: DetectionReportType
    detection_time: datetime
    view_status: DetectionReportViewStatus
    summary: str | None
    is_initial_baseline: bool


class MemberSelfDetectionReportPage(BaseModel):
    state: Literal["EMPTY", "AVAILABLE"]
    items: list[MemberSelfDetectionReportListItem]
    next_cursor: str | None


class MemberSelfDetectionReportDetail(MemberSelfDetectionReportListItem):
    state: Literal["AVAILABLE"] = "AVAILABLE"
    metrics: list[DetectionReportMetric]
