"""키워드 매칭 (FR-6) — substring/regex.

regex 는 stdlib re 가 아닌 regex 모듈로 실행한다: 신뢰불가 콘텐츠에 대해
catastrophic backtracking(ReDoS)이 이벤트 루프/워커를 잠그지 않도록
패턴당 하드 타임아웃을 건다(2차 리뷰 M3 선행 조건).
"""
import logging

import regex

from app.models import Keyword, MatchType

logger = logging.getLogger(__name__)

_REGEX_TIMEOUT_SEC = 0.1  # 패턴당 상한 — 정상 키워드는 마이크로초 단위


def find_match(content: str, keywords: list[Keyword]) -> Keyword | None:
    """첫 매칭 키워드 반환(우선순위 = 목록 순서). CPU 작업 — executor 에서 호출할 것."""
    folded = content.casefold()
    for kw in keywords:
        if kw.match_type == MatchType.substring:
            if kw.pattern.casefold() in folded:
                return kw
        else:
            try:
                if regex.search(kw.pattern, content, timeout=_REGEX_TIMEOUT_SEC):
                    return kw
            except TimeoutError:
                logger.warning("keyword %s regex 타임아웃 — 매칭 skip (ReDoS 방어)", kw.id)
            except regex.error:
                logger.warning("keyword %s regex 오류 — 매칭 skip", kw.id)
    return None
