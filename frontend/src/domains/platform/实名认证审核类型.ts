export interface IdentityReviewQueueItem {
  user_id: number;
  submission_version: number;
  id_card_masked: string;
  submitted_at: string;
}

export interface IdentityReviewQueueResponse {
  items: IdentityReviewQueueItem[];
  page: number;
  page_size: number;
  total: number;
}

export interface IdentityReviewQueueParams {
  page?: number;
  page_size?: number;
}

export interface IdentityReviewQueueWireItem extends IdentityReviewQueueItem {
  [key: string]: unknown;
}

export interface IdentityReviewQueueWireResponse {
  items: IdentityReviewQueueWireItem[];
  page: number;
  page_size: number;
  total: number;
}

export interface IdentityReviewStepUpResponse {
  step_up_token: string;
  token_type: "identity_review_step_up";
  expires_in: 120;
}

export interface IdentityReviewDetailResponse {
  user_id: number;
  submission_version: number;
  status: string;
  real_name: string;
  id_card: string;
  id_card_masked: string;
  consent_version: string;
  submitted_at: string;
}

export interface IdentityReviewDecisionResponse {
  user_id: number;
  submission_version: number;
  status: string;
  replayed: boolean;
}

export interface IdentityReviewApproveResponse extends IdentityReviewDecisionResponse {
  decision_ref: string;
}
