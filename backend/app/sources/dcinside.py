"""디시인사이드 갤러리 어댑터 — 공개 목록 페이지 수집 (read-only).

config: {"gallery_id": "dog"}

RSS 가 폐지돼(`/board/rss/?id=...` → 404, 2026-09-17 실측) 공개 목록 페이지를 읽는다.
로그인·세션 없이 누구나 보는 페이지이고 주기당 요청 1건뿐이라 예의 있는 수집(불변식 ④)
범위 안이다. **쓰기 경로는 만들지 않는다** — 디시는 댓글·글쓰기 API 를 공개하지 않으며,
세션을 흉내 내는 자동 게시는 불변식 ①·④ 위반이다(`can_write=False` 로 구조적으로 차단).

파싱 제약(상위 판단에 필요):
- **본문을 읽지 않는다.** 목록 1장만 받고 글 본문은 요청하지 않는다 — 51행이면 주기마다
  요청이 52배가 되어 예의 있는 수집에 어긋난다. 따라서 `content` 는 **제목뿐**이고
  키워드 매칭도 제목 기준이다. 디시 제목은 대개 질문 전문이라("간식 추천좀") 실용적이다.
- 게시 시각은 목록이 당일 글만 `title` 속성으로 정확히 준다. 없으면 None 이다
  (ID 는 글번호 기반이라 시각에 의존하지 않는다).
- HTML 파서는 stdlib `html.parser` 를 쓴다 — 이 한 곳 때문에 bs4/lxml 을 새로 들이지 않는다.
"""
import asyncio
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.models import Source
from app.sources.base import (
    FetchedPost,
    FetchError,
    RateLimitedError,
    fit_external_id,
)

_BASE = "https://gall.dcinside.com"
_TIMEOUT = 15.0
_DEADLINE_SEC = 45.0
_MAX_BYTES = 5 * 1024 * 1024
_MAX_ROWS = 200
_MAX_CONTENT_CHARS = 20_000
_UA = "sns-keyword-monitor/0.1 (polite; contact admin)"

# 갤러리 id 는 URL 쿼리에 그대로 들어간다 — 영숫자/언더스코어만 허용해 주입을 막는다.
_GALLERY_ID_RE = re.compile(r"^[A-Za-z0-9_]{1,40}$")
# 목록에서 제외할 행: 공지·설문·운영 배너. 일반 글에는 data-no 가 있다.
_SKIP_TYPES = frozenset({"icon_notice", "icon_survey", "icon_ad"})


class DcinsideConfig(BaseModel):
    """dcinside 소스 config 스키마 — 등록/수정 시점에 API 계층이 검증한다."""

    model_config = ConfigDict(extra="forbid")

    gallery_id: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_]+$")


def _validate_gallery_id(value: str) -> str:
    if not _GALLERY_ID_RE.match(value):
        raise FetchError("config.gallery_id 형식이 올바르지 않습니다(영숫자·_ 만)")
    return value


class _ListParser(HTMLParser):
    """`tr.ub-content` 행에서 글번호·제목·글쓴이·시각을 뽑는다.

    디시 목록은 셀마다 클래스가 붙어 있어(`gall_tit`/`gall_writer`/`gall_date`) 셀 단위로
    텍스트를 모으면 된다. 제목 셀 안의 `reply_numbox`(댓글수 링크)는 제목이 아니므로
    첫 번째 `a`(글 링크)만 취한다.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict] = []
        self._row: dict | None = None
        self._cell: str | None = None
        self._buf: list[str] = []
        self._depth = 0

    @staticmethod
    def _classes(attrs: dict) -> set[str]:
        return set((attrs.get("class") or "").split())

    def handle_starttag(self, tag: str, attrs_list) -> None:
        attrs = dict(attrs_list)
        if tag == "tr" and "ub-content" in self._classes(attrs):
            self._row = {
                "no": attrs.get("data-no"),
                "type": attrs.get("data-type") or "",
                "title": "", "writer": "", "date": "", "href": "",
            }
            self._depth = 0
            return
        if self._row is None:
            return
        if tag == "td":
            self._flush()
            cls = self._classes(attrs)
            for key in ("gall_tit", "gall_writer", "gall_date"):
                if key in cls:
                    self._cell = key
                    if key == "gall_date" and attrs.get("title"):
                        self._row["date"] = attrs["title"]
                    break
            else:
                self._cell = None
            # 글쓴이는 텍스트보다 data-nick 속성이 정확하다(닉네임 아이콘 마크업 회피)
            if "gall_writer" in cls:
                nick = attrs.get("data-nick") or attrs.get("user_name")
                if nick:
                    self._row["writer"] = nick
        elif tag == "a" and self._cell == "gall_tit":
            href = attrs.get("href") or ""
            # 댓글수 링크(t=cv)는 제목 링크가 아니다
            if not self._row["href"] and "/board/view/" in href and "t=cv" not in href:
                self._row["href"] = href

    def handle_data(self, data: str) -> None:
        if self._row is not None and self._cell:
            self._buf.append(data)

    def _flush(self) -> None:
        if self._row is None or not self._cell:
            self._buf = []
            return
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        if self._cell == "gall_tit":
            # 댓글수 `[438]` 꼬리표 제거
            self._row["title"] = re.sub(r"\[\d+\]\s*$", "", text).strip()
        elif self._cell == "gall_writer" and not self._row["writer"]:
            self._row["writer"] = text
        elif self._cell == "gall_date" and not self._row["date"]:
            self._row["date"] = text
        self._buf = []
        self._cell = None

    def handle_endtag(self, tag: str) -> None:
        if self._row is None:
            return
        if tag == "td":
            self._flush()
        elif tag == "tr":
            self._flush()
            self.rows.append(self._row)
            self._row = None


def _parse_dt(value: str) -> datetime | None:
    """목록의 시각 표기. 당일 글은 `title="2026-09-18 10:23:45"` 로 정확히 온다."""
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y.%m.%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None  # `26/09/14`·`10:23` 같은 축약 표기는 연도·타임존이 모호해 쓰지 않는다


def _to_posts(html: str, gallery_id: str) -> list[FetchedPost]:
    parser = _ListParser()
    parser.feed(html)
    posts: list[FetchedPost] = []
    for row in parser.rows[:_MAX_ROWS]:
        no, title = row["no"], row["title"]
        if not no or not title or row["type"] in _SKIP_TYPES:
            continue
        posts.append(
            FetchedPost(
                # 글번호는 갤러리 안에서 고유하고 URL 변형·시각에 흔들리지 않는다(FR-3).
                external_post_id=fit_external_id(f"dcinside|{gallery_id}|{no}"),
                content=title[:_MAX_CONTENT_CHARS],
                author=row["writer"][:255] if row["writer"] else None,
                url=urljoin(_BASE, row["href"] or f"/board/view/?id={gallery_id}&no={no}")[:1024],
                published_at=_parse_dt(row["date"]),
            )
        )
    return posts


def _client() -> httpx.AsyncClient:
    """테스트가 MockTransport 로 갈아끼울 수 있게 팩토리로 분리."""
    return httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False)


class DcinsideAdapter:
    can_write = False  # 디시는 댓글/글쓰기 API 가 없다 — 자동 게시 경로를 만들지 않는다
    author_is_person = True  # 목록의 글쓴이는 사람 닉네임이다
    config_model = DcinsideConfig

    async def fetch(self, source: Source, since: datetime | None) -> list[FetchedPost]:
        """`since` 는 쓰지 않는다 — 목록 1장을 전량 반환하고 dedup 에 위임한다."""
        cfg = source.config or {}
        gallery_id = _validate_gallery_id((cfg.get("gallery_id") or "").strip())
        url = f"{_BASE}/board/lists/"
        params = {"id": gallery_id}
        headers = {
            "User-Agent": _UA,
            # 자동 압축 해제가 크기 상한 검사보다 먼저 메모리를 잡는 것을 막는다(rss.py C2).
            "Accept-Encoding": "identity",
        }
        try:
            # 단계별 타임아웃만으로는 드립 스트림을 못 막는다 — poll_tick 이 순차 처리라
            # 한 소스가 막히면 전체 수집이 멈춘다(naver_cafe 와 같은 방어선).
            async with asyncio.timeout(_DEADLINE_SEC):
                async with _client() as client:
                    async with client.stream(
                        "GET", url, params=params, headers=headers
                    ) as resp:
                        status = resp.status_code
                        retry_after = resp.headers.get("retry-after")
                        encoding = resp.headers.get("content-encoding", "identity")
                        if status == 200 and encoding.lower() != "identity":
                            raise FetchError("압축 응답 거부(Accept-Encoding: identity 요청)")
                        body = await self._read_limited(resp)
        except TimeoutError as exc:
            raise FetchError("갤러리 목록 응답이 제한 시간을 넘겼습니다") from exc
        except httpx.HTTPError as exc:
            raise FetchError(f"갤러리 목록 요청 실패: {type(exc).__name__}") from exc

        self._raise_read_errors(status, retry_after)
        html = body.decode("utf-8", errors="replace")
        # 파싱은 CPU 작업 → executor 오프로드 (NFR-P1, rss.py 와 동일)
        return await asyncio.get_running_loop().run_in_executor(
            None, _to_posts, html, gallery_id
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
    def _raise_read_errors(status: int, retry_after: str | None) -> None:
        if status == 200:
            return
        if status in (429, 403):
            # 차단·속도제한은 backoff 대상 — 더 세게 두드리지 않는다(불변식 ④).
            sec = None
            try:
                sec = float(retry_after) if retry_after else None
            except (TypeError, ValueError):
                sec = None
            raise RateLimitedError(f"HTTP {status}", retry_after_sec=sec if sec and sec > 0 else None)
        if status in (301, 302, 303, 307, 308):
            raise FetchError(f"예상치 못한 리다이렉트(HTTP {status}) — 갤러리 id 확인")
        raise FetchError(f"갤러리 목록 실패: HTTP {status}")
