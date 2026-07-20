"""RSS 어댑터 단위 — 파싱·guid/해시 ID·오류 처리 (네트워크 불필요)."""
import pytest

from app.sources.base import FetchError
from app.sources.rss import _parse_feed

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>c</title>
<item>
  <title>guid 있는 글</title>
  <link>https://ex.com/p/1?utm=x</link>
  <guid isPermaLink="false">post-guid-1</guid>
  <description>본문 요약</description>
  <author>writer1</author>
  <pubDate>Mon, 20 Jul 2026 10:00:00 +0900</pubDate>
</item>
<item>
  <title>guid 없는 글</title>
  <link>https://ex.com/p/2</link>
  <description>내용</description>
</item>
<item><title>link 없는 글은 skip</title></item>
</channel></rss>""".encode()


def test_parse_feed_basic():
    posts = _parse_feed(RSS)
    assert len(posts) == 2
    first, second = posts
    assert first.external_post_id == "post-guid-1"  # guid 우선
    assert "guid 있는 글" in first.content and "본문 요약" in first.content
    assert first.published_at is not None and first.published_at.tzinfo is not None
    # guid 없으면 안정 해시 (sha256 hex)
    assert len(second.external_post_id) == 64
    assert second.published_at is None


def test_parse_feed_same_link_variant_same_id():
    # guid 없는 글: URL 쿼리스트링 변형이 와도 동일 ID (FR-3)
    variant = RSS.replace(b"https://ex.com/p/2", b"http://ex.com/p/2?ref=share")  # noqa: E501
    a = _parse_feed(RSS)[1].external_post_id
    b = _parse_feed(variant)[1].external_post_id
    assert a == b


def test_parse_feed_garbage_raises():
    with pytest.raises(FetchError):
        _parse_feed(b"\x00\x01 definitely not xml \xff")
