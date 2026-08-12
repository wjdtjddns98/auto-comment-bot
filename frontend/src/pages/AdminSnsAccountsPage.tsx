import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiError,
  connectThreadsOAuth,
  createSnsAccount,
  deleteSnsAccount,
  getSnsAccounts,
  getThreadsOAuthAuthorizeUrl,
  updateSnsAccountCredentials,
} from "../lib/apiClient";
import { describeApiError } from "../lib/errorMessage";
import { formatDateTime, SOURCE_TYPE_LABEL } from "../lib/matchDisplay";
import { useAuth } from "../hooks/useAuth";
import type { SnsAccount, SnsPlatform } from "../types/api";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Field, Input, Select, Textarea } from "../components/ui/Input";
import { Table, Tbody, Td, Th, Thead, Tr } from "../components/ui/Table";

const PLATFORMS: SnsPlatform[] = ["threads", "naver_cafe", "community"];

// 콜백 페이지가 표시하는 연동 값(`code=...&state=...`) 또는 전체 콜백 URL 붙여넣기를
// code/state 로 분리한다(docs/API-SPEC.md §threads-oauth — state 는 서버 검증 필수).
// `?` 뒤를 쿼리로 보므로 전체 URL·순수 페이로드 모두 수용한다.
function parseThreadsOAuthInput(raw: string): { code: string; state: string } | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const query = trimmed.includes("?") ? trimmed.slice(trimmed.indexOf("?") + 1) : trimmed;
  const params = new URLSearchParams(query);
  const code = params.get("code")?.trim() ?? "";
  const state = params.get("state")?.trim() ?? "";
  if (code && state) return { code, state };
  return null;
}

function ThreadsOAuthConnectForm() {
  const queryClient = useQueryClient();
  const [code, setCode] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  // 팝업이 차단돼 window.open 이 실패하면 수동으로 열 수 있게 URL 을 노출한다.
  const [authorizeUrl, setAuthorizeUrl] = useState<string | null>(null);

  const authorizeMutation = useMutation({
    mutationFn: getThreadsOAuthAuthorizeUrl,
    onSuccess: ({ url }) => {
      setError(null);
      const opened = window.open(url, "_blank", "noopener,noreferrer");
      setAuthorizeUrl(opened ? null : url);
    },
    onError: (err) => setError(describeApiError(err)),
  });

  const connectMutation = useMutation({
    mutationFn: connectThreadsOAuth,
    onSuccess: () => {
      setCode("");
      setDisplayName("");
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["snsAccounts"] });
    },
    onError: (err) => setError(describeApiError(err)),
  });

  function handleConnect() {
    const parsed = parseThreadsOAuthInput(code);
    if (!parsed) {
      setError("콜백 페이지의 연동 값(code=...&state=...) 또는 전체 URL을 붙여넣으세요.");
      return;
    }
    connectMutation.mutate({
      code: parsed.code,
      state: parsed.state,
      display_name: displayName.trim() || undefined,
    });
  }

  return (
    <Card className="flex flex-col gap-4">
      <div>
        <h2 className="text-sm font-semibold text-gray-900">Threads 동의 화면으로 연동</h2>
        <p className="text-xs text-gray-500">
          동의 화면을 새 창으로 열어 승인하면 콜백 페이지에 연동 값이 표시됩니다. 그 값을 통째로
          복사해 아래에 붙여넣으세요. 값은 일회용이며 곧 만료됩니다.
        </p>
        <p className="text-xs text-gray-400">
          이 도구는 키워드 매칭 글을 사람이 검토·승인한 뒤에만 답글을 전송합니다 — 자동 게시는
          하지 않습니다. 부여한 권한은 답글 게시·조회 등 승인된 작업에만 사용됩니다.
        </p>
      </div>
      <Button
        variant="secondary"
        className="self-start"
        onClick={() => authorizeMutation.mutate()}
        disabled={authorizeMutation.isPending}
      >
        {authorizeMutation.isPending ? "여는 중…" : "Threads로 연결"}
      </Button>
      {authorizeUrl && (
        <p className="text-xs text-gray-500">
          팝업이 차단된 것 같습니다.{" "}
          <a href={authorizeUrl} target="_blank" rel="noopener noreferrer" className="underline">
            여기를 눌러 동의 화면 열기
          </a>
          .
        </p>
      )}
      <div className="flex flex-wrap gap-4">
        <Field label="연동 값">
          <Input
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="콜백 페이지의 code=...&state=... 또는 전체 URL"
          />
        </Field>
        <Field label="표시 이름 (선택)">
          <Input
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="예: @nutti_official"
          />
        </Field>
      </div>
      {error && <p className="text-sm text-tone-danger">{error}</p>}
      <Button onClick={handleConnect} disabled={connectMutation.isPending} className="self-start">
        {connectMutation.isPending ? "연동 중…" : "연동 완료"}
      </Button>
    </Card>
  );
}

function CreateSnsAccountForm() {
  const queryClient = useQueryClient();
  const [platform, setPlatform] = useState<SnsPlatform>("threads");
  const [displayName, setDisplayName] = useState("");
  const [accessToken, setAccessToken] = useState("");
  const [credentials, setCredentials] = useState("{}");
  const [error, setError] = useState<string | null>(null);
  const isThreads = platform === "threads";

  const mutation = useMutation({
    mutationFn: createSnsAccount,
    onSuccess: () => {
      setDisplayName("");
      setAccessToken("");
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
    if (isThreads) {
      if (!accessToken.trim()) {
        setError("액세스 토큰을 입력하세요.");
        return;
      }
      mutation.mutate({
        platform,
        display_name: displayName,
        credentials: { access_token: accessToken.trim() },
      });
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
            placeholder="예: @nutti_official"
          />
        </Field>
      </div>
      {isThreads ? (
        <Field label="액세스 토큰">
          <Input
            type="password"
            autoComplete="off"
            placeholder="threads 액세스 토큰"
            value={accessToken}
            onChange={(e) => setAccessToken(e.target.value)}
          />
        </Field>
      ) : (
        <Field label="자격증명 (JSON)">
          <Textarea rows={3} value={credentials} onChange={(e) => setCredentials(e.target.value)} />
        </Field>
      )}
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

// 토큰 교체 입력부 — 데스크톱은 표 하단 확장 행, 모바일은 카드 안쪽에 그대로 얹는다.
function ReplaceCredentialsFields({
  account,
  onDone,
}: {
  account: SnsAccount;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const [accessToken, setAccessToken] = useState("");
  const [credentials, setCredentials] = useState("{}");
  const [error, setError] = useState<string | null>(null);
  const isThreads = account.platform === "threads";

  const mutation = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      updateSnsAccountCredentials(account.id, { credentials: body }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["snsAccounts"] });
      onDone();
    },
    onError: (err) => setError(describeApiError(err)),
  });

  function handleSubmit() {
    if (isThreads) {
      if (!accessToken.trim()) {
        setError("액세스 토큰을 입력하세요.");
        return;
      }
      mutation.mutate({ access_token: accessToken.trim() });
      return;
    }
    let parsedCredentials: Record<string, unknown>;
    try {
      parsedCredentials = JSON.parse(credentials);
    } catch {
      setError("자격증명은 올바른 JSON이어야 합니다.");
      return;
    }
    mutation.mutate(parsedCredentials);
  }

  return (
    <div className="flex flex-wrap items-end gap-3 py-2">
      {isThreads ? (
        <Field label="새 액세스 토큰">
          <Input
            type="password"
            autoComplete="off"
            placeholder="threads 액세스 토큰"
            value={accessToken}
            onChange={(e) => setAccessToken(e.target.value)}
          />
        </Field>
      ) : (
        <Field label="새 자격증명 (JSON)">
          <Textarea rows={3} value={credentials} onChange={(e) => setCredentials(e.target.value)} />
        </Field>
      )}
      <div className="flex gap-2">
        <Button size="sm" onClick={handleSubmit} disabled={mutation.isPending}>
          {mutation.isPending ? "교체 중…" : "교체"}
        </Button>
        <Button size="sm" variant="ghost" onClick={onDone} disabled={mutation.isPending}>
          취소
        </Button>
      </div>
      {error && <p className="text-sm text-tone-danger">{error}</p>}
    </div>
  );
}

export default function AdminSnsAccountsPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const queryClient = useQueryClient();
  const { data: accounts, isLoading, isError } = useQuery({
    queryKey: ["snsAccounts"],
    queryFn: getSnsAccounts,
  });
  // blocked: 참조 소스 존재로 삭제가 막힌 409(R15) — 서버 문구에 소스 id 가 들어 있어
  // 그대로 노출하고, 조치할 화면(소스 관리)으로 가는 길만 덧붙인다.
  const [rowError, setRowError] = useState<{ id: number; message: string; blocked: boolean } | null>(
    null
  );
  const [replacingId, setReplacingId] = useState<number | null>(null);

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteSnsAccount(id),
    onSuccess: () => {
      setRowError(null);
      queryClient.invalidateQueries({ queryKey: ["snsAccounts"] });
    },
    onError: (err, id) =>
      setRowError({
        id,
        message: describeApiError(err),
        blocked: err instanceof ApiError && err.status === 409,
      }),
  });

  // 안내는 카드(모바일)·표(데스크톱) 양쪽에서 같아야 한다.
  function renderDeleteError(accountId: number) {
    if (rowError?.id !== accountId) return null;
    return (
      <div className="text-sm text-tone-danger">
        <p>{rowError.message}</p>
        {rowError.blocked && isAdmin && (
          <p className="mt-0.5 text-xs text-gray-500">
            <Link to="/admin/sources" className="text-brand-600 hover:underline">
              소스 관리
            </Link>
            에서 해당 소스를 삭제하거나 다른 계정으로 바꾼 뒤 다시 시도하세요. 비활성 소스도
            참조로 셉니다.
          </p>
        )}
        {rowError.blocked && !isAdmin && (
          <p className="mt-0.5 text-xs text-gray-500">
            소스 설정은 관리자만 바꿀 수 있습니다 — 관리자에게 해당 소스 정리를 요청하세요.
            비활성 소스도 참조로 셉니다.
          </p>
        )}
      </div>
    );
  }

  const columnCount = isAdmin ? 6 : 5;

  return (
    <section className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">SNS 계정</h1>
        <p className="text-sm text-gray-500">
          {isAdmin
            ? "전체 사용자의 SNS 계정을 조회합니다. 등록·삭제는 각자 자신의 계정만 가능합니다."
            : "답변 전송에 사용할 내 SNS 계정을 등록·삭제합니다."}{" "}
          자격증명은 어떤 응답에도 포함되지 않습니다.
        </p>
      </div>

      <ThreadsOAuthConnectForm />
      <CreateSnsAccountForm />

      {isLoading && <p className="text-sm text-gray-500">불러오는 중…</p>}
      {isError && <p className="text-sm text-tone-danger">SNS 계정 목록을 불러오지 못했습니다.</p>}

      {accounts && (
        <>
          {/* 모바일: 6열 표는 좁은 폭에서 가로 오버플로 — 계정당 카드 한 장으로 접는다 */}
          <div className="flex flex-col gap-3 md:hidden">
            {accounts.length === 0 && (
              <Card className="py-8 text-center text-sm text-gray-400">
                등록된 SNS 계정이 없습니다.
              </Card>
            )}
            {accounts.map((account) => {
              const canManage = !isAdmin || account.user_id === user?.id;
              return (
                <Card key={account.id} className="flex flex-col gap-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate font-medium text-gray-900">{account.display_name}</p>
                      <p className="text-xs text-gray-500">
                        {SOURCE_TYPE_LABEL[account.platform] ?? account.platform}
                      </p>
                    </div>
                    <Badge tone={account.status === "active" ? "success" : "warning"}>
                      {account.status}
                    </Badge>
                  </div>
                  <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
                    {isAdmin && (
                      <>
                        <dt className="text-gray-500">소유자</dt>
                        <dd className="text-gray-700">
                          {account.user_id === user?.id ? "나" : `사용자 #${account.user_id}`}
                        </dd>
                      </>
                    )}
                    <dt className="text-gray-500">토큰 만료</dt>
                    <dd className="text-gray-700">{formatDateTime(account.token_expires_at)}</dd>
                  </dl>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="secondary"
                      className="flex-1"
                      disabled={!canManage}
                      onClick={() =>
                        setReplacingId((cur) => (cur === account.id ? null : account.id))
                      }
                    >
                      토큰 교체
                    </Button>
                    <Button
                      size="sm"
                      variant="danger"
                      className="flex-1"
                      disabled={deleteMutation.isPending || !canManage}
                      onClick={() => deleteMutation.mutate(account.id)}
                    >
                      삭제
                    </Button>
                  </div>
                  {replacingId === account.id && (
                    <div className="rounded-md bg-gray-50 px-3">
                      <ReplaceCredentialsFields
                        account={account}
                        onDone={() => setReplacingId(null)}
                      />
                    </div>
                  )}
                  {renderDeleteError(account.id)}
                </Card>
              );
            })}
          </div>

          <div className="hidden md:block">
            <Table>
              <Thead>
                <Tr>
                  <Th>플랫폼</Th>
                  <Th>표시 이름</Th>
                  {isAdmin && <Th>소유자</Th>}
                  <Th>상태</Th>
                  <Th>토큰 만료</Th>
                  <Th />
                </Tr>
              </Thead>
              <Tbody>
                {accounts.length === 0 && (
                  <Tr>
                    <Td colSpan={columnCount} className="py-8 text-center text-gray-400">
                      등록된 SNS 계정이 없습니다.
                    </Td>
                  </Tr>
                )}
                {accounts.map((account) => (
                  <Fragment key={account.id}>
                    <Tr className="hover:bg-gray-50">
                      <Td>{SOURCE_TYPE_LABEL[account.platform] ?? account.platform}</Td>
                      <Td>{account.display_name}</Td>
                      {isAdmin && (
                        <Td>
                          {account.user_id === user?.id ? "나" : `사용자 #${account.user_id}`}
                        </Td>
                      )}
                      <Td>
                        <Badge tone={account.status === "active" ? "success" : "warning"}>
                          {account.status}
                        </Badge>
                      </Td>
                      <Td>{formatDateTime(account.token_expires_at)}</Td>
                      <Td>
                        <div className="flex justify-end gap-2">
                          <Button
                            size="sm"
                            variant="secondary"
                            disabled={isAdmin && account.user_id !== user?.id}
                            onClick={() =>
                              setReplacingId((cur) => (cur === account.id ? null : account.id))
                            }
                          >
                            토큰 교체
                          </Button>
                          <Button
                            size="sm"
                            variant="danger"
                            disabled={
                              deleteMutation.isPending || (isAdmin && account.user_id !== user?.id)
                            }
                            onClick={() => deleteMutation.mutate(account.id)}
                          >
                            삭제
                          </Button>
                        </div>
                      </Td>
                    </Tr>
                    {replacingId === account.id && (
                      <Tr>
                        <Td colSpan={columnCount} className="bg-gray-50">
                          <ReplaceCredentialsFields
                            account={account}
                            onDone={() => setReplacingId(null)}
                          />
                        </Td>
                      </Tr>
                    )}
                    {rowError?.id === account.id && (
                      <Tr>
                        <Td colSpan={columnCount}>{renderDeleteError(account.id)}</Td>
                      </Tr>
                    )}
                  </Fragment>
                ))}
              </Tbody>
            </Table>
          </div>
        </>
      )}
    </section>
  );
}
