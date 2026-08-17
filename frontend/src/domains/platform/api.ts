import { apiRequest } from "@/shared/api/client";
import { getAccessToken } from "@/shared/auth/tokenStorage";
import { env } from "@/shared/config/env";
import type {
  TenantReviewDecisionResponse,
  TenantReviewDetailResponse,
  TenantReviewQueueParams,
  TenantReviewQueueResponse,
  TherapistReviewDecisionPayload,
  TherapistReviewDecisionResult,
  TherapistReviewItem,
  TherapistReviewProfile,
  TherapistReviewQualification,
  TherapistMutationResult,
} from "./types";

export interface InstitutionInvitationPayload {
  institution_name: string;
  institution_type: "HEALTH_STORE" | "LICENSED_CLINIC";
  applicant_phone: string;
  pilot_batch_code: string;
  administrative_region_id: number;
  expires_in_minutes?: number;
}

export interface InstitutionInvitationView {
  invitation_id: string;
  institution_name: string;
  institution_type: string;
  status: string;
  version: number;
  short_code?: string;
  expires_at?: string | null;
}

export interface InstitutionReviewView {
  application_id: string;
  status: string;
  version: number;
  submitted_at?: string | null;
  draft?: { [key: string]: string | string[] | null };
  revisions?: Array<{
    revision_no: number;
    snapshot: { [key: string]: string | string[] | null };
    created_at: string;
  }>;
  materials?: Array<{
    file_id: string;
    license_type: string;
    status: string;
  }>;
}

export interface InstitutionReviewDecisionPayload {
  decision: "APPROVED" | "NEEDS_CORRECTION" | "REJECTED";
  expected_version: number;
  correction_fields: string[];
  reason_code: string | null;
}

export const createInstitutionInvitation = (payload: InstitutionInvitationPayload, key: string) =>
  apiRequest<InstitutionInvitationView>("/api/v1/platform/institution-invitations", {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify(payload),
  });
export const listInstitutionInvitations = () =>
  apiRequest<InstitutionInvitationView[]>("/api/v1/platform/institution-invitations");
export const resendInstitutionInvitation = (id: string, expectedVersion: number, key: string) =>
  apiRequest<InstitutionInvitationView>(`/api/v1/platform/institution-invitations/${id}/resend`, {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify({ expected_version: expectedVersion, expires_in_minutes: 60 }),
  });
export const revokeInstitutionInvitation = (id: string, expectedVersion: number, key: string) =>
  apiRequest<InstitutionInvitationView>(`/api/v1/platform/institution-invitations/${id}/revoke`, {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify({ expected_version: expectedVersion }),
  });
export const listInstitutionReviews = () => apiRequest<InstitutionReviewView[]>("/api/v1/platform/institution-reviews");
export const getInstitutionReviewDetail = (id: string) =>
  apiRequest<InstitutionReviewView>(`/api/v1/platform/institution-reviews/${id}`);
export const decideInstitutionReview = (id: string, payload: InstitutionReviewDecisionPayload, key: string) =>
  apiRequest<InstitutionReviewView>(`/api/v1/platform/institution-reviews/${id}/decision`, {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify(payload),
  });
export const requestPrivateFileAccess = (fileId: string, reauthPassword: string) =>
  apiRequest<{ access_path: string }>(`/api/v1/private-files/${fileId}/access`, {
    method: "POST",
    body: JSON.stringify({
      reason_code: "INSTITUTION_REVIEW",
      reauth_password: reauthPassword,
    }),
  });
export const fetchPrivateFileContent = async (accessPath: string) => {
  const token = getAccessToken();
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${env.apiBaseUrl}${accessPath}`, { headers });
  if (!response.ok) throw new Error("材料读取失败");
  return response.blob();
};

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
    body: JSON.stringify(payload),
  });
}

export function rejectTenantReview(tenantId: number, payload: { reason: string }) {
  return apiRequest<TenantReviewDecisionResponse>(`/api/v1/reviews/tenants/${tenantId}/reject`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export const listTherapistReviews = (kind?: "INITIAL" | "RENEWAL") =>
  apiRequest<{ items: TherapistReviewItem[]; next_cursor?: string }>(
    `/api/v1/platform/therapist-reviews${kind ? `?kind=${kind}` : ""}`,
  );
export const getTherapistReview = (therapistId: string) =>
  apiRequest<{
    profile: TherapistReviewProfile;
    qualifications: TherapistReviewQualification[];
    current_qualification_ids: string[];
    review_item: TherapistReviewItem | null;
  }>(`/api/v1/platform/therapist-reviews/${therapistId}`);
export const decideTherapistReview = (therapistId: string, payload: TherapistReviewDecisionPayload, key: string) =>
  apiRequest<TherapistReviewDecisionResult>(`/api/v1/platform/therapist-reviews/${therapistId}/decision`, {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify(payload),
  });
export const suspendTherapist = (id: string, expectedVersion: number, reasonCode: string, key: string) =>
  apiRequest<TherapistMutationResult>(`/api/v1/platform/therapists/${id}/suspend`, {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify({ expected_version: expectedVersion, reason_code: reasonCode }),
  });
export const resumeTherapist = (id: string, expectedVersion: number, key: string) =>
  apiRequest<TherapistMutationResult>(`/api/v1/platform/therapists/${id}/resume`, {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify({ expected_version: expectedVersion }),
  });
export const exitTherapist = (id: string, expectedVersion: number, reasonCode: string, key: string) =>
  apiRequest<TherapistMutationResult>(`/api/v1/platform/therapists/${id}/exit`, {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify({ expected_version: expectedVersion, reason_code: reasonCode }),
  });
