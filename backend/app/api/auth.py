"""/api/auth — 로그인·로그아웃·me·csrf (API-SPEC §인증)."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app import auth
from app.api.deps import current_user, require_csrf, session_data
from app.config import settings
from app.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    role: str


@router.post("/login")
async def login(body: LoginIn, response: Response) -> UserOut:
    user = await User.get_or_none(email=body.email)
    # 미존재 이메일도 동일하게 해시 검증을 수행한다(타이밍 기반 사용자 열거 방지).
    # argon2 는 CPU-바운드 → 스레드풀 오프로드(단일 워커 이벤트 루프 블로킹 방지).
    ok = await run_in_threadpool(
        auth.verify_password, user.password_hash if user else None, body.password
    )
    if not ok or user is None:
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다")
    response.set_cookie(
        auth.SESSION_COOKIE,
        auth.issue_session(user.id),
        max_age=settings.session_ttl_sec,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )
    return UserOut(id=user.id, email=user.email, role=user.role.value)


@router.post("/logout", dependencies=[Depends(require_csrf)])
async def logout(response: Response) -> dict:
    response.delete_cookie(auth.SESSION_COOKIE)
    return {"ok": True}


@router.get("/me")
async def me(user: Annotated[User, Depends(current_user)], response: Response) -> UserOut:
    response.headers["Cache-Control"] = "no-store"
    return UserOut(id=user.id, email=user.email, role=user.role.value)


@router.get("/csrf")
async def csrf(data: Annotated[dict, Depends(session_data)], response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return {"csrf_token": data["csrf"]}
