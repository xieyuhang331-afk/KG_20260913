from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
