import { apiRequest } from "@/shared/api/client";
import type {
  IdentityReviewQueueParams,
  IdentityReviewQueueResponse,
  IdentityReviewQueueWireResponse,
} from "./实名认证审核类型";

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
