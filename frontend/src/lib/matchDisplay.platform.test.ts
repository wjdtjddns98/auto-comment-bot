/**
 * 계정 플랫폼 ↔ 소스 종류 대응 — 백엔드 `_PLATFORM_FOR_SOURCE`(app/api/matches.py)의 거울.
 *
 * 두 enum 은 네이버 표기가 다르다(계정 `naver`, 소스 `naver_cafe`). 값만 보면 오타처럼 보여서
 * "같게 맞추자" 는 수정이 반복되기 쉬운데, 그러면 계정 등록이 422 로 돌아간다. 그 회귀를 막는다.
 */
import { describe, expect, it } from "vitest";
import { platformForSourceType } from "./matchDisplay";

describe("platformForSourceType", () => {
  it("네이버는 소스 종류와 계정 플랫폼의 값이 다르다", () => {
    expect(platformForSourceType("naver_cafe")).toBe("naver");
  });

  it("threads·community 는 값이 같다 — 그래서 직접 비교가 우연히 동작했다", () => {
    expect(platformForSourceType("threads")).toBe("threads");
    expect(platformForSourceType("community")).toBe("community");
  });

  it("dcinside 는 대응하는 계정 플랫폼이 없다 — 계정을 붙이면 승인이 422 다", () => {
    // 백엔드 Platform enum 에 dcinside 값이 없고 `_PLATFORM_FOR_SOURCE` 에도 항목이 없다
    // (PR #124). "빠진 것 같으니 채우자" 는 수정이 들어오면 승인 경로가 조용히 깨진다.
    expect(platformForSourceType("dcinside")).toBeNull();
  });
});
