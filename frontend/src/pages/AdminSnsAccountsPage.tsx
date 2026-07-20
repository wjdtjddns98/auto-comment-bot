import { Fragment, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createSnsAccount, deleteSnsAccount, getSnsAccounts } from "../lib/apiClient";
import { describeApiError } from "../lib/errorMessage";
import { formatDateTime, SOURCE_TYPE_LABEL } from "../lib/matchDisplay";
import type { SnsPlatform } from "../types/api";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Field, Input, Select, Textarea } from "../components/ui/Input";
import { Table, Tbody, Td, Th, Thead, Tr } from "../components/ui/Table";

const PLATFORMS: SnsPlatform[] = ["threads", "naver_cafe", "community"];

function CreateSnsAccountForm() {
  const queryClient = useQueryClient();
  const [platform, setPlatform] = useState<SnsPlatform>("threads");
  const [displayName, setDisplayName] = useState("");
  const [credentials, setCredentials] = useState("{}");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: createSnsAccount,
    onSuccess: () => {
      setDisplayName("");
      setCredentials("{}");
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["snsAccounts"] });
    },
    onError: (err) => setError(describeApiError(err)),
  });

  function handleSubmit() {
    if (!displayName.trim()) {
      setError("표시 이름을 입력하세요.");
      return;
    }
    let parsedCredentials: Record<string, unknown>;
    try {
      parsedCredentials = JSON.parse(credentials);
    } catch {
      setError("자격증명은 올바른 JSON이어야 합니다.");
      return;
    }
    mutation.mutate({ platform, display_name: displayName, credentials: parsedCredentials });
  }

  return (
    <Card className="flex flex-col gap-4">
      <h2 className="text-sm font-semibold text-gray-900">SNS 계정 추가</h2>
      <div className="flex flex-wrap gap-4">
        <Field label="플랫폼">
          <Select value={platform} onChange={(e) => setPlatform(e.target.value as SnsPlatform)}>
            {PLATFORMS.map((p) => (
              <option key={p} value={p}>
                {SOURCE_TYPE_LABEL[p] ?? p}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="표시 이름">
          <Input
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="예: @our_brand"
          />
        </Field>
      </div>
      <Field label="자격증명 (JSON)">
        <Textarea rows={3} value={credentials} onChange={(e) => setCredentials(e.target.value)} />
      </Field>
      <p className="text-xs text-gray-500">
        저장 즉시 서버에서 암호화되어 별도 보관되며, 이후 어떤 응답에도 다시 노출되지 않습니다.
      </p>
      {error && <p className="text-sm text-tone-danger">{error}</p>}
      <Button onClick={handleSubmit} disabled={mutation.isPending} className="self-start">
        {mutation.isPending ? "추가 중…" : "추가"}
      </Button>
    </Card>
  );
}

export default function AdminSnsAccountsPage() {
  const queryClient = useQueryClient();
  const { data: accounts, isLoading, isError } = useQuery({
    queryKey: ["snsAccounts"],
    queryFn: getSnsAccounts,
  });
  const [rowError, setRowError] = useState<{ id: number; message: string } | null>(null);

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteSnsAccount(id),
    onSuccess: () => {
      setRowError(null);
      queryClient.invalidateQueries({ queryKey: ["snsAccounts"] });
    },
    onError: (err, id) => setRowError({ id, message: describeApiError(err) }),
  });

  return (
    <section className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">SNS 계정 관리</h1>
        <p className="text-sm text-gray-500">
          답변 전송에 사용할 SNS 계정을 등록·삭제합니다. 자격증명은 어떤 응답에도 포함되지 않습니다.
        </p>
      </div>

      <CreateSnsAccountForm />

      {isLoading && <p className="text-sm text-gray-500">불러오는 중…</p>}
      {isError && <p className="text-sm text-tone-danger">SNS 계정 목록을 불러오지 못했습니다.</p>}

      {accounts && (
        <Table>
          <Thead>
            <Tr>
              <Th>플랫폼</Th>
              <Th>표시 이름</Th>
              <Th>상태</Th>
              <Th>토큰 만료</Th>
              <Th />
            </Tr>
          </Thead>
          <Tbody>
            {accounts.length === 0 && (
              <Tr>
                <Td colSpan={5} className="py-8 text-center text-gray-400">
                  등록된 SNS 계정이 없습니다.
                </Td>
              </Tr>
            )}
            {accounts.map((account) => (
              <Fragment key={account.id}>
                <Tr className="hover:bg-gray-50">
                  <Td>{SOURCE_TYPE_LABEL[account.platform] ?? account.platform}</Td>
                  <Td>{account.display_name}</Td>
                  <Td>
                    <Badge tone={account.status === "active" ? "success" : "warning"}>
                      {account.status}
                    </Badge>
                  </Td>
                  <Td>{formatDateTime(account.token_expires_at)}</Td>
                  <Td>
                    <Button
                      size="sm"
                      variant="danger"
                      disabled={deleteMutation.isPending}
                      onClick={() => deleteMutation.mutate(account.id)}
                    >
                      삭제
                    </Button>
                  </Td>
                </Tr>
                {rowError?.id === account.id && (
                  <Tr>
                    <Td colSpan={5} className="text-sm text-tone-danger">
                      {rowError.message}
                    </Td>
                  </Tr>
                )}
              </Fragment>
            ))}
          </Tbody>
        </Table>
      )}
    </section>
  );
}
