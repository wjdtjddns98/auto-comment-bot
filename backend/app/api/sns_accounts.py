"""/api/sns-accounts — 본인 귀속 SNS 계정 셀프서비스 (API-SPEC §SNS 계정).

정책: 로그인 사용자 누구나 자기 계정을 연동/조회/삭제한다(계정 귀속).
admin 은 운영 파악용으로 전체 조회·삭제 가능. 자격증명은 쓰기 전용(write-only):
입력 즉시 Fernet 암호화해 sns_account_secrets 에만 저장하고, 어떤 응답에도
재노출하지 않는다. read path 는 secret 테이블을 조회조차 하지 않는 전용 스키마
(MUST-FIX #3, NFR-S1). M2 에서 OAuth 콜백 경로가 이 위에 얹힌다.
"""
import hashlib
import logging
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from app import crypto
from app.api.deps import current_user, require_csrf
from app.config import settings
from app.models import (
    AccountStatus,
    Platform,
    Role,
    SnsAccount,
    SnsAccountSecret,
    Source,
    User,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/sns-accounts", tags=["sns-accounts"],
    dependencies=[Depends(current_user)],
)


class SnsAccountOut(BaseModel):
    # 전용 read 스키마 — 여기 없는 필드는 구조적으로 응답에 실릴 수 없다.
    id: int
    user_id: int
    platform: Platform
    display_name: str
    status: AccountStatus
    token_expires_at: datetime | None


class SnsAccountIn(BaseModel):
    platform: Platform
    display_name: str = Field(min_length=1, max_length=255)
    credentials: dict[str, Any]


class CredentialsIn(BaseModel):
    # extra 금지: platform 등 미지 키를 조용히 무시하지 않는다(교체는 자격증명만 받는다)
    model_config = ConfigDict(extra="forbid")

    credentials: dict[str, Any]


def _validate_credentials(platform: Platform, credentials: dict[str, Any]) -> None:
    """플랫폼별 자격증명 형식 검증 — 등록 시점 422. 아무 JSON 이나 저장돼서 폴링/전송
    때에야 실패가 드러나는 상태 방지(소스 config 검증 #19 와 같은 원칙).
    주의: detail 에 입력값을 절대 echo 하지 않는다(불변식 ③)."""
    if platform == Platform.threads:
        token = credentials.get("access_token")
        if not isinstance(token, str) or not token.strip():
            raise HTTPException(
                status_code=422,
                detail="credentials.access_token: threads 계정은 액세스 토큰(문자열)이 필요합니다",
            )
    # naver/community: 어댑터 미구현 — 스키마 확정 시(M3) 여기에 추가한다.


async def _threads_profile(platform: Platform, credentials: dict[str, Any]) -> dict | None:
    """threads 자격증명으로 안정 식별자(upsert 키)를 best-effort 확보한다(R10①).

    **실패해도 등록/교체를 막지 않는다** — 무효 토큰은 이후 수집/전송 시점에 health
    회계로 드러나는 것이 기존 설계이고(replace_credentials 참조), 업스트림 일시 장애로
    계정 등록이 아예 불가해지는 편이 더 나쁘다. 식별자를 못 얻으면 예전처럼 NULL 로
    저장되고, 다음 교체/재연동이 다시 채울 기회를 갖는다.
    """
    if platform != Platform.threads:
        return None
    from app.sources.threads import ProfileUnavailable, fetch_profile

    token = credentials.get("access_token")
    if not isinstance(token, str) or not token.strip():
        return None  # 형식 검증이 이미 422 로 걸렀거나, threads 가 아닌 경로
    try:
        return await fetch_profile(token)
    except ProfileUnavailable as exc:
        # 토큰 값은 절대 로그에 싣지 않는다(불변식 ③) — 요약 메시지만.
        logger.warning("threads 프로필 확보 실패 — 식별자 없이 저장한다: %s", exc)
        return None


async def _adopt_same_identity(
    tx, *, user_id: int, profile: dict, ciphertext: str,
    expires_at: datetime | None, display_name: str | None,
) -> SnsAccount | None:
    """같은 (사용자, Threads 신원) 계정이 있으면 자격증명·표시정보를 갱신하고 반환한다.

    새 행을 만들지 않는 것이 핵심 — 소스 config 의 `sns_account_id` 참조가 유지된다
    (R10①). upsert 키는 안정 식별자이며 username 은 매 연동 갱신만 한다(High-5).
    호출자는 트랜잭션 안에서 쓴다.
    """
    # 같은 (사용자, 신원) 동시 연동 직렬화 — 행이 없을 때도 잠긴다(_identity_lock_key 참조)
    await tx.execute_query(
        "SELECT pg_advisory_xact_lock($1)", [_identity_lock_key(user_id, profile["user_id"])]
    )
    existing = (
        await SnsAccount.filter(
            user_id=user_id, platform=Platform.threads, platform_user_id=profile["user_id"]
        )
        .select_for_update()
        .first()
    )
    if existing is None:
        return None
    if not await SnsAccountSecret.filter(account_id=existing.id).update(
        encrypted_credentials=ciphertext
    ):
        # 결손 데이터(secret 없는 계정)도 여기서 복구한다
        await SnsAccountSecret.create(account=existing, encrypted_credentials=ciphertext)
    existing.status = AccountStatus.active
    existing.token_expires_at = expires_at
    existing.platform_username = profile["username"]
    if display_name:
        existing.display_name = display_name
    await existing.save(
        update_fields=["status", "token_expires_at", "platform_username", "display_name"]
    )
    return existing


def _out(a: SnsAccount) -> SnsAccountOut:
    return SnsAccountOut(
        id=a.id, user_id=a.user_id, platform=a.platform, display_name=a.display_name,
        status=a.status, token_expires_at=a.token_expires_at,
    )


def visible_accounts(user: User):
    """본인 것만. admin 은 전체(운영 파악·정리용)."""
    q = SnsAccount.all()
    return q if user.role == Role.admin else q.filter(user_id=user.id)


@router.get("")
async def list_accounts(user: Annotated[User, Depends(current_user)]) -> list[SnsAccountOut]:
    return [_out(a) for a in await visible_accounts(user).order_by("id")]


@router.post("", status_code=201, dependencies=[Depends(require_csrf)])
async def create_account(
    body: SnsAccountIn, user: Annotated[User, Depends(current_user)]
) -> SnsAccountOut:
    _validate_credentials(body.platform, body.credentials)
    try:
        ciphertext = crypto.encrypt_credentials(body.credentials)
    except (RuntimeError, ValueError) as exc:
        # 키 미설정(RuntimeError)/오형식(ValueError) — 자격증명을 평문으로 받아둘 수는 없다.
        # 예외 문자열은 응답에 싣지 않는다(고정 메시지).
        raise HTTPException(
            status_code=503,
            detail="자격증명 암호화 키가 설정되지 않았거나 형식이 잘못되었습니다 — 서버 설정 필요",
        ) from exc
    # 등록 시점에 안정 식별자를 확보한다 — 이게 없으면 같은 Threads 계정을 OAuth 로
    # 재연동할 때 upsert 키가 매칭되지 않아 별도 행이 생기고, 이 계정을 참조하던 소스
    # config 가 고아가 된다(R10① 실측: 계정 26→31→32 로 증식, 소스 110·119 수집 중단).
    profile = await _threads_profile(body.platform, body.credentials)
    try:
        async with in_transaction() as tx:
            if profile is not None:
                # 같은 신원 재등록이면 새 행 대신 자격증명 교체(소스 참조 보존).
                # 수동 등록은 토큰 수명을 모르므로 만료 시각은 비운다(OAuth 경로와의 차이).
                adopted = await _adopt_same_identity(
                    tx, user_id=user.id, profile=profile, ciphertext=ciphertext,
                    expires_at=None, display_name=body.display_name,
                )
                if adopted is not None:
                    return _out(adopted)
            # 생성 주체에게 자동 귀속 — 타인 명의 계정 등록 경로는 없다.
            account = await SnsAccount.create(
                user=user, platform=body.platform, display_name=body.display_name,
                platform_username=(profile["username"] if profile else None),
                platform_user_id=(profile["user_id"] if profile else None),
            )
            await SnsAccountSecret.create(account=account, encrypted_credentials=ciphertext)
    except IntegrityError as exc:
        # 부분 unique(신원) 백스톱 — 동시 등록 경합. 롤백으로 부분 반영 없음
        raise HTTPException(
            status_code=409, detail="계정 상태가 변경되었습니다 — 다시 시도해 주세요"
        ) from exc
    return _out(account)


@router.put(
    "/{account_id}/credentials", status_code=204, dependencies=[Depends(require_csrf)]
)
async def replace_credentials(
    account_id: int, body: CredentialsIn, user: Annotated[User, Depends(current_user)]
) -> None:
    """토큰 교체 — 삭제→재등록→소스 재연결 루프 제거(M2 백로그 ①).

    등록과 동일한 형식 검증(422)·암호화 실패 처리(503). 교체 성공 시 만료/회수 상태를
    active 로 복구한다 — 새 토큰이 유효하지 않으면 이후 수집/전송 시점에 다시 상태
    회계가 이뤄지므로 이전 상태를 남길 이유가 없다. platform 은 저장된 계정 값을 쓴다
    (경로로 platform 전환 불가)."""
    account = await visible_accounts(user).filter(id=account_id).first()
    if account is None:
        # 타인 계정은 존재 여부도 노출하지 않는다(404) — delete 와 동일 정책
        raise HTTPException(status_code=404, detail="SNS 계정이 없습니다")
    _validate_credentials(account.platform, body.credentials)
    try:
        ciphertext = crypto.encrypt_credentials(body.credentials)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="자격증명 암호화 키가 설정되지 않았거나 형식이 잘못되었습니다 — 서버 설정 필요",
        ) from exc
    # 교체 토큰으로도 식별자를 확보한다 — platform_user_id 가 NULL 인 기존 계정(수동
    # 등록분)이 여기서 채워져 이후 OAuth 재연동이 같은 행으로 수렴한다(R10① 자기 치유).
    profile = await _threads_profile(account.platform, body.credentials)
    try:
        async with in_transaction():
            # 락 순서 정렬(3차 독립 리뷰 중요): 부모(sns_accounts) 행을 먼저 잠근다 —
            # DELETE(부모 삭제 → cascade 자식)와 같은 parent→child 순서. 반대 순서
            # (secret 먼저)는 동시 삭제와 AB-BA 데드락(40P01)이 가능하고, 데드락 예외는
            # IntegrityError 로 변환되지 않아 500 이 된다(asyncpg 예외 계층 실확인).
            locked = await SnsAccount.filter(id=account.id).select_for_update().first()
            if locked is None:
                raise HTTPException(status_code=404, detail="SNS 계정이 없습니다")
            updated = await SnsAccountSecret.filter(account_id=locked.id).update(
                encrypted_credentials=ciphertext
            )
            if not updated:
                # 등록 경로는 secret 을 항상 만들지만, 결손 데이터도 교체가 복구한다(upsert)
                await SnsAccountSecret.create(
                    account=locked, encrypted_credentials=ciphertext
                )
            locked.status = AccountStatus.active
            locked.token_expires_at = None
            fields = ["status", "token_expires_at"]
            if profile is not None:
                # 같은 신원이 이미 다른 계정에 붙어 있으면 여기서 중단한다 — 부분 unique
                # 제약(uid_sns_accounts_identity)이 어차피 막지만, 409 로 무엇이 문제인지
                # 알려주는 편이 IntegrityError 일반 메시지보다 낫다. admin 이 타인 계정을
                # 교체할 수 있으므로 소유자 기준은 locked.user_id 다(요청자 아님).
                clash = (
                    await SnsAccount.filter(
                        user_id=locked.user_id, platform=Platform.threads,
                        platform_user_id=profile["user_id"],
                    )
                    .exclude(id=locked.id)
                    .exists()
                )
                if clash:
                    raise HTTPException(
                        status_code=409,
                        detail="같은 Threads 계정이 이미 다른 항목에 연결돼 있습니다 — 그 항목에서 교체해 주세요",
                    )
                locked.platform_user_id = profile["user_id"]
                locked.platform_username = profile["username"]
                fields += ["platform_user_id", "platform_username"]
            await locked.save(update_fields=fields)
    except IntegrityError as exc:
        # 동시 교체 경합의 잔여 창(부모 락 이후의 unique/FK 충돌) — 롤백으로 부분 반영 없음
        raise HTTPException(
            status_code=409, detail="계정 상태가 변경되었습니다 — 다시 시도해 주세요"
        ) from exc


@router.delete("/{account_id}", status_code=204, dependencies=[Depends(require_csrf)])
async def delete_account(
    account_id: int, user: Annotated[User, Depends(current_user)]
) -> None:
    """계정 삭제 — **그 계정을 참조하는 소스가 있으면 409**(R15).

    소스의 `config.sns_account_id` 는 JSON 필드라 FK 백스톱이 없다. 그래서 계정을 지우면
    소스가 조용히 고아가 되고, `enabled=true` 인데 매 틱 `FetchError: config.sns_account_id
    의 threads 계정을 찾을 수 없습니다` 만 남기며 수집이 멈춘다(2026-08-11 실측: 계정 26·31
    삭제로 소스 110·119 가 이 상태였다). `sources` 에 원인 컬럼이 없어(R17) DB 로는 진단도
    안 되므로, 삭제 시점에 막는 것이 가장 값싼 방어다.

    비활성 소스도 참조로 센다 — 나중에 다시 켜면 같은 고아 상태가 되기 때문이다.
    """
    account = await visible_accounts(user).filter(id=account_id).first()
    if account is None:
        # 타인 계정은 존재 여부도 노출하지 않는다(404) — 기존 정책 유지
        raise HTTPException(status_code=404, detail="SNS 계정이 없습니다")
    # config 가 JSONB 라 ORM 필터 대신 파이썬에서 검사한다 — 소스 수는 폴링 예산 상한
    # (계정당 7개, THREADS-APP-REVIEW §4)에 묶여 있어 전체 스캔이 무해하다.
    referring = [
        s.id
        for s in await Source.all().only("id", "config")
        if (s.config or {}).get("sns_account_id") == account_id
    ]
    if referring:
        ids = ", ".join(str(i) for i in sorted(referring))
        raise HTTPException(
            status_code=409,
            detail=f"이 계정을 사용하는 소스가 있어 삭제할 수 없습니다(소스 {ids})"
            " — 소스를 먼저 삭제하거나 다른 계정으로 변경해 주세요",
        )
    # secret 은 FK cascade 로 함께 삭제된다.
    if not await SnsAccount.filter(id=account_id).delete():
        raise HTTPException(status_code=404, detail="SNS 계정이 없습니다")


# ── Threads OAuth 연동 (동의 화면 기반 — 앱 심사 스크린캐스트 요건) ──────────────────

# state 저장소: user_id → (state, 만료 monotonic). in-memory 는 단일 워커 전제(NFR-P1)와
# 일치 — 재시작하면 진행 중이던 연동만 무효화된다(다시 연동하면 됨). 용도: 콜백 URL 로
# 노출되는 code 를 다른 로그인 사용자가 자기 세션에 제출하는 것 차단(1차 적대 리뷰
# High-3). **사용자당 미완료 state 1개**(재발급이 덮어씀) — 반복 발급으로 저장소가
# 무한 성장하는 self-DoS 차단(2차 리뷰 High-1), 크기는 사용자 수로 자연 상한.
_OAUTH_STATE_TTL_SEC = 600
_oauth_states: dict[int, tuple[str, float]] = {}


def _issue_oauth_state(user_id: int) -> str:
    state = secrets.token_urlsafe(32)
    _oauth_states[user_id] = (state, time.monotonic() + _OAUTH_STATE_TTL_SEC)
    return state


def _consume_oauth_state(state: str, user_id: int) -> bool:
    """1회용 소비 — 소유자 일치·미만료 시에만 소비한다. 타인이 유출 state 를 제출해도
    피해자의 정상 state 는 지워지지 않는다(2차 리뷰 Low-1 — 검증 후 pop)."""
    entry = _oauth_states.get(user_id)
    if (
        entry is None
        or not secrets.compare_digest(entry[0], state)
        or entry[1] < time.monotonic()
    ):
        return False
    _oauth_states.pop(user_id, None)
    return True


class ThreadsOAuthIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 인증 코드는 1회용·단수명 — 저장하지 않고 즉시 교환만 한다. 응답/로그 echo 금지.
    code: str = Field(min_length=1)
    state: str = Field(min_length=1)
    display_name: str | None = Field(default=None, max_length=255)


@router.get("/threads-oauth/authorize-url")
async def threads_authorize_url(user: Annotated[User, Depends(current_user)]) -> dict:
    """FE 가 사용자를 보낼 Threads 동의 화면 URL. 설정 미비면 503(등록 경로와 동일 원칙).

    state 는 서버 발급(사용자 바인딩·10분 TTL·1회용) — 콜백 페이지가 code 와 함께
    되돌려주고 POST 에서 검증된다. secret 포함 전체 설정을 검사한다(어느 하나라도
    없으면 어차피 교환이 불가 — 동의 화면까지 보내놓고 실패시키지 않는다).
    """
    from app.sources.threads import OAUTH_AUTHORIZE_URL, OAUTH_SCOPES

    if not (
        settings.threads_app_id and settings.threads_app_secret and settings.threads_redirect_uri
    ):
        raise HTTPException(
            status_code=503, detail="Threads OAuth 설정이 없습니다 — 서버 설정 필요"
        )
    query = urlencode(
        {
            "client_id": settings.threads_app_id,
            "redirect_uri": settings.threads_redirect_uri,
            "scope": OAUTH_SCOPES,
            "response_type": "code",
            "state": _issue_oauth_state(user.id),
        }
    )
    return {"url": f"{OAUTH_AUTHORIZE_URL}?{query}"}


def _identity_lock_key(user_id: int, platform_user_id: str) -> int:
    """트랜잭션 advisory lock 키 — 같은 (사용자, Threads 신원) 동시 연동 직렬화.
    select_for_update 는 '아직 없는 행'을 잠글 수 없어 동시 create 중복을 못 막는다
    (1차 적대 리뷰 High-4). 부분 unique(마이그레이션 6_)가 DB 백스톱."""
    digest = hashlib.sha256(f"{user_id}:threads:{platform_user_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


@router.post("/threads-oauth", status_code=201, dependencies=[Depends(require_csrf)])
async def threads_oauth_connect(
    body: ThreadsOAuthIn, user: Annotated[User, Depends(current_user)]
) -> SnsAccountOut:
    """인증 코드 → 장기 토큰 교환 → 계정 연동. 같은 신원(안정 user id) 재연동이면
    새 계정이 아니라 자격증명 교체(계정 id 보존 — 소스 config 참조 유지).

    수동 토큰 등록(POST /)과 대비: 동의 화면을 거치므로 토큰 수명(60일)·만료 시각을
    알고 저장한다. 교환 거부 400 / 업스트림 장애 502 — 전부 고정 메시지(echo 없음).
    """
    from app.sources.threads import (
        OAuthExchangeError,
        OAuthUpstreamError,
        oauth_exchange_code,
    )

    if not _consume_oauth_state(body.state, user.id):
        raise HTTPException(
            status_code=400,
            detail="연동 세션이 만료되었거나 유효하지 않습니다 — 다시 연동해 주세요",
        )
    try:
        result = await oauth_exchange_code(body.code)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503, detail="Threads OAuth 설정이 없습니다 — 서버 설정 필요"
        ) from exc
    except OAuthExchangeError as exc:
        # 요약(HTTP 상태/Meta code)은 서버 로그로만 — 응답은 고정 메시지.
        logger.warning("Threads OAuth 코드 교환 거부: %s", exc)
        raise HTTPException(
            status_code=400,
            detail="인증 코드가 유효하지 않거나 만료되었습니다 — 다시 연동해 주세요",
        ) from exc
    except OAuthUpstreamError as exc:
        logger.warning("Threads OAuth 업스트림 장애: %s", exc)
        # state 는 이미 소비됐고 코드도 소진됐을 수 있다 — 같은 요청 재시도가 아니라
        # 처음부터 재연동을 안내한다(2차 리뷰 M4 — 문구·실동작 정합).
        raise HTTPException(
            status_code=502,
            detail="Threads 연동 서버와 통신하지 못했습니다 — 처음부터 다시 연동해 주세요",
        ) from exc
    try:
        ciphertext = crypto.encrypt_credentials({"access_token": result["access_token"]})
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="자격증명 암호화 키가 설정되지 않았거나 형식이 잘못되었습니다 — 서버 설정 필요",
        ) from exc
    expires_at = (
        datetime.now(UTC) + timedelta(seconds=result["expires_in"])
        if result["expires_in"] > 0
        else None
    )
    username = result["username"][:255]
    platform_user_id = result["user_id"][:64]
    try:
        async with in_transaction() as tx:
            # 같은 신원 재연동이면 새 행 대신 자격증명 교체 — 수동 등록 경로와 같은 헬퍼를
            # 쓴다(두 경로가 같은 불변식을 지켜야 한다: 계정 id 보존 = 소스 참조 보존).
            existing = await _adopt_same_identity(
                tx, user_id=user.id,
                profile={"user_id": platform_user_id, "username": username},
                ciphertext=ciphertext, expires_at=expires_at,
                display_name=body.display_name,
            )
            if existing is not None:
                return _out(existing)
            account = await SnsAccount.create(
                user=user,
                platform=Platform.threads,
                display_name=body.display_name or username,
                platform_username=username,
                platform_user_id=platform_user_id,
                token_expires_at=expires_at,
            )
            await SnsAccountSecret.create(account=account, encrypted_credentials=ciphertext)
    except IntegrityError as exc:
        # 부분 unique(신원) 백스톱 등 잔여 경합 — 롤백으로 부분 반영 없음
        raise HTTPException(
            status_code=409, detail="계정 상태가 변경되었습니다 — 다시 시도해 주세요"
        ) from exc
    return _out(account)
