"""Tortoise 모델 — docs/ERD.md 를 그대로 구현.

enum은 CharEnumField로 자기문서화. 이중발송 방지 partial unique index는
Tortoise가 선언적으로 못 만들어 별도 마이그레이션(raw SQL)에서 추가한다.
"""
from enum import Enum

from tortoise import fields
from tortoise.models import Model


class Role(str, Enum):
    admin = "admin"
    reviewer = "reviewer"


class Platform(str, Enum):
    threads = "threads"
    naver = "naver"
    community = "community"


class AccountStatus(str, Enum):
    active = "active"
    expired = "expired"
    revoked = "revoked"


class SourceType(str, Enum):
    threads = "threads"
    naver_cafe = "naver_cafe"
    community = "community"


class HealthStatus(str, Enum):
    ok = "ok"
    degraded = "degraded"
    down = "down"


class MatchType(str, Enum):
    substring = "substring"
    regex = "regex"


class PostStatus(str, Enum):
    new = "new"
    reviewing = "reviewing"
    sending = "sending"
    replied = "replied"
    ignored = "ignored"
    # 전송 결과 불명 — 조정(reconcile) 대기. CAS 클레임(new/reviewing) 대상이 아니라
    # 재전송이 구조적으로 차단된다. ignore 만 허용(docs/M2-SEND-RECONCILIATION.md §3.1).
    verify_pending = "verify_pending"


class ReplyAction(str, Enum):
    approved = "approved"
    sent = "sent"
    failed = "failed"
    canceled = "canceled"
    unknown = "unknown"  # 전송 결과 불명 발생 audit (M2 조정 설계 §3.1)


class User(Model):
    id = fields.IntField(primary_key=True)
    email = fields.CharField(max_length=255, unique=True)
    password_hash = fields.CharField(max_length=255)
    role = fields.CharEnumField(Role, max_length=16)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "users"


class SnsAccount(Model):
    id = fields.IntField(primary_key=True)
    user = fields.ForeignKeyField("models.User", related_name="sns_accounts")
    platform = fields.CharEnumField(Platform, max_length=16)
    display_name = fields.CharField(max_length=255)
    # 플랫폼 계정 username — 조정 잡의 "우리 답글" 판정 키(live). 계정 등록 시점에 확보하고
    # write 어댑터가 **매 전송 직전** 갱신한다(threads.py, 2차 리뷰 중요-1). 조정은 이 live
    # 값과 verify_meta.platform_username 스냅샷을 둘 다 후보로 쓴다(reconcile.py).
    platform_username = fields.CharField(max_length=255, null=True)
    token_expires_at = fields.DatetimeField(null=True)
    status = fields.CharEnumField(AccountStatus, max_length=16, default=AccountStatus.active)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "sns_accounts"


class SnsAccountSecret(Model):
    # 1:1 분리 저장. read path는 이 테이블을 절대 로드하지 않는다 (MUST-FIX #3).
    account = fields.OneToOneField(
        "models.SnsAccount", related_name="secret", primary_key=True
    )
    encrypted_credentials = fields.BinaryField()
    key_version = fields.IntField(default=1)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "sns_account_secrets"


class Source(Model):
    id = fields.IntField(primary_key=True)
    user = fields.ForeignKeyField("models.User", related_name="sources")
    # 표시용 이름(예: 커뮤니티/카페 이름). 선택 — 없으면 FE 가 config 값(URL 등)으로 폴백.
    name = fields.CharField(max_length=100, null=True)
    type = fields.CharEnumField(SourceType, max_length=16)
    config = fields.JSONField()
    poll_interval_sec = fields.IntField(default=300)
    enabled = fields.BooleanField(default=True)
    last_polled_at = fields.DatetimeField(null=True)
    last_success_at = fields.DatetimeField(null=True)  # 백필 커서 (FR-5)
    health_status = fields.CharEnumField(HealthStatus, max_length=16, default=HealthStatus.ok)
    backoff_until = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "sources"
        indexes = (("enabled", "backoff_until"),)


class Keyword(Model):
    id = fields.IntField(primary_key=True)
    user = fields.ForeignKeyField("models.User", related_name="keywords")
    pattern = fields.CharField(max_length=512)
    match_type = fields.CharEnumField(MatchType, max_length=16, default=MatchType.substring)
    enabled = fields.BooleanField(default=True)
    # null = 전체 소스 대상. RESTRICT: 스코프된 키워드가 있는 소스는 삭제 불가
    # (소스 삭제가 키워드를 조용히 연쇄 삭제하지 않게).
    source_scope = fields.ForeignKeyField(
        "models.Source", related_name="keywords", null=True, on_delete=fields.RESTRICT
    )
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "keywords"


class MatchedPost(Model):
    id = fields.IntField(primary_key=True)
    # RESTRICT: 매칭 이력이 있는 소스는 삭제 불가(감사 보호) — 비활성화(enabled=False)가 정식 경로.
    source = fields.ForeignKeyField(
        "models.Source", related_name="matched_posts", on_delete=fields.RESTRICT
    )
    external_post_id = fields.CharField(max_length=512)  # 안정적 ID (FR-3)
    author = fields.CharField(max_length=255, null=True)
    url = fields.CharField(max_length=1024, null=True)
    content = fields.TextField()
    content_hash = fields.CharField(max_length=64, null=True)  # 편집 재매칭용
    # SET_NULL: 키워드가 지워져도 매칭 이력은 남는다.
    matched_keyword = fields.ForeignKeyField(
        "models.Keyword", related_name="matched_posts", null=True, on_delete=fields.SET_NULL
    )
    published_at = fields.DatetimeField(null=True)  # canonical 게시시각
    matched_at = fields.DatetimeField(auto_now_add=True)
    status = fields.CharEnumField(PostStatus, max_length=16, default=PostStatus.new)
    sending_claimed_at = fields.DatetimeField(null=True)  # sweep 회수용 (MUST-FIX #4)
    # 조정 재료(M2 조정 설계 §3.1) — CAS 클레임 시 기록, 종결 시 null 청소. 비밀 없음.
    # {target_media_id, claim_ts, attempts, reviewer_id, final_body, sns_account_id,
    #  container_id?, platform_username?(결과 불명 시점 판정 키 스냅샷 — 교체 오염 방지)}
    verify_meta = fields.JSONField(null=True)

    class Meta:
        table = "matched_posts"
        unique_together = (("source", "external_post_id"),)  # dedup (FR-2)
        indexes = (("status",), ("source_id", "matched_at"))


class ReplyTemplate(Model):
    id = fields.IntField(primary_key=True)
    user = fields.ForeignKeyField("models.User", related_name="templates")
    name = fields.CharField(max_length=255)
    body = fields.TextField()
    enabled = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "reply_templates"


class ReplyActionLog(Model):
    # append-only 감사 로그. matched_post당 action='sent' 최대 1건을
    # partial unique index로 강제 (별도 마이그레이션, MUST-FIX #1).
    id = fields.IntField(primary_key=True)
    # append-only 감사 보호: 부모 삭제로 이력이 증발하지 않게 RESTRICT/SET_NULL.
    matched_post = fields.ForeignKeyField(
        "models.MatchedPost", related_name="reply_actions", on_delete=fields.RESTRICT
    )
    # RESTRICT: 감사 행위자(user) 삭제로 이력이 증발하지 않게. 감사 이력이 있는 사용자는
    # 하드삭제 불가 — 향후 사용자 관리는 비활성화/소프트삭제 정책으로 간다.
    reviewer = fields.ForeignKeyField(
        "models.User", related_name="reply_actions", on_delete=fields.RESTRICT
    )
    template = fields.ForeignKeyField(
        "models.ReplyTemplate", related_name="reply_actions", null=True,
        on_delete=fields.SET_NULL,
    )
    final_body = fields.TextField()
    action = fields.CharEnumField(ReplyAction, max_length=16)
    sns_account = fields.ForeignKeyField(
        "models.SnsAccount", related_name="reply_actions", null=True,
        on_delete=fields.SET_NULL,
    )
    external_reply_id = fields.CharField(max_length=512, null=True)
    idempotency_key = fields.CharField(max_length=128, null=True)
    error = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "reply_actions"
        indexes = (("matched_post_id",),)
