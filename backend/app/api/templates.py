"""/api/templates — 답변 템플릿 CRUD (admin, API-SPEC §템플릿)."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import require_admin, require_csrf
from app.api.schemas import PatchModel
from app.models import ReplyTemplate, User

router = APIRouter(
    prefix="/api/templates", tags=["templates"], dependencies=[Depends(require_admin)]
)


class TemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    body: str
    enabled: bool


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1)


class TemplatePatch(PatchModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    body: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None


@router.get("")
async def list_templates() -> list[TemplateOut]:
    return [TemplateOut.model_validate(t) for t in await ReplyTemplate.all().order_by("id")]


@router.post("", status_code=201, dependencies=[Depends(require_csrf)])
async def create_template(
    body: TemplateIn, admin: Annotated[User, Depends(require_admin)]
) -> TemplateOut:
    t = await ReplyTemplate.create(user=admin, name=body.name, body=body.body)
    return TemplateOut.model_validate(t)


@router.patch("/{template_id}", dependencies=[Depends(require_csrf)])
async def update_template(template_id: int, body: TemplatePatch) -> TemplateOut:
    t = await ReplyTemplate.get_or_none(id=template_id)
    if t is None:
        raise HTTPException(status_code=404, detail="템플릿이 없습니다")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(t, field, value)
    await t.save()
    return TemplateOut.model_validate(t)


@router.delete("/{template_id}", status_code=204, dependencies=[Depends(require_csrf)])
async def delete_template(template_id: int) -> None:
    if not await ReplyTemplate.filter(id=template_id).delete():
        raise HTTPException(status_code=404, detail="템플릿이 없습니다")
