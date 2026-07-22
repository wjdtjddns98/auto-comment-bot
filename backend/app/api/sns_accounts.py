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
from pydantic import BaseModel, ConfigDict, Field
from tortoise.exceptions import IntegrityError
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


class CredentialsIn(BaseModel):
    # extra 금지: platform 등 미지 키를 조용히 무시하지 않는다(교체는 자격증명만 받는다)
    model_config = ConfigDict(extra="forbid")

    credentials: dict[str, Any]


def _validate_credentials(platform: Platform, credentials: dict[str, Any]) -> None:
    """플랫폼별 자격증명 형식 검증 — 등록 시점 422. 아무 JSON 이나 저장돼서 폴링/전송
    때에야 실패가 드러나는 상태 방지(소스 config 검증 #19 와 같은 원칙).
    주의: detail 에 입력값을 절대 echo 하지 않는다(불변식 ③)."""
    if platform == Platform.threads:
        token = credentials.get("access_token")
        if not isinstance(token, str) or not token.strip():
            raise HTTPException(
                status_code=422,
                detail="credentials.access_token: threads 계정은 액세스 토큰(문자열)이 필요합니다",
            )
    # naver/community: 어댑터 미구현 — 스키마 확정 시(M3) 여기에 추가한다.


def _out(a: SnsAccount) -> SnsAccountOut:
    return SnsAccountOut(
        id=a.id, user_id=a.user_id, platform=a.platform, display_name=a.display_name,
        status=a.status, token_expires_at=a.token_expires_at,
    )


def visible_accounts(user: User):
    """본인 것만. admin 은 전체(운영 파악·정리용)."""
    q = SnsAccount.all()
    return q if user.role == Role.admin else q.filter(user_id=user.id)


@router.get("")
async def list_accounts(user: Annotated[User, Depends(current_user)]) -> list[SnsAccountOut]:
    return [_out(a) for a in await visible_accounts(user).order_by("id")]


@router.post("", status_code=201, dependencies=[Depends(require_csrf)])
async def create_account(
    body: SnsAccountIn, user: Annotated[User, Depends(current_user)]
) -> SnsAccountOut:
    _validate_credentials(body.platform, body.credentials)
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


@router.put(
    "/{account_id}/credentials", status_code=204, dependencies=[Depends(require_csrf)]
)
async def replace_credentials(
    account_id: int, body: CredentialsIn, user: Annotated[User, Depends(current_user)]
) -> None:
    """토큰 교체 — 삭제→재등록→소스 재연결 루프 제거(M2 백로그 ①).

    등록과 동일한 형식 검증(422)·암호화 실패 처리(503). 교체 성공 시 만료/회수 상태를
    active 로 복구한다 — 새 토큰이 유효하지 않으면 이후 수집/전송 시점에 다시 상태
    회계가 이뤄지므로 이전 상태를 남길 이유가 없다. platform 은 저장된 계정 값을 쓴다
    (경로로 platform 전환 불가)."""
    account = await visible_accounts(user).filter(id=account_id).first()
    if account is None:
        # 타인 계정은 존재 여부도 노출하지 않는다(404) — delete 와 동일 정책
        raise HTTPException(status_code=404, detail="SNS 계정이 없습니다")
    _validate_credentials(account.platform, body.credentials)
    try:
        ciphertext = crypto.encrypt_credentials(body.credentials)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="자격증명 암호화 키가 설정되지 않았거나 형식이 잘못되었습니다 — 서버 설정 필요",
        ) from exc
    try:
        async with in_transaction():
            # 락 순서 정렬(3차 독립 리뷰 중요): 부모(sns_accounts) 행을 먼저 잠근다 —
            # DELETE(부모 삭제 → cascade 자식)와 같은 parent→child 순서. 반대 순서
            # (secret 먼저)는 동시 삭제와 AB-BA 데드락(40P01)이 가능하고, 데드락 예외는
            # IntegrityError 로 변환되지 않아 500 이 된다(asyncpg 예외 계층 실확인).
            locked = await SnsAccount.filter(id=account.id).select_for_update().first()
            if locked is None:
                raise HTTPException(status_code=404, detail="SNS 계정이 없습니다")
            updated = await SnsAccountSecret.filter(account_id=locked.id).update(
                encrypted_credentials=ciphertext
            )
            if not updated:
                # 등록 경로는 secret 을 항상 만들지만, 결손 데이터도 교체가 복구한다(upsert)
                await SnsAccountSecret.create(
                    account=locked, encrypted_credentials=ciphertext
                )
            locked.status = AccountStatus.active
            locked.token_expires_at = None
            await locked.save(update_fields=["status", "token_expires_at"])
    except IntegrityError as exc:
        # 동시 교체 경합의 잔여 창(부모 락 이후의 unique/FK 충돌) — 롤백으로 부분 반영 없음
        raise HTTPException(
            status_code=409, detail="계정 상태가 변경되었습니다 — 다시 시도해 주세요"
        ) from exc


@router.delete("/{account_id}", status_code=204, dependencies=[Depends(require_csrf)])
async def delete_account(
    account_id: int, user: Annotated[User, Depends(current_user)]
) -> None:
    # secret 은 FK cascade 로 함께 삭제된다. 타인 계정은 존재 여부도 노출하지 않는다(404).
    if not await visible_accounts(user).filter(id=account_id).delete():
        raise HTTPException(status_code=404, detail="SNS 계정이 없습니다")
