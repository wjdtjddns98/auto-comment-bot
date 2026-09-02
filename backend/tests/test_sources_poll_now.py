"""`POST /api/sources/{id}/poll-now` 수동 검색(실 Postgres) — 반환 글 노출·회계·rate-limit·동시성."""
import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app import poller
from app.api import sources as sources_api
from app.auth import hash_password
from app.models import Keyword, MatchedPost, Role, Source, SourceType, User
from app.sources.base import FetchedPost, FetchError, RateLimitedError

pytestmark = pytest.mark.db

PASSWORD = "pw-test-1234"


class FakeAdapter:
    """결과 주입 + fetch 동시 실행 관측(gate 로 블로킹 가능)."""

    can_write = False

    def __init__(self):
        self.result: list[FetchedPost] | Exception = []
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.gate: asyncio.Event | None = None

    async def fetch(self, source, since):
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.gate is not None:
                await self.gate.wait()
            if isinstance(self.result, Exception):
                raise self.result
            return self.result
        finally:
            self.active -= 1


async def _login(client, role: Role) -> tuple[User, dict]:
    user = await User.create(
        email=f"t-{uuid.uuid4().hex[:10]}@test.local",
        password_hash=hash_password(PASSWORD), role=role,
    )
    await client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD})
    csrf = {"X-CSRF-Token": (await client.get("/api/auth/csrf")).json()["csrf_token"]}
    return user, csrf


@pytest.fixture
async def env(api_client, monkeypatch):
    """admin 로그인 + 소스(비활성) + 전역 키워드 + fake adapter."""
    user, csrf = await _login(api_client, Role.admin)
    source = await Source.create(user=user, type=SourceType.community, config={}, enabled=False)
    keyword = await Keyword.create(user=user, pattern="키워드")
    fake = FakeAdapter()
    monkeypatch.setattr(poller, "get_adapter", lambda _type: fake)
    monkeypatch.setattr(poller, "_fail_counts", {})
    monkeypatch.setattr(poller, "_reconcile_fail_counts", {})
    monkeypatch.setattr(poller, "_source_locks", {})
    yield api_client, csrf, source, fake
    await MatchedPost.filter(source_id=source.id).delete()
    await Keyword.filter(id=keyword.id).delete()
    await Source.filter(id=source.id).delete()
    await user.delete()


async def _reset_cooldown(source: Source) -> None:
    """수동 검색 최소 간격을 지나간 것으로 만든다(테스트 내 연속 호출용)."""
    await Source.filter(id=source.id).update(
        last_polled_at=datetime.now(UTC) - timedelta(seconds=sources_api._POLL_NOW_MIN_INTERVAL_SEC + 1)
    )


def _url(source: Source) -> str:
    return f"/api/sources/{source.id}/poll-now"


async def test_poll_now_returns_posts_and_counts_new_matches_only(env):
    """반환 글은 매칭 여부와 무관하게 전량 노출, `stored` 는 큐에 **새로** 들어간 건수만
    (재검색 시 dedup 으로 0) — 비활성 소스도 허용."""
    client, csrf, source, fake = env
    fake.result = [
        FetchedPost(external_post_id="p1", content="키워드 포함 글", author="a", url="https://x/1"),
        FetchedPost(external_post_id="p2", content="무관한 글"),
    ]
    r = await client.post(_url(source), headers=csrf)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [p["external_post_id"] for p in body["posts"]] == ["p1", "p2"]
    assert body["posts"][0]["content"] == "키워드 포함 글"
    assert body["fetched"] == 2 and body["stored"] == 1
    assert body["source"]["health_status"] == "ok"
    assert body["source"]["last_success_at"] is not None
    assert await MatchedPost.filter(source_id=source.id).count() == 1

    await _reset_cooldown(source)
    body = (await client.post(_url(source), headers=csrf)).json()
    assert body["fetched"] == 2 and body["stored"] == 0  # 같은 글 → dedup → 신규 0
    assert await MatchedPost.filter(source_id=source.id).count() == 1


async def test_poll_now_truncates_display_payload(env):
    """표시용 응답 상한 — 글 수 50·본문 1000자. `fetched` 는 전체 수를 유지한다."""
    client, csrf, source, fake = env
    fake.result = [
        FetchedPost(external_post_id=f"p{i}", content="x" * 5000) for i in range(60)
    ]
    body = (await client.post(_url(source), headers=csrf)).json()
    assert body["fetched"] == 60 and len(body["posts"]) == 50
    assert all(len(p["content"]) == 1000 for p in body["posts"])


async def test_poll_now_fetch_error_502_with_accounting(env):
    client, csrf, source, fake = env
    fake.result = FetchError("config.sns_account_id 의 threads 계정을 찾을 수 없습니다")
    r = await client.post(_url(source), headers=csrf)
    assert r.status_code == 502
    assert "threads 계정을 찾을 수 없습니다" in r.json()["detail"]
    s = await Source.get(id=source.id)
    assert s.health_status == "degraded" and s.last_error.startswith("FetchError")


async def test_poll_now_unsupported_adapter_502_sets_last_error(env, monkeypatch):
    client, csrf, source, _ = env
    monkeypatch.setattr(poller, "get_adapter", lambda _type: None)
    r = await client.post(_url(source), headers=csrf)
    assert r.status_code == 502 and "지원되지 않는 소스 타입" in r.json()["detail"]
    s = await Source.get(id=source.id)
    assert s.health_status == "down" and "지원되지 않는" in s.last_error


async def test_poll_now_rate_limit_maps_to_429_and_respects_backoff(env):
    """이번 호출이 429 를 맞으면 502 가 아니라 429(+Retry-After). backoff 동안 재호출도 429,
    만료되면 통과(불변식 ④)."""
    client, csrf, source, fake = env
    fake.result = RateLimitedError("429", retry_after_sec=600)
    r = await client.post(_url(source), headers=csrf)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    r = await client.post(_url(source), headers=csrf)
    assert r.status_code == 429 and "backoff" in r.json()["detail"]
    assert fake.calls == 1  # backoff 중엔 업스트림 호출 자체가 없다
    await Source.filter(id=source.id).update(
        backoff_until=datetime.now(UTC) - timedelta(seconds=1)
    )
    await _reset_cooldown(source)
    fake.result = []
    assert (await client.post(_url(source), headers=csrf)).status_code == 200


async def test_poll_now_cooldown_after_recent_poll(env):
    """직전 수집(스케줄이든 수동이든) 후 최소 간격 안에는 429 — 연타로 스케줄 하한 우회 불가."""
    client, csrf, source, fake = env
    fake.result = []
    assert (await client.post(_url(source), headers=csrf)).status_code == 200
    r = await client.post(_url(source), headers=csrf)
    assert r.status_code == 429 and "Retry-After" in r.headers
    assert fake.calls == 1


async def test_poll_now_409_while_source_is_being_polled(env):
    """스케줄 틱이 같은 소스를 fetch 중이면 409 — 동시 아웃바운드 2회 없음."""
    client, csrf, source, fake = env
    fake.gate = asyncio.Event()
    tick = asyncio.create_task(poller.poll_source(source))
    for _ in range(50):  # 틱이 락을 잡고 fetch 에 들어갈 때까지 양보
        await asyncio.sleep(0.01)
        if fake.active:
            break
    assert fake.active == 1
    r = await client.post(_url(source), headers=csrf)
    assert r.status_code == 409
    fake.gate.set()
    await tick
    assert fake.calls == 1


async def test_scheduled_and_manual_polls_are_serialized(env):
    """poll_tick 과 수동 수집을 동시에 돌려도 소스 락으로 fetch 가 겹치지 않는다."""
    _, _, source, fake = env
    await Source.filter(id=source.id).update(enabled=True)
    source = await Source.get(id=source.id)
    fake.result = []
    await asyncio.gather(
        poller.poll_tick(), poller.poll_source_detailed(source), poller.poll_source(source)
    )
    assert fake.calls == 3 and fake.max_active == 1


async def test_poll_now_requires_admin_csrf_and_existing_source(env, api_client):
    client, csrf, source, fake = env
    assert (await client.post(_url(source))).status_code == 403  # CSRF 없음
    assert (await client.post("/api/sources/999999999/poll-now", headers=csrf)).status_code == 404
    reviewer, rcsrf = await _login(api_client, Role.reviewer)
    try:
        assert (await client.post(_url(source), headers=rcsrf)).status_code == 403
    finally:
        await reviewer.delete()
    assert fake.calls == 0
