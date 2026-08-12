"""/api/users — 사용자 관리 (admin 전용, API-SPEC §사용자 관리).

지금까지 계정 생성 경로는 `app/cli.py create-user` 뿐이라 운영자가 화면에서 검토자를
만들 수 없었다(이슈 #102). FE 제안 계약대로 구현하되, **삭제에는 가드를 건다**.

삭제가 위험한 이유(2026-08-12 마이그레이션 실측): `sources`·`keywords`·`templates`·
`sns_accounts` 4개 테이블의 `user_id` 가 **ON DELETE CASCADE** 다. 즉 사용자 한 명을
지우면 그 사람이 만든 **소스·키워드·템플릿·연동 계정이 조용히 함께 사라진다** — 수집이
멈추고 그 이유가 어디에도 남지 않는다. 그래서 소유 리소스가 하나라도 있으면 409 로
막고, 무엇을 먼저 정리해야 하는지 알려준다.

`reply_actions.reviewer_id` 는 RESTRICT(마이그레이션 3_)라 DB 가 이미 막지만, 그대로
두면 IntegrityError → 500 이 된다. 여기서 먼저 검사해 409 + 설명으로 돌려준다.

**결국 삭제는 "아무것도 안 한 계정" 에만 열린다** — 잘못 만든 계정 회수용이다.
퇴사자 계정 회수처럼 이력이 있는 사용자를 막으려면 소프트삭제(비활성화)가 필요하고,
그건 컬럼 추가가 있는 별건이다(PRD §사용자 관리 주석).
"""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from app.api.deps import current_user, require_admin, require_csrf
from app.auth import hash_password
from app.models import (
    Keyword,
    MatchedPost,
    PostStatus,
    ReplyActionLog,
    ReplyTemplate,
    Role,
    SnsAccount,
    Source,
    User,
)

router = APIRouter(
    prefix="/api/users", tags=["users"], dependencies=[Depends(require_admin)]
)


class UserOut(BaseModel):
    # 전용 read 스키마 — password_hash 는 여기 없으므로 구조적으로 응답에 실릴 수 없다.
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    role: Role
    created_at: datetime


class UserIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=255)
    # 8자 하한은 FE 와 같은 기준. 상한은 해시 비용 폭주 방지용 — argon2 는 입력 길이에
    # 크게 영향받지 않지만 무제한 입력을 해시 함수에 그대로 넘기지 않는다.
    password: str = Field(min_length=8, max_length=200)
    role: Role

    @field_validator("email")
    @classmethod
    def _looks_like_email(cls, v: str) -> str:
        # 로그인 식별자라 오타는 곧 못 쓰는 계정이다. 다만 정규식 검증은 하지 않는다 —
        # 로그인(`api/auth.py`)이 이메일을 평문 문자열로 정확히 대조하므로 여기서만
        # 엄격해지면 "만들 수는 있는데 못 들어오는" 계정이 생긴다. 공백만 정리한다.
        v = v.strip()
        if "@" not in v or " " in v:
            raise ValueError("이메일 형식이 아닙니다")
        return v


class UserPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 계약상 역할만 바꾼다. 비밀번호 재설정은 별건(본인 변경 플로우가 먼저다).
    role: Role


async def _lock_admins() -> list[User]:
    """admin 행 전체를 id 순으로 잠그고 반환한다 — "마지막 admin" 판정의 TOCTOU 차단.

    잠그지 않으면 admin 2명을 동시에 강등/삭제할 때 양쪽 다 "나 말고 또 있다" 를 보고
    통과해 **admin 0명(영구 잠김)** 이 된다. 항상 같은 순서(id)로 잠가 데드락도 없다.
    호출자는 트랜잭션 안에서 쓴다.
    """
    return await User.filter(role=Role.admin).order_by("id").select_for_update()


@router.get("")
async def list_users() -> list[UserOut]:
    return [UserOut.model_validate(u) for u in await User.all().order_by("id")]


@router.post("", status_code=201, dependencies=[Depends(require_csrf)])
async def create_user(body: UserIn) -> UserOut:
    try:
        user = await User.create(
            email=body.email, password_hash=hash_password(body.password), role=body.role
        )
    except IntegrityError as exc:
        # email unique — 입력값을 그대로 echo 하지 않는다(검증 에러 no-echo 원칙)
        raise HTTPException(
            status_code=409, detail="이미 등록된 이메일입니다"
        ) from exc
    return UserOut.model_validate(user)


@router.patch("/{user_id}", dependencies=[Depends(require_csrf)])
async def update_user(
    user_id: int, body: UserPatch, admin: Annotated[User, Depends(current_user)]
) -> UserOut:
    """역할 변경. **마지막 admin 의 강등은 409** — 아무도 관리 화면에 못 들어가는 상태 방지.

    자기 자신의 강등은 (다른 admin 이 있으면) 허용한다. 세션에는 uid 만 들어 있고 역할은
    매 요청 DB 에서 읽으므로, 강등은 다음 요청부터 즉시 적용된다.
    """
    async with in_transaction():
        target = await User.filter(id=user_id).select_for_update().first()
        if target is None:
            raise HTTPException(status_code=404, detail="사용자가 없습니다")
        if target.role == Role.admin and body.role != Role.admin:
            admins = await _lock_admins()
            if len(admins) <= 1:
                raise HTTPException(
                    status_code=409,
                    detail="마지막 admin 은 강등할 수 없습니다"
                    " — 다른 사용자를 admin 으로 올린 뒤 다시 시도해 주세요",
                )
        if target.role != body.role:
            target.role = body.role
            await target.save(update_fields=["role"])
    return UserOut.model_validate(target)


async def _blocking_references(user_id: int) -> str | None:
    """삭제를 막아야 하는 참조를 한 문장으로 요약한다. 없으면 None.

    소유 리소스(cascade 로 함께 지워질 것들)와 이력(RESTRICT/조정 참조)을 모두 본다.
    """
    owned = {
        "소스": await Source.filter(user_id=user_id).count(),
        "키워드": await Keyword.filter(user_id=user_id).count(),
        "템플릿": await ReplyTemplate.filter(user_id=user_id).count(),
        "SNS 계정": await SnsAccount.filter(user_id=user_id).count(),
    }
    present = [f"{name} {n}건" for name, n in owned.items() if n]
    if present:
        return (
            f"이 사용자가 소유한 리소스가 있어 삭제할 수 없습니다({', '.join(present)})"
            " — 삭제하면 함께 사라집니다. 먼저 정리하거나 다른 계정으로 옮겨 주세요"
        )
    if await ReplyActionLog.filter(reviewer_id=user_id).exists():
        return (
            "승인/처리 이력이 있는 사용자는 삭제할 수 없습니다"
            " — 감사 로그는 보존됩니다(역할을 reviewer 로 낮춰 사용을 중단시켜 주세요)"
        )
    # 조정 대기(verify_pending) 매칭의 승인자 — verify_meta.reviewer_id 는 FK 가 아니라
    # DB 가 못 막는다. 지우면 조정 잡이 승계 이력을 쓸 때마다 FK 위반으로 실패해 그
    # 매칭이 영구히 verify_pending 에 갇힌다(모델 주석 R-5 가 경고하던 바로 그 경로).
    pending = await MatchedPost.filter(status=PostStatus.verify_pending).only(
        "id", "verify_meta"
    )
    stuck = [p.id for p in pending if (p.verify_meta or {}).get("reviewer_id") == user_id]
    if stuck:
        ids = ", ".join(str(i) for i in sorted(stuck))
        return (
            f"전송 결과 조정 중인 매칭의 승인자입니다(매칭 {ids})"
            " — 조정이 끝난 뒤 삭제해 주세요"
        )
    return None


@router.delete("/{user_id}", status_code=204, dependencies=[Depends(require_csrf)])
async def delete_user(
    user_id: int, admin: Annotated[User, Depends(current_user)]
) -> None:
    """사용자 삭제 — 소유 리소스·이력이 없는 계정만(모듈 docstring 참조).

    본인 삭제와 마지막 admin 삭제는 별도로 막는다: 전자는 실수의 대가가 로그아웃이고,
    후자는 아무도 못 들어오는 잠김 상태다.
    """
    if user_id == admin.id:
        raise HTTPException(status_code=409, detail="자기 자신은 삭제할 수 없습니다")
    async with in_transaction():
        # 대상 행 잠금 — FK 자식 INSERT 는 부모에 FOR KEY SHARE 를 잡으므로, 여기서
        # FOR UPDATE 를 쥐면 "검사 통과 후 소스가 생겨서 cascade 로 사라지는" 창이 닫힌다
        # (`sources.py` 삭제와 같은 기법).
        target = await User.filter(id=user_id).select_for_update().first()
        if target is None:
            raise HTTPException(status_code=404, detail="사용자가 없습니다")
        if target.role == Role.admin and len(await _lock_admins()) <= 1:
            raise HTTPException(
                status_code=409,
                detail="마지막 admin 은 삭제할 수 없습니다"
                " — 다른 사용자를 admin 으로 올린 뒤 다시 시도해 주세요",
            )
        blocking = await _blocking_references(user_id)
        if blocking:
            raise HTTPException(status_code=409, detail=blocking)
        try:
            await User.filter(id=user_id).delete()
        except IntegrityError as exc:
            # 백스톱: 위 검사들이 놓친 RESTRICT 참조(동시 생성 등). 부분 삭제는 롤백된다.
            raise HTTPException(
                status_code=409,
                detail="이 사용자를 참조하는 이력이 있어 삭제할 수 없습니다",
            ) from exc
