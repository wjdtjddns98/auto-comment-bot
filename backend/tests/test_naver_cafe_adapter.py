"""네이버 카페글 검색 어댑터 — 파싱·안정 ID·오류 분류·자격증명 비노출(불변식 ③).

네트워크는 httpx.MockTransport 로 대체(_client 팩토리 교체) — 실 API 호출 없음.
"""
import asyncio
import json

import httpx
import pytest

from app.config import settings
from app.models import Source, SourceType
from app.sources import get_adapter, naver_cafe
from app.sources.base import FetchError, RateLimitedError
from app.sources.naver_cafe import (
    NaverCafeAdapter,
    NaverCafeConfig,
    _clean,
    _error_summary,
    _parse_items,
    _parse_retry_after,
)

CLIENT_ID = "NAVER-CLIENT-ID-abc"
CLIENT_SECRET = "NAVER-CLIENT-SECRET-xyz789"

# 공식 문서 응답 예와 같은 형태 — link 가 openapi 리다이렉트 토큰으로 온다.
REDIRECT_ITEM = {
    "title": "<b>창업</b> 준비중인데 권리금 질문드려요",
    "link": "http://openapi.naver.com/l?AAABXIuw7CIBSA4ac5jE24WRgYuNjVicWNtKc2QbQiNvHtxeQfvvyvD9avgbMHy8H5P5QDHchWcTVbaztwC2zqzWnF4ZEOrMP8LH0sqeY95a6TlqQZKkahpKCMa6VIMZd4vEMqEZjLdArXeyzyhsvoNVUWePgBHZyF5XwAAAA=",
    "description": "가게 인수하려는데 <b>권리금</b>이 적정한지 봐주실 분...",
    "cafename": "전국 점포 직거래",
    "cafeurl": "https://cafe.naver.com/storedeal",
}
DIRECT_ITEM = {
    "title": "무인매장 <b>창업</b> 후기",
    "link": "https://cafe.naver.com/storedeal/12345?utm_source=share",
    "description": "6개월 운영 후기입니다",
    "cafename": "전국 점포 직거래",
    "cafeurl": "https://cafe.naver.com/storedeal",
}


def _payload(*items, total: int | None = None) -> dict:
    return {"lastBuildDate": "Wed, 17 Sep 2026 10:00:00 +0900",
            "total": len(items) if total is None else total,
            "start": 1, "display": len(items), "items": list(items)}


@pytest.fixture(autouse=True)
def _reset_quota_cooldown(monkeypatch):
    """공유 쿨다운은 모듈 전역이라 테스트 간 누수를 막는다."""
    monkeypatch.setattr(naver_cafe, "_quota_cooldown_until", 0.0)


# ── 파싱 ─────────────────────────────────────────────────────────────────────

def test_parse_items_strips_highlight_tags_and_maps_fields():
    post = _parse_items(_payload(REDIRECT_ITEM))[0]
    # <b> 하이라이트 제거
    assert post.content.startswith("창업 준비중인데 권리금 질문드려요")
    assert "<b>" not in post.content
    # API 가 글쓴이를 주지 않으므로 author 자리에 카페 이름이 들어간다(모듈 docstring)
    assert post.author == "전국 점포 직거래"
    # 카페글 검색 응답에는 게시 시각 필드가 없다
    assert post.published_at is None
    assert post.url == REDIRECT_ITEM["link"]


def test_clean_keeps_literal_angle_brackets_typed_by_user():
    # 태그 제거 → unescape 순서라 사용자가 literal 로 쓴 &lt;b&gt; 는 살아남는다
    assert _clean("<b>강조</b> 그리고 &lt;b&gt; 는 글자") == "강조 그리고 <b> 는 글자"


def test_parse_items_skips_entries_without_link_or_title():
    payload = _payload({"title": "링크 없음", "cafeurl": "https://cafe.naver.com/a"},
                       {"link": "https://cafe.naver.com/a/1", "title": ""})
    assert _parse_items(payload) == []


def test_parse_items_without_items_array_raises():
    with pytest.raises(FetchError):
        _parse_items({"total": 0})


# ── 안정 ID (FR-3) ───────────────────────────────────────────────────────────

def test_redirect_link_token_change_keeps_same_id():
    """핵심 방어: openapi 리다이렉트 토큰이 호출마다 바뀌어도 같은 글은 같은 ID.

    토큰을 ID 재료로 쓰면 dedup 이 무너져 매 주기 같은 글이 새로 저장된다.
    """
    rotated = dict(REDIRECT_ITEM, link="http://openapi.naver.com/l?ZZZZ-다른토큰")
    a = _parse_items(_payload(REDIRECT_ITEM))[0].external_post_id
    b = _parse_items(_payload(rotated))[0].external_post_id
    assert a == b


def test_redirect_items_with_different_titles_get_different_ids():
    other = dict(REDIRECT_ITEM, title="다른 글 제목")
    a = _parse_items(_payload(REDIRECT_ITEM))[0].external_post_id
    b = _parse_items(_payload(other))[0].external_post_id
    assert a != b


def test_link_form_change_keeps_same_id():
    """핵심 방어 2: 네이버가 링크 형식을 바꿔도 같은 글은 같은 ID.

    링크 값으로 ID 체계를 갈랐더니(리다이렉트 vs 실제 URL) 그 분기 자체가 dedup 을
    깨뜨렸다 — 형식이 바뀌는 날 보관 중이던 전 구간이 한 주기에 재유입된다.
    """
    same_post = [
        dict(DIRECT_ITEM, link="http://openapi.naver.com/l?AAAB-토큰"),
        dict(DIRECT_ITEM, link="https://cafe.naver.com/storedeal/12345"),
        dict(DIRECT_ITEM, link="https://m.cafe.naver.com/storedeal/12345"),
        dict(DIRECT_ITEM,
             link="https://cafe.naver.com/ArticleRead.nhn?clubid=1&articleid=12345"),
        dict(DIRECT_ITEM, link="https://cafe.naver.com/storedeal/12345?commentFocus=true"),
    ]
    ids = {_parse_items(_payload(i))[0].external_post_id for i in same_post}
    assert len(ids) == 1


def test_highlight_markup_does_not_affect_id():
    # title 은 _clean 을 거친 값이라 검색어별 <b> 위치 차이에 흔들리지 않는다
    a = _parse_items(_payload(dict(DIRECT_ITEM, title="무인매장 <b>창업</b> 후기")))[0]
    b = _parse_items(_payload(dict(DIRECT_ITEM, title="무인매장 창업 <b>후기</b>")))[0]
    assert a.external_post_id == b.external_post_id


def test_direct_and_redirect_ids_are_hex_and_bounded():
    for item in (REDIRECT_ITEM, DIRECT_ITEM):
        ext_id = _parse_items(_payload(item))[0].external_post_id
        assert 0 < len(ext_id) <= 512


# ── 오류 분류 ────────────────────────────────────────────────────────────────

def test_error_summary_uses_our_text_not_the_remote_message():
    """원격 errorMessage 는 싣지 않는다 — last_error 는 GET /api/sources 로 나간다."""
    body = json.dumps({"errorCode": "SE02",
                       "errorMessage": "Invalid display value NAVER-CLIENT-ID-abc"}).encode()
    summary = _error_summary(400, body)
    assert "SE02" in summary and "허용 범위" in summary
    assert "Invalid display value" not in summary
    assert CLIENT_ID not in summary


def test_error_summary_drops_unknown_shaped_error_code():
    body = json.dumps({"errorCode": "<script>x</script>"}).encode()
    assert _error_summary(400, body) == "HTTP 400"


def test_error_summary_survives_non_json_body():
    assert _error_summary(500, b"<html>oops</html>") == "HTTP 500"


@pytest.mark.parametrize("status", [400, 404, 500])
def test_non_rate_limit_statuses_raise_fetch_error(status):
    with pytest.raises(FetchError):
        NaverCafeAdapter._raise_read_errors(status, None, b'{"errorCode":"SE01"}')


def test_429_raises_rate_limited_with_retry_after():
    with pytest.raises(RateLimitedError) as exc:
        NaverCafeAdapter._raise_read_errors(429, "30", b"{}")
    assert exc.value.retry_after_sec == 30.0


def test_403_backs_off_and_explains_cause():
    with pytest.raises(RateLimitedError) as exc:
        NaverCafeAdapter._raise_read_errors(403, None, b"{}")
    assert "검색 API 사용 설정" in str(exc.value)


def test_200_does_not_raise():
    NaverCafeAdapter._raise_read_errors(200, None, b"{}")


# ── config 스키마 ────────────────────────────────────────────────────────────

def test_config_rejects_unknown_keys_and_empty_query():
    with pytest.raises(Exception):
        NaverCafeConfig.model_validate({"query": "창업", "rss_url": "https://x.com"})
    with pytest.raises(Exception):
        NaverCafeConfig.model_validate({"query": ""})
    assert NaverCafeConfig.model_validate({"query": "창업"}).query == "창업"


def test_adapter_registered_and_read_only():
    adapter = get_adapter(SourceType.naver_cafe)
    assert adapter is not None
    assert adapter.can_write is False


# ── fetch (MockTransport) ────────────────────────────────────────────────────

def _source(query: str = "창업") -> Source:
    return Source(type=SourceType.naver_cafe, config={"query": query})


@pytest.fixture
def naver_creds(monkeypatch):
    monkeypatch.setattr(settings, "naver_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "naver_client_secret", CLIENT_SECRET)


def _mock(monkeypatch, handler):
    monkeypatch.setattr(
        naver_cafe, "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0),
    )


async def test_fetch_sends_credentials_in_headers_and_sorts_by_date(
    monkeypatch, naver_creds
):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=_payload(REDIRECT_ITEM, DIRECT_ITEM))

    _mock(monkeypatch, handler)
    posts = await NaverCafeAdapter().fetch(_source(), since=None)

    assert len(posts) == 2
    assert seen["headers"]["X-Naver-Client-Id"] == CLIENT_ID
    assert seen["headers"]["X-Naver-Client-Secret"] == CLIENT_SECRET
    # 모니터링 목적 → 날짜순 고정, 한 번에 최댓값 수집
    assert seen["params"]["sort"] == "date"
    assert seen["params"]["display"] == "100"
    assert seen["params"]["query"] == "창업"


async def test_fetch_without_credentials_fails_before_calling_api(monkeypatch):
    monkeypatch.setattr(settings, "naver_client_id", "")
    monkeypatch.setattr(settings, "naver_client_secret", "")

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("자격증명이 없으면 API 를 호출하면 안 된다")

    _mock(monkeypatch, handler)
    with pytest.raises(FetchError, match="자격증명"):
        await NaverCafeAdapter().fetch(_source(), since=None)


async def test_fetch_with_blank_query_fails(monkeypatch, naver_creds):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("빈 검색어로 API 를 호출하면 안 된다")

    _mock(monkeypatch, handler)
    with pytest.raises(FetchError, match="query"):
        await NaverCafeAdapter().fetch(_source(query="   "), since=None)


async def test_fetch_error_message_never_leaks_credentials(monkeypatch, naver_creds):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"errorCode": "SE01",
                                         "errorMessage": "Incorrect query request"})

    _mock(monkeypatch, handler)
    with pytest.raises(FetchError) as exc:
        await NaverCafeAdapter().fetch(_source(), since=None)
    message = str(exc.value)
    assert "SE01" in message
    assert CLIENT_ID not in message and CLIENT_SECRET not in message


async def test_fetch_transport_error_reports_type_only(monkeypatch, naver_creds):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"connect failed for {CLIENT_SECRET}")

    _mock(monkeypatch, handler)
    with pytest.raises(FetchError) as exc:
        await NaverCafeAdapter().fetch(_source(), since=None)
    assert CLIENT_SECRET not in str(exc.value)
    assert "ConnectError" in str(exc.value)


async def test_fetch_rejects_non_json_body(monkeypatch, naver_creds):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    _mock(monkeypatch, handler)
    with pytest.raises(FetchError, match="JSON"):
        await NaverCafeAdapter().fetch(_source(), since=None)


async def test_quota_cooldown_is_shared_across_sources(monkeypatch, naver_creds):
    """한도는 클라이언트 ID(앱 전역) 단위 — 한 소스가 429 를 맞으면 나머지도 멈춘다.

    poller 의 backoff 는 소스 단위라, 이게 없으면 고갈된 쿼터에 B..Z 가 계속 호출을
    때린다(불변식 ④).
    """
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, json={"errorCode": "SE99"})

    _mock(monkeypatch, handler)
    with pytest.raises(RateLimitedError):
        await NaverCafeAdapter().fetch(_source("창업"), since=None)
    assert calls["n"] == 1

    # 다른 소스의 다음 폴은 API 를 건드리지도 않는다
    with pytest.raises(RateLimitedError, match="공유 한도"):
        await NaverCafeAdapter().fetch(_source("권리금"), since=None)
    assert calls["n"] == 1


async def test_403_also_starts_shared_cooldown(monkeypatch, naver_creds):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b"")

    _mock(monkeypatch, handler)
    with pytest.raises(RateLimitedError):
        await NaverCafeAdapter().fetch(_source(), since=None)
    assert naver_cafe._quota_cooldown_remaining() > 0


async def test_truncated_first_page_is_logged(monkeypatch, naver_creds, caplog):
    """100건을 넘기면 초과분은 '지연'이 아니라 영구 누락 — 조용히 넘어가면 안 된다."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_payload(REDIRECT_ITEM, total=4300))

    _mock(monkeypatch, handler)
    with caplog.at_level("WARNING"):
        await NaverCafeAdapter().fetch(_source("창업"), since=None)
    assert "4300" in caplog.text and "창업" in caplog.text


def test_author_is_person_flags_across_adapters():
    # naver_cafe 의 author 는 카페 이름이라 템플릿 {{author}} 치환 대상이 아니다
    assert get_adapter(SourceType.naver_cafe).author_is_person is False
    assert get_adapter(SourceType.community).author_is_person is True
    assert get_adapter(SourceType.threads).author_is_person is True


async def test_fetch_rejects_oversized_body(monkeypatch, naver_creds):
    monkeypatch.setattr(naver_cafe, "_MAX_BYTES", 1024)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 4096)

    _mock(monkeypatch, handler)
    with pytest.raises(FetchError, match="상한"):
        await NaverCafeAdapter().fetch(_source(), since=None)


# ── 리뷰 지적 보강 ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "value,expected",
    [("30", 30.0), ("1.5", 1.5), ("-5", None), ("0", None), (None, None),
     ("", None), ("Wed, 17 Sep 2026 10:00:00 GMT", None),
     ("²", None)],  # "²".isdigit() 는 True 지만 float() 는 ValueError
)
def test_retry_after_parsing_never_raises(value, expected):
    """파싱이 예외를 내면 RateLimitedError 가 아니게 되어 429 를 맞고도 backoff 가 없다."""
    assert _parse_retry_after(value) == expected


def test_429_with_bogus_retry_after_still_rate_limits():
    with pytest.raises(RateLimitedError):
        NaverCafeAdapter._raise_read_errors(429, "²", b"{}")


def test_missing_cafe_identifier_item_is_skipped():
    """카페 식별자가 없으면 저장하지 않는다 — 없는 채로 ID 를 만들면 서로 다른 카페의
    동명 글이 한 ID 로 합쳐져 뒤의 글이 영구히 버려진다."""
    orphan = {"title": "오늘의 질문", "link": "https://cafe.naver.com/x/1",
              "description": "본문", "cafename": "", "cafeurl": ""}
    assert _parse_items(_payload(orphan)) == []


def test_same_title_in_different_cafes_gets_different_ids():
    a = dict(DIRECT_ITEM, title="오늘의 질문", cafeurl="https://cafe.naver.com/aaa")
    b = dict(DIRECT_ITEM, title="오늘의 질문", cafeurl="https://cafe.naver.com/bbb")
    ids = {_parse_items(_payload(i))[0].external_post_id for i in (a, b)}
    assert len(ids) == 2


def test_cafename_is_used_when_cafeurl_missing():
    item = dict(DIRECT_ITEM, cafeurl="")
    assert len(_parse_items(_payload(item))) == 1


def test_items_beyond_display_cap_are_sliced():
    many = [dict(DIRECT_ITEM, title=f"글 {i}", cafeurl="https://cafe.naver.com/x")
            for i in range(150)]
    assert len(_parse_items(_payload(*many))) == 100


async def test_compressed_response_is_rejected(monkeypatch, naver_creds):
    """identity 를 요청했는데 압축이 오면 거부 — httpx 는 크기 상한 검사보다 먼저
    압축을 풀어 메모리를 잡는다(rss.py 2차 리뷰 C2 실측: 81KB gzip → 190MB)."""
    import gzip

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=gzip.compress(b"x" * 200_000),
                              headers={"content-encoding": "gzip"})

    _mock(monkeypatch, handler)
    with pytest.raises(FetchError, match="압축"):
        await NaverCafeAdapter().fetch(_source(), since=None)


async def test_slow_stream_hits_absolute_deadline(monkeypatch, naver_creds):
    """httpx timeout 은 청크 단위라 드립 응답을 못 막는다. poll_tick 은 소스를 순차
    처리하므로 한 건이 막히면 전체 수집이 멈춘다."""
    monkeypatch.setattr(naver_cafe, "_DEADLINE_SEC", 0.2)

    async def slow_body():
        while True:
            yield b"x"
            await asyncio.sleep(0.05)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=slow_body())

    _mock(monkeypatch, handler)
    with pytest.raises(FetchError, match="제한 시간"):
        await NaverCafeAdapter().fetch(_source(), since=None)
