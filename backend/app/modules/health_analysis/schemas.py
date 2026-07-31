from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


HealthIndicatorSource = Literal["APP", "STORE", "DEVICE", "REPORT"]


class HealthSummaryUser(BaseModel):
    user_id: int
    status: str
    tenant_id: int | None


class HealthSummaryProfile(BaseModel):
    exists: bool
    gender: str | None = None
    birth_date: date | None = None
    height: Decimal | None = None
    weight: Decimal | None = None
    blood_type: str | None = None
    has_medical_history: bool | None = None
    has_allergy_history: bool | None = None
    has_family_history: bool | None = None
    has_symptoms: bool | None = None
    smoking: str | None = None
    drinking: str | None = None
    sleep_quality: str | None = None
    bowel_urination: str | None = None


class LatestIndicatorSummary(BaseModel):
    indicator_type: str
    display_name: str
    category: str
    value: Decimal
    unit: str
    source: HealthIndicatorSource
    recorded_at: datetime
    created_at: datetime
    quality_flags: list[str] = Field(default_factory=list)


class DataCompleteness(BaseModel):
    profile_completed: bool
    indicator_count: int
    standard_indicator_count: int
    missing_indicator_types: list[str]


class HealthSummary(BaseModel):
    user: HealthSummaryUser
    profile: HealthSummaryProfile
    latest_indicators: list[LatestIndicatorSummary]
    indicator_updated_at: datetime | None
    data_completeness: DataCompleteness


class TrendPoint(BaseModel):
    value: Decimal
    recorded_at: datetime
    source: HealthIndicatorSource


class HealthTrend(BaseModel):
    indicator_type: str
    display_name: str
    category: str
    unit: str
    points: list[TrendPoint]


class AIUserContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int
    status: str
    tenant_id: int | None


class AIProfileContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gender: str | None = None
    birth_date: date | None = None
    height: Decimal | None = None
    weight: Decimal | None = None
    blood_type: str | None = None
    has_medical_history: bool | None = None
    has_allergy_history: bool | None = None
    has_family_history: bool | None = None
    has_symptoms: bool | None = None


class AILatestIndicator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    indicator_type: str
    display_name: str
    category: str
    value: Decimal
    unit: str
    source: HealthIndicatorSource
    recorded_at: datetime


class AITrendWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_at: datetime
    end_at: datetime
    days: int | None = None


class AITrendPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Decimal
    recorded_at: datetime
    source: HealthIndicatorSource


class AITrendContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    indicator_type: str
    display_name: str
    category: str
    unit: str
    window: AITrendWindow
    points: list[AITrendPoint]


class AIDataCompleteness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_completed: bool
    indicator_count: int
    standard_indicator_count: int
    missing_indicator_types: list[str]


class AISourceRefs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_tables: list[str]
    summary_source: str
    trend_source: str
    indicator_ids: list[int] = Field(default_factory=list)


class AISafetyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    no_diagnosis: bool
    no_prescription: bool
    no_treatment_plan: bool
    no_risk_prediction: bool
    no_health_score: bool


class AIHealthInputContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: str = "f004.ai_input.v1"
    user_context: AIUserContext
    profile_context: AIProfileContext
    latest_indicators: list[AILatestIndicator]
    trend_context: list[AITrendContext]
    data_completeness: AIDataCompleteness
    source_refs: AISourceRefs
    safety_policy: AISafetyPolicy
