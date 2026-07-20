"""인증 코어 — argon2 비밀번호 해시 + Fernet 세션 토큰 + CSRF (NFR-S4).

세션은 서버 저장소 없는 stateless 토큰: {"uid", "csrf"} 를 Fernet 으로 암호화해
HttpOnly 쿠키에 담는다. 만료는 Fernet ttl 검증, 로그아웃은 쿠키 삭제.
CSRF 토큰은 세션 페이로드 안에 있어 세션과 수명을 같이한다(double-submit 불필요).
"""
import json
import secrets
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

SESSION_COOKIE = "session"

_ph = PasswordHasher()
# 존재하지 않는 이메일도 동일 시간 소모(사용자 열거 방지)용 더미 해시.
_DUMMY_HASH = _ph.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _ph.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except VerificationError:
        return False


@lru_cache(maxsize=1)
def _session_fernet() -> Fernet:
    key = settings.session_fernet_key
    if not key:
        if settings.app_env != "dev":
            raise RuntimeError("SESSION_FERNET_KEY 미설정 — dev 외 환경에서는 필수")
        # dev 편의: 프로세스 수명 동안만 유효한 임시 키(재시작 시 전체 로그아웃).
        key = Fernet.generate_key().decode()
    return Fernet(key)


def issue_session(user_id: int) -> str:
    payload = {"uid": user_id, "csrf": secrets.token_urlsafe(32)}
    return _session_fernet().encrypt(json.dumps(payload).encode()).decode()


def read_session(token: str) -> dict | None:
    """유효하면 {"uid", "csrf"}, 위조/만료/형식오류면 None."""
    try:
        raw = _session_fernet().decrypt(token.encode(), ttl=settings.session_ttl_sec)
        data = json.loads(raw)
    except (InvalidToken, ValueError):
        return None
    if not isinstance(data, dict) or "uid" not in data or "csrf" not in data:
        return None
    return data
