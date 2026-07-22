"""관리 CRUD 통합(실 Postgres) — 소스·키워드·템플릿·SNS 계정 + 권한/CSRF/토큰 배제."""
import uuid

import pytest
from cryptography.fernet import Fernet

from tortoise.exceptions import IntegrityError

from app import crypto
from app.auth import hash_password
from app.config import settings
from app.models import (
    AccountStatus,
    Keyword,
    MatchedPost,
    Platform,
    PostStatus,
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


async def test_source_name_lifecycle(admin_session):
    """소스 표시용 이름(선택): 등록·수정·제거(null)·공백 정리·상한 100자."""
    client, csrf = admin_session
    r = await client.post(
        "/api/sources",
        json={"type": "community", "name": "  강아지 커뮤니티  ",
              "config": {"rss_url": "https://ex.am/feed"}},
        headers=csrf,
    )
    assert r.status_code == 201
    src = r.json()
    assert src["name"] == "강아지 커뮤니티"  # 양끝 공백 정리
    try:
        # 이름 변경
        r = await client.patch(f"/api/sources/{src['id']}", json={"name": "새 이름"}, headers=csrf)
        assert r.status_code == 200 and r.json()["name"] == "새 이름"
        # 명시적 null = 이름 제거(FE 는 config 값으로 폴백 표시)
        r = await client.patch(f"/api/sources/{src['id']}", json={"name": None}, headers=csrf)
        assert r.status_code == 200 and r.json()["name"] is None
        # 공백뿐인 이름도 미설정으로 정규화
        r = await client.patch(f"/api/sources/{src['id']}", json={"name": "   "}, headers=csrf)
        assert r.status_code == 200 and r.json()["name"] is None
        # 상한 100자 초과 → 422
        r = await client.post(
            "/api/sources",
            json={"type": "community", "name": "a" * 101,
                  "config": {"rss_url": "https://ex.am/feed"}},
            headers=csrf,
        )
        assert r.status_code == 422
        # (이름 없는 등록은 test_sources_crud_roundtrip 이 커버 — 응답에 name 키 포함 확인만)
        assert "name" in src
    finally:
        await client.delete(f"/api/sources/{src['id']}", headers=csrf)


async def test_source_unsupported_type_422(admin_session):
    # 어댑터 미구현 타입(naver_cafe)은 등록 자체를 거부 — "정상처럼 보이는데 수집 안 됨" 방지
    client, csrf = admin_session
    r = await client.post("/api/sources", json={"type": "naver_cafe"}, headers=csrf)
    assert r.status_code == 422


async def test_source_threads_config_schema(admin_session):
    """threads 소스(M2): config 는 query + sns_account_id 필수 — 등록 시점 422."""
    client, csrf = admin_session
    # config 누락 → 422 + 필드 경로 명시
    r = await client.post("/api/sources", json={"type": "threads"}, headers=csrf)
    assert r.status_code == 422 and "config.query" in r.json()["detail"]
    # 미지의 키 불허
    r = await client.post(
        "/api/sources",
        json={"type": "threads", "config": {"query": "누띠", "sns_account_id": 1, "extra": 1}},
        headers=csrf,
    )
    assert r.status_code == 422


async def test_source_poll_interval_floor(admin_session):
    # 예의 있는 수집: 60초 미만 폴링 거부 (불변식 ④)
    client, csrf = admin_session
    r = await client.post(
        "/api/sources", json={"type": "community", "poll_interval_sec": 5}, headers=csrf
    )
    assert r.status_code == 422


async def test_source_config_schema_validation(admin_session):
    """타입별 config 스키마 — 키 오타·형식 오류를 poller 시점이 아닌 등록/수정 시점에 422."""
    client, csrf = admin_session
    # rss_url 누락 → 422 + 필드 경로 명시
    r = await client.post("/api/sources", json={"type": "community"}, headers=csrf)
    assert r.status_code == 422 and "config.rss_url" in r.json()["detail"]
    # http/https 외 스킴 거부
    r = await client.post(
        "/api/sources",
        json={"type": "community", "config": {"rss_url": "ftp://ex.am/feed"}},
        headers=csrf,
    )
    assert r.status_code == 422
    # 미지의 키(오타 rss_uri) 불허 — 조용히 저장돼 수집 실패로 이어지는 것 방지
    r = await client.post(
        "/api/sources",
        json={"type": "community", "config": {"rss_url": "https://ex.am/f", "rss_uri": "x"}},
        headers=csrf,
    )
    assert r.status_code == 422
    # userinfo(자격증명) 포함 URL 거부 — 비밀번호가 detail 로도 반사되지 않아야 한다
    r = await client.post(
        "/api/sources",
        json={"type": "community", "config": {"rss_url": "https://user:s3cr3t@ex.am/feed"}},
        headers=csrf,
    )
    assert r.status_code == 422 and "s3cr3t" not in r.text
    # 명시적 config: null → 422 (dict 타입 위반)
    r = await client.post(
        "/api/sources", json={"type": "community", "config": None}, headers=csrf
    )
    assert r.status_code == 422

    create = await client.post(
        "/api/sources",
        json={"type": "community", "config": {"rss_url": "https://ex.am/f"}},
        headers=csrf,
    )
    assert create.status_code == 201
    src = create.json()
    try:
        # PATCH 도 동일 스키마 검증
        r = await client.patch(
            f"/api/sources/{src['id']}", json={"config": {"rss_uri": "x"}}, headers=csrf
        )
        assert r.status_code == 422
        r = await client.patch(
            f"/api/sources/{src['id']}",
            json={"config": {"rss_url": "https://ex.am/g"}},
            headers=csrf,
        )
        assert r.status_code == 200 and r.json()["config"]["rss_url"] == "https://ex.am/g"
        # PATCH config: null → 422 (PatchModel 명시적 null 거부의 sources 회귀 가드)
        r = await client.patch(
            f"/api/sources/{src['id']}", json={"config": None}, headers=csrf
        )
        assert r.status_code == 422
        # 없는 소스에 config PATCH → 404 (검증 전 조회 경로)
        r = await client.patch(
            "/api/sources/999999",
            json={"config": {"rss_url": "https://ex.am/f"}},
            headers=csrf,
        )
        assert r.status_code == 404
    finally:
        await client.delete(f"/api/sources/{src['id']}", headers=csrf)


async def test_source_reactivation_revalidates_legacy_config(admin_session):
    """API 검증 도입 전 저장된 무효 config 소스는 enabled=true 재활성화도 422 로 막는다."""
    client, csrf = admin_session
    create = await client.post(
        "/api/sources",
        json={"type": "community", "config": {"rss_url": "https://ex.am/feed"}},
        headers=csrf,
    )
    assert create.status_code == 201
    src = create.json()
    try:
        # 레거시 상태 재현: API 를 우회해 무효(빈) config + 비활성으로 되돌린다
        await Source.filter(id=src["id"]).update(config={}, enabled=False)

        r = await client.patch(f"/api/sources/{src['id']}", json={"enabled": True}, headers=csrf)
        assert r.status_code == 422 and "config.rss_url" in r.json()["detail"]

        # config 를 고치면서 켜는 것은 허용
        r = await client.patch(
            f"/api/sources/{src['id']}",
            json={"enabled": True, "config": {"rss_url": "https://ex.am/fixed"}},
            headers=csrf,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is True and body["config"]["rss_url"] == "https://ex.am/fixed"
    finally:
        await client.delete(f"/api/sources/{src['id']}", headers=csrf)


async def test_registered_adapters_have_config_model():
    # 레지스트리 계약: 어댑터를 추가하면 config_model 도 반드시 딸려와야 한다(누락 시 500 방지)
    from pydantic import BaseModel

    from app.models import SourceType
    from app.sources import get_adapter

    for t in SourceType:
        adapter = get_adapter(t)
        if adapter is not None:
            assert issubclass(adapter.config_model, BaseModel)


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


async def test_delete_source_cascades_collection(admin_session):
    """소스 삭제는 수집물(매칭·비발송 이력)과 스코프 키워드를 함께 정리한다(제품 결정
    2026-07-22 — 원클릭 삭제 UX). 전역 키워드는 남는다. 키워드 삭제는 이력 보존(SET NULL)."""
    client, csrf = admin_session
    me = (await client.get("/api/auth/me")).json()
    src = (await client.post("/api/sources", json={"type": "community", "config": {"rss_url": "https://ex.am/feed"}}, headers=csrf)).json()
    global_kw = (await client.post("/api/keywords", json={"pattern": "전역"}, headers=csrf)).json()
    scoped_kw = (
        await client.post(
            "/api/keywords", json={"pattern": "스코프", "source_scope": src["id"]}, headers=csrf
        )
    ).json()
    post = await MatchedPost.create(
        source_id=src["id"], external_post_id=f"ext-{uuid.uuid4().hex}",
        content="본문", matched_keyword_id=global_kw["id"],
    )
    await ReplyActionLog.create(
        matched_post=post, reviewer_id=me["id"], final_body="복사 본문",
        action=ReplyAction.approved,
    )
    try:
        r = await client.delete(f"/api/sources/{src['id']}", headers=csrf)
        assert r.status_code == 204
        assert await MatchedPost.get_or_none(id=post.id) is None
        assert await ReplyActionLog.filter(matched_post_id=post.id).count() == 0
        assert await Keyword.get_or_none(id=scoped_kw["id"]) is None  # 스코프 키워드 정리
        assert await Keyword.get_or_none(id=global_kw["id"]) is not None  # 전역은 생존
    finally:
        await ReplyActionLog.filter(matched_post_id=post.id).delete()
        await MatchedPost.filter(id=post.id).delete()
        await Keyword.filter(id__in=[global_kw["id"], scoped_kw["id"]]).delete()
        await Source.filter(id=src["id"]).delete()


async def test_delete_source_with_sent_history_409(admin_session):
    """실발송 증거·조정 재료는 불가침(불변식 ②) — sent 이력 또는 전송 진행 중 매칭이
    있으면 소스 삭제 409(부분 삭제 없음), enabled=false 비활성화가 정식 경로."""
    client, csrf = admin_session
    me = (await client.get("/api/auth/me")).json()
    src = (await client.post("/api/sources", json={"type": "community", "config": {"rss_url": "https://ex.am/feed"}}, headers=csrf)).json()
    scoped_kw = (
        await client.post(
            "/api/keywords", json={"pattern": "스코프", "source_scope": src["id"]}, headers=csrf
        )
    ).json()
    post = await MatchedPost.create(
        source_id=src["id"], external_post_id=f"ext-{uuid.uuid4().hex}",
        content="본문", status=PostStatus.replied,
    )
    await ReplyActionLog.create(
        matched_post=post, reviewer_id=me["id"], final_body="발송 본문",
        action=ReplyAction.sent, external_reply_id="r-live-1",
    )
    try:
        assert (await client.delete(f"/api/sources/{src['id']}", headers=csrf)).status_code == 409
        # 부분 삭제 없음 — 매칭·이력·스코프 키워드 전부 생존(트랜잭션 롤백)
        assert await MatchedPost.get_or_none(id=post.id) is not None
        assert await ReplyActionLog.filter(matched_post_id=post.id).count() == 1
        assert await Keyword.get_or_none(id=scoped_kw["id"]) is not None

        # 전송 진행 중(verify_pending)도 동일 보호 — sent 이력 없이 상태만으로 차단
        await ReplyActionLog.filter(matched_post_id=post.id).delete()
        await MatchedPost.filter(id=post.id).update(status=PostStatus.verify_pending)
        assert (await client.delete(f"/api/sources/{src['id']}", headers=csrf)).status_code == 409
    finally:
        await ReplyActionLog.filter(matched_post_id=post.id).delete()
        await MatchedPost.filter(id=post.id).delete()
        await Keyword.filter(id=scoped_kw["id"]).delete()
        await Source.filter(id=src["id"]).delete()


async def test_reply_action_fk_protections(admin_session):
    """감사 이력 FK: template/sns_account 삭제 → SET NULL 보존, matched_post·reviewer → RESTRICT."""
    client, csrf = admin_session
    me = (await client.get("/api/auth/me")).json()
    src = (await client.post("/api/sources", json={"type": "community", "config": {"rss_url": "https://ex.am/feed"}}, headers=csrf)).json()
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
    assert set(item) == {
        "id", "user_id", "platform", "display_name", "status", "token_expires_at"
    }

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


async def test_sns_account_credentials_replace(admin_session, monkeypatch):
    """토큰 교체(PUT /credentials): 삭제→재등록 없이 자격증명만 교체 + 만료 상태 복구.
    등록과 동일한 형식 검증(422)·자격증명 비노출(불변식 ③)·결손 secret 복구(upsert)."""
    client, csrf = admin_session
    monkeypatch.setattr(settings, "credentials_fernet_keys", Fernet.generate_key().decode())
    old_token = f"tok-OLD-{uuid.uuid4().hex}"
    r = await client.post(
        "/api/sns-accounts",
        json={"platform": "threads", "display_name": "교체대상",
              "credentials": {"access_token": old_token}},
        headers=csrf,
    )
    assert r.status_code == 201
    account_id = r.json()["id"]
    # 만료된 계정 시나리오 — 교체가 active 로 복구해야 새 토큰이 실제로 쓰인다
    await SnsAccount.filter(id=account_id).update(status=AccountStatus.expired)

    new_token = f"tok-NEW-{uuid.uuid4().hex}"
    r = await client.put(
        f"/api/sns-accounts/{account_id}/credentials",
        json={"credentials": {"access_token": new_token}},
        headers=csrf,
    )
    assert r.status_code == 204
    secret = await SnsAccountSecret.get(account_id=account_id)
    assert crypto.decrypt_credentials(bytes(secret.encrypted_credentials)) == {
        "access_token": new_token
    }
    account = await SnsAccount.get(id=account_id)
    assert account.status == AccountStatus.active
    assert account.token_expires_at is None

    # CSRF 없이 거부 — 상태변경 라우트 공통 정책(NFR-S4)
    r = await client.put(
        f"/api/sns-accounts/{account_id}/credentials",
        json={"credentials": {"access_token": "tok-nocsrf"}},
    )
    assert r.status_code == 403

    # 등록과 동일한 형식 검증 + 에러에 입력 echo 없음(불변식 ③)
    r = await client.put(
        f"/api/sns-accounts/{account_id}/credentials",
        json={"credentials": {"access_token": "  "}},
        headers=csrf,
    )
    assert r.status_code == 422
    leaked = f"LEAK-{uuid.uuid4().hex}"
    r = await client.put(
        f"/api/sns-accounts/{account_id}/credentials",
        json={"credentials": leaked},
        headers=csrf,
    )
    assert r.status_code == 422 and leaked not in r.text

    # 결손 secret(과거 데이터) 도 교체가 복구한다 — upsert
    await SnsAccountSecret.filter(account_id=account_id).delete()
    r = await client.put(
        f"/api/sns-accounts/{account_id}/credentials",
        json={"credentials": {"access_token": new_token}},
        headers=csrf,
    )
    assert r.status_code == 204
    assert await SnsAccountSecret.get_or_none(account_id=account_id) is not None

    # 존재하지 않는 계정 → 404 (타인 계정과 동일 표면 — 존재 비노출)
    r = await client.put(
        "/api/sns-accounts/999999/credentials",
        json={"credentials": {"access_token": "tok-x"}},
        headers=csrf,
    )
    assert r.status_code == 404

    # credentials 외 키 → 422 (extra 금지 — platform 전환 시도 등 조용한 무시 방지)
    r = await client.put(
        f"/api/sns-accounts/{account_id}/credentials",
        json={"credentials": {"access_token": "tok-x"}, "platform": "naver"},
        headers=csrf,
    )
    assert r.status_code == 422

    assert (await client.delete(f"/api/sns-accounts/{account_id}", headers=csrf)).status_code == 204


async def test_sns_account_credentials_replace_without_key_503(admin_session, monkeypatch):
    """교체도 등록과 동일 — 암호화 키 미설정이면 503(자격증명을 평문으로 받아두지 않는다)."""
    client, csrf = admin_session
    monkeypatch.setattr(settings, "credentials_fernet_keys", Fernet.generate_key().decode())
    r = await client.post(
        "/api/sns-accounts",
        json={"platform": "threads", "display_name": "키테스트",
              "credentials": {"access_token": "tok-1"}},
        headers=csrf,
    )
    account_id = r.json()["id"]
    monkeypatch.setattr(settings, "credentials_fernet_keys", "")
    r = await client.put(
        f"/api/sns-accounts/{account_id}/credentials",
        json={"credentials": {"access_token": "tok-2"}},
        headers=csrf,
    )
    assert r.status_code == 503
    assert "tok-2" not in r.text
    await client.delete(f"/api/sns-accounts/{account_id}", headers=csrf)


async def test_sns_account_credentials_replace_scoped_to_owner(admin_session, monkeypatch):
    """타인 계정 토큰 교체는 존재 여부도 노출하지 않는다(404) — delete 와 동일 정책."""
    client, csrf = admin_session
    monkeypatch.setattr(settings, "credentials_fernet_keys", Fernet.generate_key().decode())
    owner_token = f"tok-OWNER-{uuid.uuid4().hex}"
    r = await client.post(
        "/api/sns-accounts",
        json={"platform": "threads", "display_name": "admin계정",
              "credentials": {"access_token": owner_token}},
        headers=csrf,
    )
    account_id = r.json()["id"]
    reviewer = await _make_user(Role.reviewer)
    try:
        # 같은 클라이언트로 reviewer 재로그인 — 이후 요청은 reviewer 권한
        await client.post(
            "/api/auth/login", json={"email": reviewer.email, "password": PASSWORD}
        )
        r_csrf = {"X-CSRF-Token": (await client.get("/api/auth/csrf")).json()["csrf_token"]}
        r = await client.put(
            f"/api/sns-accounts/{account_id}/credentials",
            json={"credentials": {"access_token": "tok-hijack"}},
            headers=r_csrf,
        )
        assert r.status_code == 404
        # 원 자격증명은 그대로다
        secret = await SnsAccountSecret.get(account_id=account_id)
        assert crypto.decrypt_credentials(bytes(secret.encrypted_credentials)) == {
            "access_token": owner_token
        }
    finally:
        await reviewer.delete()


async def test_sns_account_without_key_503(admin_session, monkeypatch):
    client, csrf = admin_session
    monkeypatch.setattr(settings, "credentials_fernet_keys", "")
    r = await client.post(
        "/api/sns-accounts",
        json={"platform": "threads", "display_name": "x", "credentials": {"access_token": "v"}},
        headers=csrf,
    )
    assert r.status_code == 503


async def test_sns_account_threads_requires_access_token(admin_session, monkeypatch):
    """threads 자격증명 형식은 등록 시점 422 — 아무 JSON 이나 저장돼 폴링 때에야
    실패가 드러나는 상태 방지. 에러 detail 에 입력값 echo 없음(불변식 ③)."""
    client, csrf = admin_session
    monkeypatch.setattr(settings, "credentials_fernet_keys", Fernet.generate_key().decode())
    for bad in ({}, {"t": "v"}, {"access_token": ""}, {"access_token": "   "},
                {"access_token": 123}):
        r = await client.post(
            "/api/sns-accounts",
            json={"platform": "threads", "display_name": "x", "credentials": bad},
            headers=csrf,
        )
        assert r.status_code == 422, bad
        assert "access_token" in r.json()["detail"]
    # naver 등 스키마 미확정 플랫폼은 종전대로 자유 형식 허용
    r = await client.post(
        "/api/sns-accounts",
        json={"platform": "naver", "display_name": "n", "credentials": {"t": "v"}},
        headers=csrf,
    )
    assert r.status_code == 201
    await client.delete(f"/api/sns-accounts/{r.json()['id']}", headers=csrf)


# sns-accounts 는 셀프서비스(로그인 사용자 전체 허용)라 admin 전용 목록에서 제외
ADMIN_PREFIXES = ["/api/sources", "/api/keywords", "/api/templates"]


async def test_admin_routes_require_admin_and_csrf(api_client):
    # 미인증 → 401 (셀프서비스 포함 전부)
    for prefix in [*ADMIN_PREFIXES, "/api/sns-accounts"]:
        assert (await api_client.get(prefix)).status_code == 401
    # reviewer → admin 라우터는 403, 셀프서비스는 200
    user = await _make_user(Role.reviewer)
    try:
        await api_client.post(
            "/api/auth/login", json={"email": user.email, "password": PASSWORD}
        )
        for prefix in ADMIN_PREFIXES:
            assert (await api_client.get(prefix)).status_code == 403
        assert (await api_client.get("/api/sns-accounts")).status_code == 200
    finally:
        await user.delete()


async def test_sns_account_self_service_ownership(api_client, monkeypatch):
    """계정 귀속: 본인 것만 보이고/지울 수 있고, admin 은 전체."""
    monkeypatch.setattr(settings, "credentials_fernet_keys", Fernet.generate_key().decode())
    owner = await _make_user(Role.reviewer)
    other = await _make_user(Role.reviewer)
    admin = await _make_user(Role.admin)

    async def _login(u):
        await api_client.post("/api/auth/login", json={"email": u.email, "password": PASSWORD})
        token = (await api_client.get("/api/auth/csrf")).json()["csrf_token"]
        return {"X-CSRF-Token": token}

    try:
        # owner(reviewer)가 본인 계정 연동 → 자동 귀속
        csrf = await _login(owner)
        r = await api_client.post(
            "/api/sns-accounts",
            json={"platform": "threads", "display_name": "내계정",
                  "credentials": {"access_token": "v"}},
            headers=csrf,
        )
        assert r.status_code == 201 and r.json()["user_id"] == owner.id
        account_id = r.json()["id"]
        assert [a["id"] for a in (await api_client.get("/api/sns-accounts")).json()] == [
            account_id
        ]

        # 타인(reviewer)에게는 목록에도 안 보이고 삭제도 404 (존재 비노출)
        csrf = await _login(other)
        assert (await api_client.get("/api/sns-accounts")).json() == []
        r = await api_client.delete(f"/api/sns-accounts/{account_id}", headers=csrf)
        assert r.status_code == 404

        # admin 은 전체 조회 + 삭제 가능
        csrf = await _login(admin)
        assert any(
            a["id"] == account_id for a in (await api_client.get("/api/sns-accounts")).json()
        )
        assert (
            await api_client.delete(f"/api/sns-accounts/{account_id}", headers=csrf)
        ).status_code == 204
    finally:
        await owner.delete()
        await other.delete()
        await admin.delete()


async def test_write_without_csrf_403(admin_session):
    client, _ = admin_session
    r = await client.post("/api/sources", json={"type": "community"})
    assert r.status_code == 403
