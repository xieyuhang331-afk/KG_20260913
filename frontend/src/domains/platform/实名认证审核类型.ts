import type { CursorPage, CursorParams, UUIDv7 } from "@/shared/api/slice3";

export type MemberReviewMode = "SELF" | "PROXY_ELDER";

export interface MemberIdentityReviewSummary {
  review_id: UUIDv7;
  enrollment_id: UUIDv7;
  member_id: UUIDv7;
  mode: MemberReviewMode;
  status: string;
  id_masked: string;
  submitted_at: string;
  version: number;
}

export interface MemberIdentityReviewDetail extends MemberIdentityReviewSummary {
  current_revision_id: UUIDv7;
  current_revision_no: number;
  institution_attestation: string | null;
  correction_fields: Array<"real_name" | "id_number">;
  proxy_witness_status: string | null;
}

export interface MemberIdentityPii {
  review_id: UUIDv7;
  revision_id: UUIDv7;
  real_name: string;
  id_number: string;
  birth_date: string;
  access_id: UUIDv7;
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

export interface MemberIdentityReviewParams extends CursorParams {
  status?: string;
}

export type MemberIdentityReviewPage = CursorPage<MemberIdentityReviewSummary>;

export interface MemberIdentityDecisionPayload {
  revision_id: UUIDv7;
  decision: "APPROVED" | "NEEDS_CORRECTION" | "REJECTED";
  reason_code: string | null;
  correction_fields: Array<"real_name" | "id_number">;
  represented_elder_eligible: boolean | null;
  expected_version: number;
}

export type ConsentDocumentType =
  | "USER_AGREEMENT"
  | "PRIVACY_POLICY"
  | "HEALTH_DATA_PROCESSING"
  | "INSTITUTION_SERVICE"
  | "NON_MEDICAL_RISK"
  | "PROXY_AUTHORIZATION";

export interface ConsentRenditionInput {
  locale: string;
  title: string;
  body: string;
}

export interface ConsentDocument {
  document_version_id: UUIDv7;
  document_type: ConsentDocumentType;
  semantic_version: string;
  status: "DRAFT" | "PUBLISHED" | "RETIRED";
  requires_reconsent: true;
  effective_at: string | null;
  retired_at: string | null;
  renditions: Array<{
    rendition_id: UUIDv7;
    locale: string;
    title: string;
    content_sha256: string;
  }>;
  version: number;
}
