import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createSource, deleteSource, getSources, patchSource } from "../lib/apiClient";
import { describeApiError } from "../lib/errorMessage";
import {
  SOURCE_TYPE_IMPLEMENTED,
  SOURCE_TYPE_LABEL,
  formatDateTime,
  getRssUrl,
  HEALTH_TONE,
} from "../lib/matchDisplay";
import type { Source, SourceType } from "../types/api";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Field, Input, Select } from "../components/ui/Input";
import { Table, Tbody, Td, Th, Thead, Tr } from "../components/ui/Table";

const SOURCE_TYPES: SourceType[] = ["threads", "naver_cafe", "community"];

function CreateSourceForm() {
  const queryClient = useQueryClient();
  const [type, setType] = useState<SourceType>("community");
  const [rssUrl, setRssUrl] = useState("");
  const [pollIntervalSec, setPollIntervalSec] = useState(300);
  const [error, setError] = useState<string | null>(null);
  const implemented = SOURCE_TYPE_IMPLEMENTED[type];

  const mutation = useMutation({
    mutationFn: createSource,
    onSuccess: () => {
      setRssUrl("");
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["sources"] });
    },
    onError: (err) => setError(describeApiError(err)),
  });

  function handleSubmit() {
    if (!implemented) return;
    if (!rssUrl.trim()) {
      setError("RSS URL을 입력하세요.");
      return;
    }
    mutation.mutate({ type, config: { rss_url: rssUrl.trim() }, poll_interval_sec: pollIntervalSec });
  }

  return (
    <Card className="flex flex-col gap-4">
      <h2 className="text-sm font-semibold text-gray-900">소스 추가</h2>
      <div className="flex flex-wrap gap-4">
        <Field label="타입">
          <Select value={type} onChange={(e) => setType(e.target.value as SourceType)}>
            {SOURCE_TYPES.map((t) => (
              <option key={t} value={t} disabled={!SOURCE_TYPE_IMPLEMENTED[t]}>
                {SOURCE_TYPE_LABEL[t]}
                {SOURCE_TYPE_IMPLEMENTED[t] ? "" : " (미구현 · M2 예정)"}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="폴링 주기(초, 최소 60)">
          <Input
            type="number"
            min={60}
            value={pollIntervalSec}
            onChange={(e) => setPollIntervalSec(Number(e.target.value))}
          />
        </Field>
      </div>
      {implemented ? (
        <Field label="RSS URL">
          <Input
            type="url"
            placeholder="https://example.com/feed"
            value={rssUrl}
            onChange={(e) => setRssUrl(e.target.value)}
          />
        </Field>
      ) : (
        <p className="text-sm text-gray-500">
          이 소스 타입은 아직 지원되지 않습니다(M2 예정). "커뮤니티" 타입만 등록할 수 있습니다.
        </p>
      )}
      {error && <p className="text-sm text-tone-danger">{error}</p>}
      <Button onClick={handleSubmit} disabled={mutation.isPending || !implemented} className="self-start">
        {mutation.isPending ? "추가 중…" : "추가"}
      </Button>
    </Card>
  );
}

function SourceRow({ source }: { source: Source }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [pollIntervalSec, setPollIntervalSec] = useState(source.poll_interval_sec);
  const [rssUrl, setRssUrl] = useState(() => getRssUrl(source.config));
  const [error, setError] = useState<string | null>(null);
  const implemented = SOURCE_TYPE_IMPLEMENTED[source.type];

  function invalidate() {
    queryClient.invalidateQueries({ queryKey: ["sources"] });
  }

  const patchMutation = useMutation({
    mutationFn: (body: Parameters<typeof patchSource>[1]) => patchSource(source.id, body),
    onSuccess: () => {
      setError(null);
      invalidate();
    },
    onError: (err) => setError(describeApiError(err)),
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteSource(source.id),
    onSuccess: invalidate,
    // 409 사유(매칭 이력 vs 스코프 키워드)에 따라 서버가 다른 문구를 보내므로 그대로 노출한다.
    onError: (err) => setError(describeApiError(err)),
  });

  function handleSave() {
    const body: Parameters<typeof patchSource>[1] = { poll_interval_sec: pollIntervalSec };
    if (implemented) {
      if (!rssUrl.trim()) {
        setError("RSS URL을 입력하세요.");
        return;
      }
      body.config = { rss_url: rssUrl.trim() };
    }
    patchMutation.mutate(body, { onSuccess: () => setEditing(false) });
  }

  return (
    <>
      <Tr className="hover:bg-gray-50 align-top">
        <Td>{SOURCE_TYPE_LABEL[source.type] ?? source.type}</Td>
        <Td className="max-w-xs">
          {editing && implemented ? (
            <Input
              type="url"
              value={rssUrl}
              onChange={(e) => setRssUrl(e.target.value)}
              className="w-full"
            />
          ) : implemented ? (
            <span className="break-all text-xs text-gray-500">{getRssUrl(source.config)}</span>
          ) : (
            <span className="break-all text-xs text-gray-400">{JSON.stringify(source.config)}</span>
          )}
        </Td>
        <Td>
          {editing ? (
            <Input
              type="number"
              min={60}
              value={pollIntervalSec}
              onChange={(e) => setPollIntervalSec(Number(e.target.value))}
              className="w-24"
            />
          ) : (
            `${source.poll_interval_sec}초`
          )}
        </Td>
        <Td>
          <Badge tone={HEALTH_TONE[source.health_status] ?? "neutral"}>{source.health_status}</Badge>
        </Td>
        <Td>{formatDateTime(source.last_success_at)}</Td>
        <Td>
          <Button
            size="sm"
            variant={source.enabled ? "secondary" : "primary"}
            disabled={patchMutation.isPending}
            onClick={() => patchMutation.mutate({ enabled: !source.enabled })}
          >
            {source.enabled ? "비활성화" : "활성화"}
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
          <Td colSpan={7} className="text-sm text-tone-danger">
            {error}
          </Td>
        </Tr>
      )}
    </>
  );
}

export default function AdminSourcesPage() {
  const { data: sources, isLoading, isError } = useQuery({ queryKey: ["sources"], queryFn: getSources });

  return (
    <section className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">소스 관리</h1>
        <p className="text-sm text-gray-500">Threads/네이버 카페/커뮤니티 소스를 등록하고 관리합니다.</p>
      </div>

      <CreateSourceForm />

      {isLoading && <p className="text-sm text-gray-500">불러오는 중…</p>}
      {isError && <p className="text-sm text-tone-danger">소스 목록을 불러오지 못했습니다.</p>}

      {sources && (
        <Table>
          <Thead>
            <Tr>
              <Th>타입</Th>
              <Th>설정</Th>
              <Th>폴링 주기</Th>
              <Th>헬스</Th>
              <Th>최근 수집</Th>
              <Th>활성</Th>
              <Th />
            </Tr>
          </Thead>
          <Tbody>
            {sources.length === 0 && (
              <Tr>
                <Td colSpan={7} className="py-8 text-center text-gray-400">
                  등록된 소스가 없습니다.
                </Td>
              </Tr>
            )}
            {sources.map((source) => (
              <SourceRow key={source.id} source={source} />
            ))}
          </Tbody>
        </Table>
      )}
    </section>
  );
}
