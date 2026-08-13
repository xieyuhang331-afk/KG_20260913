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

export interface TenantApplicationCreate {
  name: string;
  type: string;
  credit_code: string;
  license_no: string | null;
  license_image: null;
  legal_person_name: string;
  province: string;
  city: string;
  district: string;
  address: string;
  contact_name: string;
  contact_phone: string;
  contact_email: string;
  attachments: [];
}

export interface TenantApplicationCreated {
  id: number;
  tenant_code: string;
  name: string;
  status: string;
  attachment_count: number;
}
