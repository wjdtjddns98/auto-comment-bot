import { useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  approveMatch,
  getMatch,
  getSnsAccounts,
  getSources,
  getTemplates,
  ignoreMatch,
  retryMatch,
} from "../lib/apiClient";
import { describeApiError, isDuplicateReplyConflict } from "../lib/errorMessage";
import type { ApproveMatchResponse } from "../types/api";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Field, Select, Textarea } from "../components/ui/Input";
import { Toast, type ToastTone } from "../components/ui/Toast";
import {
  formatDateTime,
  getSourceDisplayName,
  isWritableSourceType,
  RECONCILED_UNPUBLISHED_ERROR,
  REPLY_ACTION_LABEL,
  STATUS_LABEL,
  STATUS_TONE,
  THREADS_BODY_LIMIT,
  toPlainText,
} from "../lib/matchDisplay";

// approve/retry 의 409 는 한 종류가 아니다 — 상태 충돌(CAS)·이미 전송된 매칭·**같은 게시물
// 중복 답글 차단(R16, 다른 소스로 중복 수집된 글)** 이 모두 409 다. 상태코드로 뭉뚱그려
// "이미 처리 중이거나 완료된 매칭" 이라 안내하면 R16 차단이 상태 문제로 오인돼, 사용자가
// 새로고침해도 status 가 `new` 인 채라 계속 재시도하게 된다. 그래서 서버 문구를 그대로 쓴다
// (describeApiError). 아래 배너는 R16 차단일 때만 붙는 추가 안내다.

export default function MatchDetailPage() {
  const { id: idParam } = useParams();
  const id = Number(idParam);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const matchQuery = useQuery({
    queryKey: ["match", id],
    queryFn: () => getMatch(id),
    enabled: Number.isFinite(id),
  });
  const sourcesQuery = useQuery({ queryKey: ["sources"], queryFn: getSources });
  const sources = sourcesQuery.data;
  const { data: templates } = useQuery({ queryKey: ["templates"], queryFn: getTemplates });
  const { data: snsAccounts } = useQuery({ queryKey: ["snsAccounts"], queryFn: getSnsAccounts });

  const [templateId, setTemplateId] = useState<number | "">("");
  const [finalBody, setFinalBody] = useState("");
  const [snsAccountId, setSnsAccountId] = useState<number | "">("");
  const [lastResult, setLastResult] = useState<ApproveMatchResponse | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  // R16 중복 답글 차단 여부 — 문구만으로는 "무시로 종료하면 된다"는 다음 행동이 안 보인다.
  const [duplicateBlocked, setDuplicateBlocked] = useState(false);
  const [copyDone, setCopyDone] = useState(false);
  const [toast, setToast] = useState<{ message: string; tone: ToastTone } | null>(null);
  const toastTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  function showToast(message: string, tone: ToastTone) {
    if (toastTimeoutRef.current) clearTimeout(toastTimeoutRef.current);
    setToast({ message, tone });
    toastTimeoutRef.current = setTimeout(() => setToast(null), 3000);
  }

  // 승인 응답이 온 시점엔 클릭의 user-activation 이 만료돼 있을 수 있어(비동기 approve 요청 이후라서)
  // 일부 브라우저(Safari 등)에서 clipboard 쓰기가 조용히 거부될 수 있다 — 실패해도 아래 수동 복사
  // 버튼은 그대로 남겨두고 토스트로만 알린다.
  async function autoCopy(body: string) {
    try {
      await navigator.clipboard.writeText(body);
      setCopyDone(true);
      showToast("클립보드에 자동으로 복사했습니다.", "success");
    } catch {
      showToast("자동 복사에 실패했습니다 — 아래 버튼으로 직접 복사해주세요.", "danger");
    }
  }

  const source = useMemo(
    () => sources?.find((s) => s.id === matchQuery.data?.source_id),
    [sources, matchQuery.data?.source_id]
  );
  const writable = source ? isWritableSourceType(source.type) : false;
  // 소스 목록을 못 받으면 source 가 undefined 라 전송 가능한 소스도 "전송 미지원"으로 보인다 —
  // reviewer 가 /api/sources 에서 403 을 받던 동안(이슈 #104) threads 매칭이 실제로 그렇게 보였다.
  // 백엔드가 읽기를 열어 그 원인은 사라졌지만, 5xx·네트워크 실패에도 같은 오표기가 나므로
  // "확인 못 함"과 "미지원"을 화면에서 구분한다.
  const sourceState = sourcesQuery.isPending ? "loading" : source ? "ready" : "unknown";
  const isThreads = source?.type === "threads";
  const overThreadsLimit = isThreads && finalBody.length > THREADS_BODY_LIMIT;

  function invalidate() {
    queryClient.invalidateQueries({ queryKey: ["match", id] });
    queryClient.invalidateQueries({ queryKey: ["matches"] });
  }

  const approveMutation = useMutation({
    mutationFn: () =>
      approveMatch(id, {
        template_id: templateId === "" ? undefined : templateId,
        final_body: finalBody,
        sns_account_id: snsAccountId === "" ? undefined : snsAccountId,
      }),
    onSuccess: (res) => {
      setLastResult(res);
      setActionError(null);
      setDuplicateBlocked(false);
      setCopyDone(false);
      if (res.action === "approved") void autoCopy(res.clipboard_body);
    },
    onError: (err) => {
      setActionError(describeApiError(err));
      setDuplicateBlocked(isDuplicateReplyConflict(err));
    },
    // 409/502 실패 시에도 서버가 상태를 되돌리거나 reply_actions 를 기록하므로 재조회가 필요하다.
    onSettled: () => invalidate(),
  });

  const ignoreMutation = useMutation({
    mutationFn: () => ignoreMatch(id),
    onSuccess: () => {
      invalidate();
      navigate("/");
    },
  });

  const retryMutation = useMutation({
    mutationFn: () =>
      retryMatch(id, {
        template_id: templateId === "" ? undefined : templateId,
        final_body: finalBody,
        sns_account_id: snsAccountId === "" ? undefined : snsAccountId,
      }),
    onSuccess: (res) => {
      setLastResult(res);
      setActionError(null);
      setDuplicateBlocked(false);
      setCopyDone(false);
      if (res.action === "approved") void autoCopy(res.clipboard_body);
    },
    onError: (err) => {
      setActionError(describeApiError(err));
      setDuplicateBlocked(isDuplicateReplyConflict(err));
    },
    onSettled: () => invalidate(),
  });

  function handleTemplateChange(value: string) {
    const nextId = value === "" ? "" : Number(value);
    setTemplateId(nextId);
    if (nextId !== "") {
      const template = templates?.find((t) => t.id === nextId);
      if (template) setFinalBody(template.body);
    }
  }

  async function handleCopy() {
    if (lastResult?.action !== "approved") return;
    try {
      await navigator.clipboard.writeText(lastResult.clipboard_body);
      setCopyDone(true);
      showToast("복사했습니다.", "success");
    } catch {
      showToast("복사에 실패했습니다.", "danger");
    }
  }

  if (matchQuery.isLoading) return <p className="text-sm text-gray-500">불러오는 중…</p>;
  if (matchQuery.isError || !matchQuery.data) {
    return <p className="text-sm text-tone-danger">매칭을 찾을 수 없습니다.</p>;
  }

  const match = matchQuery.data;
  const actionable = match.status === "new" || match.status === "reviewing";
  const verifyPending = match.status === "verify_pending";
  const canRetry = match.status === "reviewing" && match.reply_actions.some((a) => a.action === "failed");
  const unpublishedHint =
    canRetry && match.reply_actions.some((a) => a.action === "failed" && a.error === RECONCILED_UNPUBLISHED_ERROR);
  const writableAccountMissing = writable && snsAccountId === "";

  return (
    <section className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <Link to="/" className="text-sm text-brand-600 hover:underline">
            ← 대시보드
          </Link>
          <h1 className="mt-1 text-xl font-semibold text-gray-900">매칭 상세 #{match.id}</h1>
        </div>
        <Badge tone={STATUS_TONE[match.status]}>{STATUS_LABEL[match.status]}</Badge>
      </div>

      <Card className="flex flex-col gap-2">
        <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm text-gray-500">
          <span className="inline-flex flex-wrap items-center gap-1.5">
            소스: {source ? getSourceDisplayName(source) : `#${match.source_id}`}
            {/* 목록(#73)과 동일하게 비활성 소스는 배지로 표시 — 화면 간 상태 표시 일관성. */}
            {source && !source.enabled && <Badge tone="neutral">비활성</Badge>}
          </span>
          <span>작성자: {match.author ?? "-"}</span>
          <span>게시일시: {formatDateTime(match.published_at)}</span>
          <span>매칭일시: {formatDateTime(match.matched_at)}</span>
        </div>
        {/* RSS 본문은 피드 원문 HTML — 평문 정규화 + break-words 로 긴 URL 이 카드를 밀지 않게 한다. */}
        <p className="whitespace-pre-wrap break-words text-gray-900">{toPlainText(match.content)}</p>
        {match.url ? (
          <a href={match.url} target="_blank" rel="noreferrer" className="text-sm text-brand-600 hover:underline">
            원문 보기 ↗
          </a>
        ) : (
          <span className="text-sm text-gray-400">원문 링크 없음</span>
        )}
      </Card>

      {actionable ? (
        <Card className="flex flex-col gap-4">
          <Field label="템플릿">
            <Select value={templateId} onChange={(e) => handleTemplateChange(e.target.value)}>
              <option value="">선택 안 함</option>
              {(templates ?? [])
                .filter((t) => t.enabled)
                .map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
            </Select>
          </Field>

          <Field label="답변 문구" htmlFor="final-body">
            <Textarea
              id="final-body"
              rows={4}
              value={finalBody}
              onChange={(e) => setFinalBody(e.target.value)}
              placeholder="전송할 답변을 입력하거나 템플릿을 선택하세요."
            />
            {isThreads && (
              <p className={`text-xs ${overThreadsLimit ? "text-tone-danger" : "text-gray-400"}`}>
                {finalBody.length}/{THREADS_BODY_LIMIT}자 (Threads 전송 상한 — 초과 시 전송이 실패합니다)
              </p>
            )}
          </Field>

          {writable ? (
            <Field label="SNS 계정 (필수)">
              <Select
                value={snsAccountId}
                onChange={(e) => setSnsAccountId(e.target.value === "" ? "" : Number(e.target.value))}
              >
                <option value="">계정 선택</option>
                {(snsAccounts ?? [])
                  .filter((a) => a.platform === source?.type)
                  .map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.display_name}
                    </option>
                  ))}
              </Select>
              {writableAccountMissing && (
                <p className="text-xs text-tone-danger">전송 가능한 소스는 SNS 계정 선택이 필수입니다.</p>
              )}
            </Field>
          ) : sourceState === "ready" ? (
            <p className="text-xs text-gray-500">
              이 소스는 자동 전송을 지원하지 않습니다. 승인 시 답변 문구를 클립보드 복사용으로 제공합니다.
            </p>
          ) : sourceState === "unknown" ? (
            <p className="text-xs text-tone-warning">
              소스 정보를 불러오지 못해 이 매칭의 전송 지원 여부를 확인할 수 없습니다. 새로고침 후에도
              같으면 승인하지 말고 관리자에게 문의하세요.
            </p>
          ) : null}

          <div className="flex items-center gap-2">
            <Button
              onClick={() => approveMutation.mutate()}
              disabled={!finalBody.trim() || writableAccountMissing || overThreadsLimit || approveMutation.isPending}
            >
              {approveMutation.isPending ? "처리 중…" : "승인"}
            </Button>
            {canRetry && (
              <Button
                variant="secondary"
                onClick={() => retryMutation.mutate()}
                disabled={writableAccountMissing || overThreadsLimit || retryMutation.isPending}
              >
                재시도
              </Button>
            )}
            <Button variant="secondary" onClick={() => ignoreMutation.mutate()} disabled={ignoreMutation.isPending}>
              무시
            </Button>
          </div>

          {unpublishedHint && (
            <p className="text-xs text-tone-warning">
              조정 결과 미게시로 판정되었습니다 — 재시도 전 대상 글에서 직접 게시 여부를 확인해 주세요.
            </p>
          )}

          {actionError && <p className="text-sm text-tone-danger">{actionError}</p>}
          {/* 서버 문구는 이유만 말한다 — 이 매칭이 계속 `new` 로 남아 재시도를 유도하므로
              다음 행동(무시로 종료)까지 붙여준다. */}
          {duplicateBlocked && (
            <p className="text-xs text-tone-warning">
              같은 글이 다른 소스로도 수집돼 이미 답글이 나갔습니다. 이 매칭은 승인·재시도가 계속
              막히니 <strong>무시</strong>로 종료하세요. 답글은 원문 링크에서 확인할 수 있습니다.
            </p>
          )}
        </Card>
      ) : verifyPending ? (
        <Card className="flex flex-col gap-3">
          <p className="text-sm text-gray-500">
            전송 결과를 확인할 수 없어 자동 조정 대기 중입니다. 조정이 끝나면 답변완료 또는 검토중으로
            자동 전환됩니다. 조정이 오래 걸리면 무시로 종료할 수 있습니다.
          </p>
          <div>
            <Button variant="secondary" onClick={() => ignoreMutation.mutate()} disabled={ignoreMutation.isPending}>
              무시
            </Button>
          </div>
          {actionError && <p className="text-sm text-tone-danger">{actionError}</p>}
        </Card>
      ) : (
        <Card className="text-sm text-gray-500">이미 처리 완료된 매칭입니다 ({STATUS_LABEL[match.status]}).</Card>
      )}

      {/* 승인/재시도 직후 서버 상태가 즉시 replied 등으로 바뀌어도(재조회로 actionable 카드가 사라져도)
          결과 안내·복사 버튼은 별도로 계속 보여야 한다 — 위 분기와 무관하게 렌더링. */}
      {lastResult?.action === "sent" && (
        <Card className="text-sm text-tone-success">전송 완료 — external_reply_id: {lastResult.external_reply_id}</Card>
      )}
      {lastResult?.action === "approved" && (
        <Card className="flex items-center gap-2 text-sm text-tone-success">
          <span>승인 완료 — 아래 문구를 복사해 직접 전송하세요.</span>
          <Button size="sm" variant="secondary" onClick={handleCopy}>
            {copyDone ? "복사됨" : "클립보드 복사"}
          </Button>
        </Card>
      )}

      <Card className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold text-gray-900">처리 이력</h2>
        {match.reply_actions.length === 0 ? (
          <p className="text-sm text-gray-400">이력이 없습니다.</p>
        ) : (
          <ul className="flex flex-col gap-1 text-sm text-gray-700">
            {match.reply_actions.map((action) => (
              <li key={action.id} className="flex flex-wrap gap-2">
                <span className="font-medium">{REPLY_ACTION_LABEL[action.action] ?? action.action}</span>
                <span className="text-gray-500">{formatDateTime(action.created_at)}</span>
                {action.external_reply_id && (
                  <span className="text-gray-500">reply_id: {action.external_reply_id}</span>
                )}
                {action.error && <span className="text-tone-danger">{action.error}</span>}
              </li>
            ))}
          </ul>
        )}
        {/* 전송중(sending) 전이 시각 — approve/retry 의 CAS 클레임이 찍는 값(#101 요청 2-a).
            reply_actions 행으로는 남지 않아 이력만 봐선 "언제 전송을 걸었는지"가 안 보인다.
            클레임이 풀린 뒤에도 마지막 시도 값이 남으므로 현재 상태(status)와 함께 읽어야 한다. */}
        {match.sending_claimed_at && (
          <p className="text-xs text-gray-500">
            전송중 전이: {formatDateTime(match.sending_claimed_at)}
            {match.status === "sending" ? " (처리 중)" : " (마지막 시도)"}
          </p>
        )}
      </Card>

      {toast && <Toast message={toast.message} tone={toast.tone} />}
    </section>
  );
}
