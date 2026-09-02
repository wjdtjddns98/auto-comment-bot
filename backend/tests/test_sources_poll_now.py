"""`POST /api/sources/{id}/poll-now` 수동 검색(실 Postgres) — 반환 글 노출·회계·backoff 거절."""
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
    can_write = False

    def __init__(self):
        self.result: list[FetchedPost] | Exception = []

    async def fetch(self, source, since):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
async def env(api_client, monkeypatch):
    """admin 로그인 + 소스(비활성) + 전역 키워드 + fake adapter."""
    user = await User.create(
        email=f"t-{uuid.uuid4().hex[:10]}@test.local",
        password_hash=hash_password(PASSWORD), role=Role.admin,
    )
    await api_client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD})
    csrf = {"X-CSRF-Token": (await api_client.get("/api/auth/csrf")).json()["csrf_token"]}
    source = await Source.create(user=user, type=SourceType.community, config={}, enabled=False)
    keyword = await Keyword.create(user=user, pattern="키워드")
    fake = FakeAdapter()
    monkeypatch.setattr(poller, "get_adapter", lambda _type: fake)
    monkeypatch.setattr(poller, "_fail_counts", {})
    monkeypatch.setattr(poller, "_reconcile_fail_counts", {})
    monkeypatch.setattr(sources_api, "_poll_now_inflight", set())
    yield api_client, csrf, source, fake
    await MatchedPost.filter(source_id=source.id).delete()
    await Keyword.filter(id=keyword.id).delete()
    await Source.filter(id=source.id).delete()
    await user.delete()


async def test_poll_now_returns_posts_and_stores_matches(env):
    """반환 글은 매칭 여부와 무관하게 전량 노출, 큐 저장은 매칭분만 — 비활성 소스도 허용."""
    client, csrf, source, fake = env
    fake.result = [
        FetchedPost(external_post_id="p1", content="키워드 포함 글", author="a", url="https://x/1"),
        FetchedPost(external_post_id="p2", content="무관한 글"),
    ]
    r = await client.post(f"/api/sources/{source.id}/poll-now", headers=csrf)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [p["external_post_id"] for p in body["posts"]] == ["p1", "p2"]
    assert body["posts"][0]["content"] == "키워드 포함 글"
    assert body["stored"] == 1
    assert body["source"]["health_status"] == "ok"
    assert body["source"]["last_success_at"] is not None
    assert await MatchedPost.filter(source_id=source.id).count() == 1


async def test_poll_now_fetch_error_502_with_accounting(env):
    client, csrf, source, fake = env
    fake.result = FetchError("config.sns_account_id 의 threads 계정을 찾을 수 없습니다")
    r = await client.post(f"/api/sources/{source.id}/poll-now", headers=csrf)
    assert r.status_code == 502
    assert "threads 계정을 찾을 수 없습니다" in r.json()["detail"]
    s = await Source.get(id=source.id)
    assert s.health_status == "degraded" and s.last_error.startswith("FetchError")


async def test_poll_now_respects_backoff(env):
    """429 를 맞은 소스는 backoff 동안 수동 검색도 거절한다(불변식 ④)."""
    client, csrf, source, fake = env
    fake.result = RateLimitedError("429", retry_after_sec=600)
    assert (await client.post(f"/api/sources/{source.id}/poll-now", headers=csrf)).status_code == 502
    r = await client.post(f"/api/sources/{source.id}/poll-now", headers=csrf)
    assert r.status_code == 429
    # 만료된 backoff 는 통과
    await Source.filter(id=source.id).update(
        backoff_until=datetime.now(UTC) - timedelta(seconds=1)
    )
    fake.result = []
    assert (await client.post(f"/api/sources/{source.id}/poll-now", headers=csrf)).status_code == 200


async def test_poll_now_requires_admin_csrf_and_existing_source(env):
    client, csrf, source, fake = env
    assert (await client.post(f"/api/sources/{source.id}/poll-now")).status_code == 403
    assert (await client.post("/api/sources/999999999/poll-now", headers=csrf)).status_code == 404
