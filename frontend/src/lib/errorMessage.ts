import { ApiError } from "./apiClient";

export function describeApiError(error: unknown): string {
  if (error instanceof ApiError) return error.detail || "요청을 처리할 수 없습니다.";
  return "요청 처리 중 오류가 발생했습니다.";
}

// 같은 게시물 중복 답글 차단(R16 — docs/API-SPEC.md §approve). approve/retry 의 409 는
// 상태 충돌(CAS)·이미 전송된 매칭·이 차단이 뒤섞여 있고 서버가 구분 코드를 따로 주지 않아
// 문구로 판정한다. 서버 문구가 바뀌면 판정이 빠지고 일반 409 안내로 퇴화할 뿐이라, 잘못된
// 안내를 만들지는 않는다(문구 자체는 어차피 서버 detail 을 그대로 노출한다).
const DUPLICATE_REPLY_MARK = "이미 답글을 보냈습니다";

/** approve/retry 가 "이 게시물에는 이미 답글을 보냈습니다"(다른 소스로 중복 수집된 글)로 막혔는지. */
export function isDuplicateReplyConflict(error: unknown): boolean {
  return (
    error instanceof ApiError && error.status === 409 && error.detail.includes(DUPLICATE_REPLY_MARK)
  );
}
