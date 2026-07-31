import { apiRequest } from "@/shared/api/client";
import type {
  TenantReviewDecisionResponse,
  TenantReviewDetailResponse,
  TenantReviewQueueParams,
  TenantReviewQueueResponse
} from "./types";

export function listTenantReviewQueue(params: TenantReviewQueueParams = {}) {
  const query = new URLSearchParams();

  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value).trim() !== "") {
      query.set(key, String(value));
    }
  });

  const suffix = query.toString() ? `?${query.toString()}` : "";
  return apiRequest<TenantReviewQueueResponse>(`/api/v1/reviews/queue/tenant${suffix}`);
}

export function getTenantReviewDetail(tenantId: number) {
  return apiRequest<TenantReviewDetailResponse>(`/api/v1/reviews/tenants/${tenantId}`);
}

export function approveTenantReview(tenantId: number, payload: { comment?: string; grade?: string }) {
  return apiRequest<TenantReviewDecisionResponse>(`/api/v1/reviews/tenants/${tenantId}/approve`, {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function rejectTenantReview(tenantId: number, payload: { reason: string }) {
  return apiRequest<TenantReviewDecisionResponse>(`/api/v1/reviews/tenants/${tenantId}/reject`, {
    method: "POST",
    body: JSON.stringify(payload)
  });
}
