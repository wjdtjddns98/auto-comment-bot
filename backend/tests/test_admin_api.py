"""관리 CRUD 통합(실 Postgres) — 소스·키워드·템플릿·SNS 계정 + 권한/CSRF/토큰 배제."""
import uuid

import pytest
from cryptography.fernet import Fernet

from tortoise.exceptions import IntegrityError

from app import crypto
from app.auth import hash_password
from app.config import settings
from app.models import (
    MatchedPost,
    Platform,
    ReplyAction,
    ReplyActionLog,
    Role,
    SnsAccount,
    SnsAccountSecret,
    Source,
    User,
)

pytestmark = pytest.mark.db

PASSWORD = "pw-test-1234"


async def _make_user(role: Role) -> User:
    return await User.create(
        email=f"t-{uuid.uuid4().hex[:10]}@test.local",
        password_hash=hash_password(PASSWORD),
        role=role,
    )


@pytest.fixture
async def admin_session(api_client):
    """admin 로그인된 클라이언트 + CSRF 헤더. 유저 삭제 시 생성 리소스도 cascade 정리."""
    user = await _make_user(Role.admin)
    await api_client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD})
    token = (await api_client.get("/api/auth/csrf")).json()["csrf_token"]
    yield api_client, {"X-CSRF-Token": token}
    await user.delete()


async def test_sources_crud_roundtrip(admin_session):
    client, csrf = admin_session
    r = await client.post(
        "/api/sources",
        json={"type": "community", "config": {"rss_url": "https://ex.am/feed"}},
        headers=csrf,
    )
    assert r.status_code == 201
    src = r.json()
    assert src["type"] == "community" and src["poll_interval_sec"] == 300
    assert src["health_status"] == "ok"

    assert any(s["id"] == src["id"] for s in (await client.get("/api/sources")).json())

    r = await client.patch(f"/api/sources/{src['id']}", json={"enabled": False}, headers=csrf)
    assert r.status_code == 200 and r.json()["enabled"] is False

    assert (await client.delete(f"/api/sources/{src['id']}", headers=csrf)).status_code == 204
    assert (await client.delete(f"/api/sources/{src['id']}", headers=csrf)).status_code == 404


async def test_source_poll_interval_floor(admin_session):
    # 예의 있는 수집: 60초 미만 폴링 거부 (불변식 ④)
    client, csrf = admin_session
    r = await client.post(
        "/api/sources", json={"type": "community", "poll_interval_sec": 5}, headers=csrf
    )
    assert r.status_code == 422


async def test_keyword_regex_validation_and_scope(admin_session):
    client, csrf = admin_session
    # 잘못된 regex → 422
    r = await client.post(
        "/api/keywords", json={"pattern": "[unclosed", "match_type": "regex"}, headers=csrf
    )
    assert r.status_code == 422
    # 없는 source_scope → 404
    r = await client.post(
        "/api/keywords", json={"pattern": "키워드", "source_scope": 999999}, headers=csrf
    )
    assert r.status_code == 404
    # 정상 생성 → PATCH 로 regex 전환 시에도 조합 재검증
    r = await client.post("/api/keywords", json={"pattern": "[unclosed"}, headers=csrf)
    assert r.status_code == 201
    kid = r.json()["id"]
    r = await client.patch(f"/api/keywords/{kid}", json={"match_type": "regex"}, headers=csrf)
    assert r.status_code == 422
    assert (await client.delete(f"/api/keywords/{kid}", headers=csrf)).status_code == 204


async def test_patch_explicit_null_422(admin_session):
    # 명시적 null 은 ORM 500 이 아니라 422 로 거부 (생략=변경 없음과 구분)
    client, csrf = admin_session
    r = await client.post("/api/keywords", json={"pattern": "키워드"}, headers=csrf)
    kid = r.json()["id"]
    assert (
        await client.patch(f"/api/keywords/{kid}", json={"pattern": None}, headers=csrf)
    ).status_code == 422
    # source_scope 는 null 이 유효값(스코프 해제)
    r = await client.patch(f"/api/keywords/{kid}", json={"source_scope": None}, headers=csrf)
    assert r.status_code == 200 and r.json()["source_scope"] is None
    # 필드명 오타는 조용한 no-op 이 아니라 422 (extra="forbid")
    assert (
        await client.patch(f"/api/keywords/{kid}", json={"enabeld": True}, headers=csrf)
    ).status_code == 422
    await client.delete(f"/api/keywords/{kid}", headers=csrf)


async def test_delete_preserves_match_history(admin_session):
    """감사 보호: 소스 삭제는 이력 있으면 409(RESTRICT), 키워드 삭제는 이력 보존(SET NULL)."""
    client, csrf = admin_session
    src = (await client.post("/api/sources", json={"type": "community"}, headers=csrf)).json()
    kw = (await client.post("/api/keywords", json={"pattern": "키워드"}, headers=csrf)).json()
    post = await MatchedPost.create(
        source_id=src["id"], external_post_id=f"ext-{uuid.uuid4().hex}",
        content="본문", matched_keyword_id=kw["id"],
    )
    try:
        r = await client.delete(f"/api/sources/{src['id']}", headers=csrf)
        assert r.status_code == 409

        assert (await client.delete(f"/api/keywords/{kw['id']}", headers=csrf)).status_code == 204
        await post.refresh_from_db()
        assert post.matched_keyword_id is None  # 이력 행 생존 + 참조만 해제
    finally:
        await post.delete()
        await Source.filter(id=src["id"]).delete()


async def test_delete_source_with_scoped_keyword_409(admin_session):
    # 소스 삭제가 스코프된 키워드를 조용히 연쇄 삭제하지 않는다 (RESTRICT → 409)
    client, csrf = admin_session
    src = (await client.post("/api/sources", json={"type": "community"}, headers=csrf)).json()
    kw = (
        await client.post(
            "/api/keywords", json={"pattern": "키워드", "source_scope": src["id"]}, headers=csrf
        )
    ).json()
    assert (await client.delete(f"/api/sources/{src['id']}", headers=csrf)).status_code == 409
    assert (await client.delete(f"/api/keywords/{kw['id']}", headers=csrf)).status_code == 204
    assert (await client.delete(f"/api/sources/{src['id']}", headers=csrf)).status_code == 204


async def test_reply_action_fk_protections(admin_session):
    """감사 이력 FK: template/sns_account 삭제 → SET NULL 보존, matched_post·reviewer → RESTRICT."""
    client, csrf = admin_session
    me = (await client.get("/api/auth/me")).json()
    src = (await client.post("/api/sources", json={"type": "community"}, headers=csrf)).json()
    tpl = (await client.post("/api/templates", json={"name": "t", "body": "b"}, headers=csrf)).json()
    account = await SnsAccount.create(
        user_id=me["id"], platform=Platform.threads, display_name="감사테스트"
    )
    post = await MatchedPost.create(
        source_id=src["id"], external_post_id=f"ext-{uuid.uuid4().hex}", content="본문"
    )
    log = await ReplyActionLog.create(
        matched_post=post, reviewer_id=me["id"], template_id=tpl["id"],
        sns_account=account, final_body="답변", action=ReplyAction.approved,
    )
    try:
        # 참조 대상 삭제 → 이력 행은 생존하고 참조만 해제 (SET NULL)
        assert (await client.delete(f"/api/templates/{tpl['id']}", headers=csrf)).status_code == 204
        assert (
            await client.delete(f"/api/sns-accounts/{account.id}", headers=csrf)
        ).status_code == 204
        await log.refresh_from_db()
        assert log.template_id is None and log.sns_account_id is None

        # 이력이 딸린 부모는 하드삭제 불가 (RESTRICT)
        with pytest.raises(IntegrityError):
            await post.delete()
        with pytest.raises(IntegrityError):
            await User.filter(id=me["id"]).delete()
    finally:
        await log.delete()
        await post.delete()
        await Source.filter(id=src["id"]).delete()


async def test_templates_crud_roundtrip(admin_session):
    client, csrf = admin_session
    r = await client.post("/api/templates", json={"name": "기본", "body": "안녕하세요"}, headers=csrf)
    assert r.status_code == 201
    tid = r.json()["id"]
    r = await client.patch(f"/api/templates/{tid}", json={"enabled": False}, headers=csrf)
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert (await client.delete(f"/api/templates/{tid}", headers=csrf)).status_code == 204


async def test_sns_account_secret_never_in_responses(admin_session, monkeypatch):
    """테스트 게이트 ②: 어떤 응답에도 자격증명 원문/암호문이 없다 (MUST-FIX #3)."""
    client, csrf = admin_session
    monkeypatch.setattr(settings, "credentials_fernet_keys", Fernet.generate_key().decode())
    secret_token = f"tok-SECRET-{uuid.uuid4().hex}"

    r = await client.post(
        "/api/sns-accounts",
        json={
            "platform": "threads",
            "display_name": "테스트계정",
            "credentials": {"access_token": secret_token},
        },
        headers=csrf,
    )
    assert r.status_code == 201
    account_id = r.json()["id"]
    assert secret_token not in r.text
    assert "credentials" not in r.json()

    r = await client.get("/api/sns-accounts")
    assert r.status_code == 200
    assert secret_token not in r.text
    assert "encrypted_credentials" not in r.text
    # allowlist: 응답 필드 집합 자체를 고정 — 새 필드가 몰래 실리는 것도 차단
    item = next(a for a in r.json() if a["id"] == account_id)
    assert set(item) == {"id", "platform", "display_name", "status", "token_expires_at"}

    # 저장은 암호화본으로만 — 평문 substring 부재 + 복호 왕복 일치, 암호문도 응답에 없음
    secret = await SnsAccountSecret.get(account_id=account_id)
    raw = bytes(secret.encrypted_credentials)
    assert secret_token.encode() not in raw
    assert crypto.decrypt_credentials(raw) == {"access_token": secret_token}
    assert raw.decode() not in r.text

    # 422 검증 에러도 입력 원문(자격증명)을 echo 하지 않는다 (불변식 ③)
    leaked = f"LEAK-{uuid.uuid4().hex}"
    r = await client.post(
        "/api/sns-accounts",
        json={"platform": "threads", "display_name": "x", "credentials": leaked},
        headers=csrf,
    )
    assert r.status_code == 422 and leaked not in r.text

    # 삭제 시 secret cascade
    assert (await client.delete(f"/api/sns-accounts/{account_id}", headers=csrf)).status_code == 204
    assert await SnsAccountSecret.get_or_none(account_id=account_id) is None
    assert await SnsAccount.get_or_none(id=account_id) is None


async def test_sns_account_without_key_503(admin_session, monkeypatch):
    client, csrf = admin_session
    monkeypatch.setattr(settings, "credentials_fernet_keys", "")
    r = await client.post(
        "/api/sns-accounts",
        json={"platform": "threads", "display_name": "x", "credentials": {"t": "v"}},
        headers=csrf,
    )
    assert r.status_code == 503


ADMIN_PREFIXES = ["/api/sources", "/api/keywords", "/api/templates", "/api/sns-accounts"]


async def test_admin_routes_require_admin_and_csrf(api_client):
    # 미인증 → 401 (4개 라우터 전부)
    for prefix in ADMIN_PREFIXES:
        assert (await api_client.get(prefix)).status_code == 401
    # reviewer → 403 (4개 라우터 전부)
    user = await _make_user(Role.reviewer)
    try:
        await api_client.post(
            "/api/auth/login", json={"email": user.email, "password": PASSWORD}
        )
        for prefix in ADMIN_PREFIXES:
            assert (await api_client.get(prefix)).status_code == 403
    finally:
        await user.delete()


async def test_write_without_csrf_403(admin_session):
    client, _ = admin_session
    r = await client.post("/api/sources", json={"type": "community"})
    assert r.status_code == 403
