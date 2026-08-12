"""/api/users 통합(실 Postgres) — 사용자 관리 CRUD + 삭제 가드 (이슈 #102).

이 모듈의 위험은 CRUD 가 아니라 **삭제**다: `sources`·`keywords`·`templates`·
`sns_accounts` 가 `users` 에 ON DELETE CASCADE 로 물려 있어, 가드가 하나라도 빠지면
사용자 한 명 삭제가 수집 설정을 통째로 날린다. 그래서 가드마다 테스트를 따로 두고
**막혔는지(409)만이 아니라 대상이 실제로 살아남았는지**까지 확인한다.
"""
import uuid

import pytest

from app.auth import hash_password
from app.models import (
    Keyword,
    MatchedPost,
    PostStatus,
    ReplyAction,
    ReplyActionLog,
    ReplyTemplate,
    Role,
    SnsAccount,
    Source,
    SourceType,
    User,
)

pytestmark = pytest.mark.db

PASSWORD = "pw-test-1234"


async def _make_user(role: Role) -> User:
    return await User.create(
        email=f"t-{uuid.uuid4().hex[:10]}@test.local",
        password_hash=hash_password(PASSWORD), role=role,
    )


async def _login(client, user: User) -> dict:
    await client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD})
    return {"X-CSRF-Token": (await client.get("/api/auth/csrf")).json()["csrf_token"]}


@pytest.fixture
async def admin_session(api_client):
    """admin 로그인 클라이언트 + CSRF + 본인 User."""
    admin = await _make_user(Role.admin)
    csrf = await _login(api_client, admin)
    yield api_client, csrf, admin
    await User.filter(id=admin.id).delete()


async def test_user_crud_roundtrip(admin_session):
    """생성 → 목록 → 역할 변경 → 삭제. password_hash 는 어떤 응답에도 없다."""
    client, csrf, _admin = admin_session
    email = f"new-{uuid.uuid4().hex[:8]}@test.local"
    r = await client.post(
        "/api/users", json={"email": email, "password": "pw-12345678", "role": "reviewer"},
        headers=csrf,
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["email"] == email and created["role"] == "reviewer"
    assert set(created) == {"id", "email", "role", "created_at"}  # password_hash 없음
    user_id = created["id"]

    listed = (await client.get("/api/users")).json()
    assert all(set(u) == {"id", "email", "role", "created_at"} for u in listed)
    assert next(u for u in listed if u["id"] == user_id)["email"] == email
    assert [u["id"] for u in listed] == sorted(u["id"] for u in listed)  # id 오름차순

    r = await client.patch(f"/api/users/{user_id}", json={"role": "admin"}, headers=csrf)
    assert r.status_code == 200 and r.json()["role"] == "admin"
    assert (await User.get(id=user_id)).role == Role.admin

    # 새 비밀번호로 실제 로그인이 된다 — 해시 저장이 맞는지까지 확인
    assert (
        await client.post("/api/auth/login", json={"email": email, "password": "pw-12345678"})
    ).status_code == 200
    csrf = await _login(client, _admin)  # 세션을 admin 으로 되돌린다(쿠키 공유)

    assert (await client.delete(f"/api/users/{user_id}", headers=csrf)).status_code == 204
    assert (await client.delete(f"/api/users/{user_id}", headers=csrf)).status_code == 404


async def test_create_validation(admin_session):
    """8자 미만 비밀번호·이메일 형식·중복·미지 키를 각각 거른다."""
    client, csrf, _admin = admin_session
    email = f"v-{uuid.uuid4().hex[:8]}@test.local"

    async def post(payload):
        return await client.post("/api/users", json=payload, headers=csrf)

    assert (await post({"email": email, "password": "pw-1234", "role": "reviewer"})).status_code == 422
    assert (await post({"email": "at-없음", "password": "pw-12345678", "role": "reviewer"})).status_code == 422
    assert (await post({"email": email, "password": "pw-12345678", "role": "superuser"})).status_code == 422
    assert (
        await post({"email": email, "password": "pw-12345678", "role": "reviewer", "x": 1})
    ).status_code == 422

    # 앞뒤 공백은 서버가 정리한다 — 그래야 저장값과 로그인 입력이 어긋나지 않는다
    r = await post({"email": f"  {email}  ", "password": "pw-12345678", "role": "reviewer"})
    assert r.status_code == 201 and r.json()["email"] == email
    user_id = r.json()["id"]
    try:
        # 중복 이메일 → 409, 입력값 echo 없음
        dup = await post({"email": email, "password": "pw-12345678", "role": "admin"})
        assert dup.status_code == 409 and email not in dup.json()["detail"]
    finally:
        await User.filter(id=user_id).delete()


async def test_requires_admin_and_csrf(api_client, admin_session):
    """미인증 401 · reviewer 403 · CSRF 없는 쓰기 403."""
    client, csrf, admin = admin_session
    target = await _make_user(Role.reviewer)
    try:
        # CSRF 헤더 없는 쓰기(세션은 admin) → 403
        assert (
            await client.post(
                "/api/users", json={"email": "x@y.z", "password": "pw-12345678", "role": "reviewer"}
            )
        ).status_code == 403
        assert (await client.patch(f"/api/users/{target.id}", json={"role": "admin"})).status_code == 403
        assert (await client.delete(f"/api/users/{target.id}")).status_code == 403

        # reviewer 세션 → 전부 403
        reviewer_csrf = await _login(client, target)
        assert (await client.get("/api/users")).status_code == 403
        assert (
            await client.post(
                "/api/users",
                json={"email": "x@y.z", "password": "pw-12345678", "role": "admin"},
                headers=reviewer_csrf,
            )
        ).status_code == 403
        assert (
            await client.patch(
                f"/api/users/{target.id}", json={"role": "admin"}, headers=reviewer_csrf
            )
        ).status_code == 403
        assert (
            await client.delete(f"/api/users/{admin.id}", headers=reviewer_csrf)
        ).status_code == 403
        assert (await User.get(id=target.id)).role == Role.reviewer  # 아무것도 안 바뀜

        # 미인증 → 401 (logout 자체도 CSRF 필요)
        assert (
            await client.post("/api/auth/logout", headers=reviewer_csrf)
        ).status_code == 200
        assert (await client.get("/api/users")).status_code == 401
        assert (await client.delete(f"/api/users/{target.id}")).status_code == 401
    finally:
        await User.filter(id=target.id).delete()


async def test_cannot_delete_self(admin_session):
    client, csrf, admin = admin_session
    r = await client.delete(f"/api/users/{admin.id}", headers=csrf)
    assert r.status_code == 409 and "자기 자신" in r.json()["detail"]
    assert await User.exists(id=admin.id)


async def test_cannot_demote_last_admin(admin_session):
    """admin 이 하나뿐이면 강등 불가 — 아무도 관리 화면에 못 들어가는 상태 방지.

    다른 테스트가 남긴 admin 이 있으면 조건이 성립하지 않으므로, 이 테스트 동안만
    나머지 admin 을 reviewer 로 내렸다가 원복한다(테스트는 순차 실행).
    """
    client, csrf, admin = admin_session
    others = await User.filter(role=Role.admin).exclude(id=admin.id)
    await User.filter(id__in=[u.id for u in others]).update(role=Role.reviewer)
    try:
        r = await client.patch(f"/api/users/{admin.id}", json={"role": "reviewer"}, headers=csrf)
        assert r.status_code == 409 and "마지막 admin" in r.json()["detail"]
        assert (await User.get(id=admin.id)).role == Role.admin

        # 다른 admin 이 생기면 자기 강등이 허용된다
        peer = await _make_user(Role.admin)
        try:
            r = await client.patch(
                f"/api/users/{admin.id}", json={"role": "reviewer"}, headers=csrf
            )
            assert r.status_code == 200 and r.json()["role"] == "reviewer"
            # 역할은 매 요청 DB 에서 읽는다 — 재로그인 없이 즉시 admin 라우터에서 밀린다
            assert (await client.get("/api/users")).status_code == 403
        finally:
            await User.filter(id=admin.id).update(role=Role.admin)
            await User.filter(id=peer.id).delete()
    finally:
        await User.filter(id__in=[u.id for u in others]).update(role=Role.admin)


async def test_last_admin_delete_guard_is_wired(admin_session, monkeypatch):
    """마지막 admin 삭제 차단이 라우트에 실제로 연결돼 있는지.

    이 분기는 HTTP 로 자연 재현이 안 된다(요청자 자신이 admin 이라 '남은 admin' 에
    항상 포함된다). 실제로 노리는 건 **동시 강등과의 교차**다: admins={A,B} 에서
    B 가 A 를 강등하는 트랜잭션이 먼저 커밋되면, A 의 진행 중이던 "B 삭제" 가
    admin 0명을 만든다. 여기서는 그 상태(admin 목록에 대상만 남음)를 주입해 가드가
    걸리는지만 고정한다 — 경합 자체를 재현하는 테스트는 아니다.
    """
    from app.api import users as users_api

    client, csrf, _admin = admin_session
    target = await _make_user(Role.admin)
    try:
        async def _only_target():
            return [target]

        monkeypatch.setattr(users_api, "_lock_admins", _only_target)
        r = await client.delete(f"/api/users/{target.id}", headers=csrf)
        assert r.status_code == 409 and "마지막 admin" in r.json()["detail"]
        assert await User.exists(id=target.id)
    finally:
        await User.filter(id=target.id).delete()


async def test_delete_blocked_by_owned_resources(admin_session):
    """소유 리소스가 있으면 409 — cascade 로 수집 설정이 함께 사라지는 것을 막는다.

    소스·키워드·템플릿·SNS 계정 4종을 각각 확인한다(하나라도 검사에서 빠지면
    그 종류만 조용히 cascade 된다).
    """
    client, csrf, _admin = admin_session
    target = await _make_user(Role.reviewer)
    source = await Source.create(
        user=target, type=SourceType.community, config={"rss_url": "https://ex.am/feed"}
    )
    keyword = await Keyword.create(user=target, pattern="간식")
    template = await ReplyTemplate.create(user=target, name="t", body="b")
    account = await SnsAccount.create(
        user=target, platform="threads", display_name="계정"
    )
    try:
        # 4종 전부 있는 상태 — 이유 문구에 종류가 나열된다
        r = await client.delete(f"/api/users/{target.id}", headers=csrf)
        assert r.status_code == 409
        detail = r.json()["detail"]
        for name in ("소스", "키워드", "템플릿", "SNS 계정"):
            assert name in detail, detail

        # 하나씩 지워도 남은 게 있는 한 계속 막힌다 — 검사 누락 종류가 없는지 확인
        for obj in (source, keyword, template, account):
            assert (await client.delete(f"/api/users/{target.id}", headers=csrf)).status_code == 409
            await obj.__class__.filter(id=obj.pk).delete()
        # 전부 정리되면 삭제된다
        assert (await client.delete(f"/api/users/{target.id}", headers=csrf)).status_code == 204
    finally:
        await Source.filter(id=source.id).delete()
        await Keyword.filter(id=keyword.id).delete()
        await ReplyTemplate.filter(id=template.id).delete()
        await SnsAccount.filter(id=account.id).delete()
        await User.filter(id=target.id).delete()


async def test_delete_blocked_by_audit_history(admin_session):
    """승인 이력이 있으면 409(감사 보존) — DB RESTRICT 가 500 으로 새지 않는다."""
    client, csrf, admin = admin_session
    target = await _make_user(Role.reviewer)
    source = await Source.create(
        user=admin, type=SourceType.community, config={"rss_url": "https://ex.am/f"}
    )
    post = await MatchedPost.create(
        source=source, external_post_id=f"u-{uuid.uuid4().hex[:8]}", content="본문"
    )
    log = await ReplyActionLog.create(
        matched_post=post, reviewer=target, final_body="답변", action=ReplyAction.approved
    )
    try:
        r = await client.delete(f"/api/users/{target.id}", headers=csrf)
        assert r.status_code == 409 and "이력" in r.json()["detail"]
        assert await User.exists(id=target.id)
        assert await ReplyActionLog.exists(id=log.id)  # 감사 로그는 그대로
    finally:
        await ReplyActionLog.filter(id=log.id).delete()
        await MatchedPost.filter(id=post.id).delete()
        await Source.filter(id=source.id).delete()
        await User.filter(id=target.id).delete()


async def test_delete_blocked_by_verify_pending_reviewer(admin_session):
    """조정 대기 매칭의 승인자는 삭제 불가(모델 주석 R-5).

    verify_meta.reviewer_id 는 FK 가 아니라 DB 가 못 막는다 — 지우면 조정 잡이 승계
    이력을 쓸 때마다 FK 위반으로 실패해 그 매칭이 영구히 verify_pending 에 갇힌다.
    """
    client, csrf, admin = admin_session
    target = await _make_user(Role.reviewer)
    source = await Source.create(
        user=admin, type=SourceType.community, config={"rss_url": "https://ex.am/f"}
    )
    post = await MatchedPost.create(
        source=source, external_post_id=f"vp-{uuid.uuid4().hex[:8]}", content="본문",
        status=PostStatus.verify_pending,
        verify_meta={"target_media_id": "m-1", "attempts": 0, "reviewer_id": target.id},
    )
    try:
        r = await client.delete(f"/api/users/{target.id}", headers=csrf)
        assert r.status_code == 409
        assert str(post.id) in r.json()["detail"]  # 어느 매칭인지 알려준다
        assert await User.exists(id=target.id)

        # 조정이 끝나면(verify_pending 해제) 삭제된다
        await MatchedPost.filter(id=post.id).update(
            status=PostStatus.reviewing, verify_meta=None
        )
        assert (await client.delete(f"/api/users/{target.id}", headers=csrf)).status_code == 204
    finally:
        await MatchedPost.filter(id=post.id).delete()
        await Source.filter(id=source.id).delete()
        await User.filter(id=target.id).delete()
