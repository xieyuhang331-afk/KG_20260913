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
