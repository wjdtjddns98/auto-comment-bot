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
    # 플랫폼의 **안정 식별자**(Threads user id) — OAuth upsert 키. username 은 변경/탈취
    # 가능이라 식별자로 쓰지 않는다(OAuth 1차 적대 리뷰 High-5). 수동 토큰 등록 행은 null.
    # (user_id, platform, platform_user_id) 부분 unique 는 마이그레이션 6_ 참조.
    platform_user_id = fields.CharField(max_length=64, null=True)
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
    # null = 전체 소스 대상. RESTRICT: DB 레벨 백스톱 — 소스 삭제는 API 경로에서만
    # 스코프 키워드를 함께 정리한다(sources.py, 제품 결정 2026-07-22). 전역 키워드는 무관.
    source_scope = fields.ForeignKeyField(
        "models.Source", related_name="keywords", null=True, on_delete=fields.RESTRICT
    )
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "keywords"


class MatchedPost(Model):
    id = fields.IntField(primary_key=True)
    # RESTRICT: DB 레벨 백스톱 — 소스 삭제 API 가 명시 순서(이력→매칭→소스)로만 지울 수
    # 있게 한다. **전송 진행 중**(sending·verify_pending) 매칭이 있으면 API 가 409
    # (정합성 보호 — sources.py). 발송 이력 보존 목적의 409 는 제거됐다(2026-08-11).
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
        # external_post_id 단독 인덱스(R16): approve 가 "같은 플랫폼 글에 이미 보냈나"를
        # 소스 무관하게 조회한다. unique_together 는 (source_id, …) 가 선두라 이 조회에
        # 쓰이지 않는다.
        indexes = (("status",), ("source_id", "matched_at"), ("external_post_id",))


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
    # 주의(어댑터 2차 리뷰 R-5): 사용자 삭제 기능을 도입한다면 이 RESTRICT 만으로는
    # 부족하다 — matched_posts.verify_meta.reviewer_id 는 FK 가 아니어서, 감사 이력이
    # 아직 없는 승인자(크래시 잔재 verify_pending)가 삭제되면 조정 잡의 승계 기록이
    # IntegrityError 로 영구 반복된다. 도입 시 verify_pending 참조 검사를 함께 넣을 것.
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
