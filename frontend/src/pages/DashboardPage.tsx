import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getKeywords, getMatches, getSources } from "../lib/apiClient";
import type { MatchedPostStatus } from "../types/api";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Select } from "../components/ui/Input";
import { Table, Tbody, Td, Th, Thead, Tr } from "../components/ui/Table";
import {
  formatDateTime,
  HEALTH_TONE,
  SOURCE_TYPE_LABEL,
  STATUS_LABEL,
  STATUS_TONE,
} from "../lib/matchDisplay";

const PAGE_SIZE = 20;

function ExternalLinkIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="currentColor" className="h-4 w-4" aria-hidden="true">
      <path d="M11 3a1 1 0 100 2h2.586l-6.293 6.293a1 1 0 101.414 1.414L15 6.414V9a1 1 0 102 0V4a1 1 0 00-1-1h-5z" />
      <path d="M5 5a2 2 0 00-2 2v8a2 2 0 002 2h8a2 2 0 002-2v-3a1 1 0 10-2 0v3H5V7h3a1 1 0 000-2H5z" />
    </svg>
  );
}

const STATUS_OPTIONS: Array<{ value: MatchedPostStatus | "all"; label: string }> = [
  { value: "all", label: "전체" },
  { value: "new", label: "신규" },
  { value: "reviewing", label: "검토중" },
  { value: "sending", label: "전송중" },
  { value: "replied", label: "답변완료" },
  { value: "ignored", label: "무시됨" },
];

function SourceHealthBar() {
  const { data: sources, isLoading } = useQuery({ queryKey: ["sources"], queryFn: getSources });

  if (isLoading) return <p className="text-sm text-gray-500">소스 상태 확인 중…</p>;
  if (!sources || sources.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-3">
      {sources.map((source) => (
        <Card key={source.id} className="flex items-center gap-3 px-4 py-2.5">
          <span className="font-medium text-gray-900">
            {SOURCE_TYPE_LABEL[source.type] ?? source.type}
          </span>
          <Badge tone={HEALTH_TONE[source.health_status] ?? "neutral"}>
            ● {source.health_status}
          </Badge>
          {!source.enabled && <Badge tone="neutral">비활성</Badge>}
          <span className="text-xs text-gray-500">
            최근 수집 {formatDateTime(source.last_success_at)}
          </span>
          {source.backoff_until && (
            <span className="text-xs text-tone-danger">
              backoff ~{formatDateTime(source.backoff_until)}
            </span>
          )}
        </Card>
      ))}
    </div>
  );
}

export default function DashboardPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<MatchedPostStatus | "all">("all");
  const [sourceId, setSourceId] = useState<number | "all">("all");
  const [page, setPage] = useState(1);

  const { data: sources } = useQuery({ queryKey: ["sources"], queryFn: getSources });
  const { data: keywords } = useQuery({ queryKey: ["keywords"], queryFn: getKeywords });

  const matchesQuery = useQuery({
    queryKey: ["matches", { status, sourceId, page }],
    queryFn: () =>
      getMatches({
        status: status === "all" ? undefined : status,
        source_id: sourceId === "all" ? undefined : sourceId,
        page,
        size: PAGE_SIZE,
      }),
    placeholderData: (prev) => prev,
  });

  const sourceMap = useMemo(() => new Map((sources ?? []).map((s) => [s.id, s])), [sources]);
  const keywordMap = useMemo(() => new Map((keywords ?? []).map((k) => [k.id, k])), [keywords]);

  const totalPages = matchesQuery.data
    ? Math.max(1, Math.ceil(matchesQuery.data.total / PAGE_SIZE))
    : 1;

  function updateStatus(value: string) {
    setStatus(value === "all" ? "all" : (value as MatchedPostStatus));
    setPage(1);
  }

  function updateSource(value: string) {
    setSourceId(value === "all" ? "all" : Number(value));
    setPage(1);
  }

  return (
    <section className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">대시보드</h1>
        <p className="text-sm text-gray-500">키워드 매칭 글을 검토하고 승인·전송합니다.</p>
      </div>

      <SourceHealthBar />

      <Card className="flex flex-wrap items-end gap-4">
        <label className="flex flex-col gap-1 text-sm text-gray-700">
          <span className="font-medium">상태</span>
          <Select value={status} onChange={(e) => updateStatus(e.target.value)}>
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </Select>
        </label>
        <label className="flex flex-col gap-1 text-sm text-gray-700">
          <span className="font-medium">소스</span>
          <Select value={sourceId} onChange={(e) => updateSource(e.target.value)}>
            <option value="all">전체</option>
            {(sources ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {SOURCE_TYPE_LABEL[s.type] ?? s.type} #{s.id}
              </option>
            ))}
          </Select>
        </label>
        <span className="ml-auto text-sm text-gray-500">총 {matchesQuery.data?.total ?? 0}건</span>
      </Card>

      {matchesQuery.isLoading && <p className="text-sm text-gray-500">불러오는 중…</p>}
      {matchesQuery.isError && (
        <p className="text-sm text-tone-danger">매칭 목록을 불러오지 못했습니다.</p>
      )}

      {matchesQuery.data && (
        <>
          <Table>
            <Thead>
              <Tr>
                <Th>상태</Th>
                <Th>소스</Th>
                <Th>키워드</Th>
                <Th>내용</Th>
                <Th>작성자</Th>
                <Th>매칭일시</Th>
                <Th />
              </Tr>
            </Thead>
            <Tbody>
              {matchesQuery.data.items.length === 0 && (
                <Tr>
                  <Td colSpan={7} className="py-8 text-center text-gray-400">
                    조건에 맞는 매칭 글이 없습니다.
                  </Td>
                </Tr>
              )}
              {matchesQuery.data.items.map((m) => {
                const source = sourceMap.get(m.source_id);
                const keyword =
                  m.matched_keyword_id != null ? keywordMap.get(m.matched_keyword_id) : undefined;
                return (
                  <Tr
                    key={m.id}
                    className="cursor-pointer hover:bg-gray-50"
                    onClick={() => navigate(`/matches/${m.id}`)}
                  >
                    <Td>
                      <Badge tone={STATUS_TONE[m.status]}>{STATUS_LABEL[m.status]}</Badge>
                    </Td>
                    <Td>{source ? SOURCE_TYPE_LABEL[source.type] ?? source.type : `#${m.source_id}`}</Td>
                    <Td>{keyword?.pattern ?? "-"}</Td>
                    <Td className="max-w-sm truncate" title={m.content}>
                      {m.content}
                    </Td>
                    <Td>{m.author ?? "-"}</Td>
                    <Td className="whitespace-nowrap">{formatDateTime(m.matched_at)}</Td>
                    <Td>
                      {m.url && (
                        <a
                          href={m.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          title="원문 보기"
                          className="inline-flex text-gray-400 hover:text-brand-600"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <ExternalLinkIcon />
                        </a>
                      )}
                    </Td>
                  </Tr>
                );
              })}
            </Tbody>
          </Table>

          <div className="flex items-center justify-between">
            <Button
              variant="secondary"
              size="sm"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              이전
            </Button>
            <span className="text-sm text-gray-500">
              {page} / {totalPages} 페이지
            </span>
            <Button
              variant="secondary"
              size="sm"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              다음
            </Button>
          </div>
        </>
      )}
    </section>
  );
}
