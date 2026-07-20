// 백엔드 없이 개발 가능하도록 만든 in-memory 모의 API 서버.
// apiClient.ts 의 request() 가 VITE_USE_MOCK=true 일 때 실제 fetch 대신 이 라우터로 위임한다.

import type {
  ApproveMatchRequest,
  ApproveMatchResponse,
  Health,
  Keyword,
  MatchDetail,
  MatchListResponse,
  MatchedPost,
  MatchedPostStatus,
  ReplyAction,
  Source,
  SnsAccount,
  Template,
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
} from "./fixtures";

export class MockApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
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
// retry(재시도)가 마지막 approve 시도 내용을 재사용할 수 있도록 보관.
const lastApproveRequestByMatchId = new Map<number, ApproveMatchRequest>();

function nextId(records: Array<{ id: number }>): number {
  return records.reduce((max, r) => Math.max(max, r.id), 0) + 1;
}

function delay(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 150 + Math.random() * 200));
}

function canWrite(sourceId: number): boolean {
  return sources.find((s) => s.id === sourceId)?.type === "threads";
}

function toPublicUser(record: (typeof MOCK_USERS)[number]): User {
  return { id: record.id, email: record.email, role: record.role };
}

function requireUser(): User {
  const id = getSessionUserId();
  const record = id ? MOCK_USERS.find((u) => u.id === id) : undefined;
  if (!record) throw new MockApiError(401, "인증이 필요합니다.");
  return toPublicUser(record);
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
  const record = MOCK_USERS.find((u) => u.email === email && u.password === password);
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
  return { items: items.slice(start, start + size), total };
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
  externalReplyId: string | null,
  error: string | null
): void {
  replyActions.push({
    id: nextId(replyActions),
    matched_post_id: matchedPostId,
    reviewer_user_id: reviewerId,
    action,
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
  lastApproveRequestByMatchId.set(id, req);

  if (canWrite(match.source_id)) {
    // final_body에 "실패테스트"를 포함해 502/재시도 흐름을 재현해볼 수 있다.
    if (req.final_body.includes("실패테스트")) {
      match.status = "reviewing";
      recordReplyAction(id, user.id, "failed", null, "모의 전송 실패 (final_body에 '실패테스트' 포함)");
      throw new MockApiError(502, "SNS 전송에 실패했습니다.");
    }
    match.status = "replied";
    const externalReplyId = `mock-reply-${id}-${Date.now()}`;
    recordReplyAction(id, user.id, "sent", externalReplyId, null);
    return { action: "sent", external_reply_id: externalReplyId };
  }

  match.status = "replied";
  recordReplyAction(id, user.id, "approved", null, null);
  return { action: "approved", clipboard_body: req.final_body };
}

function handleIgnore(id: number): void {
  requireUser();
  const match = findMatchOr404(id);
  match.status = "ignored";
}

function handleRetry(id: number): ApproveMatchResponse {
  const match = findMatchOr404(id);
  if (match.status !== "reviewing") {
    throw new MockApiError(409, "재시도는 reviewing 상태에서만 가능합니다.");
  }
  const previous = lastApproveRequestByMatchId.get(id);
  if (!previous) throw new MockApiError(404, "이전 승인 시도 기록이 없습니다.");
  return handleApprove(id, previous);
}

// ---- 소스 (admin) ----

function handleGetSources(): Source[] {
  requireUser();
  return sources;
}

function handleCreateSource(body: unknown): Source {
  requireUser();
  const req = (body ?? {}) as Pick<Source, "type" | "config" | "poll_interval_sec">;
  const record: Source = {
    id: nextId(sources),
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
  Object.assign(record, body ?? {});
  return record;
}

function handleDeleteSource(id: number): void {
  requireUser();
  const idx = sources.findIndex((s) => s.id === id);
  if (idx === -1) throw new MockApiError(404, "소스를 찾을 수 없습니다.");
  sources.splice(idx, 1);
}

// ---- 키워드 (admin) ----

function handleGetKeywords(): Keyword[] {
  requireUser();
  return keywords;
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
  keywords.splice(idx, 1);
}

// ---- 템플릿 (admin) ----

function handleGetTemplates(): Template[] {
  requireUser();
  return templates;
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

// ---- SNS 계정 (admin) — 토큰 배제 ----

function handleGetSnsAccounts(): SnsAccount[] {
  requireUser();
  return snsAccounts;
}

function handleCreateSnsAccount(body: unknown): SnsAccount {
  requireUser();
  const req = (body ?? {}) as Pick<SnsAccount, "platform" | "display_name">;
  // credentials 는 실제로는 서버가 즉시 암호화해 별도 테이블에 저장 — 모의 서버는 아예 보관하지 않는다.
  const record: SnsAccount = {
    id: nextId(snsAccounts),
    platform: req.platform,
    display_name: req.display_name,
    status: "active",
    token_expires_at: null,
  };
  snsAccounts.push(record);
  return record;
}

function handleDeleteSnsAccount(id: number): void {
  requireUser();
  const idx = snsAccounts.findIndex((a) => a.id === id);
  if (idx === -1) throw new MockApiError(404, "SNS 계정을 찾을 수 없습니다.");
  snsAccounts.splice(idx, 1);
}

// ---- 감사 로그 ----

function handleGetReplyActions(query: MockRequestOptions["query"]): ReplyAction[] {
  requireUser();
  const matchId = query?.match_id ? Number(query.match_id) : undefined;
  return replyActions.filter((r) => !matchId || r.matched_post_id === matchId);
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
    handler: (p) => handleRetry(Number(p.id)),
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

  { method: "GET", pattern: /^\/api\/reply-actions$/, handler: (_p, q) => handleGetReplyActions(q) },
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
