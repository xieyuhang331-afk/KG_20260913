import { apiRequest } from "@/shared/api/client";
import { cursorQuery, type UUIDv7 } from "@/shared/api/slice3";
import type {
  ConsentDocument,
  ConsentDocumentType,
  ConsentRenditionInput,
  MemberIdentityDecisionPayload,
  MemberIdentityPii,
  MemberIdentityReviewDetail,
  MemberIdentityReviewPage,
  MemberIdentityReviewParams,
  MemberIdentityStatus,
} from "./实名认证审核类型";

export const listMemberIdentityReviews = (params: MemberIdentityReviewParams = {}) =>
  apiRequest<MemberIdentityReviewPage>(`/api/v1/platform/member-identity-reviews${cursorQuery(params)}`);

export const getMemberIdentityReview = (reviewId: UUIDv7, signal?: AbortSignal) =>
  apiRequest<MemberIdentityReviewDetail>(`/api/v1/platform/member-identity-reviews/${reviewId}`, {
    signal,
  });

export const claimMemberIdentityReview = (reviewId: UUIDv7, expectedVersion: number, key: string) =>
  apiRequest<MemberIdentityReviewDetail>(`/api/v1/platform/member-identity-reviews/${reviewId}/claim`, {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify({ expected_version: expectedVersion }),
  });

export const accessMemberIdentityPii = (
  reviewId: UUIDv7,
  currentPassword: string,
  reasonCode: "PLATFORM_IDENTITY_REVIEW" | "DUPLICATE_IDENTITY_INVESTIGATION",
  key: string,
  signal?: AbortSignal,
) =>
  apiRequest<MemberIdentityPii>(`/api/v1/platform/member-identity-reviews/${reviewId}/pii-access`, {
    method: "POST",
    headers: { "Idempotency-Key": key, "Cache-Control": "no-store" },
    body: JSON.stringify({ current_password: currentPassword, reason_code: reasonCode }),
    cache: "no-store",
    signal,
  });

export const decideMemberIdentityReview = (reviewId: UUIDv7, payload: MemberIdentityDecisionPayload, key: string) =>
  apiRequest<MemberIdentityStatus>(`/api/v1/platform/member-identity-reviews/${reviewId}/decision`, {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify(payload),
  });

export const createConsentDocument = (
  payload: {
    document_type: ConsentDocumentType;
    semantic_version: string;
    requires_reconsent: true;
    renditions: ConsentRenditionInput[];
  },
  key: string,
) =>
  apiRequest<ConsentDocument>("/api/v1/platform/consent-documents", {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify(payload),
  });

export const publishConsentDocument = (documentId: UUIDv7, expectedVersion: number, effectiveAt: string, key: string) =>
  apiRequest<ConsentDocument>(`/api/v1/platform/consent-documents/${documentId}/publish`, {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify({ expected_version: expectedVersion, effective_at: effectiveAt }),
  });

export const retireConsentDocument = (
  documentId: UUIDv7,
  expectedVersion: number,
  reasonCode: "SUPERSEDED_BY_NEW_VERSION" | "LEGAL_WITHDRAWAL",
  key: string,
) =>
  apiRequest<ConsentDocument>(`/api/v1/platform/consent-documents/${documentId}/retire`, {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify({ expected_version: expectedVersion, reason_code: reasonCode }),
  });
