import { ApiError } from "./apiClient";

export function describeApiError(error: unknown): string {
  if (error instanceof ApiError) return error.detail;
  return "요청 처리 중 오류가 발생했습니다.";
}
