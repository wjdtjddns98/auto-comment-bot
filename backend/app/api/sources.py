"""/api/sources — 소스 CRUD (admin, API-SPEC §소스)."""
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from tortoise.exceptions import IntegrityError

from app import poller
from app.api.deps import require_admin, require_csrf
from app.api.schemas import PatchModel
from app.models import HealthStatus, Source, SourceType, User
from app.sources import get_adapter

router = APIRouter(
    prefix="/api/sources", tags=["sources"], dependencies=[Depends(require_admin)]
)


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str | None
    type: SourceType
    config: dict[str, Any]
    poll_interval_sec: int
    enabled: bool
    last_success_at: datetime | None
    health_status: HealthStatus
    backoff_until: datetime | None


def _clean_name(v: str | None) -> str | None:
    """공백뿐인 이름은 미설정(None)으로 — FE 폴백(config 값 표시)이 동작하게."""
    if v is None:
        return None
    v = v.strip()
    return v or None


class SourceIn(BaseModel):
    # 표시용 이름(예: 커뮤니티 이름) — 선택. 없으면 FE 가 config 값(URL 등)으로 폴백.
    name: str | None = Field(default=None, max_length=100)
    type: SourceType
    config: dict[str, Any] = {}
    # 하한 60초: 예의 있는 수집(불변식 ④) — 소스에 초 단위 폴링을 걸 수 없게 한다.
    poll_interval_sec: int = Field(default=300, ge=60)


class SourcePatch(PatchModel):
    # name 은 명시적 null = "이름 제거"(config 폴백 표시로 복귀)가 유효한 값이다.
    nullable_fields = frozenset({"name"})

    name: str | None = Field(default=None, max_length=100)
    enabled: bool | None = None
    poll_interval_sec: int | None = Field(default=None, ge=60)
    config: dict[str, Any] | None = None


def _validate_config(source_type: SourceType, config: dict[str, Any]) -> dict[str, Any]:
    """타입별 config 스키마 검증 — 키 오타·형식 오류를 poller 시점이 아닌 등록/수정
    시점에 422 로 돌려준다. 정규화된 dict(예: HttpUrl→str)를 반환한다."""
    adapter = get_adapter(source_type)
    if adapter is None:
        # 어댑터 미구현 타입을 "정상(ok)"으로 보이게 등록만 되는 상태 방지(silent skip).
        raise HTTPException(
            status_code=422, detail=f"아직 지원되지 않는 소스 타입입니다: {source_type.value}"
        )
    try:
        model = adapter.config_model.model_validate(config)
    except ValidationError as exc:
        fields = "; ".join(
            f"config.{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}"
            for e in exc.errors()
        )
        raise HTTPException(
            status_code=422, detail=f"소스 설정이 올바르지 않습니다 — {fields}"
        ) from exc
    return model.model_dump(mode="json")


@router.get("")
async def list_sources() -> list[SourceOut]:
    return [SourceOut.model_validate(s) for s in await Source.all().order_by("id")]


@router.post("", status_code=201, dependencies=[Depends(require_csrf)])
async def create_source(
    body: SourceIn, admin: Annotated[User, Depends(require_admin)]
) -> SourceOut:
    s = await Source.create(
        user=admin, name=_clean_name(body.name), type=body.type,
        config=_validate_config(body.type, body.config),
        poll_interval_sec=body.poll_interval_sec,
    )
    return SourceOut.model_validate(s)


@router.patch("/{source_id}", dependencies=[Depends(require_csrf)])
async def update_source(source_id: int, body: SourcePatch) -> SourceOut:
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes:
        changes["name"] = _clean_name(changes["name"])
    # config 변경 시 + enabled=true 재활성화 시 검증한다 — 후자는 API 검증 도입 전에
    # 저장된 무효 config 소스를 그대로 켜서 poller 만 영구 실패하는 상태를 막는다(리뷰 #2).
    if "config" in changes or changes.get("enabled") is True:
        # 검증에 소스 타입(과 기존 config)이 필요 — UPDATE 전에 조회한다(없으면 404).
        s = await Source.get_or_none(id=source_id)
        if s is None:
            raise HTTPException(status_code=404, detail="소스가 없습니다")
        changes["config"] = _validate_config(s.type, changes.get("config", s.config or {}))
    # 부분 UPDATE 만 실행 — 전체 save() 는 poller 가 방금 갱신한 last_polled_at·
    # health_status 등을 stale 값으로 되돌리는 lost-update 를 만든다(적대 리뷰 H5).
    if changes:
        updated = await Source.filter(id=source_id).update(**changes)
        if not updated:
            raise HTTPException(status_code=404, detail="소스가 없습니다")
    s = await Source.get_or_none(id=source_id)
    if s is None:
        raise HTTPException(status_code=404, detail="소스가 없습니다")
    return SourceOut.model_validate(s)


@router.delete("/{source_id}", status_code=204, dependencies=[Depends(require_csrf)])
async def delete_source(source_id: int) -> None:
    try:
        deleted = await Source.filter(id=source_id).delete()
    except IntegrityError as exc:
        # RESTRICT: 매칭 이력(matched_posts) 또는 스코프된 키워드가 남아 있으면 삭제 불가.
        raise HTTPException(
            status_code=409,
            detail="매칭 이력 또는 스코프된 키워드가 있는 소스는 삭제할 수 없습니다"
            " — 키워드를 먼저 정리하거나 enabled=false 로 비활성화하세요",
        ) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="소스가 없습니다")
    poller.forget_source(source_id)
