"""crypto.py — 자격증명 암/복호 + 키 회전(NFR-S1·S2). DB 불필요."""
import pytest
from cryptography.fernet import Fernet, InvalidToken

from app import crypto
from app.config import settings

KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()


def test_roundtrip(monkeypatch):
    monkeypatch.setattr(settings, "credentials_fernet_keys", KEY_A)
    creds = {"access_token": "tok-123", "refresh_token": "ref-456"}
    ct = crypto.encrypt_credentials(creds)
    assert b"tok-123" not in ct  # 평문이 그대로 남지 않는다
    assert crypto.decrypt_credentials(ct) == creds


def test_key_rotation_old_key_still_decrypts(monkeypatch):
    monkeypatch.setattr(settings, "credentials_fernet_keys", KEY_A)
    ct = crypto.encrypt_credentials({"t": 1})
    # 신키 B 를 앞에 추가(회전) — 구키 A 로 암호화된 데이터도 계속 복호돼야 한다.
    monkeypatch.setattr(settings, "credentials_fernet_keys", f"{KEY_B},{KEY_A}")
    assert crypto.decrypt_credentials(ct) == {"t": 1}


def test_unknown_key_fails(monkeypatch):
    monkeypatch.setattr(settings, "credentials_fernet_keys", KEY_A)
    ct = crypto.encrypt_credentials({"t": 1})
    monkeypatch.setattr(settings, "credentials_fernet_keys", KEY_B)
    with pytest.raises(InvalidToken):
        crypto.decrypt_credentials(ct)


def test_missing_keys_raises(monkeypatch):
    monkeypatch.setattr(settings, "credentials_fernet_keys", "")
    with pytest.raises(RuntimeError):
        crypto.encrypt_credentials({"t": 1})
