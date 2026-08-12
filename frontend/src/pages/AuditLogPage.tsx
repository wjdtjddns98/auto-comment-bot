import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getReplyActions } from "../lib/apiClient";
import type { ReplyActionType } from "../types/api";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Field, Input, Select } from "../components/ui/Input";
import { Table, Tbody, Td, Th, Thead, Tr } from "../components/ui/Table";
import { formatDateTime, REPLY_ACTION_LABEL, REPLY_ACTION_TONE } from "../lib/matchDisplay";

// 서버는 최신 limit 건만 준다(docs/API-SPEC.md §감사 로그 — 기본 200, 상한 500).
const DEFAULT_LIMIT = 200;
const MAX_LIMIT = 500;

const ACTION_OPTIONS: Array<{ value: ReplyActionType | "all"; label: string }> = [
  { value: "all", label: "전체" },
  { value: "approved", label: REPLY_ACTION_LABEL.approved },
  { value: "sent", label: REPLY_ACTION_LABEL.sent },
  { value: "failed", label: REPLY_ACTION_LABEL.failed },
  { value: "canceled", label: REPLY_ACTION_LABEL.canceled },
  { value: "unknown", label: REPLY_ACTION_LABEL.unknown },
];

export default function AuditLogPage() {
  const [action, setAction] = useState<ReplyActionType | "all">("all");
  const [matchIdFilter, setMatchIdFilter] = useState("");
  const [limit, setLimit] = useState(DEFAULT_LIMIT);

  const trimmedMatchId = matchIdFilter.trim();
  const matchId = trimmedMatchId === "" ? undefined : Number(trimmedMatchId);
  const matchIdInvalid = trimmedMatchId !== "" && !Number.isFinite(matchId);

  const query = useQuery({
    queryKey: ["reply-actions", matchId, limit],
    queryFn: () => getReplyActions({ match_id: matchId, limit }),
    enabled: !matchIdInvalid,
  });

  const loaded = query.data ?? [];
  // 서버가 이미 created_at desc 로 주지만, 정렬 계약이 화면 표시의 전제라 여기서도 고정한다.
  const items = loaded
    .filter((a) => action === "all" || a.action === action)
    .slice()
    .sort((a, b) => (a.created_at < b.created_at ? 1 : -1));

  // limit 만큼 꽉 찼으면 더 오래된 이력이 잘렸을 수 있다. 액션 필터는 **받아온 창 안에서만**
  // 걸리므로(서버는 action 필터를 지원하지 않는다) 이 사실을 숨기면 "실패 0건"처럼 읽힌다.
  const truncated = loaded.length >= limit;

  return (
    <section className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">감사 로그</h1>
        <p className="text-sm text-gray-500">
          승인·전송·무시 등 처리 이력을 조회합니다 (reply_actions append-only).
        </p>
      </div>

      <Card className="flex flex-wrap items-end gap-4">
        <Field label="액션">
          <Select value={action} onChange={(e) => setAction(e.target.value as ReplyActionType | "all")}>
            {ACTION_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="매칭 ID">
          <Input
            type="number"
            min={1}
            placeholder="전체"
            value={matchIdFilter}
            onChange={(e) => setMatchIdFilter(e.target.value)}
          />
        </Field>
        <span className="ml-auto text-sm text-gray-500">
          {action === "all" ? `총 ${items.length}건` : `${items.length}건 / 조회 ${loaded.length}건`}
        </span>
      </Card>

      {matchIdInvalid && <p className="text-sm text-tone-danger">매칭 ID는 숫자로 입력해 주세요.</p>}
      {query.isLoading && <p className="text-sm text-gray-500">불러오는 중…</p>}
      {query.isError && <p className="text-sm text-tone-danger">감사 로그를 불러오지 못했습니다.</p>}
      {truncated && (
        <div className="flex flex-wrap items-center gap-2 text-sm text-gray-500">
          <span>
            최신 {limit}건만 조회했습니다 — 더 오래된 이력이 있을 수 있고, 액션 필터도 이 범위
            안에서만 적용됩니다.
          </span>
          {limit < MAX_LIMIT && (
            <Button size="sm" variant="secondary" onClick={() => setLimit(MAX_LIMIT)}>
              {MAX_LIMIT}건까지 불러오기
            </Button>
          )}
        </div>
      )}

      {query.data && (
        <Table>
          <Thead>
            <Tr>
              <Th>일시</Th>
              <Th>액션</Th>
              <Th>매칭</Th>
              <Th>리뷰어</Th>
              <Th>결과/오류</Th>
            </Tr>
          </Thead>
          <Tbody>
            {items.length === 0 && (
              <Tr>
                <Td colSpan={5} className="py-8 text-center text-gray-400">
                  조건에 맞는 이력이 없습니다.
                </Td>
              </Tr>
            )}
            {items.map((a) => (
              <Tr key={a.id}>
                <Td className="whitespace-nowrap">{formatDateTime(a.created_at)}</Td>
                <Td>
                  <Badge tone={REPLY_ACTION_TONE[a.action]}>{REPLY_ACTION_LABEL[a.action]}</Badge>
                </Td>
                <Td>
                  <Link to={`/matches/${a.matched_post_id}`} className="text-brand-600 hover:underline">
                    매칭 #{a.matched_post_id}
                  </Link>
                </Td>
                <Td>사용자 #{a.reviewer_user_id}</Td>
                <Td className="max-w-sm truncate" title={a.error ?? a.external_reply_id ?? undefined}>
                  {a.action === "sent" && a.external_reply_id
                    ? `reply_id: ${a.external_reply_id}`
                    : (a.error ?? "-")}
                </Td>
              </Tr>
            ))}
          </Tbody>
        </Table>
      )}
    </section>
  );
}
