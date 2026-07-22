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
  created_at: string;
}

export const MOCK_USERS: MockUserRecord[] = [
  {
    id: 1,
    email: "admin@example.com",
    password: "admin1234",
    role: "admin",
    created_at: "2026-07-08T00:00:00Z",
  },
  {
    id: 2,
    email: "reviewer@example.com",
    password: "reviewer1234",
    role: "reviewer",
    created_at: "2026-07-08T00:00:00Z",
  },
];

export const MOCK_SOURCES: Source[] = [
  {
    id: 1,
    name: null,
    type: "threads",
    config: { query: "강아지 간식", sns_account_id: 1 },
    poll_interval_sec: 300,
    enabled: true,
    last_success_at: "2026-07-19T09:00:00Z",
    health_status: "ok",
    backoff_until: null,
  },
  {
    id: 2,
    name: "댕러버 카페",
    type: "naver_cafe",
    config: { cafe_id: "dogloveu" },
    poll_interval_sec: 600,
    enabled: true,
    last_success_at: "2026-07-19T08:30:00Z",
    // M2 수용기준 재현: 429 주입 → health_status='degraded' + backoff_until 동시 반영
    // (backend/app/poller.py _record_failure — rate_limited=True 경로).
    health_status: "degraded",
    backoff_until: "2026-07-22T15:30:00Z",
  },
  {
    id: 3,
    name: null,
    type: "community",
    config: { rss_url: "https://example-petcommunity.com/board/feed.rss" },
    poll_interval_sec: 900,
    enabled: false,
    last_success_at: null,
    health_status: "down",
    backoff_until: "2026-07-20T12:00:00Z",
  },
];

export const MOCK_KEYWORDS: Keyword[] = [
  { id: 1, pattern: "강아지 간식", match_type: "substring", enabled: true, source_scope: null },
  { id: 2, pattern: "간식 계산기", match_type: "substring", enabled: true, source_scope: 1 },
  { id: 3, pattern: "반려견.{0,4}간식.{0,4}추천", match_type: "regex", enabled: true, source_scope: null },
];

export const MOCK_TEMPLATES: Template[] = [
  {
    id: 1,
    name: "기본 안내",
    body: "안녕하세요! 강아지 급여량 궁금하시면 누띠 간식 계산기로 체중·나이 입력해서 바로 확인해보세요 🐾",
    enabled: true,
  },
  {
    id: 2,
    name: "간식 계산기 안내",
    body: "누띠 간식 계산기는 수의영양학 공식 기반으로 우리 아이 맞춤 급여량을 알려드려요. 프로필 링크에서 무료로 계산해보세요!",
    enabled: true,
  },
];

export const MOCK_SNS_ACCOUNTS: SnsAccount[] = [
  {
    id: 1,
    user_id: 2,
    platform: "threads",
    display_name: "@nutti_official",
    status: "active",
    token_expires_at: "2026-09-01T00:00:00Z",
  },
  {
    id: 2,
    user_id: 1,
    platform: "naver_cafe",
    display_name: "누띠_어드민",
    status: "active",
    token_expires_at: "2026-08-15T00:00:00Z",
  },
];

export const MOCK_MATCHED_POSTS: MatchedPost[] = [
  {
    id: 1,
    source_id: 1,
    external_post_id: "th_1001",
    author: "user_abc",
    url: "https://www.threads.net/@user_abc/post/1001",
    content: "우리 강아지 다이어트 중인데 강아지 간식 뭐 먹여야 할지 모르겠어요",
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
    content: "간식 계산기 같은 거 있나요, 급여량 매번 감으로 주고 있어서 불안하네요",
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
    content: "반려견 수제간식 추천 부탁드려요 초보라 칼로리 계산이 어렵네요",
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
    content: "강아지 간식 너무 많이 줘서 살쪘나봐요 체중관리 후기 공유합니다",
    matched_keyword_id: 1,
    published_at: "2026-07-17T14:00:00Z",
    matched_at: "2026-07-17T14:03:00Z",
    status: "ignored",
  },
  {
    id: 5,
    source_id: 3,
    external_post_id: "cm_3001",
    // RSS 글 중 author/guid published 가 없는 경우를 재현(FR-3) — null 필드 렌더링 확인용.
    author: null,
    url: null,
    content: "강아지 간식 급여량 계산기 써보고 후기 남깁니다",
    matched_keyword_id: null,
    published_at: null,
    matched_at: "2026-07-20T08:00:00Z",
    status: "new",
  },
  {
    id: 6,
    source_id: 1,
    external_post_id: "th_1003",
    author: "user_ghi",
    url: "https://www.threads.net/@user_ghi/post/1003",
    content: "강아지 간식 계산기 써봤는데 결과가 정확한지 궁금해요",
    matched_keyword_id: 1,
    published_at: "2026-07-20T12:00:00Z",
    matched_at: "2026-07-20T12:05:00Z",
    // 전송 결과 불명 — 자동 조정 대기(M2). 읽기 전용 뱃지 + ignore 만 활성 화면 재현용.
    status: "verify_pending",
  },
  {
    id: 7,
    source_id: 1,
    external_post_id: "th_1004",
    author: "user_jkl",
    url: "https://www.threads.net/@user_jkl/post/1004",
    content: "강아지 간식 추천 감사합니다 계산기로 급여량 확인해봤어요",
    matched_keyword_id: 1,
    published_at: "2026-07-20T13:00:00Z",
    matched_at: "2026-07-20T13:05:00Z",
    // 조정(reconcile) 성공 확정 — verify_pending 에서 실 게시가 확인돼 replied 로 전이(M2 수용
    // 기준 "Threads 실발송→external_reply_id 기록", #28 조정 조회 흐름 최종 확인용).
    status: "replied",
  },
  {
    id: 8,
    source_id: 2,
    external_post_id: "nc_2003",
    author: "cafe_user_3",
    url: "https://cafe.naver.com/example/2003",
    // 네이버 카페(can_write=false) + status=new 재현용 — 승인→클립보드 복사 플로우
    // (MatchDetailPage handleCopy)를 mock 환경에서 확인할 유일한 naver_cafe 케이스.
    content: "강아지 간식 급여량 계산기 써보신 분 계신가요, 초보라 감이 안 잡히네요",
    matched_keyword_id: 1,
    published_at: "2026-07-21T10:00:00Z",
    matched_at: "2026-07-21T10:05:00Z",
    status: "new",
  },
];

export const MOCK_REPLY_ACTIONS: ReplyAction[] = [
  {
    id: 1,
    matched_post_id: 3,
    reviewer_user_id: 2,
    action: "approved",
    template_id: 1,
    external_reply_id: null,
    error: null,
    created_at: "2026-07-18T09:20:00Z",
  },
  {
    id: 2,
    matched_post_id: 2,
    reviewer_user_id: 2,
    action: "failed",
    template_id: null,
    external_reply_id: null,
    // 조정 잡의 미게시 판정 이력(backend/app/reconcile.py) — retry 버튼 옆 권장 문구 재현용.
    error: "조정 완료 — 미게시 판정. 재시도 전 대상 글에서 직접 확인을 권장합니다",
    created_at: "2026-07-19T11:30:00Z",
  },
  {
    id: 3,
    matched_post_id: 6,
    reviewer_user_id: 2,
    action: "unknown",
    template_id: 1,
    external_reply_id: null,
    error: "전송 결과 확인 중 — 자동 조정 후 재시도 가능해집니다",
    created_at: "2026-07-20T12:06:00Z",
  },
  {
    id: 4,
    matched_post_id: 7,
    reviewer_user_id: 2,
    action: "unknown",
    template_id: 1,
    external_reply_id: null,
    error: "전송 결과 확인 중 — 자동 조정 후 재시도 가능해집니다",
    created_at: "2026-07-20T13:06:00Z",
  },
  {
    id: 5,
    matched_post_id: 7,
    reviewer_user_id: 2,
    // 조정 잡이 대상 글에서 실제 게시된 답글을 찾아 확정한 이력(backend/app/reconcile.py 168-174)
    // — external_reply_id 가 이 시점에 채워진다. unknown → sent 순서 재현이 핵심(#28 최종 확인).
    action: "sent",
    template_id: 1,
    external_reply_id: "th_reply_9001",
    error: null,
    created_at: "2026-07-20T13:11:00Z",
  },
];
