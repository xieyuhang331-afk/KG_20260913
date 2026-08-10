import { apiRequest } from "@/shared/api/client";
import type {
  CreateOrganizationRequest,
  InstitutionOrganizationProfile,
  InstitutionOrganizationProfileWire,
  OrganizationAdminCandidate,
  OrganizationAdminCandidatePageWire,
  OrganizationAdminMutationResponse,
  OrganizationChildrenOrderResponse,
  OrganizationDetail,
  OrganizationDetailWire,
  OrganizationStateRequest,
  OrganizationStateResponse,
  OrganizationTenantPage,
  OrganizationTenantPageWire,
  OrganizationTenantParams,
  OrganizationTreeNode,
  OrganizationTreeWireNode,
  OrganizationTreeWireResponse,
  PatchOrganizationRequest,
  ReorderOrganizationChildrenRequest,
} from "./组织基础类型";

export function listOrganizationTree(includeArchived = false): Promise<OrganizationTreeNode[]> {
  const query = new URLSearchParams({ include_archived: String(includeArchived) });
  return apiRequest<OrganizationTreeWireResponse>(`/api/v1/platform/organizations/tree?${query}`).then((response) =>
    response.items.map(mapTreeNode),
  );
}

export function getOrganizationDetail(organizationId: number): Promise<OrganizationDetail> {
  return apiRequest<OrganizationDetailWire>(`/api/v1/platform/organizations/${organizationId}`).then(mapDetail);
}

export async function listOrganizationTenants(
  organizationId: number,
  params: OrganizationTenantParams,
): Promise<OrganizationTenantPage> {
  const query = new URLSearchParams({
    include_descendants: String(params.include_descendants),
  });
  if (params.cursor) query.set("cursor", params.cursor);
  query.set("page_size", String(params.page_size ?? 20));
  const response = await apiRequest<OrganizationTenantPageWire>(
    `/api/v1/platform/organizations/${organizationId}/tenants?${query}`,
  );
  return {
    items: response.items.map((item) => ({
      tenant_id: item.tenant_id,
      tenant_code: item.tenant_code,
      name: item.name,
      type: item.type,
      province: item.province,
      city: item.city,
      district: item.district,
      grade: item.grade,
      status: item.status,
      organization_id: item.organization_id,
      organization_path_codes: [...item.organization_path_codes],
    })),
    next_cursor: response.next_cursor,
  };
}

export async function listOrganizationAdminCandidates(
  organizationId: number,
  signal?: AbortSignal,
): Promise<OrganizationAdminCandidate[]> {
  const candidates: OrganizationAdminCandidate[] = [];
  let cursor: string | null = null;
  do {
    const query = new URLSearchParams({ page_size: "100" });
    if (cursor) query.set("cursor", cursor);
    const response = await apiRequest<OrganizationAdminCandidatePageWire>(
      `/api/v1/platform/organizations/${organizationId}/admin-candidates?${query}`,
      { signal },
    );
    candidates.push(
      ...response.items.map((item) => ({
        user_id: item.user_id,
        display_name: item.display_name,
        role: item.role,
        assignment_status: item.assignment_status,
      })),
    );
    cursor = response.next_cursor;
  } while (cursor);
  return candidates;
}

export function createOrganization(payload: CreateOrganizationRequest): Promise<OrganizationAdminMutationResponse> {
  return apiRequest<OrganizationAdminMutationResponse>("/api/v1/platform/organizations", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function patchOrganization(
  organizationId: number,
  payload: PatchOrganizationRequest,
): Promise<OrganizationAdminMutationResponse> {
  return apiRequest<OrganizationAdminMutationResponse>(`/api/v1/platform/organizations/${organizationId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function reorderOrganizationChildren(
  parentId: number,
  payload: ReorderOrganizationChildrenRequest,
): Promise<OrganizationChildrenOrderResponse> {
  return apiRequest<OrganizationChildrenOrderResponse>(`/api/v1/platform/organizations/${parentId}/children/order`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function activateOrganization(
  organizationId: number,
  payload: OrganizationStateRequest,
): Promise<OrganizationStateResponse> {
  return organizationStateMutation(organizationId, "activate", payload);
}

export function deactivateOrganization(
  organizationId: number,
  payload: OrganizationStateRequest,
): Promise<OrganizationStateResponse> {
  return organizationStateMutation(organizationId, "deactivate", payload);
}

export function getMyOrganization(): Promise<InstitutionOrganizationProfile> {
  return apiRequest<InstitutionOrganizationProfileWire>("/api/v1/organizations/me").then((item) => ({
    tenant_id: item.tenant_id,
    tenant_code: item.tenant_code,
    tenant_name: item.tenant_name,
    tenant_type: item.tenant_type,
    tenant_status: item.tenant_status,
    assignment_status: item.assignment_status,
    organization_id: item.organization_id,
    organization_path: item.organization_path.map((node) => ({
      organization_id: node.id,
      org_code: node.org_code,
      org_name: node.org_name,
      org_type: node.org_type,
      status: node.status,
      compatibility_mode: node.compatibility_mode,
    })),
    compatibility_mode: item.compatibility_mode,
  }));
}

function organizationStateMutation(
  organizationId: number,
  action: "activate" | "deactivate",
  payload: OrganizationStateRequest,
) {
  return apiRequest<OrganizationStateResponse>(`/api/v1/platform/organizations/${organizationId}/${action}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

function mapTreeNode(item: OrganizationTreeWireNode): OrganizationTreeNode {
  return {
    organization_id: item.id,
    parent_id: item.parent_id,
    org_code: item.org_code,
    org_name: item.org_name,
    org_type: item.org_type,
    status: item.status,
    compatibility_mode: item.compatibility_mode,
    sort_order: item.sort_order,
    version: item.version,
    children: item.children.map(mapTreeNode),
  };
}

function mapDetail(item: OrganizationDetailWire): OrganizationDetail {
  return {
    organization_id: item.id,
    parent_id: item.parent_id,
    org_code: item.org_code,
    org_name: item.org_name,
    org_type: item.org_type,
    status: item.status,
    compatibility_mode: item.compatibility_mode,
    path_codes: [...item.path_codes],
    path_names: [...item.path_names],
    sort_order: item.sort_order,
    version: item.version,
    admin_user_id: item.admin_user_id,
  };
}
