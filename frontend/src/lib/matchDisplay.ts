import type { BadgeTone } from "../components/ui/Badge";
import type { MatchedPostStatus, ReplyActionType, Source, SourceType } from "../types/api";

export const STATUS_LABEL: Record<MatchedPostStatus, string> = {
  new: "신규",
  reviewing: "검토중",
  sending: "전송중",
  replied: "답변완료",
  ignored: "무시됨",
  verify_pending: "전송결과 확인중",
};

export const STATUS_TONE: Record<MatchedPostStatus, BadgeTone> = {
  new: "info",
  reviewing: "warning",
  sending: "warning",
  replied: "success",
  ignored: "neutral",
  verify_pending: "warning",
};

// 조정(reconcile) 잡이 미게시 판정 시 남기는 이력 문구(backend/app/reconcile.py) — 원문 그대로 매칭해
// retry 버튼 옆 권장 문구를 노출하는 트리거로 쓴다.
export const RECONCILED_UNPUBLISHED_ERROR =
  "조정 완료 — 미게시 판정. 재시도 전 대상 글에서 직접 확인을 권장합니다";

export const SOURCE_TYPE_LABEL: Record<SourceType, string> = {
  threads: "Threads",
  naver_cafe: "네이버 카페",
  community: "커뮤니티",
};

// 어댑터 구현 여부 — community(RSS)·threads(키워드 검색) 등록 가능, naver_cafe 는 여전히 미구현(등록 422).
export const SOURCE_TYPE_IMPLEMENTED: Record<SourceType, boolean> = {
  threads: true,
  naver_cafe: false,
  community: true,
};

export function getRssUrl(config: Record<string, unknown>): string {
  const value = config.rss_url;
  return typeof value === "string" ? value : "";
}

export function getThreadsQuery(config: Record<string, unknown>): string {
  const value = config.query;
  return typeof value === "string" ? value : "";
}

export function getThreadsAccountId(config: Record<string, unknown>): number | "" {
  const value = config.sns_account_id;
  return typeof value === "number" ? value : "";
}

// 이슈 #35 — 소스 표시용 이름. 미설정 시 타입별 config 값(URL/검색어)으로 폴백.
export function getSourceDisplayName(source: Source): string {
  if (source.name) return source.name;
  if (source.type === "threads") return getThreadsQuery(source.config) || `#${source.id}`;
  if (source.type === "community") return getRssUrl(source.config) || `#${source.id}`;
  return `#${source.id}`;
}

// Threads 플랫폼 실 상한(API 의 final_body 상한은 2000자지만 전송 시 500자 초과는 502 확정 실패) — 이슈 #30.
export const THREADS_BODY_LIMIT = 500;

export const HEALTH_TONE: Record<string, BadgeTone> = {
  ok: "success",
  degraded: "warning",
  down: "danger",
};

// docs/PRD.md 소스 능력 매트릭스 — Threads만 전송(write) 지원, 나머지는 클립보드 복사.
const WRITABLE_SOURCE_TYPES: SourceType[] = ["threads"];

export function isWritableSourceType(type: SourceType): boolean {
  return WRITABLE_SOURCE_TYPES.includes(type);
}

export const REPLY_ACTION_LABEL: Record<ReplyActionType, string> = {
  approved: "승인(수동 복사)",
  sent: "전송 성공",
  failed: "전송 실패",
  canceled: "무시(취소)",
  unknown: "결과 불명(조정 대기)",
};

export const REPLY_ACTION_TONE: Record<ReplyActionType, BadgeTone> = {
  approved: "info",
  sent: "success",
  failed: "danger",
  canceled: "neutral",
  unknown: "warning",
};

// 마크업(태그)이나 엔티티가 섞여 있는지 — 순수 텍스트 본문(Threads 등)은 건드리지 않기 위한 가드.
const HTML_LIKE = /<[a-z!/][^>]*>|&(?:[a-z]+|#\d+|#x[0-9a-f]+);/i;

/**
 * 게시물 본문을 표시용 평문으로 정규화한다.
 *
 * RSS(community) 소스의 `content` 는 피드 description 원문이라 `<a href="...">`·`<font>`·`&nbsp;`
 * 가 그대로 들어온다. React 가 이스케이프하므로 XSS 는 없지만 목록·상세에 마크업이 그대로 찍히고
 * href 의 긴 URL 이 카드를 가로로 밀어낸다(2026-07-24 실서버 QA).
 *
 * DOMParser 로 파싱한 문서는 비활성(inert) 이라 스크립트 실행·리소스 로드가 없고, 여기서는
 * textContent 만 꺼내 쓴다 — innerHTML 로 되돌리지 말 것.
 */
export function toPlainText(content: string): string {
  if (!HTML_LIKE.test(content)) return content;
  // 블록 경계는 줄바꿈으로 살린다 — textContent 는 태그를 지우며 줄바꿈도 함께 잃는다.
  const withBreaks = content.replace(/<br\s*\/?>|<\/(?:p|div|li)>/gi, "\n");
  const text = new DOMParser().parseFromString(withBreaks, "text/html").body.textContent ?? "";
  return text
    .replace(/ /g, " ") // &nbsp; → 일반 공백
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// health 배지 툴팁 문구(R17). 화면에는 잘린 한 줄만 보이므로 전문(최대 500자)과 발생
// 시각은 title 로 노출한다. 원문을 가공하지 않는 이유: 백엔드가 넣는 요약
// (`FetchError: HTTP 500`·`RateLimitedError: HTTP 429`·`내부 오류: <타입명>`)이 진단에
// 실제로 쓰이는 값이라, FE 가 임의 매핑하면 새 실패 유형이 생길 때 조용히 어긋난다.
export function sourceErrorTitle(error: string, at: string | null): string {
  return at ? `${error}\n(발생: ${formatDateTime(at)})` : error;
}
