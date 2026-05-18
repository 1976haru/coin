"""PairTradingContractStrategy — 체크리스트 #32 (확장).

기존 `PairTradingStrategy` (Protocol 기반, `pair_trading.py`) 와 별도로
#29 의 신규 `StrategyContract` ABC 를 따르는 페어 평균회귀 전략.

설계 원칙 (CLAUDE.md §2.3 / §2.4 / §3.1):
  - 본 전략은 **Signal 만 생성한다**. broker/adapter/OrderGateway/place_order/
    cancel_order/get_balance 직접 호출 절대 금지.
  - 모든 신호의 `is_order_intent` / `is_final_order_size` / `used_for_order` 영구 False.
  - 본 전략의 결과 `action` 은 `BUY` / `SELL` / `HOLD` / `BLOCKED` / `NO_ACTION` /
    `WATCH_ONLY` 만 사용 (ALLOWED_SIGNAL_ACTIONS). LONG/SHORT 방향은 `extra` 에
    *설명용 candidate_context* 로만 들어간다 — "long A short B" 를 action 으로
    반환하지 않는다.

전략 핵심:
  - 두 자산 A, B (예: BTC, ETH) 의 가격 윈도우(window 봉).
  - OLS hedge ratio h = cov(A,B) / var(B).
  - spread = A - h × B. z = (spread_now - mean) / std.
  - |z| ≥ entry_z 면 mean reversion *후보* 신호.
  - |z| ≤ exit_z 면 회귀 달성 → exit_candidate.
  - 음의 |z| 는 leg_bias 에서 표현되지만 action 은 후보 의미 그대로 BUY/SELL/HOLD
    중 하나로 표현 (BUY = "rel-cheap leg 매수 후보 + counterpart leg 매도 후보").

StrategyContext 입력:
  - ``closes``       : leg A 가격 series (tuple[float])
  - ``symbol``       : pair label, 권장 형식 "SYMBOL_A,SYMBOL_B" 또는 "BTC-USDT,ETH-USDT"
  - ``extra["closes_b"]`` : leg B 가격 series (tuple[float])
  - ``extra["symbol_a"]`` : (선택) leg A 심볼 — 없으면 symbol 의 좌측
  - ``extra["symbol_b"]`` : (선택) leg B 심볼 — 없으면 symbol 의 우측
  - 기타 freshness / data_quality / notice_context / theme_context / regime
    필드는 동일.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Any, Sequence

from app.strategies.base import StrategyCapability
from app.strategies._signals import StrategySignal
from app.strategies.contract import (
    StrategyContract, StrategyContext,
    PositionSizingHint, ExitRuleDecision, SignalExplanation,
)


# ── 파라미터 ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class PairTradingParams:
    """페어 평균회귀 전략 파라미터."""

    window: int = 60                          # z-score / hedge ratio 윈도우
    entry_z: float = 2.0                      # 진입 후보 임계값
    exit_z: float = 0.5                       # 회귀 달성 임계값
    extreme_z: float = 3.0                    # 강한 진입 임계값 — confidence 가중
    min_correlation: float = 0.6              # 상관 미달 시 BLOCKED
    min_window_required: int = 0              # 0 = window
    hedge_stability_window: int = 20          # 직전 N봉 hedge ratio 안정성 비교
    hedge_stability_max_drift: float = 0.5    # 50% 이상 변동 시 review_required
    base_pair_notional_usdt: float = 100.0    # leg 양쪽 합계의 base hint
    high_z_size_shrink: float = 0.5           # |z| >= extreme_z 시 sizing 축소
    risk_budget_low_z: float = 0.01           # 진입~중간 (entry_z~extreme_z) 비율
    risk_budget_high_z: float = 0.005         # extreme_z 이상 시 비율 (축소)


# ── 전략 ─────────────────────────────────────────────────────────


class PairTradingContractStrategy(StrategyContract):
    """BTC-ETH/SOL 등 페어 평균회귀 *후보 판단* 전략 (#29 ABC 구현).

    실제 주문 명령이 아닌 *후보 신호*만 생성. RiskManager/OrderGuard/PermissionGate/
    ApprovalQueue/OrderGateway 가 실제 주문 전환을 결정한다.

    LONG/SHORT 방향성은 ``leg_bias`` 설명 context 로만 전달되며 *주문 지시가 아니다*.
    """

    capability = StrategyCapability(
        name="pair_trading_meanrev_v2",
        description=(
            "Pair mean reversion (BTC-ETH / BTC-SOL etc). OLS hedge ratio + "
            "spread z-score. Generates candidate signals only — leg bias is "
            "for explanation, not order intent."
        ),
        required_inputs=("closes", "extra.closes_b"),
        signal_actions=("BUY", "SELL", "HOLD", "BLOCKED", "NO_ACTION", "WATCH_ONLY"),
        supports_pair=True,
        output_signal_class="StrategySignal",
    )
    # 페어 평균회귀는 range / mean reversion / relative value 시장에서 가장 효과적.
    # strong trend 에서는 spread 가 회귀하지 않을 위험.
    enabled_by_default: bool = False
    preferred_regimes: tuple[str, ...] = ("RANGE", "MEAN_REVERSION", "RELATIVE_VALUE")

    def __init__(self, params: PairTradingParams | None = None):
        self.params = params or PairTradingParams()

    # ── 1. generate_signal ────────────────────────────────────────

    def generate_signal(self, context: StrategyContext) -> StrategySignal:
        p = self.params

        # ── 안전 가드 (단일 전략과 동일) ─────────────────────────
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
                reason=("high-risk notice present on either leg "
                        "(delisting / withdrawal suspension) — entry blocked"),
            )

        # ── 데이터 추출 ──────────────────────────────────────────
        prices_b = _coerce_prices(context.extra.get("closes_b") if context.extra else None)
        if prices_b is None:
            return StrategySignal(
                action="NO_ACTION", confidence=0.0,
                reason="pair strategy requires extra['closes_b'] (leg B prices)",
            )

        prices_a = context.closes
        if not prices_a:
            return StrategySignal(
                action="NO_ACTION", confidence=0.0,
                reason="leg A prices empty",
            )

        symbol_a, symbol_b = _resolve_pair_symbols(context)

        min_required = (p.min_window_required if p.min_window_required > 0
                        else p.window)
        n = min(len(prices_a), len(prices_b))
        if n < min_required:
            return StrategySignal(
                action="NO_ACTION", confidence=0.0,
                reason=(f"insufficient pair data: have {n}, need {min_required} "
                        f"(window={p.window})"),
            )

        a = tuple(prices_a[-p.window:])
        b = tuple(prices_b[-p.window:])
        if len(a) != len(b):
            return StrategySignal(
                action="NO_ACTION", confidence=0.0,
                reason=f"pair length mismatch after window: {len(a)} vs {len(b)}",
            )

        # ── hedge ratio / spread / z-score ──────────────────────
        stats = _compute_pair_stats(a, b)
        if stats is None:
            return StrategySignal(
                action="NO_ACTION", confidence=0.0,
                reason="degenerate variance — cannot compute hedge ratio",
            )
        z = stats["z"]
        hedge = stats["hedge"]
        corr = stats["corr"]

        # 상관 미달 — 페어 가설 자체가 약함
        if corr < p.min_correlation:
            return StrategySignal(
                action="BLOCKED", confidence=0.0,
                reason=(f"pair correlation {corr:.3f} < min ({p.min_correlation}) "
                        "— pair hypothesis weak"),
            )

        abs_z = abs(z)

        # 회귀 달성 → exit candidate (HOLD 으로 보고, exit_rule 이 청산 후보 표현)
        if abs_z < p.exit_z:
            return StrategySignal(
                action="HOLD", confidence=0.0,
                reason=(f"pair spread reverted to mean: z={z:.3f} "
                        f"(|z|<{p.exit_z}) — exit candidate via exit_rule"),
            )

        # |z| 가 entry_z 미만 이면 watch only (관찰)
        if abs_z < p.entry_z:
            return StrategySignal(
                action="WATCH_ONLY", confidence=0.0,
                reason=(f"pair z={z:.3f} between exit_z={p.exit_z} and "
                        f"entry_z={p.entry_z} — watch only"),
            )

        # 진입 후보 — z > 0 → A 상대적 비쌈, B 상대적 쌈 → SELL candidate (A 매도/B 매수 bias)
        # z < 0 → 반대 → BUY candidate
        conf = _confidence_from_pair(abs_z, corr, p)
        action = "SELL" if z > 0 else "BUY"
        leg_bias = ("short_a_long_b" if z > 0 else "long_a_short_b")
        tag = " [extreme]" if abs_z >= p.extreme_z else ""

        return StrategySignal(
            action=action,                # 후보 — leg_bias 는 reason 에 설명
            confidence=conf,
            reason=(f"pair mean-reversion candidate{tag}: "
                    f"{symbol_a}/{symbol_b} z={z:.3f}, hedge={hedge:.4f}, "
                    f"corr={corr:.3f}, leg_bias={leg_bias} "
                    "(candidate only — not an order; leg bias is descriptive)"),
            entry_price=a[-1],
            stop_loss=0.0,                # 페어 stop 은 z 임계로 별도 관리
            take_profit=0.0,
            quality_score=_quality_score(abs_z, corr),
        )

    # ── 2. calculate_size ────────────────────────────────────────

    def calculate_size(
        self, context: StrategyContext, signal: StrategySignal,
    ) -> PositionSizingHint:
        """페어 양쪽 leg 합계의 base notional hint.

        - HOLD/BLOCKED/NO_ACTION/WATCH_ONLY → 0
        - |z| >= extreme_z 면 high_z_size_shrink 적용
        - data_quality WARNING 또는 notice/theme risk 있으면 hint 축소
        - is_final_order_size=False / used_for_order=False 영구
        """
        p = self.params
        if signal.action in ("BLOCKED", "NO_ACTION", "HOLD", "WATCH_ONLY"):
            return PositionSizingHint(
                symbol=context.symbol, base_currency="USDT",
                suggested_notional_usdt=0.0,
                confidence=0.0,
                reason="no size hint for non-actionable pair signal",
            )

        # |z| 재계산 (sizing 단계에서)
        abs_z = _safe_abs_z(context, p)

        c = max(0.0, min(1.0, signal.confidence))
        base = p.base_pair_notional_usdt * c

        # high-z shrink
        shrink_applied = False
        if abs_z >= p.extreme_z:
            base *= p.high_z_size_shrink
            shrink_applied = True

        # data quality WARNING → 추가 축소
        quality_shrink = False
        if context.data_quality_grade == "WARNING":
            base *= 0.7
            quality_shrink = True

        # notice/theme review_required 메타
        risk_note = ""
        if context.theme_context and context.theme_context.get("review_required_symbols"):
            risk_note = " (review_required: theme context)"
        if context.notice_context and context.notice_context.get("warning_symbols"):
            risk_note = (risk_note + " (review_required: notice warning)").strip()

        reason_parts = [
            f"pair_total_hint = base({p.base_pair_notional_usdt}) × "
            f"confidence({c:.2f})",
        ]
        if shrink_applied:
            reason_parts.append(
                f"× high_z_shrink({p.high_z_size_shrink}) (|z|>={p.extreme_z})",
            )
        if quality_shrink:
            reason_parts.append("× quality_warning_shrink(0.7)")
        reason_parts.append(f"= {base:.2f} USDT (sum of both legs)")
        reason_parts.append(
            "Final leg sizes (A vs B notional split, leverage, short-leg "
            "permissibility) are decided by RiskManager / OrderGuard / "
            "PermissionGate — not by this hint."
        )
        if risk_note:
            reason_parts.append(risk_note)

        return PositionSizingHint(
            symbol=context.symbol,
            base_currency="USDT",
            suggested_notional_usdt=base,
            leverage_hint=1.0,           # 페어는 보수적 1.0 hint
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
          4. |z| <= exit_z → 평균 회귀 달성 → 전량 청산 후보 normal
          5. |z| >= extreme_z 이고 entry 와 부호 반대 (방향 무효) → 70% partial normal
          6. correlation 하락 (< min_correlation) → 50% partial high (페어 가설 붕괴)
          7. 그 외 → should_exit=False
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
                reason="high-risk notice on leg — exit candidate",
            )

        prices_b = _coerce_prices(context.extra.get("closes_b") if context.extra else None)
        if prices_b is None:
            return ExitRuleDecision(
                symbol=context.symbol, should_exit=False,
                reason="cannot evaluate exit — leg B prices missing",
            )

        prices_a = context.closes
        n = min(len(prices_a), len(prices_b))
        if n < p.window:
            return ExitRuleDecision(
                symbol=context.symbol, should_exit=False,
                reason="insufficient pair data for exit evaluation",
            )
        a = tuple(prices_a[-p.window:])
        b = tuple(prices_b[-p.window:])
        stats = _compute_pair_stats(a, b)
        if stats is None:
            return ExitRuleDecision(
                symbol=context.symbol, should_exit=False,
                reason="degenerate variance — cannot evaluate exit",
            )
        z = stats["z"]
        corr = stats["corr"]

        # 1) 평균 회귀 달성
        if abs(z) <= p.exit_z:
            return ExitRuleDecision(
                symbol=context.symbol,
                should_exit=True, exit_qty_fraction=1.0,
                urgency="normal",
                reason=(f"pair spread reverted: |z|={abs(z):.3f} <= "
                        f"exit_z={p.exit_z} — full exit candidate"),
            )

        # 2) 페어 가설 붕괴 — correlation 하락
        if corr < p.min_correlation:
            return ExitRuleDecision(
                symbol=context.symbol,
                should_exit=True, exit_qty_fraction=0.5,
                urgency="high",
                reason=(f"pair correlation dropped to {corr:.3f} < "
                        f"{p.min_correlation} — partial exit candidate"),
            )

        # 3) 진입 방향과 부호 반대로 |z| 가 극단 확장
        if signal.action in ("BUY", "SELL"):
            entry_signed = -1.0 if signal.action == "BUY" else 1.0
            current_signed = 1.0 if z > 0 else -1.0
            if abs(z) >= p.extreme_z and current_signed != entry_signed:
                return ExitRuleDecision(
                    symbol=context.symbol,
                    should_exit=True, exit_qty_fraction=0.7,
                    urgency="normal",
                    reason=(f"pair z={z:.3f} extreme and inverted vs entry "
                            f"({signal.action}) — partial exit candidate"),
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
            "leg_bias is descriptive context, not an order instruction",
            "RiskManager / OrderGuard 가 leg 별 notional / short 가능 여부 / "
            "leverage 최종 결정",
            "pair mean reversion is *not* directionally neutral in practice — "
            "spread may diverge further before mean reversion",
            "BUY/SELL action 은 전략 판단 표현이며 직접 주문 명령이 아님",
        ]

        symbol_a, symbol_b = _resolve_pair_symbols(context)
        evidence.append(f"pair=({symbol_a}, {symbol_b})")

        prices_b = _coerce_prices(context.extra.get("closes_b") if context.extra else None)
        if prices_b is not None:
            n = min(len(context.closes), len(prices_b))
            if n >= p.window:
                a = tuple(context.closes[-p.window:])
                b = tuple(prices_b[-p.window:])
                stats = _compute_pair_stats(a, b)
                if stats is not None:
                    evidence.append(f"hedge_ratio={stats['hedge']:.4f}")
                    evidence.append(f"spread_mean={stats['mean']:.4f}")
                    evidence.append(f"spread_std={stats['std']:.4f}")
                    evidence.append(f"z_score={stats['z']:.3f}")
                    evidence.append(f"correlation={stats['corr']:.3f}")
                    leg_bias = (
                        "short_a_long_b" if stats["z"] > 0 else "long_a_short_b"
                    )
                    evidence.append(f"leg_bias={leg_bias}")
                    if stats["corr"] < p.min_correlation:
                        risks.append(
                            f"correlation {stats['corr']:.3f} below minimum "
                            f"({p.min_correlation}) — pair hypothesis weak"
                        )
                    if abs(stats["z"]) >= p.extreme_z:
                        risks.append(
                            f"extreme z={stats['z']:.3f} — divergence may "
                            "continue before reversion"
                        )

        evidence.append(f"window={p.window}")
        evidence.append(f"entry_z={p.entry_z}, exit_z={p.exit_z}")
        evidence.append(f"data_quality_grade={context.data_quality_grade}")
        evidence.append(f"freshness_ok={context.freshness_ok}")
        evidence.append(f"is_in_universe={context.is_in_universe}")
        evidence.append(f"regime={context.regime}")

        # 안전 위험
        if not context.freshness_ok:
            risks.append("stale market data — BUY blocked")
        if context.data_quality_grade == "EXCLUDE":
            risks.append("data quality EXCLUDE")
        if context.data_quality_grade == "WARNING":
            risks.append("data quality WARNING — size hint shrunk")
        if _has_high_risk_notice(context):
            risks.append("high-risk notice present on one of the legs")
        if context.theme_context and context.theme_context.get(
            "review_required_symbols",
        ):
            risks.append(
                "theme context review_required — leg may carry sector risk"
            )

        summary = _summary_for_action(signal.action)
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
    """#18 notice_context.high_risk_symbols 매칭 — pair 두 leg 중 하나라도."""
    nc = context.notice_context
    if not nc or not isinstance(nc, dict):
        return False
    hrs = nc.get("high_risk_symbols") or []
    if not isinstance(hrs, (list, tuple)):
        return False
    sa, sb = _resolve_pair_symbols(context)
    targets = []
    for s in (sa, sb):
        s_up = (s or "").upper()
        if not s_up:
            continue
        base = s_up.split("-")[0].split("/")[0]
        targets.extend([s_up, base])
    for s in hrs:
        s_up = str(s).upper()
        if s_up in targets:
            return True
    return False


def _resolve_pair_symbols(context: StrategyContext) -> tuple[str, str]:
    """Pair label 'BTC-USDT,ETH-USDT' 또는 extra symbol_a/symbol_b 우선."""
    extra = context.extra or {}
    sym_a = extra.get("symbol_a")
    sym_b = extra.get("symbol_b")
    if sym_a and sym_b:
        return str(sym_a), str(sym_b)
    sym = context.symbol or ""
    if "," in sym:
        parts = [p.strip() for p in sym.split(",", 1)]
        return parts[0], parts[1] if len(parts) > 1 else "B"
    return sym or "A", "B"


def _coerce_prices(value: Any) -> tuple[float, ...] | None:
    """leg B 가격 데이터 정규화 — None / 빈 / 비-iterable 은 None."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        try:
            return tuple(float(x) for x in value)
        except (TypeError, ValueError):
            return None
    return None


def _compute_pair_stats(
    a: Sequence[float], b: Sequence[float],
) -> dict[str, float] | None:
    """OLS hedge ratio + spread mean/std/z + correlation."""
    n = min(len(a), len(b))
    if n < 5:
        return None
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    cov_ab = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n)) / n
    var_a = sum((x - mean_a) ** 2 for x in a) / n
    var_b = sum((x - mean_b) ** 2 for x in b) / n
    if var_b <= 0 or var_a <= 0:
        return None
    hedge = cov_ab / var_b
    spread = [a[i] - hedge * b[i] for i in range(n)]
    mean_s = sum(spread) / n
    var_s = sum((s - mean_s) ** 2 for s in spread) / n
    std_s = math.sqrt(var_s) if var_s > 0 else 0.0
    if std_s <= 0:
        return None
    z = (spread[-1] - mean_s) / std_s
    corr = cov_ab / (math.sqrt(var_a) * math.sqrt(var_b))
    return {
        "hedge": hedge,
        "mean": mean_s,
        "std": std_s,
        "z": z,
        "corr": corr,
    }


def _safe_abs_z(context: StrategyContext, p: PairTradingParams) -> float:
    """sizing 단계 abs(z) 안전 계산 — 실패 시 0."""
    prices_b = _coerce_prices(context.extra.get("closes_b") if context.extra else None)
    if prices_b is None:
        return 0.0
    n = min(len(context.closes), len(prices_b))
    if n < p.window:
        return 0.0
    a = tuple(context.closes[-p.window:])
    b = tuple(prices_b[-p.window:])
    stats = _compute_pair_stats(a, b)
    if stats is None:
        return 0.0
    return abs(stats["z"])


def _confidence_from_pair(
    abs_z: float, corr: float, p: PairTradingParams,
) -> float:
    """abs(z) + correlation → confidence 0..0.9."""
    # base 0.5, 진입 임계에서 +0, 강한 임계(extreme_z) 에서 +0.3
    span = max(p.extreme_z - p.entry_z, 1e-6)
    base = 0.5 + min(0.3, (abs_z - p.entry_z) / span * 0.3)
    # correlation 보너스 — 0.6 부근 0, 0.9 이상 +0.1
    corr_bonus = min(0.1, max(0.0, (corr - 0.6) * 0.3))
    return round(max(0.0, min(0.9, base + corr_bonus)), 4)


def _quality_score(abs_z: float, corr: float) -> float:
    """SignalQualityAgent 입력 (0..100)."""
    score = 50.0 + (abs_z - 2.0) * 15.0 + (corr - 0.6) * 50.0
    return round(max(0.0, min(100.0, score)), 2)


def _summary_for_action(action: str) -> str:
    if action == "BUY":
        return ("candidate_pair_long_a_short_b: relative value spread "
                "candidate — candidate only, not an order")
    if action == "SELL":
        return ("candidate_pair_short_a_long_b: relative value spread "
                "candidate — candidate only, not an order")
    if action == "BLOCKED":
        return "pair trading BLOCKED (safety gate / correlation too low)"
    if action == "NO_ACTION":
        return "pair trading NO_ACTION (insufficient pair data)"
    if action == "WATCH_ONLY":
        return "pair trading WATCH_ONLY (z below entry threshold)"
    return ("pair trading HOLD (spread reverted or near mean) — "
            "candidate evaluation only")


__all__ = (
    "PairTradingParams",
    "PairTradingContractStrategy",
)
