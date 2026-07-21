import type { BadgeTone } from "../components/ui/Badge";
import type { MatchedPostStatus, Source, SourceType } from "../types/api";

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
