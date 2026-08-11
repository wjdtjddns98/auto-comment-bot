import { Fragment, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
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
import { THREADS_OAUTH_DISPLAY_NAME_KEY } from "../lib/threadsOAuth";

const PLATFORMS: SnsPlatform[] = ["threads", "naver_cafe", "community"];

// 서버가 준 동의 화면 URL 의 redirect_uri 가 이 앱을 가리키면 콜백 라우트가 연동을 자동으로
// 끝낸다 → 같은 탭에서 이동한다(팝업 차단·복붙 없음). 아직 외부 정적 콜백 페이지를 가리키면
// 기존대로 새 창으로 열고 연동 값을 붙여넣게 둔다. 백엔드 THREADS_REDIRECT_URI 설정만으로
// 두 경로가 갈리므로 FE 는 배포 순서와 무관하게 동작한다.
function usesInAppCallback(authorizeUrl: string): boolean {
  try {
    const redirect = new URL(authorizeUrl).searchParams.get("redirect_uri");
    if (!redirect) return false;
    return new URL(redirect, window.location.origin).origin === window.location.origin;
  } catch {
    return false;
  }
}

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
  // 서버가 외부 콜백 페이지를 가리키는 배포 — 승인 뒤 연동 값을 손으로 옮겨야 한다.
  // 판정 결과를 렌더에 남겨 안내 문구와 수동 입력 영역 펼침을 그 경로에 맞춘다.
  const [manualHandoff, setManualHandoff] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);
  // 붙여넣기 경로에도 자동 콜백과 같은 완료 확인을 남긴다(어느 계정이 붙었는지 화면에 드러남).
  const [connected, setConnected] = useState<SnsAccount | null>(null);

  const authorizeMutation = useMutation({
    mutationFn: getThreadsOAuthAuthorizeUrl,
    onSuccess: ({ url }) => {
      setError(null);
      setConnected(null);
      if (usesInAppCallback(url)) {
        // 전체 페이지 이동이라 폼 상태가 끊긴다 — 표시 이름만 콜백까지 넘긴다.
        const name = displayName.trim();
        if (name) sessionStorage.setItem(THREADS_OAUTH_DISPLAY_NAME_KEY, name);
        else sessionStorage.removeItem(THREADS_OAUTH_DISPLAY_NAME_KEY);
        window.location.assign(url);
        return;
      }
      setManualHandoff(true);
      setManualOpen(true);
      const opened = window.open(url, "_blank", "noopener,noreferrer");
      setAuthorizeUrl(opened ? null : url);
    },
    onError: (err) => setError(describeApiError(err)),
  });

  const connectMutation = useMutation({
    mutationFn: connectThreadsOAuth,
    onSuccess: (account) => {
      setCode("");
      setDisplayName("");
      setError(null);
      setConnected(account);
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
          Threads 동의 화면에서 승인하면 연동이 이어집니다. 표시 이름을 정해 두려면 연결을
          시작하기 전에 먼저 입력하세요.
        </p>
        <p className="text-xs text-gray-400">
          이 도구는 키워드 매칭 글을 사람이 검토·승인한 뒤에만 답글을 전송합니다 — 자동 게시는
          하지 않습니다. 부여한 권한은 답글 게시·조회 등 승인된 작업에만 사용됩니다.
        </p>
      </div>
      <div className="flex flex-wrap gap-4">
        <Field label="표시 이름 (선택)">
          <Input
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="예: @nutti_official"
          />
        </Field>
      </div>
      <p className="-mt-2 text-xs text-gray-500">
        비워두면 연결된 Threads 계정의 username 이 그대로 표시 이름이 됩니다.
      </p>
      <Button
        variant="secondary"
        className="self-start"
        onClick={() => authorizeMutation.mutate()}
        disabled={authorizeMutation.isPending}
      >
        {authorizeMutation.isPending ? "여는 중…" : "Threads로 연결"}
      </Button>
      {/* 팝업이 떴는지 차단됐는지에 따라 첫 문장만 갈리고, 그 뒤 수동 운반 안내는 공통이다.
          두 문구를 따로 쓰면 차단 시 "새 창으로 열었습니다"와 "차단된 것 같습니다"가 함께 뜬다. */}
      {manualHandoff && (
        <p className="text-xs text-gray-500">
          {authorizeUrl ? (
            <>
              팝업이 차단된 것 같습니다.{" "}
              <a
                href={authorizeUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="underline"
              >
                여기를 눌러 동의 화면 열기
              </a>
              .{" "}
            </>
          ) : (
            "동의 화면을 새 창으로 열었습니다. "
          )}
          승인하면 콜백 페이지에 연동 값이 표시됩니다 — 그 값을 통째로 복사해 아래{" "}
          <strong>수동 입력</strong> 칸에 붙여넣고 “연동 완료”를 누르세요. 값은 일회용이며 곧
          만료됩니다.
        </p>
      )}
      {connected && (
        <p className="text-sm text-tone-success">
          <strong>{connected.display_name}</strong> 계정을 연동했습니다. 아래 목록에서 확인할 수
          있습니다.
        </p>
      )}
      {error && <p className="text-sm text-tone-danger">{error}</p>}
      {/* 콜백이 아직 외부 정적 페이지를 가리키는 배포에서는 연동 값을 손으로 옮겨야 한다.
          자동 콜백으로 전환된 뒤에도 승인 직후 창이 닫히는 등의 사고를 위한 수동 경로로 남긴다.
          외부 콜백 배포에서는 이 경로가 유일한 완료 수단이라, 연결 시작과 동시에 펼친다. */}
      <details
        className="border-t border-gray-200 pt-3"
        open={manualOpen}
        onToggle={(e) => setManualOpen(e.currentTarget.open)}
      >
        <summary className="cursor-pointer text-xs text-gray-500">
          콜백 페이지에서 연동 값을 안내받았나요? (수동 입력)
        </summary>
        <div className="mt-3 flex flex-col gap-3">
          <Field label="연동 값">
            <Input
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="콜백 페이지의 code=...&state=... 또는 전체 URL"
            />
          </Field>
          <Button
            onClick={handleConnect}
            disabled={connectMutation.isPending}
            className="self-start"
          >
            {connectMutation.isPending ? "연동 중…" : "연동 완료"}
          </Button>
        </div>
      </details>
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

function ReplaceCredentialsForm({
  account,
  columnCount,
  onDone,
}: {
  account: SnsAccount;
  columnCount: number;
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
    <Tr>
      <Td colSpan={columnCount} className="bg-gray-50">
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
      </Td>
    </Tr>
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
  const [rowError, setRowError] = useState<{ id: number; message: string } | null>(null);
  const [replacingId, setReplacingId] = useState<number | null>(null);

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteSnsAccount(id),
    onSuccess: () => {
      setRowError(null);
      queryClient.invalidateQueries({ queryKey: ["snsAccounts"] });
    },
    onError: (err, id) => setRowError({ id, message: describeApiError(err) }),
  });

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
                        disabled={deleteMutation.isPending || (isAdmin && account.user_id !== user?.id)}
                        onClick={() => deleteMutation.mutate(account.id)}
                      >
                        삭제
                      </Button>
                    </div>
                  </Td>
                </Tr>
                {replacingId === account.id && (
                  <ReplaceCredentialsForm
                    account={account}
                    columnCount={columnCount}
                    onDone={() => setReplacingId(null)}
                  />
                )}
                {rowError?.id === account.id && (
                  <Tr>
                    <Td colSpan={columnCount} className="text-sm text-tone-danger">
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
