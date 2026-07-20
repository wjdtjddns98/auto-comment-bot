import type { BadgeTone } from "../components/ui/Badge";
import type { MatchedPostStatus, SourceType } from "../types/api";

export const STATUS_LABEL: Record<MatchedPostStatus, string> = {
  new: "신규",
  reviewing: "검토중",
  sending: "전송중",
  replied: "답변완료",
  ignored: "무시됨",
};

export const STATUS_TONE: Record<MatchedPostStatus, BadgeTone> = {
  new: "info",
  reviewing: "warning",
  sending: "warning",
  replied: "success",
  ignored: "neutral",
};

export const SOURCE_TYPE_LABEL: Record<SourceType, string> = {
  threads: "Threads",
  naver_cafe: "네이버 카페",
  community: "커뮤니티",
};

// 어댑터 구현 여부 — community(RSS)만 M1 범위, 나머지는 M2 예정(backend/app/sources/__init__.py 참고).
export const SOURCE_TYPE_IMPLEMENTED: Record<SourceType, boolean> = {
  threads: false,
  naver_cafe: false,
  community: true,
};

export function getRssUrl(config: Record<string, unknown>): string {
  const value = config.rss_url;
  return typeof value === "string" ? value : "";
}

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
