"""VolatilityBreakoutContractStrategy — 체크리스트 #31 (확장).

기존 `VolatilityBreakoutStrategy` (Protocol 기반, `volatility_breakout.py`) 와
별도로 #29 의 신규 `StrategyContract` ABC 를 따르는 ATR 기반 변동성 돌파 전략.

설계 원칙 (CLAUDE.md §2.3 / §2.4 / §3.1):
  - 본 전략은 **Signal 만 생성한다**. broker/adapter/OrderGateway/place_order/
    cancel_order/get_balance 직접 호출 절대 금지.
  - 모든 신호의 `is_order_intent` / `is_final_order_size` / `used_for_order` 영구 False.
  - `BUY`/`SELL` 은 *전략 판단 표현*. 실제 주문 전환은 별도 단계.

전략 핵심:
  - ATR(14) 로 최근 변동성 측정.
  - 직전 N봉 (기본 26) 의 high/low 에서 breakout level 계산 (lookahead 방지 —
    현재 봉 제외).
  - 현재 close > breakout_level + buffer 이면 LONG candidate.
  - 현재 close < breakdown_level - buffer 이면 SHORT candidate (옵트인).
  - 거래량 확장 (volume_ratio ≥ volume_surge) + 변동성 확장 (ATR_now /
    ATR_avg ≥ vol_expansion_min) 두 필터 모두 통과해야.
  - 초고변동(ATR > 평균×high_vol_mult) 시 사이즈 자동 축소 (high_vol_size_shrink).
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Sequence

from app.strategies.base import StrategyCapability
from app.strategies._signals import StrategySignal
from app.strategies._indicators import ema, atr
from app.strategies.contract import (
    StrategyContract, StrategyContext,
    PositionSizingHint, ExitRuleDecision, SignalExplanation,
)


# ── 파라미터 ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class VolatilityBreakoutParams:
    """ATR 변동성 돌파 전략 파라미터."""

    atr_period: int = 14
    atr_avg_period: int = 42                  # 변동성 확장 비교 baseline
    range_lookback: int = 25                  # 직전 N봉 high/low (현재 봉 제외 = N+1 표본 필요)
    breakout_buffer_pct: float = 0.002        # 0.2% 버퍼
    volume_surge_min: float = 1.2             # vol_ratio >= 1.2 필요 (인자 없으면 1.0 가정)
    require_volume_filter: bool = False       # 거래량 데이터 없으면 우회 옵션
    vol_expansion_min: float = 1.0            # ATR_now / ATR_avg >= 1.0 (확장)
    high_vol_mult: float = 3.0                # 초고변동 기준
    high_vol_size_shrink: float = 0.5         # 초고변동 시 size hint 50%
    adx_max_for_breakout: float = 0.0         # 0=비활성. 양수면 ADX 이 값 이하만 후보 (range expansion 시점)
    min_candles_required: int = 0             # 0 = atr_avg_period + 5 자동
    allow_short_candidates: bool = False      # SHORT 옵트인
    base_notional_usdt: float = 100.0
    max_leverage_hint: float = 1.0


# ── 전략 ─────────────────────────────────────────────────────────


class VolatilityBreakoutContractStrategy(StrategyContract):
    """ATR 기반 변동성 돌파 후보 판단 전략 (#29 ABC 구현).

    실제 주문 명령이 아닌 *후보 신호*만 생성. RiskManager/OrderGuard/PermissionGate/
    ApprovalQueue/OrderGateway 가 실제 주문 전환을 결정한다.
    """

    capability = StrategyCapability(
        name="volatility_breakout_atr_v2",
        description=(
            "ATR-based volatility breakout. Uses ATR(14) vs ATR(42) expansion + "
            "recent N-bar range breakout + optional volume filter. Generates "
            "candidate signals only — never order intent."
        ),
        required_inputs=("closes", "highs", "lows"),
        signal_actions=("BUY", "SELL", "HOLD", "BLOCKED", "NO_ACTION", "WATCH_ONLY"),
        output_signal_class="StrategySignal",
    )
    # StrategySelectionAgent metadata —
    # breakout 은 range expansion 시점에 가장 효과적이지만 trend 초기에도 후보.
    enabled_by_default: bool = False
    preferred_regimes: tuple[str, ...] = ("RANGE", "TREND_UP", "TREND_DOWN")

    def __init__(self, params: VolatilityBreakoutParams | None = None):
        self.params = params or VolatilityBreakoutParams()

    # ── 1. generate_signal ────────────────────────────────────────

    def generate_signal(self, context: StrategyContext) -> StrategySignal:
        p = self.params

        # ── 안전 가드 ────────────────────────────────────────────
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
        if _has_high_risk_notice(context):
            return StrategySignal(
                action="BLOCKED", confidence=0.0,
                reason=("high-risk notice present (delisting / withdrawal "
                        "suspension / trading suspension) — entry blocked"),
            )

        # 데이터 수량 검증
        min_candles = (p.min_candles_required
                       if p.min_candles_required > 0
                       else p.atr_avg_period + 5)
        n = len(context.closes)
        if n < min_candles:
            return StrategySignal(
                action="NO_ACTION", confidence=0.0,
                reason=f"insufficient candles: have {n}, need {min_candles}",
            )
        if len(context.highs) != n or len(context.lows) != n:
            return StrategySignal(
                action="NO_ACTION", confidence=0.0,
                reason="OHLC length mismatch",
            )
        # range_lookback + 1 (현재 봉 제외) 보다 적으면 안 됨
        if n < p.range_lookback + 1:
            return StrategySignal(
                action="NO_ACTION", confidence=0.0,
                reason=(f"insufficient candles for range_lookback: "
                        f"need {p.range_lookback + 1}, have {n}"),
            )

        closes = context.closes
        highs = context.highs
        lows = context.lows
        current = closes[-1]

        # breakout level — 직전 N봉 (현재 봉 제외) max/min
        window_end = n - 1
        window_start = max(0, window_end - p.range_lookback)
        prev_high = max(highs[window_start:window_end])
        prev_low = min(lows[window_start:window_end])
        breakout_level = prev_high * (1 + p.breakout_buffer_pct)
        breakdown_level = prev_low * (1 - p.breakout_buffer_pct)

        # ATR + 변동성 확장 비율
        atr_now = atr(highs, lows, closes, p.atr_period)
        atr_avg = atr(highs, lows, closes, p.atr_avg_period)
        if atr_avg <= 0:
            return StrategySignal(
                action="NO_ACTION", confidence=0.0,
                reason="ATR baseline (avg) <= 0 — cannot compute expansion",
            )
        vol_expansion_ratio = atr_now / atr_avg
        is_high_vol = atr_now > atr_avg * p.high_vol_mult

        # 변동성 확장 필터 (range expansion 시점)
        if vol_expansion_ratio < p.vol_expansion_min:
            return StrategySignal(
                action="HOLD", confidence=0.0,
                reason=(f"no volatility expansion: ATR/avg={vol_expansion_ratio:.2f} "
                        f"< {p.vol_expansion_min}"),
            )

        # optional ADX 상한 (breakout 은 보통 추세 *시작* 시점)
        if p.adx_max_for_breakout > 0:
            from app.strategies._indicators import adx as adx_calc
            adx_v = adx_calc(highs, lows, closes, period=p.atr_period)
            if adx_v > p.adx_max_for_breakout:
                return StrategySignal(
                    action="HOLD", confidence=0.0,
                    reason=(f"ADX={adx_v:.1f} > {p.adx_max_for_breakout} — "
                            "trend already extended"),
                )

        # 거래량 필터 (옵션)
        volume_ratio = _compute_volume_ratio(context)
        if p.require_volume_filter and volume_ratio < p.volume_surge_min:
            return StrategySignal(
                action="HOLD", confidence=0.0,
                reason=(f"insufficient volume: ratio={volume_ratio:.2f} < "
                        f"{p.volume_surge_min}"),
            )

        stop_long = max(0.0, current - atr_now * 1.5)
        stop_short = current + atr_now * 1.5
        tp_long = current + atr_now * 3.0
        tp_short = max(0.0, current - atr_now * 3.0)

        # ── LONG 후보 ────────────────────────────────────────────
        if current > breakout_level:
            conf = _confidence_from_expansion(
                vol_expansion_ratio, volume_ratio, p,
            )
            tag = " [high-vol]" if is_high_vol else ""
            return StrategySignal(
                action="BUY",                # 후보 — 주문 명령 아님
                confidence=conf,
                reason=(f"LONG breakout candidate{tag}: close={current:.4f} > "
                        f"level={breakout_level:.4f} (prev_high={prev_high:.4f}), "
                        f"ATR/avg={vol_expansion_ratio:.2f}, "
                        f"vol={volume_ratio:.2f}"),
                entry_price=current,
                stop_loss=stop_long,
                take_profit=tp_long,
                quality_score=_quality_score(vol_expansion_ratio, volume_ratio),
            )

        # ── SHORT 후보 (옵트인) ──────────────────────────────────
        if p.allow_short_candidates and current < breakdown_level:
            conf = _confidence_from_expansion(
                vol_expansion_ratio, volume_ratio, p,
            )
            tag = " [high-vol]" if is_high_vol else ""
            return StrategySignal(
                action="SELL",
                confidence=conf,
                reason=(f"SHORT breakdown candidate{tag}: close={current:.4f} < "
                        f"level={breakdown_level:.4f} (prev_low={prev_low:.4f}), "
                        f"ATR/avg={vol_expansion_ratio:.2f}, "
                        f"vol={volume_ratio:.2f}"),
                entry_price=current,
                stop_loss=stop_short,
                take_profit=tp_short,
                quality_score=_quality_score(vol_expansion_ratio, volume_ratio),
            )

        return StrategySignal(
            action="HOLD", confidence=0.0,
            reason=(f"no breakout: close={current:.4f}, "
                    f"long_level={breakout_level:.4f}, short_level={breakdown_level:.4f}"),
        )

    # ── 2. calculate_size ────────────────────────────────────────

    def calculate_size(
        self, context: StrategyContext, signal: StrategySignal,
    ) -> PositionSizingHint:
        """confidence 비례 hint + 초고변동 시 자동 축소.

        최종 주문 수량이 아니다 (`is_final_order_size=False` 영구).
        """
        p = self.params
        if signal.action in ("BLOCKED", "NO_ACTION", "HOLD"):
            return PositionSizingHint(
                symbol=context.symbol, base_currency="USDT",
                suggested_notional_usdt=0.0,
                confidence=0.0,
                reason="no size hint for non-actionable signal",
            )
        c = max(0.0, min(1.0, signal.confidence))
        base_hint = p.base_notional_usdt * c

        # 초고변동 시 자동 축소
        shrink_applied = False
        n = len(context.closes)
        if n >= p.atr_avg_period + 1 and len(context.highs) == n and len(context.lows) == n:
            atr_now = atr(context.highs, context.lows, context.closes, p.atr_period)
            atr_avg = atr(context.highs, context.lows, context.closes, p.atr_avg_period)
            if atr_avg > 0 and atr_now > atr_avg * p.high_vol_mult:
                base_hint *= p.high_vol_size_shrink
                shrink_applied = True

        reason_parts = [
            f"hint = base({p.base_notional_usdt}) × confidence({c:.2f})",
        ]
        if shrink_applied:
            reason_parts.append(
                f"× high_vol_shrink({p.high_vol_size_shrink}) (ATR > avg × {p.high_vol_mult})",
            )
        reason_parts.append(f"= {base_hint:.2f} USDT")

        return PositionSizingHint(
            symbol=context.symbol,
            base_currency="USDT",
            suggested_notional_usdt=base_hint,
            leverage_hint=p.max_leverage_hint,
            confidence=c,
            reason=" ".join(reason_parts),
            # is_final_order_size=False, used_for_order=False 영구
        )

    # ── 3. exit_rule ─────────────────────────────────────────────

    def exit_rule(
        self, context: StrategyContext, signal: StrategySignal,
    ) -> ExitRuleDecision:
        """청산 후보 판단 — 실제 주문 명령 아님.

        조건:
          1. data_quality EXCLUDE → 전량 청산 후보 critical
          2. freshness stale → 전량 청산 후보 high
          3. high-risk notice → 전량 청산 후보 high
          4. close 가 ATR×1.5 만큼 entry_price 대비 역행 → 70% 부분 청산 normal
          5. ATR 급락 (확장 비율 < 0.5) — 변동성 축소 → 30% 부분 청산 normal
          6. 그 외 (정상) → should_exit=False
        """
        p = self.params
        if context.data_quality_grade == "EXCLUDE":
            return ExitRuleDecision(
                symbol=context.symbol,
                should_exit=True, exit_qty_fraction=1.0,
                urgency="critical",
                reason="data quality EXCLUDE — full exit candidate",
            )
        if not context.freshness_ok:
            return ExitRuleDecision(
                symbol=context.symbol,
                should_exit=True, exit_qty_fraction=1.0,
                urgency="high",
                reason="freshness stale — exit candidate (#16)",
            )
        if _has_high_risk_notice(context):
            return ExitRuleDecision(
                symbol=context.symbol,
                should_exit=True, exit_qty_fraction=1.0,
                urgency="high",
                reason="high-risk notice (delisting / suspension) — exit candidate",
            )

        n = len(context.closes)
        if n < p.atr_avg_period + 1:
            return ExitRuleDecision(
                symbol=context.symbol, should_exit=False,
                reason="insufficient candles for exit evaluation",
            )
        if len(context.highs) != n or len(context.lows) != n:
            return ExitRuleDecision(
                symbol=context.symbol, should_exit=False,
                reason="OHLC length mismatch",
            )

        atr_now = atr(context.highs, context.lows, context.closes, p.atr_period)
        atr_avg = atr(context.highs, context.lows, context.closes, p.atr_avg_period)

        # 1) ATR-based adverse move from entry_price (if signal had one)
        entry = float(getattr(signal, "entry_price", 0.0) or 0.0)
        current = context.closes[-1]
        if entry > 0 and atr_now > 0:
            adverse = entry - current  # LONG 가정 시 양수면 손실
            if signal.action == "SELL":
                adverse = current - entry  # SHORT 미러
            if adverse >= atr_now * 1.5:
                return ExitRuleDecision(
                    symbol=context.symbol,
                    should_exit=True, exit_qty_fraction=0.7,
                    urgency="normal",
                    reason=(f"adverse move {adverse:.4f} >= ATR×1.5 "
                            f"({atr_now * 1.5:.4f}) — partial exit candidate"),
                )

        # 2) 변동성 축소 (확장 비율 < 0.5)
        if atr_avg > 0 and atr_now / atr_avg < 0.5:
            return ExitRuleDecision(
                symbol=context.symbol,
                should_exit=True, exit_qty_fraction=0.3,
                urgency="normal",
                reason=(f"volatility contraction: ATR/avg="
                        f"{atr_now / atr_avg:.2f} < 0.5 — partial exit"),
            )

        return ExitRuleDecision(
            symbol=context.symbol, should_exit=False,
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
            "ATR 변동성 돌파는 거짓 돌파 (false breakout) 위험 — confidence/sizing 보수적",
        ]

        n = len(context.closes)
        if n >= p.atr_avg_period + 1 and len(context.highs) == n and len(context.lows) == n:
            atr_now = atr(context.highs, context.lows, context.closes, p.atr_period)
            atr_avg = atr(context.highs, context.lows, context.closes, p.atr_avg_period)
            evidence.append(
                f"ATR({p.atr_period})={atr_now:.6f}, ATR({p.atr_avg_period})={atr_avg:.6f}"
            )
            if atr_avg > 0:
                expansion = atr_now / atr_avg
                evidence.append(f"volatility_expansion_ratio={expansion:.2f}")
                if atr_now > atr_avg * p.high_vol_mult:
                    risks.append(
                        f"high-volatility regime (ATR > avg × {p.high_vol_mult}) — sizing auto-shrunk"
                    )
                if expansion < p.vol_expansion_min:
                    risks.append(
                        f"volatility below expansion threshold ({p.vol_expansion_min})"
                    )
        if n >= p.range_lookback + 1 and len(context.highs) == n and len(context.lows) == n:
            window_end = n - 1
            window_start = max(0, window_end - p.range_lookback)
            ph = max(context.highs[window_start:window_end])
            pl = min(context.lows[window_start:window_end])
            evidence.append(
                f"recent_high({p.range_lookback})={ph:.4f}, "
                f"recent_low={pl:.4f}, current={context.closes[-1]:.4f}"
            )
            evidence.append(
                f"breakout_level={ph * (1 + p.breakout_buffer_pct):.4f}, "
                f"breakdown_level={pl * (1 - p.breakout_buffer_pct):.4f}"
            )

        # 거래량 / trend filter
        vr = _compute_volume_ratio(context)
        evidence.append(f"volume_ratio={vr:.2f}")
        evidence.append(f"data_quality_grade={context.data_quality_grade}")
        evidence.append(f"freshness_ok={context.freshness_ok}")
        evidence.append(f"is_in_universe={context.is_in_universe}")
        evidence.append(f"regime={context.regime}")

        # 안전 위험
        if not context.freshness_ok:
            risks.append("stale market data — BUY blocked")
        if context.data_quality_grade == "EXCLUDE":
            risks.append("data quality EXCLUDE")
        if _has_high_risk_notice(context):
            risks.append("high-risk notice present (delisting / suspension)")
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
    """#18 notice_context.high_risk_symbols 매칭."""
    nc = context.notice_context
    if not nc or not isinstance(nc, dict):
        return False
    hrs = nc.get("high_risk_symbols") or []
    if not isinstance(hrs, (list, tuple)):
        return False
    sym = (context.symbol or "").upper()
    base = sym.split("-")[0].split("/")[0] if "-" in sym or "/" in sym else sym
    for s in hrs:
        s_up = str(s).upper()
        if s_up == sym or s_up == base:
            return True
    return False


def _compute_volume_ratio(context: StrategyContext) -> float:
    """context.extra 의 volume_ratio 우선, 없으면 context.volumes 에서 추정.

    volumes 가 충분히 있으면: ``current / mean(volumes[-20:-1])`` (lookahead 회피).
    데이터 없으면 1.0 (중립).
    """
    extra_vr = (context.extra or {}).get("volume_ratio")
    if isinstance(extra_vr, (int, float)) and extra_vr > 0:
        return float(extra_vr)
    vols = context.volumes
    if not vols or len(vols) < 20:
        return 1.0
    current = vols[-1]
    if current is None or current <= 0:
        return 1.0
    window = vols[-21:-1]   # 직전 20봉
    if not window:
        return 1.0
    avg = sum(window) / len(window)
    if avg <= 0:
        return 1.0
    return current / avg


def _confidence_from_expansion(
    vol_expansion_ratio: float,
    volume_ratio: float,
    p: VolatilityBreakoutParams,
) -> float:
    """ATR 확장 비율 + 거래량 비율 → confidence 0~0.9."""
    # baseline 0.5, expansion 1.0 → +0.0, expansion 2.0 → +0.2 (max 0.3)
    base = 0.5 + min(0.3, (vol_expansion_ratio - p.vol_expansion_min) * 0.2)
    # 거래량 보너스: volume_surge_min 부근 0, 그 두 배에서 +0.1
    if volume_ratio >= p.volume_surge_min:
        bonus = min(0.1, (volume_ratio - p.volume_surge_min) * 0.1)
    else:
        bonus = -0.1
    conf = max(0.0, min(0.9, base + bonus))
    return round(conf, 4)


def _quality_score(
    vol_expansion_ratio: float,
    volume_ratio: float,
) -> float:
    """SignalQualityAgent 입력 (0~100)."""
    score = 50.0 + (vol_expansion_ratio - 1.0) * 20.0 + (volume_ratio - 1.0) * 10.0
    return round(max(0.0, min(100.0, score)), 2)


def _summary_for_action(action: str, p: VolatilityBreakoutParams) -> str:
    if action == "BUY":
        return ("candidate_long: ATR volatility breakout (range expansion) — "
                "candidate only, not an order")
    if action == "SELL":
        return ("candidate_short: ATR volatility breakdown — "
                "candidate only, not an order")
    if action == "BLOCKED":
        return "volatility breakout BLOCKED (safety gate)"
    if action == "NO_ACTION":
        return "volatility breakout NO_ACTION (insufficient data)"
    return ("volatility breakout HOLD (no breakout / insufficient expansion) — "
            "candidate evaluation only")


__all__ = (
    "VolatilityBreakoutParams",
    "VolatilityBreakoutContractStrategy",
)
