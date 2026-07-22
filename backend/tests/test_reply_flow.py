"""승인/전송 플로우 통합(실 Postgres) — CAS 이중발송 방지·audit·sweep.

PRD 테스트 게이트 ①(동시 approve 2건 → 정확히 1건 sent)·④(uvicorn workers=1)를 포함한다.
"""
import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app import reply
from app import sources as sources_registry
from app.auth import hash_password
from app.models import (
    AccountStatus,
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
from app.sources.base import SendError, SendOutcomeUnknown

pytestmark = pytest.mark.db

PASSWORD = "pw-test-1234"


class FakeWriteAdapter:
    """전송 가능한 mock 어댑터 — M1 은 실제 write 어댑터가 없어 게이트 검증용으로 주입."""

    can_write = True

    def __init__(self, *, fail: bool = False, unknown: bool = False, delay: float = 0.0):
        self.fail = fail
        self.unknown = unknown
        self.delay = delay
        self.send_calls = 0

    async def fetch(self, source, since):
        return []

    async def send_reply(self, source, post, body, account) -> str:
        self.send_calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.unknown:
            raise SendOutcomeUnknown("mock publish 응답 유실", container_id="mock-container-1")
        if self.fail:
            raise SendError("mock 전송 실패")
        return f"mock-reply-{post.id}-{self.send_calls}"


@pytest.fixture
async def reviewer_session(api_client):
    """reviewer 로그인 클라이언트 + CSRF 헤더 + 본인 User."""
    user = await User.create(
        email=f"t-{uuid.uuid4().hex[:10]}@test.local",
        password_hash=hash_password(PASSWORD),
        role=Role.reviewer,
    )
    await api_client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD})
    token = (await api_client.get("/api/auth/csrf")).json()["csrf_token"]
    yield api_client, {"X-CSRF-Token": token}, user
    await user.delete()


async def _make_match(user: User, source_type: SourceType = SourceType.community) -> MatchedPost:
    source = await Source.create(
        user=user, type=source_type, config={"rss_url": "https://ex.am/feed"}
    )
    return await MatchedPost.create(
        source=source, external_post_id=f"ext-{uuid.uuid4().hex}", content="본문"
    )


async def _make_threads_account(user: User) -> SnsAccount:
    """전송 소스 approve 는 SNS 계정 필수(1차 리뷰 F-4) — 테스트용 활성 threads 계정."""
    return await SnsAccount.create(
        user=user, platform=Platform.threads, display_name="전송봇",
        platform_username="our_bot",
    )


async def _cleanup(match: MatchedPost) -> None:
    await ReplyActionLog.filter(matched_post_id=match.id).delete()
    source_id = match.source_id
    await match.delete()
    await Source.filter(id=source_id).delete()


async def test_approve_readonly_source_records_approved(reviewer_session):
    """can_write=False(커뮤니티): 전송 없이 approved 기록 + clipboard_body 반환."""
    client, csrf, user = reviewer_session
    match = await _make_match(user)
    try:
        r = await client.post(
            f"/api/matches/{match.id}/approve", json={"final_body": "안녕하세요"}, headers=csrf
        )
        assert r.status_code == 200
        assert r.json() == {
            "action": "approved", "external_reply_id": None, "clipboard_body": "안녕하세요",
        }
        await match.refresh_from_db()
        assert match.status == PostStatus.replied
        logs = await ReplyActionLog.filter(matched_post_id=match.id)
        assert len(logs) == 1 and logs[0].action == ReplyAction.approved
        assert logs[0].reviewer_id == user.id and logs[0].final_body == "안녕하세요"

        # 완료된 매칭 재승인 → CAS 0행 → 409, 이력 추가 없음
        r = await client.post(
            f"/api/matches/{match.id}/approve", json={"final_body": "재승인"}, headers=csrf
        )
        assert r.status_code == 409
        assert await ReplyActionLog.filter(matched_post_id=match.id).count() == 1
    finally:
        await _cleanup(match)


async def test_approve_validation_does_not_touch_status(reviewer_session):
    """참조 검증 실패(422)는 CAS 클레임 전 — 매칭 상태가 변하지 않아야 한다."""
    client, csrf, user = reviewer_session
    match = await _make_match(user)
    other = await User.create(
        email=f"t-{uuid.uuid4().hex[:10]}@test.local",
        password_hash=hash_password(PASSWORD), role=Role.reviewer,
    )
    other_account = await SnsAccount.create(
        user=other, platform=Platform.threads, display_name="타인계정"
    )
    try:
        # 빈 본문 → 422
        r = await client.post(
            f"/api/matches/{match.id}/approve", json={"final_body": "   "}, headers=csrf
        )
        assert r.status_code == 422
        # 없는 템플릿 → 422
        r = await client.post(
            f"/api/matches/{match.id}/approve",
            json={"final_body": "본문", "template_id": 999999}, headers=csrf,
        )
        assert r.status_code == 422
        # 타인 SNS 계정 → 422 (존재 여부 비노출)
        r = await client.post(
            f"/api/matches/{match.id}/approve",
            json={"final_body": "본문", "sns_account_id": other_account.id}, headers=csrf,
        )
        assert r.status_code == 422
        # 만료/회수된 본인 계정 → 422
        revoked = await SnsAccount.create(
            user=user, platform=Platform.community, display_name="회수됨",
            status=AccountStatus.revoked,
        )
        r = await client.post(
            f"/api/matches/{match.id}/approve",
            json={"final_body": "본문", "sns_account_id": revoked.id}, headers=csrf,
        )
        assert r.status_code == 422
        await revoked.delete()
        # 소스 타입(community)과 계정 플랫폼(threads) 불일치 → 422
        mismatched = await SnsAccount.create(
            user=user, platform=Platform.threads, display_name="플랫폼불일치"
        )
        r = await client.post(
            f"/api/matches/{match.id}/approve",
            json={"final_body": "본문", "sns_account_id": mismatched.id}, headers=csrf,
        )
        assert r.status_code == 422
        await mismatched.delete()
        # 본문 상한(2000자) 초과 → 422
        r = await client.post(
            f"/api/matches/{match.id}/approve", json={"final_body": "a" * 2001}, headers=csrf
        )
        assert r.status_code == 422
        # CSRF 없이 → 403
        r = await client.post(f"/api/matches/{match.id}/approve", json={"final_body": "본문"})
        assert r.status_code == 403

        await match.refresh_from_db()
        assert match.status == PostStatus.new
        assert await ReplyActionLog.filter(matched_post_id=match.id).count() == 0
    finally:
        await _cleanup(match)
        await other_account.delete()
        await other.delete()


async def test_ignore_transitions(reviewer_session):
    client, csrf, user = reviewer_session
    match = await _make_match(user)
    try:
        r = await client.post(f"/api/matches/{match.id}/ignore", headers=csrf)
        assert r.status_code == 200 and r.json() == {"status": "ignored"}
        await match.refresh_from_db()
        assert match.status == PostStatus.ignored
        # 무시도 감사 이력에 남는다 (canceled)
        logs = await ReplyActionLog.filter(matched_post_id=match.id)
        assert len(logs) == 1 and logs[0].action == ReplyAction.canceled
        assert logs[0].reviewer_id == user.id
        # 무시된 매칭은 approve 불가(CAS 0행)
        r = await client.post(
            f"/api/matches/{match.id}/approve", json={"final_body": "본문"}, headers=csrf
        )
        assert r.status_code == 409
        # 이미 무시된 건 재무시도 409, 없는 매칭은 404
        assert (await client.post(f"/api/matches/{match.id}/ignore", headers=csrf)).status_code == 409
        assert (await client.post("/api/matches/999999/ignore", headers=csrf)).status_code == 404
    finally:
        await _cleanup(match)


async def test_approve_send_success_and_failure_then_retry(reviewer_session, monkeypatch):
    """can_write=True: 확정 실패 → 502 + failed 회계 + reviewing 복귀 → retry → sent."""
    client, csrf, user = reviewer_session
    adapter = FakeWriteAdapter(fail=True)
    monkeypatch.setitem(sources_registry._ADAPTERS, SourceType.threads, adapter)
    match = await _make_match(user, SourceType.threads)
    account = await _make_threads_account(user)
    body = {"final_body": "본문", "sns_account_id": account.id}
    try:
        # 전송 소스는 SNS 계정 필수 — 계정 없이 approve 는 클레임 전 422 (1차 리뷰 F-4)
        r = await client.post(
            f"/api/matches/{match.id}/approve", json={"final_body": "본문"}, headers=csrf
        )
        assert r.status_code == 422
        await match.refresh_from_db()
        assert match.status == PostStatus.new  # 상태 안 건드림

        # retry 는 reviewing 전용 — new 상태에서는 409
        r = await client.post(f"/api/matches/{match.id}/retry", json=body, headers=csrf)
        assert r.status_code == 409

        r = await client.post(f"/api/matches/{match.id}/approve", json=body, headers=csrf)
        assert r.status_code == 502
        assert r.json() == {"action": "failed", "detail": "답변 전송에 실패했습니다 — 재시도할 수 있습니다"}
        await match.refresh_from_db()
        assert match.status == PostStatus.reviewing  # 재시도 경로 확보
        failed = await ReplyActionLog.filter(matched_post_id=match.id, action=ReplyAction.failed)
        assert len(failed) == 1 and "mock 전송 실패" in failed[0].error

        adapter.fail = False
        r = await client.post(f"/api/matches/{match.id}/retry", json=body, headers=csrf)
        assert r.status_code == 200
        out = r.json()
        assert out["action"] == "sent" and out["external_reply_id"].startswith("mock-reply-")
        await match.refresh_from_db()
        assert match.status == PostStatus.replied
        actions = [a.action for a in await ReplyActionLog.filter(matched_post_id=match.id).order_by("id")]
        assert actions == [ReplyAction.failed, ReplyAction.sent]  # append-only 이력
    finally:
        await _cleanup(match)
        await account.delete()


async def test_concurrent_approve_exactly_one_sent(reviewer_session, monkeypatch):
    """테스트 게이트 ①: 동시 approve 2건 → 정확히 1건 sent, 나머지는 CAS 0행 → 409."""
    client, csrf, user = reviewer_session
    adapter = FakeWriteAdapter(delay=0.3)  # 전송 지연으로 두 요청의 시간창을 겹치게 한다
    monkeypatch.setitem(sources_registry._ADAPTERS, SourceType.threads, adapter)
    match = await _make_match(user, SourceType.threads)
    account = await _make_threads_account(user)
    try:
        r1, r2 = await asyncio.gather(
            client.post(
                f"/api/matches/{match.id}/approve",
                json={"final_body": "a", "sns_account_id": account.id}, headers=csrf,
            ),
            client.post(
                f"/api/matches/{match.id}/approve",
                json={"final_body": "b", "sns_account_id": account.id}, headers=csrf,
            ),
        )
        assert sorted([r1.status_code, r2.status_code]) == [200, 409]
        assert adapter.send_calls == 1  # 전송 자체가 1회만 일어났다
        sent = await ReplyActionLog.filter(matched_post_id=match.id, action=ReplyAction.sent)
        assert len(sent) == 1
        await match.refresh_from_db()
        assert match.status == PostStatus.replied
    finally:
        await _cleanup(match)
        await account.delete()


async def test_partial_unique_blocks_second_sent_row(reviewer_session):
    """DB 구조 보장(불변식 ②): 같은 매칭에 action='sent' 2행은 INSERT 자체가 불가."""
    from tortoise.exceptions import IntegrityError

    client, csrf, user = reviewer_session
    match = await _make_match(user)
    try:
        await ReplyActionLog.create(
            matched_post=match, reviewer=user, final_body="a", action=ReplyAction.sent
        )
        with pytest.raises(IntegrityError):
            await ReplyActionLog.create(
                matched_post=match, reviewer=user, final_body="b", action=ReplyAction.sent
            )
        # sent 외 action 은 여러 행 허용(append-only 이력)
        await ReplyActionLog.create(
            matched_post=match, reviewer=user, final_body="c", action=ReplyAction.failed
        )
    finally:
        await _cleanup(match)


async def test_sweep_branches_by_verify_meta(reviewer_session):
    """MUST-FIX #4 + M2 조정 설계 §3.3: 고착 sending 회수 분기 — 전송 착수분
    (verify_meta 有)은 verify_pending(결과 불명), 착수 전 잔재는 reviewing. 최근 클레임 보존."""
    client, csrf, user = reviewer_session
    stuck_safe = await _make_match(user)      # 전송 착수 전 크래시 잔재
    stuck_unknown = await _make_match(user)   # 전송 착수 후 크래시 잔재
    fresh = await _make_match(user)
    try:
        old = datetime.now(UTC) - timedelta(seconds=reply.SENDING_STALE_SEC + 60)
        await MatchedPost.filter(id=stuck_safe.id).update(
            status=PostStatus.sending, sending_claimed_at=old
        )
        await MatchedPost.filter(id=stuck_unknown.id).update(
            status=PostStatus.sending, sending_claimed_at=old,
            verify_meta={"target_media_id": "m1", "claim_ts": old.isoformat(),
                         "attempts": 0, "reviewer_id": user.id, "final_body": "본문",
                         "sns_account_id": None},
        )
        await MatchedPost.filter(id=fresh.id).update(
            status=PostStatus.sending, sending_claimed_at=datetime.now(UTC)
        )
        assert await reply.sweep_stuck_sending() == 2
        await stuck_safe.refresh_from_db()
        await stuck_unknown.refresh_from_db()
        await fresh.refresh_from_db()
        assert stuck_safe.status == PostStatus.reviewing
        assert stuck_unknown.status == PostStatus.verify_pending  # 이중 게시 창 차단
        assert fresh.status == PostStatus.sending
    finally:
        await _cleanup(stuck_safe)
        await _cleanup(stuck_unknown)
        await _cleanup(fresh)


async def test_send_timeout_goes_verify_pending(reviewer_session, monkeypatch):
    """send_reply 타임아웃 = 결과 불명(M2 조정 설계 §3.2 — 취소는 외부 사이드이펙트를
    중단시키지 않는다): unknown 회계 + verify_pending 전이, retry 는 열리지 않는다."""
    client, csrf, user = reviewer_session
    adapter = FakeWriteAdapter(delay=5.0)
    monkeypatch.setitem(sources_registry._ADAPTERS, SourceType.threads, adapter)
    monkeypatch.setattr(reply, "SEND_TIMEOUT_SEC", 0.1)
    match = await _make_match(user, SourceType.threads)
    account = await _make_threads_account(user)
    try:
        r = await client.post(
            f"/api/matches/{match.id}/approve",
            json={"final_body": "본문", "sns_account_id": account.id}, headers=csrf,
        )
        assert r.status_code == 502
        assert r.json()["action"] == "unknown"
        await match.refresh_from_db()
        assert match.status == PostStatus.verify_pending
        logs = await ReplyActionLog.filter(matched_post_id=match.id, action=ReplyAction.unknown)
        assert len(logs) == 1 and "타임아웃" in logs[0].error
        # 조정 재료가 남아 있어야 조정 잡이 판정할 수 있다
        assert match.verify_meta["target_media_id"] == match.external_post_id
        assert match.verify_meta["reviewer_id"] == user.id
        assert match.verify_meta["final_body"] == "본문"
        assert match.verify_meta["sns_account_id"] == account.id
        # 판정 키 스냅샷(토큰 교체 3차 리뷰 blocker) — 타임아웃 경로도 기록된다
        assert match.verify_meta["platform_username"] == "our_bot"
    finally:
        await _cleanup(match)
        await account.delete()


async def test_unknown_outcome_gates_and_ignore_escape(reviewer_session, monkeypatch):
    """SendOutcomeUnknown → verify_pending: approve/retry 는 409(재전송 구조 차단),
    ignore 만 허용(탈출구) + verify_meta 청소 (M2 조정 설계 §3.1)."""
    client, csrf, user = reviewer_session
    adapter = FakeWriteAdapter(unknown=True)
    monkeypatch.setitem(sources_registry._ADAPTERS, SourceType.threads, adapter)
    match = await _make_match(user, SourceType.threads)
    account = await _make_threads_account(user)
    body = {"final_body": "본문", "sns_account_id": account.id}
    try:
        r = await client.post(f"/api/matches/{match.id}/approve", json=body, headers=csrf)
        assert r.status_code == 502
        assert r.json() == {
            "action": "unknown",
            "detail": "전송 결과 확인 중 — 자동 조정 후 재시도 가능해집니다",
        }
        await match.refresh_from_db()
        assert match.status == PostStatus.verify_pending
        assert match.verify_meta["container_id"] == "mock-container-1"
        # 판정 키 스냅샷 — 이후 자격증명 교체(신원 교체)가 조정 판정을 오염시키지 않는다
        assert match.verify_meta["platform_username"] == "our_bot"

        # 결과 불명 상태에서 재전송 경로는 구조적으로 닫혀 있다(CAS 클레임 대상 아님)
        for path in ("approve", "retry"):
            r = await client.post(f"/api/matches/{match.id}/{path}", json=body, headers=csrf)
            assert r.status_code == 409, path
        assert adapter.send_calls == 1  # 전송은 1회뿐

        # ignore 는 허용 — 사람의 탈출구. verify_meta 도 청소된다.
        r = await client.post(f"/api/matches/{match.id}/ignore", headers=csrf)
        assert r.status_code == 200
        await match.refresh_from_db()
        assert match.status == PostStatus.ignored
        assert match.verify_meta is None
    finally:
        await _cleanup(match)
        await account.delete()


async def test_definite_failure_clears_verify_meta(reviewer_session, monkeypatch):
    """확정 실패(SendError)는 기존대로 reviewing 복귀 + retry 개방, 조정 재료는 청소."""
    client, csrf, user = reviewer_session
    adapter = FakeWriteAdapter(fail=True)
    monkeypatch.setitem(sources_registry._ADAPTERS, SourceType.threads, adapter)
    match = await _make_match(user, SourceType.threads)
    account = await _make_threads_account(user)
    try:
        r = await client.post(
            f"/api/matches/{match.id}/approve",
            json={"final_body": "본문", "sns_account_id": account.id}, headers=csrf,
        )
        assert r.status_code == 502 and r.json()["action"] == "failed"
        await match.refresh_from_db()
        assert match.status == PostStatus.reviewing
        assert match.verify_meta is None
    finally:
        await _cleanup(match)
        await account.delete()


async def test_zombie_approve_does_not_override_ignore(reviewer_session, monkeypatch):
    """펜싱(1차 리뷰 C1): sweep 회수 후 사람이 ignore 한 매칭을, 뒤늦게 완료된 원래
    approve 요청이 replied 로 덮어쓰지 못한다. 전송 사실 자체는 sent 로 audit 에 남는다."""
    client, csrf, user = reviewer_session
    adapter = FakeWriteAdapter(delay=0.5)
    monkeypatch.setitem(sources_registry._ADAPTERS, SourceType.threads, adapter)
    match = await _make_match(user, SourceType.threads)
    account = await _make_threads_account(user)
    try:
        approve_task = asyncio.create_task(
            client.post(
                f"/api/matches/{match.id}/approve",
                json={"final_body": "본문", "sns_account_id": account.id}, headers=csrf,
            )
        )
        await asyncio.sleep(0.2)  # 클레임(sending) 이후, 전송 완료 전
        # sweep 의 고착 회수를 재현(상태만 reviewing 으로 — claimed_at 은 그대로)
        assert await MatchedPost.filter(id=match.id, status=PostStatus.sending).update(
            status=PostStatus.reviewing
        ) == 1
        # 사람이 명시적으로 무시
        r = await client.post(f"/api/matches/{match.id}/ignore", headers=csrf)
        assert r.status_code == 200

        r = await approve_task
        assert r.status_code == 200 and r.json()["action"] == "sent"
        await match.refresh_from_db()
        assert match.status == PostStatus.ignored  # 사람의 결정이 이긴다
        actions = {
            a.action for a in await ReplyActionLog.filter(matched_post_id=match.id)
        }
        assert actions == {ReplyAction.canceled, ReplyAction.sent}  # 전송 사실은 기록 유지
    finally:
        await _cleanup(match)
        await account.delete()


async def test_zombie_does_not_override_active_reclaim(reviewer_session, monkeypatch):
    """펜싱 timestamp 조건 검증(2차 리뷰 M2): sweep 회수 후 재클레임(retry, 새 클레임
    시각 T2)이 진행 중일 때, 뒤늦게 완료된 T1 좀비가 T2 의 sending 상태를 덮어쓰지
    못한다 — status 체크만으로는 통과 못 하고 sending_claimed_at 비교가 있어야 한다."""
    client, csrf, user = reviewer_session
    adapter = FakeWriteAdapter(delay=1.0)
    monkeypatch.setitem(sources_registry._ADAPTERS, SourceType.threads, adapter)
    match = await _make_match(user, SourceType.threads)
    account = await _make_threads_account(user)
    try:
        zombie = asyncio.create_task(
            client.post(
                f"/api/matches/{match.id}/approve",
                json={"final_body": "T1", "sns_account_id": account.id}, headers=csrf,
            )
        )
        await asyncio.sleep(0.3)  # T1 클레임 후, 전송 완료 전
        # sweep 고착 회수 재현 → 사람이 retry 로 재클레임(T2, 전송 1.5초 진행)
        assert await MatchedPost.filter(id=match.id, status=PostStatus.sending).update(
            status=PostStatus.reviewing
        ) == 1
        adapter.delay = 1.5
        reclaim = asyncio.create_task(
            client.post(
                f"/api/matches/{match.id}/retry",
                json={"final_body": "T2", "sns_account_id": account.id}, headers=csrf,
            )
        )
        await asyncio.sleep(0.2)
        await match.refresh_from_db()
        assert match.status == PostStatus.sending  # T2 클레임 상태

        r1 = await zombie  # T1 이 T2 전송 도중 완료됨
        assert r1.status_code == 200 and r1.json()["action"] == "sent"
        await match.refresh_from_db()
        # 핵심: T1 의 fenced update 는 (status=sending 이지만 claimed_at=T2 라서) 0행 —
        # T2 의 진행 중 상태를 덮어쓰지 않는다
        assert match.status == PostStatus.sending

        r2 = await reclaim  # T2 는 sent 기록 시점에 partial unique 충돌 → 409 정합 회복
        assert r2.status_code == 409
        await match.refresh_from_db()
        assert match.status == PostStatus.replied
        sent = await ReplyActionLog.filter(matched_post_id=match.id, action=ReplyAction.sent)
        assert len(sent) == 1  # DB 는 끝까지 sent 1건만 허용(불변식 ②)
    finally:
        await _cleanup(match)
        await account.delete()


def test_uvicorn_single_worker_config():
    """테스트 게이트 ④: uvicorn 멀티워커 금지 — in-process 스케줄러 중복 실행 방지."""
    dockerfile = (Path(__file__).parent.parent / "Dockerfile").read_text(encoding="utf-8")
    cmd_lines = [line for line in dockerfile.splitlines() if line.startswith("CMD")]
    assert cmd_lines, "Dockerfile 에 CMD 가 없습니다"
    cmd = cmd_lines[-1]
    assert "uvicorn" in cmd
    # --workers 미지정(기본 1) 또는 명시적 1 만 허용
    assert "--workers" not in cmd or '"--workers", "1"' in cmd
