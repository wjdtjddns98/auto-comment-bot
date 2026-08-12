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
  RenderTemplateItem,
  RenderTemplateRequest,
  RenderTemplateResponse,
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

/** 모의 상태 배열은 모듈 상수라 재할당할 수 없다 — 조건에 맞는 항목을 제자리에서 제거한다. */
function removeWhere<T>(records: T[], predicate: (record: T) => boolean): void {
  for (let i = records.length - 1; i >= 0; i -= 1) {
    if (predicate(records[i])) records.splice(i, 1);
  }
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
  // (원본 참조를 그대로 돌려줘도 되는 이유는 mockRequest 가 응답을 깊은 복사하기 때문 — 그쪽 주석 참조.)
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

  // 같은 게시물 중복 답글 차단(R16) — 같은 external_post_id 를 가진 **다른** 매칭에 sent·unknown
  // 이력이 있으면 막는다. dedup 키가 (source_id, external_post_id) 라 같은 글이 다른 소스로
  // 재수집되면 새 매칭이 되고, matched_post_id 단위 방어로는 두 번째 답글이 나간다.
  // 전송 소스에만 적용된다(수동 복사 소스는 실제 게시가 아니므로 실서버도 검사하지 않는다).
  if (canWrite(match.source_id)) {
    const siblingIds = new Set(
      matchedPosts
        .filter((m) => m.id !== id && m.external_post_id === match.external_post_id)
        .map((m) => m.id)
    );
    const alreadyReplied = replyActions.some(
      (r) => siblingIds.has(r.matched_post_id) && (r.action === "sent" || r.action === "unknown")
    );
    if (alreadyReplied) {
      throw new MockApiError(
        409,
        "이 게시물에는 이미 답글을 보냈습니다(다른 소스로 중복 수집된 글)" +
          " — 중복 답글은 스팸으로 판정될 수 있어 차단합니다"
      );
    }
  }

  // CAS 클레임(실서버 matches.py) — 클레임이 풀린 뒤에도 값은 남는다. 화면의 "전송중 전이" 표시 재현용.
  match.status = "sending";
  match.sending_claimed_at = new Date().toISOString();
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

// ---- 템플릿 렌더 미리보기 (일괄 발송용) ----

// backend/app/templating.py 와 같은 규칙으로 치환한다. 문법이 갈리면 mock QA 가 실서버와
// 다른 문구를 보여주게 되므로 토큰 정규식·허용 변수·에러 문구를 그대로 맞춘다.
const RENDER_TOKEN_RE = /\{\{([^{}]*)\}\}/g;
const RENDER_ALLOWED_VARS = ["author", "keyword", "url"] as const;

class TemplateRenderError extends Error {}

function renderTemplateBody(body: string, context: Record<string, string | null>): string {
  const missing: string[] = [];
  const unknown: string[] = [];
  const rendered = body.replace(RENDER_TOKEN_RE, (_all, rawToken: string) => {
    const raw = rawToken.trim();
    if (raw.includes("|")) {
      // 랜덤 변형: 빈 후보도 허용한다(예: "{{안녕하세요|}}" = 있거나 없거나).
      const choices = raw.split("|").map((c) => c.trim());
      return choices[Math.floor(Math.random() * choices.length)];
    }
    if (!RENDER_ALLOWED_VARS.includes(raw as (typeof RENDER_ALLOWED_VARS)[number])) {
      unknown.push(raw);
      return "";
    }
    const value = context[raw];
    if (!value) {
      missing.push(raw);
      return "";
    }
    return value;
  });
  if (unknown.length) {
    const names = [...new Set(unknown)].sort().join(", ");
    throw new TemplateRenderError(
      `알 수 없는 템플릿 변수: ${names} (사용 가능: ${RENDER_ALLOWED_VARS.join(", ")} · 변형은 {{a|b}} 형식)`
    );
  }
  if (missing.length) {
    const names = [...new Set(missing)].sort().join(", ");
    throw new TemplateRenderError(`이 매칭에서 값을 얻을 수 없는 변수: ${names}`);
  }
  // 변형·치환으로 생긴 연속 공백만 정리한다(줄바꿈은 사람이 의도한 것이라 보존).
  return rendered.replace(/[ \t]{2,}/g, " ").trim();
}

function handleRenderTemplate(body: unknown): RenderTemplateResponse {
  requireUser();
  const req = (body ?? {}) as Partial<RenderTemplateRequest>;
  const template = templates.find((t) => t.id === req.template_id && t.enabled);
  if (!template) throw new MockApiError(422, "template_id: 템플릿이 없거나 비활성입니다");
  const matchIds = Array.isArray(req.match_ids) ? req.match_ids : [];
  if (matchIds.length < 1 || matchIds.length > 200) {
    throw new MockApiError(422, "match_ids: 1~200건이어야 합니다");
  }
  // items 는 요청 순서를 유지한다. 실패는 그 건만 body=null + error 로 표시한다.
  const items: RenderTemplateItem[] = matchIds.map((matchId: number) => {
    const post = matchedPosts.find((m) => m.id === matchId);
    if (!post) return { match_id: matchId, body: null, error: "매칭이 없습니다" };
    const keyword =
      post.matched_keyword_id != null
        ? keywords.find((k) => k.id === post.matched_keyword_id)?.pattern ?? null
        : null;
    try {
      const context = { author: post.author, keyword, url: post.url };
      return { match_id: matchId, body: renderTemplateBody(template.body, context), error: null };
    } catch (err) {
      if (err instanceof TemplateRenderError) {
        return { match_id: matchId, body: null, error: err.message };
      }
      throw err;
    }
  });
  return { items };
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
    last_error: null,
    last_error_at: null,
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

// 삭제 규칙은 docs/API-SPEC.md §소스 기준(2026-08-11 제품 결정, PR #91):
// 매칭·발송 이력·스코프 키워드는 소스와 함께 정리되고, 차단은 전송 진행 중일 때만이다.
// 발송 이력 보존 목적의 409 는 제거됐다 — 그 차단을 남겨두면 mock QA 에서 실서버가 주지
// 않는 409 를 보게 되어 FE 가 잘못된 안내를 만들게 된다.
function handleDeleteSource(id: number): void {
  requireUser();
  const idx = sources.findIndex((s) => s.id === id);
  if (idx === -1) throw new MockApiError(404, "소스를 찾을 수 없습니다.");
  // 감사 보존이 아니라 정합성 보호 — sending 은 결과 기록 대상이 사라지면 유령 전송이 되고,
  // verify_pending 은 조정 판정 중이라 지우면 "게시됐는지 모름"이 영구 미해결로 남는다.
  const inFlight = matchedPosts.some(
    (m) => m.source_id === id && (m.status === "sending" || m.status === "verify_pending")
  );
  if (inFlight) {
    throw new MockApiError(
      409,
      "전송 진행 중인 매칭이 있는 소스는 삭제할 수 없습니다" +
        " — 전송/조정이 끝난 뒤 다시 시도해 주세요(enabled=false 로 먼저 중단)"
    );
  }
  // 실서버와 같은 순서로 정리한다: 이력 → 매칭 → 스코프 키워드 → 소스.
  const matchIds = new Set(matchedPosts.filter((m) => m.source_id === id).map((m) => m.id));
  removeWhere(replyActions, (r) => matchIds.has(r.matched_post_id));
  removeWhere(matchedPosts, (m) => m.source_id === id);
  // 전역 키워드(source_scope === null)는 무관 — 이 소스를 범위로 지정한 것만 지운다.
  removeWhere(keywords, (k) => k.source_scope === id);
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
  // threads 는 등록 시 서버가 토큰으로 프로필(/me)을 조회해 안정 식별자를 확보하고, 같은 신원이
  // 이미 있으면 새 행을 만들지 않고 그 계정의 자격증명을 교체해 201 로 돌려준다(PR #86).
  // 계정 id 가 보존돼야 소스 config 의 sns_account_id 참조가 고아가 되지 않는다.
  if (req.platform === "threads") {
    const identity = mockThreadsIdentity(req.credentials?.access_token);
    const existing = findThreadsAccountByIdentity(user.id, identity);
    if (existing) {
      existing.status = "active";
      // 수동 등록 토큰은 만료 시각을 알 수 없다(OAuth 연동과 달리 null 유지).
      existing.token_expires_at = null;
      if (req.display_name) existing.display_name = req.display_name;
      return existing;
    }
  }
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
  if (req.platform === "threads") {
    threadsIdentities.set(record.id, mockThreadsIdentity(req.credentials?.access_token));
  }
  return record;
}

function handleDeleteSnsAccount(id: number): void {
  const user = requireUser();
  const idx = snsAccounts.findIndex((a) => a.id === id);
  // 본인 것만 삭제 가능(admin 은 전체) · 타인 것은 존재 여부 비노출로 404.
  if (idx === -1 || (user.role !== "admin" && snsAccounts[idx].user_id !== user.id)) {
    throw new MockApiError(404, "SNS 계정을 찾을 수 없습니다.");
  }
  // 참조 소스가 있으면 삭제 차단(R15) — 소스 config 는 JSON 이라 FK 백스톱이 없어, 이 검사가
  // 없으면 계정 삭제가 소스를 조용히 고아로 만든다. **비활성 소스도 참조로 센다**(다시 켜면
  // 같은 고아 상태가 되므로). 참조 소스 id 를 문구에 담는 것까지 실서버와 맞춘다.
  const referring = sources
    .filter((s) => (s.config as { sns_account_id?: number } | undefined)?.sns_account_id === id)
    .map((s) => s.id)
    .sort((a, b) => a - b);
  if (referring.length > 0) {
    throw new MockApiError(
      409,
      `이 계정을 사용하는 소스가 있어 삭제할 수 없습니다(소스 ${referring.join(", ")})` +
        " — 소스를 먼저 삭제하거나 다른 계정으로 변경해 주세요"
    );
  }
  snsAccounts.splice(idx, 1);
}

// 재연동·재등록 시 새 행이 아니라 기존 행을 갱신하는 실서버 동작(계정 id 보존)을 재현하기 위해
// 계정 id → 모의 Threads 신원을 별도로 추적한다(SnsAccount 응답 스키마엔 식별자가 없다 —
// 백엔드 SnsAccountOut 과 동일).
//
// 실서버는 액세스 토큰으로 /me 를 조회해 신원을 얻지만 모의 서버엔 업스트림이 없으므로 입력값에서
// 파생한다. 그래서 같은 실계정을 OAuth 로 연동한 뒤 수동 토큰으로 등록하면 실서버에서는 한 행으로
// 수렴하지만 모의 서버에서는 갈라진다 — 알려진 모의 한계이며, 각 경로 안에서의 중복 방지(계정 증식)는
// 그대로 재현된다.
const threadsIdentities = new Map<number, string>();

function mockThreadsIdentity(credential: unknown): string {
  const value = typeof credential === "string" ? credential.trim() : "";
  return `mock-identity-${value.slice(-12) || "unknown"}`;
}

function findThreadsAccountByIdentity(userId: number, identity: string): SnsAccount | undefined {
  return snsAccounts.find(
    (a) => a.user_id === userId && a.platform === "threads" && threadsIdentities.get(a.id) === identity
  );
}

function handleThreadsAuthorizeUrl(): ThreadsOAuthAuthorizeUrlResponse {
  requireUser();
  // 실서버처럼 state 를 URL 에 실어 형태를 맞춘다(값 검증은 아래 connect 에서).
  // scope 는 심사 제출킷(#75, docs/app-review/SUBMISSION.md §2)의 검수 대상 Threads 권한
  // 세트를 반영해 목 데모/스크린캐스트 대표성을 맞춘다. public_profile 은 Meta 베이스 권한이라
  // Threads OAuth scope 토큰이 아니므로 제외(실 authorize-url 도 threads_* 만 scope 에 실림).
  const params = new URLSearchParams({
    client_id: "mock",
    redirect_uri: "https://nutti.co.kr/threads-callback.html",
    scope:
      "threads_basic,threads_keyword_search,threads_content_publish,threads_read_replies,threads_manage_replies",
    response_type: "code",
    state: `mock-state-${Date.now()}`,
  });
  return { url: `https://www.threads.net/oauth/authorize?${params.toString()}` };
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
  const existing = findThreadsAccountByIdentity(user.id, username);
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
  threadsIdentities.set(record.id, username);
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
  // threads 는 교체 토큰으로도 프로필을 조회해 식별자를 채운다(수동 등록 계정의 자기 치유).
  // 그 신원이 이미 다른 항목에 연결돼 있으면 409 로 막고 아무것도 바꾸지 않는다(PR #86).
  if (record.platform === "threads") {
    const identity = mockThreadsIdentity(req.credentials?.access_token);
    const owner = findThreadsAccountByIdentity(record.user_id, identity);
    if (owner && owner.id !== record.id) {
      throw new MockApiError(
        409,
        "같은 Threads 계정이 이미 다른 항목에 연결돼 있습니다 — 그 항목에서 교체해 주세요"
      );
    }
    threadsIdentities.set(record.id, identity);
  }
  // credentials 는 실제로는 서버가 즉시 암호화해 재저장 — 모의 서버는 보관하지 않는다.
  record.status = "active";
  record.token_expires_at = null;
}

// ---- 감사 로그 ----

function handleGetReplyActions(query: MockRequestOptions["query"]): ReplyAction[] {
  requireUser();
  const matchId = query?.match_id ? Number(query.match_id) : undefined;
  // 실서버는 created_at desc 로 최신 limit 건만 준다(기본 200, 1~500 밖은 422) — 화면의 잘림
  // 안내가 mock 에서도 같은 조건으로 뜨도록 정렬·상한을 맞춘다.
  const limit = query?.limit === undefined ? 200 : Number(query.limit);
  if (!Number.isInteger(limit) || limit < 1 || limit > 500) {
    throw new MockApiError(422, "limit 은 1~500 사이여야 합니다.");
  }
  return replyActions
    .filter((r) => !matchId || r.matched_post_id === matchId)
    .slice()
    .sort((a, b) => (a.created_at === b.created_at ? b.id - a.id : a.created_at < b.created_at ? 1 : -1))
    .slice(0, limit);
}

// ---- 사용자 관리 (admin — docs/API-SPEC.md §사용자 관리, 2026-08-12 확정) ----
// 실서버(backend/app/api/users.py)와 상태코드·판정을 맞춰 둔다. 목업이 더 관대하면
// dev:mock 에서만 되는 조작을 실서버에서 실패로 만나게 된다(삭제 가드가 그 사례였다).

function adminCount(): number {
  return users.filter((u) => u.role === "admin").length;
}

function handleGetUsers(): AdminUser[] {
  requireAdmin();
  return [...users].sort((a, b) => a.id - b.id).map(toAdminUser);
}

function handleCreateUser(body: unknown): AdminUser {
  requireAdmin();
  const req = (body ?? {}) as Partial<CreateUserRequest>;
  const email = req.email?.trim() ?? "";
  // 서버는 정규식으로 보지 않는다 — 로그인이 평문 정확 대조라 생성만 엄격하면
  // "만들 수는 있는데 로그인이 안 되는" 계정이 생긴다. `@` 포함·공백 없음·3~255자만 본다.
  if (email.length < 3 || email.length > 255 || !email.includes("@") || email.includes(" ")) {
    throw new MockApiError(422, "email: 이메일 형식이 아닙니다");
  }
  if (users.some((u) => u.email === email)) {
    throw new MockApiError(409, "이미 등록된 이메일입니다");
  }
  if (!req.password || req.password.length < 8 || req.password.length > 200) {
    throw new MockApiError(422, "password: 8자 이상 200자 이하여야 합니다");
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
  requireAdmin();
  const record = users.find((u) => u.id === id);
  if (!record) throw new MockApiError(404, "사용자가 없습니다");
  const req = (body ?? {}) as Partial<PatchUserRequest>;
  if (req.role !== "admin" && req.role !== "reviewer") {
    throw new MockApiError(422, "role: admin 또는 reviewer 여야 합니다");
  }
  // 자기 강등은 다른 admin 이 있으면 허용된다(서버와 동일) — 막는 건 "마지막 admin" 뿐이다.
  if (record.role === "admin" && req.role !== "admin" && adminCount() <= 1) {
    throw new MockApiError(
      409,
      "마지막 admin 은 강등할 수 없습니다 — 다른 사용자를 admin 으로 올린 뒤 다시 시도해 주세요"
    );
  }
  record.role = req.role;
  return toAdminUser(record);
}

/** 삭제를 막아야 하는 참조를 한 문장으로. 없으면 null (서버 `_blocking_references` 대응). */
function blockingReferences(userId: number): string | null {
  // FE 계약에 소유자가 드러나는 건 SNS 계정뿐이다(소스·키워드·템플릿은 응답에 user_id 가 없다).
  // 실서버는 그 3종도 함께 보고 막으므로, 목업에서 통과했다고 삭제 가능하다는 뜻은 아니다.
  const owned = snsAccounts.filter((a) => a.user_id === userId).length;
  if (owned) {
    return (
      `이 사용자가 소유한 리소스가 있어 삭제할 수 없습니다(SNS 계정 ${owned}건)` +
      " — 삭제하면 함께 사라집니다. 먼저 정리하거나 다른 계정으로 옮겨 주세요"
    );
  }
  if (replyActions.some((a) => a.reviewer_user_id === userId)) {
    return (
      "승인/처리 이력이 있는 사용자는 삭제할 수 없습니다" +
      " — 감사 로그는 보존됩니다(역할을 reviewer 로 낮춰 사용을 중단시켜 주세요)"
    );
  }
  return null;
}

function handleDeleteUser(id: number): void {
  const actor = requireAdmin();
  if (id === actor.id) throw new MockApiError(409, "자기 자신은 삭제할 수 없습니다");
  const idx = users.findIndex((u) => u.id === id);
  if (idx === -1) throw new MockApiError(404, "사용자가 없습니다");
  const record = users[idx];
  if (record.role === "admin" && adminCount() <= 1) {
    throw new MockApiError(
      409,
      "마지막 admin 은 삭제할 수 없습니다 — 다른 사용자를 admin 으로 올린 뒤 다시 시도해 주세요"
    );
  }
  const blocking = blockingReferences(id);
  if (blocking) throw new MockApiError(409, blocking);
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
  // 고정 경로라 /matches/{id} 패턴보다 먼저 둘 필요는 없지만(id 는 \d+ 로만 매칭) 순서를
  // 바꿔도 안전하도록 approve 앞에 둔다.
  {
    method: "POST",
    pattern: /^\/api\/matches\/render-template$/,
    handler: (_p, _q, body) => handleRenderTemplate(body),
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
  const result = route.handler(route.params, options.query, options.body);
  // 응답은 항상 깊은 복사본으로 돌려준다. 핸들러들이 모의 상태를 in-place 로 변형하는데(approve 가
  // status 를, 계정 채택이 display_name 을 바꾸는 식) 같은 객체 참조를 그대로 반환하면 React Query 의
  // 구조적 공유가 "변경 없음"으로 판단해 data 참조가 유지되고, 화면에 머문 채 갱신하는 경우 리렌더가
  // 통째로 누락된다. 실서버는 매 요청 새 JSON 을 파싱해 주므로 이쪽이 실제 동작에 맞다.
  return structuredClone(result) as T;
}
