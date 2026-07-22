"""조정 잡(reconcile) — 판정 로직 단위 + verify_pending 종결 플로우 통합(실 Postgres).

docs/M2-SEND-RECONCILIATION.md §3.4·§3.6, 체크리스트 R4.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app import reconcile
from app.auth import hash_password
from app.config import settings
from app.models import (
    MatchedPost,
    Platform,
    PostStatus,
    ReplyAction,
    ReplyActionLog,
    Role,
    SnsAccount,
    Source,
    SourceType,
    User,
)
CLAIM = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)


def _reply(text: str, *, username: str = "our_bot", offset_sec: int = 10):
    from app.sources.base import FetchedReply

    return FetchedReply(
        external_reply_id=f"r-{uuid.uuid4().hex[:8]}",
        username=username,
        text=text,
        timestamp=CLAIM + timedelta(seconds=offset_sec),
    )


# ── 판정 로직 단위 테스트 (DB 불필요) ─────────────────────────────────────────


def test_normalize_nfc_and_whitespace():
    # NFD("é" 분해형) ↔ NFC 동치 + 개행/연속 공백 축약 + trim
    assert reconcile.normalize("Café  안녕\n\n하세요 ") == "Café 안녕 하세요"


def test_is_our_reply_exact_match():
    body = "안녕하세요, 문의 주셔서 감사합니다. 자세한 내용은 프로필 링크를 참고해 주세요."
    assert reconcile.is_our_reply(_reply(body), "our_bot", CLAIM, body)


def test_is_our_reply_rejects_other_username_and_manual_reply():
    body = "안녕하세요, 문의 주셔서 감사합니다. 자세한 내용은 프로필 링크를 참고해 주세요."
    # 남의 답글(본문 동일해도 username 다름)
    assert not reconcile.is_our_reply(_reply(body, username="someone"), "our_bot", CLAIM, body)
    # 같은 계정의 수동 답글 혼입(본문 다름) — §3.6 오탐 방지
    assert not reconcile.is_our_reply(
        _reply("이건 사람이 직접 단 전혀 다른 내용의 답글입니다. 판정되면 안 됩니다."),
        "our_bot", CLAIM, body,
    )


def test_is_our_reply_timestamp_window():
    body = "안녕하세요, 문의 주셔서 감사합니다. 자세한 내용은 프로필 링크를 참고해 주세요."
    # 클레임보다 (스큐 여유 초과로) 이전에 달린 답글은 우리 것이 아니다
    old = _reply(body, offset_sec=-(reconcile.CLOCK_SKEW_SEC + 5))
    assert not reconcile.is_our_reply(old, "our_bot", CLAIM, body)
    # 스큐 여유 안쪽(클레임 직전)은 허용
    skewed = _reply(body, offset_sec=-(reconcile.CLOCK_SKEW_SEC - 5))
    assert reconcile.is_our_reply(skewed, "our_bot", CLAIM, body)


def test_is_our_reply_prefix_fallback_for_truncation():
    body = "플랫폼이 절단할 수 있는 충분히 긴 본문입니다. " * 5  # 40자 이상
    truncated = reconcile.normalize(body)[:80] + "…"
    assert reconcile.is_our_reply(_reply(truncated), "our_bot", CLAIM, body)


def test_is_our_reply_short_body_requires_exact():
    body = "감사합니다!"  # 40자 미만 — prefix 보조 판정 금지(오탐 방지)
    assert reconcile.is_our_reply(_reply("감사합니다!"), "our_bot", CLAIM, body)
    assert not reconcile.is_our_reply(_reply("감사합니다! 좋은 하루 되세요."), "our_bot", CLAIM, body)


def test_is_our_reply_url_after_prefix_survives_alteration():
    """URL 이 앞 40자 밖이면 플랫폼이 URL 을 변형(단축)해도 prefix 판정으로 발견된다."""
    body = "자세한 안내는 아래 링크에서 확인해 주세요. 항상 감사합니다. https://very.long.example/path?x=1"
    altered = "자세한 안내는 아래 링크에서 확인해 주세요. 항상 감사합니다. https://l.ink/abc"
    assert reconcile.is_our_reply(_reply(altered), "our_bot", CLAIM, body)


def test_is_our_reply_url_in_prefix_is_documented_miss():
    """URL 이 앞 40자 안에 있으면 변형 시 미탐 — §3.6 잔여 위험(문서화된 한계).
    이 미탐은 '미게시 판정→retry' 로 이어질 수 있어 FE '직접 확인 권장' 문구가 방어선."""
    body = "링크: https://very.long.example/path?x=12345 참고해 주세요. 감사합니다. 좋은 하루!"
    altered = "링크: https://l.ink/abc 참고해 주세요. 감사합니다. 좋은 하루!"
    assert not reconcile.is_our_reply(_reply(altered), "our_bot", CLAIM, body)


# ── 종결 플로우 통합 테스트 (실 Postgres) ─────────────────────────────────────

PASSWORD = "pw-test-1234"


class FakeVerifyAdapter:
    """fetch_replies 를 구현한 mock — 조정 잡 대상 어댑터."""

    can_write = True

    def __init__(self, replies=None, error: Exception | None = None):
        self.replies = replies or []
        self.error = error
        self.fetch_calls = 0

    async def fetch(self, source, since):
        return []

    async def send_reply(self, source, post, body, account) -> str:
        raise AssertionError("조정 잡은 절대 전송하지 않는다(불변식 ①)")

    async def fetch_replies(self, source, target_media_id, account, since):
        self.fetch_calls += 1
        if self.error is not None:
            raise self.error
        return self.replies


@pytest.fixture
async def verify_pending_match(api_client):
    """verify_pending 매칭 + threads 소스 + platform_username 있는 계정."""
    user = await User.create(
        email=f"t-{uuid.uuid4().hex[:10]}@test.local",
        password_hash=hash_password(PASSWORD), role=Role.reviewer,
    )
    account = await SnsAccount.create(
        user=user, platform=Platform.threads, display_name="봇계정",
        platform_username="our_bot",
    )
    source = await Source.create(
        user=user, type=SourceType.threads, config={"query": "키워드"}
    )
    body = "안녕하세요, 문의 주셔서 감사합니다. 자세한 내용은 프로필 링크를 참고해 주세요."
    match = await MatchedPost.create(
        source=source, external_post_id="target-media-1", content="본문",
        status=PostStatus.verify_pending,
        verify_meta={
            "target_media_id": "target-media-1", "claim_ts": CLAIM.isoformat(),
            "attempts": 0, "reviewer_id": user.id, "final_body": body,
            "sns_account_id": account.id,
        },
    )
    await match.fetch_related("source")
    yield match, user, account, body
    await ReplyActionLog.filter(matched_post_id=match.id).delete()
    await match.delete()
    await source.delete()
    await account.delete()
    await user.delete()


@pytest.mark.db
async def test_reconcile_found_settles_sent(verify_pending_match, monkeypatch):
    """게시 확인 → sent 회계(승인자 승계) + replied + verify_meta 청소 (§3.4-3)."""
    from app import sources as sources_registry

    match, user, account, body = verify_pending_match
    adapter = FakeVerifyAdapter(replies=[_reply("무관한 답글"), _reply(body)])
    monkeypatch.setitem(sources_registry._ADAPTERS, SourceType.threads, adapter)

    await reconcile.reconcile_post(match)

    await match.refresh_from_db()
    assert match.status == PostStatus.replied
    assert match.verify_meta is None
    sent = await ReplyActionLog.filter(matched_post_id=match.id, action=ReplyAction.sent)
    assert len(sent) == 1
    assert sent[0].reviewer_id == user.id           # 조정 잡이 아니라 승인자를 기록
    assert sent[0].final_body == body
    assert sent[0].external_reply_id.startswith("r-")


@pytest.mark.db
async def test_reconcile_not_found_until_max_attempts(verify_pending_match, monkeypatch):
    """미발견 반복 → attempts 증가, 상한 도달 시 미게시 판정 → failed + reviewing (§3.4-4)."""
    from app import sources as sources_registry

    match, user, account, body = verify_pending_match
    adapter = FakeVerifyAdapter(replies=[_reply("무관한 답글")])
    monkeypatch.setitem(sources_registry._ADAPTERS, SourceType.threads, adapter)
    monkeypatch.setattr(settings, "reconcile_max_attempts", 2)

    await reconcile.reconcile_post(match)
    await match.refresh_from_db()
    assert match.status == PostStatus.verify_pending
    assert match.verify_meta["attempts"] == 1

    await match.fetch_related("source")
    await reconcile.reconcile_post(match)
    await match.refresh_from_db()
    assert match.status == PostStatus.reviewing      # retry 재개
    assert match.verify_meta is None
    failed = await ReplyActionLog.filter(matched_post_id=match.id, action=ReplyAction.failed)
    assert len(failed) == 1 and "미게시 판정" in failed[0].error
    assert failed[0].reviewer_id == user.id


@pytest.mark.db
async def test_reconcile_partial_unique_race_recovers(verify_pending_match, monkeypatch):
    """이미 sent 행이 있으면(사람/이전 주기와 경합) DB partial unique 가 이중 sent 를
    차단하고, 조정 잡은 replied 로 정합만 회복한다 (불변식 ②, §3.4-3)."""
    from app import sources as sources_registry

    match, user, account, body = verify_pending_match
    await ReplyActionLog.create(
        matched_post=match, reviewer=user, final_body=body, action=ReplyAction.sent,
        external_reply_id="already-sent",
    )
    adapter = FakeVerifyAdapter(replies=[_reply(body)])
    monkeypatch.setitem(sources_registry._ADAPTERS, SourceType.threads, adapter)

    await reconcile.reconcile_post(match)

    await match.refresh_from_db()
    assert match.status == PostStatus.replied
    sent = await ReplyActionLog.filter(matched_post_id=match.id, action=ReplyAction.sent)
    assert len(sent) == 1 and sent[0].external_reply_id == "already-sent"


@pytest.mark.db
async def test_reconcile_fetch_failure_keeps_row(verify_pending_match, monkeypatch):
    """조회 실패(토큰 만료 등) → attempts 미증가·상태 유지 + 소스 health 회계로 가시화
    (FR-18, 1차 리뷰 F-3) — read-only 라 무한 반복해도 부작용 없음. 탈출구는 ignore (§3.4-5)."""
    from app.sources import FetchError
    from app import sources as sources_registry

    match, user, account, body = verify_pending_match
    adapter = FakeVerifyAdapter(error=FetchError("mock 조회 실패"))
    monkeypatch.setitem(sources_registry._ADAPTERS, SourceType.threads, adapter)

    await reconcile.reconcile_post(match)

    await match.refresh_from_db()
    assert match.status == PostStatus.verify_pending
    assert match.verify_meta["attempts"] == 0
    assert await ReplyActionLog.filter(matched_post_id=match.id).count() == 0
    source = await Source.get(id=match.source_id)
    assert source.health_status != "ok"  # 조용히 갇히지 않는다 — 배지로 노출


@pytest.mark.db
async def test_reconcile_respects_existing_backoff(verify_pending_match, monkeypatch):
    """활성 backoff 중에는 조회 자체를 하지 않는다 (불변식 ④, 설계 §3.4-5)."""
    from app import sources as sources_registry

    match, user, account, body = verify_pending_match
    adapter = FakeVerifyAdapter(replies=[_reply(body)])
    monkeypatch.setitem(sources_registry._ADAPTERS, SourceType.threads, adapter)
    await Source.filter(id=match.source_id).update(
        backoff_until=datetime.now(UTC) + timedelta(hours=1)
    )

    await reconcile.reconcile_post(match)

    assert adapter.fetch_calls == 0
    await match.refresh_from_db()
    assert match.status == PostStatus.verify_pending  # 다음 주기로 이월


@pytest.mark.db
async def test_reconcile_failure_preserves_concurrent_backoff(verify_pending_match, monkeypatch):
    """조회 대기 중 poller 가 건 backoff 를 stale 객체 회계가 지우지 않는다 (2차 리뷰 R-1)."""
    from app.sources import FetchError
    from app import sources as sources_registry

    match, user, account, body = verify_pending_match
    until = datetime.now(UTC) + timedelta(hours=1)

    class RacingAdapter(FakeVerifyAdapter):
        async def fetch_replies(self, source, target_media_id, account, since):
            # 조회 도중 poller 의 429 회계가 backoff 를 거는 상황 재현
            await Source.filter(id=source.id).update(backoff_until=until)
            raise FetchError("mock 조회 실패")

    monkeypatch.setitem(sources_registry._ADAPTERS, SourceType.threads, RacingAdapter())

    await reconcile.reconcile_post(match)

    source = await Source.get(id=match.source_id)
    assert source.backoff_until is not None  # poller 의 backoff 가 보존된다
    await match.refresh_from_db()
    assert match.status == PostStatus.verify_pending


@pytest.mark.db
async def test_reconcile_miss_with_sent_evidence_recovers_replied(
    verify_pending_match, monkeypatch
):
    """미게시 판정 직전이라도 DB 에 sent 증거(좀비 요청의 사후 audit 등)가 있으면
    retry 를 열지 않고 replied 정합 회복 (불변식 ②, 1차 리뷰 F-2)."""
    from app import sources as sources_registry

    match, user, account, body = verify_pending_match
    await ReplyActionLog.create(
        matched_post=match, reviewer=user, final_body=body, action=ReplyAction.sent,
        external_reply_id="zombie-sent",
    )
    adapter = FakeVerifyAdapter(replies=[_reply("판정에 안 걸리는 다른 텍스트")])
    monkeypatch.setitem(sources_registry._ADAPTERS, SourceType.threads, adapter)
    monkeypatch.setattr(settings, "reconcile_max_attempts", 1)

    await reconcile.reconcile_post(match)

    await match.refresh_from_db()
    assert match.status == PostStatus.replied      # reviewing(retry 개방) 아님
    assert match.verify_meta is None
    assert await ReplyActionLog.filter(
        matched_post_id=match.id, action=ReplyAction.failed
    ).count() == 0


@pytest.mark.db
async def test_reconcile_no_username_stays_for_human(verify_pending_match, monkeypatch):
    """판정 키(platform_username) 없음 → 자동 종결하지 않는다(이중 게시 위험) — 행 유지."""
    from app import sources as sources_registry

    match, user, account, body = verify_pending_match
    account.platform_username = None
    await account.save(update_fields=["platform_username"])
    adapter = FakeVerifyAdapter(replies=[_reply(body)])
    monkeypatch.setitem(sources_registry._ADAPTERS, SourceType.threads, adapter)

    await reconcile.reconcile_post(match)

    await match.refresh_from_db()
    assert match.status == PostStatus.verify_pending
    assert adapter.fetch_calls == 0  # 판정 불능이면 조회 자체를 하지 않는다


@pytest.mark.db
async def test_reconcile_checks_username_snapshot_when_live_swapped(verify_pending_match, monkeypatch):
    """판정 후보에 결과 불명 시점 스냅샷 포함(토큰 교체 API 3차 독립 리뷰 blocker) —
    조정 전에 자격증명이 다른 신원으로 교체(live username 변경)돼도 실제 게시를 찾는다.
    live 값만 썼다면 미발견→미게시 오판→retry→이중 게시가 됐을 시나리오다."""
    from app import sources as sources_registry

    match, user, account, body = verify_pending_match
    meta = dict(match.verify_meta)
    meta["platform_username"] = "our_bot"  # 전송 시도 시점의 신원 스냅샷
    await MatchedPost.filter(id=match.id).update(verify_meta=meta)
    # 조정 전에 토큰 교체로 계정의 live 신원이 바뀐 상황
    await SnsAccount.filter(id=account.id).update(platform_username="other_account")
    await match.refresh_from_db()
    await match.fetch_related("source")
    adapter = FakeVerifyAdapter(replies=[_reply(body)])  # 실제 게시된 답글(옛 신원)
    monkeypatch.setitem(sources_registry._ADAPTERS, SourceType.threads, adapter)

    await reconcile.reconcile_post(match)

    await match.refresh_from_db()
    assert match.status == PostStatus.replied


@pytest.mark.db
async def test_reconcile_checks_live_username_when_snapshot_stale(verify_pending_match, monkeypatch):
    """판정 후보에 live 값도 포함(4차 검증 리뷰 Medium) — 핸들 변경(rename)으로 스냅샷이
    stale 이고 /replies 가 과거 답글에 현재 핸들을 반환하는 계약이어도 실제 게시를 찾는다.
    스냅샷만 썼다면 미발견→미게시 오판→retry→이중 게시가 됐을 시나리오다."""
    from app import sources as sources_registry

    match, user, account, body = verify_pending_match
    meta = dict(match.verify_meta)
    meta["platform_username"] = "old_handle"  # 전송 시점 스냅샷 — 이후 rename 으로 stale
    await MatchedPost.filter(id=match.id).update(verify_meta=meta)
    await match.refresh_from_db()
    await match.fetch_related("source")
    # 실제 게시된 답글이 현재 핸들(our_bot=live 픽스처 값)로 조회되는 상황
    adapter = FakeVerifyAdapter(replies=[_reply(body)])
    monkeypatch.setitem(sources_registry._ADAPTERS, SourceType.threads, adapter)

    await reconcile.reconcile_post(match)

    await match.refresh_from_db()
    assert match.status == PostStatus.replied
