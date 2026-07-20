"""/api/matches — 매칭 글 조회 + 승인/전송 플로우 (reviewer/admin, API-SPEC §매칭).

approve 는 이 제품의 유일한 전송 경로다(불변식 ①). 이중 발송은 CAS 클레임 +
reply_actions partial unique(action='sent')로 DB가 구조적으로 막는다(불변식 ②).
"""
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, field_validator
from tortoise.exceptions import IntegrityError

from app.api.deps import current_user, require_csrf
from app.models import (
    MatchedPost,
    PostStatus,
    ReplyAction,
    ReplyActionLog,
    ReplyTemplate,
    Role,
    SnsAccount,
    User,
)
from app.sources import get_adapter

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


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: int | None = None
    final_body: str
    sns_account_id: int | None = None

    @field_validator("final_body")
    @classmethod
    def _non_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("답변 본문이 비어 있습니다")
        return v


class ApproveOut(BaseModel):
    action: str
    external_reply_id: str | None = None
    clipboard_body: str | None = None


async def _resolve_refs(
    body: ApproveIn, user: User
) -> tuple[ReplyTemplate | None, SnsAccount | None]:
    """template/sns_account 참조 검증 — CAS 클레임 전에 실패시켜 상태를 건드리지 않는다."""
    template = None
    if body.template_id is not None:
        template = await ReplyTemplate.get_or_none(id=body.template_id, enabled=True)
        if template is None:
            raise HTTPException(status_code=422, detail="템플릿이 없거나 비활성화되어 있습니다")
    account = None
    if body.sns_account_id is not None:
        q = SnsAccount.filter(id=body.sns_account_id)
        if user.role != Role.admin:
            # 셀프서비스 정책과 동일: 타인 계정으로는 전송 불가(존재 여부도 비노출)
            q = q.filter(user_id=user.id)
        account = await q.first()
        if account is None:
            raise HTTPException(status_code=422, detail="SNS 계정을 찾을 수 없습니다")
    return template, account


async def _approve(
    match_id: int, body: ApproveIn, user: User, claim_from: tuple[PostStatus, ...]
) -> ApproveOut | JSONResponse:
    template, account = await _resolve_refs(body, user)

    # CAS 클레임(불변식 ② 1단계): 원자적 조건부 UPDATE 로 선점 — 0행이면 abort(MUST-FIX #1)
    claimed = await MatchedPost.filter(id=match_id, status__in=list(claim_from)).update(
        status=PostStatus.sending, sending_claimed_at=datetime.now(UTC)
    )
    if not claimed:
        if not await MatchedPost.exists(id=match_id):
            raise HTTPException(status_code=404, detail="매칭 글이 없습니다")
        raise HTTPException(status_code=409, detail="이미 처리 중이거나 완료된 매칭입니다")

    post = await MatchedPost.get(id=match_id)
    source = await post.source
    adapter = get_adapter(source.type)

    if adapter is None or not adapter.can_write:
        # 전송 불가 소스(네이버/커뮤니티): 전송하지 않는다 — approved 기록 + 수동 복사용 본문
        await ReplyActionLog.create(
            matched_post=post, reviewer=user, template=template, sns_account=account,
            final_body=body.final_body, action=ReplyAction.approved,
        )
        await MatchedPost.filter(id=match_id).update(status=PostStatus.replied)
        return ApproveOut(action="approved", clipboard_body=body.final_body)

    try:
        external_reply_id = await adapter.send_reply(source, post, body.final_body, account)
    except Exception as exc:  # noqa: BLE001 - 어떤 실패든 failed 회계 + reviewing 복귀
        await ReplyActionLog.create(
            matched_post=post, reviewer=user, template=template, sns_account=account,
            final_body=body.final_body, action=ReplyAction.failed, error=str(exc)[:1000],
        )
        await MatchedPost.filter(id=match_id).update(status=PostStatus.reviewing)
        # 에러 원문은 audit 로그에만 — 응답에는 어댑터 내부 사정을 노출하지 않는다
        return JSONResponse(
            status_code=502,
            content={"action": "failed", "detail": "답변 전송에 실패했습니다 — 재시도할 수 있습니다"},
        )

    try:
        await ReplyActionLog.create(
            matched_post=post, reviewer=user, template=template, sns_account=account,
            final_body=body.final_body, action=ReplyAction.sent,
            external_reply_id=external_reply_id[:512],
        )
    except IntegrityError:
        # partial unique(action='sent') 발화 — DB 가 이중 sent 를 구조적으로 차단(불변식 ②).
        # sent 기록이 이미 있다 = 실제로는 답변 완료 → replied 로 정합 회복 후 409.
        await MatchedPost.filter(id=match_id).update(status=PostStatus.replied)
        raise HTTPException(status_code=409, detail="이미 전송된 매칭입니다") from None
    await MatchedPost.filter(id=match_id).update(status=PostStatus.replied)
    return ApproveOut(action="sent", external_reply_id=external_reply_id[:512])


@router.post("/{match_id}/approve", dependencies=[Depends(require_csrf)])
async def approve_match(
    match_id: int, body: ApproveIn, user: Annotated[User, Depends(current_user)]
) -> ApproveOut:
    return await _approve(match_id, body, user, (PostStatus.new, PostStatus.reviewing))


@router.post("/{match_id}/retry", dependencies=[Depends(require_csrf)])
async def retry_match(
    match_id: int, body: ApproveIn, user: Annotated[User, Depends(current_user)]
) -> ApproveOut:
    # FR-13: 전송 실패건 수동 재시도 — reviewing 에서만, approve 와 동일 CAS 경로
    return await _approve(match_id, body, user, (PostStatus.reviewing,))


@router.post("/{match_id}/ignore", dependencies=[Depends(require_csrf)])
async def ignore_match(match_id: int) -> dict:
    changed = await MatchedPost.filter(
        id=match_id, status__in=[PostStatus.new, PostStatus.reviewing]
    ).update(status=PostStatus.ignored)
    if not changed:
        if not await MatchedPost.exists(id=match_id):
            raise HTTPException(status_code=404, detail="매칭 글이 없습니다")
        raise HTTPException(status_code=409, detail="처리 중이거나 완료된 매칭은 무시할 수 없습니다")
    return {"status": "ignored"}
