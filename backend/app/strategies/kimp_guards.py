"""Kimp Strategy 진입 가드 — 체크리스트 #35 Kimp Guards.

`KimpMeanReversionStrategy.generate_signal` 의 진입 단계 안전 체크를 순수 함수
모음으로 분리. 각 가드는 ``GuardResult`` 를 반환하며, ``evaluate_entry_guards``
가 순차 평가해 첫 실패의 사유와 severity 를 반환한다.

Strategy 외부에 있으므로 Agent / RiskManager / 다른 전략 등에서 재사용 가능.

설계 원칙:
  - 모든 가드는 순수 함수 — 외부 I/O 없음.
  - severity 등급:
      "pass" — 통과
      "hold" — 진입 기준 미달 (정상, 신호 없음)
      "block" — 안전상 진입 차단 (위험)
  - HOLD 와 BLOCK 을 분리해 운영자가 차이를 볼 수 있게 함 (감사 로그 도움).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal


GuardSeverity = Literal["pass", "hold", "block"]


@dataclass(frozen=True)
class GuardResult:
    """단일 가드의 평가 결과."""

    name: str
    passed: bool
    severity: GuardSeverity   # pass / hold / block
    reason: str


# ── 개별 가드 ────────────────────────────────────────────────────

def guard_entry_threshold(
    kimp_pct: float, entry_threshold_pct: float,
) -> GuardResult:
    """역김프 진입 기준 — kimp ≤ entry_threshold 일 때만 진입 가능.

    역김프 평균회귀이므로 entry_threshold 는 음수(예: -1.8%). HOLD severity 사용
    (아직 안전 위반이 아니라 단순 "기다리는 중").
    """
    if kimp_pct > entry_threshold_pct:
        return GuardResult(
            "entry_threshold", False, "hold",
            f"역김프 진입 기준 미달 ({kimp_pct:.2f}% > {entry_threshold_pct}%)",
        )
    return GuardResult(
        "entry_threshold", True, "pass",
        f"진입 기준 충족 ({kimp_pct:.2f}% ≤ {entry_threshold_pct}%)",
    )


def guard_deposit_withdrawal(deposit_withdrawal_ok: bool) -> GuardResult:
    """거래소 입출금 가능 여부. #18 Notice / NoticeRegistry 결과를 직접 받는다."""
    if not deposit_withdrawal_ok:
        return GuardResult(
            "deposit_withdrawal", False, "block",
            "입출금 중단 / 상장폐지 / 유의종목 리스크",
        )
    return GuardResult("deposit_withdrawal", True, "pass", "입출금 OK")


def guard_fx_anomaly(fx_anomaly_ok: bool) -> GuardResult:
    """USDT/KRW 환율 이상치 차단. #17 quality.check_fx_rate_sanity 결과 매핑."""
    if not fx_anomaly_ok:
        return GuardResult(
            "fx_anomaly", False, "block",
            "USDT/KRW 환율 이상치 — 계산 왜곡 방지",
        )
    return GuardResult("fx_anomaly", True, "pass", "환율 정상")


def guard_kimp_anomaly(
    kimp_pct: float,
    *,
    abnormal_min: float = -10.0,
    abnormal_max: float = +10.0,
) -> GuardResult:
    """김프율이 이상 범위에 있는지 — FX 오류/거래소 장애 의심.

    `app.market.kimp.is_anomaly` 와 같은 임계값을 사용한다.
    """
    if kimp_pct < abnormal_min or kimp_pct > abnormal_max:
        return GuardResult(
            "kimp_anomaly", False, "block",
            f"김프율 이상치: {kimp_pct:.2f}% "
            f"(정상 범위 {abnormal_min}~{abnormal_max}%)",
        )
    return GuardResult(
        "kimp_anomaly", True, "pass",
        f"김프율 정상 범위: {kimp_pct:.2f}%",
    )


def guard_liquidity(liquidity_ok: bool) -> GuardResult:
    """호가 유동성/거래량 충분 여부. #17 quality.assess_quote.liquidity_ok 매핑."""
    if not liquidity_ok:
        return GuardResult(
            "liquidity", False, "block",
            "호가 유동성 / 거래량 부족",
        )
    return GuardResult("liquidity", True, "pass", "유동성 OK")


def guard_bull_market(bull_market_block: bool) -> GuardResult:
    """BTC 급등장 시 역김프 숏 청산 위험 차단."""
    if bull_market_block:
        return GuardResult(
            "bull_market", False, "block",
            "BTC 급등장 — 역김프 숏 청산 위험",
        )
    return GuardResult("bull_market", True, "pass", "강세장 차단 안 함")


def guard_cost_vs_edge(
    expected_edge_pct: float,
    total_cost_pct: float,
) -> GuardResult:
    """기대 수익이 비용을 초과하는지. ``app.market.kimp.breakeven_threshold_pct`` 사용."""
    if expected_edge_pct <= total_cost_pct:
        return GuardResult(
            "cost_vs_edge", False, "block",
            f"비용({total_cost_pct:.3f}%) ≥ 기대수익({expected_edge_pct:.3f}%)",
        )
    return GuardResult(
        "cost_vs_edge", True, "pass",
        f"기대수익({expected_edge_pct:.3f}%) > 비용({total_cost_pct:.3f}%)",
    )


# ── Funding-rate 전용 가드 (체크리스트 #36) ─────────────────────

def guard_funding_extreme(
    rate_pct: float,
    *,
    threshold_pct: float = 1.0,
) -> GuardResult:
    """Funding rate 가 ±threshold% 초과 — 비정상 시장/거래소 장애 의심.

    `app.market.funding.is_extreme_funding` 와 동일 임계값.
    """
    from app.market.funding import is_extreme_funding
    if is_extreme_funding(rate_pct, threshold_pct=threshold_pct):
        return GuardResult(
            "funding_extreme", False, "block",
            f"펀딩비 이상치: {rate_pct:.3f}% (한계 ±{threshold_pct}% / 주기)",
        )
    return GuardResult(
        "funding_extreme", True, "pass",
        f"펀딩비 정상: {rate_pct:.3f}%",
    )


def guard_funding_direction(
    rate_pct: float,
    *,
    side: str = "short",
    block_when_unfavorable: bool = False,
) -> GuardResult:
    """포지션 방향 vs funding 부호 정합성.

    역김프 진입은 OKX/Binance 에서 short. funding 이 음수면 short 가 비용을
    낸다 → 불리. ``block_when_unfavorable=True`` 면 BLOCK, 기본은 pass + reason
    에 경고 (이중 차단 방지 — KimpStrategy 의 ``conservative_funding_cost_pct``
    가 이미 비용에 반영).
    """
    from app.market.funding import is_funding_unfavorable
    unfavorable = is_funding_unfavorable(rate_pct, side=side)  # type: ignore[arg-type]
    if not unfavorable:
        return GuardResult(
            "funding_direction", True, "pass",
            f"펀딩 방향 유리 ({side} / rate={rate_pct:.4f}%)",
        )
    if block_when_unfavorable:
        return GuardResult(
            "funding_direction", False, "block",
            f"펀딩 방향 불리 ({side} 가 냄 / rate={rate_pct:.4f}%)",
        )
    return GuardResult(
        "funding_direction", True, "pass",
        f"펀딩 방향 불리하지만 비용 반영됨 ({side} / rate={rate_pct:.4f}%)",
    )


# ── 집계 ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EntryGuardsReport:
    """6+1 단계 진입 가드 평가 결과."""

    passed: bool
    severity: GuardSeverity            # 첫 실패의 severity (또는 "pass")
    first_failure: GuardResult | None  # 첫 실패 가드 (있으면)
    results: tuple[GuardResult, ...] = field(default_factory=tuple)

    @property
    def reason(self) -> str:
        return self.first_failure.reason if self.first_failure else "모든 가드 통과"

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "severity": self.severity,
            "reason": self.reason,
            "first_failure": (
                {"name": self.first_failure.name,
                 "severity": self.first_failure.severity,
                 "reason": self.first_failure.reason}
                if self.first_failure else None
            ),
            "all_results": [
                {"name": r.name, "passed": r.passed,
                 "severity": r.severity, "reason": r.reason}
                for r in self.results
            ],
        }


def evaluate_entry_guards(
    *,
    kimp_pct: float,
    entry_threshold_pct: float,
    deposit_withdrawal_ok: bool = True,
    fx_anomaly_ok: bool = True,
    liquidity_ok: bool = True,
    bull_market_block: bool = False,
    expected_edge_pct: float = 0.0,
    total_cost_pct: float = 0.0,
    kimp_abnormal_min: float = -10.0,
    kimp_abnormal_max: float = +10.0,
    funding_rate_pct: float = 0.0,
    funding_extreme_threshold_pct: float = 1.0,
) -> EntryGuardsReport:
    """역김프 진입 가드 8단계 순차 평가.

    평가 순서 (실패 시 즉시 반환하지 않고 모든 결과 수집 — 감사 로그 가시성):
      1. entry_threshold      — HOLD if 미달 (정상)
      2. deposit_withdrawal   — BLOCK if 중단
      3. fx_anomaly           — BLOCK if 이상
      4. kimp_anomaly         — BLOCK if 김프율 이상치
      5. liquidity            — BLOCK if 부족
      6. bull_market          — BLOCK if 강세장
      7. funding_extreme      — BLOCK if |funding_rate| > 한계 (#36)
      8. cost_vs_edge         — BLOCK if 비용 ≥ 엣지
    """
    results = (
        guard_entry_threshold(kimp_pct, entry_threshold_pct),
        guard_deposit_withdrawal(deposit_withdrawal_ok),
        guard_fx_anomaly(fx_anomaly_ok),
        guard_kimp_anomaly(
            kimp_pct,
            abnormal_min=kimp_abnormal_min,
            abnormal_max=kimp_abnormal_max,
        ),
        guard_liquidity(liquidity_ok),
        guard_bull_market(bull_market_block),
        guard_funding_extreme(
            funding_rate_pct,
            threshold_pct=funding_extreme_threshold_pct,
        ),
        guard_cost_vs_edge(expected_edge_pct, total_cost_pct),
    )
    first_failure = next((r for r in results if not r.passed), None)
    if first_failure is None:
        return EntryGuardsReport(
            passed=True, severity="pass",
            first_failure=None, results=results,
        )
    return EntryGuardsReport(
        passed=False, severity=first_failure.severity,
        first_failure=first_failure, results=results,
    )
