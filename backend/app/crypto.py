"""SNS 자격증명 Fernet 암/복호 (NFR-S1·S2).

- 암호문은 sns_account_secrets 별도 테이블에만 저장된다(MUST-FIX #3).
- 키 회전: CREDENTIALS_FERNET_KEYS 를 쉼표 구분 목록으로 주면 MultiFernet 이
  첫 키로 암호화하고 나머지 키로도 복호를 시도한다(구키 → 신키 회전 경로).
"""
import json

from cryptography.fernet import Fernet, MultiFernet

from app.config import settings


def _multi_fernet() -> MultiFernet:
    keys = [k.strip() for k in settings.credentials_fernet_keys.split(",") if k.strip()]
    if not keys:
        raise RuntimeError("CREDENTIALS_FERNET_KEYS 미설정 — SNS 자격증명을 저장/복호할 수 없다")
    return MultiFernet([Fernet(k) for k in keys])


def encrypt_credentials(credentials: dict) -> bytes:
    return _multi_fernet().encrypt(json.dumps(credentials).encode())


def decrypt_credentials(ciphertext: bytes) -> dict:
    return json.loads(_multi_fernet().decrypt(bytes(ciphertext)))
