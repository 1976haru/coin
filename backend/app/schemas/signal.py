"""신호 스키마 — 체크리스트 #8 Shared Schemas.

전략·에이전트가 생성하는 모든 판단 객체의 공통 형식 정의.
필수 필드: action, confidence, reason, is_order_intent (기본 False, CLAUDE.md §2.3).

전략별 신호 클래스(StrategySignal, KimpSignal, PairSignal)는 정규 위치에 그대로
두되, 동일한 공통 필드를 갖도록 강제된다. 회귀 테스트는 tests/test_schemas.py 참조.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal


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
