"""/api/keywords — 키워드 CRUD (읽기=로그인 사용자, 쓰기=admin, API-SPEC §키워드).

regex 패턴은 저장 전 컴파일 검증(422). 매칭 자체는 poller(M1 후속)가 수행한다.
읽기 개방 근거는 `sources.py` 상단 참조 — 승인 화면의 키워드 열이 reviewer 에게
전부 `-` 로 뜨던 문제(이슈 #104).
"""
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import current_user, require_admin, require_csrf
from app.api.schemas import PatchModel
from app.models import Keyword, MatchType, Source, User

router = APIRouter(
    prefix="/api/keywords", tags=["keywords"], dependencies=[Depends(current_user)]
)


class KeywordOut(BaseModel):
    id: int
    pattern: str
    match_type: MatchType
    enabled: bool
    source_scope: int | None


class KeywordIn(BaseModel):
    pattern: str = Field(min_length=1, max_length=512)
    match_type: MatchType = MatchType.substring
    source_scope: int | None = None


class KeywordPatch(PatchModel):
    nullable_fields = frozenset({"source_scope"})  # null = 전체 소스로 스코프 해제

    pattern: str | None = Field(default=None, min_length=1, max_length=512)
    match_type: MatchType | None = None
    enabled: bool | None = None
    source_scope: int | None = None


def _out(k: Keyword) -> KeywordOut:
    return KeywordOut(
        id=k.id, pattern=k.pattern, match_type=k.match_type,
        enabled=k.enabled, source_scope=k.source_scope_id,
    )


def _validate_pattern(match_type: MatchType, pattern: str) -> None:
    if match_type != MatchType.regex:
        return
    try:
        re.compile(pattern)
    except re.error as exc:
        # exc.msg 만 사용 — str(exc)는 패턴 원문을 포함할 수 있다(검증 에러 no-echo 원칙).
        raise HTTPException(status_code=422, detail=f"regex 컴파일 실패: {exc.msg}") from exc


async def _resolve_scope(source_id: int | None) -> Source | None:
    if source_id is None:
        return None
    source = await Source.get_or_none(id=source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="source_scope 소스가 없습니다")
    return source


@router.get("")
async def list_keywords() -> list[KeywordOut]:
    return [_out(k) for k in await Keyword.all().order_by("id")]


@router.post("", status_code=201, dependencies=[Depends(require_csrf)])
async def create_keyword(
    body: KeywordIn, admin: Annotated[User, Depends(require_admin)]
) -> KeywordOut:
    _validate_pattern(body.match_type, body.pattern)
    k = await Keyword.create(
        user=admin, pattern=body.pattern, match_type=body.match_type,
        source_scope=await _resolve_scope(body.source_scope),
    )
    return _out(k)


@router.patch(
    "/{keyword_id}", dependencies=[Depends(require_admin), Depends(require_csrf)]
)
async def update_keyword(keyword_id: int, body: KeywordPatch) -> KeywordOut:
    k = await Keyword.get_or_none(id=keyword_id)
    if k is None:
        raise HTTPException(status_code=404, detail="키워드가 없습니다")
    changes = body.model_dump(exclude_unset=True)
    # 변경 결과 조합 기준으로 regex 재검증(패턴만 또는 타입만 바뀌는 경우 포함).
    _validate_pattern(
        changes.get("match_type", k.match_type), changes.get("pattern", k.pattern)
    )
    if "source_scope" in changes:
        changes["source_scope"] = await _resolve_scope(changes["source_scope"])
    for field, value in changes.items():
        setattr(k, field, value)
    await k.save()
    return _out(k)


@router.delete(
    "/{keyword_id}", status_code=204,
    dependencies=[Depends(require_admin), Depends(require_csrf)],
)
async def delete_keyword(keyword_id: int) -> None:
    if not await Keyword.filter(id=keyword_id).delete():
        raise HTTPException(status_code=404, detail="키워드가 없습니다")
