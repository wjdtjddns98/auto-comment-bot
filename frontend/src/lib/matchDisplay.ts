import type { BadgeTone } from "../components/ui/Badge";
import type {
  MatchedPostStatus,
  ReplyActionType,
  SnsPlatform,
  Source,
  SourceType,
} from "../types/api";

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
  dcinside: "디시인사이드",
};

// 어댑터 구현 여부 — 네 타입 모두 등록 가능하다(naver_cafe 는 백엔드 PR #120, dcinside 는 PR #124
// 에서 붙어 등록이 422 → 201, 수동 검색이 502 → 정상으로 바뀌었다. 이슈 #121·#125 공유).
export const SOURCE_TYPE_IMPLEMENTED: Record<SourceType, boolean> = {
  threads: true,
  naver_cafe: true,
  community: true,
  dcinside: true,
};

/**
 * 소스가 받는 설정 키의 종류 — 등록/편집 폼이 어떤 입력칸을 보여줄지 가르는 기준.
 *
 * 서버 스키마가 전부 `extra=forbid` 라 **종류가 다른 키를 얹으면 그 자리에서 422** 다. 그래서
 * "검색어냐 아니냐" 의 2분기로는 부족하다 — dcinside 는 검색어도 RSS 도 아닌 `gallery_id` 를
 * 받는다. 소스 종류가 늘 때 이 표 한 곳만 채우면 폼·표시 문구가 함께 따라온다.
 */
export type SourceConfigKind = "query" | "rss" | "gallery";

const SOURCE_CONFIG_KIND: Record<SourceType, SourceConfigKind> = {
  // threads 는 검색어 + 수집 계정, naver_cafe 는 검색어 하나가 설정 전부다(검색 API 가 카페를
  // 지정할 수 없어 `cafe_id` 같은 키는 존재하지 않는다).
  threads: "query",
  naver_cafe: "query",
  community: "rss",
  // dcinside 는 갤러리 공개 목록 페이지를 읽는다 — 설정은 `gallery_id` 하나뿐이다(PR #124).
  dcinside: "gallery",
};

export function sourceConfigKind(type: SourceType): SourceConfigKind {
  return SOURCE_CONFIG_KIND[type];
}

/** 검색어(`config.query`)로 수집하는 소스인가 — threads·naver_cafe. */
export function isSearchQuerySource(type: SourceType): boolean {
  return sourceConfigKind(type) === "query";
}

export function getRssUrl(config: Record<string, unknown>): string {
  const value = config.rss_url;
  return typeof value === "string" ? value : "";
}

// 검색어 기반 소스(threads·naver_cafe) 공용 — 두 타입 모두 설정 키가 `query` 다.
export function getSearchQuery(config: Record<string, unknown>): string {
  const value = config.query;
  return typeof value === "string" ? value : "";
}

/**
 * 디시인사이드 갤러리 id(`config.gallery_id`).
 *
 * 서버가 영숫자·`_` 1~40자로 제한한다 — URL 쿼리 주입 차단 목적이라 화면이 임의 문자열을
 * 보내면 422 다(backend/app/sources/dcinside.py).
 */
export function getGalleryId(config: Record<string, unknown>): string {
  const value = config.gallery_id;
  return typeof value === "string" ? value : "";
}

/**
 * 소스 편집 폼이 서버 값으로 시작할 때 채울 값 한 묶음.
 *
 * 편집 칸은 `useState(() => getXxx(source.config))` 로 잡는데, 초기화 함수는 **최초 마운트에만**
 * 평가된다. 그래서 편집을 열 때마다 여기서 다시 읽어 넣지 않으면 두 가지가 샌다 — 고치다 만
 * 값을 취소하고 다시 열었을 때 그 값이 남아 있고(폴링 주기만 바꾸려다 설정이 조용히 함께
 * 바뀐다), 다른 탭에서 바뀐 소스를 목록이 다시 받아와도 폼은 낡은 값을 보여준다.
 *
 * 설정 키가 종류마다 다르므로(`sourceConfigKind`) 쓰지 않는 칸은 빈 값으로 온다 — 저장은
 * 종류에 맞는 키 하나만 보내므로(서버 스키마가 `extra=forbid`) 그대로 두어도 새어 나가지 않는다.
 */
export function sourceEditFields(source: Source): {
  name: string;
  pollIntervalSec: number;
  rssUrl: string;
  searchQuery: string;
  galleryId: string;
  threadsAccountId: number | "";
} {
  return {
    name: source.name ?? "",
    pollIntervalSec: source.poll_interval_sec,
    rssUrl: getRssUrl(source.config),
    searchQuery: getSearchQuery(source.config),
    galleryId: getGalleryId(source.config),
    threadsAccountId: getThreadsAccountId(source.config),
  };
}

export function getThreadsAccountId(config: Record<string, unknown>): number | "" {
  const value = config.sns_account_id;
  return typeof value === "number" ? value : "";
}

// 이슈 #35 — 소스 표시용 이름. 미설정 시 타입별 config 값(URL/검색어)으로 폴백.
export function getSourceDisplayName(source: Source): string {
  if (source.name) return source.name;
  switch (sourceConfigKind(source.type)) {
    case "query":
      return getSearchQuery(source.config) || `#${source.id}`;
    case "rss":
      return getRssUrl(source.config) || `#${source.id}`;
    case "gallery": {
      // 갤러리 id 만 놓으면("dog") 사람이 지은 이름처럼 읽힌다 — 검색어·URL 과 달리 값 자체가
      // 무슨 소스인지 말해주지 않아서, 여기서만 한 단어를 붙여 준다.
      const gallery = getGalleryId(source.config);
      return gallery ? `${gallery} 갤러리` : `#${source.id}`;
    }
  }
  // 타입에는 닿지 않는 줄이지만 실행에는 닿는다 — 백엔드가 FE 보다 먼저 새 소스 종류를
  // 내보내면(이 PR 이 대응하는 바로 그 상황) `SOURCE_CONFIG_KIND` 조회가 undefined 라 switch 가
  // 어느 case 에도 안 걸린다. 이 줄이 없으면 이름 칸이 `#6` 대신 **빈칸**으로 뜬다.
  return `#${source.id}`;
}

/**
 * 라벨이 붙는 칸("작성자: …")의 제목.
 *
 * 네이버 카페 검색 API 는 글쓴이를 주지 않아 `author` 자리에 **카페 이름**이 온다(PR #120).
 * 그걸 "작성자"로 적으면 화면이 없는 사실을 말하게 되므로 소스 종류로 갈라 준다.
 */
export function authorFieldLabel(type: SourceType | undefined): string {
  return type === "naver_cafe" ? "출처 카페" : "작성자";
}

/**
 * 라벨 없이 값만 놓는 칸(표 셀·목록 머리)의 글쓴이 문구.
 *
 * 네이버 카페는 카페 이름이므로 사람 이름으로 읽히지 않게 접두어를 붙인다. `fallback` 은 값이
 * 없을 때 표시할 문구 — 표에서는 `-`, 검색 결과 목록에서는 "작성자 미상" 처럼 자리마다 다르다.
 */
export function describeAuthor(
  type: SourceType | undefined,
  author: string | null,
  fallback: string
): string {
  if (!author) return fallback;
  return type === "naver_cafe" ? `카페 ${author}` : author;
}

/**
 * 수동 검색(`POST /api/sources/{id}/poll-now`)이 무엇을 대상으로 했는지 한 줄로.
 *
 * 결과 패널 머리에 놓아 "이 검색어로 검색했다"를 화면에 남기기 위한 값이다 — 앱 검수(Threads
 * `threads_keyword_search`)가 요구하는 "keyword search within your app" 장면이 소스 설정
 * 화면과 결과 목록으로 흩어지지 않게 한다(이슈 #118).
 */
export function describeSourceTarget(source: Source): string {
  switch (sourceConfigKind(source.type)) {
    case "query": {
      const query = getSearchQuery(source.config);
      return query ? `검색어 "${query}"` : "검색어 미설정";
    }
    case "rss": {
      const url = getRssUrl(source.config);
      return url ? `피드 ${url}` : "피드 URL 미설정";
    }
    case "gallery": {
      const gallery = getGalleryId(source.config);
      return gallery ? `갤러리 "${gallery}"` : "갤러리 ID 미설정";
    }
  }
  // 위 `getSourceDisplayName` 과 같은 이유의 안전망 — 모르는 소스 종류라도 머리말이 비지 않게.
  return `소스 #${source.id}`;
}

/**
 * 수동 검색 결과 요약 문구.
 *
 * 반환 수는 `posts.length` 가 아니라 `fetched` 로 센다 — `posts` 에는 표시용 상한(최대 50건)이
 * 걸려 있어서 목록 길이로는 실제로 몇 건이 왔는지 말할 수 없다. `stored` 는 검토 큐에 **새로**
 * 저장된 건수라 재검색하면 0 이 온다(docs/API-SPEC.md §소스, 백엔드 PR #117 두 번째 커밋).
 */
export function describePollSummary(fetched: number, stored: number): string {
  if (fetched === 0) return "반환된 글 없음";
  return `${fetched}건 반환 · 검토 큐에 ${stored}건 추가`;
}

/**
 * 표시용 상한에 걸려 잘린 경우에만 알려주는 보조 문구.
 *
 * 목록에 50건만 보이는데 요약은 "120건 반환"이라고 하면 화면이 스스로 모순돼 보인다.
 * 잘리지 않았으면 null — 굳이 말할 것이 없다.
 */
export function describePollTruncation(fetched: number, shown: number): string | null {
  if (shown >= fetched) return null;
  return `반환된 ${fetched}건 중 상위 ${shown}건만 표시합니다(본문은 1,000자까지).`;
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

/**
 * 소스 종류에 대응하는 계정 플랫폼 — 백엔드 `_PLATFORM_FOR_SOURCE`(app/api/matches.py)의 거울.
 *
 * 두 값은 같지 않다(네이버는 소스 `naver_cafe` ↔ 계정 `naver`). 승인 시 서버가 이 대응으로
 * 계정을 검증하므로(어긋나면 422), 계정 목록을 걸러낼 때 소스 종류와 플랫폼을 직접 비교하면
 * 안 된다 — threads 만 우연히 값이 같아 동작하는 것처럼 보인다.
 */
const PLATFORM_FOR_SOURCE_TYPE: Record<SourceType, SnsPlatform | null> = {
  threads: "threads",
  naver_cafe: "naver",
  community: "community",
  // dcinside 에 대응하는 계정 플랫폼은 **없다** — 백엔드 Platform enum 에 값이 없고
  // `_PLATFORM_FOR_SOURCE` 에도 항목이 없어서, 어떤 계정을 붙여 승인해도 422 가 된다.
  // 읽기 전용 소스(`can_write=false`)라 승인은 클립보드 복사 경로로만 간다(PR #124).
  dcinside: null,
};

export function platformForSourceType(type: SourceType): SnsPlatform | null {
  return PLATFORM_FOR_SOURCE_TYPE[type];
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
