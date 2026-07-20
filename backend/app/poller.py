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
from app.sources import RateLimitedError, get_adapter

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


def forget_source(source_id: int) -> None:
    """소스 삭제 시 실패 카운터 정리(미세 누수 방지)."""
    _fail_counts.pop(source_id, None)


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
            # poll_source 가 자체 회계를 하므로 여기는 최후 방어선일 뿐이다.
            logger.exception("소스 폴링 실패(미회계 경로) source=%s", source.id)


async def poll_source(source: Source) -> int:
    """소스 1개 수집. 저장 시도한 매칭 글 수를 반환(dedup 무시분 포함)."""
    adapter = get_adapter(source.type)
    # 폴링 시각을 먼저 커밋 — 이후 어떤 단계가 실패해도 poll_interval 은 지켜진다
    # (저장 실패가 매 tick 재요청 루프가 되던 경로 봉합 — 불변식 ④, 적대 리뷰 H4).
    source.last_polled_at = _now()
    await source.save(update_fields=["last_polled_at"])
    if adapter is None:
        return 0

    try:
        posts = await adapter.fetch(source, source.last_success_at)
        stored = await _store_matches(source, posts)
    except RateLimitedError as exc:
        await _record_failure(source, retry_after_sec=exc.retry_after_sec, rate_limited=True)
        return 0
    except Exception:
        # fetch 실패(FetchError)든 저장 실패든 동일하게 회계한다 — 커서 미전진 + health 반영.
        logger.exception("소스 수집/저장 실패 source=%s", source.id)
        await _record_failure(source, retry_after_sec=None, rate_limited=False)
        return 0

    # store 성공 → 커서 전진 + 상태 회복 (FR-5)
    _fail_counts.pop(source.id, None)
    source.last_success_at = source.last_polled_at
    source.health_status = HealthStatus.ok
    source.backoff_until = None
    await source.save(update_fields=["last_success_at", "health_status", "backoff_until"])
    return stored


async def _record_failure(
    source: Source, *, retry_after_sec: float | None, rate_limited: bool
) -> None:
    count = _fail_counts.get(source.id, 0) + 1
    _fail_counts[source.id] = count
    source.health_status = (
        HealthStatus.down if count >= _DOWN_AFTER_FAILURES else HealthStatus.degraded
    )
    if rate_limited:
        # 429/403 지수 backoff (FR-4). 서버의 Retry-After 가 더 길면 그쪽을 존중.
        delay = min(_BACKOFF_BASE_SEC * 2 ** (count - 1), _BACKOFF_CAP_SEC)
        if retry_after_sec:
            delay = max(delay, min(retry_after_sec, 24 * 3600))
        source.backoff_until = _now() + timedelta(seconds=delay)
    await source.save(update_fields=["health_status", "backoff_until"])


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
                # 어댑터가 절단하지 못한 경우의 모델 제약 방어(배치 전체 실패 방지)
                author=post.author[:255] if post.author else None,
                url=post.url[:1024] if post.url else None,
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
