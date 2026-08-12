import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  ApiError,
  approveMatch,
  getSnsAccounts,
  getTemplates,
  renderTemplate,
} from "../lib/apiClient";
import { isDuplicateReplyConflict } from "../lib/errorMessage";
import type { MatchedPost, RenderTemplateItem, Source } from "../types/api";
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
// 전송(write) 소스는 실제 외부 게시라 건당 간격을 둔다: 소스별 rate-limit 준수 의무가 있고
// (CLAUDE.md 불변식 ④), 짧은 시간에 몰아 보내는 것 자체가 스팸 신호다.
const WRITE_SEND_INTERVAL_MS = 2000;
// 수동 복사 소스는 외부 호출 없이 서버 DB 기록만 하므로 짧은 간격이면 충분하다.
const COPY_INTERVAL_MS = 200;

type BulkOutcome = "sent" | "approved" | "duplicate" | "failed" | "skipped" | "excluded";

interface BulkResult {
  matchId: number;
  sourceName: string;
  outcome: BulkOutcome;
  message: string;
  url: string | null;
  /** 승인(수동 복사) 건에 붙는 실제 문구 — 건별 렌더에서는 건마다 다르므로 결과에 담아둔다. */
  body: string | null;
}

const OUTCOME_LABEL: Record<BulkOutcome, string> = {
  sent: "전송 완료",
  approved: "승인(수동 복사)",
  duplicate: "이미 답한 글",
  failed: "실패",
  skipped: "미처리",
  excluded: "제외",
};

const OUTCOME_TONE = {
  sent: "success",
  approved: "info",
  duplicate: "warning",
  failed: "danger",
  skipped: "neutral",
  excluded: "warning",
} as const;

/** 발송 대상 1건 — 확정된 문구를 가졌거나, 제외 사유를 가졌거나 둘 중 하나다. */
interface ResolvedTarget {
  target: BulkTarget;
  body: string | null;
  excludeReason: string | null;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function describeBulkError(error: unknown): string {
  if (error instanceof ApiError) {
    // 409 는 상태 충돌·이미 전송됨·**같은 게시물 중복 답글 차단(R16)** 이 섞여 있다. 한 문구로
    // 뭉치면 키워드가 겹치는 소스에서 가장 잘 터지는 R16 이 "상태 문제" 로 오인된다 — 서버가
    // 이유를 문구에 담아 주므로 그대로 노출한다.
    if (error.status === 429) return "요청 한도 초과(429)";
    if (error.status === 403) return "권한 또는 CSRF 거부(403)";
    if (error.status === 502 && error.action === "unknown") return "결과 불명 — 자동 조정 대기";
    if (error.status === 502) return "전송 실패";
    return error.detail || "요청을 처리할 수 없습니다";
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
  // 렌더 결과를 버리고 사람이 직접 쓴 공통 문구를 쓰는 모드. 템플릿을 다시 고르면 해제된다.
  const [manualBody, setManualBody] = useState(false);
  const [rendered, setRendered] = useState<RenderTemplateItem[] | null>(null);
  const [renderError, setRenderError] = useState<string | null>(null);
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
  const accountMissing = hasWriteTarget && snsAccountId === "";

  const writableAccounts = useMemo(
    () => (snsAccounts ?? []).filter((a) => a.platform === "threads"),
    [snsAccounts]
  );

  const renderMutation = useMutation({
    mutationFn: (ids: number[]) =>
      renderTemplate({ template_id: templateId as number, match_ids: ids }),
    onSuccess: (res) => {
      setRendered(res.items);
      setRenderError(null);
    },
    onError: (err) => {
      setRendered(null);
      setRenderError(describeBulkError(err));
    },
  });

  // 서버가 건마다 변형을 독립적으로 뽑아 주므로(POST /api/matches/render-template) 같은 템플릿으로도
  // 문구가 갈린다 — N건 동일 문구가 중복 콘텐츠로 스팸 판정되는 것을 줄이는 것이 목적이다.
  // 템플릿이나 대상이 바뀌면 다시 요청한다. 직접 편집 모드에서는 사람이 쓴 문구가 우선이라 건너뛴다.
  const renderRef = useRef(renderMutation.mutate);
  renderRef.current = renderMutation.mutate;
  useEffect(() => {
    if (templateId === "" || manualBody) {
      setRendered(null);
      setRenderError(null);
      return;
    }
    const ids = targetSignature ? targetSignature.split(",").map(Number) : [];
    if (ids.length > 0) renderRef.current(ids);
  }, [templateId, manualBody, targetSignature]);

  const renderMode = templateId !== "" && !manualBody;
  const renderedMap = useMemo(
    () => new Map((rendered ?? []).map((item) => [item.match_id, item])),
    [rendered]
  );

  // 문구 확정 + 제외 판정. 제외는 조용히 넘기지 않고 사유와 함께 화면·결과에 남긴다 —
  // 렌더 오류(오타 변수 등)를 빈 문구로 게시하는 것이 훨씬 나쁘다.
  const resolved: ResolvedTarget[] = useMemo(() => {
    return targets.map((target) => {
      const writable = target.source ? isWritableSourceType(target.source.type) : false;
      let body: string | null = null;
      let excludeReason: string | null = null;

      if (renderMode) {
        const item = renderedMap.get(target.match.id);
        if (!item) excludeReason = "미리보기 없음 — 문구를 다시 만들어 주세요";
        else if (item.error) excludeReason = item.error;
        else if (!item.body?.trim()) excludeReason = "렌더 결과가 비어 있습니다";
        else body = item.body;
      } else if (finalBody.trim()) {
        body = finalBody;
      } else {
        excludeReason = "답변 문구가 비어 있습니다";
      }

      // Threads 상한은 approve 가 검사하지만, 초과가 확실한 건을 보내 502 를 만들 이유가 없다.
      if (body && writable && body.length > THREADS_BODY_LIMIT) {
        excludeReason = `Threads 상한 초과(${body.length}/${THREADS_BODY_LIMIT}자)`;
        body = null;
      }
      return { target, body, excludeReason };
    });
  }, [targets, renderMode, renderedMap, finalBody]);

  const eligible = resolved.filter((r) => r.body !== null);
  const excluded = resolved.filter((r) => r.body === null);
  // 변형이 실제로 갈렸는지 — 갈리지 않으면 렌더를 써도 스팸 완화 효과가 없다.
  const distinctBodies = new Set(eligible.map((r) => r.body)).size;
  const canSubmit =
    eligible.length > 0 && !accountMissing && !(renderMode && renderMutation.isPending);

  function handleTemplateChange(value: string) {
    const nextId = value === "" ? "" : Number(value);
    setTemplateId(nextId);
    // 템플릿을 새로 고르는 것은 "서버 렌더를 쓰겠다"는 뜻이라 직접 편집 모드를 해제한다.
    setManualBody(false);
  }

  // 렌더 결과를 시드로 삼아 공통 문구를 사람이 직접 다듬는 경로(계약상 수정은 허용된다).
  function handleManualEdit() {
    const seed = eligible[0]?.body ?? "";
    setFinalBody(seed);
    setManualBody(true);
  }

  async function handleCopyBody(body: string) {
    try {
      await navigator.clipboard.writeText(body);
      showToast("문구를 복사했습니다.", "success");
    } catch {
      showToast("복사에 실패했습니다.", "danger");
    }
  }

  async function run() {
    // 실행 대상은 시작 시점으로 고정한다 — 처리 중 표에서 선택을 바꿔도 발송 대상은 변하지 않는다.
    const batch = eligible;
    const skippedUpfront = excluded.map((r) => ({
      matchId: r.target.match.id,
      sourceName: r.target.source ? getSourceDisplayName(r.target.source) : `#${r.target.match.source_id}`,
      outcome: "excluded" as const,
      message: r.excludeReason ?? "제외됨",
      url: r.target.match.url,
      body: null,
    }));
    abortRef.current = false;
    setAborting(false);
    setPhase("running");
    setProgress(0);
    setBatchTotal(batch.length);
    setResults(skippedUpfront);

    const collected: BulkResult[] = [...skippedUpfront];
    let aborted = false;

    for (let i = 0; i < batch.length; i += 1) {
      const { target, body } = batch[i];
      const { match, source } = target;
      const writable = source ? isWritableSourceType(source.type) : false;
      const sourceName = source ? getSourceDisplayName(source) : `#${match.source_id}`;

      if (abortRef.current || aborted) {
        collected.push({
          matchId: match.id,
          sourceName,
          outcome: "skipped",
          message: abortRef.current ? "사용자 중단" : "앞선 오류로 중단",
          url: match.url,
          body: null,
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
          body: null,
        });
        continue;
      }

      try {
        const res = await approveMatch(match.id, {
          template_id: templateId === "" ? undefined : templateId,
          final_body: body as string,
          // 수동 복사 소스에는 계정이 필요 없다 — 계약상 불필요한 값을 보내지 않는다.
          sns_account_id: writable && snsAccountId !== "" ? snsAccountId : undefined,
        });
        collected.push({
          matchId: match.id,
          sourceName,
          outcome: res.action === "sent" ? "sent" : "approved",
          message: res.action === "sent" ? `reply_id: ${res.external_reply_id}` : "수동 복사 대상",
          url: match.url,
          body: res.action === "approved" ? res.clipboard_body : null,
        });
      } catch (err) {
        collected.push({
          matchId: match.id,
          sourceName,
          // 중복 답글 차단(R16)은 "보내지 않는 것이 정답" 인 결과라 실패와 섞지 않는다 —
          // 실패로 세면 운영자가 원인을 찾아 재시도할 대상으로 오해한다.
          outcome: isDuplicateReplyConflict(err) ? "duplicate" : "failed",
          message: describeBulkError(err),
          url: match.url,
          body: null,
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
  const duplicateCount = results.filter((r) => r.outcome === "duplicate").length;
  const failedCount = results.filter((r) => r.outcome === "failed").length;
  const skippedCount = results.filter((r) => r.outcome === "skipped").length;
  const excludedCount = results.filter((r) => r.outcome === "excluded").length;

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
                <option value="">선택 안 함 (직접 입력)</option>
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

          {renderMode ? (
            <div className="flex flex-col gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-gray-700">건별 문구</span>
                {renderMutation.isPending && <span className="text-xs text-gray-500">만드는 중…</span>}
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={renderMutation.isPending}
                  onClick={() => renderMutation.mutate(targets.map((t) => t.match.id))}
                >
                  문구 다시 뽑기
                </Button>
                <Button size="sm" variant="ghost" onClick={handleManualEdit}>
                  직접 편집
                </Button>
              </div>
              <p className="text-xs text-gray-500">
                서버가 <code>{"{{a|b|c}}"}</code> 변형을 건마다 따로 뽑고{" "}
                <code>{"{{author}}"}</code>·<code>{"{{keyword}}"}</code>·<code>{"{{url}}"}</code> 를
                그 글의 값으로 채웁니다. 아래 미리보기가 실제로 전송될 문구입니다.
              </p>
              {renderError && <p className="text-sm text-tone-danger">{renderError}</p>}
              {eligible.length > 1 && distinctBodies === 1 && (
                <p className="text-xs text-tone-warning">
                  {eligible.length}건이 모두 같은 문구입니다 — 이 템플릿에 변형이 없습니다. 템플릿에{" "}
                  <code>{"{{안녕하세요|반갑습니다}}"}</code> 같은 변형을 넣으면 건마다 갈립니다.
                </p>
              )}
            </div>
          ) : (
            <Field label="답변 문구 (선택한 전 건에 동일 적용)" htmlFor="bulk-final-body">
              <Textarea
                id="bulk-final-body"
                rows={4}
                value={finalBody}
                onChange={(e) => setFinalBody(e.target.value)}
                placeholder="선택한 매칭 전부에 같은 문구로 전송됩니다."
              />
              {hasWriteTarget && (
                <p
                  className={`text-xs ${
                    finalBody.length > THREADS_BODY_LIMIT ? "text-tone-danger" : "text-gray-400"
                  }`}
                >
                  {finalBody.length}/{THREADS_BODY_LIMIT}자 (Threads 전송 상한 — 초과 시 전송이 실패합니다)
                </p>
              )}
              {hasWriteTarget && targets.length > 1 && (
                <p className="text-xs text-tone-warning">
                  같은 문구를 여러 글에 연속 게시하면 플랫폼이 스팸으로 자동 판정할 수 있습니다.
                  템플릿을 고르면 서버가 건마다 문구를 갈라 줍니다.
                </p>
              )}
            </Field>
          )}

          {excluded.length > 0 && (
            <div className="rounded-md border border-tone-warning/40 bg-amber-50 p-3">
              <p className="text-xs font-medium text-tone-warning">
                제외 {excluded.length}건 — 발송하지 않습니다
              </p>
              <ul className="mt-1 flex flex-col gap-0.5 text-xs text-gray-700">
                {excluded.map((r) => (
                  <li key={r.target.match.id}>
                    매칭 #{r.target.match.id} — {r.excludeReason}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div>
            <Button disabled={!canSubmit} onClick={() => setPhase("confirm")}>
              일괄 발송 검토 ({eligible.length}건)
            </Button>
          </div>
        </>
      )}

      {phase === "confirm" && (
        <div className="flex flex-col gap-3">
          <div className="rounded-md border border-tone-danger/40 bg-red-50 p-3 text-sm text-gray-900">
            <p className="font-medium text-tone-danger">이 동작은 되돌릴 수 없습니다.</p>
            <ul className="mt-1 list-disc pl-5 text-sm text-gray-700">
              {eligible.some((r) => r.target.source && isWritableSourceType(r.target.source.type)) && (
                <li>
                  <strong>
                    {
                      eligible.filter(
                        (r) => r.target.source && isWritableSourceType(r.target.source.type)
                      ).length
                    }
                    건
                  </strong>
                  이 지금 실제로 게시됩니다(취소·삭제 불가).
                </li>
              )}
              {eligible.some((r) => !(r.target.source && isWritableSourceType(r.target.source.type))) && (
                <li>
                  {
                    eligible.filter(
                      (r) => !(r.target.source && isWritableSourceType(r.target.source.type))
                    ).length
                  }
                  건은 전송 없이 승인 처리되고 문구는 수동 복사용으로 제공됩니다.
                </li>
              )}
              {excluded.length > 0 && <li>제외 {excluded.length}건은 발송하지 않습니다.</li>}
              <li>건당 간격을 두고 순차 처리하며, 진행 중 중단할 수 있습니다.</li>
            </ul>
          </div>

          <div className="flex flex-col gap-2">
            <p className="text-xs font-medium text-gray-500">
              전송될 문구 {renderMode ? "(건별)" : "(전 건 동일)"}
            </p>
            <ul className="flex max-h-60 flex-col gap-2 overflow-y-auto">
              {eligible.map((r) => (
                <li
                  key={r.target.match.id}
                  className="rounded-md border border-gray-200 bg-gray-50 p-2 text-sm"
                >
                  <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                    <span>매칭 #{r.target.match.id}</span>
                    <span>
                      {r.target.source ? getSourceDisplayName(r.target.source) : `#${r.target.match.source_id}`}
                    </span>
                    {r.target.source && isWritableSourceType(r.target.source.type) && (
                      <Badge tone="danger">전송</Badge>
                    )}
                  </div>
                  <p className="mt-1 whitespace-pre-wrap break-words text-gray-900">{r.body}</p>
                </li>
              ))}
            </ul>
          </div>

          <div className="flex flex-wrap gap-2">
            <Button variant="danger" onClick={() => void run()}>
              {hasWriteTarget ? `${eligible.length}건 발송 실행` : `${eligible.length}건 승인 실행`}
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
            {duplicateCount > 0 && <Badge tone="warning">이미 답한 글 {duplicateCount}건</Badge>}
            {failedCount > 0 && <Badge tone="danger">실패 {failedCount}건</Badge>}
            {skippedCount > 0 && <Badge tone="neutral">미처리 {skippedCount}건</Badge>}
            {excludedCount > 0 && <Badge tone="warning">제외 {excludedCount}건</Badge>}
          </div>

          {duplicateCount > 0 && (
            <p className="text-xs text-tone-warning">
              {duplicateCount}건은 같은 글이 다른 소스로도 수집돼 이미 답글이 나간 건입니다 —
              중복 답글은 스팸으로 판정될 수 있어 서버가 막았습니다. 재시도 대상이 아니니 무시로
              종료하세요.
            </p>
          )}

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
                {/* 건별 렌더에서는 문구가 건마다 다르므로 복사도 건별이어야 한다. */}
                {r.body && (
                  <Button size="sm" variant="ghost" onClick={() => void handleCopyBody(r.body as string)}>
                    문구 복사
                  </Button>
                )}
              </li>
            ))}
          </ul>

          <div className="flex flex-wrap gap-2">
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
