export type TenantApplicationStatus = "pending" | "active" | "rejected";

export interface MyTenantApplicationItem {
  tenant_id: number;
  tenant_code: string;
  name: string;
  status: TenantApplicationStatus;
  province: string;
  city: string;
  contact_name: string | null;
  contact_phone: string | null;
  submitted_at: string;
  reviewed_at: string | null;
  approved_at: string | null;
  reject_reason: string | null;
}

export interface MyTenantApplicationsResponse {
  items: MyTenantApplicationItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface MyTenantApplicationsParams {
  status?: TenantApplicationStatus;
  page?: number;
  page_size?: number;
}

export interface TenantApplicationDetail {
  tenant_id: number;
  tenant_code: string;
  name: string;
  status: TenantApplicationStatus;
  submitted_at: string;
  reviewed_at: string | null;
  approved_at: string | null;
  reject_reason: string | null;
}

export type TherapistStatus =
  | "ACTIVATED"
  | "DRAFT"
  | "SUBMITTED"
  | "UNDER_REVIEW"
  | "NEEDS_CORRECTION"
  | "RESUBMITTED"
  | "APPROVED_ACTIVE"
  | "SUSPENDED"
  | "EXITED"
  | "REJECTED";

export interface TherapistInvitation {
  invitation_id: string;
  masked_phone: string;
  status: "INVITED" | "ACTIVATED" | "EXPIRED" | "REVOKED";
  expires_at: string;
  issued_at: string;
  activated_at: string | null;
  revoked_at: string | null;
  version: number;
  short_code?: string;
}

export interface TherapistProfile {
  therapist_id: string;
  tenant_id: string;
  display_name: string | null;
  practice_summary: string | null;
  status: TherapistStatus;
  service_tags: Array<"HYPERTENSION" | "GLUCOSE_METABOLISM" | "DYSLIPIDEMIA" | "OBESITY"> | null;
  capacity_limit: 30;
  active_case_count: number;
  qualification_valid_until: string | null;
  current_revision_no: number;
  version: number;
  updated_at: string;
}

export interface TherapistQualification {
  qualification_version_id: string;
  qualification_type: "METABOLIC_HEALTH_PRACTICE";
  masked_certificate_no: string;
  issuer_name: string;
  valid_from: string;
  valid_until: string;
  derived_review_status: "SUBMITTED" | "APPROVED" | "REJECTED" | "SUPERSEDED";
  attachment_count: number;
  version_no: number;
}

export interface ServiceReadiness {
  tenant_id: string;
  readiness_status: "NOT_READY" | "SERVICE_READY";
  reason_codes: string[];
  qualified_therapist_count: number;
  computed_at: string;
  evidence_version: number;
}
