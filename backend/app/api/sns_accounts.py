"""/api/sns-accounts — SNS 계정 CRUD (admin, API-SPEC §SNS 계정).

자격증명은 쓰기 전용(write-only): 입력 즉시 Fernet 암호화해 sns_account_secrets 에만
저장하고, 어떤 응답에도 재노출하지 않는다. read path 는 secret 테이블을 조회조차
하지 않는 전용 스키마를 쓴다 (MUST-FIX #3, NFR-S1).
"""
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from tortoise.transactions import in_transaction

from app import crypto
from app.api.deps import require_admin, require_csrf
from app.models import AccountStatus, Platform, SnsAccount, SnsAccountSecret, User

router = APIRouter(
    prefix="/api/sns-accounts", tags=["sns-accounts"],
    dependencies=[Depends(require_admin)],
)


class SnsAccountOut(BaseModel):
    # 전용 read 스키마 — 여기 없는 필드는 구조적으로 응답에 실릴 수 없다.
    id: int
    platform: Platform
    display_name: str
    status: AccountStatus
    token_expires_at: datetime | None


class SnsAccountIn(BaseModel):
    platform: Platform
    display_name: str = Field(min_length=1, max_length=255)
    credentials: dict[str, Any]


def _out(a: SnsAccount) -> SnsAccountOut:
    return SnsAccountOut(
        id=a.id, platform=a.platform, display_name=a.display_name,
        status=a.status, token_expires_at=a.token_expires_at,
    )


@router.get("")
async def list_accounts() -> list[SnsAccountOut]:
    return [_out(a) for a in await SnsAccount.all().order_by("id")]


@router.post("", status_code=201, dependencies=[Depends(require_csrf)])
async def create_account(
    body: SnsAccountIn, admin: Annotated[User, Depends(require_admin)]
) -> SnsAccountOut:
    try:
        ciphertext = crypto.encrypt_credentials(body.credentials)
    except (RuntimeError, ValueError) as exc:
        # 키 미설정(RuntimeError)/오형식(ValueError) — 자격증명을 평문으로 받아둘 수는 없다.
        # 예외 문자열은 응답에 싣지 않는다(고정 메시지).
        raise HTTPException(
            status_code=503,
            detail="자격증명 암호화 키가 설정되지 않았거나 형식이 잘못되었습니다 — 서버 설정 필요",
        ) from exc
    async with in_transaction():
        account = await SnsAccount.create(
            user=admin, platform=body.platform, display_name=body.display_name
        )
        await SnsAccountSecret.create(account=account, encrypted_credentials=ciphertext)
    return _out(account)


@router.delete("/{account_id}", status_code=204, dependencies=[Depends(require_csrf)])
async def delete_account(account_id: int) -> None:
    # secret 은 FK cascade 로 함께 삭제된다.
    if not await SnsAccount.filter(id=account_id).delete():
        raise HTTPException(status_code=404, detail="SNS 계정이 없습니다")
