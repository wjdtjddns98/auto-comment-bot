import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { connectThreadsOAuth } from "../lib/apiClient";
import { describeApiError } from "../lib/errorMessage";
import type { SnsAccount } from "../types/api";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { THREADS_OAUTH_CALLBACK_PATH, THREADS_OAUTH_DISPLAY_NAME_KEY } from "../lib/threadsOAuth";

type ExchangeState =
  | { phase: "pending" }
  | { phase: "done"; account: SnsAccount }
  | { phase: "failed"; message: string }
  // 코드 없이 들어온 경우(사용자 거부·직접 접근) — 교환을 시도하지 않는다.
  | { phase: "no-code"; reason: string };

/** Threads 동의 화면이 되돌아오는 앱 내 콜백 라우트.
 *
 * `?code=...&state=...` 를 받아 즉시 `POST /api/sns-accounts/threads-oauth` 로 교환한다
 * (docs/API-SPEC.md §threads-oauth). 운영자가 연동 값을 복사해 붙여넣던 수동 단계를 없앤다.
 * 이 경로가 쓰이려면 백엔드 `THREADS_REDIRECT_URI` 가 이 URL 을 가리켜야 하며, 그 전까지는
 * SNS 계정 화면의 붙여넣기 폼이 그대로 동작한다.
 *
 * 교환을 useMutation 이 아니라 로컬 상태로 다루는 이유: 마운트 시 딱 한 번 실행하는
 * 일회성 작업이라 캐시·재시도가 필요 없고, 옵저버 수명주기에 얹으면 결과 반영이
 * 리렌더 타이밍에 묶여 성공한 연동이 "진행 중" 으로 멈춰 보였다.
 */
export default function ThreadsOAuthCallbackPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  // 아래에서 주소창을 즉시 비우므로 최초 렌더의 쿼리만 스냅샷으로 붙잡는다.
  const [handoff] = useState(() => ({
    code: params.get("code")?.trim() ?? "",
    state: params.get("state")?.trim() ?? "",
    denied: params.get("error_description")?.trim() || params.get("error")?.trim() || "",
  }));

  const [state, setState] = useState<ExchangeState>({ phase: "pending" });

  // 인증 코드는 1회용 — StrictMode 이중 실행이나 재렌더로 두 번 교환하면 두 번째가 400 이 되어
  // 성공한 연동을 실패로 보이게 한다. ref 로 최초 1회만 실행한다.
  const startedRef = useRef(false);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;

    // code·state 는 단수명 자격증명 — 주소창·브라우저 히스토리·리퍼러에 남기지 않는다.
    navigate(THREADS_OAUTH_CALLBACK_PATH, { replace: true });

    const displayName = sessionStorage.getItem(THREADS_OAUTH_DISPLAY_NAME_KEY)?.trim() ?? "";
    sessionStorage.removeItem(THREADS_OAUTH_DISPLAY_NAME_KEY);

    if (handoff.denied) {
      setState({ phase: "no-code", reason: handoff.denied });
      return;
    }
    if (!handoff.code || !handoff.state) {
      setState({ phase: "no-code", reason: "" });
      return;
    }

    connectThreadsOAuth({
      code: handoff.code,
      state: handoff.state,
      display_name: displayName || undefined,
    }).then(
      (account) => {
        setState({ phase: "done", account });
        queryClient.invalidateQueries({ queryKey: ["snsAccounts"] });
      },
      (err) => setState({ phase: "failed", message: describeApiError(err) })
    );
    // 최초 마운트 1회만 — 의존성 추가 시 코드 재교환 위험.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const backToAccounts = (label: string) => (
    <Button onClick={() => navigate("/sns-accounts")} className="self-start">
      {label}
    </Button>
  );

  let body;
  if (state.phase === "pending") {
    body = <p className="text-sm text-gray-700">연동을 마무리하는 중…</p>;
  } else if (state.phase === "no-code") {
    body = (
      <>
        <p className="text-sm text-gray-700">
          {state.reason
            ? "연동이 취소되었거나 승인되지 않았습니다. SNS 계정 화면에서 다시 시도해 주세요."
            : "연동 값이 없습니다. SNS 계정 화면의 “Threads로 연결” 버튼으로 다시 시작해 주세요."}
        </p>
        {state.reason && <p className="text-xs text-gray-400">사유: {state.reason}</p>}
        {backToAccounts("SNS 계정 화면으로")}
      </>
    );
  } else if (state.phase === "failed") {
    body = (
      <>
        <p className="text-sm text-tone-danger">{state.message}</p>
        <p className="text-xs text-gray-500">
          연동 값은 일회용이라 그대로 재시도할 수 없습니다. 처음부터 다시 연동해 주세요.
        </p>
        {backToAccounts("SNS 계정 화면으로")}
      </>
    );
  } else {
    body = (
      <>
        <p className="text-sm text-gray-700">
          <strong>{state.account.display_name}</strong> 계정을 연동했습니다. 이제 이 계정으로 답글을
          보낼 수 있습니다 — 전송은 사람이 승인한 건에 한합니다.
        </p>
        {backToAccounts("연동된 계정 보기")}
      </>
    );
  }

  return (
    <section className="mx-auto flex max-w-lg flex-col gap-6">
      <h1 className="text-xl font-semibold text-gray-900">Threads 계정 연동</h1>
      <Card className="flex flex-col gap-4">{body}</Card>
    </section>
  );
}
