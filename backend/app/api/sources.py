"""/api/sources — 소스 CRUD (admin, API-SPEC §소스)."""
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import require_admin, require_csrf
from app.models import HealthStatus, Source, SourceType, User

router = APIRouter(
    prefix="/api/sources", tags=["sources"], dependencies=[Depends(require_admin)]
)


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: SourceType
    config: dict[str, Any]
    poll_interval_sec: int
    enabled: bool
    last_success_at: datetime | None
    health_status: HealthStatus
    backoff_until: datetime | None


class SourceIn(BaseModel):
    type: SourceType
    config: dict[str, Any] = {}
    # 하한 60초: 예의 있는 수집(불변식 ④) — 소스에 초 단위 폴링을 걸 수 없게 한다.
    poll_interval_sec: int = Field(default=300, ge=60)


class SourcePatch(BaseModel):
    enabled: bool | None = None
    poll_interval_sec: int | None = Field(default=None, ge=60)
    config: dict[str, Any] | None = None


@router.get("")
async def list_sources() -> list[SourceOut]:
    return [SourceOut.model_validate(s) for s in await Source.all().order_by("id")]


@router.post("", status_code=201, dependencies=[Depends(require_csrf)])
async def create_source(
    body: SourceIn, admin: Annotated[User, Depends(require_admin)]
) -> SourceOut:
    s = await Source.create(
        user=admin, type=body.type, config=body.config,
        poll_interval_sec=body.poll_interval_sec,
    )
    return SourceOut.model_validate(s)


@router.patch("/{source_id}", dependencies=[Depends(require_csrf)])
async def update_source(source_id: int, body: SourcePatch) -> SourceOut:
    s = await Source.get_or_none(id=source_id)
    if s is None:
        raise HTTPException(status_code=404, detail="소스가 없습니다")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(s, field, value)
    await s.save()
    return SourceOut.model_validate(s)


@router.delete("/{source_id}", status_code=204, dependencies=[Depends(require_csrf)])
async def delete_source(source_id: int) -> None:
    if not await Source.filter(id=source_id).delete():
        raise HTTPException(status_code=404, detail="소스가 없습니다")
