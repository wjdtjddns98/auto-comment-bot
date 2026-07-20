import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "./apiClient";

function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
    return false; // 4xx는 재시도해도 결과가 같다(401/403/404/409 등).
  }
  return failureCount < 2;
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: shouldRetry,
      staleTime: 10_000,
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: false,
    },
  },
});
