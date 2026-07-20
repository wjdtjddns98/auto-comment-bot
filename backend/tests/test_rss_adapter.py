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


BASE = "https://ex.com/feed"


def test_parse_feed_basic():
    posts = _parse_feed(RSS, BASE)
    assert len(posts) == 2
    first, second = posts
    assert first.external_post_id == "post-guid-1"  # guid 우선
    assert "guid 있는 글" in first.content and "본문 요약" in first.content
    assert first.published_at is not None and first.published_at.tzinfo is not None
    # guid 없으면 안정 해시 (sha256 hex)
    assert len(second.external_post_id) == 64
    assert second.published_at is None


def test_parse_feed_same_link_variant_same_id():
    # guid 없는 글: URL 추적 파라미터 변형이 와도 동일 ID (FR-3)
    variant = RSS.replace(b"https://ex.com/p/2", b"http://ex.com/p/2?utm_source=share")
    a = _parse_feed(RSS, BASE)[1].external_post_id
    b = _parse_feed(variant, BASE)[1].external_post_id
    assert a == b


def test_updated_change_keeps_same_id():
    # updated 만 바뀐 동일 글(오탈자 수정 등) → 동일 ID (리뷰 H2-b)
    with_updated = RSS.replace(
        "<description>내용</description>".encode(),
        "<description>내용</description><updated>2026-07-20T11:00:00Z</updated>".encode(),
    )
    a = _parse_feed(RSS, BASE)[1].external_post_id
    b = _parse_feed(with_updated, BASE)[1].external_post_id
    assert a == b


def test_relative_link_joined_with_base():
    relative = RSS.replace(b"https://ex.com/p/2", b"/p/2-relative")
    posts = _parse_feed(relative, "https://ex.com/rss/board.xml")
    assert posts[1].url == "https://ex.com/p/2-relative"


def test_oversize_guid_hashed_to_fixed_length():
    big_guid = b"g" * 600
    feed = RSS.replace(b"post-guid-1", big_guid)
    assert len(_parse_feed(feed, BASE)[0].external_post_id) == 64


def test_parse_feed_garbage_raises():
    with pytest.raises(FetchError):
        _parse_feed(b"\x00\x01 definitely not xml \xff", BASE)


async def test_ssrf_guard_rejects_internal_targets():
    from app.sources.base import assert_public_http_url

    for bad in (
        "http://127.0.0.1/feed",
        "http://10.0.0.5/feed",
        "http://192.168.1.1/feed",
        "http://169.254.169.254/latest/meta-data/",
        "ftp://ex.com/feed",
        "http://user:pw@ex.com/feed",
    ):
        with pytest.raises(FetchError):
            await assert_public_http_url(bad)
    # 공인 IP 리터럴은 통과 (DNS 불필요)
    await assert_public_http_url("https://93.184.216.34/feed")
