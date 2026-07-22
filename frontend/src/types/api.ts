// docs/API-SPEC.md 기준 타입. 계약 변경 시 해당 문서와 함께 갱신할 것.

export type Role = "admin" | "reviewer";

export interface User {
  id: number;
  email: string;
  role: Role;
}

export interface Health {
  status: "ok" | "degraded";
  db: string;
  poller?: {
    last_tick: string;
    sources: Array<{
      source_id: number;
      health_status: string;
      last_success_at: string | null;
    }>;
  };
}

export type SourceType = "threads" | "naver_cafe" | "community";

export interface Source {
  id: number;
  name: string | null;
  type: SourceType;
  config: Record<string, unknown>;
  poll_interval_sec: number;
  enabled: boolean;
  last_success_at: string | null;
  health_status: string;
  backoff_until: string | null;
}

export interface CreateSourceRequest {
  name?: string;
  type: SourceType;
  config: Record<string, unknown>;
  poll_interval_sec: number;
}

export interface PatchSourceRequest {
  name?: string | null;
  enabled?: boolean;
  poll_interval_sec?: number;
  config?: Record<string, unknown>;
}

export type MatchType = "substring" | "regex";

export interface Keyword {
  id: number;
  pattern: string;
  match_type: MatchType;
  enabled: boolean;
  source_scope: number | null;
}

export interface CreateKeywordRequest {
  pattern: string;
  match_type: MatchType;
  source_scope?: number | null;
}

export interface PatchKeywordRequest {
  pattern?: string;
  match_type?: MatchType;
  enabled?: boolean;
  source_scope?: number | null;
}

export interface Template {
  id: number;
  name: string;
  body: string;
  enabled: boolean;
}

export interface CreateTemplateRequest {
  name: string;
  body: string;
}

export interface PatchTemplateRequest {
  name?: string;
  body?: string;
  enabled?: boolean;
}

export type SnsPlatform = "threads" | "naver_cafe" | "community";

export interface SnsAccount {
  id: number;
  user_id: number;
  platform: SnsPlatform;
  display_name: string;
  status: string;
  token_expires_at: string | null;
}

export interface CreateSnsAccountRequest {
  platform: SnsPlatform;
  display_name: string;
  credentials: Record<string, unknown>;
}

export interface UpdateSnsAccountCredentialsRequest {
  credentials: Record<string, unknown>;
}

export type MatchedPostStatus =
  | "new"
  | "reviewing"
  | "sending"
  | "replied"
  | "ignored"
  // 전송 결과 불명 — 자동 조정 대기. 재전송 차단, ignore 만 가능(docs/M2-SEND-RECONCILIATION.md).
  | "verify_pending";

export interface MatchedPost {
  id: number;
  source_id: number;
  external_post_id: string;
  // author/url: RSS 등 일부 소스는 값이 없을 수 있음. published_at: 피드에 게시일시가 없을 수 있음.
  author: string | null;
  url: string | null;
  content: string;
  // 키워드가 삭제되면 SET NULL 처리(매칭 이력은 보존) — backend/app/models/__init__.py 참고.
  matched_keyword_id: number | null;
  published_at: string | null;
  matched_at: string;
  status: MatchedPostStatus;
}

export interface MatchListResponse {
  items: MatchedPost[];
  total: number;
}

// GET /api/matches/{id} 의 reply_actions 는 감사로그 목록과 달리 matched_post_id 를 포함하지 않는다
// (backend/app/api/matches.py ReplyActionOut 참조).
export interface MatchDetail extends MatchedPost {
  reply_actions: Array<Omit<ReplyAction, "matched_post_id">>;
}

export type ReplyActionType = "approved" | "sent" | "failed" | "canceled" | "unknown";

export interface ReplyAction {
  id: number;
  matched_post_id: number;
  reviewer_user_id: number;
  action: ReplyActionType;
  template_id: number | null;
  external_reply_id: string | null;
  error: string | null;
  created_at: string;
}

export interface ApproveMatchRequest {
  template_id?: number;
  final_body: string;
  sns_account_id?: number;
}

export type ApproveMatchResponse =
  | { action: "sent"; external_reply_id: string }
  | { action: "approved"; clipboard_body: string };
