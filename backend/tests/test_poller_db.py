"""poller 통합(실 Postgres) — dedup·backoff·커서 규율 (테스트 게이트 ③⑤)."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app import poller
from app.auth import hash_password
from app.models import (
    HealthStatus,
    Keyword,
    MatchedPost,
    Role,
    Source,
    SourceType,
    User,
)
from app.sources.base import FetchedPost, FetchError, RateLimitedError, stable_external_id

pytestmark = pytest.mark.db


class FakeAdapter:
    """fetch 호출 기록 + 시나리오 주입용."""

    can_write = False

    def __init__(self):
        self.calls: list = []  # (since,) 기록
        self.result: list[FetchedPost] | Exception = []

    async def fetch(self, source, since):
        self.calls.append(since)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
async def env(api_client, monkeypatch):
    """user+source+keyword + fake adapter. 테스트 후 이력→소스→유저 순 정리."""
    user = await User.create(
        email=f"t-{uuid.uuid4().hex[:10]}@test.local",
        password_hash=hash_password("pw"), role=Role.admin,
    )
    source = await Source.create(user=user, type=SourceType.community, config={})
    keyword = await Keyword.create(user=user, pattern="키워드")
    fake = FakeAdapter()
    monkeypatch.setattr(poller, "get_adapter", lambda _type: fake)
    monkeypatch.setattr(poller, "_fail_counts", {})
    yield source, keyword, fake
    await MatchedPost.filter(source_id=source.id).delete()
    await Keyword.filter(id=keyword.id).delete()
    await Source.filter(id=source.id).delete()
    await user.delete()


def _post(url: str, content: str = "키워드 포함 본문") -> FetchedPost:
    return FetchedPost(
        external_post_id=stable_external_id(url, "author", None),
        content=content, author="author", url=url,
    )


async def test_poll_stores_matches_and_dedups(env):
    """게이트 ⑤(DB 레벨): URL 변형 재투입 → 동일 external_post_id → 1행 유지."""
    source, keyword, fake = env
    fake.result = [_post("https://ex.com/p/1"), _post("https://ex.com/p/x", "무관한 글")]
    assert await poller.poll_source(source) == 1  # 매칭 1건만 저장

    rows = await MatchedPost.filter(source_id=source.id)
    assert len(rows) == 1 and rows[0].matched_keyword_id == keyword.id

    # 같은 논리 글을 쿼리스트링/스킴만 바꿔 재투입 → dedup 으로 여전히 1행 (FR-2·3)
    fake.result = [_post("http://ex.com/p/1?utm_source=share")]
    await poller.poll_source(source)
    assert await MatchedPost.filter(source_id=source.id).count() == 1


async def test_rate_limit_exponential_backoff(env):
    source, _, fake = env
    fake.result = RateLimitedError("HTTP 429")

    await poller.poll_source(source)
    assert source.health_status == HealthStatus.degraded
    first_backoff = source.backoff_until
    assert first_backoff is not None and source.last_success_at is None

    # backoff 중에는 폴링 대상에서 제외
    assert poller._is_due(source, datetime.now(UTC)) is False

    source.backoff_until = None  # 시간 경과 시뮬레이션
    await poller.poll_source(source)
    second_delay = source.backoff_until - source.last_polled_at
    assert second_delay >= timedelta(seconds=110)  # 60*2^1 근사 — 지수 증가 확인


async def test_cursor_advances_only_after_store_success(env):
    """게이트 ③: 다운 동안 커서 정지 → 복구 시 같은 since 로 백필."""
    source, _, fake = env
    t0 = datetime.now(UTC) - timedelta(hours=2)
    source.last_success_at = t0
    await source.save(update_fields=["last_success_at"])

    # 소스 다운: 커서는 t0 에 머문다
    fake.result = FetchError("down")
    await poller.poll_source(source)
    assert source.last_success_at == t0
    assert source.health_status == HealthStatus.degraded

    # 복구: adapter 가 받은 since 가 여전히 t0 → 다운타임 구간(t0 이후) 백필
    fake.result = [_post("https://ex.com/p/recovered")]
    stored = await poller.poll_source(source)
    assert stored == 1
    assert fake.calls[-1] == t0
    assert source.last_success_at is not None and source.last_success_at > t0
    assert source.health_status == HealthStatus.ok and source.backoff_until is None


async def test_unknown_adapter_marks_down(env, monkeypatch):
    # 미구현 어댑터는 조용히 skip 되지 않고 down 으로 표시된다(2차 리뷰 M6)
    source, _, _ = env
    monkeypatch.setattr(poller, "get_adapter", lambda _type: None)
    assert await poller.poll_source(source) == 0
    assert source.health_status == HealthStatus.down


async def test_poll_success_preserves_concurrent_reconcile_backoff(env, monkeypatch):
    """fetch 대기 중 조정 틱이 건 **최신** backoff/degraded 를 poll 성공 회계(stale 객체)가
    지우지 않는다(PR #43 검증 리뷰 High-2 — 2차 리뷰 R-1 의 역방향, 불변식 ④)."""
    source, _, _ = env
    future = datetime.now(UTC) + timedelta(minutes=5)

    class ConcurrentBackoffAdapter:
        can_write = False

        async def fetch(self, _source, since):
            # fetch I/O 대기 중 reconcile 의 429 회계가 끼어든 상황 재현 — DB 에만 반영
            await Source.filter(id=source.id).update(
                backoff_until=future, health_status=HealthStatus.degraded
            )
            return []

    monkeypatch.setattr(poller, "get_adapter", lambda _t: ConcurrentBackoffAdapter())
    await poller.poll_source(source)

    fresh = await Source.get(id=source.id)
    assert fresh.backoff_until is not None  # 방금 걸린 backoff 유지
    assert fresh.health_status == HealthStatus.degraded  # ok 로 덮지 않는다
    assert fresh.last_success_at is not None  # 수집 회계(커서 전진)는 정상


async def test_poll_failure_does_not_downgrade_reconcile_down(env, monkeypatch):
    """R9① 회귀: 조정 지속 실패로 down 도달한 소스에 poll 실패 1회가 겹쳐도 degraded 로
    역전되지 않는다 — health 는 poll/조정 두 카운터의 최대 기준."""
    source, _, fake = env
    monkeypatch.setattr(poller, "_reconcile_fail_counts", {source.id: 5})
    fake.result = FetchError("down")

    await poller.poll_source(source)

    assert source.health_status == HealthStatus.down


async def test_store_failure_is_accounted(env, monkeypatch):
    """저장 단계 실패도 fetch 실패와 동일하게 회계된다(커서 미전진 + degraded)."""
    source, _, fake = env
    fake.result = [_post("https://ex.com/p/store-fail")]

    async def boom(*_args, **_kwargs):
        raise RuntimeError("store 실패 주입")

    monkeypatch.setattr(poller, "_store_matches", boom)
    assert await poller.poll_source(source) == 0
    assert source.last_success_at is None
    assert source.health_status == HealthStatus.degraded
    assert source.last_polled_at is not None  # 선커밋 — poll_interval 준수
