"""TrendFollowingContractStrategy — 체크리스트 #30 (확장).

기존 `TrendFollowingStrategy` (Protocol 기반, `trend_following.py`) 와 별도로 #29
의 신규 `StrategyContract` ABC 를 따르는 추세추종 전략. EMA/SMA/Donchian/ADX 기반
판단 + freshness/data_quality/notice/theme context 통합 안전 가드.

설계 원칙 (CLAUDE.md §2.3 / §2.4 / §3.1):
  - 본 전략은 **Signal 만 생성한다**. broker / adapter / OrderGateway /
    place_order / cancel_order / get_balance 직접 호출 절대 금지.
  - 모든 신호 객체의 `is_order_intent` / `is_final_order_size` 류 플래그는 영구
    False. `evaluate()` 가 contract 위반 시 raise.
  - BUY/SELL 은 *전략 판단 표현*. 실제 주문 전환은 별도 단계.

진입 후보 조건:
  - LONG: close > trend_sma AND fast_ema > slow_ema AND close >= prev_donchian_high
          AND adx >= adx_min AND data_quality != EXCLUDE AND freshness_ok
  - SHORT: (allow_short_candidates=True 일 때만) — 위 조건 부호 반전 + Donchian low
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Sequence

from app.strategies.base import StrategyCapability
from app.strategies._signals import StrategySignal
from app.strategies._indicators import ema, sma, atr, donchian_channel, adx
from app.strategies.contract import (
    StrategyContract, StrategyContext,
    PositionSizingHint, ExitRuleDecision, SignalExplanation,
)


@dataclass(frozen=True)
class TrendFollowingParams:
    """전략 파라미터 — 외부 주입 가능."""

    ema_fast: int = 20
    ema_slow: int = 60
    trend_sma_period: int = 200
    donchian_period: int = 20
    adx_period: int = 14
    adx_min: float = 18.0
    breakout_buffer_pct: float = 0.0          # 추가 buffer 비율
    min_candles_required: int = 0             # 0 = ema_slow + 5 로 자동 계산
    allow_short_candidates: bool = False      # 기본 LONG 후보만
    base_notional_usdt: float = 100.0
    max_leverage_hint: float = 1.0            # 1 = no leverage


class TrendFollowingContractStrategy(StrategyContract):
    """EMA/SMA/Donchian/ADX 기반 추세추종 후보 판단 전략.

    `StrategyContract` ABC 의 4 abstract method 구현. 모든 결과는 *후보*. 실제
    주문 전환은 RiskManager/OrderGuard/PermissionGate/ApprovalQueue/OrderGateway
    경로에서만.
    """

    capability = StrategyCapability(
        name="trend_following_v2",
        description=(
            "EMA fast/slow + SMA(200) trend filter + Donchian breakout + ADX trend "
            "strength. Generates candidate signals only — never order intent."
        ),
        required_inputs=("closes", "highs", "lows"),
        signal_actions=("BUY", "SELL", "HOLD", "BLOCKED", "NO_ACTION", "WATCH_ONLY"),
        output_signal_class="StrategySignal",
    )
    # StrategySelectionAgent metadata
    enabled_by_default: bool = False
    preferred_regimes: tuple[str, ...] = ("TREND_UP", "TREND_DOWN")

    def __init__(self, params: TrendFollowingParams | None = None):
        self.params = params or TrendFollowingParams()

    # ── 1. generate_signal ────────────────────────────────────────

    def generate_signal(self, context: StrategyContext) -> StrategySignal:
        p = self.params
        # ── 안전 가드: data quality / freshness / universe / 데이터 수량 ──
        if context.data_quality_grade == "EXCLUDE":
            return StrategySignal(
                action="BLOCKED", confidence=0.0,
                reason="data quality EXCLUDE — strategy disabled",
            )
        if not context.freshness_ok:
            return StrategySignal(
                action="BLOCKED", confidence=0.0,
                reason="freshness stale — entry blocked (#16 policy)",
            )
        if not context.is_in_universe:
            return StrategySignal(
                action="BLOCKED", confidence=0.0,
                reason="symbol not in watchlist universe",
            )
        # high-risk notice 가 있으면 candidate 유지하되 BLOCKED 로 표시 (#18 정책)
        if _has_high_risk_notice(context):
            return StrategySignal(
                action="BLOCKED", confidence=0.0,
                reason=("high-risk notice present (delisting / withdrawal "
                        "suspension / trading suspension) — entry blocked"),
            )

        min_candles = (p.min_candles_required
                       if p.min_candles_required > 0
                       else p.ema_slow + 5)
        n = len(context.closes)
        if n < min_candles:
            return StrategySignal(
                action="NO_ACTION", confidence=0.0,
                reason=(f"insufficient candles: have {n}, "
                        f"need {min_candles}"),
            )
        if len(context.highs) != n or len(context.lows) != n:
            return StrategySignal(
                action="NO_ACTION", confidence=0.0,
                reason="OHLC length mismatch",
            )

        closes = context.closes
        highs = context.highs
        lows = context.lows

        fast = ema(closes, p.ema_fast)
        slow = ema(closes, p.ema_slow)
        trend_sma = (
            sma(closes, p.trend_sma_period)
            if n >= p.trend_sma_period else sma(closes, n)
        )
        adx_v = adx(highs, lows, closes, period=p.adx_period)
        donchian_hi, donchian_lo = donchian_channel(
            highs, lows, period=p.donchian_period, exclude_current=True,
        )
        current = closes[-1]
        buffer_long = donchian_hi * (1 + p.breakout_buffer_pct)
        buffer_short = donchian_lo * (1 - p.breakout_buffer_pct)

        # 추세 강도 미달 → HOLD (NO_ACTION 보다 약간 정보 풍부)
        if adx_v < p.adx_min:
            return StrategySignal(
                action="HOLD", confidence=0.0,
                reason=f"weak trend: ADX={adx_v:.1f} < {p.adx_min}",
            )

        atr_value = atr(highs, lows, closes, period=p.adx_period) or (current * 0.02)
        stop_long = max(0.0, current - atr_value * 1.5)
        stop_short = current + atr_value * 1.5
        tp_long = current + atr_value * 3.0
        tp_short = max(0.0, current - atr_value * 3.0)

        # ── LONG 후보 조건 ────────────────────────────────────────
        if (current > trend_sma
                and fast > slow
                and current >= buffer_long
                and donchian_hi > 0):
            confidence = _confidence_from_adx(adx_v, p.adx_min)
            return StrategySignal(
                action="BUY",                # 후보 — 주문 명령 아님
                confidence=confidence,
                reason=(f"LONG candidate: close>{p.trend_sma_period}SMA, "
                        f"EMA{p.ema_fast}>EMA{p.ema_slow}, Donchian breakout, "
                        f"ADX={adx_v:.1f}"),
                entry_price=current,
                stop_loss=stop_long,
                take_profit=tp_long,
                quality_score=_quality_score_from_adx(adx_v),
            )

        # ── SHORT 후보 조건 (옵트인) ──────────────────────────────
        if (p.allow_short_candidates
                and current < trend_sma
                and fast < slow
                and current <= buffer_short
                and donchian_lo > 0):
            confidence = _confidence_from_adx(adx_v, p.adx_min)
            return StrategySignal(
                action="SELL",               # 후보 — 주문 명령 아님
                confidence=confidence,
                reason=(f"SHORT candidate: close<{p.trend_sma_period}SMA, "
                        f"EMA{p.ema_fast}<EMA{p.ema_slow}, Donchian breakdown, "
                        f"ADX={adx_v:.1f}"),
                entry_price=current,
                stop_loss=stop_short,
                take_profit=tp_short,
                quality_score=_quality_score_from_adx(adx_v),
            )

        # 추세 조건 미충족 — HOLD
        return StrategySignal(
            action="HOLD", confidence=0.0,
            reason="no trend continuation breakout",
        )

    # ── 2. calculate_size ────────────────────────────────────────

    def calculate_size(
        self, context: StrategyContext, signal: StrategySignal,
    ) -> PositionSizingHint:
        """confidence 비례 *후보 크기 힌트* — 최종 주문 수량이 아니다.

        RiskManager / OrderGuard 가 실제 수량을 결정한다.
        """
        p = self.params
        if signal.action in ("BLOCKED", "NO_ACTION", "HOLD"):
            return PositionSizingHint(
                symbol=context.symbol,
                base_currency=context.exchange or "USDT",
                suggested_notional_usdt=0.0,
                confidence=0.0,
                reason="no size hint for non-actionable signal",
                # is_final_order_size=False 영구
            )
        # confidence 0~1 비례 hint. base_notional × confidence × volatility 보정.
        c = max(0.0, min(1.0, signal.confidence))
        suggested = p.base_notional_usdt * c
        return PositionSizingHint(
            symbol=context.symbol,
            base_currency="USDT",
            suggested_notional_usdt=suggested,
            leverage_hint=p.max_leverage_hint,
            confidence=c,
            reason=(f"hint = base({p.base_notional_usdt}) × "
                    f"confidence({c:.2f}) = {suggested:.2f} USDT"),
            # is_final_order_size=False, used_for_order=False 영구
        )

    # ── 3. exit_rule ─────────────────────────────────────────────

    def exit_rule(
        self, context: StrategyContext, signal: StrategySignal,
    ) -> ExitRuleDecision:
        """청산 후보 판단 — 실제 주문 명령 아님.

        조건:
          1. data_quality EXCLUDE → 전량 청산 후보, urgency=critical
          2. freshness stale → 전량 청산 후보, urgency=high
          3. high-risk notice → 전량 청산 후보, urgency=high
          4. fast EMA 가 slow EMA 아래 cross → 60% 부분 청산 후보, urgency=normal
          5. close 가 Donchian low 이탈 → 100% 청산 후보, urgency=high (LONG 진입 후 가정)
          6. ADX 급락 (< adx_min × 0.7) → 30% 부분 청산 후보, urgency=normal
        """
        p = self.params
        if context.data_quality_grade == "EXCLUDE":
            return ExitRuleDecision(
                symbol=context.symbol,
                should_exit=True,
                exit_qty_fraction=1.0,
                urgency="critical",
                reason="data quality EXCLUDE — full exit candidate",
            )
        if not context.freshness_ok:
            return ExitRuleDecision(
                symbol=context.symbol,
                should_exit=True,
                exit_qty_fraction=1.0,
                urgency="high",
                reason="freshness stale — exit candidate (#16)",
            )
        if _has_high_risk_notice(context):
            return ExitRuleDecision(
                symbol=context.symbol,
                should_exit=True,
                exit_qty_fraction=1.0,
                urgency="high",
                reason="high-risk notice (delisting / suspension) — exit candidate",
            )

        n = len(context.closes)
        if n < p.ema_slow + 5:
            return ExitRuleDecision(
                symbol=context.symbol, should_exit=False,
                reason="insufficient candles for exit_rule evaluation",
            )
        if len(context.highs) != n or len(context.lows) != n:
            return ExitRuleDecision(
                symbol=context.symbol, should_exit=False,
                reason="OHLC length mismatch",
            )

        closes = context.closes
        fast = ema(closes, p.ema_fast)
        slow = ema(closes, p.ema_slow)
        adx_v = adx(context.highs, context.lows, closes, period=p.adx_period)
        donchian_hi, donchian_lo = donchian_channel(
            context.highs, context.lows,
            period=p.donchian_period, exclude_current=True,
        )
        current = closes[-1]

        # EMA cross-below — 60% 부분 청산
        if fast < slow:
            return ExitRuleDecision(
                symbol=context.symbol,
                should_exit=True,
                exit_qty_fraction=0.6,
                urgency="normal",
                reason=(f"fast EMA({p.ema_fast})={fast:.4f} < "
                        f"slow EMA({p.ema_slow})={slow:.4f} — partial exit candidate"),
            )

        # Donchian low 이탈 — 전량 청산 (LONG 진입 후 추세 무효화)
        if donchian_lo > 0 and current < donchian_lo:
            return ExitRuleDecision(
                symbol=context.symbol,
                should_exit=True,
                exit_qty_fraction=1.0,
                urgency="high",
                reason=(f"close={current:.4f} < Donchian low="
                        f"{donchian_lo:.4f} — full exit candidate"),
            )

        # ADX 급락 — 30% 부분 청산
        if adx_v < p.adx_min * 0.7:
            return ExitRuleDecision(
                symbol=context.symbol,
                should_exit=True,
                exit_qty_fraction=0.3,
                urgency="normal",
                reason=(f"ADX={adx_v:.1f} dropped below "
                        f"{p.adx_min * 0.7:.1f} — partial exit candidate"),
            )

        return ExitRuleDecision(
            symbol=context.symbol,
            should_exit=False,
            reason="no exit condition triggered",
        )

    # ── 4. explain_signal ────────────────────────────────────────

    def explain_signal(
        self, context: StrategyContext, signal: StrategySignal,
    ) -> SignalExplanation:
        p = self.params
        reasons: list[str] = [signal.reason]
        evidence: list[str] = []
        risks: list[str] = []
        limitations: list[str] = [
            "candidate only — not an order",
            "RiskManager / OrderGuard 가 최종 수량 결정",
            "BUY/SELL action 은 전략 판단 표현이며 직접 주문 명령이 아님",
        ]

        n = len(context.closes)
        if n >= p.ema_slow:
            evidence.append(
                f"EMA({p.ema_fast})={ema(context.closes, p.ema_fast):.4f}, "
                f"EMA({p.ema_slow})={ema(context.closes, p.ema_slow):.4f}"
            )
        if n >= p.trend_sma_period:
            evidence.append(
                f"SMA({p.trend_sma_period})={sma(context.closes, p.trend_sma_period):.4f}"
            )
        else:
            evidence.append(
                f"SMA({p.trend_sma_period}) — using available "
                f"{n} samples"
            )
        if len(context.highs) == n and len(context.lows) == n and n > 1:
            evidence.append(
                f"ADX({p.adx_period})="
                f"{adx(context.highs, context.lows, context.closes, p.adx_period):.2f}"
            )
            dh, dl = donchian_channel(
                context.highs, context.lows,
                period=p.donchian_period, exclude_current=True,
            )
            evidence.append(
                f"Donchian({p.donchian_period}) high={dh:.4f}, low={dl:.4f}, "
                f"current={context.closes[-1]:.4f}"
            )

        # 안전 상태
        evidence.append(f"data_quality_grade={context.data_quality_grade}")
        evidence.append(f"freshness_ok={context.freshness_ok}")
        evidence.append(f"is_in_universe={context.is_in_universe}")
        evidence.append(f"regime={context.regime}")

        if not context.freshness_ok:
            risks.append("stale market data — BUY blocked")
        if context.data_quality_grade == "EXCLUDE":
            risks.append("data quality EXCLUDE")
        if _has_high_risk_notice(context):
            risks.append(
                "high-risk notice present (delisting / suspension)"
            )
        if context.theme_context and context.theme_context.get(
            "review_required_symbols",
        ):
            risks.append(
                f"theme context review_required: "
                f"{context.theme_context['review_required_symbols'][:5]}"
            )

        summary = _summary_for_action(signal.action, p)
        return SignalExplanation(
            strategy_name=self.capability.name,
            symbol=context.symbol,
            summary=summary,
            reasons=tuple(reasons),
            evidence=tuple(evidence),
            risks=tuple(risks),
            limitations=tuple(limitations),
            confidence=signal.confidence,
        )


# ── 헬퍼 ─────────────────────────────────────────────────────────


def _has_high_risk_notice(context: StrategyContext) -> bool:
    """notice_context (#18) 의 high_risk_symbols 에 본 symbol 이 있는지."""
    nc = context.notice_context
    if not nc or not isinstance(nc, dict):
        return False
    hrs = nc.get("high_risk_symbols") or []
    if not isinstance(hrs, (list, tuple)):
        return False
    # symbol matching — base 만 비교 (BTC-USDT → BTC).
    sym = (context.symbol or "").upper()
    base = sym.split("-")[0].split("/")[0] if "-" in sym or "/" in sym else sym
    for s in hrs:
        s_up = str(s).upper()
        if s_up == sym or s_up == base:
            return True
    return False


def _confidence_from_adx(adx_v: float, adx_min: float) -> float:
    """ADX 값에서 confidence 0~1 환산.

    adx_min 부근에서 0.5, adx_min+20 에서 0.8, adx_min+40 에서 0.9 정도.
    """
    if adx_v < adx_min:
        return 0.0
    excess = adx_v - adx_min
    conf = 0.5 + min(0.4, excess / 100.0)
    return round(conf, 4)


def _quality_score_from_adx(adx_v: float) -> float:
    """ADX 값을 SignalQualityAgent 입력 (0~100) 으로 환산."""
    return round(min(100.0, max(0.0, adx_v * 2.0)), 2)


def _summary_for_action(action: str, p: TrendFollowingParams) -> str:
    if action == "BUY":
        return (f"candidate_long: EMA({p.ema_fast})>{p.ema_slow}, Donchian "
                f"breakout, ADX>=adx_min — candidate only, not an order")
    if action == "SELL":
        return (f"candidate_short: EMA({p.ema_fast})<{p.ema_slow}, Donchian "
                f"breakdown — candidate only, not an order")
    if action == "BLOCKED":
        return "trend following BLOCKED (safety gate)"
    if action == "NO_ACTION":
        return "trend following NO_ACTION (insufficient data)"
    return "trend following HOLD (no condition met) — candidate evaluation only"


__all__ = (
    "TrendFollowingParams",
    "TrendFollowingContractStrategy",
)
