"""AIExecutionGate — 체크리스트 #59 AI Execution Gate.

LIVE_AI_EXECUTION 모드 + ENABLE_AI_EXECUTION + source='ai' 인 주문에 대해
PermissionGate 통과 후 마지막으로 적용되는 추가 안전 가드.

CLAUDE.md §2.3: AI 자동 실행은 별도 게이트 + 보수적 임계값 필요.

검사:
  - confidence ≥ MIN_AI_CONFIDENCE
  - quality_score ≥ MIN_AI_QUALITY (signal 에 quality_score 가 있으면)
  - 일일 AI 자동 실행 횟수 한도
  - 심볼별 AI 재실행 쿨다운

OrderGateway 가 source='ai' + route='live' 인 경우만 호출.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AIGateResult:
    allowed: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def __bool__(self) -> bool:
        return self.allowed


class AIExecutionGate:
    """AI 자동 실행 전용 안전 가드."""

    DEFAULT_MIN_CONFIDENCE = 0.75
    DEFAULT_MIN_QUALITY_SCORE = 80.0
    DEFAULT_MAX_DAILY_ORDERS = 50
    DEFAULT_PER_SYMBOL_COOLDOWN_SEC = 15 * 60   # 15 분

    def __init__(
        self,
        *,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        min_quality_score: float = DEFAULT_MIN_QUALITY_SCORE,
        max_daily_orders: int = DEFAULT_MAX_DAILY_ORDERS,
        per_symbol_cooldown_sec: float = DEFAULT_PER_SYMBOL_COOLDOWN_SEC,
        time_fn=None,
    ):
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence 는 0~1")
        if not 0.0 <= min_quality_score <= 100.0:
            raise ValueError("min_quality_score 는 0~100")
        if max_daily_orders < 0:
            raise ValueError("max_daily_orders 는 음수 불가")
        if per_symbol_cooldown_sec < 0:
            raise ValueError("per_symbol_cooldown_sec 는 음수 불가")

        self.min_confidence = float(min_confidence)
        self.min_quality_score = float(min_quality_score)
        self.max_daily_orders = int(max_daily_orders)
        self.per_symbol_cooldown_sec = float(per_symbol_cooldown_sec)
        self._time_fn = time_fn or time.time

        self._daily_count = 0
        self._daily_window_start = self._time_fn()
        self._last_order_ts: dict[str, float] = {}

    # ── public ────────────────────────────────────────────────────

    def check(self, order: dict) -> AIGateResult:
        """주문 dict 평가. order 에는 confidence / quality_score / symbol 필요."""
        reasons: list[str] = []

        # 1. confidence
        try:
            conf = float(order.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        if conf < self.min_confidence:
            reasons.append(
                f"AI 신뢰도 부족: {conf:.2f} < {self.min_confidence:.2f}"
            )

        # 2. quality_score (있을 때만)
        if "quality_score" in order:
            try:
                qs = float(order["quality_score"] or 0.0)
            except (TypeError, ValueError):
                qs = 0.0
            if qs < self.min_quality_score:
                reasons.append(
                    f"AI 품질 점수 부족: {qs:.1f} < {self.min_quality_score:.1f}"
                )

        # 3. 일일 한도 (24h 윈도우)
        self._refresh_daily_window()
        if self._daily_count >= self.max_daily_orders:
            reasons.append(
                f"AI 일일 실행 한도: {self._daily_count}/{self.max_daily_orders}"
            )

        # 4. 심볼별 쿨다운
        symbol = str(order.get("symbol", ""))
        if symbol and self.per_symbol_cooldown_sec > 0:
            last = self._last_order_ts.get(symbol, 0.0)
            elapsed = self._time_fn() - last
            if elapsed < self.per_symbol_cooldown_sec:
                remaining = self.per_symbol_cooldown_sec - elapsed
                reasons.append(
                    f"AI {symbol} 재실행 쿨다운: {remaining:.0f}초 남음"
                )

        return AIGateResult(not reasons, tuple(reasons))

    def record_executed(self, order: dict) -> None:
        """실제 실행이 발생했을 때 호출 — 카운터/쿨다운 갱신."""
        self._refresh_daily_window()
        self._daily_count += 1
        symbol = str(order.get("symbol", ""))
        if symbol:
            self._last_order_ts[symbol] = self._time_fn()

    # ── 내부 ──────────────────────────────────────────────────────

    def _refresh_daily_window(self) -> None:
        now = self._time_fn()
        if now - self._daily_window_start >= 24 * 3600:
            self._daily_window_start = now
            self._daily_count = 0

    @property
    def status(self) -> dict:
        return {
            "daily_count": self._daily_count,
            "max_daily_orders": self.max_daily_orders,
            "min_confidence": self.min_confidence,
            "min_quality_score": self.min_quality_score,
            "per_symbol_cooldown_sec": self.per_symbol_cooldown_sec,
            "tracked_symbols": sorted(self._last_order_ts.keys()),
        }
