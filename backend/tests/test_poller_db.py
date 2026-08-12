"""poller 통합(실 Postgres) — dedup·backoff·커서 규율 (테스트 게이트 ③⑤)."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app import poller
from app import sources as sources_registry
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
    monkeypatch.setattr(poller, "_reconcile_fail_counts", {})
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


async def test_poll_success_keeps_reconcile_down_health(env, monkeypatch):
    """R9① 2차 회귀(High-1): 조정 429 지속으로 down 인 소스는 poll 성공이 health 를 ok 로
    되돌리지 않는다 — 429 는 _reconcile_failing 플래그를 세우지 않으므로 ok 복귀 조건이
    플래그뿐 아니라 조정 실패 카운터도 확인해야 한다."""
    source, _, fake = env
    monkeypatch.setattr(poller, "_reconcile_fail_counts", {source.id: 5})
    monkeypatch.setattr(poller, "_reconcile_failing", set())
    await Source.filter(id=source.id).update(health_status=HealthStatus.down)
    fake.result = []

    await poller.poll_source(source)

    fresh = await Source.get(id=source.id)
    assert fresh.health_status == HealthStatus.down  # ok 로 깜빡이지 않는다
    assert fresh.last_success_at is not None  # 수집 회계(커서 전진)는 정상


async def test_poll_failure_does_not_downgrade_reconcile_down(env, monkeypatch):
    """R9① 회귀: 조정 지속 실패로 down 도달한 소스에 poll 실패 1회가 겹쳐도 degraded 로
    역전되지 않는다 — health 는 poll/조정 두 카운터의 최대 기준."""
    source, _, fake = env
    monkeypatch.setattr(poller, "_reconcile_fail_counts", {source.id: 5})
    fake.result = FetchError("down")

    await poller.poll_source(source)

    assert source.health_status == HealthStatus.down


async def test_poll_failure_preserves_concurrent_reconcile_backoff(env, monkeypatch):
    """R11 회귀(R9 2차 리뷰 Medium-1): fetch 대기 중 조정 틱이 건 **미래** backoff 를
    poll 일반 실패 회계(stale 객체의 backoff_until=None 저장)가 지우지 않는다(불변식 ④)."""
    source, _, _ = env
    future = datetime.now(UTC) + timedelta(minutes=5)

    class ConcurrentBackoffThenFailAdapter:
        can_write = False

        async def fetch(self, _source, since):
            # fetch I/O 대기 중 reconcile 의 429 회계가 끼어들고, 이어서 이 poll 은
            # 일반 실패로 끝나는 교차 상황 재현 — backoff 는 DB 에만 반영돼 있다.
            await Source.filter(id=source.id).update(
                backoff_until=future, health_status=HealthStatus.degraded
            )
            raise FetchError("poll 일반 실패")

    monkeypatch.setattr(poller, "get_adapter", lambda _t: ConcurrentBackoffThenFailAdapter())
    await poller.poll_source(source)

    fresh = await Source.get(id=source.id)
    assert fresh.backoff_until == future  # 방금 걸린 미래 backoff 그대로 유지(독립 리뷰 L1)
    assert fresh.health_status == HealthStatus.degraded  # 실패 회계 자체는 정상


async def test_poll_failure_clears_expired_backoff(env):
    """R11 가드의 반대 방향: 이미 **만료된** backoff 는 일반 실패 회계가 계속 정리한다 —
    지나간 만료 시각이 API 에 남아 노출되지 않는다(기존 동작 보존)."""
    source, _, fake = env
    past = datetime.now(UTC) - timedelta(minutes=5)
    await Source.filter(id=source.id).update(backoff_until=past)
    source.backoff_until = past
    fake.result = FetchError("일반 실패")

    await poller.poll_source(source)

    fresh = await Source.get(id=source.id)
    assert fresh.backoff_until is None


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


async def test_rate_limit_backoff_does_not_shorten_longer_existing(env):
    """R12 회귀: rate-limit 회계가 다른 채널(조정)이 이미 건 **더 긴** backoff 를 자기
    카운터 기준 짧은 후보로 덮어써 만료 시각을 단축하지 않는다(불변식 ④) — 후보>현재일
    때만 원자적으로 덮어쓴다. 미수정 코드(무조건 덮어쓰기)에서 실패."""
    source, _, fake = env
    # 조정 틱이 서버 Retry-After=3600 로 건 긴 backoff 가 이미 DB 에 반영된 상황
    long_backoff = datetime.now(UTC) + timedelta(seconds=3600)
    await Source.filter(id=source.id).update(backoff_until=long_backoff)
    # poll 이 첫 429(count=1 → 60초 후보)를 맞음 — 짧은 후보로 긴 backoff 를 단축하면 안 됨
    fake.result = RateLimitedError("HTTP 429")

    await poller.poll_source(source)

    fresh = await Source.get(id=source.id)
    assert fresh.backoff_until == long_backoff  # 긴 backoff 유지(미수정 코드는 now+60 으로 단축)


async def test_rate_limit_backoff_extends_when_candidate_longer(env):
    """R12 가드의 반대 방향: 후보가 현재 backoff 보다 길면 정상적으로 연장한다 —
    조건부 원자 update 가 정당한 backoff 증가를 막지 않는다(기존 동작 보존)."""
    source, _, fake = env
    short = datetime.now(UTC) + timedelta(seconds=30)
    await Source.filter(id=source.id).update(backoff_until=short)
    fake.result = RateLimitedError("HTTP 429", retry_after_sec=3600)

    await poller.poll_source(source)

    fresh = await Source.get(id=source.id)
    assert fresh.backoff_until is not None and fresh.backoff_until > short


async def test_poll_success_atomic_backoff_clear_survives_connection_race(env, monkeypatch):
    """R13 회귀: poll 성공의 backoff 정리가 재조회↔save 사이(커넥션 레벨 TOCTOU)에 조정
    틱이 커밋한 **미래** backoff 를 지우지 않는다 — 조건부 원자 update(WHERE backoff_until
    <=now)로 교체해 경쟁 창을 닫는다. 미수정 코드(재조회 후 backoff_until=None 저장)에서 실패."""
    source, _, fake = env
    fake.result = []  # 수집 성공(매칭 0) → 성공 경로 진입
    future = datetime.now(UTC) + timedelta(minutes=5)
    real_save = source.save
    injected = {"done": False}

    async def save_with_race(*args, **kwargs):
        # 성공 경로의 조건부 원자 update(cleared/ok_set) 실행 **전에** 다른 채널(조정 틱)이
        # 미래 backoff 를 커밋하는 커넥션 레벨 인터리브 재현(1회만) — 구 코드(재조회 후 stale
        # save)는 이 개입을 놓쳐 backoff 를 지웠고, 새 코드는 WHERE 로 재평가해 보존한다.
        if not injected["done"] and "last_success_at" in (kwargs.get("update_fields") or []):
            injected["done"] = True
            await Source.filter(id=source.id).update(
                backoff_until=future, health_status=HealthStatus.degraded
            )
        return await real_save(*args, **kwargs)

    monkeypatch.setattr(source, "save", save_with_race)
    await poller.poll_source(source)

    fresh = await Source.get(id=source.id)
    assert fresh.backoff_until == future  # 조정이 방금 건 미래 backoff 유지(미수정 코드는 None)
    assert fresh.last_success_at is not None  # 커서 전진은 정상


async def test_rate_limit_commits_backoff_before_health(env, monkeypatch):
    """M1 회귀(독립 리뷰): rate-limit 회계는 backoff 를 health 보다 **먼저** 커밋한다
    (R13 성공 경로와 대칭). 순서가 반대면 두 UPDATE 사이에 health=degraded 는 커밋됐는데
    backoff_until 은 아직 NULL 인 창이 생겨, 그 순간 poll_tick 스냅샷이 _is_due 를 통과해
    방금 429 를 맞은 소스에 추가 fetch 를 할 수 있다(불변식 ④). 순서가 반대인 코드에서 실패."""
    source, _, fake = env
    fake.result = RateLimitedError("HTTP 429")  # 사전 backoff 없음 → NULL 에서 시작
    real_save = source.save
    seen: dict = {}

    async def save_probe(*args, **kwargs):
        # health_status 저장 시점엔 backoff 가 이미 DB 에 커밋돼 있어야 한다(순서 보장 검증).
        if "health_status" in (kwargs.get("update_fields") or []):
            row = await Source.filter(id=source.id).values_list("backoff_until", flat=True)
            seen["backoff_at_health_save"] = row[0] if row else None
        return await real_save(*args, **kwargs)

    monkeypatch.setattr(source, "save", save_probe)
    await poller.poll_source(source)

    # backoff 가 health 보다 먼저 커밋됨(순서 반대인 미수정 코드는 이 시점에 None)
    assert seen["backoff_at_health_save"] is not None


async def test_poll_success_clears_already_expired_backoff(env):
    """R13 평시 경로(독립 리뷰 L1): 경쟁 없이 이미 **만료된** backoff 도 성공 회계가 정상
    정리하고 health 를 ok 로 회복한다 — R11 실패 경로 test_poll_failure_clears_expired_backoff
    의 성공 경로 대칭(조건부 원자 update 로 교체 후에도 기존 정리 동작 보존)."""
    source, _, fake = env
    past = datetime.now(UTC) - timedelta(minutes=5)
    await Source.filter(id=source.id).update(
        backoff_until=past, health_status=HealthStatus.degraded
    )
    fake.result = []

    await poller.poll_source(source)

    fresh = await Source.get(id=source.id)
    assert fresh.backoff_until is None  # 만료분 정리
    assert fresh.health_status == HealthStatus.ok  # health 회복
    assert source.backoff_until is None  # in-memory 정합


# ── R17: 실패 원인을 DB 에 남긴다(진단성) ────────────────────────────────────────
#
# 실측(2026-08-11): 소스가 degraded/down 인데 **이유가 DB 에 없어** 컨테이너 로그를 봐야만
# 원인(고아 계정 참조)을 알 수 있었다. 로그가 롤링되면 사라지고 화면에도 못 띄운다.


class _BoomAdapter:
    """FetchError 를 던지는 어댑터 — 어댑터 메시지는 안전 텍스트라는 계약."""

    can_write = False

    def __init__(self, message: str = "config.sns_account_id 의 threads 계정을 찾을 수 없습니다"):
        self.message = message

    async def fetch(self, source, since):
        from app.sources.base import FetchError

        raise FetchError(self.message)


class _SecretLeakAdapter:
    """예기치 못한 예외(어댑터가 통제하지 않는 메시지) — 원문이 DB 로 새면 안 된다."""

    can_write = False

    async def fetch(self, source, since):
        raise RuntimeError("token=SECRET-abc123 로 요청 실패")


async def test_poll_records_last_error_and_clears_on_success(api_client, monkeypatch):
    """실패 시 원인 요약 저장 → 성공하면 비워진다."""
    user = await User.create(
        email=f"t-{uuid.uuid4().hex[:10]}@test.local",
        password_hash=hash_password("pw-test-1234"), role=Role.admin,
    )
    source = await Source.create(
        user=user, type=SourceType.community, config={"rss_url": "https://ex.am/feed"},
        poll_interval_sec=60,
    )
    try:
        monkeypatch.setattr(poller, "_fail_counts", {})
        monkeypatch.setattr(poller, "_reconcile_fail_counts", {})
        monkeypatch.setitem(
            sources_registry._ADAPTERS, SourceType.community, _BoomAdapter()
        )
        await poller.poll_source(source)
        await source.refresh_from_db()
        assert source.health_status == HealthStatus.degraded
        assert "FetchError" in source.last_error
        assert "계정을 찾을 수 없습니다" in source.last_error  # 진단에 쓰이는 실제 정보
        assert source.last_error_at is not None

        # 수집이 성공하면 원인은 비워진다(배지 회복과 같은 UPDATE)
        class _OkAdapter:
            can_write = False

            async def fetch(self, source, since):
                return []

        monkeypatch.setitem(sources_registry._ADAPTERS, SourceType.community, _OkAdapter())
        poller.forget_source(source.id)
        await poller.poll_source(source)
        await source.refresh_from_db()
        assert source.health_status == HealthStatus.ok
        assert source.last_error is None and source.last_error_at is None
    finally:
        poller.forget_source(source.id)
        await Source.filter(id=source.id).delete()
        await user.delete()


async def test_poll_last_error_never_leaks_unexpected_exception_text(
    api_client, monkeypatch
):
    """예기치 못한 예외의 원문은 DB 에 담지 않는다 — 타입명만(불변식 ③)."""
    user = await User.create(
        email=f"t-{uuid.uuid4().hex[:10]}@test.local",
        password_hash=hash_password("pw-test-1234"), role=Role.admin,
    )
    source = await Source.create(
        user=user, type=SourceType.community, config={"rss_url": "https://ex.am/feed"},
        poll_interval_sec=60,
    )
    try:
        monkeypatch.setattr(poller, "_fail_counts", {})
        monkeypatch.setattr(poller, "_reconcile_fail_counts", {})
        monkeypatch.setitem(
            sources_registry._ADAPTERS, SourceType.community, _SecretLeakAdapter()
        )
        await poller.poll_source(source)
        await source.refresh_from_db()
        assert source.last_error == "내부 오류: RuntimeError"
        assert "SECRET-abc123" not in source.last_error
        assert "token" not in source.last_error
    finally:
        poller.forget_source(source.id)
        await Source.filter(id=source.id).delete()
        await user.delete()


async def test_sources_api_exposes_last_error(api_client, monkeypatch):
    """GET /api/sources 가 원인을 함께 반환한다 — FE 배지 hover 용."""
    user = await User.create(
        email=f"t-{uuid.uuid4().hex[:10]}@test.local",
        password_hash=hash_password("pw-test-1234"), role=Role.admin,
    )
    source = await Source.create(
        user=user, type=SourceType.community, config={"rss_url": "https://ex.am/feed"},
        poll_interval_sec=60,
    )
    try:
        monkeypatch.setitem(
            sources_registry._ADAPTERS, SourceType.community, _BoomAdapter("피드 응답 형식 이상")
        )
        await poller.poll_source(source)
        await api_client.post(
            "/api/auth/login", json={"email": user.email, "password": "pw-test-1234"}
        )
        r = await api_client.get("/api/sources")
        assert r.status_code == 200
        item = next(s for s in r.json() if s["id"] == source.id)
        assert "피드 응답 형식 이상" in item["last_error"]
        assert item["last_error_at"] is not None
    finally:
        poller.forget_source(source.id)
        await Source.filter(id=source.id).delete()
        await user.delete()
