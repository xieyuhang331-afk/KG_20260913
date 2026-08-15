import { apiRequest } from "@/shared/api/client";
import type { MyTenantApplicationsParams, MyTenantApplicationsResponse, TenantApplicationDetail } from "./types";

export interface InstitutionActivationPayload {
  invitation_id: string;
  phone: string;
  short_code: string;
  password: string;
  totp_secret: string;
  totp_code: string;
}

export interface OnboardingDraftPayload {
  credit_code: string;
  legal_representative_name: string;
  registered_address: string;
  service_address: string;
  contact_name: string;
  contact_phone: string;
  contact_email: string;
  service_tags: string[];
  expected_version: number;
}

export interface LicenseBindingPayload {
  license_type: "BUSINESS_LICENSE" | "MEDICAL_INSTITUTION_LICENSE";
  private_file_id: string;
  license_no?: string;
}

export interface OnboardingSubmitPayload {
  expected_version: number;
  licenses: LicenseBindingPayload[];
}

export interface OnboardingResubmitPayload extends OnboardingDraftPayload {
  licenses: LicenseBindingPayload[];
}

export interface OnboardingApplication {
  application_id: string;
  institution_type: "HEALTH_STORE" | "LICENSED_CLINIC";
  status: string;
  version: number;
  correction_fields: string[];
  correction_reason_code?: string | null;
  licenses: LicenseBindingPayload[];
  draft: { [key: string]: string | string[] | null };
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

export const activateInstitution = (payload: InstitutionActivationPayload, idempotencyKey: string) =>
  apiRequest<{ application_id: string; status: string }>("/api/v1/institution-onboarding/activate", {
    method: "POST",
    skipAuth: true,
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(payload),
  });

export const getOnboardingApplication = () =>
  apiRequest<OnboardingApplication>("/api/v1/institution-onboarding/application");
export const saveOnboardingDraft = (payload: OnboardingDraftPayload) =>
  apiRequest<OnboardingApplication>("/api/v1/institution-onboarding/application", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
export const submitOnboardingApplication = (payload: OnboardingSubmitPayload, key: string) =>
  apiRequest<OnboardingApplication>("/api/v1/institution-onboarding/application/submit", {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify(payload),
  });
export const getOnboardingCorrections = () =>
  apiRequest<Pick<OnboardingApplication, "status" | "correction_fields" | "correction_reason_code" | "version">>(
    "/api/v1/institution-onboarding/application/corrections",
  );
export const resubmitOnboardingApplication = (payload: OnboardingResubmitPayload, key: string) =>
  apiRequest<OnboardingApplication>("/api/v1/institution-onboarding/application/resubmit", {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify(payload),
  });
export const initiatePrivateFileUpload = (payload: {
  purpose: LicenseBindingPayload["license_type"];
  size: number;
  mime_type: string;
  sha256: string;
}) =>
  apiRequest<{ file_id: string }>("/api/v1/private-files/uploads", {
    method: "POST",
    body: JSON.stringify(payload),
  });
export const uploadPrivateFileContent = (fileId: string, file: File) =>
  apiRequest<{ uploaded: boolean }>(`/api/v1/private-files/uploads/${fileId}/content`, {
    method: "PUT",
    headers: { "Content-Type": "application/octet-stream" },
    body: file,
  });
export const completePrivateFileUpload = (
  fileId: string,
  payload: { size: number; mime_type: string; sha256: string },
) =>
  apiRequest<{ status: string }>(`/api/v1/private-files/uploads/${fileId}/complete`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
export const getPrivateFileMetadata = (fileId: string) =>
  apiRequest<{ status: string }>(`/api/v1/private-files/${fileId}`);
