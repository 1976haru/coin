"""Promotion Gates — SIM → PAPER → SHADOW → LIVE_MANUAL → AI_ASSIST → AI_EXECUTION.

체크리스트 #64-66. 성과가 좋아도 리스크 기준 미달이면 승격 금지.
이전 위치: app/promotion/gates.py
"""
from dataclasses import dataclass
from app.core.modes import TradingMode


@dataclass(frozen=True)
class GateResult:
    passed: bool
    from_mode: TradingMode
    to_mode: TradingMode
    reason: str
    metrics: dict


def _fail(from_m, to_m, reasons: list[str], metrics: dict) -> GateResult:
    return GateResult(False, from_m, to_m, " | ".join(reasons), metrics)


def _pass(from_m, to_m, metrics: dict) -> GateResult:
    return GateResult(True, from_m, to_m, "통과", metrics)


def check_paper_gate(metrics: dict) -> GateResult:
    """PAPER → LIVE_SHADOW. 체크리스트 #65."""
    src, dst = TradingMode.PAPER, TradingMode.LIVE_SHADOW
    reasons = []
    if metrics.get("sharpe", 0) < 0.8:
        reasons.append(f"Sharpe {metrics.get('sharpe',0):.2f} < 0.8")
    if metrics.get("max_drawdown_pct", 100) > 15:
        reasons.append(f"MDD {metrics.get('max_drawdown_pct',100):.1f}% > 15%")
    if metrics.get("win_rate_pct", 0) < 45:
        reasons.append(f"승률 {metrics.get('win_rate_pct',0):.1f}% < 45%")
    if metrics.get("total_trades", 0) < 200:
        reasons.append(f"거래 수 {metrics.get('total_trades',0)} < 200")
    if metrics.get("weeks_run", 0) < 4:
        reasons.append(f"운영 {metrics.get('weeks_run',0)}주 < 4주")
    return _fail(src, dst, reasons, metrics) if reasons else _pass(src, dst, metrics)


def check_shadow_gate(metrics: dict) -> GateResult:
    """LIVE_SHADOW → LIVE_MANUAL_APPROVAL."""
    src, dst = TradingMode.LIVE_SHADOW, TradingMode.LIVE_MANUAL_APPROVAL
    reasons = []
    if metrics.get("shadow_weeks", 0) < 2:
        reasons.append(f"Shadow {metrics.get('shadow_weeks',0)}주 < 2주")
    if metrics.get("p95_latency_ms", 9999) > 500:
        reasons.append(f"P95 지연 {metrics.get('p95_latency_ms',9999)}ms > 500ms")
    if metrics.get("failure_drills_done", 0) < 4:
        reasons.append(f"장애 드릴 {metrics.get('failure_drills_done',0)}/4회")
    return _fail(src, dst, reasons, metrics) if reasons else _pass(src, dst, metrics)


def check_manual_approval_gate(metrics: dict) -> GateResult:
    """LIVE_MANUAL_APPROVAL → LIVE_AI_ASSIST. 체크리스트 #66 AI Assist Gate.

    수동 승인으로 충분한 운영 데이터가 누적된 후에만 AI 보조 모드로 승격.

    필수 기준:
      - manual_weeks ≥ 2     운영 기간
      - approval_count ≥ 100 처리한 승인 건수
      - approval_p95_response_sec ≤ 60  사용자 응답 시간 안정성
      - rejection_rate ≤ 0.4 거부율 너무 높으면 신호 품질 의심
      - daily_loss_streak < 3 연속 손실 일수
      - compliance_fatal_count == 0  ComplianceAgent fatal 0건
    """
    src, dst = TradingMode.LIVE_MANUAL_APPROVAL, TradingMode.LIVE_AI_ASSIST
    reasons = []
    if metrics.get("manual_weeks", 0) < 2:
        reasons.append(f"수동 모드 {metrics.get('manual_weeks',0)}주 < 2주")
    if metrics.get("approval_count", 0) < 100:
        reasons.append(f"승인 처리 {metrics.get('approval_count',0)}건 < 100건")
    if metrics.get("approval_p95_response_sec", 9999) > 60:
        reasons.append(
            f"P95 승인 응답 {metrics.get('approval_p95_response_sec',9999):.0f}s > 60s"
        )
    if metrics.get("rejection_rate", 1.0) > 0.4:
        reasons.append(
            f"거부율 {metrics.get('rejection_rate',1.0)*100:.1f}% > 40% — 신호 품질 의심"
        )
    if metrics.get("daily_loss_streak", 999) >= 3:
        reasons.append(f"연속 손실 {metrics.get('daily_loss_streak',999)}일 ≥ 3일")
    if metrics.get("compliance_fatal_count", 999) > 0:
        reasons.append(
            f"ComplianceAgent fatal {metrics.get('compliance_fatal_count',999)}건 — 위반 해소 필요"
        )
    return _fail(src, dst, reasons, metrics) if reasons else _pass(src, dst, metrics)


def check_ai_execution_gate(metrics: dict) -> GateResult:
    """LIVE_AI_ASSIST → LIVE_AI_EXECUTION. AI 자동 실행 승격 기준 (가장 보수적).

    필수 기준:
      - ai_assist_weeks ≥ 4     AI 보조 운영 기간
      - approval_count_ai ≥ 200 AI 제안 처리 건수
      - human_override_rate ≤ 0.2 사람이 AI 결정을 뒤집은 비율 (낮을수록 일치도↑)
      - ai_signal_sharpe ≥ 1.0  AI 제안의 위험조정수익
      - max_drawdown_pct ≤ 8.0  최근 운영의 MDD
      - compliance_fatal_count == 0  Compliance 0건
    """
    src, dst = TradingMode.LIVE_AI_ASSIST, TradingMode.LIVE_AI_EXECUTION
    reasons = []
    if metrics.get("ai_assist_weeks", 0) < 4:
        reasons.append(f"AI 보조 {metrics.get('ai_assist_weeks',0)}주 < 4주")
    if metrics.get("approval_count_ai", 0) < 200:
        reasons.append(f"AI 제안 처리 {metrics.get('approval_count_ai',0)}건 < 200건")
    if metrics.get("human_override_rate", 1.0) > 0.2:
        reasons.append(
            f"사람 오버라이드율 {metrics.get('human_override_rate',1.0)*100:.1f}% > 20%"
        )
    if metrics.get("ai_signal_sharpe", 0) < 1.0:
        reasons.append(f"AI Sharpe {metrics.get('ai_signal_sharpe',0):.2f} < 1.0")
    if metrics.get("max_drawdown_pct", 100) > 8.0:
        reasons.append(f"MDD {metrics.get('max_drawdown_pct',100):.1f}% > 8%")
    if metrics.get("compliance_fatal_count", 999) > 0:
        reasons.append(
            f"ComplianceAgent fatal {metrics.get('compliance_fatal_count',999)}건"
        )
    return _fail(src, dst, reasons, metrics) if reasons else _pass(src, dst, metrics)


def check_reversion(mode: TradingMode, metrics: dict) -> tuple[bool, TradingMode, str]:
    """승격 후 기준 미달 시 자동 강등."""
    order = [
        TradingMode.SIMULATION, TradingMode.PAPER,
        TradingMode.LIVE_SHADOW, TradingMode.LIVE_MANUAL_APPROVAL,
        TradingMode.LIVE_AI_ASSIST, TradingMode.LIVE_AI_EXECUTION,
    ]
    if abs(metrics.get("daily_pnl_pct", 0)) > 3.0 and metrics.get("daily_pnl_pct", 0) < 0:
        idx = order.index(mode)
        return True, order[max(0, idx - 1)], "일 손실 -3% 초과"
    if metrics.get("consecutive_errors", 0) > 5:
        idx = order.index(mode)
        return True, order[max(0, idx - 1)], f"연속 오류 {metrics.get('consecutive_errors')}회"
    return False, mode, ""
