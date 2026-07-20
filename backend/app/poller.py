"""poller — 소스 주기 수집 → 키워드 매칭 → matched_posts 저장 (FR-1~5).

단일 uvicorn 워커 전제(NFR-P1): 실패 카운터는 in-memory 로 충분하다.
커서(last_success_at)는 store 성공 후에만 전진한다 — 실패 시 다음 폴링이
같은 구간을 다시 긁고 dedup 이 중복을 걸러준다 (FR-5).
"""
import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta

from tortoise.expressions import Q

from app import matching
from app.models import HealthStatus, Keyword, MatchedPost, Source
from app.sources import FetchError, RateLimitedError, get_adapter

logger = logging.getLogger(__name__)

_BACKOFF_BASE_SEC = 60
_BACKOFF_CAP_SEC = 3600
_DOWN_AFTER_FAILURES = 5

_fail_counts: dict[int, int] = {}
state: dict = {"last_tick": None}  # /health poller heartbeat (FR-17)


def _now() -> datetime:
    return datetime.now(UTC)


def _is_due(source: Source, now: datetime) -> bool:
    if source.backoff_until and source.backoff_until > now:
        return False
    if source.last_polled_at is None:
        return True
    return source.last_polled_at + timedelta(seconds=source.poll_interval_sec) <= now


async def poll_tick() -> None:
    """스케줄러 진입점 — 폴링 주기가 도래한 소스만 순차 수집."""
    now = _now()
    state["last_tick"] = now
    for source in await Source.filter(enabled=True):
        if not _is_due(source, now):
            continue
        try:
            await poll_source(source)
        except Exception:
            # 한 소스의 실패(저장 오류 포함)가 다른 소스 폴링을 막지 않는다.
            # 커서는 전진하지 않았으므로 다음 tick 에 같은 구간을 재시도한다.
            logger.exception("소스 폴링 실패 source=%s", source.id)


async def poll_source(source: Source) -> int:
    """소스 1개 수집. 저장된 매칭 글 수를 반환."""
    adapter = get_adapter(source.type)
    source.last_polled_at = _now()
    if adapter is None:
        await source.save(update_fields=["last_polled_at"])
        return 0

    try:
        posts = await adapter.fetch(source, source.last_success_at)
    except RateLimitedError:
        await _record_failure(source, rate_limited=True)
        return 0
    except FetchError:
        await _record_failure(source, rate_limited=False)
        return 0

    stored = await _store_matches(source, posts)

    # store 성공 → 커서 전진 + 상태 회복 (FR-5)
    _fail_counts.pop(source.id, None)
    source.last_success_at = source.last_polled_at
    source.health_status = HealthStatus.ok
    source.backoff_until = None
    await source.save(
        update_fields=["last_polled_at", "last_success_at", "health_status", "backoff_until"]
    )
    return stored


async def _record_failure(source: Source, *, rate_limited: bool) -> None:
    count = _fail_counts.get(source.id, 0) + 1
    _fail_counts[source.id] = count
    source.health_status = (
        HealthStatus.down if count >= _DOWN_AFTER_FAILURES else HealthStatus.degraded
    )
    if rate_limited:
        # 429/403 지수 backoff (FR-4)
        delay = min(_BACKOFF_BASE_SEC * 2 ** (count - 1), _BACKOFF_CAP_SEC)
        source.backoff_until = _now() + timedelta(seconds=delay)
    await source.save(update_fields=["last_polled_at", "health_status", "backoff_until"])


async def _store_matches(source: Source, posts: list) -> int:
    if not posts:
        return 0
    keywords = list(
        await Keyword.filter(enabled=True)
        .filter(Q(source_scope=None) | Q(source_scope=source))
        .order_by("id")
    )
    if not keywords:
        return 0

    loop = asyncio.get_running_loop()
    rows = []
    for post in posts:
        # 매칭은 CPU 작업(특히 regex) → executor 오프로드 (NFR-P1)
        kw = await loop.run_in_executor(None, matching.find_match, post.content, keywords)
        if kw is None:
            continue
        rows.append(
            MatchedPost(
                source=source,
                external_post_id=post.external_post_id[:512],
                author=post.author,
                url=post.url,
                content=post.content,
                content_hash=hashlib.sha256(post.content.encode()).hexdigest(),
                matched_keyword=kw,
                published_at=post.published_at,
            )
        )
    if not rows:
        return 0
    # (source_id, external_post_id) unique + ignore_conflicts = upsert-ignore dedup (FR-2)
    await MatchedPost.bulk_create(rows, ignore_conflicts=True)
    return len(rows)
