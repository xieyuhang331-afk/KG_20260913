import { apiRequest } from "@/shared/api/client";
import type { CurrentUser } from "@/shared/auth/authStore";
import type { LoginRequest, LoginResponse } from "./types";

export function login(payload: LoginRequest) {
  const normalizedPayload = payload.totp_code ? payload : { phone: payload.phone, password: payload.password };
  return apiRequest<LoginResponse>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify(normalizedPayload),
    skipAuth: true,
  });
}

export function fetchMe() {
  return apiRequest<CurrentUser>("/api/v1/auth/me");
}
