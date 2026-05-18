"""StrategyContract — 체크리스트 #29 (확장).

기존 `app.strategies.base` 는 Protocol 기반 `StrategyBase` + `StrategyCapability` +
`StrategyRegistry` 를 제공 — 4개 기존 전략(TrendFollowing/VolatilityBreakout/
PairTrading/KimpMeanReversion) 이 이를 따른다. **본 모듈은 그 위에 더 풍부한 ABC
contract 를 추가한다** — 신규 전략은 본 ABC 를 따르고, 기존 전략은 그대로 유지.

핵심 원칙 (CLAUDE.md §2.3 / §2.4 / §3.1):
  - 전략은 **Signal 만 생성**한다. 주문/체결 호출 절대 금지.
  - 전략은 ``app.brokers.*`` / ``app.execution.*`` / OrderGateway / adapter 를
    import 하지 않는다 (정적 회귀로 강제).
  - 모든 신호 객체 (`StrategySignal` / `PositionSizingHint` / `ExitRuleDecision`)
    의 `is_order_intent` / `is_final_order_size` 류 플래그는 **항상 False 기본값**.
  - 본 contract 의 결과 어디에도 BUY/SELL 을 실제 주문 명령으로 해석하지 않는다.
    실제 주문 전환은 Strategy → Agent → RiskManager → OrderGuard → PermissionGate
    → ApprovalQueue → OrderGateway 경로에서만.

본 모듈은 `StrategyContext` (읽기 전용 입력) 와 4개 abstract 메서드:
  - `generate_signal(context) -> StrategySignal`
  - `calculate_size(context, signal) -> PositionSizingHint`
  - `exit_rule(context, signal) -> ExitRuleDecision`
  - `explain_signal(context, signal) -> SignalExplanation`
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Iterable

from app.strategies.base import StrategyCapability
from app.strategies._signals import StrategySignal


# 본 contract 의 결과 어디에도 들어가서는 안 되는 action 토큰.
# (BUY/SELL 은 StrategySignal.action 에 등장 가능 — 그러나 "주문 명령" 으로
# 해석되지 않음. 본 카탈로그는 별도 필드/메타데이터에서 action 토큰이 새는 것을
# 방어적으로 차단하기 위함.)
FORBIDDEN_ORDER_TOKENS: tuple[str, ...] = (
    "PLACE_ORDER", "CANCEL_ORDER", "SUBMIT_ORDER", "ROUTE_ORDER",
)


# Strategy 결과의 허용 action 카탈로그 (참고).
ALLOWED_SIGNAL_ACTIONS: tuple[str, ...] = (
    "BUY", "SELL", "HOLD",
    "BLOCKED",        # data freshness/quality 등 안전 사유로 비활성
    "NO_ACTION",      # 조건 미충족 — 행동 없음
    "WATCH_ONLY",     # 낮은 confidence — 관찰만
)


# ── StrategyContext (읽기 전용 입력) ─────────────────────────────


@dataclass(frozen=True)
class StrategyContext:
    """전략 판단에 필요한 *읽기 전용* 입력.

    포함:
      - 시장 데이터 (closes/highs/lows/volumes/timeframe)
      - freshness (마지막 시세 수신 시각)
      - data quality (GOOD/WARNING/EXCLUDE)
      - notice_context (#18 — 거래소 공지 요약)
      - theme_context  (#19 — Trend/News/Theme)
      - regime (TREND_UP/TREND_DOWN/RANGE/UNKNOWN — #19)
      - positions_snapshot (read-only)
      - kimp_pct / fx 보조 입력

    포함 *금지* (정적 회귀로 검증):
      - secret / api_key / token
      - broker / adapter / order_gateway 참조
      - mutable 상태

    생성 시점에 작성자가 secret 류 키를 넘기지 않도록 ``extra`` 에 들어가는 dict
    에 대해 `_assert_no_secret_keys` 가 실행된다 (방어).
    """

    symbol: str
    exchange: str = "mock"
    timeframe: str = "1m"

    # 시장 데이터 (immutable tuple)
    closes: tuple[float, ...] = ()
    highs:  tuple[float, ...] = ()
    lows:   tuple[float, ...] = ()
    volumes: tuple[float, ...] = ()

    # 안전 상태
    freshness_ok: bool = True
    freshness_age_sec: float | None = None
    data_quality_grade: str = "GOOD"             # GOOD / WARNING / EXCLUDE
    is_in_universe: bool = True

    # 외부 context (필요 시)
    notice_context: dict[str, Any] | None = None
    theme_context:  dict[str, Any] | None = None
    regime: str = "UNKNOWN"                       # TREND_UP/TREND_DOWN/RANGE/UNKNOWN

    # 보조 입력
    kimp_pct: float | None = None
    fx_rate: float | None = None

    # 포지션 스냅샷 (read-only). qty/avg_entry_price 등을 dict 로.
    positions_snapshot: dict[str, Any] | None = None

    # 추가 dict — secret 류 키 차단.
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # 방어적: extra 에 secret 류 키 부재 검증.
        _assert_no_secret_keys(self.extra, where="StrategyContext.extra")
        # closes 가 list 로 들어와도 immutable tuple 로 강제.
        # frozen=True 이므로 object.__setattr__ 우회.
        for fname in ("closes", "highs", "lows", "volumes"):
            v = getattr(self, fname)
            if isinstance(v, list):
                object.__setattr__(self, fname, tuple(v))


# ── PositionSizingHint ─────────────────────────────────────────


@dataclass(frozen=True)
class PositionSizingHint:
    """전략이 제안하는 크기 힌트.

    **최종 주문 수량이 아니다.** RiskManager / OrderGuard / PermissionGate 가
    최종 수량을 결정한다. `is_final_order_size=False` 가 영구 (frozen).
    """

    symbol: str
    base_currency: str = "USDT"
    suggested_qty: float | None = None
    suggested_notional_usdt: float | None = None
    leverage_hint: float = 1.0      # 정보용 — 실제 leverage 적용은 RiskManager
    confidence: float = 0.0
    reason: str = ""
    is_final_order_size: bool = False        # 영구
    used_for_order: bool = False             # 영구 (호환)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── ExitRuleDecision ───────────────────────────────────────────


@dataclass(frozen=True)
class ExitRuleDecision:
    """청산 또는 위험 축소가 필요한지에 대한 전략 판단.

    **실제 주문 명령이 아니다.** 후속 risk/order pipeline 이 검토한다.
    """

    symbol: str
    should_exit: bool = False
    exit_qty_fraction: float = 0.0   # 0~1 (수량 비율). 0=청산 안 함, 1=전량.
    urgency: str = "normal"          # normal | high | critical
    reason: str = ""
    is_order_intent: bool = False    # 영구

    def __post_init__(self):
        if not (0.0 <= self.exit_qty_fraction <= 1.0):
            raise ValueError(
                f"exit_qty_fraction must be in [0, 1], got {self.exit_qty_fraction}"
            )
        if self.urgency not in ("normal", "high", "critical"):
            raise ValueError(
                f"urgency must be normal/high/critical, got {self.urgency!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── SignalExplanation ──────────────────────────────────────────


@dataclass(frozen=True)
class SignalExplanation:
    """UI/로그/Agent 용 설명 — 직접 주문 지시 없음.

    "candidate", "review_required" 표현 사용. confidence 와 한계 명시.
    """

    strategy_name: str
    symbol: str
    summary: str
    reasons: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    confidence: float = 0.0
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for f in ("reasons", "evidence", "risks", "limitations"):
            d[f] = list(getattr(self, f))
        return d


# ── StrategyContract (ABC) ─────────────────────────────────────


class StrategyContract(ABC):
    """4개 abstract method 를 가진 전략 contract.

    구현체:
      - ``capability`` 클래스 속성 (`StrategyCapability`) — 기존 base 와 호환.
      - ``generate_signal(context)`` — 신호 생성.
      - ``calculate_size(context, signal)`` — 크기 힌트.
      - ``exit_rule(context, signal)`` — 청산 판단.
      - ``explain_signal(context, signal)`` — 설명.

    구현체는 **broker / adapter / order_gateway 를 import 하지 않는다** — 정적 회귀.
    """

    # 기본 메타데이터 — 하위 클래스가 덮어쓴다.
    capability: StrategyCapability = StrategyCapability(
        name="abstract",
        description="abstract base",
        required_inputs=(),
        signal_actions=("HOLD",),
    )
    # 기본 비활성 — 운영자가 명시적으로 enable.
    enabled_by_default: bool = False
    # 어떤 regime 에서 후보인가 (StrategySelectionAgent 가 참고).
    preferred_regimes: tuple[str, ...] = ("UNKNOWN",)

    @abstractmethod
    def generate_signal(self, context: StrategyContext) -> StrategySignal:
        """전략 핵심 — context 기반으로 StrategySignal 생성.

        반환 신호는 ``is_order_intent=False`` 가 기본 (StrategySignal 의 default).
        """

    @abstractmethod
    def calculate_size(
        self, context: StrategyContext, signal: StrategySignal,
    ) -> PositionSizingHint:
        """전략이 제안하는 크기 힌트.

        최종 주문 수량이 아니다. ``is_final_order_size=False`` 영구.
        """

    @abstractmethod
    def exit_rule(
        self, context: StrategyContext, signal: StrategySignal,
    ) -> ExitRuleDecision:
        """청산 또는 위험 축소가 필요한지 판단.

        실제 주문 명령이 아니다. ``is_order_intent=False`` 영구.
        """

    @abstractmethod
    def explain_signal(
        self, context: StrategyContext, signal: StrategySignal,
    ) -> SignalExplanation:
        """UI/로그/Agent 용 설명 — 직접 주문 지시 없음."""

    # ── helper — 안전 가드 ────────────────────────────────────────

    def evaluate(self, context: StrategyContext) -> dict[str, Any]:
        """편의: 전체 파이프라인 결과를 dict 로 반환.

        production 코드는 보통 generate/size/exit/explain 을 개별 호출하지만,
        UI/디버그/테스트가 한 번에 보고 싶을 때.
        """
        signal = self.generate_signal(context)
        # signal validation — 주문 의도 기본 False.
        if getattr(signal, "is_order_intent", False) is True:
            raise StrategyContractError(
                f"{self.capability.name}: generate_signal returned "
                "is_order_intent=True (forbidden — Strategy must not produce "
                "order intent directly)"
            )
        size = self.calculate_size(context, signal)
        if getattr(size, "is_final_order_size", False) is True:
            raise StrategyContractError(
                f"{self.capability.name}: calculate_size returned "
                "is_final_order_size=True (forbidden — RiskManager decides)"
            )
        exit_dec = self.exit_rule(context, signal)
        if getattr(exit_dec, "is_order_intent", False) is True:
            raise StrategyContractError(
                f"{self.capability.name}: exit_rule returned is_order_intent=True"
            )
        explanation = self.explain_signal(context, signal)
        return {
            "strategy": self.capability.name,
            "symbol": context.symbol,
            "signal": _signal_to_dict(signal),
            "sizing": size.to_dict(),
            "exit": exit_dec.to_dict(),
            "explanation": explanation.to_dict(),
            "is_order_intent": False,            # 영구 — 본 메서드 결과는 주문이 아님
            "used_for_order": False,
            "direct_order_allowed": False,
        }


# ── 예외 ─────────────────────────────────────────────────────────


class StrategyContractError(RuntimeError):
    """contract 위반 — generate*가 is_order_intent=True 반환 등."""


# ── 헬퍼 ─────────────────────────────────────────────────────────


_SECRET_KEY_TOKENS: tuple[str, ...] = (
    "api_key", "api_secret", "secret", "access_token", "token",
    "passphrase", "password", "private_key",
)


def _assert_no_secret_keys(d: dict | None, *, where: str) -> None:
    if not d:
        return
    for k in d.keys():
        kl = str(k).lower()
        for bad in _SECRET_KEY_TOKENS:
            if bad in kl:
                raise StrategyContractError(
                    f"{where}: forbidden secret-like key {k!r}"
                )


def _signal_to_dict(signal: Any) -> dict[str, Any]:
    """StrategySignal/PairSignal/KimpSignal 모두 asdict 호환."""
    if hasattr(signal, "to_order"):
        # frozen dataclass — asdict 사용
        try:
            return asdict(signal)
        except Exception:
            pass
    if hasattr(signal, "to_dict"):
        try:
            return dict(signal.to_dict())
        except Exception:
            pass
    return {"repr": repr(signal)}


def assert_no_order_intent(signal: Any) -> None:
    """외부 검증용 — 어떤 신호 객체든 is_order_intent=False 임을 보장."""
    flag = getattr(signal, "is_order_intent", None)
    if flag is True:
        raise StrategyContractError(
            f"{type(signal).__name__}.is_order_intent=True is forbidden"
        )


def is_safe_action(action: str) -> bool:
    """전략 결과 action 이 허용 카탈로그에 속하는지."""
    if not action:
        return False
    return action.upper() in ALLOWED_SIGNAL_ACTIONS


__all__ = (
    "ALLOWED_SIGNAL_ACTIONS",
    "FORBIDDEN_ORDER_TOKENS",
    "StrategyContext",
    "PositionSizingHint",
    "ExitRuleDecision",
    "SignalExplanation",
    "StrategyContract",
    "StrategyContractError",
    "assert_no_order_intent",
    "is_safe_action",
)
