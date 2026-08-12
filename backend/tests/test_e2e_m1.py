"""M1 e2e 수용 기준 (PRD §8) — 실 Postgres + 실 HTTP API 전 구간.

로그인 → 소스 등록 → (poller) 수집·매칭 → 대시보드 표시 → 승인 → (mock)send
→ reply_actions 에 action='sent' audit 1건.

mock 경계는 딱 두 곳 — 나머지는 전부 실경로다:
- RSS 네트워크 GET(`rss._get_with_limits`): 외부 피드 대신 고정 XML 반환.
  feedparser 파싱·키워드 매칭·dedup·커서 전진은 실제 코드가 수행한다.
- 전송 어댑터: M1 은 실제 write 어댑터가 없어(PRD 의 "(mock)send") can_write=True
  mock 으로 교체하되 fetch 는 실제 RssAdapter 에 위임 — CAS 클레임·audit·상태
  전이는 실제 approve 경로가 수행하고, 수집 단계 자동 전송 0회(불변식 ①)를
  send_calls 카운터로 직접 관찰한다.
"""
import uuid

import pytest

from app import poller
from app import sources as sources_registry
from app.auth import hash_password
from app.models import (
    Keyword,
    MatchedPost,
    Platform,
    ReplyAction,
    ReplyActionLog,
    Role,
    SnsAccount,
    Source,
    SourceType,
    User,
)
from app.sources import rss

pytestmark = pytest.mark.db

PASSWORD = "pw-e2e-1234"
RSS_URL = "https://feed.e2e.example/rss"
FINAL_BODY = "안녕하세요! 급여량은 체중 기준으로 안내드려요."

# 두 번째 item 은 다른 테스트의 전역 키워드와도 우발 매칭되지 않게 일반 문구만 사용
_RSS_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>e2e 피드</title><link>https://feed.e2e.example/</link>
  <item>
    <title>강아지 간식 질문</title>
    <link>https://feed.e2e.example/posts/1</link>
    <guid>e2e-guid-{token}</guid>
    <description>{token} 급여량이 궁금해요. 하루에 얼마나 줘야 하나요?</description>
  </item>
  <item>
    <title>매칭되면 안 되는 글</title>
    <link>https://feed.e2e.example/posts/2</link>
    <guid>e2e-guid-{token}-nomatch</guid>
    <description>관련 없는 일반 게시글입니다.</description>
  </item>
</channel></rss>"""


class MockWriteAdapter:
    """M1 mock 전송 어댑터 — send_reply 만 흉내내고 fetch 는 실제 RssAdapter 위임."""

    can_write = True
    config_model = rss.RssConfig

    def __init__(self):
        self.send_calls = 0
        self._rss = rss.RssAdapter()

    async def fetch(self, source, since):
        return await self._rss.fetch(source, since)

    async def send_reply(self, source, post, body, account) -> str:
        self.send_calls += 1
        return f"mock-reply-{post.id}"


async def test_m1_acceptance_flow(api_client, monkeypatch):
    token = f"누띠e2e{uuid.uuid4().hex[:8]}"
    admin = await User.create(
        email=f"e2e-{uuid.uuid4().hex[:10]}@test.local",
        password_hash=hash_password(PASSWORD),
        role=Role.admin,
    )
    adapter = MockWriteAdapter()
    monkeypatch.setitem(sources_registry._ADAPTERS, SourceType.community, adapter)
    # 전송 소스 approve 는 SNS 계정 필수(M2) — mock write 어댑터도 동일 계약을 탄다
    account = await SnsAccount.create(
        user=admin, platform=Platform.community, display_name="e2e봇",
        platform_username="e2e_bot",
    )
    source_id = keyword_id = match_id = None
    try:
        # 1) 로그인 + CSRF — 이후 모든 호출은 세션 쿠키 기반 실 인증 경로
        resp = await api_client.post(
            "/api/auth/login", json={"email": admin.email, "password": PASSWORD}
        )
        assert resp.status_code == 200, resp.text
        csrf = {"X-CSRF-Token": (await api_client.get("/api/auth/csrf")).json()["csrf_token"]}

        # 2) 소스 등록 (community/RSS) — 타입별 config 검증 포함 실 API
        resp = await api_client.post(
            "/api/sources",
            json={"type": "community", "config": {"rss_url": RSS_URL}},
            headers=csrf,
        )
        assert resp.status_code == 201, resp.text
        created = resp.json()
        source_id = created["id"]
        assert created["config"]["rss_url"] == RSS_URL

        # 3) 키워드 등록 — 이 소스에만 스코프(공유 DB 오염 방지)
        resp = await api_client.post(
            "/api/keywords",
            json={"pattern": token, "match_type": "substring", "source_scope": source_id},
            headers=csrf,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["source_scope"] == source_id
        keyword_id = resp.json()["id"]

        # 4) poller 수집 — 네트워크 GET 만 mock, 파싱→매칭→dedup 저장은 실경로
        feed_xml = _RSS_TEMPLATE.format(token=token).encode()
        fetched_urls: list[str] = []

        async def fake_get(url):
            fetched_urls.append(url)
            return feed_xml, url

        monkeypatch.setattr(rss, "_get_with_limits", fake_get)
        source = await Source.get(id=source_id)
        stored = await poller.poll_source(source)
        assert stored == 1  # 키워드 포함 글만 매칭 저장
        assert fetched_urls == [RSS_URL]  # 등록한 config 의 URL 로 실제 수집

        # store 성공 → 커서 전진 + health ok (FR-5)
        await source.refresh_from_db()
        assert source.last_success_at is not None
        assert source.health_status == "ok"

        # 재수집 시 dedup — 같은 guid 재투입돼도 매칭은 1건 유지
        # (poll_source 는 due-time 을 보지 않으므로 바로 재호출하면 된다)
        await poller.poll_source(source)
        assert await MatchedPost.filter(source_id=source_id).count() == 1

        # 5) 매칭 대시보드 표시 — 목록 API 에 status=new 로 노출
        resp = await api_client.get(f"/api/matches?source_id={source_id}&status=new")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] == 1
        match = data["items"][0]
        match_id = match["id"]
        assert token in match["content"]
        assert match["matched_keyword_id"] == keyword_id

        # 수집·대시보드 단계에서 전송이 일어나지 않았다 — 불변식 ①(자동 게시 금지)
        assert adapter.send_calls == 0

        # 6) 승인 → (mock)send — CAS 클레임·타임아웃·audit 은 실제 approve 경로
        resp = await api_client.post(
            f"/api/matches/{match_id}/approve",
            json={"final_body": FINAL_BODY, "sns_account_id": account.id},
            headers=csrf,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["action"] == "sent"
        assert body["external_reply_id"] == f"mock-reply-{match_id}"
        assert adapter.send_calls == 1

        # 7) audit — reply_actions 에 sent 정확히 1건 + 상태 replied
        resp = await api_client.get(f"/api/matches/{match_id}")
        assert resp.status_code == 200, resp.text
        detail = resp.json()
        assert detail["status"] == "replied"
        sent_actions = [a for a in detail["reply_actions"] if a["action"] == "sent"]
        assert len(sent_actions) == 1
        assert sent_actions[0]["external_reply_id"] == f"mock-reply-{match_id}"
        assert sent_actions[0]["reviewer_user_id"] == admin.id

        # audit 행 자체도 검증 — 누가/무엇을 승인했는지 그대로 남는다
        log = await ReplyActionLog.get(matched_post_id=match_id, action=ReplyAction.sent)
        assert log.reviewer_id == admin.id
        assert log.final_body == FINAL_BODY

        # 재승인 시도 → 409 + 전송 어댑터 추가 호출 없음 — 불변식 ②(이중 발송 금지)
        resp = await api_client.post(
            f"/api/matches/{match_id}/approve",
            json={"final_body": "중복 시도", "sns_account_id": account.id},
            headers=csrf,
        )
        assert resp.status_code == 409
        assert adapter.send_calls == 1
        assert await ReplyActionLog.filter(matched_post_id=match_id, action="sent").count() == 1
    finally:
        if match_id is not None:
            await ReplyActionLog.filter(matched_post_id=match_id).delete()
        if source_id is not None:
            await MatchedPost.filter(source_id=source_id).delete()
        if keyword_id is not None:
            await Keyword.filter(id=keyword_id).delete()
        if source_id is not None:
            poller.forget_source(source_id)
            await Source.filter(id=source_id).delete()
        await account.delete()
        await admin.delete()
