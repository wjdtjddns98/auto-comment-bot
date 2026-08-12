"""/api/reply-actions — 감사 로그 전역 조회 (reviewer/admin, API-SPEC §감사 로그).

append-only 이력의 **읽기 전용** 창구다. 쓰기 경로는 여기 없다 — 행은 approve/retry/
ignore 가 상태 전이와 같은 트랜잭션에서만 만든다(`matches.py`). 그래야 "이력 없는
상태 변화" 창이 생기지 않는다.

매칭 상세에 실리는 `ReplyActionOut`(`matches.py`)과 달리 `matched_post_id` 를 포함한다 —
전역 목록은 각 행에서 어느 매칭인지 되짚어야 하기 때문(이슈 #101).

`final_body`·`sns_account_id` 는 싣지 않는다: 전자는 목록 응답을 수십 KB 로 부풀리고,
후자는 계정 셀프서비스 경계(본인 것만 보임)를 감사 로그가 우회하는 통로가 된다.
"""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.deps import current_user
from app.models import ReplyActionLog

router = APIRouter(
    prefix="/api/reply-actions", tags=["reply-actions"],
    dependencies=[Depends(current_user)],
)

# 무한 성장하는 append-only 테이블이라 전량 반환하지 않는다. 화면은 최신부터 보므로
# 기본 200건이면 감사 용도로 충분하고, 더 필요하면 limit 으로 올린다(상한 500).
DEFAULT_LIMIT = 200
MAX_LIMIT = 500


class ReplyActionLogOut(BaseModel):
    id: int
    matched_post_id: int
    reviewer_user_id: int
    action: str
    template_id: int | None
    external_reply_id: str | None
    error: str | None
    created_at: datetime


@router.get("")
async def list_reply_actions(
    match_id: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> list[ReplyActionLogOut]:
    q = ReplyActionLog.all()
    if match_id is not None:
        q = q.filter(matched_post_id=match_id)
    # 최신순 — 같은 트랜잭션에서 만들어진 동시각 행은 id 로 결정적으로 정렬한다.
    rows = await q.order_by("-created_at", "-id").limit(limit)
    return [
        ReplyActionLogOut(
            id=a.id, matched_post_id=a.matched_post_id, reviewer_user_id=a.reviewer_id,
            action=a.action.value, template_id=a.template_id,
            external_reply_id=a.external_reply_id, error=a.error, created_at=a.created_at,
        )
        for a in rows
    ]
