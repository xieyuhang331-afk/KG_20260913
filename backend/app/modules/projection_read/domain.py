from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Generic, TypeVar


class ProjectionReadError(Exception):
    code = "PROJECTION_READ_ERROR"


class ProjectionInvalidRequest(ProjectionReadError):
    code = "PROJECTION_INVALID_REQUEST"


class ProjectionGenerationUnavailable(ProjectionReadError):
    code = "PROJECTION_GENERATION_UNAVAILABLE"


class ProjectionAccessDenied(ProjectionReadError):
    code = "PROJECTION_ACCESS_DENIED"


class ProjectionScopeDenied(ProjectionReadError):
    code = "PROJECTION_SCOPE_DENIED"


class ProjectionIndicatorDenied(ProjectionReadError):
    code = "PROJECTION_INDICATOR_DENIED"


class ProjectionReadUnavailable(ProjectionReadError):
    code = "PROJECTION_READ_UNAVAILABLE"


@dataclass(frozen=True)
class ProjectionReadPrincipal:
    actor_id: int
    tenant_id: int
    actor_type: str
    request_id: str


@dataclass(frozen=True)
class OrganizationReadGrant:
    tenant_id: int
    allowed_root_organization_ids: tuple[int, ...]


@dataclass(frozen=True)
class HealthReadGrant:
    tenant_id: int
    subject_user_id: int
    allowed_indicator_codes: tuple[str, ...]
    authorization_basis: str


@dataclass(frozen=True)
class ProjectionGenerationDTO:
    generation_id: int
    projection_version: int
    ready_at: datetime


@dataclass(frozen=True)
class OrganizationProjectionNodeDTO:
    generation_id: int
    organization_id: int
    parent_id: int | None
    org_code: str
    org_name: str
    org_type: str
    status: str
    sort_order: int
    path_ids: tuple[int, ...]
    path_codes: tuple[str, ...]
    scope_eligible: bool


@dataclass(frozen=True)
class HealthProjectionFactDTO:
    generation_id: int
    fact_id: int
    subject_user_id: int
    indicator_code: str
    numeric_value: Decimal
    unit: str
    measured_at: datetime
    received_at: datetime
    source_type: str
    business_day: date


@dataclass(frozen=True)
class HealthProjectionSelectionDTO:
    generation_id: int
    subject_user_id: int
    indicator_code: str
    business_day: date
    winner_fact_id: int
    rule_version: str


@dataclass(frozen=True)
class OrganizationChildCursor:
    sort_order: int
    organization_id: int


@dataclass(frozen=True)
class HealthFactCursor:
    measured_at: datetime
    fact_id: int


@dataclass(frozen=True)
class HealthSelectionCursor:
    business_day: date
    indicator_code: str
    winner_fact_id: int


T = TypeVar("T")
C = TypeVar("C")


@dataclass(frozen=True)
class ProjectionPageDTO(Generic[T, C]):
    generation: ProjectionGenerationDTO
    items: tuple[T, ...]
    next_cursor: C | None
