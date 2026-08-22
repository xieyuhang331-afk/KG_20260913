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

export type ReadinessReason =
  | "COMPLIANCE_SUSPENDED"
  | "INSTITUTION_APPROVAL_SOURCE_INVALID"
  | "INSTITUTION_LICENSE_INVALID"
  | "METABOLIC_SCOPE_MISSING"
  | "NO_APPROVED_ACTIVE_THERAPIST"
  | "TENANT_NOT_ACTIVE";

export interface ServiceReadiness {
  tenant_id: string;
  readiness_status: "NOT_READY" | "SERVICE_READY";
  reason_codes: ReadinessReason[];
  qualified_therapist_count: number;
  computed_at: string;
  evidence_version: number;
}

export interface ServiceReadinessEvidence extends ServiceReadiness {
  input_digest: string;
  result_digest: string;
}

export interface CursorPage<T> {
  items: T[];
  next_cursor?: string | null;
}

export interface CursorParams {
  cursor?: string;
  limit?: number;
}

export type MemberInvitationMode = "SELF" | "PROXY_ELDER";
export type MemberInvitationStatus = "INVITED" | "ACCEPTED" | "REVOKED" | "EXPIRED";
export type ServiceScopeTag = "HYPERTENSION" | "GLUCOSE_METABOLISM" | "DYSLIPIDEMIA" | "OBESITY";

export interface MemberInvitation {
  invitation_id: UUIDv7;
  tenant_id: UUIDv7;
  mode: MemberInvitationMode;
  phone_masked: string;
  expires_at: string;
  status: MemberInvitationStatus;
  failed_attempts: number;
  issued_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
  version: number;
}

export interface MemberInvitationSecret extends MemberInvitation {
  short_code: string;
}

export interface MemberIdentityStatus {
  verification_id: UUIDv7;
  enrollment_id: UUIDv7;
  member_id: UUIDv7;
  current_revision_id: UUIDv7;
  status: string;
  id_masked: string;
  submitted_at: string;
  institution_checked_at: string | null;
  platform_decided_at: string | null;
  reason_codes: string[];
  version: number;
}

export interface MemberProxyGrant {
  grant_id: UUIDv7;
  principal_member_id: UUIDv7;
  proxy_member_id: UUIDv7;
  permission_codes: string[];
  authorization_document_version_id: UUIDv7 | null;
  status: string;
  valid_from: string | null;
  valid_until: string | null;
  version: number;
}

export interface MemberConsentRecord {
  consent_record_id: UUIDv7;
  enrollment_id: UUIDv7;
  document_type: string;
  document_version_id: UUIDv7;
  rendition_id: UUIDv7;
  locale: string;
  choice: "ACCEPTED" | "DECLINED";
  status: string;
  presented_at: string;
  accepted_at: string | null;
  withdrawn_at: string | null;
  version: number;
}

export interface PrimaryAssignment {
  assignment_id: UUIDv7;
  enrollment_id: UUIDv7;
  tenant_id: UUIDv7;
  subject_member_id: UUIDv7;
  therapist_id: UUIDv7;
  status: string;
  service_scope_tags: ServiceScopeTag[];
  reason_code: string | null;
  service_case_id: UUIDv7 | null;
  created_at: string;
  decided_at: string | null;
  version: number;
}

export interface MemberEnrollmentSummary {
  enrollment_id: UUIDv7;
  mode: MemberInvitationMode;
  status: string;
  accepted_at: string | null;
  identity_verified_at: string | null;
  case_created_at: string | null;
  version: number;
}

export interface MemberEnrollmentDetail {
  enrollment_id: UUIDv7;
  tenant_id: UUIDv7;
  subject_member_id: UUIDv7 | null;
  proxy_member_id: UUIDv7 | null;
  mode: MemberInvitationMode;
  status: string;
  service_scope_tags: ServiceScopeTag[];
  current_identity_verification_id: UUIDv7 | null;
  current_assignment_id: UUIDv7 | null;
  service_case_id: UUIDv7 | null;
  accepted_at: string | null;
  identity_verified_at: string | null;
  case_created_at: string | null;
  version: number;
  identity: MemberIdentityStatus | null;
  proxy: MemberProxyGrant | null;
  consents: MemberConsentRecord[];
  assignment: PrimaryAssignment | null;
}

export interface PreparingServiceCase {
  case_id: UUIDv7;
  enrollment_id: UUIDv7;
  subject_member_id: UUIDv7;
  tenant_id: UUIDv7;
  primary_therapist_id: UUIDv7;
  assignment_id: UUIDv7;
  status: "PREPARING";
  service_scope_tags: ServiceScopeTag[];
  created_at: string;
  version: number;
}
import type { UUIDv7 } from "@/shared/api/slice3";
