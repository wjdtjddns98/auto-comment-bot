"""커뮤니티 RSS 어댑터 — 공개 RSS/Atom 피드 수집 (소스 능력 매트릭스: read-only).

config: {"rss_url": "https://..."}

신뢰불가 입력 방어(적대 리뷰 반영):
- 응답 크기 상한(스트리밍 누적 5MB) + 절대 데드라인 — 압축 해제 폭탄/슬로우 스트림 차단
- SSRF 가드: 최초 URL·모든 리다이렉트 hop 에서 공인 IP 검증
- since 필터 없음 — 피드에 늦게 노출된 글이 영구 누락되지 않게 전량 반환하고
  dedup(unique 제약)에 위임한다
"""
import asyncio
import calendar
from datetime import UTC, datetime
from urllib.parse import urljoin

import feedparser
import httpx

from app.models import Source
from app.sources.base import (
    FetchedPost,
    FetchError,
    RateLimitedError,
    assert_public_http_url,
    fit_external_id,
    stable_external_id,
)

_TIMEOUT = 15.0  # httpx 단계별 타임아웃
_DEADLINE_SEC = 45.0  # 요청 전체 절대 상한(슬로우 스트림 방어)
_MAX_BYTES = 5 * 1024 * 1024  # 응답 누적 상한(압축 해제 후)
_MAX_ENTRIES = 500  # 피드당 처리 상한
_MAX_CONTENT_CHARS = 20_000  # 항목 본문 상한
_MAX_REDIRECTS = 3
_UA = "sns-keyword-monitor/0.1 (polite; contact admin)"


def _ts(parsed) -> datetime | None:
    return datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC) if parsed else None


def _entry_to_post(entry, base_url: str) -> FetchedPost | None:
    link = entry.get("link")
    if not link:
        return None
    url = urljoin(base_url, link)  # 상대 경로 피드 대응
    published = _ts(entry.get("published_parsed"))
    author = entry.get("author")

    # 전문은 content(:encoded) 우선 — 요약(description)엔 티저만 있는 피드가 많다.
    body = ""
    contents = entry.get("content") or []
    if contents:
        body = contents[0].get("value") or ""
    if not body:
        body = entry.get("summary") or ""
    title = entry.get("title") or ""
    content = f"{title}\n{body}".strip()[:_MAX_CONTENT_CHARS]
    if not content:
        return None

    # guid 우선(고유 문자열), 없으면 안정 해시. ID 에는 published 만 사용 —
    # updated 는 편집만 돼도 바뀌어 같은 글이 다른 ID 로 갈라진다(적대 리뷰 H2).
    guid = entry.get("id")
    external_id = fit_external_id(guid) if guid else stable_external_id(url, author, published)
    return FetchedPost(
        external_post_id=external_id,
        content=content,
        author=author[:255] if author else None,
        url=url[:1024],
        published_at=published or _ts(entry.get("updated_parsed")),  # 표시용 폴백만 허용
    )


def _parse_feed(body: bytes, base_url: str) -> list[FetchedPost]:
    feed = feedparser.parse(body)
    if feed.bozo and not feed.entries:
        raise FetchError(f"RSS 파싱 실패: {type(feed.bozo_exception).__name__}")
    entries = feed.entries[:_MAX_ENTRIES]
    return [p for p in (_entry_to_post(e, base_url) for e in entries) if p]


async def _get_with_limits(url: str) -> tuple[bytes, str]:
    """크기 상한 스트리밍 GET + hop 별 SSRF 검증. (body, 최종 URL) 반환."""
    # Accept-Encoding: identity — httpx 의 자동 압축 해제는 상한 검사보다 먼저 메모리를
    # 할당해 81KB gzip 이 190MB 를 잡는다(2차 리뷰 C2 실측). 압축을 아예 받지 않는다.
    async with httpx.AsyncClient(
        timeout=_TIMEOUT,
        headers={"User-Agent": _UA, "Accept-Encoding": "identity"},
        follow_redirects=False,
    ) as client:
        current = url
        for _ in range(_MAX_REDIRECTS + 1):
            await assert_public_http_url(current)
            async with client.stream("GET", current) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        raise FetchError("리다이렉트에 Location 없음")
                    current = urljoin(current, location)
                    continue
                if resp.status_code == 429:
                    retry_after = resp.headers.get("retry-after")
                    sec = float(retry_after) if retry_after and retry_after.isdigit() else None
                    raise RateLimitedError("HTTP 429", retry_after_sec=sec)
                if resp.status_code == 403:
                    raise RateLimitedError("HTTP 403")
                if resp.status_code != 200:
                    raise FetchError(f"HTTP {resp.status_code}")
                # 서버가 요청을 무시하고 압축을 보내면 거부(압축 폭탄 방어선 유지)
                if resp.headers.get("content-encoding", "identity").lower() != "identity":
                    raise FetchError("압축 응답 거부(Accept-Encoding: identity 요청)")
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > _MAX_BYTES:
                        raise FetchError("응답 크기 상한 초과")
                    chunks.append(chunk)
                return b"".join(chunks), current
        raise FetchError("리다이렉트 한도 초과")


class RssAdapter:
    can_write = False

    async def fetch(self, source: Source, since: datetime | None) -> list[FetchedPost]:
        rss_url = (source.config or {}).get("rss_url")
        if not rss_url:
            raise FetchError("config.rss_url 미설정")
        try:
            # 파싱까지 데드라인 안에 둔다 — 거대/기형 XML 파싱이 tick 전체를 막지 않게.
            # 주의: 스레드는 강제 종료 불가라 타임아웃 후에도 백그라운드로 계속 돈다.
            # CPU 하드 상한이 필요해지면 ProcessPoolExecutor + terminate 로 승급할 것.
            async with asyncio.timeout(_DEADLINE_SEC):
                body, final_url = await _get_with_limits(rss_url)
                # 파싱은 CPU 작업 → executor 오프로드 (NFR-P1)
                return await asyncio.get_running_loop().run_in_executor(
                    None, _parse_feed, body, final_url
                )
        except TimeoutError as exc:
            raise FetchError("요청 데드라인 초과") from exc
        except httpx.HTTPError as exc:
            raise FetchError(f"RSS 요청 실패: {type(exc).__name__}") from exc
