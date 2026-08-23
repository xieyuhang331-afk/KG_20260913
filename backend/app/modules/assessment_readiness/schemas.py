from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict


class AssessmentReadinessDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_case_id: UUID
    status: Literal["DATA_INSUFFICIENT", "DATA_SYNC_PENDING", "DISPUTED", "ASSESSMENT_READY"]
    reason_codes: tuple[str, ...]
    missing_indicator_codes: tuple[str, ...] = ()
    expired_indicator_codes: tuple[str, ...] = ()
    disputed_indicator_codes: tuple[str, ...] = ()
    profile_revision_id: UUID | None = None
    policy_version: str | None = None
    projection_status: Literal["CURRENT", "SYNC_PENDING", "UNAVAILABLE"] | None = None
    data_as_of: AwareDatetime | None = None
    generated_at: AwareDatetime | None = None
    assembly_id: UUID | None = None
