/**
 * 수동 검색(`POST /api/sources/{id}/poll-now`) 결과 표시 헬퍼 — 이슈 #118.
 *
 * 특히 `describePollSummary` 는 문구 하나가 사실관계를 바꾸는 자리라 고정해 둔다.
 * 백엔드의 `stored` 는 **키워드에 일치한 글 수**이고 dedup 으로 무시된 중복분이 포함된다
 * (backend/app/poller.py `_store_matches` 는 `bulk_create(ignore_conflicts=True)` 에 넘긴
 * 행 수를 그대로 돌려준다). 같은 소스를 두 번 검색하면 두 번째도 같은 수가 오므로,
 * "검토 큐에 N건 추가" 로 적으면 아무것도 안 늘었는데 늘었다고 말하게 된다.
 */
import { describe, expect, it } from "vitest";
import { describePollSummary, describeSourceTarget } from "./matchDisplay";
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
  it("반환 건수와 키워드 일치 건수를 함께 보여준다", () => {
    expect(describePollSummary(3, 1)).toBe("3건 반환 · 키워드 일치 1건");
  });

  it("'추가' 로 표현하지 않는다 — stored 는 dedup 무시분을 포함한 일치 수다", () => {
    expect(describePollSummary(3, 1)).not.toContain("추가");
  });

  it("반환된 글이 없으면 건수를 나열하지 않고 빈 결과라고 말한다", () => {
    expect(describePollSummary(0, 0)).toBe("반환된 글 없음");
  });

  it("반환은 있는데 일치가 0건인 경우도 그대로 드러낸다", () => {
    expect(describePollSummary(2, 0)).toBe("2건 반환 · 키워드 일치 0건");
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
