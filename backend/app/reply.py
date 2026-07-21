"""승인/전송 플로우 보조 — sending 정체 회수 sweep (PRD §7, MUST-FIX #4).

approve 처리 중 프로세스가 죽으면 매칭이 sending 에 고착돼 영영 재시도할 수 없다.
클레임 후 일정 시간이 지난 sending 을 reviewing 으로 되돌려 수동 재시도 경로를 연다.

이중 발송 방어 계층(1차 적대 리뷰 반영):
- send_reply 는 SEND_TIMEOUT_SEC 로 강제 취소된다 — SENDING_STALE_SEC 보다 훨씬 짧아
  sweep 이 "아직 살아있는 전송"을 회수하는 일이 없다(회수 대상은 죽은 클레임뿐).
- 상태 갱신은 클레임 시각(sending_claimed_at) 펜싱으로 조건부 실행 — 회수 후 다른
  사람이 바꾼 상태(ignored 등)를 좀비 요청이 덮어쓰지 못한다.
- 회수 분기(M2 조정 설계 §3.3 — OQ-1 닫힘: idempotency-key 미지원): 전송에 착수한
  잔재(verify_meta 있음)는 결과 불명이므로 reviewing 이 아니라 verify_pending 으로
  보내 조정 잡이 실제 게시 여부를 확인한 뒤에만 retry 를 연다. 착수 전 잔재
  (verify_meta 없음, can_write=False 소스 포함)만 reviewing 복귀 — "전송 성공 후
  sent 기록 전 크래시 → 즉시 retry → 이중 발송" 창이 이 분기로 닫힌다.
"""
import logging
from datetime import UTC, datetime, timedelta

from tortoise.expressions import Q

from app.models import MatchedPost, PostStatus

logger = logging.getLogger(__name__)

# 클레임 후 이 시간이 지나면 고착 판정. SEND_TIMEOUT_SEC 보다 충분히 길어야 한다.
SENDING_STALE_SEC = 600
# send_reply 강제 상한 — 초과 시 asyncio 취소로 전송 시도 자체가 중단된다.
SEND_TIMEOUT_SEC = 120


def _stuck_q(cutoff: datetime):
    # claimed_at 이 없는 sending(비정상 경로 잔재)도 영구 고착되지 않게 함께 회수
    return Q(status=PostStatus.sending) & (
        Q(sending_claimed_at__lt=cutoff) | Q(sending_claimed_at__isnull=True)
    )


async def sweep_stuck_sending() -> int:
    """고착 sending 회수 — 전송 착수분(verify_meta 有)은 verify_pending, 그 외는
    reviewing. 회수한 행 수를 반환(스케줄러 주기 실행)."""
    cutoff = datetime.now(UTC) - timedelta(seconds=SENDING_STALE_SEC)
    ids = list(await MatchedPost.filter(_stuck_q(cutoff)).values_list("id", flat=True))
    if not ids:
        return 0
    unknown = await MatchedPost.filter(
        _stuck_q(cutoff), id__in=ids, verify_meta__isnull=False
    ).update(status=PostStatus.verify_pending)
    safe = await MatchedPost.filter(
        _stuck_q(cutoff), id__in=ids, verify_meta__isnull=True
    ).update(status=PostStatus.reviewing)
    # 사후 추적용 — SELECT~UPDATE 사이 상태가 바뀐 행은 제외되므로 ids 는 후보 목록이다
    logger.warning(
        "sending 고착 회수 %d건(→verify_pending %d, →reviewing %d) 후보 ids=%s",
        unknown + safe, unknown, safe, ids,
    )
    return unknown + safe
