from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator


PositiveInt = Annotated[int, Field(strict=True, ge=1)]
SortOrder = Annotated[int, Field(strict=True, ge=0, le=1_000_000)]
CanonicalType = Literal["headquarter", "province", "city", "county"]
CreateType = Literal["province", "city", "county"]
ReadType = Literal["headquarter", "province", "city", "county", "platform", "tenant_org"]
ReadStatus = Literal["active", "inactive", "archived", "disabled"]
CompatibilityMode = Literal["canonical", "legacy"]


class OrganizationTreeNodeResponse(BaseModel):
    id: PositiveInt
    parent_id: PositiveInt | None
    org_code: str
    org_name: str
    org_type: ReadType
    status: ReadStatus
    compatibility_mode: CompatibilityMode
    sort_order: int
    version: PositiveInt
    has_children: bool
    children: list["OrganizationTreeNodeResponse"]


class OrganizationTreeResponse(BaseModel):
    items: list[OrganizationTreeNodeResponse]


class OrganizationDetailResponse(BaseModel):
    id: PositiveInt
    parent_id: PositiveInt | None
    org_code: str
    org_name: str
    org_type: ReadType
    status: ReadStatus
    compatibility_mode: CompatibilityMode
    path_codes: list[str]
    path_names: list[str]
    sort_order: int
    version: PositiveInt
    admin_user_id: PositiveInt | None


class OrganizationTenantItem(BaseModel):
    tenant_id: PositiveInt
    tenant_code: str
    name: str
    type: str
    province: str | None
    city: str | None
    district: str | None
    grade: str | None
    status: str
    organization_id: PositiveInt
    organization_path_codes: list[str]


class OrganizationTenantPage(BaseModel):
    items: list[OrganizationTenantItem]
    next_cursor: str | None


class OrganizationAdminCandidate(BaseModel):
    user_id: PositiveInt
    display_name: str
    role: Literal["province_admin", "city_admin"]
    assignment_status: Literal["unassigned", "assigned_to_current"]


class OrganizationAdminCandidatePage(BaseModel):
    items: list[OrganizationAdminCandidate]
    next_cursor: str | None


class MyOrganizationPathItem(BaseModel):
    id: PositiveInt
    org_code: str
    org_name: str
    org_type: ReadType
    status: ReadStatus
    compatibility_mode: CompatibilityMode


class MyOrganizationResponse(BaseModel):
    tenant_id: PositiveInt
    tenant_code: str
    tenant_name: str
    tenant_type: str
    tenant_status: str
    assignment_status: Literal["assigned", "unassigned"]
    organization_id: PositiveInt | None
    organization_path: list[MyOrganizationPathItem]
    compatibility_mode: CompatibilityMode | None


class OrganizationCreateRequest(BaseModel):
    org_name: str
    org_code: str
    org_type: CreateType
    parent_id: PositiveInt
    parent_expected_version: PositiveInt
    admin_user_id: PositiveInt | None = None


class OrganizationPatchRequest(BaseModel):
    org_name: str | None = None
    admin_user_id: PositiveInt | None = None
    expected_version: PositiveInt


class OrganizationAdminMutationResponse(BaseModel):
    id: PositiveInt
    parent_id: PositiveInt | None
    org_code: str
    org_name: str
    org_type: CanonicalType
    status: Literal["active", "inactive", "archived"]
    compatibility_mode: Literal["canonical"]
    sort_order: int
    version: PositiveInt
    admin_user_id: PositiveInt | None
    created_at: datetime
    updated_at: datetime


class OrganizationChildOrderItem(BaseModel):
    organization_id: PositiveInt
    expected_version: PositiveInt
    sort_order: SortOrder


class OrganizationChildrenOrderRequest(BaseModel):
    parent_expected_version: PositiveInt
    items: list[OrganizationChildOrderItem]

    @field_validator("items")
    @classmethod
    def items_must_not_repeat(cls, value):
        ids = [item.organization_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate organization_id")
        return value


class OrganizationChildOrderResponse(BaseModel):
    organization_id: PositiveInt
    sort_order: int
    version: PositiveInt


class OrganizationChildrenOrderResponse(BaseModel):
    parent_id: PositiveInt
    parent_version: PositiveInt
    items: list[OrganizationChildOrderResponse]


class OrganizationStateRequest(BaseModel):
    expected_version: PositiveInt
    reason_code: Literal["PLATFORM_GOVERNANCE", "COMPLIANCE_HOLD", "GOVERNANCE_RESTORED"]


class OrganizationStateResponse(BaseModel):
    id: PositiveInt
    status: Literal["active", "inactive"]
    version: PositiveInt
    updated_at: datetime
