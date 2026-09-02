/**
 * 수동 검색(`POST /api/sources/{id}/poll-now`) 결과 표시 헬퍼 — 이슈 #118.
 *
 * 요약 문구는 두 숫자를 헷갈리면 바로 거짓말이 되는 자리라 고정해 둔다(계약: 이슈 #118
 * 2026-09-02 갱신 코멘트 + `docs/API-SPEC.md` §소스).
 * - 반환 수는 `fetched` 다. `posts` 는 표시용 상한(최대 50건)이 걸려 있어 목록 길이로 세면
 *   50건 넘게 온 검색에서 "50건 반환"이라고 축소 보고하게 된다.
 * - `stored` 는 검토 큐에 **새로** 저장된 건수라 재검색하면 0 이다.
 */
import { describe, expect, it } from "vitest";
import { describePollSummary, describePollTruncation, describeSourceTarget } from "./matchDisplay";
import type { Source } from "../types/api";

function makeSource(overrides: Partial<Source>): Source {
  return {
    id: 1,
    name: null,
    type: "threads",
    config: {},
    poll_interval_sec: 300,
    enabled: true,
    last_success_at: null,
    health_status: "ok",
    backoff_until: null,
    last_error: null,
    last_error_at: null,
    ...overrides,
  };
}

describe("describePollSummary", () => {
  it("반환 건수와 검토 큐에 추가된 건수를 함께 보여준다", () => {
    expect(describePollSummary(3, 1)).toBe("3건 반환 · 검토 큐에 1건 추가");
  });

  it("반환된 글이 없으면 건수를 나열하지 않고 빈 결과라고 말한다", () => {
    expect(describePollSummary(0, 0)).toBe("반환된 글 없음");
  });

  it("재검색이라 큐에 새로 들어간 게 없으면 0건 추가로 말한다", () => {
    expect(describePollSummary(3, 0)).toBe("3건 반환 · 검토 큐에 0건 추가");
  });

  it("표시 상한을 넘긴 반환 수도 그대로 센다 — 목록 길이가 아니라 fetched 기준", () => {
    expect(describePollSummary(120, 4)).toBe("120건 반환 · 검토 큐에 4건 추가");
  });
});

describe("describePollTruncation", () => {
  it("표시 상한에 걸린 경우에만 잘렸다고 알린다", () => {
    expect(describePollTruncation(120, 50)).toBe(
      "반환된 120건 중 상위 50건만 표시합니다(본문은 1,000자까지)."
    );
  });

  it("전량이 보이면 굳이 말하지 않는다", () => {
    expect(describePollTruncation(3, 3)).toBeNull();
    expect(describePollTruncation(0, 0)).toBeNull();
  });
});

describe("describeSourceTarget", () => {
  it("threads 는 검색어를 보여준다 — 검수 영상이 요구하는 '무엇으로 검색했나'", () => {
    const source = makeSource({ type: "threads", config: { query: "강아지 간식", sns_account_id: 1 } });
    expect(describeSourceTarget(source)).toBe('검색어 "강아지 간식"');
  });

  it("community 는 피드 URL 을 보여준다", () => {
    const source = makeSource({ type: "community", config: { rss_url: "https://ex.com/feed.rss" } });
    expect(describeSourceTarget(source)).toBe("피드 https://ex.com/feed.rss");
  });

  it("설정이 비어 있어도 빈 따옴표를 보여주지 않는다", () => {
    expect(describeSourceTarget(makeSource({ type: "threads", config: {} }))).toBe("검색어 미설정");
    expect(describeSourceTarget(makeSource({ type: "community", config: {} }))).toBe(
      "피드 URL 미설정"
    );
  });

  it("어댑터가 없는 타입은 소스 번호로 떨어진다", () => {
    const source = makeSource({ id: 7, type: "naver_cafe", config: { cafe_id: "x" } });
    expect(describeSourceTarget(source)).toBe("소스 #7");
  });
});
