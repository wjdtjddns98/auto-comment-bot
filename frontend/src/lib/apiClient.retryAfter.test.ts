/**
 * 429 의 `Retry-After`(초) 를 ApiError 로 실어 나르는지 — 이슈 #118 수동 검색.
 *
 * 소스 관리 화면의 [지금 검색] 버튼은 이 값만큼 잠긴다. 헤더가 유실되면 잠금이 조용히
 * 사라져 연타가 가능해지므로(불변식 ④ — 예의 있는 수집), 파싱 경로를 고정한다.
 * 없거나 이상한 값이면 잠그지 않는다(undefined) — 잘못된 값으로 오래 잠그는 것보다 낫다.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const originalFetch = globalThis.fetch;

/** 실제 Response 대신 쓰는 최소 스텁 — headers 는 Headers 로 진짜 조회 동작을 재현한다. */
function errorResponse(status: number, detail: string, headers: Record<string, string> = {}) {
  return {
    ok: false,
    status,
    statusText: String(status),
    headers: new Headers(headers),
    json: async () => ({ detail }),
  };
}

async function freshApiClient() {
  vi.resetModules();
  return import("./apiClient");
}

beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

/** CSRF 발급 1회 + 대상 요청 1회를 처리하는 스텁. */
function stubFetch(response: unknown) {
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).includes("/api/auth/csrf")) {
      return {
        ok: true,
        status: 200,
        statusText: "200",
        headers: new Headers(),
        json: async () => ({ csrf_token: "T1" }),
      } as unknown as Response;
    }
    return response as Response;
  }) as unknown as typeof fetch;
}

describe("429 Retry-After", () => {
  it("초 단위 헤더를 ApiError.retryAfterSec 로 싣는다", async () => {
    stubFetch(errorResponse(429, "직전 수집 후 10초 이내입니다 — 7초 후 다시 시도해 주세요", {
      "Retry-After": "7",
    }));
    const { pollSourceNow, ApiError } = await freshApiClient();
    const err = await pollSourceNow(1).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(429);
    expect(err.retryAfterSec).toBe(7);
    // detail 은 손대지 않고 그대로 — 사용자에게 서버 문구를 보여줘야 한다.
    expect(err.detail).toContain("7초 후 다시 시도해 주세요");
  });

  it("헤더가 없으면 undefined — 임의로 잠그지 않는다", async () => {
    stubFetch(errorResponse(429, "소스가 rate-limit backoff 중입니다"));
    const { pollSourceNow } = await freshApiClient();
    const err = await pollSourceNow(1).catch((e) => e);
    expect(err.retryAfterSec).toBeUndefined();
  });

  it("숫자가 아니거나 0 이하면 무시한다", async () => {
    for (const value of ["Wed, 21 Oct 2026 07:28:00 GMT", "0", "-5", "abc"]) {
      stubFetch(errorResponse(429, "…", { "Retry-After": value }));
      const { pollSourceNow } = await freshApiClient();
      const err = await pollSourceNow(1).catch((e) => e);
      expect(err.retryAfterSec, `Retry-After: ${value}`).toBeUndefined();
    }
  });

  it("소수 초는 올림한다 — 덜 기다려 다시 429 를 맞지 않게", async () => {
    stubFetch(errorResponse(429, "…", { "Retry-After": "2.4" }));
    const { pollSourceNow } = await freshApiClient();
    const err = await pollSourceNow(1).catch((e) => e);
    expect(err.retryAfterSec).toBe(3);
  });
});
