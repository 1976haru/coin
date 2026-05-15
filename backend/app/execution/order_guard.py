"""OrderGuard — 체크리스트 #51 Order Guard.

OrderGateway 내부의 pre-flight 안전 검사. RiskManager 통과 후 PermissionGate
실행 전에 주문 dict 자체의 형태/값 sanity 를 점검한다.

CLAUDE.md §2.4 단일 주문 경로:
  StrategySignal → AgentReview → RiskManager → **OrderGuard** → PermissionGate
                                                ^^^^^^^^^^^

순수 함수 — 외부 I/O 없이 주문 dict + 정책으로 평가. RiskManager 가 잡지 못하는
형태/문자열 비정상을 추가로 잡아낸다.

검사 항목:
  - shape         : 필수 필드 (symbol, side, notional_usdt) 존재
  - notional      : 양수 + 시스템 절대 한도 이내 (RiskManager 보다 보수적 cap)
  - leverage      : 양수
  - action        : 허용된 액션 집합
  - source        : 허용된 출처 (system/strategy/ai/manual)
  - symbol_format : 문자열 형식 sanity (빈 문자열, 비정상 문자)
  - symbol_blacklist : 운영자가 명시 금지한 심볼
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Iterable


REQUIRED_FIELDS = ("symbol", "side", "notional_usdt")

DEFAULT_ALLOWED_ACTIONS = frozenset({
    "BUY", "SELL", "HOLD", "BLOCKED", "CLOSE",
    "OPEN_REVERSE_KIMP",
    "OPEN_LONG_A_SHORT_B", "OPEN_SHORT_A_LONG_B",
})

DEFAULT_ALLOWED_SOURCES = frozenset({
    "system", "strategy", "ai", "manual", "test",
})

# 시스템 절대 cap — RiskManager max_order_notional_usdt 보다 보수적 외곽 한도.
# 운영 시 settings 외부에서 명시 cap. 비정상적으로 큰 주문 자체를 차단.
DEFAULT_ABSOLUTE_MAX_NOTIONAL_USDT = 10_000.0

# 심볼 형식 — 영숫자 + 슬래시 + 하이픈만 허용. 길이 1~32.
SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9/_\-]{1,32}$")


@dataclass(frozen=True)
class OrderGuardResult:
    """OrderGuard 평가 결과."""

    passed: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def __bool__(self) -> bool:
        return self.passed


class OrderGuard:
    """주문 dict pre-flight 검사기.

    Parameters
    ----------
    absolute_max_notional_usdt:
        시스템 절대 cap. RiskManager 보다 보수적이어야 의미가 있음.
    allowed_actions / allowed_sources:
        허용 집합. None 이면 기본값.
    symbol_blacklist:
        운영자가 임시 차단한 심볼들 (예: 갑작스러운 사고 종목).
    """

    def __init__(
        self,
        *,
        absolute_max_notional_usdt: float = DEFAULT_ABSOLUTE_MAX_NOTIONAL_USDT,
        allowed_actions: Iterable[str] | None = None,
        allowed_sources: Iterable[str] | None = None,
        symbol_blacklist: Iterable[str] = (),
    ):
        if absolute_max_notional_usdt <= 0:
            raise ValueError("absolute_max_notional_usdt 는 양수")
        self.absolute_max_notional_usdt = float(absolute_max_notional_usdt)
        self.allowed_actions = frozenset(allowed_actions or DEFAULT_ALLOWED_ACTIONS)
        self.allowed_sources = frozenset(allowed_sources or DEFAULT_ALLOWED_SOURCES)
        self.symbol_blacklist = frozenset(symbol_blacklist)

    def check(self, order: dict, *, source: str = "system") -> OrderGuardResult:
        reasons: list[str] = []

        # 1. 필수 필드
        for f in REQUIRED_FIELDS:
            if f not in order or order.get(f) in (None, ""):
                reasons.append(f"필수 필드 누락: '{f}'")
        if reasons:
            return OrderGuardResult(False, tuple(reasons))

        # 2. notional 검증
        try:
            notional = float(order.get("notional_usdt", 0))
        except (TypeError, ValueError):
            return OrderGuardResult(
                False, (f"notional_usdt 타입 오류: {order.get('notional_usdt')!r}",)
            )
        if notional <= 0:
            reasons.append(f"notional_usdt 양수여야 함 (현재 {notional})")
        elif notional > self.absolute_max_notional_usdt:
            reasons.append(
                f"notional_usdt {notional:.2f} > 절대 한도 {self.absolute_max_notional_usdt:.2f}"
            )

        # 3. leverage 검증 (옵션 — 누락 시 1.0 가정)
        leverage_raw = order.get("leverage", 1.0)
        try:
            leverage = float(leverage_raw)
        except (TypeError, ValueError):
            reasons.append(f"leverage 타입 오류: {leverage_raw!r}")
            leverage = 1.0
        if leverage <= 0:
            reasons.append(f"leverage 양수여야 함 (현재 {leverage})")

        # 4. action 검증
        action = order.get("side")
        if action not in self.allowed_actions:
            reasons.append(f"허용되지 않은 action: {action!r}")

        # 5. symbol 형식
        symbol = order.get("symbol", "")
        if not isinstance(symbol, str) or not SYMBOL_PATTERN.match(symbol):
            reasons.append(f"비정상 symbol 형식: {symbol!r}")

        # 6. symbol blacklist (대소문자 통일 비교)
        if isinstance(symbol, str) and symbol.upper() in {
            s.upper() for s in self.symbol_blacklist
        }:
            reasons.append(f"심볼 blacklist: {symbol}")

        # 7. source 검증
        if source not in self.allowed_sources:
            reasons.append(f"허용되지 않은 source: {source!r}")

        if reasons:
            return OrderGuardResult(False, tuple(reasons))
        return OrderGuardResult(True, ())
