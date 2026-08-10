import { apiRequest } from "@/shared/api/client";
import type {
  IdentityReviewApproveResponse,
  IdentityReviewDecisionResponse,
  IdentityReviewDetailResponse,
  IdentityReviewQueueParams,
  IdentityReviewQueueResponse,
  IdentityReviewQueueWireResponse,
  IdentityReviewStepUpResponse,
} from "./实名认证审核类型";

export const IDENTITY_REVIEW_PURPOSE = "MANUAL_REVIEW" as const;
export const IDENTITY_REVIEW_APPROVE_BASIS = "APPROVED_OFFLINE_IDENTITY_CHECK" as const;
export const IDENTITY_REVIEW_REJECT_REASON = "OFFLINE_CHECK_FAILED" as const;

export async function listIdentityReviews(
  params: IdentityReviewQueueParams = {},
): Promise<IdentityReviewQueueResponse> {
  const query = new URLSearchParams({
    status: "submitted",
    page: String(params.page ?? 1),
    page_size: String(params.page_size ?? 20),
  });
  const response = await apiRequest<IdentityReviewQueueWireResponse>(`/api/v1/reviews/identity?${query.toString()}`);

  return {
    page: response.page,
    page_size: response.page_size,
    total: response.total,
    items: response.items.map((item) => ({
      user_id: item.user_id,
      submission_version: item.submission_version,
      id_card_masked: item.id_card_masked,
      submitted_at: item.submitted_at,
    })),
  };
}

export function issueIdentityReviewStepUp(userId: number, password: string, signal?: AbortSignal) {
  return apiRequest<IdentityReviewStepUpResponse>(`/api/v1/reviews/users/${userId}/identity/step-up`, {
    method: "POST",
    body: JSON.stringify({ password }),
    signal,
  });
}

export function getIdentityReviewDetail(userId: number, stepUpToken: string, signal?: AbortSignal) {
  const query = new URLSearchParams({ purpose_code: IDENTITY_REVIEW_PURPOSE });
  return apiRequest<IdentityReviewDetailResponse>(`/api/v1/reviews/users/${userId}/identity?${query.toString()}`, {
    headers: { "X-Identity-Review-Step-Up": stepUpToken },
    signal,
  });
}

export function approveIdentityReview(userId: number, submissionVersion: number, idempotencyKey: string) {
  return apiRequest<IdentityReviewApproveResponse>(`/api/v1/reviews/users/${userId}/identity/approve`, {
    method: "POST",
    body: JSON.stringify({
      idempotency_key: idempotencyKey,
      submission_version: submissionVersion,
      decision_basis_code: IDENTITY_REVIEW_APPROVE_BASIS,
    }),
  });
}

export function rejectIdentityReview(userId: number, submissionVersion: number, idempotencyKey: string) {
  return apiRequest<IdentityReviewDecisionResponse>(`/api/v1/reviews/users/${userId}/identity/reject`, {
    method: "POST",
    body: JSON.stringify({
      idempotency_key: idempotencyKey,
      submission_version: submissionVersion,
      reason_code: IDENTITY_REVIEW_REJECT_REASON,
    }),
  });
}
