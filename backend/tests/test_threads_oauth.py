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
