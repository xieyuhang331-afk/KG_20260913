export interface ApiEnvelope<T> {
  code: number;
  message: string;
  data: T;
}

export function unwrapApiResponse<T>(payload: ApiEnvelope<T> | T): T {
  if (payload && typeof payload === "object" && "data" in payload) {
    return (payload as ApiEnvelope<T>).data;
  }

  return payload as T;
}
