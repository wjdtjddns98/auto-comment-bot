"""source 어댑터 레지스트리."""
from app.models import SourceType
from app.sources.base import (  # noqa: F401 - 편의 re-export
    FetchedPost,
    FetchedReply,
    FetchError,
    RateLimitedError,
    SendError,
    SendOutcomeUnknown,
    SourceAdapter,
)
from app.sources.rss import RssAdapter

# threads/naver_cafe 어댑터는 후속 PR (Threads API·네이버 검색 OpenAPI)
_ADAPTERS: dict[SourceType, SourceAdapter] = {
    SourceType.community: RssAdapter(),
}


def get_adapter(source_type: SourceType) -> SourceAdapter | None:
    """어댑터 미구현 소스 타입이면 None (poller 가 skip)."""
    return _ADAPTERS.get(source_type)
