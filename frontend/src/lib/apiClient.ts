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
  PollNowResponse,
  RenderTemplateRequest,
  RenderTemplateResponse,
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
  // 429 의 `Retry-After`(초). 수동 검색(poll-now)이 backoff·쿨다운·업스트림 429 를 이 헤더로
  // 알려준다 — 호출부가 그 시간만큼 버튼을 잠근다(docs/API-SPEC.md §소스).
  retryAfterSec?: number;

  constructor(status: number, detail: string, action?: string, retryAfterSec?: number) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.action = action;
    this.retryAfterSec = retryAfterSec;
  }
}

/** `Retry-After`(초 단위 정수)만 해석한다. HTTP-date 형식은 이 API 가 쓰지 않는다. */
function parseRetryAfter(value: string | null): number | undefined {
  if (!value) return undefined;
  const secs = Number(value.trim());
  return Number.isFinite(secs) && secs > 0 ? Math.ceil(secs) : undefined;
}

const MUTATING_METHODS = new Set(["POST", "PATCH", "DELETE", "PUT"]);

// FastAPI 검증 오류(422)의 detail 은 문자열이 아니라 [{ loc, msg, type }] 배열이다.
// 이걸 그대로 ApiError.detail 에 넣으면 UI 의 <p>{detail}</p> 렌더에서 React 가
// 크래시한다(객체/배열은 자식으로 렌더 불가 → 페이지 백지). 항상 문자열로 정규화한다.
function normalizeErrorDetail(detail: unknown): string | undefined {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((item) =>
        item && typeof item === "object" && "msg" in item
          ? String((item as { msg: unknown }).msg)
          : null
      )
      .filter((msg): msg is string => Boolean(msg));
    if (msgs.length) return msgs.join(", ");
  }
  return undefined;
}

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
  // 서버가 require_csrf 를 걸지 않는 POST 에 쓴다. 두 곳뿐이다:
  // - `/api/auth/login`: 로그인 전엔 세션 쿠키가 없어 `/api/auth/csrf` 가 401을 낸다(app/api/auth.py).
  // - `/api/matches/render-template`: 상태를 바꾸지 않는 조회성 POST(app/api/matches.py).
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
      if (err instanceof MockApiError) {
        throw new ApiError(err.status, err.detail, err.action, err.retryAfterSec);
      }
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

  // 403 은 CSRF 불일치만 뜻하지 않는다 — 서버는 권한 부족에도 403 을 낸다
  // (backend/app/api/deps.py `require_admin`: "admin 권한이 필요합니다"). detail 문자열로
  // 갈라내는 건 깨지기 쉬우므로, **토큰을 다시 받아보고 실제로 달라졌을 때만** 재시도한다.
  //
  // CSRF 토큰은 Fernet 세션 페이로드 안에 있어(backend/app/auth.py) `/api/auth/csrf` 는
  // 매번 같은 값을 준다. 값이 달라지는 경우는 세션 쿠키가 교체됐을 때(다른 탭에서 재로그인)
  // 뿐이고, 재시도가 구제할 수 있는 상황도 그 하나뿐이다. 토큰이 그대로면 재시도해도 같은
  // 403 이므로 뮤테이션을 한 번 더 보내지 않고 아래 에러 경로로 떨어뜨린다.
  if (res.status === 403 && isMutating && retryOn403) {
    const sent = headers["X-CSRF-Token"];
    // 무효화는 "내가 보낸 토큰이 아직 캐시에 있을 때"로 한정한다 — 다른 갈래가 이미
    // 재발급을 끝낸 뒤 뒤늦게 403 이 도착했는데 조건 없이 비우면, 갓 받아온 토큰을
    // 버리고 불필요한 재발급이 갈래 수만큼 더 나간다(apiClient.csrf.test.ts 로 고정).
    if (sent === csrfToken) csrfToken = null;
    if ((await ensureCsrfToken()) !== sent) {
      return request<T>(path, options, false);
    }
  }

  if (!res.ok) {
    let detail = res.statusText;
    let action: string | undefined;
    try {
      const errBody = (await res.json()) as { detail?: unknown; action?: string };
      const normalized = normalizeErrorDetail(errBody.detail);
      if (normalized) detail = normalized;
      action = errBody.action;
    } catch {
      // 응답 본문이 JSON이 아니면 statusText 유지
    }
    throw new ApiError(res.status, detail, action, parseRetryAfter(res.headers?.get("Retry-After")));
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
// 일괄 발송 문구 미리보기 — 전송하지 않는다. 조회성 POST 라 서버가 CSRF 를 요구하지 않는다.
export const renderTemplate = (body: RenderTemplateRequest) =>
  request<RenderTemplateResponse>("/api/matches/render-template", {
    method: "POST",
    body,
    skipCsrf: true,
  });
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
// 수동 검색 — 스케줄 주기와 무관하게 이 소스를 지금 1회 수집하고 반환된 글을 돌려준다.
// `enabled=false` 소스도 호출 가능하다(주기 수집 on/off 와 별개). 오류는 detail 을 그대로
// 보여준다: 429(활성 backoff 중) · 409(같은 소스 검색이 이미 진행 중) · 502(수집 실패).
export const pollSourceNow = (id: number) =>
  request<PollNowResponse>(`/api/sources/${id}/poll-now`, { method: "POST" });

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
// append-only 테이블이라 서버가 전량을 주지 않는다 — `created_at desc` 최신 limit 건(기본 200,
// 상한 500)만 온다(docs/API-SPEC.md §감사 로그). 화면은 잘림을 사용자에게 알려야 한다.
export const getReplyActions = (query?: { match_id?: number; limit?: number }) =>
  request<ReplyAction[]>("/api/reply-actions", { query });

// --- 사용자 관리 (admin 전용 — docs/API-SPEC.md §사용자 관리) ---
// 삭제는 "아무것도 하지 않은 계정" 에만 열린다. 소유 리소스(소스·키워드·템플릿·SNS 계정)나
// 승인 이력이 있으면 409 + 이유가 온다 — 호출부는 그 detail 을 그대로 보여줘야 한다.
export const getUsers = () => request<AdminUser[]>("/api/users");
export const createUser = (body: CreateUserRequest) =>
  request<AdminUser>("/api/users", { method: "POST", body });
export const patchUser = (id: number, body: PatchUserRequest) =>
  request<AdminUser>(`/api/users/${id}`, { method: "PATCH", body });
export const deleteUser = (id: number) =>
  request<void>(`/api/users/${id}`, { method: "DELETE" });
