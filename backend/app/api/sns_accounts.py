"""/api/sns-accounts — 본인 귀속 SNS 계정 셀프서비스 (API-SPEC §SNS 계정).

정책: 로그인 사용자 누구나 자기 계정을 연동/조회/삭제한다(계정 귀속).
admin 은 운영 파악용으로 전체 조회·삭제 가능. 자격증명은 쓰기 전용(write-only):
입력 즉시 Fernet 암호화해 sns_account_secrets 에만 저장하고, 어떤 응답에도
재노출하지 않는다. read path 는 secret 테이블을 조회조차 하지 않는 전용 스키마
(MUST-FIX #3, NFR-S1). M2 에서 OAuth 콜백 경로가 이 위에 얹힌다.
"""
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from tortoise.transactions import in_transaction

from app import crypto
from app.api.deps import current_user, require_csrf
from app.models import AccountStatus, Platform, Role, SnsAccount, SnsAccountSecret, User

router = APIRouter(
    prefix="/api/sns-accounts", tags=["sns-accounts"],
    dependencies=[Depends(current_user)],
)


class SnsAccountOut(BaseModel):
    # 전용 read 스키마 — 여기 없는 필드는 구조적으로 응답에 실릴 수 없다.
    id: int
    user_id: int
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
        id=a.id, user_id=a.user_id, platform=a.platform, display_name=a.display_name,
        status=a.status, token_expires_at=a.token_expires_at,
    )


def _scope(user: User):
    """본인 것만. admin 은 전체(운영 파악·정리용)."""
    q = SnsAccount.all()
    return q if user.role == Role.admin else q.filter(user_id=user.id)


@router.get("")
async def list_accounts(user: Annotated[User, Depends(current_user)]) -> list[SnsAccountOut]:
    return [_out(a) for a in await _scope(user).order_by("id")]


@router.post("", status_code=201, dependencies=[Depends(require_csrf)])
async def create_account(
    body: SnsAccountIn, user: Annotated[User, Depends(current_user)]
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
        # 생성 주체에게 자동 귀속 — 타인 명의 계정 등록 경로는 없다.
        account = await SnsAccount.create(
            user=user, platform=body.platform, display_name=body.display_name
        )
        await SnsAccountSecret.create(account=account, encrypted_credentials=ciphertext)
    return _out(account)


@router.delete("/{account_id}", status_code=204, dependencies=[Depends(require_csrf)])
async def delete_account(
    account_id: int, user: Annotated[User, Depends(current_user)]
) -> None:
    # secret 은 FK cascade 로 함께 삭제된다. 타인 계정은 존재 여부도 노출하지 않는다(404).
    if not await _scope(user).filter(id=account_id).delete():
        raise HTTPException(status_code=404, detail="SNS 계정이 없습니다")
