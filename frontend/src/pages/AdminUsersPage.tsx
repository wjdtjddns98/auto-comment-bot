// /api/users 는 2026-08-12 확정(#102 / PR #106) — docs/API-SPEC.md §사용자 관리.
// 서버와 다른 두 지점만 화면에서 처리한다:
//  - 자기 강등은 (다른 admin 이 있으면) 허용되고 재로그인 없이 즉시 적용된다 → 확인 + 세션 갱신.
//  - 삭제는 소유 리소스·이력이 있으면 409 로 막히고 이유가 문장으로 온다 → detail 을 그대로 노출.
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

// 행/카드 두 표현이 같은 상태를 쓰도록 뮤테이션·에러를 훅으로 뺀다.
function useUserActions(user: AdminUser) {
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
      // 서버는 역할을 매 요청 DB 에서 읽는다 — 자기 강등은 즉시 유효하다. 세션 캐시의 role 이
      // 낡은 채로 남으면 admin 화면에 머물러 이후 요청이 전부 403 이 된다. 갱신하면
      // AdminRoute 가 대시보드로 돌려보낸다.
      if (isSelf) queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
    },
    onError: (err) => setError(describeApiError(err)),
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteUser(user.id),
    onSuccess: invalidate,
    onError: (err) => setError(describeApiError(err)),
  });

  // 자기 강등은 되돌릴 방법이 화면에 없다(강등 직후 이 화면에서 밀려난다) — 한 번 묻는다.
  function changeRole(next: Role) {
    if (isSelf && next !== "admin") {
      const ok = window.confirm(
        "본인 역할을 검토자로 낮춥니다. 즉시 적용되어 관리 화면에서 밀려나고," +
          " 되돌리려면 다른 관리자가 필요합니다. 계속할까요?"
      );
      if (!ok) return;
    }
    patchMutation.mutate(next);
  }

  return { error, isSelf, patchMutation, deleteMutation, changeRole };
}

function UserCard({ user }: { user: AdminUser }) {
  const { error, isSelf, patchMutation, deleteMutation, changeRole } = useUserActions(user);

  return (
    <Card className="flex flex-col gap-3">
      <div className="min-w-0">
        <p className="truncate font-medium text-gray-900">
          {user.email}
          {isSelf && (
            <Badge tone="neutral" className="ml-2">
              나
            </Badge>
          )}
        </p>
        <p className="text-xs text-gray-500">가입 {formatDateTime(user.created_at)}</p>
      </div>
      <Field label="역할">
        <Select
          value={user.role}
          disabled={patchMutation.isPending}
          onChange={(e) => changeRole(e.target.value as Role)}
        >
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {ROLE_LABEL[r]}
            </option>
          ))}
        </Select>
      </Field>
      <Button
        size="sm"
        variant="danger"
        className="self-start"
        disabled={deleteMutation.isPending || isSelf}
        title={
          isSelf
            ? "본인 계정은 삭제할 수 없습니다."
            : "소유 리소스·승인 이력이 없는 계정만 삭제할 수 있습니다."
        }
        onClick={() => deleteMutation.mutate()}
      >
        삭제
      </Button>
      {error && <p className="text-sm text-tone-danger">{error}</p>}
    </Card>
  );
}

function UserRow({ user }: { user: AdminUser }) {
  const { error, isSelf, patchMutation, deleteMutation, changeRole } = useUserActions(user);

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
            onChange={(e) => changeRole(e.target.value as Role)}
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
            title={
              isSelf
                ? "본인 계정은 삭제할 수 없습니다."
                : "소유 리소스·승인 이력이 없는 계정만 삭제할 수 있습니다."
            }
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
        {/* 삭제가 거의 항상 막히는 이유를 실패 후에야 알게 되는 걸 피한다 — 실제 사용 중단 수단은 강등이다. */}
        <p className="mt-1 text-sm text-gray-500">
          삭제는 소유한 소스·키워드·템플릿·SNS 계정과 승인 이력이 <strong>모두 없는 계정</strong>에만
          됩니다. 이미 활동한 계정은 역할을 <strong>검토자</strong>로 낮춰 사용을 중단시키세요.
        </p>
      </div>

      <CreateUserForm />

      {isLoading && <p className="text-sm text-gray-500">불러오는 중…</p>}
      {isError && <p className="text-sm text-tone-danger">사용자 목록을 불러오지 못했습니다.</p>}

      {users && (
        <>
          {/* 모바일: 표 대신 카드 — 이메일이 길어 좁은 폭에서 가로 오버플로가 난다 */}
          <div className="flex flex-col gap-3 md:hidden">
            {users.length === 0 && (
              <Card className="py-8 text-center text-sm text-gray-400">
                등록된 사용자가 없습니다.
              </Card>
            )}
            {users.map((user) => (
              <UserCard key={user.id} user={user} />
            ))}
          </div>

          <div className="hidden md:block">
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
          </div>
        </>
      )}
    </section>
  );
}
