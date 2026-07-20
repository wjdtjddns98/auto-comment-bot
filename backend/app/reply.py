"""승인/전송 플로우 보조 — sending 정체 회수 sweep (PRD §7, MUST-FIX #4).

approve 처리 중 프로세스가 죽으면 매칭이 sending 에 고착돼 영영 재시도할 수 없다.
클레임 후 일정 시간이 지난 sending 을 reviewing 으로 되돌려 수동 재시도 경로를 연다.

이중 발송 방어 계층(1차 적대 리뷰 반영):
- send_reply 는 SEND_TIMEOUT_SEC 로 강제 취소된다 — SENDING_STALE_SEC 보다 훨씬 짧아
  sweep 이 "아직 살아있는 전송"을 회수하는 일이 없다(회수 대상은 죽은 클레임뿐).
- 상태 갱신은 클레임 시각(sending_claimed_at) 펜싱으로 조건부 실행 — 회수 후 다른
  사람이 바꾼 상태(ignored 등)를 좀비 요청이 덮어쓰지 못한다.
- 잔여 창: "전송 성공 후 sent 기록 전 크래시"는 여전히 재시도 시 이중 발송 가능.
  근본 해결은 제공자 idempotency-key(FR-14, OQ-1)로 M2 에서 다룬다.
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
    """고착 sending → reviewing 회수. 회수한 행 수를 반환(스케줄러 주기 실행)."""
    cutoff = datetime.now(UTC) - timedelta(seconds=SENDING_STALE_SEC)
    ids = list(await MatchedPost.filter(_stuck_q(cutoff)).values_list("id", flat=True))
    if not ids:
        return 0
    n = await MatchedPost.filter(_stuck_q(cutoff), id__in=ids).update(
        status=PostStatus.reviewing
    )
    # 사후 추적용 — SELECT~UPDATE 사이 상태가 바뀐 행은 제외되므로 ids 는 후보 목록이다
    logger.warning("sending 고착 회수 %d/%d건 → reviewing 후보 ids=%s", n, len(ids), ids)
    return n
