// 백엔드 없이 개발 가능하도록 만든 in-memory 모의 API 서버.
// apiClient.ts 의 request() 가 VITE_USE_MOCK=true 일 때 실제 fetch 대신 이 라우터로 위임한다.

import type {
  AdminUser,
  ApproveMatchRequest,
  ApproveMatchResponse,
  CreateUserRequest,
  Health,
  Keyword,
  MatchDetail,
  MatchListResponse,
  MatchedPost,
  MatchedPostStatus,
  PatchUserRequest,
  ReplyAction,
  Source,
  SnsAccount,
  Template,
  ThreadsOAuthAuthorizeUrlResponse,
  User,
} from "../../types/api";
import {
  MOCK_KEYWORDS,
  MOCK_MATCHED_POSTS,
  MOCK_REPLY_ACTIONS,
  MOCK_SNS_ACCOUNTS,
  MOCK_SOURCES,
  MOCK_TEMPLATES,
  MOCK_USERS,
  type MockUserRecord,
} from "./fixtures";
import { THREADS_OAUTH_CALLBACK_PATH } from "../threadsOAuth";

export class MockApiError extends Error {
  status: number;
  detail: string;
  action?: string;

  constructor(status: number, detail: string, action?: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
    this.action = action;
  }
}

interface MockRequestOptions {
  method?: string;
  body?: unknown;
  query?: Record<string, string | number | undefined>;
}

const SESSION_KEY = "mock_session_user_id";

function getSessionUserId(): number | null {
  const raw = sessionStorage.getItem(SESSION_KEY);
  return raw ? Number(raw) : null;
}

function setSessionUserId(id: number | null): void {
  if (id === null) sessionStorage.removeItem(SESSION_KEY);
  else sessionStorage.setItem(SESSION_KEY, String(id));
}

// 새로고침 사이에는 시드를 유지하되(세션 스토리지), 모듈이 다시 로드되면(=새 탭/재기동) 초기화된다.
const sources = structuredClone(MOCK_SOURCES);
const keywords = structuredClone(MOCK_KEYWORDS);
const templates = structuredClone(MOCK_TEMPLATES);
const snsAccounts = structuredClone(MOCK_SNS_ACCOUNTS);
const matchedPosts = structuredClone(MOCK_MATCHED_POSTS);
const replyActions = structuredClone(MOCK_REPLY_ACTIONS);
const users = structuredClone(MOCK_USERS);

function nextId(records: Array<{ id: number }>): number {
  return records.reduce((max, r) => Math.max(max, r.id), 0) + 1;
}

function delay(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 150 + Math.random() * 200));
}

function canWrite(sourceId: number): boolean {
  return sources.find((s) => s.id === sourceId)?.type === "threads";
}

function toPublicUser(record: MockUserRecord): User {
  return { id: record.id, email: record.email, role: record.role };
}

function toAdminUser(record: MockUserRecord): AdminUser {
  return { ...toPublicUser(record), created_at: record.created_at };
}

function requireUser(): User {
  const id = getSessionUserId();
  const record = id ? users.find((u) => u.id === id) : undefined;
  if (!record) throw new MockApiError(401, "인증이 필요합니다.");
  return toPublicUser(record);
}

function requireAdmin(): User {
  const user = requireUser();
  if (user.role !== "admin") throw new MockApiError(403, "관리자만 접근할 수 있습니다.");
  return user;
}

function findMatchOr404(id: number): MatchedPost {
  const match = matchedPosts.find((m) => m.id === id);
  if (!match) throw new MockApiError(404, "매칭을 찾을 수 없습니다.");
  return match;
}

function validateKeywordPattern(pattern: string, matchType: string): void {
  if (matchType !== "regex") return;
  try {
    new RegExp(pattern);
  } catch {
    throw new MockApiError(422, "정규식 컴파일에 실패했습니다.");
  }
}

// ---- 인증 ----

function handleLogin(body: unknown): User {
  const { email, password } = (body ?? {}) as { email?: string; password?: string };
  const record = users.find((u) => u.email === email && u.password === password);
  if (!record) throw new MockApiError(401, "이메일 또는 비밀번호가 올바르지 않습니다.");
  setSessionUserId(record.id);
  return toPublicUser(record);
}

function handleLogout(): void {
  setSessionUserId(null);
}

// ---- 매칭 ----

function handleGetMatches(query: MockRequestOptions["query"]): MatchListResponse {
  requireUser();
  const status = query?.status as MatchedPostStatus | undefined;
  const sourceId = query?.source_id ? Number(query.source_id) : undefined;
  const page = query?.page ? Number(query.page) : 1;
  const size = query?.size ? Number(query.size) : 20;

  let items = [...matchedPosts];
  if (status) items = items.filter((m) => m.status === status);
  if (sourceId) items = items.filter((m) => m.source_id === sourceId);
  items.sort((a, b) => (a.matched_at < b.matched_at ? 1 : -1));

  const total = items.length;
  const start = (page - 1) * size;
  const paged = items.slice(start, start + size);
  // 목록은 요약만(최대 500자) — 전체 본문은 상세 API 에서. backend/app/api/matches.py 와 동일한 규칙.
  const summarized = paged.map((m) =>
    m.content.length > 500 ? { ...m, content: m.content.slice(0, 500) } : m
  );
  return { items: summarized, total };
}

function handleGetMatchDetail(id: number): MatchDetail {
  requireUser();
  const match = findMatchOr404(id);
  return { ...match, reply_actions: replyActions.filter((r) => r.matched_post_id === id) };
}

function recordReplyAction(
  matchedPostId: number,
  reviewerId: number,
  action: ReplyAction["action"],
  templateId: number | null,
  externalReplyId: string | null,
  error: string | null
): void {
  replyActions.push({
    id: nextId(replyActions),
    matched_post_id: matchedPostId,
    reviewer_user_id: reviewerId,
    action,
    template_id: templateId,
    external_reply_id: externalReplyId,
    error,
    created_at: new Date().toISOString(),
  });
}

function handleApprove(id: number, body: unknown): ApproveMatchResponse {
  const user = requireUser();
  const match = findMatchOr404(id);
  if (match.status !== "new" && match.status !== "reviewing") {
    throw new MockApiError(409, "이미 처리 중이거나 완료된 매칭입니다.");
  }
  const req = (body ?? {}) as ApproveMatchRequest;
  if (!req.final_body) throw new MockApiError(422, "final_body는 필수입니다.");

  match.status = "sending";
  const templateId = req.template_id ?? null;

  if (canWrite(match.source_id)) {
    if (!req.sns_account_id) throw new MockApiError(422, "sns_account_id는 필수입니다.");
    // final_body에 "실패테스트"를 포함해 502/재시도 흐름을 재현해볼 수 있다.
    if (req.final_body.includes("실패테스트")) {
      match.status = "reviewing";
      recordReplyAction(
        id, user.id, "failed", templateId, null,
        "모의 전송 실패 (final_body에 '실패테스트' 포함)"
      );
      throw new MockApiError(502, "SNS 전송에 실패했습니다.", "failed");
    }
    // final_body에 "불명테스트"를 포함해 전송 결과 불명(verify_pending) 조정 흐름을 재현해볼 수 있다.
    if (req.final_body.includes("불명테스트")) {
      match.status = "verify_pending";
      recordReplyAction(
        id, user.id, "unknown", templateId, null,
        "모의 전송 결과 불명 (final_body에 '불명테스트' 포함)"
      );
      throw new MockApiError(502, "전송 결과 확인 중 — 자동 조정 후 재시도 가능해집니다", "unknown");
    }
    match.status = "replied";
    const externalReplyId = `mock-reply-${id}-${Date.now()}`;
    recordReplyAction(id, user.id, "sent", templateId, externalReplyId, null);
    return { action: "sent", external_reply_id: externalReplyId };
  }

  match.status = "replied";
  recordReplyAction(id, user.id, "approved", templateId, null, null);
  return { action: "approved", clipboard_body: req.final_body };
}

function handleIgnore(id: number): void {
  const user = requireUser();
  const match = findMatchOr404(id);
  // verify_pending 도 허용 — 조정 장기 실패 시 사람의 탈출구(docs/M2-SEND-RECONCILIATION.md §3.1).
  if (match.status !== "new" && match.status !== "reviewing" && match.status !== "verify_pending") {
    throw new MockApiError(409, "이미 처리 중이거나 완료된 매칭입니다.");
  }
  match.status = "ignored";
  recordReplyAction(id, user.id, "canceled", null, null, null);
}

// 실서버는 이전 시도 본문을 재사용하지 않는다 — retry 요청 바디는 approve 와 동일하게 필수.
function handleRetry(id: number, body: unknown): ApproveMatchResponse {
  const match = findMatchOr404(id);
  if (match.status !== "reviewing") {
    throw new MockApiError(409, "재시도는 reviewing 상태에서만 가능합니다.");
  }
  return handleApprove(id, body);
}

// ---- 소스 (admin) ----

function handleGetSources(): Source[] {
  requireUser();
  // 배열을 그대로 반환하면 create/patch/delete 가 같은 참조를 in-place 로 변형해
  // React Query 의 구조적 공유(structural sharing)가 "변경 없음"으로 오판해 리렌더가 누락된다.
  return [...sources];
}

// 공백뿐인 값은 null 로 정규화(API-SPEC.md §소스).
function normalizeSourceName(name: unknown): string | null {
  if (typeof name !== "string") return null;
  const trimmed = name.trim();
  return trimmed || null;
}

function handleCreateSource(body: unknown): Source {
  requireUser();
  const req = (body ?? {}) as Pick<Source, "type" | "config" | "poll_interval_sec"> & {
    name?: string | null;
  };
  const record: Source = {
    id: nextId(sources),
    name: normalizeSourceName(req.name),
    type: req.type,
    config: req.config ?? {},
    poll_interval_sec: req.poll_interval_sec ?? 300,
    enabled: true,
    last_success_at: null,
    health_status: "ok",
    backoff_until: null,
  };
  sources.push(record);
  return record;
}

function handlePatchSource(id: number, body: unknown): Source {
  requireUser();
  const record = sources.find((s) => s.id === id);
  if (!record) throw new MockApiError(404, "소스를 찾을 수 없습니다.");
  const req = (body ?? {}) as { name?: string | null };
  const patch = { ...(body as object) };
  if ("name" in req) {
    Object.assign(patch, { name: normalizeSourceName(req.name) });
  }
  Object.assign(record, patch);
  return record;
}

function handleDeleteSource(id: number): void {
  requireUser();
  const idx = sources.findIndex((s) => s.id === id);
  if (idx === -1) throw new MockApiError(404, "소스를 찾을 수 없습니다.");
  if (matchedPosts.some((m) => m.source_id === id)) {
    throw new MockApiError(409, "매칭 이력이 있는 소스는 삭제할 수 없습니다. 비활성화를 사용하세요.");
  }
  if (keywords.some((k) => k.source_scope === id)) {
    throw new MockApiError(409, "이 소스를 범위로 지정한 키워드가 있어 삭제할 수 없습니다. 비활성화를 사용하세요.");
  }
  sources.splice(idx, 1);
}

// ---- 키워드 (admin) ----

function handleGetKeywords(): Keyword[] {
  requireUser();
  return [...keywords];
}

function handleCreateKeyword(body: unknown): Keyword {
  requireUser();
  const req = (body ?? {}) as Pick<Keyword, "pattern" | "match_type" | "source_scope">;
  validateKeywordPattern(req.pattern, req.match_type ?? "substring");
  const record: Keyword = {
    id: nextId(keywords),
    pattern: req.pattern,
    match_type: req.match_type ?? "substring",
    enabled: true,
    source_scope: req.source_scope ?? null,
  };
  keywords.push(record);
  return record;
}

function handlePatchKeyword(id: number, body: unknown): Keyword {
  requireUser();
  const record = keywords.find((k) => k.id === id);
  if (!record) throw new MockApiError(404, "키워드를 찾을 수 없습니다.");
  const patch = (body ?? {}) as Partial<Keyword>;
  if (patch.pattern !== undefined || patch.match_type !== undefined) {
    validateKeywordPattern(patch.pattern ?? record.pattern, patch.match_type ?? record.match_type);
  }
  Object.assign(record, patch);
  return record;
}

function handleDeleteKeyword(id: number): void {
  requireUser();
  const idx = keywords.findIndex((k) => k.id === id);
  if (idx === -1) throw new MockApiError(404, "키워드를 찾을 수 없습니다.");
  if (matchedPosts.some((m) => m.matched_keyword_id === id)) {
    throw new MockApiError(409, "매칭 이력이 있는 키워드는 삭제할 수 없습니다. 비활성화를 사용하세요.");
  }
  keywords.splice(idx, 1);
}

// ---- 템플릿 (admin) ----

function handleGetTemplates(): Template[] {
  requireUser();
  return [...templates];
}

function handleCreateTemplate(body: unknown): Template {
  requireUser();
  const req = (body ?? {}) as Pick<Template, "name" | "body">;
  const record: Template = { id: nextId(templates), name: req.name, body: req.body, enabled: true };
  templates.push(record);
  return record;
}

function handlePatchTemplate(id: number, body: unknown): Template {
  requireUser();
  const record = templates.find((t) => t.id === id);
  if (!record) throw new MockApiError(404, "템플릿을 찾을 수 없습니다.");
  Object.assign(record, body ?? {});
  return record;
}

function handleDeleteTemplate(id: number): void {
  requireUser();
  const idx = templates.findIndex((t) => t.id === id);
  if (idx === -1) throw new MockApiError(404, "템플릿을 찾을 수 없습니다.");
  templates.splice(idx, 1);
}

// ---- SNS 계정 (로그인 사용자 — 본인 귀속 셀프서비스) — 토큰 배제 ----

function handleGetSnsAccounts(): SnsAccount[] {
  const user = requireUser();
  if (user.role === "admin") return [...snsAccounts];
  return snsAccounts.filter((a) => a.user_id === user.id);
}

// 플랫폼별 자격증명 형식 검증 — 등록/교체 시점 422(백엔드 _validate_credentials 와 동일 규칙, #34).
// naver/community 는 어댑터 스키마 확정 시(M3) 추가.
function validateSnsCredentials(platform: SnsAccount["platform"], credentials: Record<string, unknown> | undefined): void {
  if (platform !== "threads") return;
  const token = credentials?.access_token;
  if (typeof token !== "string" || !token.trim()) {
    throw new MockApiError(422, "credentials.access_token: threads 계정은 액세스 토큰(문자열)이 필요합니다");
  }
}

function handleCreateSnsAccount(body: unknown): SnsAccount {
  const user = requireUser();
  const req = (body ?? {}) as Pick<SnsAccount, "platform" | "display_name"> & {
    credentials?: Record<string, unknown>;
  };
  validateSnsCredentials(req.platform, req.credentials);
  // credentials 는 실제로는 서버가 즉시 암호화해 별도 테이블에 저장 — 모의 서버는 아예 보관하지 않는다.
  // 생성 주체에게 자동 귀속(타인 명의 등록 불가) — 소유자 지정 입력 자체가 없다.
  const record: SnsAccount = {
    id: nextId(snsAccounts),
    user_id: user.id,
    platform: req.platform,
    display_name: req.display_name,
    status: "active",
    token_expires_at: null,
  };
  snsAccounts.push(record);
  return record;
}

function handleDeleteSnsAccount(id: number): void {
  const user = requireUser();
  const idx = snsAccounts.findIndex((a) => a.id === id);
  // 본인 것만 삭제 가능(admin 은 전체) · 타인 것은 존재 여부 비노출로 404.
  if (idx === -1 || (user.role !== "admin" && snsAccounts[idx].user_id !== user.id)) {
    throw new MockApiError(404, "SNS 계정을 찾을 수 없습니다.");
  }
  snsAccounts.splice(idx, 1);
}

// Threads OAuth 연동 모의: 재연동(같은 code 접두) 시 새 행이 아니라 기존 행을 갱신하는
// 실서버 동작(계정 id 보존)을 재현하기 위해 계정 id → 모의 platform_username 을 별도로 추적한다
// (SnsAccount 응답 스키마엔 platform_username 이 없다 — 백엔드 SnsAccountOut 과 동일).
const threadsOAuthUsernames = new Map<number, string>();

function handleThreadsAuthorizeUrl(): ThreadsOAuthAuthorizeUrlResponse {
  requireUser();
  // 실서버처럼 state 를 URL 에 실어 형태를 맞춘다(값 검증은 아래 connect 에서).
  // scope 는 심사 제출킷(#75, docs/app-review/SUBMISSION.md §2)의 검수 대상 Threads 권한
  // 세트를 반영해 목 데모/스크린캐스트 대표성을 맞춘다. public_profile 은 Meta 베이스 권한이라
  // Threads OAuth scope 토큰이 아니므로 제외(실 authorize-url 도 threads_* 만 scope 에 실림).
  //
  // redirect_uri 는 앱 콜백 라우트로 둔다 — FE 의 자동 연동 경로(같은 탭 이동 → 콜백이 교환)를
  // 백엔드·실 Meta 앱 없이 QA 하기 위함이다. 목에는 동의 화면이 없으므로 승인 단계를 건너뛰고
  // 곧바로 콜백으로 되돌린다(실서버에서는 threads.net 동의 화면을 한 번 거친다).
  const redirectUri = `${window.location.origin}${THREADS_OAUTH_CALLBACK_PATH}`;
  const state = `mock-state-${Date.now()}`;
  const params = new URLSearchParams({
    client_id: "mock",
    redirect_uri: redirectUri,
    scope:
      "threads_basic,threads_keyword_search,threads_content_publish,threads_read_replies,threads_manage_replies",
    response_type: "code",
    state,
    code: `mock-code-${Date.now()}`,
  });
  return { url: `${redirectUri}?${params.toString()}` };
}

// 실서버 계약(state 필수)을 모의해 FE 가 state 를 빠뜨리는 드리프트를 mock QA 에서 잡는다.
// 매직 값: code "invalid" → 400 코드 무효, state "expired" → 400 세션 만료.
function handleThreadsOAuthConnect(body: unknown): SnsAccount {
  const user = requireUser();
  const req = (body ?? {}) as { code?: string; state?: string; display_name?: string };
  const code = (req.code ?? "").trim();
  const state = (req.state ?? "").trim();
  if (!code) throw new MockApiError(422, "code: 인증 코드가 필요합니다");
  if (!state) throw new MockApiError(422, "state: 연동 값(state)이 필요합니다");
  if (state === "expired") {
    throw new MockApiError(400, "연동 세션이 만료되었거나 유효하지 않습니다 — 다시 연동해 주세요");
  }
  if (code === "invalid") {
    throw new MockApiError(400, "인증 코드가 유효하지 않거나 만료되었습니다 — 다시 연동해 주세요");
  }
  const username = `mock_${code.slice(0, 12)}`;
  const tokenExpiresAt = new Date(Date.now() + 60 * 24 * 60 * 60 * 1000).toISOString();
  const existing = snsAccounts.find(
    (a) =>
      a.user_id === user.id &&
      a.platform === "threads" &&
      threadsOAuthUsernames.get(a.id) === username
  );
  if (existing) {
    existing.status = "active";
    existing.token_expires_at = tokenExpiresAt;
    if (req.display_name) existing.display_name = req.display_name;
    return existing;
  }
  const record: SnsAccount = {
    id: nextId(snsAccounts),
    user_id: user.id,
    platform: "threads",
    display_name: req.display_name || username,
    status: "active",
    token_expires_at: tokenExpiresAt,
  };
  snsAccounts.push(record);
  threadsOAuthUsernames.set(record.id, username);
  return record;
}

function handleUpdateSnsAccountCredentials(id: number, body: unknown): void {
  const user = requireUser();
  const record = snsAccounts.find((a) => a.id === id);
  // 본인 것만 교체 가능(admin 은 전체) · 타인 것은 존재 여부 비노출로 404(이슈 #40).
  if (!record || (user.role !== "admin" && record.user_id !== user.id)) {
    throw new MockApiError(404, "SNS 계정을 찾을 수 없습니다.");
  }
  const req = (body ?? {}) as { credentials?: Record<string, unknown> };
  validateSnsCredentials(record.platform, req.credentials);
  // credentials 는 실제로는 서버가 즉시 암호화해 재저장 — 모의 서버는 보관하지 않는다.
  record.status = "active";
  record.token_expires_at = null;
}

// ---- 감사 로그 ----

function handleGetReplyActions(query: MockRequestOptions["query"]): ReplyAction[] {
  requireUser();
  const matchId = query?.match_id ? Number(query.match_id) : undefined;
  return replyActions.filter((r) => !matchId || r.matched_post_id === matchId);
}

// ---- 사용자 관리 (admin, 제안 계약 — types/api.ts 주석 참조) ----

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function adminCount(): number {
  return users.filter((u) => u.role === "admin").length;
}

function handleGetUsers(): AdminUser[] {
  requireAdmin();
  return users.map(toAdminUser);
}

function handleCreateUser(body: unknown): AdminUser {
  requireAdmin();
  const req = (body ?? {}) as Partial<CreateUserRequest>;
  const email = req.email?.trim() ?? "";
  if (!EMAIL_RE.test(email)) throw new MockApiError(422, "email: 올바른 이메일 형식이 아닙니다");
  if (users.some((u) => u.email === email)) throw new MockApiError(422, "email: 이미 사용 중인 이메일입니다");
  if (!req.password || req.password.length < 8) {
    throw new MockApiError(422, "password: 8자 이상이어야 합니다");
  }
  if (req.role !== "admin" && req.role !== "reviewer") {
    throw new MockApiError(422, "role: admin 또는 reviewer 여야 합니다");
  }
  const record: MockUserRecord = {
    id: nextId(users),
    email,
    password: req.password,
    role: req.role,
    created_at: new Date().toISOString(),
  };
  users.push(record);
  return toAdminUser(record);
}

function handlePatchUser(id: number, body: unknown): AdminUser {
  const actor = requireAdmin();
  const record = users.find((u) => u.id === id);
  if (!record) throw new MockApiError(404, "사용자를 찾을 수 없습니다.");
  const req = (body ?? {}) as PatchUserRequest;
  if (req.role !== undefined) {
    if (req.role !== "admin" && req.role !== "reviewer") {
      throw new MockApiError(422, "role: admin 또는 reviewer 여야 합니다");
    }
    if (record.id === actor.id && req.role !== "admin") {
      throw new MockApiError(422, "본인의 관리자 권한은 스스로 해제할 수 없습니다.");
    }
    if (record.role === "admin" && req.role !== "admin" && adminCount() <= 1) {
      throw new MockApiError(422, "마지막 관리자의 역할은 변경할 수 없습니다.");
    }
    record.role = req.role;
  }
  return toAdminUser(record);
}

function handleDeleteUser(id: number): void {
  const actor = requireAdmin();
  const idx = users.findIndex((u) => u.id === id);
  if (idx === -1) throw new MockApiError(404, "사용자를 찾을 수 없습니다.");
  const record = users[idx];
  if (record.id === actor.id) throw new MockApiError(422, "본인 계정은 삭제할 수 없습니다.");
  if (record.role === "admin" && adminCount() <= 1) {
    throw new MockApiError(422, "마지막 관리자는 삭제할 수 없습니다.");
  }
  users.splice(idx, 1);
}

// ---- 헬스 ----

function handleHealth(): Health {
  return {
    status: "ok",
    db: "ok",
    poller: {
      last_tick: new Date().toISOString(),
      sources: sources.map((s) => ({
        source_id: s.id,
        health_status: s.health_status,
        last_success_at: s.last_success_at,
      })),
    },
  };
}

// ---- 라우팅 ----

type Handler = (params: Record<string, string>, query: MockRequestOptions["query"], body: unknown) => unknown;

const ROUTES: Array<{ method: string; pattern: RegExp; handler: Handler }> = [
  { method: "GET", pattern: /^\/health$/, handler: () => handleHealth() },

  { method: "GET", pattern: /^\/api\/auth\/me$/, handler: () => requireUser() },
  { method: "POST", pattern: /^\/api\/auth\/login$/, handler: (_p, _q, body) => handleLogin(body) },
  { method: "POST", pattern: /^\/api\/auth\/logout$/, handler: () => handleLogout() },

  { method: "GET", pattern: /^\/api\/matches$/, handler: (_p, q) => handleGetMatches(q) },
  {
    method: "GET",
    pattern: /^\/api\/matches\/(?<id>\d+)$/,
    handler: (p) => handleGetMatchDetail(Number(p.id)),
  },
  {
    method: "POST",
    pattern: /^\/api\/matches\/(?<id>\d+)\/approve$/,
    handler: (p, _q, body) => handleApprove(Number(p.id), body),
  },
  {
    method: "POST",
    pattern: /^\/api\/matches\/(?<id>\d+)\/ignore$/,
    handler: (p) => handleIgnore(Number(p.id)),
  },
  {
    method: "POST",
    pattern: /^\/api\/matches\/(?<id>\d+)\/retry$/,
    handler: (p, _q, body) => handleRetry(Number(p.id), body),
  },

  { method: "GET", pattern: /^\/api\/sources$/, handler: () => handleGetSources() },
  { method: "POST", pattern: /^\/api\/sources$/, handler: (_p, _q, body) => handleCreateSource(body) },
  {
    method: "PATCH",
    pattern: /^\/api\/sources\/(?<id>\d+)$/,
    handler: (p, _q, body) => handlePatchSource(Number(p.id), body),
  },
  {
    method: "DELETE",
    pattern: /^\/api\/sources\/(?<id>\d+)$/,
    handler: (p) => handleDeleteSource(Number(p.id)),
  },

  { method: "GET", pattern: /^\/api\/keywords$/, handler: () => handleGetKeywords() },
  { method: "POST", pattern: /^\/api\/keywords$/, handler: (_p, _q, body) => handleCreateKeyword(body) },
  {
    method: "PATCH",
    pattern: /^\/api\/keywords\/(?<id>\d+)$/,
    handler: (p, _q, body) => handlePatchKeyword(Number(p.id), body),
  },
  {
    method: "DELETE",
    pattern: /^\/api\/keywords\/(?<id>\d+)$/,
    handler: (p) => handleDeleteKeyword(Number(p.id)),
  },

  { method: "GET", pattern: /^\/api\/templates$/, handler: () => handleGetTemplates() },
  { method: "POST", pattern: /^\/api\/templates$/, handler: (_p, _q, body) => handleCreateTemplate(body) },
  {
    method: "PATCH",
    pattern: /^\/api\/templates\/(?<id>\d+)$/,
    handler: (p, _q, body) => handlePatchTemplate(Number(p.id), body),
  },
  {
    method: "DELETE",
    pattern: /^\/api\/templates\/(?<id>\d+)$/,
    handler: (p) => handleDeleteTemplate(Number(p.id)),
  },

  { method: "GET", pattern: /^\/api\/sns-accounts$/, handler: () => handleGetSnsAccounts() },
  {
    method: "POST",
    pattern: /^\/api\/sns-accounts$/,
    handler: (_p, _q, body) => handleCreateSnsAccount(body),
  },
  {
    method: "DELETE",
    pattern: /^\/api\/sns-accounts\/(?<id>\d+)$/,
    handler: (p) => handleDeleteSnsAccount(Number(p.id)),
  },
  {
    method: "PUT",
    pattern: /^\/api\/sns-accounts\/(?<id>\d+)\/credentials$/,
    handler: (p, _q, body) => handleUpdateSnsAccountCredentials(Number(p.id), body),
  },
  {
    method: "GET",
    pattern: /^\/api\/sns-accounts\/threads-oauth\/authorize-url$/,
    handler: () => handleThreadsAuthorizeUrl(),
  },
  {
    method: "POST",
    pattern: /^\/api\/sns-accounts\/threads-oauth$/,
    handler: (_p, _q, body) => handleThreadsOAuthConnect(body),
  },

  { method: "GET", pattern: /^\/api\/reply-actions$/, handler: (_p, q) => handleGetReplyActions(q) },

  { method: "GET", pattern: /^\/api\/users$/, handler: () => handleGetUsers() },
  { method: "POST", pattern: /^\/api\/users$/, handler: (_p, _q, body) => handleCreateUser(body) },
  {
    method: "PATCH",
    pattern: /^\/api\/users\/(?<id>\d+)$/,
    handler: (p, _q, body) => handlePatchUser(Number(p.id), body),
  },
  {
    method: "DELETE",
    pattern: /^\/api\/users\/(?<id>\d+)$/,
    handler: (p) => handleDeleteUser(Number(p.id)),
  },
];

function matchRoute(
  method: string,
  path: string
): { handler: Handler; params: Record<string, string> } | null {
  for (const route of ROUTES) {
    if (route.method !== method) continue;
    const m = route.pattern.exec(path);
    if (m) return { handler: route.handler, params: m.groups ?? {} };
  }
  return null;
}

export async function mockRequest<T>(path: string, options: MockRequestOptions): Promise<T> {
  await delay();
  const method = options.method ?? "GET";
  const route = matchRoute(method, path);
  if (!route) throw new MockApiError(404, `모의 서버에 정의되지 않은 경로: ${method} ${path}`);
  return route.handler(route.params, options.query, options.body) as T;
}
