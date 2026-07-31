import { apiRequest } from "@/shared/api/client";
import type { CurrentUser } from "@/shared/auth/authStore";
import type { LoginRequest, LoginResponse } from "./types";

export function login(payload: LoginRequest) {
  return apiRequest<LoginResponse>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify(payload),
    skipAuth: true
  });
}

export function fetchMe() {
  return apiRequest<CurrentUser>("/api/v1/auth/me");
}
