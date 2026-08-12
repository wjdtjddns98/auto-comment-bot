"""API 공용 스키마 베이스."""
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic_core import PydanticCustomError


class PatchModel(BaseModel):
    """PATCH 입력 베이스 — "필드 생략"(변경 없음)과 "명시적 null"을 구분한다.

    null 을 허용하지 않는 필드에 명시적 null 이 오면 ORM 단계 500 대신 422 로 거부.
    null 이 유효한 값인 필드는 서브클래스의 nullable_fields 에 선언한다.
    extra="forbid": 필드명 오타가 조용한 no-op 200 이 되지 않게 422 로 거부.
    """

    model_config = ConfigDict(extra="forbid")

    nullable_fields: ClassVar[frozenset[str]] = frozenset()

    @field_validator("*", mode="before")
    @classmethod
    def _reject_explicit_null(cls, value, info):
        if value is None and info.field_name not in cls.nullable_fields:
            raise PydanticCustomError(
                "none_not_allowed", "null 은 허용되지 않습니다 — 변경하지 않을 필드는 생략하세요"
            )
        return value
