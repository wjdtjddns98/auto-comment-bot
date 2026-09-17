"""네이버 카페글 검색 어댑터 — 검색 API 로 공개 카페 게시글 수집 (read-only).

config: {"query": "검색어"}

공식 스펙(developers.naver.com 카페글 검색, 2026-09-17 확인):
- `GET https://openapi.naver.com/v1/search/cafearticle.json`
- 비로그인 방식 — 헤더 `X-Naver-Client-Id` / `X-Naver-Client-Secret` 만으로 인증
- 파라미터: `query`(필수, UTF-8) · `display`(1~100, 기본 10) · `start`(1~1000) ·
  `sort`(`sim` 정확도순 기본 / `date` 날짜순)
- item 필드: `title` · `link` · `description` · `cafename` · `cafeurl` **뿐**
- 한도: **25,000회/일, 클라이언트 ID 별 합산**(검색 API 전체가 같은 한도를 나눠 쓴다)
- 오류: 400 `SE01~SE06`(요청 오류) / 404 `SE05` / 500 `SE99` / 403 = 검색 API 권한 미설정

이 소스의 구조적 제약(어댑터가 '고칠 수 없는' 것들 — 상위 판단에 필요):
- **게시 시각이 없다.** `published_at` 은 항상 None 이고, 따라서 `since` 커서로 증분
  수집을 할 수 없다. rss 어댑터와 같이 매 주기 전량을 반환하고 dedup 에 위임한다.
- **작성자가 없다.** API 가 글쓴이를 주지 않아 `author` 에는 **카페 이름**(cafename)을
  넣는다. 화면의 '작성자' 열이 카페명으로 보이는 것은 의도된 동작이다.
- 자격증명은 `.env`(Settings) 로만 주입한다 — source.config 에 두면 GET /api/sources
  응답으로 그대로 반사된다(불변식 ③).
- **한 주기에 최대 100건(첫 페이지)만 수집한다.** `since` 커서가 없어 다음 주기도 최신부터
  다시 시작하므로, 주기 사이에 100건 밖으로 밀려난 글은 지연이 아니라 **영구 누락**이다.
  `total > 100` 이면 경고 로그를 남긴다 — cursor 순회로 덮으려면 호출이 배로 드는데
  한도가 앱 전역 공유라, 검색어를 좁히거나 주기를 줄이는 쪽이 맞는 대응이다.
"""
import asyncio
import hashlib
import html
import json
import logging
import re
import time
from datetime import datetime

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.models import Source
from app.sources.base import (
    FetchedPost,
    FetchError,
    RateLimitedError,
)

# 호스트가 코드 상수라 사용자 입력이 URL 에 끼어들 수 없다 — rss 어댑터와 달리
# SSRF 가드(assert_public_http_url)가 필요 없는 이유.
_API = "https://openapi.naver.com/v1/search/cafearticle.json"
_TIMEOUT = 15.0  # httpx 단계별 타임아웃
_DEADLINE_SEC = 45.0  # 요청 전체 절대 상한(슬로우 스트림이 poller 를 멈추는 것 방어)
_MAX_BYTES = 2 * 1024 * 1024  # display=100 이면 수백 KB — 상한은 방어선일 뿐
_MAX_ITEMS = 100  # display 최댓값과 동일(응답이 더 주더라도 잘라낸다)
_MAX_CONTENT_CHARS = 20_000
_UA = "sns-keyword-monitor/0.1 (polite; contact admin)"

# 날짜 필터가 없어 "첫 페이지가 곧 전부"다 — 한 번에 최댓값을 받아 주기 사이 누락을 줄인다.
_DISPLAY = 100

_TAG_RE = re.compile(r"<[^>]+>")

logger = logging.getLogger(__name__)

# ── 공유 쿼터 차단기 ──────────────────────────────────────────────────────────
# 한도는 **클라이언트 ID 단위**(앱 전역)인데 poller 의 backoff 는 **소스 단위**다
# (`sources.backoff_until`). 그대로 두면 소스 A 가 429 를 맞고 쉬는 동안 B..Z 는 이미
# 고갈된 쿼터에 계속 호출을 때린다 — 네이버가 "그만" 이라고 말한 직후 가장 세게 두드리는
# 꼴이라 불변식 ④ 위반이다. 그래서 모든 naver_cafe 소스가 공유하는 쿨다운을 둔다.
# uvicorn `--workers 1` 고정(운영 규칙)이라 프로세스 전역 변수로 충분하다.
_QUOTA_COOLDOWN_SEC = 600.0
_quota_cooldown_until = 0.0


def _quota_cooldown_remaining() -> float:
    return max(0.0, _quota_cooldown_until - time.monotonic())


def _start_quota_cooldown(seconds: float | None = None) -> None:
    global _quota_cooldown_until
    _quota_cooldown_until = time.monotonic() + (seconds or _QUOTA_COOLDOWN_SEC)


def _parse_retry_after(value: str | None) -> float | None:
    """`Retry-After` 초 값. 파싱 실패/비양수는 None(어댑터 기본 ladder 에 맡긴다).

    `str.isdigit()` 로 거르면 안 된다 — `"²".isdigit()` 는 True 인데 `float("²")` 는
    ValueError 다. 그 예외는 `_raise_read_errors` 가 httpx 예외 핸들러 **밖**에서
    불리므로 poller 까지 올라가고, RateLimitedError 가 아니게 되어 **429 를 맞고도
    backoff 가 안 걸린다**(불변식 ④). "1.5"·음수·HTTP-date 도 이 방식이면 함께 처리된다.
    """
    try:
        sec = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return sec if sec > 0 else None


def _client() -> httpx.AsyncClient:
    """테스트가 MockTransport 로 갈아끼울 수 있게 팩토리로 분리(threads 어댑터와 동일)."""
    return httpx.AsyncClient(timeout=_TIMEOUT)


class NaverCafeConfig(BaseModel):
    """naver_cafe 소스 config 스키마 — 등록/수정 시점에 API 계층이 검증한다."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=100)  # 카페글 검색어


def _clean(text: str | None) -> str:
    """검색어 하이라이트(`<b>`) 등 태그 제거 후 HTML 엔티티 복원.

    순서가 중요하다 — 먼저 태그를 지우고 나중에 unescape 한다. 반대로 하면 사용자가
    본문에 literal 로 쓴 `&lt;b&gt;` 가 태그로 되살아나 그대로 삭제된다.
    """
    return html.unescape(_TAG_RE.sub("", text or "")).strip()


def _external_id(cafeurl: str, cafename: str, title: str) -> str | None:
    """FR-3 안정 ID — **링크를 재료로 쓰지 않는다.**

    `link` 는 `http://openapi.naver.com/l?AAAB...` 형태의 리다이렉트 토큰으로 올 수도,
    실제 카페 글 URL 로 올 수도 있다(공식 문서 응답 예는 전자). 링크 값에 따라 ID 체계를
    갈랐더니 **그 분기 자체가 dedup 을 깨는 원인**이 됐다(적대 리뷰 2건 공통 지적):
    같은 글이 두 형태로 한 번씩 오면 ID 가 갈라지고, 네이버가 링크 형식을 바꾸는 날
    보관 중이던 전 구간이 unique 제약을 빗나가 **한 주기에 통째로 재유입**된다. 이는
    분기로 막으려던 "무한 중복 저장" 과 정확히 같은 실패다.

    그래서 링크 형태와 무관한 (카페 URL + 제목) 하나로 고정한다. m./PC 도메인 차이,
    구형 `ArticleRead.nhn?clubid=..` ↔ 신형 경로, `commentFocus` 같은 비추적 파라미터도
    전부 흡수된다. 대가는 같은 카페에 제목이 완전히 같은 글이 둘일 때 뒤의 글이
    누락되는 것 — 누락은 회복 가능하고 중복 홍수는 아니라서 이쪽을 택한다.

    주의: `title` 은 `_clean` 을 거친 값이어야 한다(하이라이트 `<b>` 는 검색어에 따라
    붙는 위치가 달라진다). 즉 `_clean` 은 표시용이 아니라 **dedup 의 일부**다.

    카페를 식별할 값(`cafeurl`, 없으면 `cafename`)이 전혀 없으면 **None** 을 돌려준다 —
    그때 `naver_cafe||{title}` 로 만들면 서로 다른 카페의 동명 글("오늘의 질문")이 한 ID 로
    합쳐져 뒤의 글이 unique 제약에 걸려 영구히 버려진다. 식별 불가한 항목은 저장하지 않는
    편이 조용한 유실보다 낫다.
    """
    namespace = cafeurl or cafename
    if not namespace:
        return None
    # 항상 해시한다 — 게시자 통제 문자열(제목)이 인덱스에 평문으로 들어가지 않게.
    return hashlib.sha256(f"naver_cafe|{namespace}|{title}".encode()).hexdigest()


def _item_to_post(item: dict) -> FetchedPost | None:
    link = (item.get("link") or "").strip()
    title = _clean(item.get("title"))
    if not link or not title:
        return None
    cafename = _clean(item.get("cafename"))
    cafeurl = (item.get("cafeurl") or "").strip()
    external_id = _external_id(cafeurl, cafename, title)
    if external_id is None:
        logger.warning("naver_cafe 항목에 카페 식별자가 없어 건너뜀 — title=%r", title[:60])
        return None
    body = _clean(item.get("description"))
    content = f"{title}\n{body}".strip()[:_MAX_CONTENT_CHARS]
    return FetchedPost(
        external_post_id=external_id,
        content=content,
        # API 가 글쓴이를 주지 않는다 — 출처를 알 수 있게 카페 이름을 넣는다(모듈 docstring).
        author=cafename[:255] or None,
        url=link[:1024],
        published_at=None,  # 카페글 검색 응답에는 게시 시각 필드가 없다
    )


def _parse_items(payload: dict, query: str = "") -> list[FetchedPost]:
    items = payload.get("items")
    if not isinstance(items, list):
        raise FetchError("검색 응답에 items 배열이 없습니다")
    # 첫 페이지만 가져오고 cursor 순회를 하지 않는다 — 날짜 필터가 없어 다음 주기가
    # 다시 최신부터 시작하므로, 주기 사이에 100건을 넘겨 밀려난 글은 "지연"이 아니라
    # **영구 누락**이다. total 을 보면 그 사실을 운영자가 알 수 있으므로 경고로 남긴다
    # (수집을 늘리는 대신 검색어를 좁히거나 주기를 줄이는 것이 쿼터상 맞는 대응이다).
    total = payload.get("total")
    if isinstance(total, int) and total > _DISPLAY:
        logger.warning(
            "naver_cafe 검색 결과가 첫 페이지를 넘김 — query=%r total=%d 수집=%d "
            "(초과분은 이번 주기에 누락된다. 검색어를 좁히거나 주기를 줄일 것)",
            query, total, min(len(items), _DISPLAY),
        )
    posts = (_item_to_post(i) for i in items[:_MAX_ITEMS] if isinstance(i, dict))
    return [p for p in posts if p]


# 문서화된 오류 코드 → **우리가 쓴** 설명. 원격 서버의 errorMessage 를 그대로 싣지
# 않는 이유는 threads 어댑터와 같다(거긴 "message 는 요청 원문 echo 가능" 이라 제외했다):
# 이 문자열은 poller 가 "어댑터 통제 안전 텍스트" 로 믿고 sources.last_error 에 저장하고,
# 그대로 GET /api/sources 응답과 poll-now 502 detail 로 나간다. 원격이 통제하는 문자열을
# 거기에 실으면 그 계약이 깨진다(불변식 ③ 방향).
_ERROR_CODES = {
    "SE01": "잘못된 쿼리 요청",
    "SE02": "display 값이 허용 범위를 벗어남",
    "SE03": "start 값이 허용 범위를 벗어남",
    "SE04": "sort 값 오류",
    "SE05": "존재하지 않는 검색 API",
    "SE06": "검색어 인코딩 오류",
    "SE99": "네이버 시스템 오류",
}


def _parse_body(body: bytes, query: str) -> list[FetchedPost]:
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise FetchError("카페글 검색 응답 JSON 파싱 실패") from exc
    if not isinstance(payload, dict):
        raise FetchError("카페글 검색 응답 형식이 올바르지 않습니다")
    return _parse_items(payload, query)


def _error_summary(status: int, body: bytes) -> str:
    """오류 응답 요약 — **알려진 errorCode 만** 우리 문구로 옮긴다.

    본문·헤더·원격 메시지를 그대로 싣지 않는다. 모르는 코드는 형식이 확실한 경우에만
    코드값을 남기고(영숫자 16자 이하), 그 외에는 상태코드만 남긴다.
    """
    try:
        code = str(json.loads(body).get("errorCode") or "")
    except Exception:  # noqa: BLE001 - 비 JSON 오류 응답
        code = ""
    known = _ERROR_CODES.get(code)
    if known:
        return f"HTTP {status} — {code} {known}"
    if code.isalnum() and len(code) <= 16:
        return f"HTTP {status} — {code}"
    return f"HTTP {status}"


class NaverCafeAdapter:
    can_write = False  # 검색 API 는 읽기 전용 — 답글은 사람이 카페에서 직접 단다
    # author 에 글쓴이가 아니라 카페 이름이 들어간다(API 가 글쓴이를 주지 않는다).
    # 템플릿 {{author}} 치환에서 제외되는 근거 — base.SourceAdapter 주석 참고.
    author_is_person = False
    config_model = NaverCafeConfig

    async def fetch(self, source: Source, since: datetime | None) -> list[FetchedPost]:
        """`since` 는 쓰지 않는다 — 응답에 게시 시각이 없어 증분 판정이 불가능하다.
        매 주기 전량을 돌려주고 중복 제거는 unique 제약에 맡긴다(rss 어댑터와 동일)."""
        cfg = source.config or {}
        query = (cfg.get("query") or "").strip()
        if not query:
            raise FetchError("config.query 가 비어 있습니다")
        client_id = settings.naver_client_id
        client_secret = settings.naver_client_secret
        if not client_id or not client_secret:
            raise FetchError("네이버 검색 API 자격증명이 설정되지 않았습니다")
        # 다른 naver_cafe 소스가 이미 한도에 막혔다면 호출 자체를 하지 않는다.
        remaining = _quota_cooldown_remaining()
        if remaining > 0:
            raise RateLimitedError(
                "네이버 검색 API 공유 한도 쿨다운 중(다른 소스가 429/403 을 받음)",
                retry_after_sec=remaining,
            )

        params = {
            "query": query,
            "display": _DISPLAY,
            "start": 1,
            # 모니터링 목적이라 날짜순 — sim(정확도순)은 첫 페이지가 인기 글로 고정돼
            # 신규 글을 체계적으로 놓친다(threads 어댑터가 RECENT 를 쓰는 것과 같은 이유).
            "sort": "date",
        }
        headers = {
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
            "User-Agent": _UA,
            "Accept-Encoding": "identity",  # 자동 압축 해제가 크기 상한을 우회하는 것 차단
        }
        try:
            # httpx timeout 은 **청크 단위** 상한이라, 14초마다 1바이트씩 흘리는 응답은
            # 영원히 타임아웃을 회피한다. poll_tick 은 소스를 순차 처리하므로 그 한 건이
            # 전체 수집을 멈추고, 예외가 안 나 health 는 ok 로 남는다(적대 리뷰 실측).
            # rss 어댑터와 같이 절대 데드라인으로 감싼다.
            async with asyncio.timeout(_DEADLINE_SEC):
                async with _client() as client:
                    async with client.stream(
                        "GET", _API, params=params, headers=headers
                    ) as resp:
                        # 스트림을 닫기 전에 판정에 필요한 값을 꺼내 둔다.
                        status = resp.status_code
                        retry_after = resp.headers.get("retry-after")
                        # 서버가 identity 요청을 무시하고 압축을 보내면 거부한다 —
                        # httpx 는 상한 검사보다 **먼저** 압축을 풀어 메모리를 잡는다
                        # (rss.py 2차 리뷰 C2 실측: 81KB gzip → 190MB).
                        encoding = resp.headers.get("content-encoding", "identity")
                        if encoding.lower() != "identity":
                            raise FetchError("압축 응답 거부(Accept-Encoding: identity 요청)")
                        body = await self._read_limited(resp)
        except TimeoutError as exc:
            raise FetchError("카페글 검색 응답이 제한 시간을 넘겼습니다") from exc
        except httpx.HTTPError as exc:
            # 예외 원문에 요청 헤더가 섞일 수 있어 타입명만 남긴다(불변식 ③).
            raise FetchError(f"카페글 검색 요청 실패: {type(exc).__name__}") from exc
        self._raise_read_errors(status, retry_after, body)
        # JSON 파싱 + 항목별 태그 제거/해시는 CPU 작업 → executor 오프로드(NFR-P1).
        # 상한(2MB) 응답이면 단일 워커 이벤트 루프를 수십 ms 막는다(rss.py 와 같은 처리).
        return await asyncio.get_running_loop().run_in_executor(
            None, _parse_body, body, query
        )

    @staticmethod
    async def _read_limited(resp: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        total = 0
        async for chunk in resp.aiter_bytes():
            total += len(chunk)
            if total > _MAX_BYTES:
                raise FetchError("응답 크기 상한 초과")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _raise_read_errors(status: int, retry_after: str | None, body: bytes) -> None:
        if status == 200:
            return
        if status == 429:
            sec = _parse_retry_after(retry_after)
            # 한도는 앱 전역이므로 이 소스뿐 아니라 모든 naver_cafe 소스를 멈춘다.
            _start_quota_cooldown(sec)
            raise RateLimitedError(_error_summary(status, body), retry_after_sec=sec)
        if status == 403:
            _start_quota_cooldown()
            # 공식 문서상 403 = "검색 API 사용 설정 안 함"(영구 설정 오류)이지만, 한도
            # 초과가 403 으로 오는 경우까지 배제할 수 없다. 예의 있는 수집(불변식 ④)을
            # 우선해 backoff 를 걸되, 원인을 last_error 로 화면에 남겨 오진을 막는다.
            raise RateLimitedError(
                f"{_error_summary(status, body)} — 개발자센터에서 검색 API 사용 설정/한도 확인"
            )
        raise FetchError(f"카페글 검색 실패: {_error_summary(status, body)}")
