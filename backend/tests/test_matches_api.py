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


async def test_list_truncates_content_detail_full(reviewer_env):
    client, source, _ = reviewer_env
    long_content = "가" * 1200
    post = await MatchedPost.create(
        source=source, external_post_id=f"long-{uuid.uuid4().hex[:8]}", content=long_content
    )
    try:
        r = await client.get("/api/matches", params={"source_id": source.id, "size": 100})
        item = next(i for i in r.json()["items"] if i["id"] == post.id)
        assert len(item["content"]) == 500  # 목록은 요약만
        r = await client.get(f"/api/matches/{post.id}")
        assert r.json()["content"] == long_content  # 상세는 전체
    finally:
        await post.delete()


async def test_matches_requires_auth(api_client):
    assert (await api_client.get("/api/matches")).status_code == 401


# ── 템플릿 일괄 발송용 렌더 미리보기(POST /api/matches/render-template) ─────────
#
# 일괄 발송이 N건에 동일 문구를 보내면 중복 콘텐츠로 스팸 판정될 수 있다. 서버가 건마다
# {{a|b|c}} 변형을 독립적으로 뽑아 문구를 갈라준다. **렌더는 전송이 아니다** — 사람이
# 미리보기를 보고 approve 를 눌러야 나간다(불변식 ①).


@pytest.fixture
async def render_env(api_client):
    """reviewer 로그인 + 소스 + 매칭 2건(author/keyword 보유) + 템플릿."""
    from app.models import Keyword, ReplyTemplate

    user = await User.create(
        email=f"t-{uuid.uuid4().hex[:10]}@test.local",
        password_hash=hash_password(PASSWORD), role=Role.reviewer,
    )
    source = await Source.create(user=user, type=SourceType.community, config={})
    keyword = await Keyword.create(user=user, pattern="간식")
    posts = [
        await MatchedPost.create(
            source=source, external_post_id=f"r-{i}-{uuid.uuid4().hex[:8]}",
            content=f"본문 {i}", author=f"user_{i}", url=f"https://ex.am/{i}",
            matched_keyword=keyword,
        )
        for i in range(2)
    ]
    template = await ReplyTemplate.create(
        user=user, name="간식 안내",
        body="{{author}}님 {{안녕하세요|반갑습니다}} {{keyword}} 급여량 계산기 참고하세요",
    )
    await api_client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD})
    yield api_client, posts, template, user
    await MatchedPost.filter(source_id=source.id).delete()
    await ReplyTemplate.filter(id=template.id).delete()
    await Keyword.filter(id=keyword.id).delete()
    await Source.filter(id=source.id).delete()
    await user.delete()


async def test_render_template_substitutes_per_match(render_env):
    """건마다 그 글의 author·keyword 로 치환된다 — 변형은 후보 중 하나."""
    client, posts, template, _user = render_env
    r = await client.post(
        "/api/matches/render-template",
        json={"template_id": template.id, "match_ids": [p.id for p in posts]},
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["match_id"] for i in items] == [p.id for p in posts]  # 요청 순서 유지
    for item, post in zip(items, posts, strict=True):
        assert item["error"] is None
        assert item["body"].startswith(f"{post.author}님 ")
        assert "간식 급여량 계산기 참고하세요" in item["body"]
        assert any(v in item["body"] for v in ("안녕하세요", "반갑습니다"))
        assert "{{" not in item["body"]  # 미치환 토큰이 남지 않는다


async def test_render_template_reports_per_item_errors(render_env):
    """한 건의 실패가 나머지 미리보기를 막지 않는다 — 그 건만 body=null + error."""
    from app.models import ReplyTemplate

    client, posts, _tpl, user = render_env
    # author 가 없는 매칭 + 존재하지 않는 매칭 id 를 섞는다
    no_author = await MatchedPost.create(
        source_id=posts[0].source_id, external_post_id=f"na-{uuid.uuid4().hex[:8]}",
        content="본문", author=None,
    )
    tpl = await ReplyTemplate.create(user=user, name="author 필요", body="{{author}}님 안녕하세요")
    try:
        r = await client.post(
            "/api/matches/render-template",
            json={"template_id": tpl.id, "match_ids": [posts[0].id, no_author.id, 99999999]},
        )
        assert r.status_code == 200
        ok, missing, absent = r.json()["items"]
        assert ok["body"] == f"{posts[0].author}님 안녕하세요" and ok["error"] is None
        assert missing["body"] is None and "author" in missing["error"]
        assert absent["body"] is None and "매칭이 없습니다" in absent["error"]
    finally:
        await no_author.delete()
        await ReplyTemplate.filter(id=tpl.id).delete()


async def test_render_template_unknown_variable_error(render_env):
    """오타 변수는 그 건 error 로 — 조용히 비워서 게시되게 하지 않는다."""
    from app.models import ReplyTemplate

    client, posts, _tpl, user = render_env
    tpl = await ReplyTemplate.create(user=user, name="오타", body="{{autor}}님")
    try:
        r = await client.post(
            "/api/matches/render-template",
            json={"template_id": tpl.id, "match_ids": [posts[0].id]},
        )
        assert r.status_code == 200
        item = r.json()["items"][0]
        assert item["body"] is None and "autor" in item["error"]
    finally:
        await ReplyTemplate.filter(id=tpl.id).delete()


async def test_render_template_rejects_bad_input(render_env):
    """비활성/없는 템플릿 422 · 빈 match_ids 422 · 미지 키 422(extra 금지)."""
    from app.models import ReplyTemplate

    client, posts, template, _user = render_env
    assert (
        await client.post(
            "/api/matches/render-template",
            json={"template_id": 99999999, "match_ids": [posts[0].id]},
        )
    ).status_code == 422
    await ReplyTemplate.filter(id=template.id).update(enabled=False)
    assert (
        await client.post(
            "/api/matches/render-template",
            json={"template_id": template.id, "match_ids": [posts[0].id]},
        )
    ).status_code == 422
    await ReplyTemplate.filter(id=template.id).update(enabled=True)
    assert (
        await client.post(
            "/api/matches/render-template", json={"template_id": template.id, "match_ids": []}
        )
    ).status_code == 422
    assert (
        await client.post(
            "/api/matches/render-template",
            json={"template_id": template.id, "match_ids": [posts[0].id], "x": 1},
        )
    ).status_code == 422


async def test_render_template_requires_auth(api_client):
    r = await api_client.post(
        "/api/matches/render-template", json={"template_id": 1, "match_ids": [1]}
    )
    assert r.status_code == 401
