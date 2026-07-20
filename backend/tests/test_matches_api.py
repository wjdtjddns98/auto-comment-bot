"""/api/matches 통합(실 Postgres) — 목록 필터/페이징·상세·권한."""
import uuid

import pytest

from app.auth import hash_password
from app.models import MatchedPost, PostStatus, Role, Source, SourceType, User

pytestmark = pytest.mark.db

PASSWORD = "pw-test-1234"


@pytest.fixture
async def reviewer_env(api_client):
    """reviewer 로그인 + 소스 1 + 매칭 글 3(new 2, ignored 1)."""
    user = await User.create(
        email=f"t-{uuid.uuid4().hex[:10]}@test.local",
        password_hash=hash_password(PASSWORD), role=Role.reviewer,
    )
    source = await Source.create(user=user, type=SourceType.community, config={})
    posts = [
        await MatchedPost.create(
            source=source, external_post_id=f"e-{i}-{uuid.uuid4().hex[:8]}",
            content=f"본문 {i}",
            status=PostStatus.ignored if i == 2 else PostStatus.new,
        )
        for i in range(3)
    ]
    await api_client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD})
    yield api_client, source, posts
    await MatchedPost.filter(source_id=source.id).delete()
    await Source.filter(id=source.id).delete()
    await user.delete()


async def test_list_filters_and_pagination(reviewer_env):
    client, source, _ = reviewer_env
    r = await client.get("/api/matches", params={"source_id": source.id})
    assert r.status_code == 200
    assert r.json()["total"] == 3

    r = await client.get("/api/matches", params={"source_id": source.id, "status": "new"})
    assert r.json()["total"] == 2

    r = await client.get(
        "/api/matches", params={"source_id": source.id, "page": 2, "size": 2}
    )
    body = r.json()
    assert body["total"] == 3 and len(body["items"]) == 1


async def test_detail_and_404(reviewer_env):
    client, source, posts = reviewer_env
    r = await client.get(f"/api/matches/{posts[0].id}")
    assert r.status_code == 200
    body = r.json()
    assert body["source_id"] == source.id and body["reply_actions"] == []
    assert (await client.get("/api/matches/999999999")).status_code == 404


async def test_matches_requires_auth(api_client):
    assert (await api_client.get("/api/matches")).status_code == 401
