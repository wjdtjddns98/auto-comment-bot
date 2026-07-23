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
from app.sources.base import fit_external_id

logger = logging.getLogger(__name__)

_BACKOFF_BASE_SEC = 60
_BACKOFF_CAP_SEC = 3600
_DOWN_AFTER_FAILURES = 5

_fail_counts: dict[int, int] = {}
# 조정(reconcile) 실패 카운터 — poll 과 분리(R9①). 공용이면 수집 성공의 리셋이 조정
# 지속 실패(429 포함)의 지수 backoff 를 매번 최소치(60초)로 되돌리고(불변식 ④ 약화),
# _DOWN_AFTER_FAILURES 미도달·down→degraded 역전을 일으킨다. 해제는 reconcile_tick
# 틱말 집계(조정 성공/대상 소멸)에서만 한다.
_reconcile_fail_counts: dict[int, int] = {}
state: dict = {"last_tick": None}  # /health poller heartbeat (FR-17)
# 조정 전용 실패 상태(어댑터 2차 리뷰 R-2): 조정 조회가 실패 중인 소스는 수집 성공이
# health 를 ok 로 되돌리지 않는다 — 되돌리면 다음 조정 틱이 다시 degraded 로 낮추며
# 배지가 주기마다 깜빡여, 지속 장애(전송 계정 토큰 만료 등)가 일시 문제처럼 보인다.
# 갱신은 reconcile_tick 이 틱 단위로 집계하고, 조회 실패 시엔 행 처리 시점에 즉시
# 추가된다(PR #43 검증 리뷰 High-1 — 배치 반영 창 레이스 차단). in-memory(NFR-P1 단일
# 워커) — 재시작으로 리셋되면 다음 조정 틱(60초)까지 poll 성공이 ok 로 되돌리는 1회성
# 깜빡임이 있을 수 있다(재시작 이벤트 한정, 허용).
_reconcile_failing: set[int] = set()


def _now() -> datetime:
    return datetime.now(UTC)


def _is_due(source: Source, now: datetime) -> bool:
    if source.backoff_until and source.backoff_until > now:
        return False
    if source.last_polled_at is None:
        return True
    return source.last_polled_at + timedelta(seconds=source.poll_interval_sec) <= now


def forget_source(source_id: int) -> None:
    """소스 삭제 시 실패 카운터/조정 실패 상태 정리(미세 누수 방지)."""
    _fail_counts.pop(source_id, None)
    _reconcile_fail_counts.pop(source_id, None)
    _reconcile_failing.discard(source_id)


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
        # 어댑터 미구현 타입이 "정상(ok)"으로 보이며 영구 skip 되지 않게 명시 회계.
        source.health_status = HealthStatus.down
        await source.save(update_fields=["health_status"])
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
    fields = ["last_success_at"]
    # fetch 대기 중 조정(reconcile) 틱이 같은 소스에 걸었을 수 있는 **최신** backoff 를
    # 이 stale 객체의 backoff_until=None 저장으로 지우지 않는다(PR #43 검증 리뷰
    # High-2 — 2차 리뷰 R-1 의 역방향). 미래 backoff 가 새로 걸려 있으면 그 회계
    # (429 degraded)를 존중해 backoff/health 둘 다 건드리지 않는다.
    row = await Source.filter(id=source.id).values_list("backoff_until", flat=True)
    backoff_active = bool(row) and row[0] is not None and row[0] > _now()
    if not backoff_active:
        source.backoff_until = None
        fields.append("backoff_until")
        # 조정 조회가 실패 중이면 health 는 건드리지 않는다(R-2 깜빡임 방지) — 이 객체의
        # health 값 자체가 stale 일 수 있으므로 조건부 대입이 아니라 저장 필드에서 제외한다.
        # 카운터도 함께 확인한다(R9 2차 리뷰 High-1): 429 조정 실패는 플래그를 세우지
        # 않으므로 플래그만 보면 backoff 만료 직후 수집 성공이 down 을 ok 로 되돌린다.
        if source.id not in _reconcile_failing and not _reconcile_fail_counts.get(source.id):
            source.health_status = HealthStatus.ok
            fields.append("health_status")
    await source.save(update_fields=fields)
    return stored


async def _record_failure(
    source: Source, *, retry_after_sec: float | None, rate_limited: bool,
    reconcile: bool = False,
) -> None:
    counts = _reconcile_fail_counts if reconcile else _fail_counts
    count = counts.get(source.id, 0) + 1
    counts[source.id] = count
    # health 는 두 카운터의 최대 기준 — 한쪽 경로의 낮은 카운트 회계가 다른 쪽이 이미
    # 도달한 down 을 degraded 로 되돌리지 않는다(R9① 역전 방지).
    other = _fail_counts if reconcile else _reconcile_fail_counts
    effective = max(count, other.get(source.id, 0))
    source.health_status = (
        HealthStatus.down if effective >= _DOWN_AFTER_FAILURES else HealthStatus.degraded
    )
    if rate_limited:
        # 429/403 지수 backoff (FR-4). 서버의 Retry-After 가 더 길면 그쪽을 존중.
        delay = min(_BACKOFF_BASE_SEC * 2 ** (count - 1), _BACKOFF_CAP_SEC)
        if retry_after_sec:
            delay = max(delay, min(retry_after_sec, 24 * 3600))
        source.backoff_until = _now() + timedelta(seconds=delay)
        await source.save(update_fields=["health_status", "backoff_until"])
    else:
        # 일반 실패에는 backoff 를 걸지 않는다 — 만료된 backoff 가 API 에 계속 노출되지
        # 않게 정리하되, 다른 채널(poll↔reconcile interleave)이 fetch 대기 중 방금 건
        # **미래** backoff 를 이 stale 객체의 무조건 None 저장으로 지우지 않는다(R11
        # TOCTOU, 불변식 ④). 성공 경로(poll_source)의 재조회 가드와 같은 방향이되,
        # 만료분만 지우는 조건부 원자 update 라 재조회↔저장 사이 경쟁 창 자체가 없다.
        await source.save(update_fields=["health_status"])
        await Source.filter(id=source.id, backoff_until__lte=_now()).update(backoff_until=None)


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
                # 저장 경계에서도 해시 고정 길이화 — 어댑터가 빠뜨려도 접두사 충돌 방지
                external_post_id=fit_external_id(post.external_post_id),
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
