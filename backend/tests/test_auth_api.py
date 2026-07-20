"""/api/auth 통합(실 Postgres) — 로그인/me/csrf/logout + 쿠키 속성."""
import uuid

import pytest

from app.auth import hash_password
from app.models import Role, User

pytestmark = pytest.mark.db

PASSWORD = "pw-test-1234"


@pytest.fixture
async def user(api_client):
    u = await User.create(
        email=f"t-{uuid.uuid4().hex[:10]}@test.local",
        password_hash=hash_password(PASSWORD),
        role=Role.admin,
    )
    yield u
    await u.delete()


async def test_login_sets_httponly_cookie_and_me(api_client, user):
    r = await api_client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD})
    assert r.status_code == 200
    assert r.json() == {"id": user.id, "email": user.email, "role": "admin"}
    set_cookie = r.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=lax" in set_cookie

    r = await api_client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["email"] == user.email


async def test_login_wrong_password_401(api_client, user):
    r = await api_client.post("/api/auth/login", json={"email": user.email, "password": "nope"})
    assert r.status_code == 401
    r = await api_client.post(
        "/api/auth/login", json={"email": "ghost@test.local", "password": "nope"}
    )
    assert r.status_code == 401


async def test_me_without_session_401(api_client):
    assert (await api_client.get("/api/auth/me")).status_code == 401


async def test_logout_requires_csrf_then_clears_session(api_client, user):
    await api_client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD})

    # CSRF 헤더 없이 → 403 (NFR-S4)
    assert (await api_client.post("/api/auth/logout")).status_code == 403
    # 틀린 토큰 → 403
    r = await api_client.post("/api/auth/logout", headers={"X-CSRF-Token": "wrong"})
    assert r.status_code == 403

    token = (await api_client.get("/api/auth/csrf")).json()["csrf_token"]
    r = await api_client.post("/api/auth/logout", headers={"X-CSRF-Token": token})
    assert r.status_code == 200
    assert (await api_client.get("/api/auth/me")).status_code == 401
