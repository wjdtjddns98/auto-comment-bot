/**
 * 디시인사이드 소스 계약 — 백엔드 PR #124 / 이슈 #125.
 *
 * 이 소스가 들여오는 함정은 "설정 키가 세 번째 종류" 라는 점이다. 화면은 오래 「검색어냐
 * RSS 냐」의 2분기로 폼을 갈랐고, 그 분기에 dcinside 가 들어오면 조용히 RSS 쪽으로 떨어져
 * `{rss_url}` 을 보낸다 — 서버 스키마가 `extra=forbid` 라 **그 자리에서 422** 다. 타입체크로는
 * 안 잡히는 종류라(키 이름이 아니라 값의 의미가 다르다) 테스트로 고정한다.
 *
 * 함께 고정하는 것:
 * - 설정 키는 `gallery_id` 하나다(`query`·`rss_url` 은 이 소스에 존재하지 않는 키).
 * - 계정 플랫폼 대응이 **없다** — 승인은 클립보드 복사 경로로만 간다.
 * - 글쓴이는 사람 닉네임이라 카페처럼 접두어를 붙이면 안 된다.
 */
import { describe, expect, it } from "vitest";
import {
  authorFieldLabel,
  describeAuthor,
  describeSourceTarget,
  getGalleryId,
  getSourceDisplayName,
  isWritableSourceType,
  SOURCE_TYPE_LABEL,
  sourceConfigKind,
} from "./matchDisplay";
import type { Source } from "../types/api";

function makeSource(overrides: Partial<Source>): Source {
  return {
    id: 6,
    name: null,
    type: "dcinside",
    config: { gallery_id: "dog" },
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

describe("설정 키의 종류", () => {
  it("dcinside 는 검색어도 RSS 도 아닌 세 번째 종류다 — 2분기로 가르면 RSS 로 샌다", () => {
    expect(sourceConfigKind("dcinside")).toBe("gallery");
    expect(sourceConfigKind("threads")).toBe("query");
    expect(sourceConfigKind("naver_cafe")).toBe("query");
    expect(sourceConfigKind("community")).toBe("rss");
  });

  it("갤러리 id 는 config.gallery_id 에서 읽는다", () => {
    expect(getGalleryId({ gallery_id: "dog" })).toBe("dog");
  });

  it("query·rss_url 은 이 소스에 존재하지 않는 키라 갤러리 id 로 읽히지 않는다", () => {
    expect(getGalleryId({ query: "강아지 간식" })).toBe("");
    expect(getGalleryId({ rss_url: "https://example.com/feed" })).toBe("");
  });
});

describe("소스 표시", () => {
  it("이름이 없으면 갤러리 id 로 표시한다 — 소스 번호로 떨어지지 않는다", () => {
    expect(getSourceDisplayName(makeSource({}))).toBe("dog 갤러리");
  });

  it("이름을 붙였으면 이름이 우선이다", () => {
    expect(getSourceDisplayName(makeSource({ name: "멍멍이갤" }))).toBe("멍멍이갤");
  });

  it("갤러리 id 가 비었을 때만 소스 번호로 떨어진다", () => {
    expect(getSourceDisplayName(makeSource({ id: 9, config: {} }))).toBe("#9");
  });

  it("수동 검색 결과 머리말이 무엇을 대상으로 했는지 말한다", () => {
    expect(describeSourceTarget(makeSource({}))).toBe('갤러리 "dog"');
    expect(describeSourceTarget(makeSource({ config: {} }))).toBe("갤러리 ID 미설정");
  });

  it("타입 라벨이 붙어 있다 — 표의 종류 칸이 원문 값으로 새지 않아야 한다", () => {
    expect(SOURCE_TYPE_LABEL.dcinside).toBe("디시인사이드");
  });
});

describe("글쓴이 자리의 의미", () => {
  it("갤 닉네임은 사람 이름이다 — 카페처럼 접두어를 붙이지 않는다", () => {
    expect(describeAuthor("dcinside", "댕댕이아빠", "-")).toBe("댕댕이아빠");
    expect(authorFieldLabel("dcinside")).toBe("작성자");
  });

  it("유동닉도 값 그대로 둔다 — 여러 글에 같은 'ㅇㅇ' 가 반복되는 것이 정상이다", () => {
    expect(describeAuthor("dcinside", "ㅇㅇ", "-")).toBe("ㅇㅇ");
  });
});

describe("전송 가능 여부", () => {
  it("디시는 댓글/글쓰기 API 가 없다 — 승인은 클립보드 복사 경로여야 한다", () => {
    expect(isWritableSourceType("dcinside")).toBe(false);
  });
});
