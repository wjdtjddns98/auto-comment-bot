// 백엔드 없이 프론트 개발을 진행하기 위한 모의(mock) 시드 데이터.
// docs/API-SPEC.md·src/types/api.ts 계약을 그대로 따른다.

import type {
  Keyword,
  MatchedPost,
  ReplyAction,
  SnsAccount,
  Source,
  Template,
  User,
} from "../../types/api";

export interface MockUserRecord extends User {
  password: string;
}

export const MOCK_USERS: MockUserRecord[] = [
  { id: 1, email: "admin@example.com", password: "admin1234", role: "admin" },
  { id: 2, email: "reviewer@example.com", password: "reviewer1234", role: "reviewer" },
];

export const MOCK_SOURCES: Source[] = [
  {
    id: 1,
    type: "threads",
    config: { keyword_scope: "public" },
    poll_interval_sec: 300,
    enabled: true,
    last_success_at: "2026-07-19T09:00:00Z",
    health_status: "ok",
    backoff_until: null,
  },
  {
    id: 2,
    type: "naver_cafe",
    config: { cafe_id: "12345" },
    poll_interval_sec: 600,
    enabled: true,
    last_success_at: "2026-07-19T08:30:00Z",
    health_status: "degraded",
    backoff_until: null,
  },
  {
    id: 3,
    type: "community",
    config: { board_url: "https://example-community.com/board" },
    poll_interval_sec: 900,
    enabled: false,
    last_success_at: null,
    health_status: "down",
    backoff_until: "2026-07-20T12:00:00Z",
  },
];

export const MOCK_KEYWORDS: Keyword[] = [
  { id: 1, pattern: "이사 업체", match_type: "substring", enabled: true, source_scope: null },
  { id: 2, pattern: "포장이사", match_type: "substring", enabled: true, source_scope: 1 },
  { id: 3, pattern: "이사.{0,4}추천", match_type: "regex", enabled: true, source_scope: null },
];

export const MOCK_TEMPLATES: Template[] = [
  {
    id: 1,
    name: "기본 안내",
    body: "안녕하세요! 문의 주셔서 감사합니다. 무료 견적은 프로필 링크에서 확인하실 수 있어요.",
    enabled: true,
  },
  {
    id: 2,
    name: "포장이사 안내",
    body: "포장이사 전문 업체입니다. 지역/평수 알려주시면 견적 도와드릴게요!",
    enabled: true,
  },
];

export const MOCK_SNS_ACCOUNTS: SnsAccount[] = [
  {
    id: 1,
    platform: "threads",
    display_name: "@our_brand",
    status: "active",
    token_expires_at: "2026-09-01T00:00:00Z",
  },
];

export const MOCK_MATCHED_POSTS: MatchedPost[] = [
  {
    id: 1,
    source_id: 1,
    external_post_id: "th_1001",
    author: "user_abc",
    url: "https://www.threads.net/@user_abc/post/1001",
    content: "다음주에 이사 업체 알아보는 중인데 다들 어디 쓰세요?",
    matched_keyword_id: 1,
    published_at: "2026-07-19T10:00:00Z",
    matched_at: "2026-07-19T10:05:00Z",
    status: "new",
  },
  {
    id: 2,
    source_id: 1,
    external_post_id: "th_1002",
    author: "user_def",
    url: "https://www.threads.net/@user_def/post/1002",
    content: "포장이사 견적 비교하고 있어요, 추천 좀요",
    matched_keyword_id: 2,
    published_at: "2026-07-19T11:00:00Z",
    matched_at: "2026-07-19T11:02:00Z",
    status: "reviewing",
  },
  {
    id: 3,
    source_id: 2,
    external_post_id: "nc_2001",
    author: "cafe_user_1",
    url: "https://cafe.naver.com/example/2001",
    content: "이사 업체 추천 부탁드려요 원룸이라 짐은 적어요",
    matched_keyword_id: 3,
    published_at: "2026-07-18T09:00:00Z",
    matched_at: "2026-07-18T09:10:00Z",
    status: "replied",
  },
  {
    id: 4,
    source_id: 2,
    external_post_id: "nc_2002",
    author: "cafe_user_2",
    url: "https://cafe.naver.com/example/2002",
    content: "이사 업체 사기 조심하세요 후기 공유합니다",
    matched_keyword_id: 1,
    published_at: "2026-07-17T14:00:00Z",
    matched_at: "2026-07-17T14:03:00Z",
    status: "ignored",
  },
];

export const MOCK_REPLY_ACTIONS: ReplyAction[] = [
  {
    id: 1,
    matched_post_id: 3,
    reviewer_user_id: 2,
    action: "approved",
    external_reply_id: null,
    error: null,
    created_at: "2026-07-18T09:20:00Z",
  },
];
