export interface TenantReviewQueueItem {
  tenant_id: number;
  tenant_code: string;
  name: string;
  type: string;
  credit_code: string | null;
  province: string;
  city: string;
  district: string | null;
  contact_name: string | null;
  contact_phone: string | null;
  status: string;
  submitted_at: string;
  attachment_count: number;
}

export interface TenantReviewQueueResponse {
  items: TenantReviewQueueItem[];
  page: number;
  page_size: number;
  total: number;
}

export interface TenantReviewDetailTenant {
  id: number;
  tenant_code: string;
  name: string;
  short_name: string | null;
  type: string;
  credit_code: string | null;
  license_no: string | null;
  license_image: string | null;
  legal_person_name: string | null;
  province: string;
  city: string;
  district: string | null;
  address: string | null;
  grade: string | null;
}

export interface TenantReviewDetailContact {
  contact_name: string | null;
  contact_phone: string | null;
  contact_email: string | null;
}

export interface TenantReviewDetailAttachment {
  id: number;
  file_type: string;
  file_url: string;
  created_at: string;
}

export interface TenantReviewDetailStatus {
  current: string;
  reviewed_by: number | null;
  reviewed_at: string | null;
  reject_reason: string | null;
  approved_at: string | null;
}

export interface TenantReviewDetailResponse {
  tenant: TenantReviewDetailTenant;
  contact: TenantReviewDetailContact;
  attachments: TenantReviewDetailAttachment[];
  status: TenantReviewDetailStatus;
  submitted_at: string;
}

export interface TenantReviewDecisionResponse {
  tenant_id: number;
  status: string;
  reviewed_by: number;
  reviewed_at: string;
}

export interface TenantReviewQueueParams {
  page?: number;
  page_size?: number;
  keyword?: string;
  province?: string;
  city?: string;
}

export interface TherapistReviewItem {
  review_item_id: string;
  therapist_id: string;
  revision_id: string;
  qualification_version_id?: string | null;
  review_kind: "INITIAL" | "RENEWAL";
  status: "QUEUED" | "UNDER_REVIEW" | "DECIDED";
  created_at: string;
  version: number;
}

export interface TherapistReviewDecisionPayload {
  decision: "NEEDS_CORRECTION" | "REJECTED" | "APPROVED";
  reason_code?: string | null;
  profile_fields: string[];
  qualification_targets: string[];
  qualification_outcomes: Record<string, "APPROVED" | "REJECTED">;
  expected_version: number;
}

export interface TherapistReviewProfile {
  therapist_id: string;
  tenant_id: string;
  display_name: string | null;
  practice_summary: string | null;
  status:
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
  service_tags: Array<"HYPERTENSION" | "GLUCOSE_METABOLISM" | "DYSLIPIDEMIA" | "OBESITY"> | null;
  capacity_limit: 30;
  active_case_count: number;
  qualification_valid_until: string | null;
  current_revision_no: number;
  version: number;
  updated_at: string;
}

export interface TherapistReviewQualification {
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

export interface TherapistMutationResult {
  therapist_id: string;
  status: string;
  version: number;
  revision_id?: string | null;
  revision_no?: number | null;
  review_item_id?: string | null;
}

export interface TherapistReviewDecisionResult {
  review_item: Pick<TherapistReviewItem, "review_item_id" | "status" | "version">;
  profile: Pick<TherapistMutationResult, "therapist_id" | "status" | "version">;
  decision_id: string | null;
}
