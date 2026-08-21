from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, SecretStr


class UploadInitiateRequest(BaseModel):
    purpose: Literal[
        "BUSINESS_LICENSE",
        "MEDICAL_INSTITUTION_LICENSE",
        "THERAPIST_QUALIFICATION",
        "DETECTION_REPORT",
    ]
    size: int = Field(ge=1, le=10 * 1024 * 1024)
    mime_type: Literal["application/pdf", "image/jpeg", "image/png"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class UploadCompleteRequest(BaseModel):
    size: int = Field(ge=1, le=10 * 1024 * 1024)
    mime_type: Literal["application/pdf", "image/jpeg", "image/png"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class FileAccessRequest(BaseModel):
    reason_code: str = Field(min_length=2, max_length=64)
    reauth_password: SecretStr | None = Field(default=None, min_length=12, max_length=128)
