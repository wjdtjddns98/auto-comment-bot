import type {
  AdminUser,
  ApproveMatchRequest,
  ApproveMatchResponse,
  CreateKeywordRequest,
  CreateSnsAccountRequest,
  CreateSourceRequest,
  CreateTemplateRequest,
  CreateUserRequest,
  Health,
  Keyword,
  MatchDetail,
  MatchListResponse,
  MatchedPostStatus,
  PatchKeywordRequest,
  PatchSourceRequest,
  PatchTemplateRequest,
  PatchUserRequest,
  ReplyAction,
  Source,
  SnsAccount,
  Template,
  ThreadsOAuthAuthorizeUrlResponse,
  ThreadsOAuthConnectRequest,
  UpdateSnsAccountCredentialsRequest,
  User,
} from "../types/api";
import { MockApiError, mockRequest } from "./mock/mockServer";

// 백엔드 없이 개발 가능하도록 하는 모의 서버 스위치 (frontend/.env.mock, `npm run dev:mock`).
const MOCK_ENABLED = import.meta.env.VITE_USE_MOCK === "true";

export class ApiError extends Error {
  status: number;
  detail: string;
  // 502 응답의 action("failed"|"unknown") — 재시도 가능 여부 구분에 필요(docs/API-SPEC.md §매칭).
  action?: string;

  constructor(status: number, detail: string, action?: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.action = action;
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
  // /api/auth/login 전용: 로그인 전엔 세션 쿠키가 없어 /api/auth/csrf 가 401을 낸다
  // (백엔드도 login 라우트엔 require_csrf 를 걸지 않음 — app/api/auth.py 참조).
  skipCsrf?: boolean;
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
  const isMutating = MUTATING_METHODS.has(method) && !options.skipCsrf;

  if (MOCK_ENABLED) {
    try {
      return await mockRequest<T>(path, options);
    } catch (err) {
      if (err instanceof MockApiError) throw new ApiError(err.status, err.detail, err.action);
      throw err;
    }
  }

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
    let action: string | undefined;
    try {
      const errBody = (await res.json()) as { detail?: string; action?: string };
      if (errBody.detail) detail = errBody.detail;
      action = errBody.action;
    } catch {
      // 응답 본문이 JSON이 아니면 statusText 유지
    }
    throw new ApiError(res.status, detail, action);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// --- 인증 ---
export const getHealth = () => request<Health>("/health");
export const getMe = () => request<User>("/api/auth/me");
export const login = (email: string, password: string) =>
  request<User>("/api/auth/login", { method: "POST", body: { email, password }, skipCsrf: true });
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
// retry 는 body 없이 이전 시도를 재사용하지 않는다 — approve 와 동일한 요청 바디(final_body 필수)가 필요하다.
export const retryMatch = (id: number, body: ApproveMatchRequest) =>
  request<ApproveMatchResponse>(`/api/matches/${id}/retry`, { method: "POST", body });

// --- 관리 (admin) ---
export const getSources = () => request<Source[]>("/api/sources");
export const createSource = (body: CreateSourceRequest) =>
  request<Source>("/api/sources", { method: "POST", body });
export const patchSource = (id: number, body: PatchSourceRequest) =>
  request<Source>(`/api/sources/${id}`, { method: "PATCH", body });
export const deleteSource = (id: number) =>
  request<void>(`/api/sources/${id}`, { method: "DELETE" });

export const getKeywords = () => request<Keyword[]>("/api/keywords");
export const createKeyword = (body: CreateKeywordRequest) =>
  request<Keyword>("/api/keywords", { method: "POST", body });
export const patchKeyword = (id: number, body: PatchKeywordRequest) =>
  request<Keyword>(`/api/keywords/${id}`, { method: "PATCH", body });
export const deleteKeyword = (id: number) =>
  request<void>(`/api/keywords/${id}`, { method: "DELETE" });

export const getTemplates = () => request<Template[]>("/api/templates");
export const createTemplate = (body: CreateTemplateRequest) =>
  request<Template>("/api/templates", { method: "POST", body });
export const patchTemplate = (id: number, body: PatchTemplateRequest) =>
  request<Template>(`/api/templates/${id}`, { method: "PATCH", body });
export const deleteTemplate = (id: number) =>
  request<void>(`/api/templates/${id}`, { method: "DELETE" });

export const getSnsAccounts = () => request<SnsAccount[]>("/api/sns-accounts");
export const createSnsAccount = (body: CreateSnsAccountRequest) =>
  request<SnsAccount>("/api/sns-accounts", { method: "POST", body });
export const deleteSnsAccount = (id: number) =>
  request<void>(`/api/sns-accounts/${id}`, { method: "DELETE" });
export const updateSnsAccountCredentials = (id: number, body: UpdateSnsAccountCredentialsRequest) =>
  request<void>(`/api/sns-accounts/${id}/credentials`, { method: "PUT", body });
export const getThreadsOAuthAuthorizeUrl = () =>
  request<ThreadsOAuthAuthorizeUrlResponse>("/api/sns-accounts/threads-oauth/authorize-url");
export const connectThreadsOAuth = (body: ThreadsOAuthConnectRequest) =>
  request<SnsAccount>("/api/sns-accounts/threads-oauth", { method: "POST", body });

// --- 감사 로그 ---
export const getReplyActions = (query?: { match_id?: number }) =>
  request<ReplyAction[]>("/api/reply-actions", { query });

// --- 사용자 관리 (admin, 제안 계약 — docs/API-SPEC.md 미확정, types/api.ts 주석 참조) ---
export const getUsers = () => request<AdminUser[]>("/api/users");
export const createUser = (body: CreateUserRequest) =>
  request<AdminUser>("/api/users", { method: "POST", body });
export const patchUser = (id: number, body: PatchUserRequest) =>
  request<AdminUser>(`/api/users/${id}`, { method: "PATCH", body });
export const deleteUser = (id: number) =>
  request<void>(`/api/users/${id}`, { method: "DELETE" });
