import type {
  ApproveMatchRequest,
  ApproveMatchResponse,
  Health,
  Keyword,
  MatchDetail,
  MatchListResponse,
  MatchedPostStatus,
  ReplyAction,
  Source,
  SnsAccount,
  Template,
  User,
} from "../types/api";

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

const MUTATING_METHODS = new Set(["POST", "PATCH", "DELETE", "PUT"]);

// CSRF 토큰은 메모리에 캐시하고, 만료(403) 시 1회 재발급 후 재시도한다(docs/API-SPEC.md 공통).
let csrfToken: string | null = null;
let csrfPromise: Promise<string> | null = null;

async function fetchCsrfToken(): Promise<string> {
  const res = await fetch("/api/auth/csrf", { credentials: "include" });
  if (!res.ok) {
    throw new ApiError(res.status, "CSRF 토큰 발급 실패");
  }
  const body = (await res.json()) as { csrf_token: string };
  csrfToken = body.csrf_token;
  return csrfToken;
}

function ensureCsrfToken(): Promise<string> {
  if (csrfToken) return Promise.resolve(csrfToken);
  if (!csrfPromise) {
    csrfPromise = fetchCsrfToken().finally(() => {
      csrfPromise = null;
    });
  }
  return csrfPromise;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  query?: Record<string, string | number | undefined>;
}

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

async function request<T>(
  path: string,
  options: RequestOptions = {},
  retryOn403 = true
): Promise<T> {
  const method = options.method ?? "GET";
  const isMutating = MUTATING_METHODS.has(method);
  const headers: Record<string, string> = {};
  let body: string | undefined;

  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  if (isMutating) {
    headers["X-CSRF-Token"] = await ensureCsrfToken();
  }

  const res = await fetch(buildUrl(path, options.query), {
    method,
    headers,
    body,
    credentials: "include",
  });

  if (res.status === 403 && isMutating && retryOn403) {
    csrfToken = null;
    return request<T>(path, options, false);
  }

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const errBody = (await res.json()) as { detail?: string };
      if (errBody.detail) detail = errBody.detail;
    } catch {
      // 응답 본문이 JSON이 아니면 statusText 유지
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// --- 인증 ---
export const getHealth = () => request<Health>("/health");
export const getMe = () => request<User>("/api/auth/me");
export const login = (email: string, password: string) =>
  request<User>("/api/auth/login", { method: "POST", body: { email, password } });
export const logout = () => request<void>("/api/auth/logout", { method: "POST" });

// --- 매칭 (reviewer) ---
export const getMatches = (query?: {
  status?: MatchedPostStatus;
  source_id?: number;
  page?: number;
  size?: number;
}) => request<MatchListResponse>("/api/matches", { query });
export const getMatch = (id: number) => request<MatchDetail>(`/api/matches/${id}`);
export const approveMatch = (id: number, body: ApproveMatchRequest) =>
  request<ApproveMatchResponse>(`/api/matches/${id}/approve`, { method: "POST", body });
export const ignoreMatch = (id: number) =>
  request<void>(`/api/matches/${id}/ignore`, { method: "POST" });
export const retryMatch = (id: number) =>
  request<ApproveMatchResponse>(`/api/matches/${id}/retry`, { method: "POST" });

// --- 관리 (admin) ---
export const getSources = () => request<Source[]>("/api/sources");
export const getKeywords = () => request<Keyword[]>("/api/keywords");
export const getTemplates = () => request<Template[]>("/api/templates");
export const getSnsAccounts = () => request<SnsAccount[]>("/api/sns-accounts");

// --- 감사 로그 ---
export const getReplyActions = (matchId: number) =>
  request<ReplyAction[]>("/api/reply-actions", { query: { match_id: matchId } });
