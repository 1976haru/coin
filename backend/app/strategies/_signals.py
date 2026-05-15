"""공유 신호 타입 — 체크리스트 #29 / #30·#31·#32 분리 시 순환 import 회피.

전략 분리(#30/#31/#32) 과정에서 여러 전략이 같은 신호 클래스를 사용하므로
중립 위치에 둔다.
  - ``StrategySignal`` — SignalBase 호환 (체크리스트 #8). 추세/돌파에서 사용.
  - ``PairSignal``     — 페어트레이딩(#32) 전용. SignalBase 호환.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class StrategySignal:
    action: str          # BUY | SELL | HOLD | BLOCKED
    confidence: float    # 0.0 ~ 1.0
    reason: str
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    quality_score: float = 0.0   # 0~100 (SignalQualityAgent 입력)
    is_order_intent: bool = False  # CLAUDE.md §3.2

    def to_order(self, symbol: str, notional_usdt: float = 100.0) -> dict:
        return {
            "symbol": symbol,
            "side": self.action,
            "notional_usdt": notional_usdt,
            "price": self.entry_price,
            "confidence": self.confidence,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PairSignal:
    action: str          # OPEN_LONG_A_SHORT_B | OPEN_SHORT_A_LONG_B | CLOSE | HOLD | BLOCKED
    symbol_a: str
    symbol_b: str
    z_score: float
    hedge_ratio: float
    confidence: float
    reason: str
    is_order_intent: bool = False  # CLAUDE.md §3.2
