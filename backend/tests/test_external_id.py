"""테스트 게이트 ⑤ (단위) — external_post_id 안정성 (FR-3, MUST-FIX #2)."""
from datetime import UTC, datetime

from app.sources.base import normalize_url, stable_external_id

TS = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)


def test_url_variants_same_id():
    # 추적 파라미터·http↔https·말미 슬래시·호스트 대소문자·프래그먼트 → 동일 ID
    variants = [
        "https://cafe.example.com/post/123",
        "http://cafe.example.com/post/123",
        "https://CAFE.example.com/post/123/",
        "https://cafe.example.com/post/123?ref=share&utm_source=x&fbclid=abc",
        "https://cafe.example.com/post/123#comment",
    ]
    ids = {stable_external_id(u, "author1", TS) for u in variants}
    assert len(ids) == 1


def test_query_identified_posts_not_merged():
    # ?id=N 처럼 쿼리가 게시물 식별자인 게시판 URL — 서로 다른 글은 다른 ID (리뷰 H2-a)
    a = stable_external_id("https://forum.ex.com/view.php?id=1", "author", None)
    b = stable_external_id("https://forum.ex.com/view.php?id=2", "author", None)
    assert a != b
    # 같은 글: 식별 쿼리는 유지 + 추적 파라미터만 제거 + 순서 무관
    c = stable_external_id("https://forum.ex.com/view.php?id=1&utm_source=x", "author", None)
    d = stable_external_id("http://forum.ex.com/view.php?utm_medium=y&id=1", "author", None)
    assert a == c == d


def test_ref_prefixed_identifiers_preserved():
    # "ref" 는 정확일치로만 제거 — refid/referrer 같은 식별자는 보존 (2차 리뷰 C1 회귀)
    a = stable_external_id("https://board.ex.com/view?refid=12345", "author", None)
    b = stable_external_id("https://board.ex.com/view?refid=99999", "author", None)
    assert a != b
    assert normalize_url("https://board.ex.com/view?referrer=x") == "board.ex.com/view?referrer=x"
    # 정확일치 ref 는 여전히 제거
    assert normalize_url("https://board.ex.com/view?ref=share") == "board.ex.com/view"


def test_different_post_different_id():
    a = stable_external_id("https://ex.com/post/1", "author1", TS)
    b = stable_external_id("https://ex.com/post/2", "author1", TS)
    c = stable_external_id("https://ex.com/post/1", "author2", TS)
    assert len({a, b, c}) == 3


def test_timestamp_excluded_when_absent():
    # 게시시각 미노출 소스: URL+author 만으로 결정 (MUST-FIX #2)
    a = stable_external_id("https://ex.com/p/1", "author1", None)
    b = stable_external_id("https://ex.com/p/1?utm_source=share", "author1", None)
    assert a == b


def test_normalize_url():
    assert normalize_url("HTTPS://Ex.Com/a/b/?x=1&utm_source=s") == "ex.com/a/b?x=1"
