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
  getSourceDisplayName,
  HEALTH_TONE,
  STATUS_LABEL,
  STATUS_TONE,
  toPlainText,
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
  { value: "verify_pending", label: "전송결과 확인중" },
  { value: "replied", label: "답변완료" },
  { value: "ignored", label: "무시됨" },
];

// 백엔드는 만료된 backoff 를 "해당 소스의 다음 poll 틱"에서 정리하므로(app/poller.py), 만료 시각
// ~ 다음 틱 사이에는 API 가 이미 지난 backoff_until 을 그대로 내려준다. 그 값을 그대로 빨간
// 문구로 띄우면 이미 풀린 제한을 소스 주기(poll_interval_sec)만큼 계속 경고하는 셈이라, 미래
// 시각일 때만 표시한다. 클라이언트 시계 기준 비교라 초 단위 오차는 있으나 분 단위로 틀린
// 값을 노출하는 것보다 정확하다.
function isBackoffActive(backoffUntil: string | null): boolean {
  if (!backoffUntil) return false;
  const until = new Date(backoffUntil).getTime();
  // 파싱 실패(NaN)면 값 자체를 신뢰할 수 없으므로 표시하지 않는다.
  return Number.isFinite(until) && until > Date.now();
}

function SourceHealthBar() {
  const { data: sources, isLoading } = useQuery({ queryKey: ["sources"], queryFn: getSources });

  if (isLoading) return <p className="text-sm text-gray-500">소스 상태 확인 중…</p>;

  // 상단 상태 바에는 활성 소스만 노출(비활성 소스는 표시하지 않음).
  const activeSources = (sources ?? []).filter((source) => source.enabled);
  if (activeSources.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-3">
      {activeSources.map((source) => (
        // 모바일: 풀폭 카드 + 내용 줄바꿈(가로 오버플로 방지), sm↑: 콘텐츠 폭.
        <Card
          key={source.id}
          className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 sm:w-auto"
        >
          <span className="font-medium text-gray-900">{getSourceDisplayName(source)}</span>
          <Badge tone={HEALTH_TONE[source.health_status] ?? "neutral"}>
            ● {source.health_status}
          </Badge>
          <span className="text-xs text-gray-500">
            최근 수집 {formatDateTime(source.last_success_at)}
          </span>
          {isBackoffActive(source.backoff_until) && (
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
        <label className="flex w-full flex-col gap-1 text-sm text-gray-700 sm:w-auto">
          <span className="font-medium">상태</span>
          <Select value={status} onChange={(e) => updateStatus(e.target.value)}>
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </Select>
        </label>
        <label className="flex w-full flex-col gap-1 text-sm text-gray-700 sm:w-auto">
          <span className="font-medium">소스</span>
          <Select value={sourceId} onChange={(e) => updateSource(e.target.value)}>
            <option value="all">전체</option>
            {(sources ?? [])
              .filter((s) => s.enabled)
              .map((s) => (
                <option key={s.id} value={s.id}>
                  {getSourceDisplayName(s)}
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
                // RSS 본문은 피드 원문 HTML 이라 평문으로 정규화해 표시한다(목록·툴팁 동일).
                const content = toPlainText(m.content);
                return (
                  <Tr
                    key={m.id}
                    className="cursor-pointer hover:bg-gray-50"
                    onClick={() => navigate(`/matches/${m.id}`)}
                  >
                    <Td>
                      <Badge tone={STATUS_TONE[m.status]}>{STATUS_LABEL[m.status]}</Badge>
                    </Td>
                    <Td>
                      {source ? (
                        <span className="inline-flex flex-wrap items-center gap-1.5">
                          {getSourceDisplayName(source)}
                          {/* 비활성 소스는 상태 바·필터 선택지에서 빠지지만(#70) 이미 수집된 매칭은
                              목록에 남는다 — 필터에 없는 소스가 뜨는 이유를 배지로 설명한다. */}
                          {!source.enabled && <Badge tone="neutral">비활성</Badge>}
                        </span>
                      ) : (
                        `#${m.source_id}`
                      )}
                    </Td>
                    <Td>{keyword?.pattern ?? "-"}</Td>
                    <Td className="max-w-sm truncate" title={content}>
                      {content}
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
