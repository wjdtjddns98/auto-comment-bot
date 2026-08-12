/**
 * CSRF 토큰 단일비행(single-flight) 회귀 테스트.
 *
 * apiClient 의 `ensureCsrfToken()` 은 모듈 스코프 `csrfToken`/`csrfPromise` 로
 * "동시에 여러 갈래가 몰려도 `/api/auth/csrf` 발급은 1회" 를 보장한다.
 * 403(토큰 만료) 재시도 경로와 발급 실패 후 복구 경로까지 함께 고정한다.
 *
 * ⚠️ 서버 모델 주의 — 아래 스텁은 재발급마다 T1→T2 로 **다른** 토큰을 준다. 이건
 * 클라이언트 단일비행 로직을 관찰하기 위한 것이고, 실제 백엔드는 그렇지 않다.
 * CSRF 토큰은 Fernet 세션 페이로드 안에 들어 있어(backend/app/auth.py) `/api/auth/csrf`
 * 는 매번 같은 값을 되돌려준다. 토큰이 바뀌는 유일한 경우는 세션 쿠키 자체가 교체될
 * 때(다른 탭에서 재로그인)다. 즉 403 재시도가 실제로 구제하는 상황은 그 한 가지뿐이다.
 *
 * 모듈 스코프 상태가 테스트 간에 새는 걸 막으려고, 매 테스트에서
 * `vi.resetModules()` + 동적 import 로 새 모듈 인스턴스를 받는다.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

interface StubResponse {
  ok: boolean;
  status: number;
  statusText: string;
  json: () => Promise<unknown>;
}

function stubResponse(status: number, body: unknown): StubResponse {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: String(status),
    json: async () => body,
  };
}

function headerToken(init?: RequestInit): string | undefined {
  const headers = init?.headers as Record<string, string> | undefined;
  return headers?.["X-CSRF-Token"];
}

/** apiClient 를 깨끗한 모듈 상태로 다시 불러온다. */
async function freshApiClient() {
  vi.resetModules();
  return import("./apiClient");
}

const originalFetch = globalThis.fetch;

beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("CSRF 단일비행 — 네 갈래 동시 요청", () => {
  it("콜드 스타트: 4갈래 동시 mutation 이어도 발급은 1회뿐이다", async () => {
    let issued = 0;
    const mutationTokens: (string | undefined)[] = [];

    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/auth/csrf") {
        issued += 1;
        return stubResponse(200, { csrf_token: `T${issued}` }) as unknown as Response;
      }
      mutationTokens.push(headerToken(init));
      // `/api/matches/{id}/ignore` 는 204 를 준다(docs/API-SPEC.md).
      return stubResponse(204, null) as unknown as Response;
    });

    const api = await freshApiClient();
    await Promise.all([
      api.ignoreMatch(1),
      api.ignoreMatch(2),
      api.ignoreMatch(3),
      api.ignoreMatch(4),
    ]);

    expect(issued).toBe(1);
    // 네 갈래 모두 같은 토큰을 공유해야 한다.
    expect(mutationTokens).toEqual(["T1", "T1", "T1", "T1"]);
  });

  it("4갈래가 동시에 403 을 받아도 재발급은 1회, 각 요청은 정확히 1번만 재시도한다", async () => {
    let issued = 0;
    const mutationTokens: (string | undefined)[] = [];

    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/auth/csrf") {
        issued += 1;
        return stubResponse(200, { csrf_token: `T${issued}` }) as unknown as Response;
      }
      const token = headerToken(init);
      mutationTokens.push(token);
      // 구토큰(T1)이면 만료 403, 재발급된 토큰이면 통과.
      if (token === "T1") {
        return stubResponse(403, { detail: "CSRF 토큰이 유효하지 않습니다." }) as unknown as Response;
      }
      // `/api/matches/{id}/ignore` 는 204 를 준다(docs/API-SPEC.md).
      return stubResponse(204, null) as unknown as Response;
    });

    const api = await freshApiClient();
    await Promise.all([
      api.ignoreMatch(1),
      api.ignoreMatch(2),
      api.ignoreMatch(3),
      api.ignoreMatch(4),
    ]);

    // 최초 발급 1회 + 만료 후 재발급 1회 = 2회. (갈래마다 재발급하면 5회가 된다)
    expect(issued).toBe(2);
    // 4갈래 × (최초 시도 + 재시도 1회) = 8번, 앞 4번은 구토큰·뒤 4번은 신토큰.
    expect(mutationTokens).toEqual(["T1", "T1", "T1", "T1", "T2", "T2", "T2", "T2"]);
  });

  it("재시도한 요청이 또 403 이면 더 재시도하지 않고 ApiError 로 전파한다(무한루프 방지)", async () => {
    let issued = 0;
    let mutationCalls = 0;

    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/auth/csrf") {
        issued += 1;
        return stubResponse(200, { csrf_token: `T${issued}` }) as unknown as Response;
      }
      mutationCalls += 1;
      return stubResponse(403, { detail: "CSRF 토큰이 유효하지 않습니다." }) as unknown as Response;
    });

    const api = await freshApiClient();
    await expect(api.ignoreMatch(1)).rejects.toMatchObject({
      name: "ApiError",
      status: 403,
      detail: "CSRF 토큰이 유효하지 않습니다.",
    });

    expect(mutationCalls).toBe(2); // 최초 + 재시도 1회에서 멈춘다
    expect(issued).toBe(2);
  });
});

describe("발급 실패 복구 경로", () => {
  it("발급이 실패하면 4갈래 모두 실패하지만, 다음 시도에서 재발급에 성공한다", async () => {
    let issued = 0;
    let failIssue = true;

    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/auth/csrf") {
        issued += 1;
        if (failIssue) {
          return stubResponse(500, { detail: "서버 오류" }) as unknown as Response;
        }
        return stubResponse(200, { csrf_token: `T${issued}` }) as unknown as Response;
      }
      // `/api/matches/{id}/ignore` 는 204 를 준다(docs/API-SPEC.md).
      return stubResponse(204, null) as unknown as Response;
    });

    const api = await freshApiClient();

    const settled = await Promise.allSettled([
      api.ignoreMatch(1),
      api.ignoreMatch(2),
      api.ignoreMatch(3),
      api.ignoreMatch(4),
    ]);

    // 네 갈래 전부 거절되고, 발급 시도는 (단일비행이므로) 1회뿐이어야 한다.
    expect(settled.map((r) => r.status)).toEqual([
      "rejected",
      "rejected",
      "rejected",
      "rejected",
    ]);
    for (const result of settled) {
      expect(result.status === "rejected" && result.reason).toMatchObject({
        name: "ApiError",
        status: 500,
        detail: "CSRF 토큰 발급 실패",
      });
    }
    expect(issued).toBe(1);

    // 복구: 실패한 promise 가 캐시에 남아 잠기지 않고 다음 시도에서 다시 발급된다.
    failIssue = false;
    await expect(api.ignoreMatch(5)).resolves.toBeUndefined();
    expect(issued).toBe(2);
  });
});

describe("403 이 시차를 두고 도착하는 경우", () => {
  it("재발급 완료 뒤에 뒤늦은 403 이 와도 갓 받은 토큰을 버리지 않는다", async () => {
    let issued = 0;
    let mutationSeq = 0;
    // 시차를 wall-clock 타이머로 만들면 CI 부하에 따라 순서가 뒤집혀 flaky 해진다.
    // 뒤늦은 갈래의 403 을 명시적 게이트로 붙잡아 순서를 결정론적으로 고정한다.
    let releaseLate!: () => void;
    const lateGate = new Promise<void>((resolve) => {
      releaseLate = resolve;
    });

    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/auth/csrf") {
        issued += 1;
        return stubResponse(200, { csrf_token: `T${issued}` }) as unknown as Response;
      }
      const token = headerToken(init);
      if (token === "T1") {
        // 첫 갈래는 즉시 403, 두 번째 갈래는 재발급이 끝난 뒤에야 403 을 돌려준다.
        mutationSeq += 1;
        if (mutationSeq > 1) await lateGate;
        return stubResponse(403, { detail: "CSRF 토큰이 유효하지 않습니다." }) as unknown as Response;
      }
      // `/api/matches/{id}/ignore` 는 204 를 준다(docs/API-SPEC.md).
      return stubResponse(204, null) as unknown as Response;
    });

    const api = await freshApiClient();
    // 두 갈래가 같은 T1 을 공유한 채 출발한다(단일비행 성립).
    const first = api.ignoreMatch(1);
    const late = api.ignoreMatch(2);
    await first; // 첫 갈래가 403 → 재발급(T2) → 재시도까지 끝낸다
    releaseLate(); // 그 뒤에야 뒤늦은 403 이 도착한다
    await late;

    // 뒤늦은 갈래는 자기가 보낸 T1 이 이미 캐시에서 밀려난 걸 보고 무효화를 건너뛴다.
    // 최초 1 + 첫 갈래 재발급 1 = 2. (조건 없이 비우면 3회가 된다 — 회귀 감지 지점)
    expect(issued).toBe(2);
  });
});
