"""전송 결과 불명(verify_pending) 조정 잡 — docs/M2-SEND-RECONCILIATION.md §3.4.

publish 결과를 모르는 매칭에 대해 대상 글의 답글 목록을 read-only 로 조회해
실제 게시 여부를 판정한다. 게시 확인 → sent 회계 + replied, 미게시 판정 →
failed 회계 + reviewing 복귀(retry 개방). 조정 잡 자체는 어떤 전송도 하지 않는다
(불변식 ① — sent/failed 기록은 이미 사람이 승인한 전송의 사후 회계다).
"""
import logging
import unicodedata
from datetime import UTC, datetime, timedelta

from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from app import poller
from app.config import settings
from app.models import MatchedPost, PostStatus, ReplyAction, ReplyActionLog, SnsAccount
from app.sources import RateLimitedError, get_adapter
from app.sources.base import FetchedReply

logger = logging.getLogger(__name__)

# 판정 시각 여유 — 서버↔플랫폼 클럭 스큐 + 발행 처리 지연 합산 보수치(설계 §3.4-2)
CLOCK_SKEW_SEC = 60
# prefix 보조 판정 최소 길이 — 이보다 짧은 본문은 완전일치만(짧은 문구 오탐 방지)
PREFIX_MIN_LEN = 40


def normalize(text: str) -> str:
    """판정용 본문 정규화: NFC + 연속 공백(개행 포함) 축약 + 양끝 trim."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def is_our_reply(
    reply: FetchedReply, username: str, claim_ts: datetime, final_body: str
) -> bool:
    """"우리 답글" 판정(설계 §3.4-2). 오판 방향별 결과는 설계 §3.6 참조."""
    if reply.username != username:
        return False
    ts = reply.timestamp
    if ts.tzinfo is None:  # 어댑터가 naive 를 주면 UTC 로 간주(방어)
        ts = ts.replace(tzinfo=UTC)
    if ts < claim_ts - timedelta(seconds=CLOCK_SKEW_SEC):
        return False
    body_norm = normalize(final_body)
    reply_norm = normalize(reply.text)
    if reply_norm == body_norm:
        return True
    # 플랫폼 본문 변형(절단 등) 대비 prefix 보조 판정 — 40자 미만 본문은 완전일치만
    return (
        len(body_norm) >= PREFIX_MIN_LEN
        and len(reply_norm) >= PREFIX_MIN_LEN
        and reply_norm[:PREFIX_MIN_LEN] == body_norm[:PREFIX_MIN_LEN]
    )


async def reconcile_tick() -> None:
    """스케줄러 진입점 — verify_pending 전건 순차 조정 + 조정 실패 상태 집계(R-2)."""
    rows = await MatchedPost.filter(status=PostStatus.verify_pending).prefetch_related("source")
    fetch_failed: set[int] = set()
    fetch_ok: set[int] = set()
    for post in rows:
        try:
            outcome = await reconcile_post(post)
        except Exception:
            # 다음 행/다음 주기가 이어가면 된다 — 원문은 서버 로그에만(불변식 ③).
            logger.exception("조정 처리 실패 match=%s", post.id)
            continue
        if outcome == "fetch_failed":
            fetch_failed.add(post.source_id)
        elif outcome == "fetch_ok":
            fetch_ok.add(post.source_id)
    # 조정 전용 실패 상태 갱신(2차 리뷰 R-2) — poller 의 수집 성공이 health 를 ok 로
    # 되돌리지 않게 하는 플래그. 틱 단위 집계라 같은 소스에 성공/실패 행이 섞여도
    # 실패가 하나라도 있으면 유지된다. 조정 대상이 사라진 소스는 해제(영구 잔류 방지).
    active = {post.source_id for post in rows}
    poller._reconcile_failing &= active
    poller._reconcile_failing |= fetch_failed
    poller._reconcile_failing -= fetch_ok - fetch_failed


async def reconcile_post(post: MatchedPost) -> str | None:
    """행 1건 조정. 반환은 R-2 집계 재료: "fetch_ok"(조회 성공)·"fetch_failed"(조정 전용
    조회 실패)·None(조회 미도달/rate-limit — 공유 backoff 회계라 조정 전용이 아님)."""
    meta = post.verify_meta
    try:
        # 재료 검증은 조회 호출 전에 전부 — malformed meta 행이 매 틱 실 API 호출을
        # 소모하며 영구 반복되지 않게 한다(1차 적대 리뷰 F-6).
        target_media_id = meta["target_media_id"]
        reviewer_id = meta["reviewer_id"]
        claim_ts = datetime.fromisoformat(meta["claim_ts"])
        if not target_media_id or reviewer_id is None:
            raise ValueError("조정 재료 필드 비어 있음")
    except (TypeError, KeyError, ValueError):
        # 설계상 생기지 않는 행(전이 경로가 모두 verify_meta 를 남긴다) — 버그 신호.
        # 자동 복귀는 이중 게시 위험이 있어 하지 않는다. 사람 탈출구는 ignore(설계 §3.1).
        logger.error("verify_pending 인데 조정 재료 없음/손상 match=%s — ignore 로만 종결 가능", post.id)
        return
    source = post.source
    # 같은 틱의 앞선 행이나 poller 가 방금 건 backoff 를 stale 객체로 놓치지 않는다(2차 리뷰 R-1)
    await source.refresh_from_db()
    now = datetime.now(UTC)
    if source.backoff_until and source.backoff_until > now:
        return  # rate-limit 회계 공유 — backoff 중에는 조회하지 않는다(불변식 ④)
    adapter = get_adapter(source.type)
    fetch_replies = getattr(adapter, "fetch_replies", None)
    if fetch_replies is None:
        logger.error("조정 불가: 어댑터가 답글 조회 미지원 source=%s match=%s", source.id, post.id)
        return
    account = None
    if meta.get("sns_account_id") is not None:
        account = await SnsAccount.get_or_none(id=meta["sns_account_id"])
    username = account.platform_username if account else None
    if not username:
        # username 없이는 판정 불가(설계 §3.1) — 계정 삭제/미저장. 사람 탈출구는 ignore.
        logger.error("조정 불가: 판정용 platform_username 없음 match=%s", post.id)
        return

    since = claim_ts - timedelta(seconds=CLOCK_SKEW_SEC)
    try:
        replies = await fetch_replies(source, target_media_id, account, since)
    except RateLimitedError as exc:
        await source.refresh_from_db()  # 조회 대기 중의 회계 변경을 덮지 않게(2차 리뷰 R-1)
        await poller._record_failure(
            source, retry_after_sec=exc.retry_after_sec, rate_limited=True
        )
        return
    except Exception:
        # 401/403(토큰 만료) 포함 조회 실패 — attempts 미증가, 다음 주기 재시도.
        # read-only 라 반복해도 부작용 없음. 지속되면 사람이 ignore 로 종결(설계 §3.4-5).
        # health 회계로 소스 배지에 가시화한다(FR-18, 1차 적대 리뷰 F-3) — 원문은 로그에만.
        logger.exception("조정 조회 실패 match=%s source=%s", post.id, source.id)
        # 조회 대기 중 poller 가 건 backoff 를 stale 객체 회계(backoff_until=None 저장)로
        # 지우지 않는다(2차 리뷰 R-1) — 활성 backoff 가 있으면 health 회계를 생략한다.
        await source.refresh_from_db()
        if not (source.backoff_until and source.backoff_until > datetime.now(UTC)):
            await poller._record_failure(source, retry_after_sec=None, rate_limited=False)
        return "fetch_failed"

    final_body = meta.get("final_body") or ""
    found = next(
        (r for r in replies if is_our_reply(r, username, claim_ts, final_body)), None
    )
    if found is not None:
        await _settle_sent(post, meta, found)
        return "fetch_ok"

    attempts = int(meta.get("attempts", 0)) + 1
    if attempts < settings.reconcile_max_attempts:
        meta["attempts"] = attempts
        # CAS(verify_pending 유지 시에만) — 그 사이 사람이 ignore 했으면 건드리지 않는다
        await MatchedPost.filter(id=post.id, status=PostStatus.verify_pending).update(
            verify_meta=meta
        )
        return "fetch_ok"
    # 미게시 판정 전 최종 확인(1차 적대 리뷰 F-2): DB 에 전송 증거(sent 행 — 좀비 요청의
    # 사후 audit 등)가 있으면 조회 판정이 못 찾았어도 retry 를 열지 않는다(불변식 ②).
    if await ReplyActionLog.exists(matched_post_id=post.id, action=ReplyAction.sent):
        await MatchedPost.filter(id=post.id, status=PostStatus.verify_pending).update(
            status=PostStatus.replied, verify_meta=None
        )
        logger.warning("조정: sent 증거 발견 — 미게시 판정 대신 replied 정합 회복 match=%s", post.id)
        return "fetch_ok"
    # 미게시 판정(설계 §3.4-4): retry 재개. 오판 잔여 위험은 §3.6 — FE 문구가 최종 방어선.
    async with in_transaction():
        await ReplyActionLog.create(
            matched_post_id=post.id,
            reviewer_id=meta["reviewer_id"],
            final_body=final_body,
            action=ReplyAction.failed,
            sns_account_id=meta.get("sns_account_id"),
            error="조정 완료 — 미게시 판정. 재시도 전 대상 글에서 직접 확인을 권장합니다",
        )
        await MatchedPost.filter(id=post.id, status=PostStatus.verify_pending).update(
            status=PostStatus.reviewing, verify_meta=None
        )
    logger.info("조정 미게시 판정 match=%s attempts=%d → reviewing", post.id, attempts)
    return "fetch_ok"


async def _settle_sent(post: MatchedPost, meta: dict, found: FetchedReply) -> None:
    """게시 확인 → 기존 sent 경로와 동일 회계. reviewer/final_body 는 승인자 것을 승계."""
    try:
        async with in_transaction():
            await ReplyActionLog.create(
                matched_post_id=post.id,
                reviewer_id=meta["reviewer_id"],
                final_body=meta.get("final_body") or "",
                action=ReplyAction.sent,
                sns_account_id=meta.get("sns_account_id"),
                external_reply_id=found.external_reply_id[:512],
            )
            await MatchedPost.filter(id=post.id, status=PostStatus.verify_pending).update(
                status=PostStatus.replied, verify_meta=None
            )
    except IntegrityError:
        # partial unique(action='sent') — 사람/이전 주기와 경합해도 DB 가 이중 sent 를
        # 차단한다(불변식 ②). 실제 sent 존재 확인 후에만 정합 회복(_approve 와 동일 원칙).
        if await ReplyActionLog.exists(matched_post_id=post.id, action=ReplyAction.sent):
            await MatchedPost.filter(id=post.id, status=PostStatus.verify_pending).update(
                status=PostStatus.replied, verify_meta=None
            )
            return
        raise
    logger.info("조정 게시 확인 match=%s external_reply_id=%s", post.id, found.external_reply_id)
