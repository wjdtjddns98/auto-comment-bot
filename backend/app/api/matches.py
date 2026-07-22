"""/api/matches — 매칭 글 조회 + 승인/전송 플로우 (reviewer/admin, API-SPEC §매칭).

approve 는 이 제품의 유일한 전송 경로다(불변식 ①). 이중 발송은 CAS 클레임 +
reply_actions partial unique(action='sent')로 DB가 구조적으로 막는다(불변식 ②).
"""
import asyncio
import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from app import reply as reply_flow
from app.api.deps import current_user, require_csrf
from app.api.sns_accounts import visible_accounts
from app.models import (
    AccountStatus,
    MatchedPost,
    Platform,
    PostStatus,
    ReplyAction,
    ReplyActionLog,
    ReplyTemplate,
    SnsAccount,
    Source,
    SourceType,
    User,
)
from app.sources import SendError, SendOutcomeUnknown, get_adapter

logger = logging.getLogger(__name__)

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
    # 상한 2000자: 외부 플랫폼으로 나가는 값 — 어댑터별 실제 상한은 M2 에서 조정
    final_body: str = Field(max_length=2000)
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


# 계정 플랫폼 ↔ 소스 타입 대응 — 어긋난 계정으로의 승인을 클레임 전에 거른다
_PLATFORM_FOR_SOURCE = {
    SourceType.threads: Platform.threads,
    SourceType.naver_cafe: Platform.naver,
    SourceType.community: Platform.community,
}


async def _resolve_refs(
    body: ApproveIn, user: User, source: Source
) -> tuple[ReplyTemplate | None, SnsAccount | None]:
    """template/sns_account 참조 검증 — CAS 클레임 전에 실패시켜 상태를 건드리지 않는다."""
    template = None
    if body.template_id is not None:
        template = await ReplyTemplate.get_or_none(id=body.template_id, enabled=True)
        if template is None:
            raise HTTPException(status_code=422, detail="템플릿이 없거나 비활성화되어 있습니다")
    account = None
    if body.sns_account_id is not None:
        # 셀프서비스 정책 공유: 타인 계정으로는 전송 불가(존재 여부도 비노출)
        account = await visible_accounts(user).filter(id=body.sns_account_id).first()
        if account is None:
            raise HTTPException(status_code=422, detail="SNS 계정을 찾을 수 없습니다")
        if account.status != AccountStatus.active:
            raise HTTPException(status_code=422, detail="만료/회수된 SNS 계정입니다")
        if account.platform != _PLATFORM_FOR_SOURCE.get(source.type):
            raise HTTPException(status_code=422, detail="소스 타입과 SNS 계정 플랫폼이 다릅니다")
    return template, account


def _safe_error(exc: Exception, match_id: int) -> str:
    """audit 저장용 에러 텍스트 — 예기치 못한 예외 원문은 자격증명 포함 가능성이 있어
    응답·DB 로 내보내지 않는다(불변식 ③). 원문은 서버 로그에만."""
    if isinstance(exc, (SendError, SendOutcomeUnknown)):
        # 두 예외의 메시지는 어댑터가 통제하는 안전한 텍스트만 담는다는 계약
        return str(exc)[:1000]
    if isinstance(exc, TimeoutError):
        return f"전송 타임아웃({reply_flow.SEND_TIMEOUT_SEC}초 초과)"
    logger.exception("send_reply 예기치 못한 실패 match=%s", match_id)
    return f"내부 오류: {type(exc).__name__}"


async def _approve(
    match_id: int, body: ApproveIn, user: User, claim_from: tuple[PostStatus, ...]
) -> ApproveOut | JSONResponse:
    post = await MatchedPost.get_or_none(id=match_id)
    if post is None:
        raise HTTPException(status_code=404, detail="매칭 글이 없습니다")
    source = await post.source
    template, account = await _resolve_refs(body, user, source)

    adapter = get_adapter(source.type)
    will_send = adapter is not None and adapter.can_write

    if will_send and account is None:
        # 전송 소스는 계정 없이는 전송도 (결과 불명 시) 조정도 불가 — 클레임 전에 거른다
        # (1차 적대 리뷰 F-4: account 없는 unknown 은 판정 키가 없어 영구 limbo 가 된다).
        raise HTTPException(status_code=422, detail="전송 가능한 소스는 SNS 계정 선택이 필요합니다")
    if will_send and await ReplyActionLog.exists(
        matched_post_id=match_id, action=ReplyAction.sent
    ):
        # DB 에 전송 증거(sent)가 있으면 상태가 무엇이든 재전송을 열지 않는다(불변식 ②,
        # 1차 적대 리뷰 F-2) — 상태만 replied 로 정합 회복. partial unique 의 사전 방어층.
        await MatchedPost.filter(id=match_id, status__in=list(claim_from)).update(
            status=PostStatus.replied, verify_meta=None
        )
        raise HTTPException(status_code=409, detail="이미 전송된 매칭입니다")

    # CAS 클레임(불변식 ② 1단계): 원자적 조건부 UPDATE 로 선점 — 0행이면 abort(MUST-FIX #1).
    # claim_ts 는 펜싱 토큰을 겸한다: 이후 모든 상태 갱신은 "내 클레임이 아직 유효할 때만".
    # 전송 경로면 조정 재료(verify_meta)를 클레임과 같은 UPDATE 로 기록한다 — 전송 중
    # 크래시(sweep 회수분)에도 조정 잡이 판정할 재료가 남는다(M2 조정 설계 §3.1).
    claim_ts = datetime.now(UTC)
    verify_meta = None
    if will_send:
        verify_meta = {
            "target_media_id": post.external_post_id,
            "claim_ts": claim_ts.isoformat(),
            "attempts": 0,
            "reviewer_id": user.id,
            "final_body": body.final_body,
            "sns_account_id": account.id if account else None,
        }
    claimed = await MatchedPost.filter(id=match_id, status__in=list(claim_from)).update(
        status=PostStatus.sending, sending_claimed_at=claim_ts, verify_meta=verify_meta
    )
    if not claimed:
        if not await MatchedPost.exists(id=match_id):
            raise HTTPException(status_code=404, detail="매칭 글이 없습니다")
        raise HTTPException(status_code=409, detail="이미 처리 중이거나 완료된 매칭입니다")

    def _fenced():
        # 펜싱(1차 리뷰 C1): sweep 회수 후 사람이 바꾼 상태(ignored 등)를 좀비 요청이
        # 덮어쓰지 못하게, 상태 갱신은 클레임 소유권이 유지된 경우에만 적용된다.
        return MatchedPost.filter(
            id=match_id, status=PostStatus.sending, sending_claimed_at=claim_ts
        )

    def _log(**kwargs):
        return ReplyActionLog.create(
            matched_post=post, reviewer=user, template=template, sns_account=account,
            final_body=body.final_body, **kwargs,
        )

    if not will_send:
        # 전송 불가 소스(네이버/커뮤니티): 전송하지 않는다 — approved 기록 + 수동 복사용 본문.
        # 로그와 상태 전이는 한 트랜잭션 — 로그만 남고 sending 고착되는 창 제거(1차 리뷰 #6)
        async with in_transaction():
            await _log(action=ReplyAction.approved)
            fenced = await _fenced().update(status=PostStatus.replied)
        if not fenced:
            logger.warning("approved 기록 시점에 클레임 소유권 상실 — 상태 미변경 match=%s", match_id)
        return ApproveOut(action="approved", clipboard_body=body.final_body)

    try:
        # 타임아웃 강제(1차 리뷰 C2): 초과 시 코루틴이 취소돼 전송 시도 자체가 중단된다 —
        # sweep(SENDING_STALE_SEC)이 살아있는 전송을 회수하는 경우가 없도록 훨씬 짧게.
        external_reply_id = await asyncio.wait_for(
            adapter.send_reply(source, post, body.final_body, account),
            timeout=reply_flow.SEND_TIMEOUT_SEC,
        )
    except SendError as exc:
        # 확정 실패 — 어댑터가 "게시 안 됐음"을 보장한 경우만 retry 를 다시 연다(§3.2 계약).
        error_text = _safe_error(exc, match_id)
        async with in_transaction():
            await _log(action=ReplyAction.failed, error=error_text)
            fenced = await _fenced().update(status=PostStatus.reviewing, verify_meta=None)
        if not fenced:
            logger.warning("failed 회계 시점에 클레임 소유권 상실 — 상태 미변경 match=%s", match_id)
        return JSONResponse(
            status_code=502,
            content={"action": "failed", "detail": "답변 전송에 실패했습니다 — 재시도할 수 있습니다"},
        )
    except Exception as exc:  # noqa: BLE001 - SendError 외 전부 결과 불명(fail-safe 기본값)
        # 결과 불명(M2 조정 설계 §3.3): SendOutcomeUnknown/타임아웃뿐 아니라 정체불명 예외
        # (어댑터 응답 파싱 버그 등)도 publish 요청이 나간 뒤일 수 있다 — reviewing 복귀
        # (=retry 개방) 대신 verify_pending 으로 펜싱 유지, 실제 게시 여부는 조정 잡이 확인한
        # 뒤에만 retry 가 열린다(불변식 ② — 1차 적대 리뷰 F-1: 기본값이 fail-open 이면 이 PR
        # 이 닫으려는 창이 그대로 열린다). 타임아웃 취소는 외부 사이드이펙트를 중단시키지
        # 않으므로 동일 취급(2차 리뷰 H-1).
        error_text = _safe_error(exc, match_id)
        if isinstance(exc, SendOutcomeUnknown) and exc.container_id:
            verify_meta["container_id"] = exc.container_id
        # 판정 키 스냅샷(토큰 교체 API 3차 독립 리뷰 blocker): 이 시도를 수행한 계정의
        # username 을 결과 불명 시점에 고정한다 — 이후 자격증명 교체(신원 교체)가 조정
        # 판정을 오염시켜 "실제 게시됨"을 미게시로 오판(→retry→이중 게시)하는 경로 차단.
        # send_reply 는 publish 전에 platform_username 을 갱신·저장하므로(2차 리뷰 중요-1)
        # 이 in-memory 값이 곧 이번 시도의 게시 신원이다. 갱신 전에 취소/실패했다면
        # 게시 자체가 없었으므로 이전 값이어도 판정 결과(미발견→미게시)가 옳다.
        if account is not None and account.platform_username:
            verify_meta["platform_username"] = account.platform_username
        async with in_transaction():
            await _log(action=ReplyAction.unknown, error=error_text)
            fenced = await _fenced().update(
                status=PostStatus.verify_pending, verify_meta=verify_meta
            )
        if not fenced:
            logger.warning("unknown 회계 시점에 클레임 소유권 상실 — 상태 미변경 match=%s", match_id)
        return JSONResponse(
            status_code=502,
            content={
                "action": "unknown",
                "detail": "전송 결과 확인 중 — 자동 조정 후 재시도 가능해집니다",
            },
        )

    try:
        async with in_transaction():
            await _log(
                action=ReplyAction.sent, external_reply_id=external_reply_id[:512]
            )
            fenced = await _fenced().update(status=PostStatus.replied, verify_meta=None)
    except IntegrityError:
        # partial unique(action='sent') 충돌로 추정 — 실제 sent 존재를 확인한 경우에만
        # replied 로 정합 회복(그 외 원인의 IntegrityError 를 삼키지 않는다, 1차 리뷰 #7)
        if await ReplyActionLog.exists(matched_post_id=match_id, action=ReplyAction.sent):
            await _fenced().update(status=PostStatus.replied, verify_meta=None)
            raise HTTPException(status_code=409, detail="이미 전송된 매칭입니다") from None
        raise
    if not fenced:
        # 전송은 실제로 일어났으므로 audit(sent 행)은 남긴다. 상태는 그 사이 사람이 내린
        # 결정(ignored 등)을 존중해 덮어쓰지 않는다.
        logger.warning("sent 기록 시점에 클레임 소유권 상실 — 상태 미변경 match=%s", match_id)
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
async def ignore_match(
    match_id: int, user: Annotated[User, Depends(current_user)]
) -> dict:
    # 상태 전이 + audit 을 한 트랜잭션으로 — "ignored 인데 기록 없음" 창 제거(2차 리뷰 M1)
    # verify_pending 도 허용: 전송이 없는 안전한 동작이라 조정 장기 실패 시 사람의 유일한
    # 탈출구다(M2 조정 설계 §3.1 — approve/retry 는 계속 409).
    async with in_transaction():
        changed = await MatchedPost.filter(
            id=match_id,
            status__in=[PostStatus.new, PostStatus.reviewing, PostStatus.verify_pending],
        ).update(status=PostStatus.ignored, verify_meta=None)
        if changed:
            # 무시도 사람의 의사결정 — 누가/언제 응답하지 않기로 했는지 audit 에 남긴다
            await ReplyActionLog.create(
                matched_post_id=match_id, reviewer=user, final_body="",
                action=ReplyAction.canceled,
            )
    if not changed:
        if not await MatchedPost.exists(id=match_id):
            raise HTTPException(status_code=404, detail="매칭 글이 없습니다")
        raise HTTPException(status_code=409, detail="처리 중이거나 완료된 매칭은 무시할 수 없습니다")
    return {"status": "ignored"}
