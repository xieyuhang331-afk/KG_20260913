import { isApiError } from "./errors";

declare const uuidV7Brand: unique symbol;
export type UUIDv7 = string & { readonly [uuidV7Brand]: "UUIDv7" };

export interface CursorPage<T> {
  items: T[];
  next_cursor: string | null;
}

export interface CursorParams {
  cursor?: string;
  limit?: number;
}

export interface SafeApiError {
  status: number;
  code: string;
  message: string;
  refreshRequired: boolean;
  resultUnknown: boolean;
}

const UUID_V7_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function isUuidV7(value: string): value is UUIDv7 {
  return UUID_V7_PATTERN.test(value);
}

export function toUuidV7(value: string): UUIDv7 {
  if (!isUuidV7(value)) throw new Error("UUID_V7_REQUIRED");
  return value;
}

export function createIdempotencyKey() {
  return crypto.randomUUID();
}

export function cursorQuery(params: CursorParams & object) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") query.set(key, String(value));
  }
  const serialized = query.toString();
  return serialized ? `?${serialized}` : "";
}

export function getSafeApiError(error: unknown): SafeApiError {
  const status = isApiError(error) ? error.status : readStatus(error);
  const payload = isApiError(error) ? error.payload : readPayload(error);
  const code = readCode(payload);
  const resultUnknown = status === 503 && code === "COMMIT_OUTCOME_UNKNOWN";
  const message = resultUnknown
    ? "提交结果暂时无法确认，请刷新记录后核对，避免重复操作"
    : status === 400
      ? "请求内容不符合要求，请检查后重试"
      : status === 401
        ? "登录状态已失效，请重新登录"
        : status === 403
          ? "当前账号无权执行此操作"
          : status === 404
            ? "未找到该记录，或该记录不在当前权限范围"
            : status === 409
              ? "数据已更新，请刷新后重试"
              : status === 429
                ? "操作过于频繁，请稍后重试"
                : status === 503
                  ? "服务暂时不可用，请稍后重试"
                  : "操作未完成，请稍后重试";
  return { status, code, message, refreshRequired: status === 409 || resultUnknown, resultUnknown };
}

function readStatus(error: unknown) {
  if (error && typeof error === "object" && "status" in error && typeof error.status === "number") {
    return error.status;
  }
  return 0;
}

function readPayload(error: unknown) {
  if (error && typeof error === "object" && "payload" in error) return error.payload;
  return undefined;
}

function readCode(payload: unknown) {
  if (!payload || typeof payload !== "object") return "UNKNOWN";
  if ("code" in payload && typeof payload.code === "string") return payload.code;
  if ("error_code" in payload && typeof payload.error_code === "string") return payload.error_code;
  return "UNKNOWN";
}
