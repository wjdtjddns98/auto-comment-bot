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


class ReplyAction(str, Enum):
    approved = "approved"
    sent = "sent"
    failed = "failed"
    canceled = "canceled"


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
    # null = 전체 소스 대상
    source_scope = fields.ForeignKeyField(
        "models.Source", related_name="keywords", null=True
    )
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "keywords"


class MatchedPost(Model):
    id = fields.IntField(primary_key=True)
    source = fields.ForeignKeyField("models.Source", related_name="matched_posts")
    external_post_id = fields.CharField(max_length=512)  # 안정적 ID (FR-3)
    author = fields.CharField(max_length=255, null=True)
    url = fields.CharField(max_length=1024, null=True)
    content = fields.TextField()
    content_hash = fields.CharField(max_length=64, null=True)  # 편집 재매칭용
    matched_keyword = fields.ForeignKeyField(
        "models.Keyword", related_name="matched_posts", null=True
    )
    published_at = fields.DatetimeField(null=True)  # canonical 게시시각
    matched_at = fields.DatetimeField(auto_now_add=True)
    status = fields.CharEnumField(PostStatus, max_length=16, default=PostStatus.new)
    sending_claimed_at = fields.DatetimeField(null=True)  # sweep 회수용 (MUST-FIX #4)

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
    matched_post = fields.ForeignKeyField("models.MatchedPost", related_name="reply_actions")
    reviewer = fields.ForeignKeyField("models.User", related_name="reply_actions")
    template = fields.ForeignKeyField(
        "models.ReplyTemplate", related_name="reply_actions", null=True
    )
    final_body = fields.TextField()
    action = fields.CharEnumField(ReplyAction, max_length=16)
    sns_account = fields.ForeignKeyField(
        "models.SnsAccount", related_name="reply_actions", null=True
    )
    external_reply_id = fields.CharField(max_length=512, null=True)
    idempotency_key = fields.CharField(max_length=128, null=True)
    error = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "reply_actions"
        indexes = (("matched_post_id",),)
