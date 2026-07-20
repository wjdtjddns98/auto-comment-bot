"""source 어댑터 공통 — 수집 계약 + 안정적 external_post_id (FR-3) + SSRF 가드.

어댑터는 "읽기(fetch)"만 담당한다. 전송(write)은 M2 에서 별도 메서드로 추가하며,
그때도 사람 승인 없는 호출 경로는 만들지 않는다(불변식 ①).
"""
import asyncio
import hashlib
import ipaddress
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit

from pydantic import BaseModel

from app.models import Source


class RateLimitedError(Exception):
    """429/403 — poller 가 소스 단위 지수 backoff 를 건다 (FR-4)."""

    def __init__(self, message: str, retry_after_sec: float | None = None):
        super().__init__(message)
        # 서버가 Retry-After 로 더 긴 대기를 요구하면 존중한다(예의 있는 수집).
        self.retry_after_sec = retry_after_sec


class FetchError(Exception):
    """그 외 수집 실패(네트워크·파싱·SSRF 차단). health 갱신용."""


class SendError(Exception):
    """답변 전송 실패 — approve 플로우가 failed 회계 + reviewing 복귀 후 502 로 변환한다."""


@dataclass(frozen=True)
class FetchedPost:
    external_post_id: str
    content: str
    author: str | None = None
    url: str | None = None
    published_at: datetime | None = None


class SourceAdapter(Protocol):
    can_write: bool
    # 타입별 config 스키마 — API 계층이 등록/수정 시점에 검증한다(extra="forbid" 권장).
    config_model: type[BaseModel]

    async def fetch(self, source: Source, since: datetime | None) -> list[FetchedPost]:
        """since(=last_success_at 커서) 이후 글 목록. 활용 여부는 어댑터 재량 —
        보존창 전체를 반환해도 안전하다(dedup 이 중복을 걸러준다)."""
        ...

    async def send_reply(self, source: Source, post, body: str, account) -> str:
        """승인된 답변 전송 → external_reply_id 반환. 실패는 SendError.

        can_write=False 어댑터에서는 절대 호출되지 않는다(approve 가 approved 기록으로
        분기). 호출 경로는 사람 승인(approve/retry) 엔드포인트뿐이다 — 불변식 ①."""
        ...


# 추적용 파라미터만 제거한다. 쿼리 전체 제거는 ?id=N 처럼 쿼리가 게시물 식별자인
# 게시판 URL 에서 서로 다른 글을 병합시키므로 금지(적대 리뷰 H2 실측).
# 접두사 매칭은 utm_ 에만 — "ref" 를 접두사로 쓰면 refid/referrer 같은 식별자까지
# 지워져 같은 버그가 재발한다(2차 리뷰 C1 실측).
_TRACKING_EXACT = frozenset({"ref", "fbclid", "gclid", "igshid", "share_id"})
_TRACKING_PREFIXES = ("utm_",)


def _is_tracking_param(key: str) -> bool:
    k = key.lower()
    return k in _TRACKING_EXACT or any(k.startswith(p) for p in _TRACKING_PREFIXES)


def normalize_url(url: str) -> str:
    """표면적 변형 제거: 스킴 http↔https, 호스트 대소문자, 추적 파라미터,
    프래그먼트, 말미 슬래시. 식별용 쿼리 파라미터는 정렬해 보존한다."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    path = parts.path.rstrip("/")
    kept = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking_param(k)
    )
    query = f"?{urlencode(kept)}" if kept else ""
    return f"{host}{path}{query}"


def stable_external_id(
    url: str, author: str | None, published_at: datetime | None
) -> str:
    """FR-3: 재폴링·URL 변형에도 동일한 ID.

    timestamp 는 소스의 canonical 게시 절대시각(published)만 사용하고,
    게시시각을 노출하지 않는 소스는 제외(URL+author)한다 (MUST-FIX #2).
    updated 류 가변 시각은 절대 섞지 않는다 — 편집만 돼도 ID 가 갈라진다.
    """
    ts = str(int(published_at.timestamp())) if published_at else ""
    raw = f"{normalize_url(url)}|{author or ''}|{ts}"
    return hashlib.sha256(raw.encode()).hexdigest()


def fit_external_id(raw_id: str) -> str:
    """모델 제약(512자) 초과 ID 는 절단 대신 해시로 고정 길이화(충돌 방지)."""
    if len(raw_id) <= 512:
        return raw_id
    return hashlib.sha256(raw_id.encode()).hexdigest()


async def assert_public_http_url(url: str) -> None:
    """SSRF 가드: http/https + userinfo 없음 + 해석된 모든 IP 가 공인 대역이어야 한다.

    앱 레벨 검증은 DNS TOCTOU 를 완전히 못 막는다 — prod 에서는 egress 차단(인프라)을
    병행한다(후속 이슈). 매 요청·매 리다이렉트 hop 마다 호출할 것.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise FetchError("허용되지 않는 URL 스킴")
    if parts.username or parts.password:
        raise FetchError("URL userinfo 불허")
    host = parts.hostname
    if not host:
        raise FetchError("호스트 없는 URL")
    try:
        ip = ipaddress.ip_address(host)
        addrs = [ip]
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, None)
        except OSError as exc:
            raise FetchError("호스트 해석 실패") from exc
        addrs = []
        for info in infos:
            try:
                addrs.append(ipaddress.ip_address(info[4][0]))
            except ValueError as exc:
                # fail-closed: 파싱 못 한 주소가 섞이면 통과시키지 않는다.
                raise FetchError("해석된 주소 형식 확인 실패") from exc
    if not addrs:
        raise FetchError("호스트 해석 실패")
    for addr in addrs:
        if not addr.is_global or addr.is_multicast:
            raise FetchError("사설/내부망 IP 로의 요청 차단(SSRF 가드)")
