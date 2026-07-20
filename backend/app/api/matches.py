"""/api/matches — 매칭 글 조회 (reviewer/admin, API-SPEC §매칭).

approve/ignore/retry 상태변경은 승인 플로우 PR 에서 추가한다.
"""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from app.api.deps import current_user
from app.models import MatchedPost, PostStatus, ReplyActionLog

router = APIRouter(
    prefix="/api/matches", tags=["matches"], dependencies=[Depends(current_user)]
)


class MatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    external_post_id: str
    author: str | None
    url: str | None
    content: str
    matched_keyword_id: int | None
    published_at: datetime | None
    matched_at: datetime
    status: PostStatus


class MatchListOut(BaseModel):
    items: list[MatchOut]
    total: int


class ReplyActionOut(BaseModel):
    id: int
    action: str
    reviewer_user_id: int
    template_id: int | None
    external_reply_id: str | None
    error: str | None
    created_at: datetime


class MatchDetailOut(MatchOut):
    reply_actions: list[ReplyActionOut]


@router.get("")
async def list_matches(
    status: Annotated[PostStatus | None, Query()] = None,
    source_id: Annotated[int | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> MatchListOut:
    q = MatchedPost.all()
    if status is not None:
        q = q.filter(status=status)
    if source_id is not None:
        q = q.filter(source_id=source_id)
    total = await q.count()
    items = await q.order_by("-matched_at", "-id").offset((page - 1) * size).limit(size)
    out = []
    for m in items:
        row = MatchOut.model_validate(m)
        # 목록은 요약만(최대 500자) — 전체 본문은 상세 API 에서. (응답 크기 상한)
        if len(row.content) > 500:
            row = row.model_copy(update={"content": row.content[:500]})
        out.append(row)
    return MatchListOut(items=out, total=total)


@router.get("/{match_id}")
async def get_match(match_id: int) -> MatchDetailOut:
    m = await MatchedPost.get_or_none(id=match_id)
    if m is None:
        raise HTTPException(status_code=404, detail="매칭 글이 없습니다")
    actions = await ReplyActionLog.filter(matched_post_id=match_id).order_by("id")
    return MatchDetailOut(
        **MatchOut.model_validate(m).model_dump(),
        reply_actions=[
            ReplyActionOut(
                id=a.id, action=a.action.value, reviewer_user_id=a.reviewer_id,
                template_id=a.template_id, external_reply_id=a.external_reply_id,
                error=a.error, created_at=a.created_at,
            )
            for a in actions
        ],
    )
