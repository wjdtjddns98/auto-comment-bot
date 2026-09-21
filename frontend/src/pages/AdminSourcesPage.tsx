import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiError,
  createSource,
  deleteSource,
  getSnsAccounts,
  getSources,
  patchSource,
  pollSourceNow,
} from "../lib/apiClient";
import { describeApiError } from "../lib/errorMessage";
import {
  SOURCE_TYPE_IMPLEMENTED,
  SOURCE_TYPE_LABEL,
  describePollSummary,
  describePollTruncation,
  describeAuthor,
  describeSourceTarget,
  formatDateTime,
  getGalleryId,
  getRssUrl,
  getSearchQuery,
  getSourceDisplayName,
  getThreadsAccountId,
  HEALTH_TONE,
  sourceConfigKind,
  sourceErrorTitle,
  toPlainText,
} from "../lib/matchDisplay";
import type { PollNowResponse, Source, SourceType } from "../types/api";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Field, Input, Select } from "../components/ui/Input";
import { Table, Tbody, Td, Th, Thead, Tr } from "../components/ui/Table";
import { Toast } from "../components/ui/Toast";

const SOURCE_TYPES: SourceType[] = ["threads", "naver_cafe", "community", "dcinside"];

// 서버가 `gallery_id` 에 거는 제한(영숫자·`_`, 1~40자) — URL 쿼리 주입 차단 목적이라 넘기면
// 422 다. 같은 규칙을 화면에서 먼저 걸러 "추가를 눌렀는데 에러" 대신 입력 중에 알려준다.
const GALLERY_ID_PATTERN = /^[A-Za-z0-9_]{1,40}$/;

function CreateSourceForm() {
  const queryClient = useQueryClient();
  const { data: snsAccounts } = useQuery({ queryKey: ["snsAccounts"], queryFn: getSnsAccounts });
  const [type, setType] = useState<SourceType>("community");
  const [name, setName] = useState("");
  const [rssUrl, setRssUrl] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [galleryId, setGalleryId] = useState("");
  const [threadsAccountId, setThreadsAccountId] = useState<number | "">("");
  const [pollIntervalSec, setPollIntervalSec] = useState(300);
  const [error, setError] = useState<string | null>(null);
  const implemented = SOURCE_TYPE_IMPLEMENTED[type];
  const configKind = sourceConfigKind(type);
  const threadsAccounts = (snsAccounts ?? []).filter((a) => a.platform === "threads");

  const mutation = useMutation({
    mutationFn: createSource,
    onSuccess: () => {
      setName("");
      setRssUrl("");
      setSearchQuery("");
      setGalleryId("");
      setThreadsAccountId("");
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["sources"] });
    },
    onError: (err) => setError(describeApiError(err)),
  });

  function handleSubmit() {
    if (!implemented) return;
    const trimmedName = name.trim();
    if (configKind === "query") {
      if (!searchQuery.trim()) {
        setError("검색어를 입력하세요.");
        return;
      }
      if (type === "threads" && threadsAccountId === "") {
        setError("수집에 사용할 threads 계정을 선택하세요.");
        return;
      }
      mutation.mutate({
        name: trimmedName || undefined,
        type,
        // 네이버 카페는 검색어가 유일한 설정 키다 — 계정 등 다른 키를 얹으면 서버가 422 로 거른다.
        config:
          type === "threads"
            ? { query: searchQuery.trim(), sns_account_id: threadsAccountId }
            : { query: searchQuery.trim() },
        poll_interval_sec: pollIntervalSec,
      });
      return;
    }
    if (configKind === "gallery") {
      const gallery = galleryId.trim();
      if (!GALLERY_ID_PATTERN.test(gallery)) {
        setError("갤러리 ID는 영문·숫자·밑줄(_)만 쓸 수 있고 1~40자여야 합니다.");
        return;
      }
      // 디시인사이드는 갤러리 id 가 설정의 전부다 — 다른 키를 얹으면 422 다(extra=forbid).
      mutation.mutate({
        name: trimmedName || undefined,
        type,
        config: { gallery_id: gallery },
        poll_interval_sec: pollIntervalSec,
      });
      return;
    }
    if (!rssUrl.trim()) {
      setError("RSS URL을 입력하세요.");
      return;
    }
    mutation.mutate({
      name: trimmedName || undefined,
      type,
      config: { rss_url: rssUrl.trim() },
      poll_interval_sec: pollIntervalSec,
    });
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
                {SOURCE_TYPE_IMPLEMENTED[t] ? "" : " (미구현)"}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="이름(선택, ≤100자)">
          <Input
            type="text"
            maxLength={100}
            placeholder="예: 강아지 커뮤니티"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
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
      {configKind === "query" ? (
        <div className="flex flex-col gap-1">
          <div className="flex flex-wrap gap-4">
            <Field label="검색어(1~100자)">
              <Input
                type="text"
                maxLength={100}
                placeholder="예: 강아지 간식"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
            </Field>
            {/* 네이버 카페는 검색 API 가 카페를 지정할 수 없어 검색어 외에 설정할 것이 없다. */}
            {type === "threads" && (
              <Field label="수집 계정(본인 threads 계정)">
                <Select
                  value={threadsAccountId}
                  onChange={(e) => setThreadsAccountId(e.target.value === "" ? "" : Number(e.target.value))}
                >
                  <option value="">계정 선택</option>
                  {threadsAccounts.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.display_name}
                    </option>
                  ))}
                </Select>
              </Field>
            )}
          </div>
          {type === "naver_cafe" && (
            <p className="text-xs text-gray-500">
              검색어로 카페글 전체를 검색합니다(특정 카페 지정 불가). 수집 자격증명은 서버 설정을
              쓰므로 여기서 계정을 고르지 않습니다 — 답변은 전송 대신 클립보드 복사로 처리됩니다.
            </p>
          )}
        </div>
      ) : configKind === "gallery" ? (
        <div className="flex flex-col gap-1">
          <Field label="갤러리 ID(영문·숫자·밑줄, 1~40자)">
            <Input
              type="text"
              maxLength={40}
              placeholder="예: dog"
              value={galleryId}
              onChange={(e) => setGalleryId(e.target.value)}
            />
          </Field>
          <p className="text-xs text-gray-500">
            갤러리 주소의 <code>id=</code> 값입니다(<code>…/board/lists/?id=dog</code> → <code>dog</code>).
            목록 페이지만 읽어 <strong>제목만</strong> 수집하므로 키워드도 제목에서만 걸리고, 게시
            시각은 당일 글에만 붙습니다 — 답변은 전송 대신 클립보드 복사로 처리됩니다.
          </p>
        </div>
      ) : implemented ? (
        <Field label="RSS URL">
          <Input
            type="url"
            placeholder="https://example.com/feed"
            value={rssUrl}
            onChange={(e) => setRssUrl(e.target.value)}
          />
        </Field>
      ) : (
        <p className="text-sm text-gray-500">이 소스 타입은 아직 지원되지 않습니다(어댑터 미구현).</p>
      )}
      {error && <p className="text-sm text-tone-danger">{error}</p>}
      <Button onClick={handleSubmit} disabled={mutation.isPending || !implemented} className="self-start">
        {mutation.isPending ? "추가 중…" : "추가"}
      </Button>
    </Card>
  );
}

/**
 * 수동 검색 결과 패널(이슈 #118).
 *
 * 앱 검수(Threads `threads_keyword_search`)가 요구하는 "검색이 앱 안에서 실행되고 반환된 글이
 * 표시된다"를 한 화면에 담는다 — 그래서 무엇으로 검색했는지(머리말)와 반환된 글 목록을 함께 둔다.
 * 여기 목록은 **어댑터 반환값 전량**이라 키워드에 걸리지 않은 글도 포함된다(검토 큐와 다름).
 */
function PollResultModal({ result, onClose }: { result: PollNowResponse; onClose: () => void }) {
  // 응답에 담겨 온 소스를 쓴다 — 이번 검색으로 갱신된 값이라 목록 캐시보다 최신이다.
  const source = result.source;
  const truncation = describePollTruncation(result.fetched, result.posts.length);
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // 열릴 때 포커스를 패널로 옮긴다 — Esc 로 닫는 흐름과 스크린리더 진입점을 함께 준다.
    dialogRef.current?.focus();
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label="검색 결과"
        className="flex max-h-[85vh] w-full max-w-2xl flex-col gap-4 overflow-y-auto rounded-lg bg-white p-6 shadow-xl outline-none"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex flex-col gap-1">
          <h2 className="text-base font-semibold text-gray-900">검색 결과</h2>
          <p className="break-all text-sm text-gray-500">
            {SOURCE_TYPE_LABEL[source.type] ?? source.type} · {describeSourceTarget(source)}
          </p>
          <p className="text-sm font-medium text-gray-900">
            {describePollSummary(result.fetched, result.stored)}
          </p>
          {truncation && <p className="text-xs text-gray-500">{truncation}</p>}
        </div>

        {result.posts.length === 0 ? (
          <p className="py-8 text-center text-sm text-gray-400">반환된 글 없음</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {result.posts.map((post) => (
              <li
                key={post.external_post_id}
                className="flex flex-col gap-1 rounded-md border border-gray-200 p-3"
              >
                <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                  {/* 네이버 카페는 이 자리에 카페 이름이 온다 — 사람 이름처럼 보이지 않게 갈라 준다. */}
                  <span className="font-medium text-gray-700">
                    {describeAuthor(source.type, post.author, "작성자 미상")}
                  </span>
                  <span>{formatDateTime(post.published_at)}</span>
                  {post.url && (
                    <a
                      href={post.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-brand-600 underline"
                    >
                      원문 보기
                    </a>
                  )}
                </div>
                <p className="whitespace-pre-wrap break-words text-sm text-gray-900">
                  {toPlainText(post.content)}
                </p>
              </li>
            ))}
          </ul>
        )}

        <div className="flex items-center justify-between gap-2">
          {/* 검수 영상에서 "이 데이터가 어떻게 쓰이는지" 로 이어지는 동선 — 검토 큐(대시보드). */}
          <Link to="/" className="text-sm text-brand-600 underline">
            검토 큐로 이동
          </Link>
          <Button variant="secondary" onClick={onClose}>
            닫기
          </Button>
        </div>
      </div>
    </div>
  );
}

function SourceRow({ source }: { source: Source }) {
  const queryClient = useQueryClient();
  const { data: snsAccounts } = useQuery({ queryKey: ["snsAccounts"], queryFn: getSnsAccounts });
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(source.name ?? "");
  const [pollIntervalSec, setPollIntervalSec] = useState(source.poll_interval_sec);
  const [rssUrl, setRssUrl] = useState(() => getRssUrl(source.config));
  const [searchQuery, setSearchQuery] = useState(() => getSearchQuery(source.config));
  const [galleryId, setGalleryId] = useState(() => getGalleryId(source.config));
  const [threadsAccountId, setThreadsAccountId] = useState<number | "">(() =>
    getThreadsAccountId(source.config)
  );
  const [error, setError] = useState<string | null>(null);
  const [deleteBlocked, setDeleteBlocked] = useState(false);
  const [pollResult, setPollResult] = useState<PollNowResponse | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  // 429 의 Retry-After 만큼 남은 초. 0 이 되면 버튼이 다시 열린다.
  const [retryAfterSec, setRetryAfterSec] = useState(0);
  const pollErrorTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const implemented = SOURCE_TYPE_IMPLEMENTED[source.type];
  const isThreads = source.type === "threads";
  const configKind = sourceConfigKind(source.type);
  const threadsAccounts = (snsAccounts ?? []).filter((a) => a.platform === "threads");

  function invalidate() {
    queryClient.invalidateQueries({ queryKey: ["sources"] });
  }

  const patchMutation = useMutation({
    mutationFn: (body: Parameters<typeof patchSource>[1]) => patchSource(source.id, body),
    onSuccess: () => {
      setError(null);
      setDeleteBlocked(false);
      invalidate();
    },
    onError: (err) => setError(describeApiError(err)),
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteSource(source.id),
    onSuccess: () => {
      setDeleteBlocked(false);
      invalidate();
      // 소스 삭제는 매칭 이력·스코프 키워드를 cascade 정리하므로 관련 캐시도 함께 갱신한다.
      queryClient.invalidateQueries({ queryKey: ["matches"] });
      queryClient.invalidateQueries({ queryKey: ["keywords"] });
    },
    // 409 는 전송 진행 중(sending·verify_pending)일 때만 온다 — 발송 이력 보존 목적의 차단은
    // 제거됐다(PR #91). 영구 대안이 아니라 "먼저 멈추고 조정이 끝나면 다시 삭제"를 위한 것이므로
    // 서버 문구를 그대로 노출하고 비활성화 액션을 함께 제시한다.
    onError: (err) => {
      setError(describeApiError(err));
      setDeleteBlocked(err instanceof ApiError && err.status === 409);
    },
  });

  // 수동 검색(이슈 #118). 실패는 서버 detail 을 그대로 토스트로 띄운다 — 429(backoff 중)·
  // 409(같은 소스 검색 진행 중)·502(수집 실패)는 각각 다음 행동이 달라서 뭉개면 안 된다.
  const pollMutation = useMutation({
    mutationFn: () => pollSourceNow(source.id),
    onSuccess: (result) => {
      setPollError(null);
      setPollResult(result);
      // 소스 행의 health·최근 수집 배지와 대시보드 검토 큐가 바로 갱신되게 한다.
      queryClient.invalidateQueries({ queryKey: ["sources"] });
      queryClient.invalidateQueries({ queryKey: ["matches"] });
    },
    onError: (err) => {
      if (pollErrorTimeoutRef.current) clearTimeout(pollErrorTimeoutRef.current);
      setPollError(describeApiError(err));
      pollErrorTimeoutRef.current = setTimeout(() => setPollError(null), 5000);
      // 429 는 "잠시 뒤에 되는" 실패다 — 서버가 알려준 시간만큼 버튼을 잠가 연타를 막는다
      // (불변식 ④). 토스트는 5초 뒤 사라지므로 남은 시간은 버튼 라벨이 계속 보여준다.
      if (err instanceof ApiError && err.status === 429 && err.retryAfterSec) {
        setRetryAfterSec(err.retryAfterSec);
      }
      // 502 는 실패해도 서버가 소스 health·last_error 를 갱신한다 — 행 배지를 맞춰준다.
      queryClient.invalidateQueries({ queryKey: ["sources"] });
    },
  });

  useEffect(() => {
    if (retryAfterSec <= 0) return;
    const timer = setTimeout(() => setRetryAfterSec((sec) => Math.max(0, sec - 1)), 1000);
    return () => clearTimeout(timer);
  }, [retryAfterSec]);

  useEffect(() => {
    return () => {
      if (pollErrorTimeoutRef.current) clearTimeout(pollErrorTimeoutRef.current);
    };
  }, []);

  function handleSave() {
    const body: Parameters<typeof patchSource>[1] = {
      poll_interval_sec: pollIntervalSec,
      name: name.trim() || null,
    };
    if (implemented) {
      if (configKind === "query") {
        if (!searchQuery.trim()) {
          setError("검색어를 입력하세요.");
          return;
        }
        if (isThreads && threadsAccountId === "") {
          setError("수집에 사용할 threads 계정을 선택하세요.");
          return;
        }
        // 네이버 카페는 검색어만 — 다른 키를 함께 보내면 422 다(서버 스키마 extra=forbid).
        body.config = isThreads
          ? { query: searchQuery.trim(), sns_account_id: threadsAccountId }
          : { query: searchQuery.trim() };
      } else if (configKind === "gallery") {
        const gallery = galleryId.trim();
        if (!GALLERY_ID_PATTERN.test(gallery)) {
          setError("갤러리 ID는 영문·숫자·밑줄(_)만 쓸 수 있고 1~40자여야 합니다.");
          return;
        }
        body.config = { gallery_id: gallery };
      } else {
        if (!rssUrl.trim()) {
          setError("RSS URL을 입력하세요.");
          return;
        }
        body.config = { rss_url: rssUrl.trim() };
      }
    }
    patchMutation.mutate(body, { onSuccess: () => setEditing(false) });
  }

  return (
    <>
      <Tr className="hover:bg-gray-50 align-top">
        <Td>{SOURCE_TYPE_LABEL[source.type] ?? source.type}</Td>
        {/* 이름은 이 표에서 유일하게 줄바꿈되는 칸이라, 폭이 모자라면 여기만 쥐어짜인다
            (버튼이 하나 늘어난 뒤 한 글자씩 세로로 쪼개졌다). 최소 폭을 줘서, 좁은 화면에서는
            칸을 쥐어짜는 대신 Table 래퍼의 가로 스크롤(overflow-x-auto)로 넘긴다. */}
        <Td className="min-w-[7rem] max-w-xs">
          {editing ? (
            <Input
              type="text"
              maxLength={100}
              placeholder="이름(선택)"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          ) : (
            <span className="block whitespace-normal break-all text-gray-900">
              {getSourceDisplayName(source)}
            </span>
          )}
        </Td>
        <Td className="max-w-xs">
          {editing && implemented && configKind === "query" ? (
            <div className="flex flex-col gap-1">
              <Input
                type="text"
                maxLength={100}
                placeholder="검색어"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
              {/* 네이버 카페는 계정 설정이 없다 — 수집은 서버 자격증명으로 돈다. */}
              {isThreads && (
                <Select
                  value={threadsAccountId}
                  onChange={(e) => setThreadsAccountId(e.target.value === "" ? "" : Number(e.target.value))}
                >
                  <option value="">계정 선택</option>
                  {threadsAccounts.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.display_name}
                    </option>
                  ))}
                </Select>
              )}
            </div>
          ) : editing && implemented && configKind === "gallery" ? (
            <Input
              type="text"
              maxLength={40}
              placeholder="갤러리 ID(예: dog)"
              value={galleryId}
              onChange={(e) => setGalleryId(e.target.value)}
              className="w-full"
            />
          ) : editing && implemented ? (
            <Input
              type="url"
              value={rssUrl}
              onChange={(e) => setRssUrl(e.target.value)}
              className="w-full"
            />
          ) : isThreads ? (
            <span className="break-all text-xs text-gray-500">
              "{getSearchQuery(source.config)}" · 계정 #{getThreadsAccountId(source.config) || "-"}
            </span>
          ) : configKind === "query" ? (
            <span className="break-all text-xs text-gray-500">"{getSearchQuery(source.config)}"</span>
          ) : configKind === "gallery" ? (
            <span className="break-all text-xs text-gray-500">
              갤러리 {getGalleryId(source.config) || "미설정"}
            </span>
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
        <Td className="whitespace-normal">
          <div className="flex flex-col items-start gap-1">
            <Badge tone={HEALTH_TONE[source.health_status] ?? "neutral"}>
              {source.health_status}
            </Badge>
            {/* 실패 원인(R17) — 전문은 title. 소스 관리 화면이 설정을 고치는 곳이라
                "무엇이 잘못됐는지"가 대시보드보다 여기서 더 필요하다. */}
            {source.last_error && (
              <span
                className="block max-w-[16rem] truncate text-xs text-gray-500"
                title={sourceErrorTitle(source.last_error, source.last_error_at)}
              >
                {source.last_error}
              </span>
            )}
          </div>
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
                {/* 앱 검수용 수동 검색(이슈 #118) — 주기 수집과 무관하게 지금 1회 검색한다.
                    어댑터가 없는 타입은 서버가 502 를 내므로 아예 막는다(현재는 세 타입 모두
                    어댑터가 있어 열려 있다 — naver_cafe 는 백엔드 PR #120 에서 붙었다). */}
                <Button
                  size="sm"
                  disabled={!implemented || pollMutation.isPending || retryAfterSec > 0}
                  onClick={() => pollMutation.mutate()}
                >
                  {pollMutation.isPending
                    ? "검색 중…"
                    : retryAfterSec > 0
                      ? `${retryAfterSec}초 후 재시도`
                      : "지금 검색"}
                </Button>
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
      {/* 모달·토스트는 fixed 라 화면에서는 표 밖에 뜨지만, DOM 상으로는 tbody 안이라
          빈 셀(p-0)에 담아 표 구조를 깨지 않게 한다. */}
      {(pollResult || pollError) && (
        <Tr>
          <Td colSpan={8} className="p-0">
            {pollResult && (
              <PollResultModal result={pollResult} onClose={() => setPollResult(null)} />
            )}
            {pollError && <Toast message={pollError} tone="danger" />}
          </Td>
        </Tr>
      )}
      {error && (
        <Tr>
          <Td colSpan={8} className="text-sm text-tone-danger">
            <div className="flex flex-wrap items-center gap-2">
              <span>{error}</span>
              {deleteBlocked && source.enabled && (
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={patchMutation.isPending}
                  onClick={() => patchMutation.mutate({ enabled: false })}
                >
                  먼저 비활성화
                </Button>
              )}
            </div>
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
        <p className="text-sm text-gray-500">
          Threads/네이버 카페/커뮤니티/디시인사이드 소스를 등록하고 관리합니다.
        </p>
      </div>

      <CreateSourceForm />

      {isLoading && <p className="text-sm text-gray-500">불러오는 중…</p>}
      {isError && <p className="text-sm text-tone-danger">소스 목록을 불러오지 못했습니다.</p>}

      {sources && (
        <Table>
          <Thead>
            <Tr>
              <Th>타입</Th>
              <Th>이름</Th>
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
                <Td colSpan={8} className="py-8 text-center text-gray-400">
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
