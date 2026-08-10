export type OrganizationType = "headquarter" | "province" | "city" | "county" | "platform" | "tenant_org";
export type OrganizationCreateType = "province" | "city" | "county";
export type OrganizationStatus = "active" | "inactive" | "archived" | "disabled";
export type OrganizationCompatibilityMode = "canonical" | "legacy";

export interface OrganizationTreeNode {
  organization_id: number;
  parent_id: number | null;
  org_code: string;
  org_name: string;
  org_type: OrganizationType;
  status: OrganizationStatus;
  compatibility_mode: OrganizationCompatibilityMode;
  sort_order: number;
  version: number;
  children: OrganizationTreeNode[];
}

export interface OrganizationDetail {
  organization_id: number;
  parent_id: number | null;
  org_code: string;
  org_name: string;
  org_type: OrganizationType;
  status: OrganizationStatus;
  compatibility_mode: OrganizationCompatibilityMode;
  path_codes: string[];
  path_names: string[];
  sort_order: number;
  version: number;
  admin_user_id: number | null;
}

export interface OrganizationTenant {
  tenant_id: number;
  tenant_code: string;
  name: string;
  type: string;
  province: string | null;
  city: string | null;
  district: string | null;
  grade: string | null;
  status: string;
  organization_id: number;
  organization_path_codes: string[];
}

export interface OrganizationTenantPage {
  items: OrganizationTenant[];
  next_cursor: string | null;
}

export interface OrganizationTenantParams {
  include_descendants: boolean;
  cursor?: string | null;
  page_size?: number;
}

export interface OrganizationAdminCandidate {
  user_id: number;
  display_name: string;
  role: string;
  assignment_status: string;
}

export interface CreateOrganizationRequest {
  org_name: string;
  org_code: string;
  org_type: OrganizationCreateType;
  parent_id: number;
  parent_expected_version: number;
  admin_user_id?: number | null;
}

export interface PatchOrganizationRequest {
  org_name?: string;
  admin_user_id?: number | null;
  expected_version: number;
}

export interface OrganizationOrderChild {
  organization_id: number;
  expected_version: number;
  sort_order: number;
}

export interface ReorderOrganizationChildrenRequest {
  parent_expected_version: number;
  items: OrganizationOrderChild[];
}

export interface OrganizationStateRequest {
  expected_version: number;
  reason_code: "PLATFORM_GOVERNANCE" | "COMPLIANCE_HOLD" | "GOVERNANCE_RESTORED";
}

export interface InstitutionOrganizationPathNode {
  organization_id: number;
  org_code: string;
  org_name: string;
  org_type: OrganizationType;
  status: OrganizationStatus;
  compatibility_mode: OrganizationCompatibilityMode;
}

export interface InstitutionOrganizationProfile {
  tenant_id: number;
  tenant_code: string;
  tenant_name: string;
  tenant_type: string;
  tenant_status: string;
  assignment_status: "assigned" | "unassigned";
  organization_id: number | null;
  organization_path: InstitutionOrganizationPathNode[];
  compatibility_mode: OrganizationCompatibilityMode | null;
}

export interface OrganizationTreeWireNode {
  id: number;
  parent_id: number | null;
  org_code: string;
  org_name: string;
  org_type: OrganizationType;
  status: OrganizationStatus;
  compatibility_mode: OrganizationCompatibilityMode;
  sort_order: number;
  version: number;
  has_children: boolean;
  children: OrganizationTreeWireNode[];
  [key: string]: unknown;
}

export interface OrganizationTreeWireResponse {
  items: OrganizationTreeWireNode[];
}

export interface OrganizationDetailWire {
  id: number;
  parent_id: number | null;
  org_code: string;
  org_name: string;
  org_type: OrganizationType;
  status: OrganizationStatus;
  compatibility_mode: OrganizationCompatibilityMode;
  path_codes: string[];
  path_names: string[];
  sort_order: number;
  version: number;
  admin_user_id: number | null;
  [key: string]: unknown;
}

export interface OrganizationTenantPageWire {
  items: Array<OrganizationTenant & { [key: string]: unknown }>;
  next_cursor: string | null;
  [key: string]: unknown;
}

export interface InstitutionOrganizationProfileWire {
  tenant_id: number;
  tenant_code: string;
  tenant_name: string;
  tenant_type: string;
  tenant_status: string;
  assignment_status: "assigned" | "unassigned";
  organization_id: number | null;
  organization_path: Array<{
    id: number;
    org_code: string;
    org_name: string;
    org_type: OrganizationType;
    status: OrganizationStatus;
    compatibility_mode: OrganizationCompatibilityMode;
  }>;
  compatibility_mode: OrganizationCompatibilityMode | null;
  [key: string]: unknown;
}

export interface OrganizationAdminMutationResponse {
  id: number;
  parent_id: number | null;
  org_code: string;
  org_name: string;
  org_type: "headquarter" | OrganizationCreateType;
  status: "active" | "inactive" | "archived";
  compatibility_mode: "canonical";
  sort_order: number;
  version: number;
  admin_user_id: number | null;
  created_at: string;
  updated_at: string;
  [key: string]: unknown;
}

export interface OrganizationChildrenOrderResponse {
  parent_id: number;
  parent_version: number;
  items: Array<{ organization_id: number; sort_order: number; version: number }>;
}

export interface OrganizationStateResponse {
  id: number;
  status: "active" | "inactive";
  version: number;
  updated_at: string;
}

export interface OrganizationAdminCandidatePageWire {
  items: Array<OrganizationAdminCandidate & { [key: string]: unknown }>;
  next_cursor: string | null;
}
