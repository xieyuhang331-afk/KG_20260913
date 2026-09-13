import { apiRequest } from "@/shared/api/client";
import type { UUIDv7 } from "@/shared/api/slice3";
import type {
  MyTenantApplicationsParams,
  MyTenantApplicationsResponse,
  CursorPage,
  CursorParams,
  ServiceReadiness,
  ServiceReadinessEvidence,
  TenantApplicationDetail,
  TherapistInvitation,
  TherapistProfile,
  TherapistQualification,
  TherapistStatus,
  MemberEnrollmentDetail,
  MemberEnrollmentSummary,
  MemberIdentityStatus,
  MemberInvitation,
  MemberInvitationMode,
  MemberInvitationSecret,
  MemberInvitationStatus,
  PreparingServiceCase,
  PrimaryAssignment,
  ServiceScopeTag,
  AssessmentReadiness,
  DetectionReportMetadata,
  HealthIndicatorFact,
  InstitutionHealthRecord,
} from "./types";

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
  valid_from: string;
  valid_until: string;
}

export interface OnboardingSubmitPayload {
  expected_version: number;
  licenses: LicenseBindingPayload[];
}

export interface OnboardingResubmitPayload extends Partial<Omit<OnboardingDraftPayload, "expected_version">> {
  expected_version: number;
  licenses: LicenseBindingPayload[];
}

export interface OnboardingApplication {
  application_id: string;
  institution_type: "HEALTH_STORE" | "LICENSED_CLINIC";
  status: string;
  version: number;
  tenant_id?: string | null;
  tenant_active: boolean;
  service_ready: boolean;
  current_revision_no?: number;
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

export const createTherapistInvitation = (payload: { phone: string; expires_in_minutes: number }, key: string) =>
  apiRequest<TherapistInvitation>("/api/v1/institution/therapist-invitations", {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify(payload),
  });
export const listTherapistInvitations = (params: CursorParams & { status?: TherapistInvitation["status"] } = {}) =>
  apiRequest<CursorPage<TherapistInvitation>>(`/api/v1/institution/therapist-invitations${therapistQuery(params)}`);
export const revokeTherapistInvitation = (id: string, expectedVersion: number, key: string) =>
  apiRequest<TherapistInvitation>(`/api/v1/institution/therapist-invitations/${id}/revoke`, {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify({ expected_version: expectedVersion, reason_code: "INVITE_WITHDRAWN" }),
  });
export const listTherapists = (params: CursorParams & { status?: TherapistStatus } = {}) =>
  apiRequest<CursorPage<TherapistProfile>>(`/api/v1/institution/therapists${therapistQuery(params)}`);
export const getTherapist = (id: string) =>
  apiRequest<{ profile: TherapistProfile; qualifications: TherapistQualification[] }>(
    `/api/v1/institution/therapists/${id}`,
  );
export const getServiceReadiness = () => apiRequest<ServiceReadiness>("/api/v1/institution/service-readiness");
export const getServiceReadinessEvidence = (params: CursorParams = {}) =>
  apiRequest<CursorPage<ServiceReadinessEvidence>>(
    `/api/v1/institution/service-readiness/evidence${therapistQuery(params)}`,
  );

export const createMemberInvitation = (payload: { mode: MemberInvitationMode; phone: string }, key: string) =>
  apiRequest<MemberInvitationSecret>("/api/v1/institution/member-invitations", {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify(payload),
  });

export const listMemberInvitations = (params: CursorParams & { status?: MemberInvitationStatus } = {}) =>
  apiRequest<CursorPage<MemberInvitation>>(`/api/v1/institution/member-invitations${therapistQuery(params)}`);

export const resendMemberInvitation = (id: UUIDv7, expectedVersion: number, key: string) =>
  apiRequest<MemberInvitationSecret>(`/api/v1/institution/member-invitations/${id}/resend`, {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify({ expected_version: expectedVersion }),
  });

export const revokeMemberInvitation = (
  id: UUIDv7,
  expectedVersion: number,
  reasonCode: "DUPLICATE_INVITATION" | "WRONG_RECIPIENT" | "INSTITUTION_CANCELLED",
  key: string,
) =>
  apiRequest<MemberInvitation>(`/api/v1/institution/member-invitations/${id}/revoke`, {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify({ expected_version: expectedVersion, reason_code: reasonCode }),
  });

export const listMemberEnrollments = (params: CursorParams & { status?: string } = {}) =>
  apiRequest<CursorPage<MemberEnrollmentSummary>>(`/api/v1/institution/member-enrollments${therapistQuery(params)}`);

export const getMemberEnrollment = (id: UUIDv7, signal?: AbortSignal) =>
  apiRequest<MemberEnrollmentDetail>(`/api/v1/institution/member-enrollments/${id}`, { signal });

export interface InstitutionIdentityCheckPayload {
  revision_id: UUIDv7;
  decision: "CHECKED" | "NEEDS_CORRECTION" | "REJECTED";
  reason_code: string | null;
  correction_fields: Array<"real_name" | "id_number">;
  attestation_code: "OFFLINE_IDENTITY_CHECKED" | "PRINCIPAL_PRESENT_AND_AUTHORIZED_PROXY" | null;
  expected_version: number;
}

export const checkMemberIdentity = (enrollmentId: UUIDv7, payload: InstitutionIdentityCheckPayload, key: string) =>
  apiRequest<MemberIdentityStatus>(`/api/v1/institution/member-enrollments/${enrollmentId}/identity-check`, {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify(payload),
  });

export const assignPrimaryTherapist = (
  enrollmentId: UUIDv7,
  payload: { therapist_id: UUIDv7; service_scope_tags: ServiceScopeTag[]; expected_version: number },
  key: string,
) =>
  apiRequest<PrimaryAssignment>(`/api/v1/institution/member-enrollments/${enrollmentId}/primary-assignments`, {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify(payload),
  });

export const cancelPrimaryAssignment = (
  assignmentId: UUIDv7,
  expectedVersion: number,
  reasonCode: "MEMBER_UNAVAILABLE" | "THERAPIST_UNAVAILABLE" | "INSTITUTION_CANCELLED",
  key: string,
) =>
  apiRequest<PrimaryAssignment>(`/api/v1/institution/primary-assignments/${assignmentId}/cancel`, {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify({ expected_version: expectedVersion, reason_code: reasonCode }),
  });

export const getPreparingServiceCase = (caseId: UUIDv7) =>
  apiRequest<PreparingServiceCase>(`/api/v1/institution/service-cases/${caseId}`);

export const getInstitutionHealthRecord = (caseId: UUIDv7, signal?: AbortSignal) =>
  apiRequest<InstitutionHealthRecord>(`/api/v1/institution/service-cases/${caseId}/health-record`, { signal });

export const listInstitutionDetectionReports = (caseId: UUIDv7, params: CursorParams = {}, signal?: AbortSignal) =>
  apiRequest<CursorPage<DetectionReportMetadata>>(
    `/api/v1/institution/service-cases/${caseId}/detection-reports${therapistQuery(params)}`,
    { signal },
  );

export const getInstitutionLatestHealthIndicators = (caseId: UUIDv7, signal?: AbortSignal) =>
  apiRequest<{ items: HealthIndicatorFact[] }>(`/api/v1/institution/service-cases/${caseId}/health-indicators/latest`, {
    signal,
  });

export const getAssessmentReadiness = (caseId: UUIDv7, signal?: AbortSignal) =>
  apiRequest<AssessmentReadiness>(`/api/v1/service-cases/${caseId}/assessment-readiness`, { signal });

function therapistQuery(params: object) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") query.set(key, String(value));
  }
  const value = query.toString();
  return value ? `?${value}` : "";
}
