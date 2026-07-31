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
