import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ApiError, getReplyActions } from "../lib/apiClient";
import type { ReplyActionType } from "../types/api";
import { Badge } from "../components/ui/Badge";
import { Card } from "../components/ui/Card";
import { Field, Input, Select } from "../components/ui/Input";
import { Table, Tbody, Td, Th, Thead, Tr } from "../components/ui/Table";
import { formatDateTime, REPLY_ACTION_LABEL, REPLY_ACTION_TONE } from "../lib/matchDisplay";

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

  const trimmedMatchId = matchIdFilter.trim();
  const matchId = trimmedMatchId === "" ? undefined : Number(trimmedMatchId);
  const matchIdInvalid = trimmedMatchId !== "" && !Number.isFinite(matchId);

  const query = useQuery({
    queryKey: ["reply-actions", matchId],
    queryFn: () => getReplyActions({ match_id: matchId }),
    enabled: !matchIdInvalid,
  });

  const items = (query.data ?? [])
    .filter((a) => action === "all" || a.action === action)
    .slice()
    .sort((a, b) => (a.created_at < b.created_at ? 1 : -1));

  // 전역 감사 로그 API(GET /api/reply-actions)는 아직 백엔드 미구현(404)이다.
  // 일반 오류와 구분해 "준비 중" 안내로 표시한다(매칭 상세의 처리 이력은 정상 동작).
  const notImplemented =
    query.isError && query.error instanceof ApiError && query.error.status === 404;

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
        <span className="ml-auto text-sm text-gray-500">총 {items.length}건</span>
      </Card>

      {matchIdInvalid && <p className="text-sm text-tone-danger">매칭 ID는 숫자로 입력해 주세요.</p>}
      {query.isLoading && <p className="text-sm text-gray-500">불러오는 중…</p>}
      {notImplemented && (
        <Card className="flex flex-col gap-1 border-dashed bg-gray-50 text-center">
          <p className="text-sm font-medium text-gray-700">전역 감사 로그는 아직 준비 중입니다.</p>
          <p className="text-sm text-gray-500">
            백엔드에서 조회 API 구현 후 이용할 수 있습니다. 개별 매칭의 처리 이력은{" "}
            <Link to="/" className="text-brand-600 hover:underline">
              매칭 목록
            </Link>
            에서 각 매칭을 열어 확인할 수 있습니다.
          </p>
        </Card>
      )}
      {query.isError && !notImplemented && (
        <p className="text-sm text-tone-danger">감사 로그를 불러오지 못했습니다.</p>
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
