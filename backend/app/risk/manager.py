"""
RiskManager — GPT의 깔끔한 evaluate() + v5 KillSwitch + 연속손실/쿨다운
"""
import time
from dataclasses import dataclass, field
from datetime import date
from collections import defaultdict


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reasons: list[str]

    def __bool__(self) -> bool:
        return self.approved


class RiskManager:
    """
    주문 전 리스크 체크. GPT의 evaluate() 패턴 + v5의 상태 추적.

    체크 순서:
      1. KillSwitch
      2. Emergency stop
      3. Data freshness (외부 주입)
      4. 일일 손실 한도
      5. 연속 손실 한도
      6. 주문 금액 한도
      7. 동시 포지션 한도
      8. 레버리지 한도
    """

    def __init__(
        self,
        max_order_notional_usdt: float = 100.0,
        max_open_positions: int = 5,
        daily_loss_limit_pct: float = 2.0,
        max_leverage: float = 2.0,
        max_consecutive_losses: int = 5,
        re_entry_cooldown_min: int = 5,
    ):
        self.max_order_notional_usdt = max_order_notional_usdt
        self.max_open_positions = max_open_positions
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.max_leverage = max_leverage
        self.max_consecutive_losses = max_consecutive_losses
        self.cooldown_sec = re_entry_cooldown_min * 60

        # 상태
        self._kill_switch = False
        self._consecutive_losses = 0
        self._daily_pnl_pct = 0.0
        self._today = date.today()
        self._last_entry_ts: dict[str, float] = {}

    # ── 메인 평가 ─────────────────────────────────────────────────

    def evaluate(
        self,
        order: dict,
        account: dict,
        freshness_block_reasons: list[str] | None = None,
    ) -> RiskDecision:
        reasons: list[str] = []

        # 1. Kill Switch
        if self._kill_switch:
            reasons.append("Kill Switch 활성화")
            return RiskDecision(False, reasons)

        # 2. Emergency stop
        if account.get("emergency_stop", False):
            reasons.append("Emergency Stop 활성화")

        # 3. Freshness
        reasons.extend(freshness_block_reasons or [])

        # 4. 일일 손실
        self._refresh_daily()
        if self._daily_pnl_pct <= -self.daily_loss_limit_pct / 100:
            reasons.append(f"일 손실 한도 도달: {self._daily_pnl_pct*100:.2f}%")

        # 5. 연속 손실
        if self._consecutive_losses >= self.max_consecutive_losses:
            reasons.append(f"연속 손실 {self._consecutive_losses}회 → 거래 중단")

        # 6. 주문 금액
        if float(order.get("notional_usdt", 0)) > self.max_order_notional_usdt:
            reasons.append(f"주문 금액 초과: {order.get('notional_usdt')} > {self.max_order_notional_usdt}")

        # 7. 포지션 수
        if int(account.get("open_positions", 0)) >= self.max_open_positions:
            reasons.append(f"동시 포지션 한도: {account.get('open_positions')}/{self.max_open_positions}")

        # 8. 레버리지
        if float(order.get("leverage", 1)) > self.max_leverage:
            reasons.append(f"레버리지 초과: {order.get('leverage')}x > {self.max_leverage}x")

        # 9. 쿨다운 (BUY만)
        if order.get("side") in {"BUY", "OPEN_REVERSE_KIMP"}:
            symbol = order.get("symbol", "")
            last = self._last_entry_ts.get(symbol, 0)
            if time.time() - last < self.cooldown_sec:
                remaining = int((self.cooldown_sec - (time.time() - last)) / 60)
                reasons.append(f"{symbol} 재진입 쿨다운 중 ({remaining}분)")

        return RiskDecision(not reasons, reasons)

    # ── 상태 업데이트 ─────────────────────────────────────────────

    def record_trade(self, symbol: str, pnl_pct: float):
        """거래 결과 기록"""
        self._refresh_daily()
        self._daily_pnl_pct += pnl_pct / 100
        if pnl_pct < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

    def record_entry(self, symbol: str):
        """진입 시각 기록 (쿨다운용)"""
        self._last_entry_ts[symbol] = time.time()

    # ── Kill Switch ────────────────────────────────────────────────

    def activate_kill_switch(self, reason: str = ""):
        self._kill_switch = True

    def deactivate_kill_switch(self):
        self._kill_switch = False

    # ── 상태 조회 ─────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "kill_switch": self._kill_switch,
            "daily_pnl_pct": round(self._daily_pnl_pct * 100, 3),
            "consecutive_losses": self._consecutive_losses,
            "daily_loss_limit_pct": self.daily_loss_limit_pct,
        }

    def _refresh_daily(self):
        today = date.today()
        if today != self._today:
            self._today = today
            self._daily_pnl_pct = 0.0
