"""테스트 게이트 ⑤ (단위) — external_post_id 안정성 (FR-3, MUST-FIX #2)."""
from datetime import UTC, datetime

from app.sources.base import normalize_url, stable_external_id

TS = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)


def test_url_variants_same_id():
    # 쿼리스트링·http↔https·말미 슬래시·호스트 대소문자 → 동일 ID
    variants = [
        "https://cafe.example.com/post/123",
        "http://cafe.example.com/post/123",
        "https://CAFE.example.com/post/123/",
        "https://cafe.example.com/post/123?ref=share&utm_source=x",
        "https://cafe.example.com/post/123#comment",
    ]
    ids = {stable_external_id(u, "author1", TS) for u in variants}
    assert len(ids) == 1


def test_different_post_different_id():
    a = stable_external_id("https://ex.com/post/1", "author1", TS)
    b = stable_external_id("https://ex.com/post/2", "author1", TS)
    c = stable_external_id("https://ex.com/post/1", "author2", TS)
    assert len({a, b, c}) == 3


def test_timestamp_excluded_when_absent():
    # 게시시각 미노출 소스: URL+author 만으로 결정 (MUST-FIX #2)
    a = stable_external_id("https://ex.com/p/1", "author1", None)
    b = stable_external_id("https://ex.com/p/1?q=1", "author1", None)
    assert a == b


def test_normalize_url():
    assert normalize_url("HTTPS://Ex.Com/a/b/?x=1") == "ex.com/a/b"
