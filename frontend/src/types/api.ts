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
  type: SourceType;
  config: Record<string, unknown>;
  poll_interval_sec: number;
  enabled: boolean;
  last_success_at: string | null;
  health_status: string;
  backoff_until: string | null;
}

export interface CreateSourceRequest {
  type: SourceType;
  config: Record<string, unknown>;
  poll_interval_sec: number;
}

export interface PatchSourceRequest {
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

export type MatchedPostStatus =
  | "new"
  | "reviewing"
  | "sending"
  | "replied"
  | "ignored";

export interface MatchedPost {
  id: number;
  source_id: number;
  external_post_id: string;
  author: string;
  url: string;
  content: string;
  matched_keyword_id: number;
  published_at: string;
  matched_at: string;
  status: MatchedPostStatus;
}

export interface MatchListResponse {
  items: MatchedPost[];
  total: number;
}

export interface MatchDetail extends MatchedPost {
  reply_actions: ReplyAction[];
}

export type ReplyActionType = "approved" | "sent" | "failed";

export interface ReplyAction {
  id: number;
  matched_post_id: number;
  reviewer_user_id: number;
  action: ReplyActionType;
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
