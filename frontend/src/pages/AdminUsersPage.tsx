// ⚠️ /api/users 는 아직 백엔드 미확정 제안 계약이다(types/api.ts 주석 참조).
// 목업 서버(lib/mock/mockServer.ts) 기준으로 구현했으며, 실제 API 확정 후 재검증이 필요하다.
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createUser, deleteUser, getUsers, patchUser } from "../lib/apiClient";
import { describeApiError } from "../lib/errorMessage";
import { formatDateTime } from "../lib/matchDisplay";
import { useAuth } from "../hooks/useAuth";
import type { AdminUser, Role } from "../types/api";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Field, Input, Select } from "../components/ui/Input";
import { Table, Tbody, Td, Th, Thead, Tr } from "../components/ui/Table";

const ROLES: Role[] = ["admin", "reviewer"];
const ROLE_LABEL: Record<Role, string> = { admin: "관리자", reviewer: "검토자" };

function CreateUserForm() {
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("reviewer");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: createUser,
    onSuccess: () => {
      setEmail("");
      setPassword("");
      setRole("reviewer");
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["users"] });
    },
    onError: (err) => setError(describeApiError(err)),
  });

  function handleSubmit() {
    if (!email.trim()) {
      setError("이메일을 입력하세요.");
      return;
    }
    if (password.length < 8) {
      setError("비밀번호는 8자 이상이어야 합니다.");
      return;
    }
    mutation.mutate({ email: email.trim(), password, role });
  }

  return (
    <Card className="flex flex-col gap-4">
      <h2 className="text-sm font-semibold text-gray-900">사용자 추가</h2>
      <div className="flex flex-wrap gap-4">
        <Field label="이메일">
          <Input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="name@example.com"
          />
        </Field>
        <Field label="초기 비밀번호(8자 이상)">
          <Input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
          />
        </Field>
        <Field label="역할">
          <Select value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABEL[r]}
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

function UserRow({ user }: { user: AdminUser }) {
  const { user: me } = useAuth();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const isSelf = me?.id === user.id;

  function invalidate() {
    queryClient.invalidateQueries({ queryKey: ["users"] });
  }

  const patchMutation = useMutation({
    mutationFn: (role: Role) => patchUser(user.id, { role }),
    onSuccess: () => {
      setError(null);
      invalidate();
    },
    onError: (err) => setError(describeApiError(err)),
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteUser(user.id),
    onSuccess: invalidate,
    onError: (err) => setError(describeApiError(err)),
  });

  return (
    <>
      <Tr className="hover:bg-gray-50 align-top">
        <Td>
          {user.email}
          {isSelf && (
            <Badge tone="neutral" className="ml-2">
              나
            </Badge>
          )}
        </Td>
        <Td>
          <Select
            value={user.role}
            disabled={patchMutation.isPending}
            onChange={(e) => patchMutation.mutate(e.target.value as Role)}
          >
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABEL[r]}
              </option>
            ))}
          </Select>
        </Td>
        <Td>{formatDateTime(user.created_at)}</Td>
        <Td>
          <Button
            size="sm"
            variant="danger"
            disabled={deleteMutation.isPending || isSelf}
            title={isSelf ? "본인 계정은 삭제할 수 없습니다." : undefined}
            onClick={() => deleteMutation.mutate()}
          >
            삭제
          </Button>
        </Td>
      </Tr>
      {error && (
        <Tr>
          <Td colSpan={4} className="text-sm text-tone-danger">
            {error}
          </Td>
        </Tr>
      )}
    </>
  );
}

export default function AdminUsersPage() {
  const { data: users, isLoading, isError } = useQuery({ queryKey: ["users"], queryFn: getUsers });

  return (
    <section className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">사용자 관리</h1>
        <p className="text-sm text-gray-500">
          내부 사용자 계정과 역할(관리자/검토자)을 관리합니다.
        </p>
      </div>

      <CreateUserForm />

      {isLoading && <p className="text-sm text-gray-500">불러오는 중…</p>}
      {isError && <p className="text-sm text-tone-danger">사용자 목록을 불러오지 못했습니다.</p>}

      {users && (
        <Table>
          <Thead>
            <Tr>
              <Th>이메일</Th>
              <Th>역할</Th>
              <Th>가입일</Th>
              <Th />
            </Tr>
          </Thead>
          <Tbody>
            {users.length === 0 && (
              <Tr>
                <Td colSpan={4} className="py-8 text-center text-gray-400">
                  등록된 사용자가 없습니다.
                </Td>
              </Tr>
            )}
            {users.map((user) => (
              <UserRow key={user.id} user={user} />
            ))}
          </Tbody>
        </Table>
      )}
    </section>
  );
}
