"""GET /api/reply-actions 전역 감사 로그 통합(실 Postgres) — 이슈 #101.

이력 행은 approve/ignore 를 **실제로 호출해서** 만든다. 라우트가 읽는 것과 승인 플로우가
쓰는 것이 같은 계약임을 함께 고정하기 위해서다(스키마만 맞춘 픽스처였다면
`matched_post_id`·`template_id` 매핑 실수를 못 잡는다).
"""
import uuid

import pytest

from app.auth import hash_password
from app.models import (
    MatchedPost,
    PostStatus,
    ReplyActionLog,
    ReplyTemplate,
    Role,
    Source,
    SourceType,
    User,
)

pytestmark = pytest.mark.db

PASSWORD = "pw-test-1234"


@pytest.fixture
async def audit_env(api_client):
    """reviewer 로그인 + 커뮤니티 소스 + 매칭 2건 + 템플릿 1개.

    커뮤니티(can_write=False) 소스라 approve 가 외부 전송 없이 `approved` 만 기록한다 —
    감사 로그 조회 테스트에 어댑터 mock 이 필요 없다.
    """
    user = await User.create(
        email=f"t-{uuid.uuid4().hex[:10]}@test.local",
        password_hash=hash_password(PASSWORD), role=Role.reviewer,
    )
    source = await Source.create(
        user=user, type=SourceType.community, config={"rss_url": "https://ex.am/feed"}
    )
    posts = [
        await MatchedPost.create(
            source=source, external_post_id=f"a-{i}-{uuid.uuid4().hex[:8]}", content=f"본문 {i}"
        )
        for i in range(2)
    ]
    template = await ReplyTemplate.create(user=user, name="안내", body="안녕하세요")
    await api_client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD})
    token = (await api_client.get("/api/auth/csrf")).json()["csrf_token"]
    yield api_client, {"X-CSRF-Token": token}, posts, template, user
    await ReplyActionLog.filter(matched_post_id__in=[p.id for p in posts]).delete()
    await MatchedPost.filter(source_id=source.id).delete()
    await ReplyTemplate.filter(id=template.id).delete()
    await Source.filter(id=source.id).delete()
    await user.delete()


async def test_global_list_links_match_and_orders_newest_first(audit_env):
    """전역 목록은 여러 매칭의 이력을 최신순으로 묶어 주고, 각 행에 matched_post_id 가 있다."""
    client, csrf, posts, template, user = audit_env
    r = await client.post(
        f"/api/matches/{posts[0].id}/approve",
        json={"final_body": "안녕하세요", "template_id": template.id}, headers=csrf,
    )
    assert r.status_code == 200, r.text
    assert (await client.post(f"/api/matches/{posts[1].id}/ignore", headers=csrf)).status_code == 200

    rows = (await client.get("/api/reply-actions")).json()
    mine = [a for a in rows if a["matched_post_id"] in {p.id for p in posts}]
    assert len(mine) == 2
    # 최신순 — 나중에 부른 ignore(canceled)가 앞
    assert [a["action"] for a in mine] == ["canceled", "approved"]
    canceled, approved = mine
    assert canceled["matched_post_id"] == posts[1].id and canceled["template_id"] is None
    assert approved["matched_post_id"] == posts[0].id
    assert approved["template_id"] == template.id
    assert approved["reviewer_user_id"] == user.id
    assert approved["external_reply_id"] is None and approved["error"] is None
    # 목록을 부풀리거나 계정 경계를 우회하는 필드는 싣지 않는다
    assert "final_body" not in approved and "sns_account_id" not in approved


async def test_match_id_filter_and_limit(audit_env):
    """?match_id= 는 그 매칭만 · limit 은 최신부터 자르고 상한(500)을 넘기면 422."""
    client, csrf, posts, _template, _user = audit_env
    await client.post(
        f"/api/matches/{posts[0].id}/approve", json={"final_body": "본문"}, headers=csrf
    )
    await client.post(f"/api/matches/{posts[1].id}/ignore", headers=csrf)

    only = (await client.get("/api/reply-actions", params={"match_id": posts[0].id})).json()
    assert [a["matched_post_id"] for a in only] == [posts[0].id]

    # 없는 매칭 → 빈 배열(404 아님 — 이력이 아직 없는 매칭과 구분할 이유가 없다)
    assert (await client.get("/api/reply-actions", params={"match_id": 99999999})).json() == []

    newest = (await client.get("/api/reply-actions", params={"limit": 1})).json()
    assert len(newest) == 1
    assert (await client.get("/api/reply-actions", params={"limit": 501})).status_code == 422
    assert (await client.get("/api/reply-actions", params={"limit": 0})).status_code == 422


async def test_requires_login(api_client):
    assert (await api_client.get("/api/reply-actions")).status_code == 401


async def test_sending_claimed_at_exposed_on_approve(audit_env):
    """#101 요청 2-a: 승인 전 null → 승인(CAS 클레임) 후 전이 시각이 응답에 실린다."""
    client, csrf, posts, _template, _user = audit_env
    match_id = posts[0].id
    assert (await client.get(f"/api/matches/{match_id}")).json()["sending_claimed_at"] is None

    await client.post(
        f"/api/matches/{match_id}/approve", json={"final_body": "본문"}, headers=csrf
    )
    detail = (await client.get(f"/api/matches/{match_id}")).json()
    assert detail["status"] == PostStatus.replied.value
    claimed_at = detail["sending_claimed_at"]
    assert claimed_at is not None
    # 목록 응답에도 같은 값이 실린다(FE 가 목록에서도 전이 시각을 쓴다)
    items = (await client.get("/api/matches", params={"source_id": posts[0].source_id})).json()
    assert next(i for i in items["items"] if i["id"] == match_id)["sending_claimed_at"] == claimed_at
