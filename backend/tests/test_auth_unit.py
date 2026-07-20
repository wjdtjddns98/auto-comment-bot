"""auth.py 단위 — 해시·세션 토큰·CSRF 페이로드. DB 불필요."""
from app import auth


def test_password_hash_and_verify():
    h = auth.hash_password("secret-pw")
    assert h != "secret-pw"
    assert auth.verify_password(h, "secret-pw") is True
    assert auth.verify_password(h, "wrong") is False
    # 존재하지 않는 사용자 경로(해시 None)도 예외 없이 False.
    assert auth.verify_password(None, "whatever") is False


def test_session_roundtrip():
    token = auth.issue_session(42)
    data = auth.read_session(token)
    assert data is not None
    assert data["uid"] == 42
    assert len(data["csrf"]) >= 32


def test_session_tampered_token_rejected():
    token = auth.issue_session(1)
    assert auth.read_session(token[:-2] + "xx") is None
    assert auth.read_session("garbage") is None


def test_csrf_differs_per_session():
    a = auth.read_session(auth.issue_session(1))
    b = auth.read_session(auth.issue_session(1))
    assert a["csrf"] != b["csrf"]


def test_session_wrong_payload_types_rejected():
    # 정상 암호화됐지만 형식이 오염된 payload 는 거부(uid=str, csrf=int, dict 아님).
    f = auth._session_fernet()
    for payload in ('{"uid": "1", "csrf": "x"}', '{"uid": 1, "csrf": 7}', '"not-a-dict"'):
        token = f.encrypt(payload.encode()).decode()
        assert auth.read_session(token) is None
