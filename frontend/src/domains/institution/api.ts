import { apiRequest } from "@/shared/api/client";
import type {
  MyTenantApplicationsParams,
  MyTenantApplicationsResponse,
  TenantApplicationCreate,
  TenantApplicationCreated,
  TenantApplicationDetail,
} from "./types";

export function createTenantApplication(payload: TenantApplicationCreate) {
  return apiRequest<TenantApplicationCreated>("/api/v1/tenants", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function listMyTenantApplications(params: MyTenantApplicationsParams = {}) {
  const query = new URLSearchParams();

  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value).trim() !== "") {
      query.set(key, String(value));
    }
  });

  const suffix = query.toString() ? `?${query.toString()}` : "";
  return apiRequest<MyTenantApplicationsResponse>(`/api/v1/tenants/my-applications${suffix}`);
}

export function getTenantApplicationDetail(tenantId: number) {
  return apiRequest<TenantApplicationDetail>(`/api/v1/tenants/${tenantId}/application-status`);
}
