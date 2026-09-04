from __future__ import annotations

from datetime import datetime
from typing import Generic, Literal, TypeVar

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
    reason_code: Literal[
        "OWNER_DOWNLOAD", "INSTITUTION_REVIEW", "DETECTION_REPORT"
    ]
    reauth_password: SecretStr | None = Field(default=None, min_length=12, max_length=128)


class PrivateFileMetadata(BaseModel):
    file_id: str
    purpose: str
    size: int
    mime_type: str
    status: str
    bound: bool
    failure_code: str | None = None
    retryable: bool
    next_poll_after_seconds: int | None = None


class PrivateFileInitiated(BaseModel):
    file_id: str
    status: str
    upload_path: str
    expires_at: datetime


class PrivateFileUploadResult(BaseModel):
    file_id: str
    status: str | None = None
    uploaded: bool | None = None
    dispatch_pending: bool | None = None


class PrivateFileAccessResponse(BaseModel):
    file_id: str
    content_path: str
    access_credential: str = Field(min_length=32, max_length=256)
    expires_at_epoch: int


class PrivateFileDeleteResult(BaseModel):
    file_id: str
    deleted: bool


_T = TypeVar("_T")


class PrivateFileEnvelope(BaseModel, Generic[_T]):
    code: int
    message: str
    data: _T
