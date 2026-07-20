"""커뮤니티 RSS 어댑터 — 공개 RSS/Atom 피드 수집 (소스 능력 매트릭스: read-only).

config: {"rss_url": "https://..."}
"""
import asyncio
import calendar
from datetime import UTC, datetime

import feedparser
import httpx

from app.models import Source
from app.sources.base import FetchedPost, FetchError, RateLimitedError, stable_external_id

_TIMEOUT = 15.0
_UA = "sns-keyword-monitor/0.1 (polite; contact admin)"


def _entry_to_post(entry) -> FetchedPost | None:
    url = entry.get("link")
    if not url:
        return None
    published: datetime | None = None
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        published = datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC)
    title = entry.get("title") or ""
    summary = entry.get("summary") or ""
    content = f"{title}\n{summary}".strip()
    if not content:
        return None
    author = entry.get("author")
    # guid 가 영구적(isPermaLink 여부 무관 고유 문자열)이면 그대로, 없으면 안정 해시 (FR-3)
    guid = entry.get("id")
    external_id = guid or stable_external_id(url, author, published)
    return FetchedPost(
        external_post_id=external_id, content=content,
        author=author, url=url, published_at=published,
    )


def _parse_feed(body: bytes) -> list[FetchedPost]:
    feed = feedparser.parse(body)
    if feed.bozo and not feed.entries:
        raise FetchError(f"RSS 파싱 실패: {feed.bozo_exception!r}")
    return [p for p in (_entry_to_post(e) for e in feed.entries) if p]


class RssAdapter:
    can_write = False

    async def fetch(self, source: Source, since: datetime | None) -> list[FetchedPost]:
        rss_url = (source.config or {}).get("rss_url")
        if not rss_url:
            raise FetchError("config.rss_url 미설정")
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT, headers={"User-Agent": _UA}, follow_redirects=True
            ) as client:
                resp = await client.get(rss_url)
        except httpx.HTTPError as exc:
            raise FetchError(f"RSS 요청 실패: {type(exc).__name__}") from exc
        if resp.status_code in (429, 403):
            raise RateLimitedError(f"HTTP {resp.status_code}")
        if resp.status_code != 200:
            raise FetchError(f"HTTP {resp.status_code}")
        # 파싱은 CPU 작업 → executor 오프로드 (NFR-P1)
        posts = await asyncio.get_running_loop().run_in_executor(
            None, _parse_feed, resp.content
        )
        if since is not None:
            # published 없는 항목은 필터 불가 → 포함(dedup 이 중복을 걸러준다)
            posts = [p for p in posts if p.published_at is None or p.published_at > since]
        return posts
