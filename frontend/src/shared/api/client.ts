import { clearAccessToken, getAccessToken } from "@/shared/auth/tokenStorage";
import { env } from "@/shared/config/env";
import { ApiError } from "./errors";
import { unwrapApiResponse } from "./response";

interface RequestOptions extends RequestInit {
  skipAuth?: boolean;
}

export async function apiRequest<T>(
  path: string,
  options: RequestOptions = {}
): Promise<T> {
  const headers = new Headers(options.headers);
  const hasJsonBody = options.body && !(options.body instanceof FormData);

  if (!headers.has("Content-Type") && hasJsonBody) {
    headers.set("Content-Type", "application/json");
  }

  const token = getAccessToken();
  if (!options.skipAuth && token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${env.apiBaseUrl}${path}`, {
    ...options,
    headers
  });

  const payload = await readPayload(response);

  if (!response.ok) {
    if (response.status === 401) {
      clearAccessToken();
    }

    throw new ApiError(response.status, getErrorMessage(payload), payload);
  }

  return unwrapApiResponse<T>(payload as T);
}

async function readPayload(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) {
    return null;
  }

  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function getErrorMessage(payload: unknown): string {
  if (payload && typeof payload === "object" && "message" in payload) {
    return String((payload as { message: unknown }).message);
  }

  if (payload && typeof payload === "object" && "detail" in payload) {
    return String((payload as { detail: unknown }).detail);
  }

  return "请求失败";
}
