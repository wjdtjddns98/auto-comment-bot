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
          placeholder="답변 문구를 입력하세요."
        />
      </Field>
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
            <Textarea rows={2} value={body} onChange={(e) => setBody(e.target.value)} />
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
