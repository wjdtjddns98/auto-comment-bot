import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createKeyword,
  deleteKeyword,
  getKeywords,
  getSources,
  patchKeyword,
} from "../lib/apiClient";
import { describeApiError } from "../lib/errorMessage";
import { SOURCE_TYPE_LABEL } from "../lib/matchDisplay";
import type { Keyword, MatchType } from "../types/api";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Field, Input, Select } from "../components/ui/Input";
import { Table, Tbody, Td, Th, Thead, Tr } from "../components/ui/Table";

const MATCH_TYPES: MatchType[] = ["substring", "regex"];

function useSourceOptions() {
  const { data: sources } = useQuery({ queryKey: ["sources"], queryFn: getSources });
  return sources ?? [];
}

function CreateKeywordForm() {
  const queryClient = useQueryClient();
  const sources = useSourceOptions();
  const [pattern, setPattern] = useState("");
  const [matchType, setMatchType] = useState<MatchType>("substring");
  const [sourceScope, setSourceScope] = useState<number | "">("");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: createKeyword,
    onSuccess: () => {
      setPattern("");
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["keywords"] });
    },
    onError: (err) => setError(describeApiError(err)),
  });

  function handleSubmit() {
    if (!pattern.trim()) {
      setError("패턴을 입력하세요.");
      return;
    }
    mutation.mutate({
      pattern,
      match_type: matchType,
      source_scope: sourceScope === "" ? null : sourceScope,
    });
  }

  return (
    <Card className="flex flex-col gap-4">
      <h2 className="text-sm font-semibold text-gray-900">키워드 추가</h2>
      <div className="flex flex-wrap gap-4">
        <Field label="패턴">
          <Input value={pattern} onChange={(e) => setPattern(e.target.value)} placeholder="예: 강아지 간식" />
        </Field>
        <Field label="매칭 방식">
          <Select value={matchType} onChange={(e) => setMatchType(e.target.value as MatchType)}>
            {MATCH_TYPES.map((t) => (
              <option key={t} value={t}>
                {t === "substring" ? "부분 일치" : "정규식"}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="적용 범위">
          <Select
            value={sourceScope}
            onChange={(e) => setSourceScope(e.target.value === "" ? "" : Number(e.target.value))}
          >
            <option value="">전체 소스</option>
            {sources.map((s) => (
              <option key={s.id} value={s.id}>
                {SOURCE_TYPE_LABEL[s.type] ?? s.type} #{s.id}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      {error && <p className="text-sm text-tone-danger">{error}</p>}
      <Button onClick={handleSubmit} disabled={mutation.isPending} className="self-start">
        {mutation.isPending ? "추가 중…" : "추가"}
      </Button>
    </Card>
  );
}

function KeywordRow({ keyword }: { keyword: Keyword }) {
  const queryClient = useQueryClient();
  const sources = useSourceOptions();
  const [editing, setEditing] = useState(false);
  const [pattern, setPattern] = useState(keyword.pattern);
  const [matchType, setMatchType] = useState<MatchType>(keyword.match_type);
  const [sourceScope, setSourceScope] = useState<number | "">(keyword.source_scope ?? "");
  const [error, setError] = useState<string | null>(null);

  function invalidate() {
    queryClient.invalidateQueries({ queryKey: ["keywords"] });
  }

  const patchMutation = useMutation({
    mutationFn: (body: Parameters<typeof patchKeyword>[1]) => patchKeyword(keyword.id, body),
    onSuccess: () => {
      setError(null);
      invalidate();
    },
    onError: (err) => setError(describeApiError(err)),
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteKeyword(keyword.id),
    onSuccess: invalidate,
    onError: (err) => setError(describeApiError(err)),
  });

  function handleSave() {
    patchMutation.mutate(
      { pattern, match_type: matchType, source_scope: sourceScope === "" ? null : sourceScope },
      { onSuccess: () => setEditing(false) }
    );
  }

  const scopeLabel = useMemo(() => {
    if (keyword.source_scope === null) return "전체";
    const s = sources.find((s) => s.id === keyword.source_scope);
    return s ? `${SOURCE_TYPE_LABEL[s.type] ?? s.type} #${s.id}` : `#${keyword.source_scope}`;
  }, [keyword.source_scope, sources]);

  return (
    <>
      <Tr className="hover:bg-gray-50 align-top">
        <Td>
          {editing ? (
            <Input value={pattern} onChange={(e) => setPattern(e.target.value)} />
          ) : (
            keyword.pattern
          )}
        </Td>
        <Td>
          {editing ? (
            <Select value={matchType} onChange={(e) => setMatchType(e.target.value as MatchType)}>
              {MATCH_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t === "substring" ? "부분 일치" : "정규식"}
                </option>
              ))}
            </Select>
          ) : keyword.match_type === "substring" ? (
            "부분 일치"
          ) : (
            "정규식"
          )}
        </Td>
        <Td>
          {editing ? (
            <Select
              value={sourceScope}
              onChange={(e) => setSourceScope(e.target.value === "" ? "" : Number(e.target.value))}
            >
              <option value="">전체 소스</option>
              {sources.map((s) => (
                <option key={s.id} value={s.id}>
                  {SOURCE_TYPE_LABEL[s.type] ?? s.type} #{s.id}
                </option>
              ))}
            </Select>
          ) : (
            scopeLabel
          )}
        </Td>
        <Td>
          <Badge tone={keyword.enabled ? "success" : "neutral"}>
            {keyword.enabled ? "활성" : "비활성"}
          </Badge>
        </Td>
        <Td>
          <Button
            size="sm"
            variant={keyword.enabled ? "secondary" : "primary"}
            disabled={patchMutation.isPending}
            onClick={() => patchMutation.mutate({ enabled: !keyword.enabled })}
          >
            {keyword.enabled ? "비활성화" : "활성화"}
          </Button>
        </Td>
        <Td>
          <div className="flex gap-2">
            {editing ? (
              <>
                <Button size="sm" onClick={handleSave} disabled={patchMutation.isPending}>
                  저장
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
                  취소
                </Button>
              </>
            ) : (
              <>
                <Button size="sm" variant="secondary" onClick={() => setEditing(true)}>
                  수정
                </Button>
                <Button
                  size="sm"
                  variant="danger"
                  disabled={deleteMutation.isPending}
                  onClick={() => deleteMutation.mutate()}
                >
                  삭제
                </Button>
              </>
            )}
          </div>
        </Td>
      </Tr>
      {error && (
        <Tr>
          <Td colSpan={6} className="text-sm text-tone-danger">
            {error}
          </Td>
        </Tr>
      )}
    </>
  );
}

export default function AdminKeywordsPage() {
  const { data: keywords, isLoading, isError } = useQuery({
    queryKey: ["keywords"],
    queryFn: getKeywords,
  });

  return (
    <section className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">키워드 관리</h1>
        <p className="text-sm text-gray-500">매칭에 사용할 부분 일치·정규식 키워드를 관리합니다.</p>
      </div>

      <CreateKeywordForm />

      {isLoading && <p className="text-sm text-gray-500">불러오는 중…</p>}
      {isError && <p className="text-sm text-tone-danger">키워드 목록을 불러오지 못했습니다.</p>}

      {keywords && (
        <Table>
          <Thead>
            <Tr>
              <Th>패턴</Th>
              <Th>매칭 방식</Th>
              <Th>적용 범위</Th>
              <Th>상태</Th>
              <Th>활성</Th>
              <Th />
            </Tr>
          </Thead>
          <Tbody>
            {keywords.length === 0 && (
              <Tr>
                <Td colSpan={6} className="py-8 text-center text-gray-400">
                  등록된 키워드가 없습니다.
                </Td>
              </Tr>
            )}
            {keywords.map((keyword) => (
              <KeywordRow key={keyword.id} keyword={keyword} />
            ))}
          </Tbody>
        </Table>
      )}
    </section>
  );
}
