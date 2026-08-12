import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createTemplate, deleteTemplate, getTemplates, patchTemplate } from "../lib/apiClient";
import { describeApiError } from "../lib/errorMessage";
import type { Template } from "../types/api";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Field, Input, Textarea } from "../components/ui/Input";
import { Table, Tbody, Td, Th, Thead, Tr } from "../components/ui/Table";
import { THREADS_BODY_LIMIT } from "../lib/matchDisplay";

// 치환은 서버(POST /api/matches/render-template)가 한다 — 여기서는 문법만 안내한다.
// 안내가 일괄 발송 화면에만 있으면 정작 템플릿을 쓰는 순간에는 보이지 않아, 변형 없는
// 템플릿이 만들어지고 N건이 같은 문구로 나간다(중복 콘텐츠 = 스팸 신호).
function TemplateSyntaxHelp() {
  return (
    <div className="rounded-md bg-gray-50 p-3 text-xs text-gray-600">
      <p className="font-medium text-gray-700">치환 문법</p>
      <ul className="mt-1 flex flex-col gap-0.5">
        <li>
          <code>{"{{안녕하세요|반갑습니다}}"}</code> — 후보 중 하나를 <strong>건마다 따로</strong>{" "}
          뽑습니다. 같은 템플릿으로 여러 건을 보내도 문구가 갈립니다.
        </li>
        <li>
          <code>{"{{author}}"}</code>·<code>{"{{keyword}}"}</code>·<code>{"{{url}}"}</code> — 그
          글의 작성자·매칭 키워드·원문 링크로 채워집니다.
        </li>
        <li>
          값이 없는 변수(작성자 미확보)나 오타 변수는 <strong>그 건만 미리보기에서 제외</strong>
          됩니다 — 빈 문구로 나가지 않습니다.
        </li>
      </ul>
    </div>
  );
}

// 치환 결과 길이는 뽑히는 변형에 따라 달라져 여기서 확정할 수 없다 — 원문만으로 이미 상한을
// 넘으면 어떤 변형을 뽑아도 초과라, 그때만 경고한다(승인 화면에서 최종 검사).
function BodyLengthHint({ body }: { body: string }) {
  const over = body.length > THREADS_BODY_LIMIT;
  return (
    <p className={`text-xs ${over ? "text-tone-danger" : "text-gray-400"}`}>
      {body.length}/{THREADS_BODY_LIMIT}자
      {over
        ? " — 치환 전에 이미 Threads 상한을 넘었습니다. 이 템플릿으로는 전송이 실패합니다."
        : " (Threads 전송 상한 기준 — 치환 결과 길이는 달라질 수 있습니다)"}
    </p>
  );
}

function CreateTemplateForm() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [body, setBody] = useState("");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: createTemplate,
    onSuccess: () => {
      setName("");
      setBody("");
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["templates"] });
    },
    onError: (err) => setError(describeApiError(err)),
  });

  function handleSubmit() {
    if (!name.trim() || !body.trim()) {
      setError("이름과 내용을 모두 입력하세요.");
      return;
    }
    mutation.mutate({ name, body });
  }

  return (
    <Card className="flex flex-col gap-4">
      <h2 className="text-sm font-semibold text-gray-900">템플릿 추가</h2>
      <Field label="이름">
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="예: 기본 안내" />
      </Field>
      <Field label="내용" htmlFor="new-template-body">
        <Textarea
          id="new-template-body"
          rows={3}
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="예: {{안녕하세요|반갑습니다}} {{keyword}} 관련해서 도움이 될 만한 내용이 있어 남깁니다."
        />
        <BodyLengthHint body={body} />
      </Field>
      <TemplateSyntaxHelp />
      {error && <p className="text-sm text-tone-danger">{error}</p>}
      <Button onClick={handleSubmit} disabled={mutation.isPending} className="self-start">
        {mutation.isPending ? "추가 중…" : "추가"}
      </Button>
    </Card>
  );
}

function TemplateRow({ template }: { template: Template }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(template.name);
  const [body, setBody] = useState(template.body);
  const [error, setError] = useState<string | null>(null);

  function invalidate() {
    queryClient.invalidateQueries({ queryKey: ["templates"] });
  }

  const patchMutation = useMutation({
    mutationFn: (patch: Parameters<typeof patchTemplate>[1]) => patchTemplate(template.id, patch),
    onSuccess: () => {
      setError(null);
      invalidate();
    },
    onError: (err) => setError(describeApiError(err)),
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteTemplate(template.id),
    onSuccess: invalidate,
    onError: (err) => setError(describeApiError(err)),
  });

  function handleSave() {
    patchMutation.mutate({ name, body }, { onSuccess: () => setEditing(false) });
  }

  return (
    <>
      <Tr className="hover:bg-gray-50 align-top">
        <Td className="max-w-[10rem]">
          {editing ? <Input value={name} onChange={(e) => setName(e.target.value)} /> : template.name}
        </Td>
        <Td className="max-w-md">
          {editing ? (
            <>
              <Textarea rows={2} value={body} onChange={(e) => setBody(e.target.value)} />
              <BodyLengthHint body={body} />
            </>
          ) : (
            <span className="line-clamp-2 whitespace-pre-wrap">{template.body}</span>
          )}
        </Td>
        <Td>
          <Badge tone={template.enabled ? "success" : "neutral"}>
            {template.enabled ? "활성" : "비활성"}
          </Badge>
        </Td>
        <Td>
          <Button
            size="sm"
            variant={template.enabled ? "secondary" : "primary"}
            disabled={patchMutation.isPending}
            onClick={() => patchMutation.mutate({ enabled: !template.enabled })}
          >
            {template.enabled ? "비활성화" : "활성화"}
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
          <Td colSpan={5} className="text-sm text-tone-danger">
            {error}
          </Td>
        </Tr>
      )}
    </>
  );
}

export default function AdminTemplatesPage() {
  const { data: templates, isLoading, isError } = useQuery({
    queryKey: ["templates"],
    queryFn: getTemplates,
  });

  return (
    <section className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">템플릿 관리</h1>
        <p className="text-sm text-gray-500">승인 시 사용할 답변 템플릿을 관리합니다.</p>
      </div>

      <CreateTemplateForm />

      {isLoading && <p className="text-sm text-gray-500">불러오는 중…</p>}
      {isError && <p className="text-sm text-tone-danger">템플릿 목록을 불러오지 못했습니다.</p>}

      {templates && (
        <Table>
          <Thead>
            <Tr>
              <Th>이름</Th>
              <Th>내용</Th>
              <Th>상태</Th>
              <Th>활성</Th>
              <Th />
            </Tr>
          </Thead>
          <Tbody>
            {templates.length === 0 && (
              <Tr>
                <Td colSpan={5} className="py-8 text-center text-gray-400">
                  등록된 템플릿이 없습니다.
                </Td>
              </Tr>
            )}
            {templates.map((template) => (
              <TemplateRow key={template.id} template={template} />
            ))}
          </Tbody>
        </Table>
      )}
    </section>
  );
}
