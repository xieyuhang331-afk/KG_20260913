import type { CurrentUser } from "@/shared/auth/authStore";

export interface LoginRequest {
  phone: string;
  password: string;
  totp_code?: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  user: CurrentUser;
}
