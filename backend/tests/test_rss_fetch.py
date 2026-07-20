"""_get_with_limits 단위 — 리다이렉트·SSRF hop 재검증·크기 상한·429 (네트워크 없음).

httpx.MockTransport 로 전송 계층만 가짜로 바꾼다(2차 리뷰 H4: 이 경로가 무테스트였음).
"""
import gzip

import httpx
import pytest

from app.sources import rss
from app.sources.base import FetchError, RateLimitedError


def _patch_transport(monkeypatch, handler):
    """AsyncClient 가 MockTransport 를 쓰도록 교체."""
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(rss.httpx, "AsyncClient", factory)


async def test_relative_and_absolute_redirects_followed(monkeypatch):
    # 호스트명 대신 공인 IP 리터럴 — SSRF 가드가 실제 DNS 를 타지 않게(테스트 격리)
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/feed":
            return httpx.Response(302, headers={"location": "/moved/feed"})
        if request.url.path == "/moved/feed":
            return httpx.Response(301, headers={"location": "https://93.184.216.35/final"})
        return httpx.Response(200, content=b"<rss/>")

    _patch_transport(monkeypatch, handler)
    body, final_url = await rss._get_with_limits("https://93.184.216.34/feed")
    assert body == b"<rss/>"
    assert final_url == "https://93.184.216.35/final"
    assert len(seen) == 3


async def test_redirect_to_internal_ip_blocked(monkeypatch):
    def handler(request):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    _patch_transport(monkeypatch, handler)
    # hop 별 SSRF 재검증 — 최초 URL 이 공인이어도 리다이렉트 대상이 내부망이면 차단
    with pytest.raises(FetchError, match="SSRF"):
        await rss._get_with_limits("https://93.184.216.34/feed")


async def test_redirect_limit_exceeded(monkeypatch):
    def handler(request):
        return httpx.Response(302, headers={"location": "/next"})

    _patch_transport(monkeypatch, handler)
    with pytest.raises(FetchError, match="리다이렉트"):
        await rss._get_with_limits("https://93.184.216.34/feed")


async def test_oversize_body_rejected(monkeypatch):
    def handler(request):
        return httpx.Response(200, content=b"x" * (rss._MAX_BYTES + 1))

    _patch_transport(monkeypatch, handler)
    with pytest.raises(FetchError, match="크기 상한"):
        await rss._get_with_limits("https://93.184.216.34/feed")


async def test_compressed_response_rejected(monkeypatch):
    """압축 폭탄 방어: identity 를 요청했는데 gzip 이 오면 해제 전에 거부."""
    payload = gzip.compress(b"a" * (80 * 1024 * 1024))

    def handler(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(
            200, content=payload, headers={"content-encoding": "gzip"}
        )

    _patch_transport(monkeypatch, handler)
    with pytest.raises(FetchError, match="압축"):
        await rss._get_with_limits("https://93.184.216.34/feed")


async def test_429_uses_retry_after(monkeypatch):
    def handler(request):
        return httpx.Response(429, headers={"retry-after": "120"})

    _patch_transport(monkeypatch, handler)
    with pytest.raises(RateLimitedError) as exc:
        await rss._get_with_limits("https://93.184.216.34/feed")
    assert exc.value.retry_after_sec == 120.0


async def test_403_is_rate_limited(monkeypatch):
    def handler(request):
        return httpx.Response(403)

    _patch_transport(monkeypatch, handler)
    with pytest.raises(RateLimitedError):
        await rss._get_with_limits("https://93.184.216.34/feed")


async def test_slow_parse_hits_deadline(monkeypatch):
    """데드라인이 파싱 구간까지 덮는지(2차 리뷰 H3)."""
    import time

    from app.models import Source, SourceType

    def handler(request):
        return httpx.Response(200, content=b"<rss/>")

    _patch_transport(monkeypatch, handler)
    monkeypatch.setattr(rss, "_DEADLINE_SEC", 0.05)
    monkeypatch.setattr(rss, "_parse_feed", lambda body, url: time.sleep(0.4) or [])
    source = Source(type=SourceType.community, config={"rss_url": "https://93.184.216.34/f"})
    with pytest.raises(FetchError, match="데드라인"):
        await rss.RssAdapter().fetch(source, None)
