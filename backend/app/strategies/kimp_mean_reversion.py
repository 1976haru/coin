"""
KimpMeanReversionStrategy — 역김프 평균회귀 신호 생성기.
순수 신호. 주문 실행은 OrderGateway 담당.

체크리스트:
  - #29: ``capability`` 속성으로 StrategyBase Protocol 합류
  - #34: ``calculate_kimp`` 가 ``app.market.kimp`` 단일 진리 소스에 위임
  - #35: 진입 가드 6+1 단계는 ``app.strategies.kimp_guards.evaluate_entry_guards`` 위임
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from app.strategies.base import StrategyCapability
from app.strategies.kimp_guards import evaluate_entry_guards


@dataclass(frozen=True)
class KimpSignal:
    action: str             # OPEN_REVERSE_KIMP | CLOSE | HOLD | BLOCKED
    symbol: str
    kimp_pct: float
    confidence: float
    reason: str
    expected_edge_pct: float = 0.0
    cost_pct: float = 0.0
    entry_kimp_pct: float | None = None
    is_order_intent: bool = False  # CLAUDE.md §3.2: 신호 객체 필수 필드

    @property
    def is_entry(self) -> bool:
        return self.action == "OPEN_REVERSE_KIMP"

    @property
    def is_exit(self) -> bool:
        return self.action == "CLOSE"

    def to_order(self, notional_usdt: float = 100.0) -> dict:
        """OrderGateway에 전달할 주문 dict 생성"""
        return {
            "symbol":        f"{self.symbol}/USDT",
            "side":          self.action,
            "notional_usdt": notional_usdt,
            "kimp_pct":      self.kimp_pct,
            "confidence":    self.confidence,
            "reason":        self.reason,
        }


@dataclass
class KimpPosition:
    symbol: str
    entry_kimp_pct: float
    entry_time: datetime


class KimpMeanReversionStrategy:
    """
    역김프 평균회귀 전략.

    안전 체크 순서:
      1. 역김프 진입 기준 미달 → HOLD
      2. 입출금 중단 / 상장폐지 / 유의종목 → BLOCKED
      3. 환율 이상치 → BLOCKED
      4. 호가 유동성 부족 → BLOCKED
      5. BTC 급등장 (숏 청산 위험) → BLOCKED
      6. 비용 > 기대수익 → BLOCKED
      7. 모든 조건 통과 → OPEN_REVERSE_KIMP
    """

    capability = StrategyCapability(
        name="kimp_mean_reversion",
        description="한국 김프/역김프 평균회귀. 환율·유동성·입출금 안전 체크 다수.",
        required_inputs=(
            "symbol", "upbit_price_krw", "okx_price_usdt", "usdt_krw",
            "deposit_withdrawal_ok", "fx_anomaly_ok", "liquidity_ok",
        ),
        signal_actions=("OPEN_REVERSE_KIMP", "CLOSE", "HOLD", "BLOCKED"),
        supports_kimp=True,
        output_signal_class="KimpSignal",
    )

    def __init__(
        self,
        entry_threshold:    float = -1.8,
        exit_threshold:     float = -1.0,
        stop_loss:          float = -3.0,
        time_stop_minutes:  float = 15.0,
    ):
        self.entry_threshold   = entry_threshold
        self.exit_threshold    = exit_threshold
        self.stop_loss         = stop_loss
        self.time_stop_minutes = time_stop_minutes
        self.open_positions: dict[str, KimpPosition] = {}

    @staticmethod
    def calculate_kimp(
        upbit_price_krw: float,
        okx_price_usdt: float,
        usdt_krw: float,
    ) -> float:
        """체크리스트 #34: `app.market.kimp.compute_kimp_pct(strict=True)` 에 위임."""
        from app.market.kimp import compute_kimp_pct
        return compute_kimp_pct(
            upbit_price_krw, okx_price_usdt, usdt_krw, strict=True,
        )

    def generate_signal(
        self,
        symbol: str,
        upbit_price_krw: float,
        okx_price_usdt: float,
        usdt_krw: float,
        deposit_withdrawal_ok: bool = True,
        fx_anomaly_ok:         bool = True,
        liquidity_ok:          bool = True,
        bull_market_block:     bool = False,
        upbit_spread_pct:      float = 0.001,
        okx_spread_pct:        float = 0.001,
        funding_rate_pct:      float = 0.0,
        now: datetime | None = None,
    ) -> KimpSignal:
        now  = now or datetime.now(timezone.utc)
        kimp = self.calculate_kimp(upbit_price_krw, okx_price_usdt, usdt_krw)
        pos  = self.open_positions.get(symbol)

        # 비용 계산 (수수료 + 슬리피지 + 펀딩비)
        total_cost_pct  = (upbit_spread_pct + okx_spread_pct + abs(funding_rate_pct)) * 100.0
        expected_edge   = abs(kimp - self.exit_threshold)

        # ── 포지션 보유 중: 청산 체크 ─────────────────────────────
        if pos:
            elapsed_min = (now - pos.entry_time).total_seconds() / 60.0

            if kimp >= self.exit_threshold:
                del self.open_positions[symbol]
                return KimpSignal("CLOSE", symbol, kimp, 0.85,
                                  "역김프 수렴 → 평균회귀 청산",
                                  expected_edge, total_cost_pct, pos.entry_kimp_pct)

            if kimp <= self.stop_loss:
                del self.open_positions[symbol]
                return KimpSignal("CLOSE", symbol, kimp, 0.95,
                                  f"역김프 확대 손절 ({kimp:.2f}% ≤ {self.stop_loss}%)",
                                  expected_edge, total_cost_pct, pos.entry_kimp_pct)

            if elapsed_min >= self.time_stop_minutes:
                del self.open_positions[symbol]
                return KimpSignal("CLOSE", symbol, kimp, 0.65,
                                  f"시간 청산 ({elapsed_min:.1f}분 경과)",
                                  expected_edge, total_cost_pct, pos.entry_kimp_pct)

            return KimpSignal("HOLD", symbol, kimp, 0.0, "포지션 보유 중",
                              expected_edge, total_cost_pct, pos.entry_kimp_pct)

        # ── 신규 진입 가드 (체크리스트 #35 + #36) ─────────────────
        report = evaluate_entry_guards(
            kimp_pct=kimp,
            entry_threshold_pct=self.entry_threshold,
            deposit_withdrawal_ok=deposit_withdrawal_ok,
            fx_anomaly_ok=fx_anomaly_ok,
            liquidity_ok=liquidity_ok,
            bull_market_block=bull_market_block,
            expected_edge_pct=expected_edge,
            total_cost_pct=total_cost_pct,
            funding_rate_pct=funding_rate_pct,    # #36
        )
        if not report.passed:
            # severity → KimpSignal action: hold → HOLD, block → BLOCKED
            action = "HOLD" if report.severity == "hold" else "BLOCKED"
            return KimpSignal(
                action, symbol, kimp, 0.0,
                report.reason,
                expected_edge, total_cost_pct,
            )

        # ── 진입 ──────────────────────────────────────────────────
        self.open_positions[symbol] = KimpPosition(symbol, kimp, now)
        confidence = min(0.9, 0.5 + abs(kimp - self.entry_threshold) * 0.15)
        return KimpSignal("OPEN_REVERSE_KIMP", symbol, kimp, confidence,
                          f"역김프 평균회귀 진입 후보 (kimp={kimp:.2f}%, edge={expected_edge:.2f}%)",
                          expected_edge, total_cost_pct)
