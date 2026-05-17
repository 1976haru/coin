"""체크리스트 #8 Shared Schemas — 공통 base 모델 / 헬퍼.

본 모듈은 Pydantic v2 기반 신규 스키마들이 공유할 base 설정과 유틸을 둔다.
- `ConfiguredBaseModel`: 모든 신규 스키마의 부모. extra="forbid" 로 오타 차단.
- `utc_now`: 모든 스키마의 timestamp 기본값 생성기.
- `Money`: 거래 금액/가격/수량용 Decimal 별칭 (정밀도 보장).

기존 dataclass 기반 스키마(`market.py`, `signal.py` 등의 dataclass)는 본 base 를
사용하지 않으며 그대로 보존된다. 본 base 는 새 Pydantic 모델만 상속한다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict


def utc_now() -> datetime:
    """모든 스키마의 timestamp 기본 — tz-aware UTC."""
    return datetime.now(timezone.utc)


# 거래 수치(가격/수량/수수료)는 float 가 아닌 Decimal 로 표현 — 부동소수 오차 방지.
# pydantic v2 는 Decimal 입력으로 str/int/float/Decimal 모두 수용한다.
Money = Decimal

# 가독성을 위한 type alias (양수 강제는 각 모델에서 Field(gt=0) 으로 명시).
MoneyField = Annotated[Decimal, ...]


class ConfiguredBaseModel(BaseModel):
    """본 단계 모든 신규 스키마의 부모.

    설정:
      - `extra="forbid"`: 알지 못하는 필드 → ValidationError (오타 조기 발견).
      - `frozen=True`: 생성 후 변경 불가 (단일 진리 원칙).
      - `validate_assignment=True`: (해당 없음 — frozen 이므로 무의미하지만 명시.)
      - `populate_by_name=True`: alias / field name 모두 허용.
      - `arbitrary_types_allowed=False`: 일반 타입만.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )
