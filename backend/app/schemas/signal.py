"""신호 스키마 — 체크리스트 #8 Shared Schemas.

전략·에이전트가 생성하는 모든 판단 객체의 공통 형식 정의.
필수 필드: action, confidence, reason, is_order_intent (기본 False, CLAUDE.md §2.3).

전략별 신호 클래스(StrategySignal, KimpSignal, PairSignal)는 정규 위치에 그대로
두되, 동일한 공통 필드를 갖도록 강제된다. 회귀 테스트는 tests/test_schemas.py 참조.

본 모듈은 두 계층을 함께 제공한다:
  1. (legacy) `SignalBase` — frozen dataclass. 기존 strategies/* 와 호환.
  2. (new) `TradingSignal` — Pydantic v2 BaseModel. FastAPI 요청/응답 + validation.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import Field

from .common import ConfiguredBaseModel, utc_now
from .enums import AgentAction, TradingMode


Action = Literal[
    "BUY", "SELL", "HOLD", "CLOSE", "BLOCKED",
    "OPEN_REVERSE_KIMP",
    "OPEN_LONG_A_SHORT_B", "OPEN_SHORT_A_LONG_B",
]

Side = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class SignalBase:
    """모든 신호 객체의 공통 최소 형태.

    실제 전략 신호 클래스는 이 형태를 만족해야 한다 (덕 타이핑 + 회귀 테스트로 강제).
    is_order_intent=False 기본값은 안전 원칙상 필수 — 신호 자체는 주문 의도를
    내포하지 않는다.
    """

    action: str
    confidence: float
    reason: str
    is_order_intent: bool = False
    quality_score: float = 0.0


class TradingSignal(ConfiguredBaseModel):
    """전략/에이전트가 생성하는 매매 신호 (Pydantic v2).

    `confidence` 는 [0, 1] 범위 강제 — 신호 객체는 그 자체로 주문 의도를 갖지
    않는다 (`is_order_intent=False` 기본).
    """

    symbol:       str         = Field(..., min_length=1, description="대상 심볼")
    action:       AgentAction = Field(..., description="추천 액션")
    confidence:   float       = Field(..., ge=0.0, le=1.0,
                                      description="신뢰도 [0,1]")
    reason:       str         = Field(default="", description="신호 사유(설명)")
    trading_mode: TradingMode = Field(
        default=TradingMode.PAPER,
        description="신호 발생 환경. live 는 본 단계 비활성.",
    )
    is_order_intent: bool = Field(
        default=False,
        description="신호 자체가 주문 의도를 포함하는가 — 기본 False (CLAUDE.md §2.3)",
    )
    quality_score: float = Field(default=0.0, ge=0.0, le=100.0,
                                 description="신호 품질(0~100)")
    ts: datetime = Field(default_factory=utc_now, description="발생 시각 (UTC)")
