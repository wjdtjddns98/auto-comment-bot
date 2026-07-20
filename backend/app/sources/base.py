"""source 어댑터 공통 — 수집 계약 + 안정적 external_post_id (FR-3).

어댑터는 "읽기(fetch)"만 담당한다. 전송(write)은 M2 에서 별도 메서드로 추가하며,
그때도 사람 승인 없는 호출 경로는 만들지 않는다(불변식 ①).
"""
import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from urllib.parse import urlsplit

from app.models import Source


class RateLimitedError(Exception):
    """429/403 — poller 가 소스 단위 지수 backoff 를 건다 (FR-4)."""


class FetchError(Exception):
    """그 외 수집 실패(네트워크·파싱). health 갱신용."""


@dataclass(frozen=True)
class FetchedPost:
    external_post_id: str
    content: str
    author: str | None = None
    url: str | None = None
    published_at: datetime | None = None


class SourceAdapter(Protocol):
    can_write: bool

    async def fetch(self, source: Source, since: datetime | None) -> list[FetchedPost]:
        """since(=last_success_at 커서) 이후 글 목록. 없으면 전체 보존창."""
        ...


def normalize_url(url: str) -> str:
    """표면적 변형(스킴 http↔https, 대소문자 호스트, 쿼리스트링/프래그먼트, 말미 /) 제거."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    path = parts.path.rstrip("/")
    return f"{host}{path}"


def stable_external_id(
    url: str, author: str | None, published_at: datetime | None
) -> str:
    """FR-3: 재폴링·URL 변형에도 동일한 ID.

    timestamp 는 소스의 canonical 게시 절대시각만 사용하고,
    게시시각을 노출하지 않는 소스는 제외(URL+author)한다 (MUST-FIX #2).
    """
    ts = str(int(published_at.timestamp())) if published_at else ""
    raw = f"{normalize_url(url)}|{author or ''}|{ts}"
    return hashlib.sha256(raw.encode()).hexdigest()
