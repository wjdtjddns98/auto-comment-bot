/**
 * 소스 편집 폼의 초기값 — 「취소했는데 값이 남아 있다」 회귀 방지.
 *
 * 편집 칸은 `useState(() => getXxx(source.config))` 로 잡는데 초기화 함수는 **최초 마운트에만**
 * 평가된다. 그래서 편집을 열 때마다 서버 값으로 다시 채우지 않으면 두 가지가 샌다:
 *
 * 1. 갤러리를 `dog` → `cat` 으로 고치다 취소하고 다시 열면 `cat` 이 남아 있다. 폴링 주기만
 *    바꾸려고 저장을 누르면 갤러리가 **조용히 함께 바뀐다**.
 * 2. 다른 탭에서 소스가 바뀌어 목록을 다시 받아와도 폼은 낡은 값을 보여준다.
 *
 * `AdminSourcesPage` 의 `startEditing` 이 이 함수로 모든 칸을 되돌린다. 컴포넌트 테스트 러너가
 * 없어 폼 자체는 직접 못 돌리므로, 폼이 의존하는 이 순수 함수를 고정한다.
 */
import { describe, expect, it } from "vitest";
import { sourceEditFields } from "./matchDisplay";
import type { Source } from "../types/api";

function makeSource(overrides: Partial<Source>): Source {
  return {
    id: 1,
    name: null,
    type: "community",
    config: {},
    poll_interval_sec: 900,
    enabled: true,
    last_success_at: null,
    health_status: "ok",
    backoff_until: null,
    last_error: null,
    last_error_at: null,
    ...overrides,
  };
}

describe("sourceEditFields", () => {
  it("항상 소스의 현재 값을 돌려준다 — 편집 중 입력이 아니라", () => {
    const source = makeSource({
      type: "dcinside",
      name: "멍멍이갤",
      config: { gallery_id: "dog" },
      poll_interval_sec: 600,
    });
    expect(sourceEditFields(source)).toEqual({
      name: "멍멍이갤",
      pollIntervalSec: 600,
      rssUrl: "",
      searchQuery: "",
      galleryId: "dog",
      threadsAccountId: "",
    });
  });

  it("이름 없는 소스는 빈 문자열로 — null 이 그대로 입력칸에 들어가면 안 된다", () => {
    expect(sourceEditFields(makeSource({ name: null })).name).toBe("");
  });

  it("종류마다 쓰는 칸만 채워진다 — 나머지는 빈 값", () => {
    const rss = sourceEditFields(
      makeSource({ type: "community", config: { rss_url: "https://example.com/feed" } })
    );
    expect(rss.rssUrl).toBe("https://example.com/feed");
    expect(rss.galleryId).toBe("");
    expect(rss.searchQuery).toBe("");

    const query = sourceEditFields(
      makeSource({ type: "threads", config: { query: "강아지 간식", sns_account_id: 3 } })
    );
    expect(query.searchQuery).toBe("강아지 간식");
    expect(query.threadsAccountId).toBe(3);
    expect(query.rssUrl).toBe("");
    expect(query.galleryId).toBe("");
  });

  it("같은 소스로 두 번 불러도 같은 값이다 — 편집을 다시 열면 늘 서버 값에서 시작한다", () => {
    const source = makeSource({ type: "dcinside", config: { gallery_id: "dog" } });
    expect(sourceEditFields(source)).toEqual(sourceEditFields(source));
  });
});
