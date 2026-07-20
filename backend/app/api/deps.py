"""FastAPI 공용 의존성 — 세션 인증·권한·CSRF (API-SPEC §공통)."""
import hmac
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException

from app import auth
from app.models import Role, User

SessionCookie = Annotated[str | None, Cookie(alias=auth.SESSION_COOKIE)]


async def session_data(session: SessionCookie = None) -> dict:
    data = auth.read_session(session) if session else None
    if data is None:
        raise HTTPException(status_code=401, detail="인증이 필요합니다")
    return data


async def current_user(data: Annotated[dict, Depends(session_data)]) -> User:
    user = await User.get_or_none(id=data["uid"])
    if user is None:
        raise HTTPException(status_code=401, detail="인증이 필요합니다")
    return user


async def require_admin(user: Annotated[User, Depends(current_user)]) -> User:
    if user.role != Role.admin:
        raise HTTPException(status_code=403, detail="admin 권한이 필요합니다")
    return user


async def require_csrf(
    data: Annotated[dict, Depends(session_data)],
    # 삭제된 사용자의 세션이 CSRF 검사를 통과하지 못하게 사용자 생존도 함께 요구한다
    # (향후 approve 등 상태변경 라우트가 이 의존성 하나만 걸어도 안전하도록).
    _user: Annotated[User, Depends(current_user)],
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    # 상태변경(POST/PATCH/DELETE) 엔드포인트는 이 의존성을 반드시 건다(NFR-S4).
    if not x_csrf_token or not hmac.compare_digest(data["csrf"], x_csrf_token):
        raise HTTPException(status_code=403, detail="CSRF 토큰이 유효하지 않습니다")
