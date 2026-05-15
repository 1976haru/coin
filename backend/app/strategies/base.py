"""Strategy 공통 인터페이스 — 체크리스트 #29 StrategyBase.

각 전략 클래스가 따라야 할 메타데이터·contract 를 정의한다. 기존 전략 클래스
(TrendFollowingStrategy / VolatilityBreakoutStrategy / PairTradingStrategy /
KimpMeanReversionStrategy) 는 동작 변경 없이 ``capability`` 클래스 속성만
선언해 본 contract 에 합류한다.

설계 원칙 (CLAUDE.md):
  - Strategy 는 신호만 생성. 주문/체결은 OrderGateway 경유 (모듈 경계).
  - 모든 신호는 ``is_order_intent: bool = False`` 기본 — 안전 체인 외에서는 주문 의도 없음.
  - 본 모듈은 brokers/execution 을 import 하지 않는다 (모듈 경계 회귀로 강제).

`StrategyBase` 는 Protocol 로 정의되어 기존 클래스가 inheritance 변경 없이
duck typing 으로 만족할 수 있게 한다.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Protocol, runtime_checkable


# ── Capability ───────────────────────────────────────────────────

@dataclass(frozen=True)
class StrategyCapability:
    """전략의 기능·요구사항·출력 카탈로그.

    Attributes
    ----------
    name:
        전략 고유 식별자 (snake_case). registry 키로 사용.
    description:
        한 줄 설명.
    required_inputs:
        ``generate*`` 호출 시 필요한 입력 키 이름 (예: ("closes", "adx")).
        외부 호출자가 어떤 데이터를 준비해야 하는지 알리기 위한 hint.
    signal_actions:
        가능한 ``action`` 값 집합 (예: ("BUY", "SELL", "HOLD", "BLOCKED")).
    supports_pair:
        두 심볼 (A, B) 페어를 요구하는지 (페어트레이딩).
    supports_kimp:
        김프/역김프 입력 (upbit/okx 가격, 환율) 을 사용하는지.
    supports_futures:
        선물 시장 가정 — 체크리스트 Phase 8 후순위.
    output_signal_class:
        반환 타입 이름 (StrategySignal / KimpSignal / PairSignal).
    """

    name: str
    description: str
    required_inputs: tuple[str, ...]
    signal_actions: tuple[str, ...]
    supports_pair: bool = False
    supports_kimp: bool = False
    supports_futures: bool = False
    output_signal_class: str = "StrategySignal"

    def to_dict(self) -> dict:
        return asdict(self)


# ── Protocol ─────────────────────────────────────────────────────

@runtime_checkable
class StrategyBase(Protocol):
    """전략 공통 contract.

    구현은 ``capability`` 속성과 generate* 메서드를 가진다. 메서드 시그니처는
    전략별로 다르므로 Protocol 은 ``capability`` 만 강제하고, 신호 생성은
    capability.required_inputs 와 일치하는 메서드를 호출한다.
    """

    capability: StrategyCapability


# ── Registry ─────────────────────────────────────────────────────

class StrategyRegistry:
    """전략 클래스/인스턴스 등록소.

    클래스 자체 또는 인스턴스 모두 등록 가능. 조회는 capability.name 키로.
    """

    def __init__(self):
        self._items: dict[str, Any] = {}

    def register(self, strategy: Any, name: str | None = None) -> None:
        cap = self._extract_capability(strategy)
        key = name or cap.name
        if not key:
            raise ValueError("StrategyRegistry: name 또는 capability.name 이 필요")
        self._items[key] = strategy

    def get(self, name: str) -> Any | None:
        return self._items.get(name)

    def all(self) -> list[Any]:
        return list(self._items.values())

    def names(self) -> list[str]:
        return sorted(self._items.keys())

    def capabilities(self) -> list[StrategyCapability]:
        return [self._extract_capability(s) for s in self._items.values()]

    def catalog(self) -> list[dict]:
        return [c.to_dict() for c in self.capabilities()]

    def remove(self, name: str) -> bool:
        return self._items.pop(name, None) is not None

    def clear(self) -> None:
        self._items.clear()

    @staticmethod
    def _extract_capability(strategy: Any) -> StrategyCapability:
        cap = getattr(strategy, "capability", None)
        if cap is None:
            raise TypeError(
                f"{strategy!r} 에 'capability' 속성이 없음 — "
                "StrategyBase Protocol 미준수"
            )
        if not isinstance(cap, StrategyCapability):
            raise TypeError(
                f"{strategy!r}.capability 가 StrategyCapability 가 아님 (got {type(cap).__name__})"
            )
        return cap


# ── Helpers ──────────────────────────────────────────────────────

def assert_signal_contract(signal: Any) -> None:
    """신호 객체가 SignalBase 호환 필드를 갖는지 검증.

    StrategySignal/KimpSignal/PairSignal 모두 #8 에서 다음 필드가 추가됨:
      action, confidence, reason, is_order_intent.
    """
    for f in ("action", "confidence", "reason", "is_order_intent"):
        if not hasattr(signal, f):
            raise AssertionError(
                f"{type(signal).__name__} signal 객체에 '{f}' 속성 누락 "
                "(체크리스트 #8/#29 contract 위반)"
            )


def collect_default_strategies() -> StrategyRegistry:
    """기본 4개 전략을 등록한 registry 반환.

    Lazy import — 본 모듈이 strategies 서브모듈을 직접 의존하지 않게 함
    (의존 방향: strategies.* → base, base → strategies.* 는 함수 안에서만).
    """
    from app.strategies.strategies import (
        TrendFollowingStrategy, VolatilityBreakoutStrategy, PairTradingStrategy,
    )
    from app.strategies.kimp_mean_reversion import KimpMeanReversionStrategy

    r = StrategyRegistry()
    r.register(TrendFollowingStrategy())
    r.register(VolatilityBreakoutStrategy())
    r.register(PairTradingStrategy())
    r.register(KimpMeanReversionStrategy())
    return r
