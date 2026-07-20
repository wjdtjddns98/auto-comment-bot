"""승인/전송 플로우 보조 — sending 정체 회수 sweep (PRD §7, MUST-FIX #4).

approve 처리 중 프로세스가 죽으면 매칭이 sending 에 고착돼 영영 재시도할 수 없다.
클레임 후 일정 시간이 지난 sending 을 reviewing 으로 되돌려 수동 재시도 경로를 연다.

주의: "send 성공 후 sent 기록 전 크래시" 케이스는 sweep 복귀 후 재시도 시 이중 발송
가능성이 남는다 — partial unique 는 기록이 있어야 막을 수 있다. 근본 해결은 제공자
idempotency-key(FR-14, OQ-1) 로 M2 에서 다룬다.
"""
import logging
from datetime import UTC, datetime, timedelta

from tortoise.expressions import Q

from app.models import MatchedPost, PostStatus

logger = logging.getLogger(__name__)

# 클레임 후 이 시간이 지나면 고착 판정 — 요청 타임아웃보다 충분히 길게.
SENDING_STALE_SEC = 600


async def sweep_stuck_sending() -> int:
    """고착 sending → reviewing 회수. 회수한 행 수를 반환(스케줄러 주기 실행)."""
    cutoff = datetime.now(UTC) - timedelta(seconds=SENDING_STALE_SEC)
    n = await MatchedPost.filter(
        Q(status=PostStatus.sending)
        # claimed_at 이 없는 sending(비정상 경로 잔재)도 영구 고착되지 않게 함께 회수
        & (Q(sending_claimed_at__lt=cutoff) | Q(sending_claimed_at__isnull=True))
    ).update(status=PostStatus.reviewing)
    if n:
        logger.warning("sending 고착 %d건 회수 → reviewing", n)
    return n
