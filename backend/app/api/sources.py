"""/api/sources — 소스 CRUD (admin, API-SPEC §소스)."""
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from app import poller
from app.api.deps import require_admin, require_csrf
from app.api.schemas import PatchModel
from app.models import (
    HealthStatus,
    Keyword,
    MatchedPost,
    PostStatus,
    ReplyAction,
    ReplyActionLog,
    Source,
    SourceType,
    User,
)
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
    """소스 삭제 — 수집물(매칭·비발송 감사 이력)과 스코프 키워드를 함께 정리한다.

    실발송 증거·조정 재료는 불가침(불변식 ②): sent **또는 unknown**(결과 불명 — 조정
    미게시 판정이 오판일 수 있어 §3.6 이 흔적 보존을 전제) 이력이 있거나 전송 진행 중
    (sending·verify_pending)인 매칭이 하나라도 있으면 409 — 비활성화가 정식 경로.
    비발송 이력(approved 등)까지 함께 지우는 것은 제품 결정(2026-07-22) — 테스트/
    오등록 소스의 원클릭 삭제 UX 를 감사 완전성보다 우선한다(전송이 개입한 이력 제외).
    """
    try:
        async with in_transaction():
            # 락 순서: Keyword → Source → MatchedPost (검증 리뷰 Medium). 키워드를
            # 잠그는 다른 경로들 — 키워드 삭제(SET NULL cascade 로 매칭 행 잠금)와
            # poller 매칭 INSERT 의 FK 검사(트리거 순서상 Keyword 가 Source 보다 먼저,
            # 마이그레이션 2_ 실측) — 과 순서를 맞춰 AB-BA 데드락(40P01→500)을 없앤다.
            scoped_kw_ids = [
                k.id
                for k in await Keyword.filter(source_scope_id=source_id).select_for_update()
            ]
            source = await Source.filter(id=source_id).select_for_update().first()
            if source is None:
                raise HTTPException(status_code=404, detail="소스가 없습니다")
            # 매칭 행 전부 잠금 — 동시 approve 의 CAS 클레임(new→sending)이 보호 검사와
            # 삭제 사이에 끼어드는 창을 닫는다(클레임 UPDATE 는 커밋까지 블록된 뒤
            # 행이 사라져 0행 → abort. 이중 게시·유령 전송 경로 없음).
            matches = await MatchedPost.filter(source_id=source_id).select_for_update()
            match_ids = [m.id for m in matches]
            in_flight = any(
                m.status in (PostStatus.sending, PostStatus.verify_pending) for m in matches
            )
            has_send_history = bool(match_ids) and await ReplyActionLog.filter(
                matched_post_id__in=match_ids,
                action__in=(ReplyAction.sent, ReplyAction.unknown),
            ).exists()
            if in_flight or has_send_history:
                raise HTTPException(
                    status_code=409,
                    detail="실발송·결과 불명 이력이 있거나 전송 진행 중인 매칭이 있는 소스는"
                    " 삭제할 수 없습니다(감사 보호) — enabled=false 비활성화가 정식 경로입니다",
                )
            # RESTRICT 3중(이력→매칭→소스) 순서대로 명시 삭제 — DB cascade 아님.
            # 관계 필터 삭제(matched_post__source_id)는 pg 에서 DELETE+JOIN 구문 오류라
            # 잠금 조회로 확보한 id 목록을 쓴다(id 집합은 락으로 고정돼 있어 정확).
            if match_ids:
                await ReplyActionLog.filter(matched_post_id__in=match_ids).delete()
                await MatchedPost.filter(id__in=match_ids).delete()
            # 잠근 스냅샷 id 로만 삭제 — 스냅샷 이후 커밋된 신규 스코프 키워드는 여기서
            # 잠그면 락 순서가 역전되므로(위 데드락 재유입) 남겨두고, 소스 삭제의
            # RESTRICT 백스톱 → IntegrityError → 409 재시도로 처리한다.
            if scoped_kw_ids:
                await Keyword.filter(id__in=scoped_kw_ids).delete()
            await Source.filter(id=source_id).delete()
    except IntegrityError as exc:
        # 백스톱: 신규 매칭 INSERT 는 Source FOR UPDATE 가 FK 검사(KEY SHARE)를 블록해
        # 구조적으로 못 끼어든다 — 남는 경합은 스냅샷 직후 커밋된 스코프 키워드 등
        # 드문 경우뿐이고, 롤백으로 부분 삭제 없이 재시도 안내로 수렴한다.
        raise HTTPException(
            status_code=409, detail="소스 상태가 변경되었습니다 — 다시 시도해 주세요"
        ) from exc
    poller.forget_source(source_id)
