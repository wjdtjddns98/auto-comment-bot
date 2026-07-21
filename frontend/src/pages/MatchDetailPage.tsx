import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiError,
  approveMatch,
  getMatch,
  getSnsAccounts,
  getSources,
  getTemplates,
  ignoreMatch,
  retryMatch,
} from "../lib/apiClient";
import type { ApproveMatchResponse } from "../types/api";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Field, Select, Textarea } from "../components/ui/Input";
import {
  formatDateTime,
  isWritableSourceType,
  SOURCE_TYPE_LABEL,
  STATUS_LABEL,
  STATUS_TONE,
} from "../lib/matchDisplay";

const REPLY_ACTION_LABEL: Record<string, string> = {
  approved: "승인(수동 복사)",
  sent: "전송 성공",
  failed: "전송 실패",
  canceled: "무시(취소)",
};

function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 409) return "이미 처리 중이거나 완료된 매칭입니다.";
    return error.detail;
  }
  return "요청 처리 중 오류가 발생했습니다.";
}

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
  const { data: sources } = useQuery({ queryKey: ["sources"], queryFn: getSources });
  const { data: templates } = useQuery({ queryKey: ["templates"], queryFn: getTemplates });
  const { data: snsAccounts } = useQuery({ queryKey: ["snsAccounts"], queryFn: getSnsAccounts });

  const [templateId, setTemplateId] = useState<number | "">("");
  const [finalBody, setFinalBody] = useState("");
  const [snsAccountId, setSnsAccountId] = useState<number | "">("");
  const [lastResult, setLastResult] = useState<ApproveMatchResponse | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [copyDone, setCopyDone] = useState(false);

  const source = useMemo(
    () => sources?.find((s) => s.id === matchQuery.data?.source_id),
    [sources, matchQuery.data?.source_id]
  );
  const writable = source ? isWritableSourceType(source.type) : false;

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
      setCopyDone(false);
    },
    onError: (err) => setActionError(describeError(err)),
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
      setCopyDone(false);
    },
    onError: (err) => setActionError(describeError(err)),
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
    await navigator.clipboard.writeText(lastResult.clipboard_body);
    setCopyDone(true);
  }

  if (matchQuery.isLoading) return <p className="text-sm text-gray-500">불러오는 중…</p>;
  if (matchQuery.isError || !matchQuery.data) {
    return <p className="text-sm text-tone-danger">매칭을 찾을 수 없습니다.</p>;
  }

  const match = matchQuery.data;
  const actionable = match.status === "new" || match.status === "reviewing";
  const canRetry = match.status === "reviewing" && match.reply_actions.some((a) => a.action === "failed");

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
          <span>소스: {source ? SOURCE_TYPE_LABEL[source.type] : `#${match.source_id}`}</span>
          <span>작성자: {match.author ?? "-"}</span>
          <span>게시일시: {formatDateTime(match.published_at)}</span>
          <span>매칭일시: {formatDateTime(match.matched_at)}</span>
        </div>
        <p className="whitespace-pre-wrap text-gray-900">{match.content}</p>
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
          </Field>

          {writable ? (
            <Field label="SNS 계정">
              <Select
                value={snsAccountId}
                onChange={(e) => setSnsAccountId(e.target.value === "" ? "" : Number(e.target.value))}
              >
                <option value="">기본 계정</option>
                {(snsAccounts ?? [])
                  .filter((a) => a.platform === source?.type)
                  .map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.display_name}
                    </option>
                  ))}
              </Select>
            </Field>
          ) : (
            <p className="text-xs text-gray-500">
              이 소스는 자동 전송을 지원하지 않습니다. 승인 시 답변 문구를 클립보드 복사용으로 제공합니다.
            </p>
          )}

          <div className="flex items-center gap-2">
            <Button onClick={() => approveMutation.mutate()} disabled={!finalBody.trim() || approveMutation.isPending}>
              {approveMutation.isPending ? "처리 중…" : "승인"}
            </Button>
            {canRetry && (
              <Button variant="secondary" onClick={() => retryMutation.mutate()} disabled={retryMutation.isPending}>
                재시도
              </Button>
            )}
            <Button variant="danger" onClick={() => ignoreMutation.mutate()} disabled={ignoreMutation.isPending}>
              무시
            </Button>
          </div>

          {actionError && <p className="text-sm text-tone-danger">{actionError}</p>}

          {lastResult?.action === "sent" && (
            <p className="text-sm text-tone-success">전송 완료 — external_reply_id: {lastResult.external_reply_id}</p>
          )}
          {lastResult?.action === "approved" && (
            <div className="flex items-center gap-2 text-sm text-tone-success">
              <span>승인 완료 — 아래 문구를 복사해 직접 전송하세요.</span>
              <Button size="sm" variant="secondary" onClick={handleCopy}>
                {copyDone ? "복사됨" : "클립보드 복사"}
              </Button>
            </div>
          )}
        </Card>
      ) : (
        <Card className="text-sm text-gray-500">이미 처리 완료된 매칭입니다 ({STATUS_LABEL[match.status]}).</Card>
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
      </Card>
    </section>
  );
}
