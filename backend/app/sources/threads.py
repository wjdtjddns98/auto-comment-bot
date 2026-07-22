"""Threads(Meta) 어댑터 — 키워드 수집(read) + 답글 전송(write) + 조정 조회.

공식 Threads API 만 사용한다(불변식 ④). 인증 토큰은 sns_account_secrets 의
Fernet 암호문을 전송/수집 시점에만 복호해 **Authorization 헤더로만** 보낸다 —
URL·예외 메시지·로그에 절대 싣지 않는다(불변식 ③, M2 조정 설계 R7).

전송 실패 분류(M2 조정 설계 §3.2 — 단계별 분류):
- 컨테이너 생성 단계의 모든 실패 → SendError(확정 실패, 게시 위험 없음)
- publish 단계: 401/403/429 + 400 중 유효성/OAuth 계열 Meta code 만 → SendError,
  그 외 전부(5xx·408·409·목록 밖 4xx·transient 400·타임아웃·커넥션 오류)
  → SendOutcomeUnknown(container_id 보존)
- publish 400 code=24(컨테이너 준비 전)는 같은 creation_id 로 짧게 재시도(재호출은
  idempotent — R3 실측 2026-07-22, 설계 §1). 소진 시엔 결과 불명 — "리소스 없음"이
  발행 미수행을 보장한다는 공식 계약이 없어(N=1 관찰) 조정 안전망으로 넘긴다

config: {"query": "...", "sns_account_id": N} — 수집 인증에 쓸 본인 threads 계정.
"""
import asyncio
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app import crypto
from app.models import AccountStatus, Platform, SnsAccount, SnsAccountSecret, Source
from app.sources.base import (
    FetchedPost,
    FetchedReply,
    FetchError,
    RateLimitedError,
    SendError,
    SendOutcomeUnknown,
)

_API = "https://graph.threads.net/v1.0"
_TIMEOUT = 15.0
_UA = "sns-keyword-monitor/0.1 (polite; contact admin)"
# Threads 텍스트 게시 상한(공식 500자). ApproveIn 상한(2000자)보다 좁다 —
# 초과분은 컨테이너 생성 전에 확정 실패로 거른다.
MAX_TEXT_LEN = 500
_MAX_CONTENT_CHARS = 20_000  # 수집 본문 상한(rss 와 동일)
_REPLIES_PAGE_CAP = 10  # 조정 조회 cursor 순회 상한(폭주 방어)

# publish 단계에서 "요청이 수행되지 않았음이 명확"한 상태코드 — 그 외 응답/무응답은
# 전부 결과 불명. 목록을 임의로 넓히지 않는다(fail-safe: 확정 실패는 증명 책임을 진다).
# 400 은 제외: Meta 계열은 일시 오류(error code 1·2)도 HTTP 400 으로 내려보내는 사례가
# 있어 code 기반으로 세분한다(_publish_400_is_definite — 어댑터 1차 적대 리뷰 중요-1).
_PUBLISH_DEFINITE_FAIL = {401, 403, 429}
# HTTP 400 중 "요청 미수행 명확"으로 취급하는 Meta error code — 유효성/OAuth/권한 계열.
# 목록 밖(특히 1 "API Unknown"·2 "API Service") 은 결과 불명으로 남긴다.
# 24("미디어를 찾을 수 없음")는 넣지 않는다 — "리소스 없음 = 발행 미수행"은 N=1 실측
# 관찰일 뿐 공식 계약이 아니고, 반례(발행 커밋 후 replica lag 로 24 응답)가 하나라도
# 있으면 확정 실패→verify_meta 폐기→새 컨테이너 재시도 경로가 이중 게시가 된다
# (독립 리뷰 blocker). 소진 시 결과 불명 → 기존 조정 안전망(§3.4)이 받는다.
_DEFINITE_400_CODES = {100, 190, 200, 10}
# 생성 직후의 publish 는 컨테이너 준비 전이라 400 code=24 로 거부될 수 있다(R3 실측:
# 즉시 호출 400 code=24/subcode 4279009 → 3초 뒤 같은 creation_id 로 성공. 매치는
# top-level code 로만 — 관찰 튜플보다 넓지만 오매치의 최악이 "+6초 후 결과 불명"이라
# 안전). 같은 creation_id 재호출은 발행 완료 후에도 같은 media id 를 돌려주는 사실상
# idempotent 동작(같은 실측)이므로 짧은 재시도는 이중 게시를 만들 수 없다.
# 예산 2s+4s: approve 는 사람이 기다리는 동기 요청 — 공식 30초 처리 지연 권장치를 다
# 기다리는 대신 소진 시 조정(verify_pending)으로 넘긴다. SEND_TIMEOUT_SEC(120s) 내.
_PUBLISH_NOT_READY_CODE = 24
_PUBLISH_RETRY_DELAYS = (2.0, 4.0)


def _client() -> httpx.AsyncClient:
    # 테스트가 이 팩토리를 교체해 MockTransport 를 주입한다.
    return httpx.AsyncClient(base_url=_API, timeout=_TIMEOUT, headers={"User-Agent": _UA})


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _error_code(resp: httpx.Response) -> int | None:
    try:
        return int(resp.json().get("error", {}).get("code"))
    except Exception:  # noqa: BLE001 - 비 JSON/형식 이상
        return None


def _error_summary(resp: httpx.Response) -> str:
    """응답 에러를 audit 안전 텍스트로 — Meta 에러 JSON 의 code/type 만 쓰고
    message(요청 원문 echo 가능)는 서버 로그로도 보내지 않는다."""
    try:
        err = resp.json().get("error", {})
        detail = f" (code={err.get('code')}, type={err.get('type')})"
    except Exception:  # noqa: BLE001 - 비 JSON 응답
        detail = ""
    return f"Threads API HTTP {resp.status_code}{detail}"


def _publish_definite_fail(resp: httpx.Response) -> bool:
    """publish 응답이 "요청 미수행 명확"(확정 실패)인가 — 아니면 결과 불명."""
    if resp.status_code in _PUBLISH_DEFINITE_FAIL:
        return True
    if resp.status_code == 400:
        code = _error_code(resp)
        return code in _DEFINITE_400_CODES
    return False


async def _load_token(account: SnsAccount | None, exc_cls: type[Exception]) -> str:
    """계정 → access_token. 실패는 exc_cls(전송 경로 SendError / 수집 경로 FetchError) —
    메시지에 토큰·암호문을 절대 싣지 않는다."""
    if account is None:
        raise exc_cls("SNS 계정이 지정되지 않았습니다")
    secret = await SnsAccountSecret.get_or_none(account_id=account.id)
    if secret is None:
        raise exc_cls("계정 자격증명이 등록되어 있지 않습니다")
    try:
        creds = crypto.decrypt_credentials(secret.encrypted_credentials)
    except Exception as exc:  # noqa: BLE001 - 키 미설정/회전 실패 등
        raise exc_cls("자격증명 복호화 실패 — 암호화 키 설정을 확인하세요") from exc
    token = creds.get("access_token")
    if not token or not isinstance(token, str):
        raise exc_cls("자격증명에 access_token 이 없습니다")
    return token


def _parse_ts(value) -> datetime | None:
    """Threads timestamp(예: 2026-07-21T09:00:00+0000) → aware datetime."""
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


class ThreadsConfig(BaseModel):
    """threads 소스 config 스키마 — 등록/수정 시점에 API 계층이 검증한다."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=100)  # 키워드 검색어
    sns_account_id: int  # 수집 인증에 쓸 threads 계정(본인 소유)


class ThreadsAdapter:
    can_write = True
    config_model = ThreadsConfig

    # ── read: 키워드 수집 ─────────────────────────────────────────────────

    async def fetch(self, source: Source, since: datetime | None) -> list[FetchedPost]:
        cfg = source.config or {}
        account = await SnsAccount.get_or_none(id=cfg.get("sns_account_id"))
        if account is None or account.platform != Platform.threads:
            raise FetchError("config.sns_account_id 의 threads 계정을 찾을 수 없습니다")
        if account.status != AccountStatus.active:
            raise FetchError("수집 계정이 만료/회수 상태입니다")
        token = await _load_token(account, FetchError)
        params = {
            "q": cfg.get("query") or "",
            # 모니터링 목적이라 최신순(RECENT) — TOP(인기순)은 첫 페이지가 인기 글로
            # 고정돼 신규 매칭 글을 체계적으로 놓친다(어댑터 1차 적대 리뷰 중요-4).
            "search_type": "RECENT",
            "fields": "id,text,username,permalink,timestamp",
        }
        async with _client() as client:
            try:
                resp = await client.get(
                    "/keyword_search", params=params, headers=_auth(token)
                )
            except httpx.HTTPError as exc:
                raise FetchError(f"Threads 검색 요청 실패: {type(exc).__name__}") from exc
        self._raise_read_errors(resp)
        posts = []
        # 첫 페이지만 수집 — 주기 폴링 + dedup(unique 제약)이 연속성을 보장한다.
        for item in (resp.json().get("data") or []):
            media_id = item.get("id")
            text = (item.get("text") or "").strip()
            if not media_id or not text:
                continue
            posts.append(
                FetchedPost(
                    external_post_id=str(media_id),
                    content=text[:_MAX_CONTENT_CHARS],
                    author=(item.get("username") or None),
                    url=(item.get("permalink") or None),
                    published_at=_parse_ts(item.get("timestamp")),
                )
            )
        return posts

    # ── write: 답글 전송 (호출 경로는 사람 승인 approve/retry 뿐 — 불변식 ①) ──

    async def send_reply(self, source: Source, post, body: str, account) -> str:
        # 로컬 검증이 최상단 — 네트워크 호출 전에 거를 수 있는 확정 실패를 먼저.
        if len(body) > MAX_TEXT_LEN:
            raise SendError(f"본문이 Threads 상한({MAX_TEXT_LEN}자)을 초과합니다")
        token = await _load_token(account, SendError)
        # 판정 키 확보(M2 조정 설계 §3.1·R7): 전송 전에 platform_username 이 저장돼
        # 있어야 결과 불명 시 조정이 가능하다. 핸들 변경 시 stale 값으로 조정이 결정적
        # 오판(미게시→retry→이중 게시)하지 않게 **전송마다** 갱신한다(2차 리뷰 중요-1).
        # 이 단계의 어떤 실패든 전송 전 — 확정 실패로 분류한다(2차 리뷰 사소-2).
        try:
            account.platform_username = (await self._get_username(token))[:255]
            await account.save(update_fields=["platform_username"])
        except SendError:
            raise
        except Exception as exc:  # noqa: BLE001 - save() 등 DB 오류 포함
            raise SendError("판정 키(platform_username) 갱신 실패") from exc

        async with _client() as client:
            # 1단계: 답글 컨테이너 생성 — 모든 실패는 확정 실패(발행은 별도 호출).
            try:
                resp = await client.post(
                    "/me/threads",
                    data={
                        "media_type": "TEXT",
                        "text": body,
                        "reply_to_id": post.external_post_id,
                    },
                    headers=_auth(token),
                )
            except httpx.HTTPError as exc:
                raise SendError(f"컨테이너 생성 요청 실패: {type(exc).__name__}") from exc
            if resp.status_code != 200:
                raise SendError(f"컨테이너 생성 실패 — {_error_summary(resp)}")
            container_id = str(resp.json().get("id") or "")
            if not container_id:
                raise SendError("컨테이너 생성 응답에 id 없음")

            # 2단계: 발행 — 여기서부터의 불확실성은 전부 결과 불명(§3.2).
            # 준비 전(code 24)만은 같은 creation_id 로 짧게 재시도한다 — 재호출이
            # idempotent 라 안전(R3 실측). 소진 시 24 는 확정 목록에 없으므로 아래
            # 분류가 결과 불명(container_id 보존 → 조정)으로 처리한다.
            for delay in (*_PUBLISH_RETRY_DELAYS, None):
                try:
                    resp = await client.post(
                        "/me/threads_publish",
                        data={"creation_id": container_id},
                        headers=_auth(token),
                    )
                except httpx.HTTPError as exc:
                    raise SendOutcomeUnknown(
                        f"publish 응답 미수신: {type(exc).__name__}", container_id=container_id
                    ) from exc
                not_ready = (
                    resp.status_code == 400
                    and _error_code(resp) == _PUBLISH_NOT_READY_CODE
                )
                if not (not_ready and delay is not None):
                    break
                await asyncio.sleep(delay)
            if _publish_definite_fail(resp):
                raise SendError(f"publish 거부 — {_error_summary(resp)}")
            if resp.status_code != 200:
                raise SendOutcomeUnknown(
                    f"publish 결과 불명 — {_error_summary(resp)}", container_id=container_id
                )
            media_id = str(resp.json().get("id") or "")
            if not media_id:
                # 200 인데 id 없음 — 발행됐을 수 있다. 확정 실패로 좁히지 않는다.
                raise SendOutcomeUnknown("publish 200 응답에 id 없음", container_id=container_id)
            return media_id

    # ── 조정: 대상 글 답글 read-only 조회 (M2 조정 설계 §3.4) ────────────────

    async def fetch_replies(
        self, source: Source, target_media_id: str, account, since: datetime
    ) -> list[FetchedReply]:
        # 경로 주입 방어: media id 는 숫자 문자열이어야 한다 — `?`·`/` 삽입으로 같은
        # 호스트의 다른 엔드포인트를 토큰 실린 채 치는 경로 차단(1차 적대 리뷰 사소-7).
        if not target_media_id.isdigit():
            raise FetchError("target_media_id 형식이 올바르지 않습니다")
        token = await _load_token(account, FetchError)
        replies: list[FetchedReply] = []
        # 최신순은 가정이 아니라 계약이어야 한다 — reverse 를 명시해 since 조기 중단이
        # 플랫폼 기본값 변경에 흔들리지 않게 한다(1차 적대 리뷰 중요-3). limit 상향으로
        # 페이지 캡 내 커버리지도 넓힌다.
        base_params = {"fields": "id,username,text,timestamp", "reverse": "true", "limit": "100"}
        params = dict(base_params)
        async with _client() as client:
            for _ in range(_REPLIES_PAGE_CAP):
                try:
                    resp = await client.get(
                        f"/{target_media_id}/replies", params=params, headers=_auth(token)
                    )
                except httpx.HTTPError as exc:
                    raise FetchError(f"답글 조회 요청 실패: {type(exc).__name__}") from exc
                self._raise_read_errors(resp)
                payload = resp.json()
                stop = False
                for item in (payload.get("data") or []):
                    ts = _parse_ts(item.get("timestamp"))
                    if ts is None or not item.get("id"):
                        continue
                    # 최신순(reverse=true 명시) — since 이전이 나오면 순회 중단
                    if ts < since:
                        stop = True
                        break
                    replies.append(
                        FetchedReply(
                            external_reply_id=str(item["id"]),
                            username=item.get("username") or "",
                            text=item.get("text") or "",
                            timestamp=ts,
                        )
                    )
                after = (payload.get("paging") or {}).get("cursors", {}).get("after")
                if stop or not after:
                    return replies
                params = dict(base_params, after=after)
        # 캡 소진 = since 에 도달하지 못한 부분 결과 — 조용히 반환하면 조정이 "미게시"로
        # 오판할 수 있다(1차 적대 리뷰 중요-2). 조회 실패로 취급해 attempts 를 동결한다.
        raise FetchError("답글 조회 페이지 상한 초과 — 부분 결과로는 판정하지 않습니다")

    # ── 내부 헬퍼 ────────────────────────────────────────────────────────

    async def _get_username(self, token: str) -> str:
        async with _client() as client:
            try:
                resp = await client.get(
                    "/me", params={"fields": "username"}, headers=_auth(token)
                )
            except httpx.HTTPError as exc:
                raise SendError(f"계정 username 조회 실패: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise SendError(f"계정 username 조회 실패 — {_error_summary(resp)}")
        username = resp.json().get("username")
        if not username:
            raise SendError("계정 username 조회 응답에 username 없음")
        return str(username)

    @staticmethod
    def _raise_read_errors(resp: httpx.Response) -> None:
        """read 경로 공통 에러 매핑 — 429/403 은 backoff 대상(불변식 ④, R7)."""
        if resp.status_code == 429:
            retry_after = resp.headers.get("retry-after")
            sec = float(retry_after) if retry_after and retry_after.isdigit() else None
            raise RateLimitedError(_error_summary(resp), retry_after_sec=sec)
        if resp.status_code == 403:
            raise RateLimitedError(_error_summary(resp))
        if resp.status_code != 200:
            raise FetchError(_error_summary(resp))


async def probe_republish(account_id: int, reply_to: str, text: str) -> None:
    """R3 실측 프로브(사람이 CLI 로 명시 실행) — 발행된 컨테이너에 threads_publish 를
    재호출하면 무슨 일이 일어나는지 관찰한다. 결과는 docs/M2-SEND-RECONCILIATION.md
    §1·§3.5 에 반영할 것. 실제 답글이 1건 게시된다 — 테스트 계정/게시물로 실행하라.
    """
    account = await SnsAccount.get_or_none(id=account_id)
    token = await _load_token(account, SendError)
    async with _client() as client:
        resp = await client.post(
            "/me/threads",
            data={"media_type": "TEXT", "text": text, "reply_to_id": reply_to},
            headers=_auth(token),
        )
        print(f"[1] 컨테이너 생성: HTTP {resp.status_code} → {resp.json()}")
        resp.raise_for_status()
        creation_id = resp.json()["id"]

        resp = await client.post(
            "/me/threads_publish", data={"creation_id": creation_id}, headers=_auth(token)
        )
        print(f"[2] 1차 publish: HTTP {resp.status_code} → {resp.json()}")
        await asyncio.sleep(3)
        resp = await client.post(
            "/me/threads_publish", data={"creation_id": creation_id}, headers=_auth(token)
        )
        # 핵심 관찰점: 같은 media id 반환(사실상 idempotent)인가, 에러인가
        print(f"[3] 2차 publish(재호출): HTTP {resp.status_code} → {resp.json()}")


# ── OAuth 코드 교환 (앱 심사 — 동의 화면 기반 계정 연동, API-SPEC §SNS 계정) ────────

# 인증 코드 교환/장기 토큰 전환은 v1.0 프리픽스가 아닌 루트 경로 — 공식 계약 그대로.
_OAUTH_TOKEN_URL = "https://graph.threads.net/oauth/access_token"
_OAUTH_EXCHANGE_URL = "https://graph.threads.net/access_token"
# 동의 화면(authorize)은 www.threads.net — FE 가 사용자를 보낼 URL 의 베이스.
OAUTH_AUTHORIZE_URL = "https://threads.net/oauth/authorize"
# 신청 권한 전체 — 콘솔 앱 검수 신청 목록과 일치해야 동의 화면에 같은 범위가 뜬다.
OAUTH_SCOPES = (
    "threads_basic,threads_keyword_search,threads_content_publish,"
    "threads_read_replies,threads_manage_replies"
)


class OAuthExchangeError(Exception):
    """코드 무효/만료/재사용 등 교환 거부. 메시지는 audit 안전 요약만(코드/토큰/시크릿 없음)."""


class OAuthUpstreamError(Exception):
    """Threads API 통신 실패/일시 장애(타임아웃·429·5xx). 메시지는 고정 요약만 —
    httpx 예외 문자열에는 URL(시크릿 쿼리 포함 가능)이 실리므로 절대 전달하지 않는다."""


def _json_dict(resp: httpx.Response) -> dict:
    """200 응답의 JSON dict 파싱 — 비JSON/비dict 는 업스트림 이상으로 취급."""
    try:
        body = resp.json()
    except Exception as exc:  # noqa: BLE001 - JSONDecodeError 포함
        raise OAuthUpstreamError("Threads API 응답 형식 이상(비 JSON)") from exc
    if not isinstance(body, dict):
        raise OAuthUpstreamError("Threads API 응답 형식 이상(비 dict)")
    return body


def _raise_for_oauth_status(resp: httpx.Response) -> None:
    """비 200 응답 분류: 429/5xx=일시 장애(업스트림), 그 외 4xx=교환 거부(코드 무효 계열)."""
    if resp.status_code == 200:
        return
    if resp.status_code == 429 or resp.status_code >= 500:
        raise OAuthUpstreamError(_error_summary(resp))
    raise OAuthExchangeError(_error_summary(resp))


async def oauth_exchange_code(code: str) -> dict:
    """인증 코드 → 장기(60일) 토큰 + 프로필. 반환:
    {"access_token": str, "expires_in": int, "user_id": str, "username": str}.

    시크릿/토큰/코드는 응답·예외 메시지·우리 로그 어디에도 싣지 않는다(불변식 ③).
    장기 토큰 전환의 쿼리 파라미터 전달은 공식 계약인데, **httpx 는 INFO 레벨에서
    전체 URL(쿼리 포함)을 로깅하므로** main.py 가 httpx/httpcore 로거를 WARNING 으로
    고정한다(1차 적대 리뷰 High-2 실측). 네트워크 예외(httpx.RequestError)의 문자열에도
    URL 이 실릴 수 있어 고정 메시지의 OAuthUpstreamError 로 변환한다.
    """
    from app.config import settings

    if not (
        settings.threads_app_id and settings.threads_app_secret and settings.threads_redirect_uri
    ):
        raise RuntimeError("Threads OAuth 설정(threads_app_id/secret/redirect_uri) 미설정")
    try:
        async with _client() as client:
            # 1) 인증 코드 → 단기(1시간) 토큰. 코드는 1회용 — 재사용/만료는 4xx.
            resp = await client.post(
                _OAUTH_TOKEN_URL,
                data={
                    "client_id": settings.threads_app_id,
                    "client_secret": settings.threads_app_secret,
                    "grant_type": "authorization_code",
                    "redirect_uri": settings.threads_redirect_uri,
                    "code": code,
                },
            )
            _raise_for_oauth_status(resp)
            short_token = _json_dict(resp).get("access_token")
            if not isinstance(short_token, str) or not short_token:
                raise OAuthUpstreamError("교환 응답에 access_token 없음")

            # 2) 단기 → 장기(60일) 토큰. 쿼리 파라미터는 공식 계약(위 docstring).
            resp = await client.get(
                _OAUTH_EXCHANGE_URL,
                params={
                    "grant_type": "th_exchange_token",
                    "client_secret": settings.threads_app_secret,
                    "access_token": short_token,
                },
            )
            _raise_for_oauth_status(resp)
            body = _json_dict(resp)
            long_token = body.get("access_token")
            if not isinstance(long_token, str) or not long_token:
                raise OAuthUpstreamError("장기 토큰 응답에 access_token 없음")
            try:
                expires_in = max(0, int(body.get("expires_in") or 0))
            except (TypeError, ValueError):
                expires_in = 0

            # 3) 프로필 확보 — 안정 식별자(user id, upsert 키)와 판정 키(username).
            resp = await client.get(
                f"{_API}/me", params={"fields": "id,username"}, headers=_auth(long_token)
            )
            _raise_for_oauth_status(resp)
            me = _json_dict(resp)
            user_id = str(me.get("id") or "")
            username = str(me.get("username") or "")
            # 안정 식별자 없이는 upsert 가 변경 가능한 username 에 의존하게 된다
            # (1차 적대 리뷰 High-5 — username 탈취/변경 시 오연동 위험). 필수로 강제.
            if not user_id or not username:
                raise OAuthUpstreamError("프로필 응답에 id/username 없음")
    except httpx.RequestError as exc:
        # 예외 문자열에 URL(시크릿 쿼리)이 실릴 수 있다 — 고정 메시지로만 변환(불변식 ③)
        raise OAuthUpstreamError("Threads API 통신 실패(네트워크)") from exc
    return {
        "access_token": long_token,
        "expires_in": expires_in,
        "user_id": user_id,
        "username": username,
    }
