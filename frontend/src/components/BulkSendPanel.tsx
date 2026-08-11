import { useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ApiError, approveMatch, getSnsAccounts, getTemplates } from "../lib/apiClient";
import type { MatchedPost, Source } from "../types/api";
import { Badge } from "./ui/Badge";
import { Button } from "./ui/Button";
import { Card } from "./ui/Card";
import { Field, Select, Textarea } from "./ui/Input";
import { Toast, type ToastTone } from "./ui/Toast";
import { getSourceDisplayName, isWritableSourceType, THREADS_BODY_LIMIT } from "../lib/matchDisplay";

export interface BulkTarget {
  match: MatchedPost;
  source: Source | undefined;
}

// 일괄 발송은 되돌릴 수 없다 — 아래 간격은 그 전제 위의 최소 안전장치다.
// 전송(write) 소스는 실제 외부 게시라 건당 간격을 둔다: 동일 문구 연속 게시는 Threads 의 스팸
// 자동 판정 대상이고, 소스별 rate-limit 준수 의무도 있다(CLAUDE.md 불변식 ④).
const WRITE_SEND_INTERVAL_MS = 2000;
// 수동 복사 소스는 외부 호출 없이 서버 DB 기록만 하므로 짧은 간격이면 충분하다.
const COPY_INTERVAL_MS = 200;

type BulkOutcome = "sent" | "approved" | "failed" | "skipped";

interface BulkResult {
  matchId: number;
  sourceName: string;
  outcome: BulkOutcome;
  message: string;
  url: string | null;
}

const OUTCOME_LABEL: Record<BulkOutcome, string> = {
  sent: "전송 완료",
  approved: "승인(수동 복사)",
  failed: "실패",
  skipped: "미처리",
};

const OUTCOME_TONE = {
  sent: "success",
  approved: "info",
  failed: "danger",
  skipped: "neutral",
} as const;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function describeBulkError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 409) return "이미 처리 중이거나 완료된 매칭";
    if (error.status === 429) return "요청 한도 초과(429)";
    if (error.status === 403) return "권한 또는 CSRF 거부(403)";
    if (error.status === 502 && error.action === "unknown") return "결과 불명 — 자동 조정 대기";
    if (error.status === 502) return "전송 실패";
    return error.detail;
  }
  return "요청 처리 중 오류";
}

// 429/403 은 개별 건의 문제가 아니라 계정·세션 전체에 걸린 제약이라, 남은 건을 계속 두드리면
// 제한만 악화된다(불변식 ④ 예의 있는 수집) — 즉시 중단하고 미처리로 리포트한다.
function isAbortingError(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 429 || error.status === 403);
}

interface BulkSendPanelProps {
  targets: BulkTarget[];
  /** 발송 결과 반영을 위한 매칭 목록 재조회. */
  onSettled: () => void;
  /** 패널 닫기 — 선택 해제까지 호출부에서 처리한다. */
  onClose: () => void;
}

export function BulkSendPanel({ targets, onSettled, onClose }: BulkSendPanelProps) {
  const { data: templates } = useQuery({ queryKey: ["templates"], queryFn: getTemplates });
  const { data: snsAccounts } = useQuery({ queryKey: ["snsAccounts"], queryFn: getSnsAccounts });

  const [templateId, setTemplateId] = useState<number | "">("");
  const [finalBody, setFinalBody] = useState("");
  const [snsAccountId, setSnsAccountId] = useState<number | "">("");
  const [phase, setPhase] = useState<"form" | "confirm" | "running" | "done">("form");
  const [progress, setProgress] = useState(0);
  // 실행 시점의 대상 건수 스냅샷 — 실행 중/직후에 표 선택이 바뀌어도 진행률 분모가 흔들리면 안 된다.
  const [batchTotal, setBatchTotal] = useState(0);
  const [results, setResults] = useState<BulkResult[]>([]);
  const [toast, setToast] = useState<{ message: string; tone: ToastTone } | null>(null);
  // 중단 요청 표시용 — 루프 판단은 ref 가, 화면 안내는 이 상태가 담당한다.
  const [aborting, setAborting] = useState(false);
  // 진행 중 중단 요청 — 루프가 매 건 시작 전에 확인한다(리렌더와 무관해야 해서 ref).
  const abortRef = useRef(false);
  const toastTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  function showToast(message: string, tone: ToastTone) {
    if (toastTimeoutRef.current) clearTimeout(toastTimeoutRef.current);
    setToast({ message, tone });
    toastTimeoutRef.current = setTimeout(() => setToast(null), 3000);
  }

  // 선택이 바뀌면 직전 실행 결과·확인 단계는 그 대상에 대한 것이 아니므로 무효화한다.
  // 입력값(템플릿·문구·계정)은 유지 — 선택을 한 건 더 추가했다고 작성 중인 문구가 날아가면 안 된다.
  const targetSignature = targets.map((t) => t.match.id).join(",");
  const [prevSignature, setPrevSignature] = useState(targetSignature);
  if (prevSignature !== targetSignature) {
    setPrevSignature(targetSignature);
    // 실행 중에는 루프가 잡아둔 스냅샷으로 계속 진행한다 — 화면 상태를 되돌리지 않는다.
    if (phase !== "running") {
      setPhase("form");
      setResults([]);
      setProgress(0);
    }
  }

  const writeTargets = useMemo(
    () => targets.filter((t) => t.source && isWritableSourceType(t.source.type)),
    [targets]
  );
  const copyTargets = targets.length - writeTargets.length;
  // 전송 가능 소스는 현재 threads 뿐이므로(matchDisplay.WRITABLE_SOURCE_TYPES) 상한도 threads 기준.
  const hasWriteTarget = writeTargets.length > 0;
  const overThreadsLimit = hasWriteTarget && finalBody.length > THREADS_BODY_LIMIT;
  const accountMissing = hasWriteTarget && snsAccountId === "";
  const canSubmit = Boolean(finalBody.trim()) && !accountMissing && !overThreadsLimit;

  const writableAccounts = useMemo(
    () => (snsAccounts ?? []).filter((a) => a.platform === "threads"),
    [snsAccounts]
  );

  function handleTemplateChange(value: string) {
    const nextId = value === "" ? "" : Number(value);
    setTemplateId(nextId);
    if (nextId !== "") {
      const template = templates?.find((t) => t.id === nextId);
      if (template) setFinalBody(template.body);
    }
  }

  async function handleCopyBody() {
    try {
      await navigator.clipboard.writeText(finalBody);
      showToast("문구를 복사했습니다.", "success");
    } catch {
      showToast("복사에 실패했습니다.", "danger");
    }
  }

  async function run() {
    // 실행 대상은 시작 시점으로 고정한다 — 처리 중 표에서 선택을 바꿔도 발송 대상은 변하지 않는다.
    const batch = targets;
    abortRef.current = false;
    setAborting(false);
    setPhase("running");
    setProgress(0);
    setBatchTotal(batch.length);
    setResults([]);

    const collected: BulkResult[] = [];
    let aborted = false;

    for (let i = 0; i < batch.length; i += 1) {
      const { match, source } = batch[i];
      const writable = source ? isWritableSourceType(source.type) : false;
      const sourceName = source ? getSourceDisplayName(source) : `#${match.source_id}`;

      if (abortRef.current || aborted) {
        collected.push({
          matchId: match.id,
          sourceName,
          outcome: "skipped",
          message: abortRef.current ? "사용자 중단" : "앞선 오류로 중단",
          url: match.url,
        });
        continue;
      }

      // 첫 건은 지연 없이 시작하고, 이후에는 현재 건의 성격에 맞는 간격을 둔다.
      if (i > 0) await sleep(writable ? WRITE_SEND_INTERVAL_MS : COPY_INTERVAL_MS);
      if (abortRef.current) {
        collected.push({
          matchId: match.id,
          sourceName,
          outcome: "skipped",
          message: "사용자 중단",
          url: match.url,
        });
        continue;
      }

      try {
        const res = await approveMatch(match.id, {
          template_id: templateId === "" ? undefined : templateId,
          final_body: finalBody,
          // 수동 복사 소스에는 계정이 필요 없다 — 계약상 불필요한 값을 보내지 않는다.
          sns_account_id: writable && snsAccountId !== "" ? snsAccountId : undefined,
        });
        collected.push({
          matchId: match.id,
          sourceName,
          outcome: res.action === "sent" ? "sent" : "approved",
          message: res.action === "sent" ? `reply_id: ${res.external_reply_id}` : "클립보드 복사 대상",
          url: match.url,
        });
      } catch (err) {
        collected.push({
          matchId: match.id,
          sourceName,
          outcome: "failed",
          message: describeBulkError(err),
          url: match.url,
        });
        if (isAbortingError(err)) aborted = true;
      }

      setProgress(i + 1);
      setResults([...collected]);
    }

    setResults(collected);
    setPhase("done");
    onSettled();
  }

  const sentCount = results.filter((r) => r.outcome === "sent").length;
  const approvedCount = results.filter((r) => r.outcome === "approved").length;
  const failedCount = results.filter((r) => r.outcome === "failed").length;
  const skippedCount = results.filter((r) => r.outcome === "skipped").length;

  return (
    <Card className="sticky bottom-4 z-10 flex flex-col gap-4 border-brand-500 shadow-lg">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <h2 className="text-sm font-semibold text-gray-900">일괄 발송 — {targets.length}건 선택</h2>
        {hasWriteTarget && <Badge tone="danger">실제 전송 {writeTargets.length}건</Badge>}
        {copyTargets > 0 && <Badge tone="info">수동 복사 {copyTargets}건</Badge>}
        {phase !== "running" && (
          <Button size="sm" variant="ghost" className="ml-auto" onClick={onClose}>
            선택 해제
          </Button>
        )}
      </div>

      {phase === "form" && (
        <>
          <div className="flex flex-wrap gap-4">
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
            {hasWriteTarget && (
              <Field label="SNS 계정 (필수)">
                <Select
                  value={snsAccountId}
                  onChange={(e) => setSnsAccountId(e.target.value === "" ? "" : Number(e.target.value))}
                >
                  <option value="">계정 선택</option>
                  {writableAccounts.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.display_name}
                    </option>
                  ))}
                </Select>
                {accountMissing && (
                  <p className="text-xs text-tone-danger">전송 대상이 있어 계정 선택이 필수입니다.</p>
                )}
              </Field>
            )}
          </div>

          <Field label="답변 문구 (선택한 전 건에 동일 적용)" htmlFor="bulk-final-body">
            <Textarea
              id="bulk-final-body"
              rows={4}
              value={finalBody}
              onChange={(e) => setFinalBody(e.target.value)}
              placeholder="선택한 매칭 전부에 같은 문구로 전송됩니다."
            />
            {hasWriteTarget && (
              <p className={`text-xs ${overThreadsLimit ? "text-tone-danger" : "text-gray-400"}`}>
                {finalBody.length}/{THREADS_BODY_LIMIT}자 (Threads 전송 상한 — 초과 시 전송이 실패합니다)
              </p>
            )}
          </Field>

          {hasWriteTarget && (
            <p className="text-xs text-tone-warning">
              동일 문구를 여러 글에 연속 게시하면 플랫폼이 스팸으로 자동 판정할 수 있습니다. 건별로
              문구를 조정하려면 매칭 상세에서 개별 승인하세요.
            </p>
          )}

          <div>
            <Button disabled={!canSubmit} onClick={() => setPhase("confirm")}>
              일괄 발송 검토
            </Button>
          </div>
        </>
      )}

      {phase === "confirm" && (
        <div className="flex flex-col gap-3">
          <div className="rounded-md border border-tone-danger/40 bg-red-50 p-3 text-sm text-gray-900">
            <p className="font-medium text-tone-danger">이 동작은 되돌릴 수 없습니다.</p>
            <ul className="mt-1 list-disc pl-5 text-sm text-gray-700">
              {hasWriteTarget && (
                <li>
                  <strong>{writeTargets.length}건</strong>이 지금 실제로 게시됩니다(취소·삭제 불가).
                </li>
              )}
              {copyTargets > 0 && (
                <li>{copyTargets}건은 전송 없이 승인 처리되고 문구는 수동 복사용으로 제공됩니다.</li>
              )}
              <li>건당 간격을 두고 순차 처리하며, 진행 중 중단할 수 있습니다.</li>
            </ul>
          </div>
          <div className="rounded-md border border-gray-200 bg-gray-50 p-3">
            <p className="text-xs font-medium text-gray-500">전송될 문구</p>
            <p className="mt-1 whitespace-pre-wrap break-words text-sm text-gray-900">{finalBody}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="danger" onClick={() => void run()}>
              {hasWriteTarget ? `${targets.length}건 발송 실행` : `${targets.length}건 승인 실행`}
            </Button>
            <Button variant="secondary" onClick={() => setPhase("form")}>
              돌아가기
            </Button>
          </div>
        </div>
      )}

      {phase === "running" && (
        <div className="flex flex-col gap-2">
          <p className="text-sm text-gray-700">
            처리 중… {progress}/{batchTotal}건
          </p>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-gray-200">
            <div
              className="h-full bg-brand-600 transition-all"
              style={{ width: `${batchTotal ? (progress / batchTotal) * 100 : 0}%` }}
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="secondary"
              disabled={aborting}
              onClick={() => {
                abortRef.current = true;
                setAborting(true);
              }}
            >
              중단
            </Button>
            {aborting && (
              // 이미 서버로 나간 요청은 되돌릴 수 없다 — 진행 중 1건은 그대로 끝난다는 점을 명시한다.
              <span className="text-xs text-tone-warning">
                중단 요청됨 — 진행 중인 1건까지 완료 후 멈춥니다.
              </span>
            )}
          </div>
        </div>
      )}

      {phase === "done" && (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            {sentCount > 0 && <Badge tone="success">전송 {sentCount}건</Badge>}
            {approvedCount > 0 && <Badge tone="info">승인 {approvedCount}건</Badge>}
            {failedCount > 0 && <Badge tone="danger">실패 {failedCount}건</Badge>}
            {skippedCount > 0 && <Badge tone="neutral">미처리 {skippedCount}건</Badge>}
          </div>

          <ul className="flex max-h-60 flex-col gap-1 overflow-y-auto text-sm">
            {results.map((r) => (
              <li key={r.matchId} className="flex flex-wrap items-center gap-2">
                <Badge tone={OUTCOME_TONE[r.outcome]}>{OUTCOME_LABEL[r.outcome]}</Badge>
                <Link to={`/matches/${r.matchId}`} className="text-brand-600 hover:underline">
                  매칭 #{r.matchId}
                </Link>
                <span className="text-gray-500">{r.sourceName}</span>
                <span className="text-gray-500">{r.message}</span>
                {r.url && (
                  <a
                    href={r.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-gray-400 hover:text-brand-600"
                  >
                    원문 ↗
                  </a>
                )}
              </li>
            ))}
          </ul>

          <div className="flex flex-wrap gap-2">
            {approvedCount > 0 && (
              <Button size="sm" variant="secondary" onClick={() => void handleCopyBody()}>
                문구 복사 (수동 전송용)
              </Button>
            )}
            <Button size="sm" onClick={onClose}>
              완료
            </Button>
          </div>
        </div>
      )}

      {toast && <Toast message={toast.message} tone={toast.tone} />}
    </Card>
  );
}
