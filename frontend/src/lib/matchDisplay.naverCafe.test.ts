/**
 * 네이버 카페 소스 계약 — 백엔드 PR #120 / 이슈 #121.
 *
 * 이 소스는 "필드 이름은 그대로인데 의미가 다른" 종류의 변경이라 타입체크로는 안 잡힌다.
 * 그래서 화면이 의존하는 세 가지를 테스트로 고정한다:
 * - 설정 키는 `query` 하나다(`cafe_id` 는 존재하지 않는 키 — 보내면 서버가 422).
 * - `author` 자리에는 글쓴이가 아니라 **카페 이름**이 온다.
 * - 답변은 전송이 아니라 클립보드 복사 경로다(`can_write=false`).
 */
import { describe, expect, it } from "vitest";
import {
  authorFieldLabel,
  describeAuthor,
  getSearchQuery,
  getSourceDisplayName,
  isSearchQuerySource,
  isWritableSourceType,
  SOURCE_TYPE_IMPLEMENTED,
} from "./matchDisplay";
import type { Source } from "../types/api";

function makeSource(overrides: Partial<Source>): Source {
  return {
    id: 1,
    name: null,
    type: "naver_cafe",
    config: {},
    poll_interval_sec: 600,
    enabled: true,
    last_success_at: null,
    health_status: "ok",
    backoff_until: null,
    last_error: null,
    last_error_at: null,
    ...overrides,
  };
}

describe("naver_cafe 소스 등록 가능 여부", () => {
  it("세 타입 모두 어댑터가 있다 — 화면이 등록을 막지 않아야 한다", () => {
    expect(SOURCE_TYPE_IMPLEMENTED).toEqual({
      threads: true,
      naver_cafe: true,
      community: true,
    });
  });
});

describe("isSearchQuerySource", () => {
  it("threads·naver_cafe 는 검색어로 수집하고 community 만 RSS URL 을 받는다", () => {
    expect(isSearchQuerySource("threads")).toBe(true);
    expect(isSearchQuerySource("naver_cafe")).toBe(true);
    expect(isSearchQuerySource("community")).toBe(false);
  });
});

describe("naver_cafe 설정 키", () => {
  it("검색어는 config.query 에서 읽는다", () => {
    expect(getSearchQuery({ query: "강아지 간식" })).toBe("강아지 간식");
  });

  it("cafe_id 는 존재하지 않는 키라 검색어로 읽히지 않는다", () => {
    expect(getSearchQuery({ cafe_id: "dogloveu" })).toBe("");
  });

  it("이름이 없으면 검색어로 소스를 표시한다 — 소스 번호로 떨어지지 않는다", () => {
    const source = makeSource({ id: 12, config: { query: "애견카페 창업" } });
    expect(getSourceDisplayName(source)).toBe("애견카페 창업");
  });
});

describe("글쓴이 자리의 의미", () => {
  it("네이버 카페는 카페 이름이므로 사람 이름처럼 보이지 않게 표시한다", () => {
    expect(describeAuthor("naver_cafe", "댕댕이 사랑방", "-")).toBe("카페 댕댕이 사랑방");
    expect(authorFieldLabel("naver_cafe")).toBe("출처 카페");
  });

  it("다른 소스는 값을 그대로 둔다", () => {
    expect(describeAuthor("threads", "dogmom_lee", "-")).toBe("dogmom_lee");
    expect(authorFieldLabel("threads")).toBe("작성자");
    // 소스를 아직 못 불러온 경우(목록 캐시 미스)도 사람 이름 취급이 기본이다.
    expect(authorFieldLabel(undefined)).toBe("작성자");
  });

  it("값이 없으면 자리마다 정한 문구로 떨어진다", () => {
    expect(describeAuthor("naver_cafe", null, "-")).toBe("-");
    expect(describeAuthor("threads", null, "작성자 미상")).toBe("작성자 미상");
  });
});

describe("전송 가능 여부", () => {
  it("네이버 카페는 전송 불가 — 승인은 클립보드 복사 경로여야 한다", () => {
    expect(isWritableSourceType("naver_cafe")).toBe(false);
    expect(isWritableSourceType("threads")).toBe(true);
  });
});
