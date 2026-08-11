"""Threads OAuth 코드 연동(실 Postgres + MockTransport) — 교환·state·upsert·비밀 비노출."""
import logging
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet

from app import crypto
from app.auth import hash_password
from app.config import settings
from app.models import (
    AccountStatus,
    Platform,
    Role,
    SnsAccount,
    SnsAccountSecret,
    User,
)
from app.sources import threads

pytestmark = pytest.mark.db

PASSWORD = "pw-test-1234"
LONG_TOKEN = "long-lived-token-xyz"
SHORT_TOKEN = "short-token-abc"
APP_SECRET = "app-secret-1"
EXPIRES_IN = 5_184_000  # 60일


@pytest.fixture
async def oauth_session(api_client, monkeypatch):
    """로그인 세션 + CSRF + OAuth/암호화 설정. 생성 계정은 테스트가 정리."""
    user = await User.create(
        email=f"t-{uuid.uuid4().hex[:10]}@test.local",
        password_hash=hash_password(PASSWORD), role=Role.reviewer,
    )
    await api_client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD})
    csrf = {"X-CSRF-Token": (await api_client.get("/api/auth/csrf")).json()["csrf_token"]}
    monkeypatch.setattr(settings, "credentials_fernet_keys", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "threads_app_id", "app-id-1")
    monkeypatch.setattr(settings, "threads_app_secret", APP_SECRET)
    monkeypatch.setattr(settings, "threads_redirect_uri", "https://nutti.co.kr/threads-callback.html")
    yield api_client, csrf, user
    await SnsAccount.filter(user_id=user.id).delete()
    await user.delete()


async def _fresh_state(client) -> str:
    """authorize-url 을 호출해 실제 발급된 state 를 URL 에서 회수."""
    r = await client.get("/api/sns-accounts/threads-oauth/authorize-url")
    assert r.status_code == 200
    return parse_qs(urlparse(r.json()["url"]).query)["state"][0]


def _mock_transport(monkeypatch, *, exchange_status: int = 200, username: str = "oauth_bot",
                    network_error: bool = False):
    """OAuth 3단계(코드 교환→장기 전환→/me)를 시나리오별로 응답하는 MockTransport."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if network_error:
            raise httpx.ConnectError("boom", request=request)
        path = request.url.path
        if path == "/oauth/access_token":
            if exchange_status != 200:
                return httpx.Response(
                    exchange_status,
                    json={"error": {"code": 100, "type": "OAuthException",
                          "message": "SENSITIVE-ECHO: code=..."}},
                )
            return httpx.Response(200, json={"access_token": SHORT_TOKEN, "user_id": 999})
        if path == "/access_token":
            return httpx.Response(
                200,
                json={"access_token": LONG_TOKEN, "token_type": "bearer",
                      "expires_in": EXPIRES_IN},
            )
        if path == "/v1.0/me":
            assert request.headers["Authorization"] == f"Bearer {LONG_TOKEN}"
            return httpx.Response(200, json={"id": "999", "username": username})
        raise AssertionError(f"예상 밖 호출: {request.url}")

    def _mock_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=threads._API, timeout=1.0, transport=httpx.MockTransport(handler)
        )

    monkeypatch.setattr(threads, "_client", _mock_client)
    return calls


async def test_authorize_url_issues_state(oauth_session):
    client, csrf, user = oauth_session
    r = await client.get("/api/sns-accounts/threads-oauth/authorize-url")
    assert r.status_code == 200
    q = parse_qs(urlparse(r.json()["url"]).query)
    assert q["client_id"] == ["app-id-1"] and q["response_type"] == ["code"]
    assert q["redirect_uri"] == ["https://nutti.co.kr/threads-callback.html"]
    assert "threads_keyword_search" in q["scope"][0]
    assert len(q["state"][0]) > 20  # 예측 불가 state 발급
    assert APP_SECRET not in r.text  # secret 은 authorize URL 에 없어야 한다


async def test_authorize_url_unconfigured_503(oauth_session, monkeypatch):
    client, csrf, user = oauth_session
    # secret 누락도 503 — 동의 화면까지 보내놓고 교환만 실패하는 반쪽 설정 차단(리뷰 M8)
    monkeypatch.setattr(settings, "threads_app_secret", "")
    assert (await client.get("/api/sns-accounts/threads-oauth/authorize-url")).status_code == 503


async def test_oauth_connect_creates_account(oauth_session, monkeypatch, caplog):
    """정상 연동 → 계정 생성(안정 식별자 저장) + 장기 토큰 암호화 저장 + 만료 기록.
    응답/로그 어디에도 code/토큰/시크릿 비노출(불변식 ③)."""
    client, csrf, user = oauth_session
    calls = _mock_transport(monkeypatch)
    state = await _fresh_state(client)

    with caplog.at_level(logging.INFO):
        r = await client.post(
            "/api/sns-accounts/threads-oauth",
            json={"code": "auth-code-1", "state": state}, headers=csrf,
        )
    assert r.status_code == 201
    body = r.json()
    assert body["platform"] == "threads" and body["display_name"] == "oauth_bot"
    assert LONG_TOKEN not in r.text and SHORT_TOKEN not in r.text
    # 로그 유출 회귀(리뷰 High-2): INFO 로그에 시크릿/토큰/코드 없음
    assert APP_SECRET not in caplog.text and LONG_TOKEN not in caplog.text
    assert SHORT_TOKEN not in caplog.text and "auth-code-1" not in caplog.text

    # 교환 요청이 올바른 재료로 전송됐는지(리뷰 M9 — 전송 파라미터 검증)
    exchange = calls[0]
    sent = parse_qs(exchange.content.decode())
    assert sent["code"] == ["auth-code-1"] and sent["client_secret"] == [APP_SECRET]
    assert sent["redirect_uri"] == ["https://nutti.co.kr/threads-callback.html"]
    assert calls[1].url.params["access_token"] == SHORT_TOKEN

    account = await SnsAccount.get(id=body["id"])
    assert account.platform_username == "oauth_bot"
    assert account.platform_user_id == "999"  # 안정 식별자 저장(리뷰 High-5)
    assert account.status == AccountStatus.active
    expected = datetime.now(UTC) + timedelta(seconds=EXPIRES_IN)
    assert abs((account.token_expires_at - expected).total_seconds()) < 60
    secret = await SnsAccountSecret.get(account_id=account.id)
    assert crypto.decrypt_credentials(secret.encrypted_credentials) == {
        "access_token": LONG_TOKEN
    }


async def test_oauth_reconnect_matches_stable_id_and_updates_username(oauth_session, monkeypatch):
    """재연동 upsert 키는 안정 식별자 — username 이 바뀌어도(rename) 같은 계정을 찾아
    자격증명 교체 + username 갱신(새 계정 생성 없음, 계정 id 보존)."""
    client, csrf, user = oauth_session
    _mock_transport(monkeypatch, username="renamed_bot")
    existing = await SnsAccount.create(
        user=user, platform=Platform.threads, display_name="기존",
        platform_username="old_name", platform_user_id="999",
        status=AccountStatus.expired,
    )
    await SnsAccountSecret.create(
        account=existing,
        encrypted_credentials=crypto.encrypt_credentials({"access_token": "old-tok"}),
    )
    state = await _fresh_state(client)

    r = await client.post(
        "/api/sns-accounts/threads-oauth",
        json={"code": "auth-code-2", "state": state}, headers=csrf,
    )
    assert r.status_code == 201 and r.json()["id"] == existing.id
    assert await SnsAccount.filter(user_id=user.id).count() == 1
    await existing.refresh_from_db()
    assert existing.status == AccountStatus.active
    assert existing.platform_username == "renamed_bot"  # 표시/판정 키만 갱신
    secret = await SnsAccountSecret.get(account_id=existing.id)
    assert crypto.decrypt_credentials(secret.encrypted_credentials) == {
        "access_token": LONG_TOKEN
    }


async def test_oauth_state_required_and_single_use(oauth_session, monkeypatch):
    """state 없으면 422, 위조 state 400, 사용된 state 재사용 400 — 타인 code 이식 차단
    (리뷰 High-3). 실패 시 계정 미생성."""
    client, csrf, user = oauth_session
    _mock_transport(monkeypatch)

    r = await client.post(
        "/api/sns-accounts/threads-oauth", json={"code": "c1"}, headers=csrf
    )
    assert r.status_code == 422  # state 필수

    r = await client.post(
        "/api/sns-accounts/threads-oauth",
        json={"code": "c1", "state": "forged-state"}, headers=csrf,
    )
    assert r.status_code == 400 and "연동 세션" in r.json()["detail"]

    state = await _fresh_state(client)
    assert (
        await client.post(
            "/api/sns-accounts/threads-oauth",
            json={"code": "c1", "state": state}, headers=csrf,
        )
    ).status_code == 201
    await SnsAccount.filter(user_id=user.id).delete()
    r = await client.post(
        "/api/sns-accounts/threads-oauth",
        json={"code": "c2", "state": state}, headers=csrf,  # 1회용 재사용
    )
    assert r.status_code == 400
    assert await SnsAccount.filter(user_id=user.id).count() == 0


async def test_oauth_state_superseded_by_reissue(oauth_session, monkeypatch):
    """사용자당 state 1개 — 재발급이 이전 state 를 무효화(2차 리뷰 High-1: 반복 발급으로
    저장소가 사용자 수 이상 자라지 않는다). 최신 state 만 유효."""
    client, csrf, user = oauth_session
    _mock_transport(monkeypatch)
    state_old = await _fresh_state(client)
    state_new = await _fresh_state(client)

    r = await client.post(
        "/api/sns-accounts/threads-oauth",
        json={"code": "c1", "state": state_old}, headers=csrf,
    )
    assert r.status_code == 400  # 덮어쓰인 이전 state 거부
    r = await client.post(
        "/api/sns-accounts/threads-oauth",
        json={"code": "c1", "state": state_new}, headers=csrf,
    )
    assert r.status_code == 201


async def test_oauth_state_is_user_bound(oauth_session, monkeypatch):
    """다른 사용자가 발급받은 state 는 내 세션에서 거부 — code 이식 공격 경로 차단."""
    client, csrf, user = oauth_session
    _mock_transport(monkeypatch)
    from app.api import sns_accounts as sa

    foreign_state = sa._issue_oauth_state(user.id + 987654)  # 타 사용자 명의 state
    r = await client.post(
        "/api/sns-accounts/threads-oauth",
        json={"code": "c1", "state": foreign_state}, headers=csrf,
    )
    assert r.status_code == 400
    assert await SnsAccount.filter(user_id=user.id).count() == 0


async def test_oauth_invalid_code_400_no_echo(oauth_session, monkeypatch, caplog):
    """교환 거부(만료/재사용 코드) → 400 고정 메시지. 코드·Meta message 필드가 응답/로그에
    비반사(불변식 ③ — Meta error.message 는 요청 echo 가능성이 있어 요약에서 제외)."""
    client, csrf, user = oauth_session
    _mock_transport(monkeypatch, exchange_status=400)
    state = await _fresh_state(client)

    with caplog.at_level(logging.INFO):
        r = await client.post(
            "/api/sns-accounts/threads-oauth",
            json={"code": "expired-code-1", "state": state}, headers=csrf,
        )
    assert r.status_code == 400
    assert "expired-code-1" not in r.text
    assert "인증 코드가 유효하지 않거나" in r.json()["detail"]
    assert "SENSITIVE-ECHO" not in r.text and "SENSITIVE-ECHO" not in caplog.text
    assert await SnsAccount.filter(user_id=user.id).count() == 0


async def test_oauth_upstream_failure_502_no_url_leak(oauth_session, monkeypatch, caplog):
    """네트워크 실패 → 502 고정 메시지(400 '코드 무효' 오안내 아님 — 리뷰 M6).
    httpx 예외 문자열의 URL(시크릿 쿼리 가능)이 응답/로그에 없음."""
    client, csrf, user = oauth_session
    _mock_transport(monkeypatch, network_error=True)
    state = await _fresh_state(client)

    with caplog.at_level(logging.INFO):
        r = await client.post(
            "/api/sns-accounts/threads-oauth",
            json={"code": "c1", "state": state}, headers=csrf,
        )
    assert r.status_code == 502
    assert "다시 연동" in r.json()["detail"]  # state 소비 후라 재시도가 아닌 재연동 안내(M4)
    assert APP_SECRET not in r.text and APP_SECRET not in caplog.text
    assert "graph.threads.net" not in r.text


async def test_oauth_unconfigured_503(oauth_session, monkeypatch):
    client, csrf, user = oauth_session
    state = await _fresh_state(client)
    monkeypatch.setattr(settings, "threads_app_secret", "")
    r = await client.post(
        "/api/sns-accounts/threads-oauth",
        json={"code": "auth-code-3", "state": state}, headers=csrf,
    )
    assert r.status_code == 503


async def test_oauth_extra_key_422(oauth_session):
    client, csrf, user = oauth_session
    r = await client.post(
        "/api/sns-accounts/threads-oauth",
        json={"code": "c", "state": "s", "platform": "naver"}, headers=csrf,
    )
    assert r.status_code == 422


def test_httpx_logger_pinned_to_warning():
    """main.py 임포트만으로 httpx/httpcore 로거가 WARNING 고정 — INFO 재상승 회귀 방지
    (리뷰 High-2: httpx 는 INFO 에서 시크릿 쿼리 포함 전체 URL 을 로깅한다)."""
    import app.main  # noqa: F401 - 임포트 부수효과(로거 고정) 검증

    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING


# ── R10①: 수동 토큰 등록/교체도 안정 식별자를 확보해야 OAuth 재연동이 같은 행으로 수렴 ──
#
# 실측 회귀(2026-08-11): 수동 등록 계정은 platform_user_id 가 NULL 이라 같은 Threads
# 계정을 OAuth 로 연동하면 upsert 키가 매칭되지 않아 별도 행이 생겼다(계정 26→31→32).
# 그때 옛 계정을 참조하던 소스 config 는 고아가 되어 조용히 수집이 멈춘다
# (FetchError: config.sns_account_id 의 threads 계정을 찾을 수 없습니다 — 소스 110·119).

MANUAL_TOKEN = "manual-access-token-1"


def _mock_me(monkeypatch, *, user_id: str = "999", username: str = "manual_bot",
             status: int = 200, network_error: bool = False):
    """`/me` 만 응답하는 MockTransport — 수동 등록/교체 경로는 이 호출만 한다."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if network_error:
            raise httpx.ConnectError("boom", request=request)
        assert request.url.path == "/v1.0/me", f"예상 밖 호출: {request.url}"
        if status != 200:
            return httpx.Response(
                status,
                json={"error": {"code": 190, "message": "Invalid OAuth access token"}},
            )
        return httpx.Response(200, json={"id": user_id, "username": username})

    monkeypatch.setattr(
        threads, "_client",
        lambda: httpx.AsyncClient(
            base_url=threads._API, timeout=1.0, transport=httpx.MockTransport(handler)
        ),
    )
    return calls


async def _register_manual(client, csrf, *, display_name: str = "수동등록"):
    return await client.post(
        "/api/sns-accounts",
        json={"platform": "threads", "display_name": display_name,
              "credentials": {"access_token": MANUAL_TOKEN}},
        headers=csrf,
    )


async def test_manual_register_stores_stable_identity(oauth_session, monkeypatch, caplog):
    """수동 토큰 등록도 /me 로 안정 식별자를 확보해 저장한다 — 토큰은 어디에도 노출 없음."""
    client, csrf, user = oauth_session
    calls = _mock_me(monkeypatch)

    with caplog.at_level(logging.INFO):
        r = await _register_manual(client, csrf)
    assert r.status_code == 201
    assert MANUAL_TOKEN not in r.text and MANUAL_TOKEN not in caplog.text
    assert calls[0].headers["Authorization"] == f"Bearer {MANUAL_TOKEN}"

    account = await SnsAccount.get(id=r.json()["id"])
    assert account.platform_user_id == "999"
    assert account.platform_username == "manual_bot"
    # 수동 등록은 토큰 수명을 모른다 — OAuth 경로와 달리 만료 시각은 비운다
    assert account.token_expires_at is None


async def test_manual_register_then_oauth_keeps_same_row(oauth_session, monkeypatch):
    """**R10① 핵심 회귀**: 수동 등록 → 같은 계정 OAuth 연동 → 계정 행이 늘지 않고
    id 가 보존된다(소스 config 의 sns_account_id 참조가 살아 있어야 한다)."""
    from app.models import Source

    client, csrf, user = oauth_session
    _mock_me(monkeypatch)
    manual = await _register_manual(client, csrf)
    assert manual.status_code == 201
    account_id = manual.json()["id"]

    # 그 계정을 참조하는 소스 — 재연동 후에도 이 참조가 유효해야 한다
    source = await Source.create(
        user=user, type="threads", name="간식",
        config={"query": "간식", "sns_account_id": account_id},
    )

    # 같은 Threads 신원(id=999)으로 OAuth 연동 — username 은 바뀌어 있어도 무관
    _mock_transport(monkeypatch, username="renamed_after_oauth")
    state = await _fresh_state(client)
    r = await client.post(
        "/api/sns-accounts/threads-oauth",
        json={"code": "auth-code-r10", "state": state}, headers=csrf,
    )
    assert r.status_code == 201
    assert r.json()["id"] == account_id, "새 계정 행이 생겼다 — R10① 재발"
    assert await SnsAccount.filter(user_id=user.id, platform=Platform.threads).count() == 1

    account = await SnsAccount.get(id=account_id)
    assert account.platform_username == "renamed_after_oauth"  # 표시용은 갱신
    assert account.token_expires_at is not None  # OAuth 는 수명을 안다
    secret = await SnsAccountSecret.get(account_id=account_id)
    assert crypto.decrypt_credentials(secret.encrypted_credentials) == {
        "access_token": LONG_TOKEN
    }
    # 소스가 여전히 실재하는 계정을 가리킨다(고아 아님)
    await source.refresh_from_db()
    assert await SnsAccount.filter(id=source.config["sns_account_id"]).exists()
    await source.delete()


async def test_manual_register_survives_profile_failure(oauth_session, monkeypatch):
    """/me 가 거부/장애여도 등록은 막지 않는다(best-effort) — 무효 토큰은 이후 수집·전송
    시점의 health 회계로 드러나고, 업스트림 장애로 등록 자체가 불가한 편이 더 나쁘다."""
    client, csrf, user = oauth_session
    _mock_me(monkeypatch, status=400)
    r = await _register_manual(client, csrf)
    assert r.status_code == 201
    assert (await SnsAccount.get(id=r.json()["id"])).platform_user_id is None

    _mock_me(monkeypatch, network_error=True)
    r2 = await _register_manual(client, csrf, display_name="두번째")
    assert r2.status_code == 201
    assert (await SnsAccount.get(id=r2.json()["id"])).platform_user_id is None


async def test_credentials_replace_backfills_identity(oauth_session, monkeypatch):
    """토큰 교체가 NULL 식별자를 채운다 — 기존 수동 등록 계정의 자기 치유 경로."""
    client, csrf, user = oauth_session
    legacy = await SnsAccount.create(
        user=user, platform=Platform.threads, display_name="옛 수동등록",
        status=AccountStatus.expired,
    )
    await SnsAccountSecret.create(
        account=legacy, encrypted_credentials=crypto.encrypt_credentials({"access_token": "old"})
    )
    _mock_me(monkeypatch, username="healed_bot")
    r = await client.put(
        f"/api/sns-accounts/{legacy.id}/credentials",
        json={"credentials": {"access_token": MANUAL_TOKEN}}, headers=csrf,
    )
    assert r.status_code == 204
    await legacy.refresh_from_db()
    assert legacy.platform_user_id == "999" and legacy.platform_username == "healed_bot"
    assert legacy.status == AccountStatus.active


async def test_credentials_replace_identity_clash_409(oauth_session, monkeypatch):
    """그 신원이 이미 다른 계정에 붙어 있으면 409 — 부분 unique 제약(IntegrityError)보다
    무엇이 문제인지 알려준다. 잘못된 계정에 토큰을 몰아넣어 중복 신원을 만들지 않는다."""
    client, csrf, user = oauth_session
    owner = await SnsAccount.create(
        user=user, platform=Platform.threads, display_name="이미 연결됨",
        platform_username="owner_bot", platform_user_id="999",
    )
    other = await SnsAccount.create(
        user=user, platform=Platform.threads, display_name="다른 항목",
    )
    await SnsAccountSecret.create(
        account=other, encrypted_credentials=crypto.encrypt_credentials({"access_token": "old"})
    )
    _mock_me(monkeypatch)  # id=999 → owner 와 충돌
    r = await client.put(
        f"/api/sns-accounts/{other.id}/credentials",
        json={"credentials": {"access_token": MANUAL_TOKEN}}, headers=csrf,
    )
    assert r.status_code == 409
    await other.refresh_from_db()
    assert other.platform_user_id is None  # 롤백 — 부분 반영 없음
    assert (await SnsAccount.get(id=owner.id)).platform_user_id == "999"
