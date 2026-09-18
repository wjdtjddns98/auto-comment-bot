"""디시인사이드 어댑터 — 목록 파싱·공지 제외·안정 ID·오류 분류.

네트워크는 httpx.MockTransport 로 대체(_client 팩토리 교체) — 실 요청 없음.
HTML 은 2026-09-18 실제 응답 구조를 축약한 것이다.
"""
import httpx
import pytest

from app.models import Source, SourceType
from app.sources import dcinside, get_adapter
from app.sources.base import FetchError, RateLimitedError
from app.sources.dcinside import DcinsideAdapter, DcinsideConfig, _parse_dt, _to_posts

HTML = """
<table><tbody class="listwrap2">
<tr class="ub-content ">
  <td class="gall_num">설문</td>
  <td class="gall_tit ub-word"><a href="javascript:;"><b>설문 배너입니다</b></a></td>
  <td class="gall_writer ub-writer" user_name="운영자"><b>운영자</b></td>
  <td class="gall_date">26/09/14</td>
</tr>
<tr class="ub-content us-post" data-no="76329" data-type="icon_notice">
  <td class="gall_num">공지</td>
  <td class="gall_tit ub-word">
    <a href="/board/view/?id=dog&amp;no=76329&amp;page=1"><b><b>멍멍이 갤러리 이용 안내</b></b></a>
    <a class="reply_numbox" href="https://gall.dcinside.com/board/view/?id=dog&amp;no=76329&amp;t=cv"><span class="reply_num">[438]</span></a>
  </td>
  <td class="gall_writer ub-writer" data-nick="운영자" data-ip="211.232"><b>운영자</b></td>
  <td class="gall_date">26/09/14</td>
</tr>
<tr class="ub-content us-post" data-no="76500" data-type="icon_txt">
  <td class="gall_num">76500</td>
  <td class="gall_tit ub-word">
    <a href="/board/view/?id=dog&amp;no=76500&amp;page=1">간식 추천좀</a>
    <a class="reply_numbox" href="/board/view/?id=dog&amp;no=76500&amp;t=cv"><span class="reply_num">[12]</span></a>
  </td>
  <td class="gall_writer ub-writer" data-nick="댕댕이집사" data-ip="1.2"><b>댕댕이집사</b></td>
  <td class="gall_date" title="2026-09-18 10:23:45">10:23</td>
</tr>
<tr class="ub-content us-post" data-no="76501" data-type="icon_pic">
  <td class="gall_num">76501</td>
  <td class="gall_tit ub-word"><a href="/board/view/?id=dog&amp;no=76501">체리아이 수술비용 대략 얼마임?</a></td>
  <td class="gall_writer ub-writer" data-nick="ㅇㅇ" data-ip="223.38"><b>ㅇㅇ</b></td>
  <td class="gall_date">26/09/17</td>
</tr>
</tbody></table>
"""


# ── 파싱 ─────────────────────────────────────────────────────────────────────

def test_parses_normal_rows_only():
    posts = _to_posts(HTML, "dog")
    # 설문(글번호 없음)·공지(icon_notice)는 제외 → 일반 글 2건
    assert [p.content for p in posts] == ["간식 추천좀", "체리아이 수술비용 대략 얼마임?"]


def test_reply_count_suffix_is_stripped_from_title():
    posts = _to_posts(HTML, "dog")
    assert "[12]" not in posts[0].content


def test_author_comes_from_data_nick():
    posts = _to_posts(HTML, "dog")
    assert posts[0].author == "댕댕이집사"
    assert posts[1].author == "ㅇㅇ"


def test_url_is_absolute_and_not_the_comment_link():
    url = _to_posts(HTML, "dog")[0].url
    assert url.startswith("https://gall.dcinside.com/board/view/")
    assert "t=cv" not in url  # 댓글수 링크가 아니라 글 링크여야 한다


def test_published_at_uses_title_attr_only():
    posts = _to_posts(HTML, "dog")
    assert posts[0].published_at is not None  # title="2026-09-18 10:23:45"
    assert posts[1].published_at is None      # `26/09/17` 축약은 쓰지 않는다


@pytest.mark.parametrize(
    "value,ok",
    [("2026-09-18 10:23:45", True), ("2026-09-18", True),
     ("26/09/17", False), ("10:23", False), ("", False)],
)
def test_parse_dt_rejects_ambiguous_formats(value, ok):
    assert (_parse_dt(value) is not None) is ok


def test_empty_html_yields_nothing():
    assert _to_posts("<table></table>", "dog") == []


# ── 안정 ID (FR-3) ───────────────────────────────────────────────────────────

def test_id_is_gallery_scoped_and_stable():
    a = _to_posts(HTML, "dog")[0].external_post_id
    b = _to_posts(HTML.replace("page=1", "page=3"), "dog")[0].external_post_id
    assert a == b  # 목록 페이지 파라미터가 달라도 같은 글
    other = _to_posts(HTML, "cat")[0].external_post_id
    assert a != other  # 갤러리가 다르면 같은 글번호라도 다른 ID


# ── config ───────────────────────────────────────────────────────────────────

def test_config_rejects_bad_gallery_id_and_extra_keys():
    assert DcinsideConfig.model_validate({"gallery_id": "dog"}).gallery_id == "dog"
    for bad in ({"gallery_id": "dog&id=x"}, {"gallery_id": ""},
                {"gallery_id": "dog", "rss_url": "https://x"}):
        with pytest.raises(Exception):
            DcinsideConfig.model_validate(bad)


def test_adapter_registered_read_only():
    adapter = get_adapter(SourceType.dcinside)
    assert adapter is not None
    assert adapter.can_write is False          # 자동 게시 경로 없음(불변식 ①)
    assert not hasattr(adapter, "send_reply")  # 쓰기 메서드 자체를 두지 않는다


# ── fetch (MockTransport) ────────────────────────────────────────────────────

def _source(gallery_id: str = "dog") -> Source:
    return Source(type=SourceType.dcinside, config={"gallery_id": gallery_id})


def _mock(monkeypatch, handler):
    monkeypatch.setattr(
        dcinside, "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0),
    )


async def test_fetch_requests_the_gallery_list(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, content=HTML.encode())

    _mock(monkeypatch, handler)
    posts = await DcinsideAdapter().fetch(_source(), since=None)
    assert len(posts) == 2
    assert "gall.dcinside.com/board/lists/" in seen["url"] and "id=dog" in seen["url"]
    assert "sns-keyword-monitor" in seen["ua"]  # 정직한 UA(불변식 ④)


async def test_fetch_rejects_injected_gallery_id(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("검증 실패한 id 로 요청하면 안 된다")

    _mock(monkeypatch, handler)
    with pytest.raises(FetchError, match="gallery_id"):
        await DcinsideAdapter().fetch(_source("dog&no=1"), since=None)


@pytest.mark.parametrize("status", [429, 403])
async def test_blocked_statuses_back_off(monkeypatch, status):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers={"retry-after": "30"})

    _mock(monkeypatch, handler)
    with pytest.raises(RateLimitedError) as exc:
        await DcinsideAdapter().fetch(_source(), since=None)
    assert exc.value.retry_after_sec == 30.0


@pytest.mark.parametrize("value", ["abc", "-5", "0", "Wed, 18 Sep 2026 10:00:00 GMT"])
async def test_unparseable_retry_after_falls_back_to_none(monkeypatch, value):
    """파싱 실패가 예외로 새면 RateLimitedError 가 아니게 되어 backoff 가 사라진다."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": value})

    _mock(monkeypatch, handler)
    with pytest.raises(RateLimitedError) as exc:
        await DcinsideAdapter().fetch(_source(), since=None)
    assert exc.value.retry_after_sec is None


async def test_redirect_is_reported_not_followed(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://gall.dcinside.com/"})

    _mock(monkeypatch, handler)
    with pytest.raises(FetchError, match="리다이렉트"):
        await DcinsideAdapter().fetch(_source("nosuchgallery"), since=None)


async def test_compressed_response_is_rejected(monkeypatch):
    import gzip

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=gzip.compress(b"x" * 100_000),
                              headers={"content-encoding": "gzip"})

    _mock(monkeypatch, handler)
    with pytest.raises(FetchError, match="압축"):
        await DcinsideAdapter().fetch(_source(), since=None)


async def test_oversized_response_is_rejected(monkeypatch):
    monkeypatch.setattr(dcinside, "_MAX_BYTES", 1024)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 4096)

    _mock(monkeypatch, handler)
    with pytest.raises(FetchError, match="상한"):
        await DcinsideAdapter().fetch(_source(), since=None)
