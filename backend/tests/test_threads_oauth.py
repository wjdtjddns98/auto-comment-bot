"""Threads OAuth 코드 연동(실 Postgres + MockTransport) — 교환·upsert·비밀 비노출."""
import uuid
from datetime import UTC, datetime, timedelta

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
    monkeypatch.setattr(settings, "threads_app_secret", "app-secret-1")
    monkeypatch.setattr(settings, "threads_redirect_uri", "https://nutti.co.kr/threads-callback.html")
    yield api_client, csrf, user
    await SnsAccount.filter(user_id=user.id).delete()
    await user.delete()


def _mock_transport(monkeypatch, *, exchange_status: int = 200, username: str = "oauth_bot"):
    """OAuth 3단계(코드 교환→장기 전환→/me)를 시나리오별로 응답하는 MockTransport."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/oauth/access_token":
            if exchange_status != 200:
                return httpx.Response(
                    exchange_status,
                    json={"error": {"code": 100, "type": "OAuthException"}},
                )
            return httpx.Response(200, json={"access_token": SHORT_TOKEN, "user_id": 999})
        if path == "/access_token":
            assert request.url.params["access_token"] == SHORT_TOKEN
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


async def test_authorize_url(oauth_session):
    client, csrf, user = oauth_session
    r = await client.get("/api/sns-accounts/threads-oauth/authorize-url")
    assert r.status_code == 200
    url = r.json()["url"]
    assert url.startswith("https://threads.net/oauth/authorize?client_id=app-id-1")
    assert "threads_keyword_search" in url and "response_type=code" in url
    assert "nutti.co.kr%2Fthreads-callback.html" in url  # redirect_uri 인코딩


async def test_authorize_url_unconfigured_503(oauth_session, monkeypatch):
    client, csrf, user = oauth_session
    monkeypatch.setattr(settings, "threads_app_id", "")
    assert (await client.get("/api/sns-accounts/threads-oauth/authorize-url")).status_code == 503


async def test_oauth_connect_creates_account(oauth_session, monkeypatch):
    """코드 교환 성공 → 계정 생성 + 장기 토큰 암호화 저장 + 만료시각 기록, 토큰 비노출."""
    client, csrf, user = oauth_session
    _mock_transport(monkeypatch)

    r = await client.post(
        "/api/sns-accounts/threads-oauth", json={"code": "auth-code-1"}, headers=csrf
    )
    assert r.status_code == 201
    body = r.json()
    assert body["platform"] == "threads" and body["display_name"] == "oauth_bot"
    assert LONG_TOKEN not in r.text and SHORT_TOKEN not in r.text  # 불변식 ③

    account = await SnsAccount.get(id=body["id"])
    assert account.platform_username == "oauth_bot"
    assert account.status == AccountStatus.active
    expected = datetime.now(UTC) + timedelta(seconds=EXPIRES_IN)
    assert abs((account.token_expires_at - expected).total_seconds()) < 60
    secret = await SnsAccountSecret.get(account_id=account.id)
    assert crypto.decrypt_credentials(secret.encrypted_credentials) == {
        "access_token": LONG_TOKEN
    }


async def test_oauth_reconnect_replaces_credentials(oauth_session, monkeypatch):
    """같은 username 재연동 → 새 계정이 아니라 기존 계정 자격증명 교체(id 보존) + active."""
    client, csrf, user = oauth_session
    _mock_transport(monkeypatch)
    existing = await SnsAccount.create(
        user=user, platform=Platform.threads, display_name="기존", platform_username="oauth_bot",
        status=AccountStatus.expired,
    )
    await SnsAccountSecret.create(
        account=existing,
        encrypted_credentials=crypto.encrypt_credentials({"access_token": "old-tok"}),
    )

    r = await client.post(
        "/api/sns-accounts/threads-oauth", json={"code": "auth-code-2"}, headers=csrf
    )
    assert r.status_code == 201 and r.json()["id"] == existing.id
    assert await SnsAccount.filter(user_id=user.id, platform_username="oauth_bot").count() == 1
    await existing.refresh_from_db()
    assert existing.status == AccountStatus.active
    secret = await SnsAccountSecret.get(account_id=existing.id)
    assert crypto.decrypt_credentials(secret.encrypted_credentials) == {
        "access_token": LONG_TOKEN
    }


async def test_oauth_invalid_code_400_no_echo(oauth_session, monkeypatch):
    """교환 실패(만료/재사용 코드) → 400 고정 메시지, 코드 echo 없음, 계정 미생성."""
    client, csrf, user = oauth_session
    _mock_transport(monkeypatch, exchange_status=400)

    r = await client.post(
        "/api/sns-accounts/threads-oauth", json={"code": "expired-code-1"}, headers=csrf
    )
    assert r.status_code == 400
    assert "expired-code-1" not in r.text  # 코드 미반사
    assert "인증 코드가 유효하지 않거나" in r.json()["detail"]
    assert await SnsAccount.filter(user_id=user.id).count() == 0


async def test_oauth_unconfigured_503(oauth_session, monkeypatch):
    client, csrf, user = oauth_session
    monkeypatch.setattr(settings, "threads_app_secret", "")
    r = await client.post(
        "/api/sns-accounts/threads-oauth", json={"code": "auth-code-3"}, headers=csrf
    )
    assert r.status_code == 503


async def test_oauth_extra_key_422(oauth_session):
    client, csrf, user = oauth_session
    r = await client.post(
        "/api/sns-accounts/threads-oauth",
        json={"code": "c", "platform": "naver"}, headers=csrf,
    )
    assert r.status_code == 422
