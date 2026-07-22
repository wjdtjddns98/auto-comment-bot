"""Threads 어댑터 — 전송 분류(§3.2)·수집·조정 조회·토큰 비노출(불변식 ③).

네트워크는 httpx.MockTransport 로 대체(_client 팩토리 교체) — 실 API 호출 없음.
주의: 모듈 pytestmark 를 쓰면 상단 단위 테스트까지 db 마킹되므로 개별 @db 를 쓴다.
"""
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.fernet import Fernet

from app import crypto
from app.auth import hash_password
from app.config import settings
from app.models import (
    AccountStatus,
    MatchedPost,
    Platform,
    Role,
    SnsAccount,
    SnsAccountSecret,
    Source,
    SourceType,
    User,
)
from app.sources import threads
from app.sources.base import (
    FetchError,
    RateLimitedError,
    SendError,
    SendOutcomeUnknown,
)

TOKEN = "THREADS-SECRET-TOKEN-abc123"
db = pytest.mark.db


# ── 단위: 파싱/스키마 (DB 불필요) ─────────────────────────────────────────────


def test_parse_ts_formats():
    assert threads._parse_ts("2026-07-21T09:00:00+0000") == datetime(
        2026, 7, 21, 9, 0, 0, tzinfo=UTC
    )
    assert threads._parse_ts("2026-07-21T09:00:00Z") is not None
    assert threads._parse_ts(None) is None
    assert threads._parse_ts("잘못된값") is None


def test_threads_config_schema():
    threads.ThreadsConfig(query="누띠", sns_account_id=1)
    with pytest.raises(ValueError):
        threads.ThreadsConfig(query="", sns_account_id=1)  # 빈 검색어
    with pytest.raises(ValueError):
        threads.ThreadsConfig(query="q", sns_account_id=1, extra="x")  # 미지 키


def test_error_summary_excludes_meta_message():
    """Meta 에러 JSON 의 message(요청 원문 echo 가능)는 요약에 싣지 않는다(불변식 ③)."""
    resp = httpx.Response(
        400,
        json={"error": {"code": 100, "type": "OAuthException",
                        "message": f"Invalid token {TOKEN} for request"}},
        request=httpx.Request("POST", "https://x"),
    )
    summary = threads._error_summary(resp)
    assert TOKEN not in summary
    assert "code=100" in summary


# ── 통합: 실 DB(계정/시크릿) + MockTransport ─────────────────────────────────


class Recorder:
    """요청 기록 + 라우팅 핸들러. routes: (method, path) → (status, json) or 예외."""

    def __init__(self, routes):
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        result = self.routes.get((request.method, request.url.path))
        if result is None:
            return httpx.Response(404, json={"error": {"code": 803, "type": "NotFound"}})
        if isinstance(result, Exception):
            raise result
        status, body = result
        return httpx.Response(status, json=body)

    def install(self, monkeypatch):
        rec = self

        def _mock_client() -> httpx.AsyncClient:
            return httpx.AsyncClient(
                base_url=threads._API, timeout=1.0,
                transport=httpx.MockTransport(rec.handler),
            )

        monkeypatch.setattr(threads, "_client", _mock_client)

    def assert_token_only_in_auth_header(self):
        """토큰이 Authorization 헤더에만 있고 URL/본문에 없다(불변식 ③, R7)."""
        for req in self.requests:
            assert TOKEN not in str(req.url)
            assert TOKEN not in (req.content or b"").decode(errors="ignore")
            assert req.headers.get("Authorization") == f"Bearer {TOKEN}"


@pytest.fixture
async def threads_setup(api_client, monkeypatch):
    """threads 계정(+암호화 시크릿)·소스·매칭 글."""
    monkeypatch.setattr(settings, "credentials_fernet_keys", Fernet.generate_key().decode())
    user = await User.create(
        email=f"t-{uuid.uuid4().hex[:10]}@test.local",
        password_hash=hash_password("pw-test-1234"), role=Role.reviewer,
    )
    account = await SnsAccount.create(
        user=user, platform=Platform.threads, display_name="봇계정"
    )
    await SnsAccountSecret.create(
        account=account,
        encrypted_credentials=crypto.encrypt_credentials({"access_token": TOKEN}),
    )
    source = await Source.create(
        user=user, type=SourceType.threads,
        config={"query": "누띠", "sns_account_id": account.id},
    )
    post = await MatchedPost.create(
        source=source, external_post_id="target-media-9", content="본문"
    )
    yield account, source, post
    await post.delete()
    await source.delete()
    await account.delete()
    await user.delete()


ADAPTER = threads.ThreadsAdapter()


@db
async def test_send_reply_success_backfills_username(threads_setup, monkeypatch):
    account, source, post = threads_setup
    rec = Recorder({
        ("GET", "/v1.0/me"): (200, {"id": "u1", "username": "our_bot"}),
        ("POST", "/v1.0/me/threads"): (200, {"id": "C1"}),
        ("POST", "/v1.0/me/threads_publish"): (200, {"id": "M1"}),
    })
    rec.install(monkeypatch)

    media_id = await ADAPTER.send_reply(source, post, "안녕하세요", account)

    assert media_id == "M1"
    await account.refresh_from_db()
    assert account.platform_username == "our_bot"  # 전송 전 판정 키 저장(§3.1·R7)
    # 컨테이너 생성 요청에 reply_to_id 가 실렸다
    create_req = next(r for r in rec.requests if r.url.path == "/v1.0/me/threads")
    assert b"reply_to_id=target-media-9" in create_req.content
    rec.assert_token_only_in_auth_header()


@db
async def test_send_refreshes_stale_username(threads_setup, monkeypatch):
    """핸들 변경 대비 — 전송마다 판정 키를 갱신한다(2차 리뷰 중요-1)."""
    account, source, post = threads_setup
    await SnsAccount.filter(id=account.id).update(platform_username="old_bot")
    await account.refresh_from_db()
    rec = Recorder({
        ("GET", "/v1.0/me"): (200, {"id": "u1", "username": "renamed_bot"}),
        ("POST", "/v1.0/me/threads"): (200, {"id": "C1"}),
        ("POST", "/v1.0/me/threads_publish"): (200, {"id": "M1"}),
    })
    rec.install(monkeypatch)
    await ADAPTER.send_reply(source, post, "안녕하세요", account)
    await account.refresh_from_db()
    assert account.platform_username == "renamed_bot"


@db
async def test_send_container_failure_is_definite(threads_setup, monkeypatch):
    account, source, post = threads_setup
    rec = Recorder({
        ("GET", "/v1.0/me"): (200, {"id": "u1", "username": "our_bot"}),
        ("POST", "/v1.0/me/threads"): (500, {"error": {"code": 2, "type": "ServerError"}}),
    })
    rec.install(monkeypatch)
    with pytest.raises(SendError) as exc_info:
        await ADAPTER.send_reply(source, post, "안녕하세요", account)
    assert TOKEN not in str(exc_info.value)


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (500, 2, SendOutcomeUnknown),    # 발행 후 응답 생성 실패 가능 — 불명
        (408, 1, SendOutcomeUnknown),
        (409, 1, SendOutcomeUnknown),
        (404, 803, SendOutcomeUnknown),  # §3.2 명시 목록 밖 4xx — 확정으로 넓히지 않음
        (400, 100, SendError),           # 유효성 거부(code 100) — 요청 미수행 명확
        (400, 190, SendError),           # OAuth 만료(code 190)
        (400, 2, SendOutcomeUnknown),    # Meta 는 transient(code 1·2)도 400 으로 준다 — 불명
        (400, 1, SendOutcomeUnknown),
        (400, None, SendOutcomeUnknown),  # code 파싱 불가 — 확정으로 좁히지 않는다
        (401, 190, SendError),
        (429, 4, SendError),
    ],
)
@db
async def test_publish_status_classification(threads_setup, monkeypatch, status, code, expected):
    account, source, post = threads_setup
    rec = Recorder({
        ("GET", "/v1.0/me"): (200, {"id": "u1", "username": "our_bot"}),
        ("POST", "/v1.0/me/threads"): (200, {"id": "C1"}),
        ("POST", "/v1.0/me/threads_publish"): (status, {"error": {"code": code, "type": "T"}}),
    })
    rec.install(monkeypatch)
    with pytest.raises(expected) as exc_info:
        await ADAPTER.send_reply(source, post, "안녕하세요", account)
    if expected is SendOutcomeUnknown:
        assert exc_info.value.container_id == "C1"  # R3 재발행 승격 대비 보존
    assert TOKEN not in str(exc_info.value)


@db
async def test_publish_200_without_id_is_unknown(threads_setup, monkeypatch):
    """200 인데 id 없음 — 발행됐을 수 있으므로 확정 실패로 좁히지 않는다."""
    account, source, post = threads_setup
    rec = Recorder({
        ("GET", "/v1.0/me"): (200, {"id": "u1", "username": "our_bot"}),
        ("POST", "/v1.0/me/threads"): (200, {"id": "C1"}),
        ("POST", "/v1.0/me/threads_publish"): (200, {}),
    })
    rec.install(monkeypatch)
    with pytest.raises(SendOutcomeUnknown) as exc_info:
        await ADAPTER.send_reply(source, post, "안녕하세요", account)
    assert exc_info.value.container_id == "C1"


@db
async def test_publish_not_ready_retries_same_container(threads_setup, monkeypatch):
    """생성 직후 400 code=24(컨테이너 준비 전) → 같은 creation_id 로 재시도해 성공.
    재호출은 idempotent 라 이중 게시 경로가 없다(R3 실측 2026-07-22, 설계 §1)."""
    account, source, post = threads_setup
    monkeypatch.setattr(threads, "_PUBLISH_RETRY_DELAYS", (0.0, 0.0))
    publish_responses = [
        (400, {"error": {"code": 24, "type": "OAuthException"}}),
        (200, {"id": "M1"}),
    ]
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1.0/me":
            return httpx.Response(200, json={"id": "u1", "username": "our_bot"})
        if request.url.path == "/v1.0/me/threads":
            return httpx.Response(200, json={"id": "C1"})
        status, body = publish_responses.pop(0)
        return httpx.Response(status, json=body)

    def _mock_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=threads._API, timeout=1.0, transport=httpx.MockTransport(handler)
        )

    monkeypatch.setattr(threads, "_client", _mock_client)
    media_id = await ADAPTER.send_reply(source, post, "안녕하세요", account)
    assert media_id == "M1"
    # 컨테이너 생성은 1회뿐 — 재시도는 같은 creation_id 의 publish 재호출로만
    assert sum(1 for r in requests if r.url.path == "/v1.0/me/threads") == 1
    publishes = [r for r in requests if r.url.path == "/v1.0/me/threads_publish"]
    assert len(publishes) == 2
    assert all(b"creation_id=C1" in (r.content or b"") for r in publishes)


@db
async def test_publish_not_ready_exhausted_is_definite(threads_setup, monkeypatch):
    """재시도 소진까지 400 code=24 → 확정 실패(SendError) — "컨테이너를 찾지 못함"은
    발행 미수행 보장(R3 실측). verify_pending 5분 대기 없이 즉시 retry 가 열린다."""
    account, source, post = threads_setup
    monkeypatch.setattr(threads, "_PUBLISH_RETRY_DELAYS", (0.0, 0.0))
    rec = Recorder({
        ("GET", "/v1.0/me"): (200, {"id": "u1", "username": "our_bot"}),
        ("POST", "/v1.0/me/threads"): (200, {"id": "C1"}),
        ("POST", "/v1.0/me/threads_publish"):
            (400, {"error": {"code": 24, "type": "OAuthException"}}),
    })
    rec.install(monkeypatch)
    with pytest.raises(SendError) as exc_info:
        await ADAPTER.send_reply(source, post, "안녕하세요", account)
    assert sum(1 for r in rec.requests if r.url.path == "/v1.0/me/threads_publish") == 3
    assert TOKEN not in str(exc_info.value)


@db
async def test_publish_timeout_is_unknown(threads_setup, monkeypatch):
    account, source, post = threads_setup
    rec = Recorder({
        ("GET", "/v1.0/me"): (200, {"id": "u1", "username": "our_bot"}),
        ("POST", "/v1.0/me/threads"): (200, {"id": "C1"}),
        ("POST", "/v1.0/me/threads_publish"): httpx.ReadTimeout("timeout"),
    })
    rec.install(monkeypatch)
    with pytest.raises(SendOutcomeUnknown) as exc_info:
        await ADAPTER.send_reply(source, post, "안녕하세요", account)
    assert exc_info.value.container_id == "C1"
    assert TOKEN not in str(exc_info.value)


@db
async def test_send_body_over_limit_rejected_before_any_request(threads_setup, monkeypatch):
    account, source, post = threads_setup
    await SnsAccount.filter(id=account.id).update(platform_username="our_bot")
    await account.refresh_from_db()
    rec = Recorder({})
    rec.install(monkeypatch)
    with pytest.raises(SendError):
        await ADAPTER.send_reply(source, post, "a" * (threads.MAX_TEXT_LEN + 1), account)
    assert rec.requests == []  # 컨테이너 생성 자체가 없었다 — 게시 위험 0


@db
async def test_fetch_maps_keyword_search(threads_setup, monkeypatch):
    account, source, post = threads_setup
    rec = Recorder({
        ("GET", "/v1.0/keyword_search"): (200, {"data": [
            {"id": "m1", "text": "누띠 간식 후기", "username": "u1",
             "permalink": "https://threads.net/p/1", "timestamp": "2026-07-21T09:00:00+0000"},
            {"id": "m2", "text": ""},  # 빈 본문 skip
        ]}),
    })
    rec.install(monkeypatch)
    posts = await ADAPTER.fetch(source, None)
    assert len(posts) == 1
    assert posts[0].external_post_id == "m1" and posts[0].author == "u1"
    assert posts[0].published_at == datetime(2026, 7, 21, 9, 0, 0, tzinfo=UTC)
    # 검색어·최신순(RECENT)이 쿼리로 전달됐고 토큰은 헤더에만
    assert rec.requests[0].url.params["q"] == "누띠"
    assert rec.requests[0].url.params["search_type"] == "RECENT"  # 모니터링은 최신순
    rec.assert_token_only_in_auth_header()


@db
async def test_fetch_429_maps_rate_limited(threads_setup, monkeypatch):
    account, source, post = threads_setup
    rec = Recorder({
        ("GET", "/v1.0/keyword_search"): (429, {"error": {"code": 4, "type": "Throttle"}}),
    })
    rec.install(monkeypatch)
    with pytest.raises(RateLimitedError):
        await ADAPTER.fetch(source, None)


@db
async def test_fetch_inactive_account_fails(threads_setup, monkeypatch):
    account, source, post = threads_setup
    await SnsAccount.filter(id=account.id).update(status=AccountStatus.revoked)
    rec = Recorder({})
    rec.install(monkeypatch)
    with pytest.raises(FetchError):
        await ADAPTER.fetch(source, None)
    assert rec.requests == []


@db
async def test_fetch_replies_pagination_stops_at_since(threads_setup, monkeypatch):
    """최신순 페이지 순회 — since 이전 timestamp 를 만나면 다음 페이지로 가지 않는다."""
    account, source, post = threads_setup
    base = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)

    def ts(offset):
        return (base + timedelta(seconds=offset)).strftime("%Y-%m-%dT%H:%M:%S+0000")

    page1 = {
        "data": [
            {"id": "r3", "username": "u", "text": "셋", "timestamp": ts(30)},
            {"id": "r2", "username": "u", "text": "둘", "timestamp": ts(20)},
        ],
        "paging": {"cursors": {"after": "CURSOR1"}},
    }
    page2 = {
        "data": [
            {"id": "r1", "username": "u", "text": "하나", "timestamp": ts(10)},
            {"id": "r0", "username": "u", "text": "옛글", "timestamp": ts(-999)},  # since 이전
        ],
        "paging": {"cursors": {"after": "CURSOR2"}},
    }
    pages = {None: page1, "CURSOR1": page2}
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=pages[request.url.params.get("after")])

    def _mock_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=threads._API, timeout=1.0, transport=httpx.MockTransport(handler)
        )

    monkeypatch.setattr(threads, "_client", _mock_client)

    replies = await ADAPTER.fetch_replies(source, "900000009", account, since=base)
    assert [r.external_reply_id for r in replies] == ["r3", "r2", "r1"]
    assert len(requests) == 2  # CURSOR2 페이지는 요청하지 않았다(since 중단)
    # 최신순은 가정이 아니라 계약 — 매 요청에 reverse/limit 명시(1차 리뷰 중요-3)
    for req in requests:
        assert req.url.params["reverse"] == "true"
        assert req.url.params["limit"] == "100"


@db
async def test_fetch_replies_cap_exhaustion_raises(threads_setup, monkeypatch):
    """페이지 캡 소진(since 미도달) → 부분 결과로 조용히 반환하지 않고 조회 실패 —
    조정이 미게시로 오판해 retry 를 여는 것 방지(1차 리뷰 중요-2)."""
    account, source, post = threads_setup
    ts = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)
    endless_page = {
        "data": [{"id": "r1", "username": "u", "text": "x",
                  "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S+0000")}],
        "paging": {"cursors": {"after": "MORE"}},  # 항상 다음 페이지 존재
    }
    rec = Recorder({("GET", "/v1.0/900000009/replies"): (200, endless_page)})
    rec.install(monkeypatch)
    with pytest.raises(FetchError):
        await ADAPTER.fetch_replies(
            source, "900000009", account, since=ts - timedelta(hours=1)
        )
    assert len(rec.requests) == threads._REPLIES_PAGE_CAP  # 캡까지 순회 후 실패 처리


@db
async def test_fetch_replies_rejects_non_numeric_media_id(threads_setup, monkeypatch):
    """경로 주입 방어 — media id 는 숫자만(1차 리뷰 사소-7). 요청 자체가 없어야 한다."""
    account, source, post = threads_setup
    rec = Recorder({})
    rec.install(monkeypatch)
    with pytest.raises(FetchError):
        await ADAPTER.fetch_replies(
            source, "../me/threads?x=", account, since=datetime.now(UTC)
        )
    assert rec.requests == []


@db
async def test_fetch_replies_403_maps_rate_limited(threads_setup, monkeypatch):
    account, source, post = threads_setup
    rec = Recorder({
        ("GET", "/v1.0/900000009/replies"): (403, {"error": {"code": 10, "type": "Perm"}}),
    })
    rec.install(monkeypatch)
    with pytest.raises(RateLimitedError):
        await ADAPTER.fetch_replies(source, "900000009", account, since=datetime.now(UTC))
