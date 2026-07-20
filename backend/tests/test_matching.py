"""키워드 매칭 단위 — substring/regex + ReDoS 타임아웃 방어."""
import time

from app.matching import find_match
from app.models import Keyword, MatchType


def _kw(pattern: str, match_type: MatchType, kid: int = 1) -> Keyword:
    k = Keyword(pattern=pattern, match_type=match_type)
    k.id = kid
    return k


def test_substring_casefold():
    kws = [_kw("맛집", MatchType.substring), _kw("OpenAI", MatchType.substring, 2)]
    assert find_match("강남 맛집 추천해요", kws).id == 1
    assert find_match("openai 신모델 발표", kws).id == 2
    assert find_match("무관한 내용", kws) is None


def test_regex_match():
    kws = [_kw(r"환불\s*(요청|문의)", MatchType.regex)]
    assert find_match("환불 요청합니다", kws) is not None
    assert find_match("환불요청", kws) is not None
    assert find_match("환영합니다", kws) is None


def test_redos_pattern_times_out_without_hanging():
    # catastrophic backtracking 패턴 + 불일치 입력 — 타임아웃으로 skip 되고 멈추지 않아야 함
    evil = _kw(r"(a+)+$", MatchType.regex)
    content = "a" * 40 + "b"
    start = time.monotonic()
    assert find_match(content, [evil]) is None
    assert time.monotonic() - start < 2.0


def test_invalid_regex_skipped():
    # 저장 전 검증이 막지만, 방어적으로 매칭 단계에서도 죽지 않아야 함
    kws = [_kw("[broken", MatchType.regex), _kw("정상", MatchType.substring, 2)]
    assert find_match("정상 키워드 포함", kws).id == 2
