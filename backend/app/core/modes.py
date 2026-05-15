"""TradingMode + ModeCapability — 체크리스트 #3.

6단계 운용 모드와 각 모드의 capability matrix를 단일 진리 소스로 정의한다.
PermissionGate / RiskManager / Executors 가 본 매트릭스를 참조하며,
새 모드/액션 추가 시 본 파일과 docs/operating_modes.md 동시 갱신.

원칙:
- 위험 capability는 명시적 true 일 때만 허용 (default deny).
- LIVE_AI_EXECUTION 도 capability 자체는 정의하되 ENABLE_AI_EXECUTION 플래그 별도 검증.
- 모드 enum 값은 환경변수 호환을 위해 string.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TradingMode(str, Enum):
    SIMULATION           = "SIMULATION"
    PAPER                = "PAPER"
    LIVE_SHADOW          = "LIVE_SHADOW"
    LIVE_MANUAL_APPROVAL = "LIVE_MANUAL_APPROVAL"
    LIVE_AI_ASSIST       = "LIVE_AI_ASSIST"
    LIVE_AI_EXECUTION    = "LIVE_AI_EXECUTION"

    # ── 호환용 property (기존 코드/테스트에서 사용) ────────────
    @property
    def allows_real_order(self) -> bool:
        return capability_for(self).can_execute_live

    @property
    def requires_manual_approval(self) -> bool:
        return capability_for(self).needs_manual_approval

    @property
    def is_paper_or_sim(self) -> bool:
        return self in {TradingMode.SIMULATION, TradingMode.PAPER}

    @property
    def allows_ai_auto_execute(self) -> bool:
        return capability_for(self).can_execute_live_ai_auto

    @property
    def allows_kimp_strategy(self) -> bool:
        return capability_for(self).can_use_kimp_strategy

    @property
    def label(self) -> str:
        return _LABELS.get(self.value, self.value)


_LABELS = {
    "SIMULATION":           "백테스트 전용",
    "PAPER":                "가상 주문",
    "LIVE_SHADOW":          "실시세 신호 기록 (주문 없음)",
    "LIVE_MANUAL_APPROVAL": "사람 승인 후 실주문",
    "LIVE_AI_ASSIST":       "AI 제안 + 사람 최종 승인",
    "LIVE_AI_EXECUTION":    "AI 제한 자동 실행 (옵트인)",
}


# ── Capability Matrix ─────────────────────────────────────────────

@dataclass(frozen=True)
class ModeCapability:
    """모드별 9개 행동 허용/금지.

    Attributes
    ----------
    can_emit_signal:
        전략이 신호를 만들고 Agent가 판단하는 행위. 모든 모드 true.
    can_run_paper_orders:
        PaperBroker 가상 체결 허용. SIM/PAPER 만.
    can_log_shadow:
        주문 시도를 송신 없이 감사 로그에만 기록. SHADOW 이상.
    needs_manual_approval:
        주문성 결정에 사람 승인이 필요한가.
    can_execute_live:
        실제 거래소로 주문이 나갈 수 있는가 (live route).
    can_execute_live_ai_auto:
        ``source="ai"`` 주문이 사람 승인 없이 LIVE 로 갈 수 있는가.
        LIVE_AI_EXECUTION + ENABLE_AI_EXECUTION 모두 true 시에만.
    can_use_kimp_strategy:
        김프/역김프 신호 생성. ENABLE_KIMP_STRATEGY 와 함께 PermissionGate 가
        2차 검증.
    can_use_futures:
        선물/파생 신호 생성. ENABLE_CRYPTO_FUTURES_LIVE 와 별도 게이트.
        본 매트릭스에서는 모든 모드 false (Phase 8 후순위).
    requires_admin_token:
        모드 변경/킬스위치/promotion 등 관리 액션에 admin token 필요.
        모든 모드 true (운영 안전).
    """

    can_emit_signal:           bool
    can_run_paper_orders:      bool
    can_log_shadow:            bool
    needs_manual_approval:     bool
    can_execute_live:          bool
    can_execute_live_ai_auto:  bool
    can_use_kimp_strategy:     bool
    can_use_futures:           bool
    requires_admin_token:      bool


_CAPABILITY_MATRIX: dict[TradingMode, ModeCapability] = {
    TradingMode.SIMULATION: ModeCapability(
        can_emit_signal           = True,
        can_run_paper_orders      = True,
        can_log_shadow            = False,
        needs_manual_approval     = False,
        can_execute_live          = False,
        can_execute_live_ai_auto  = False,
        can_use_kimp_strategy     = True,
        can_use_futures           = False,
        requires_admin_token      = True,
    ),
    TradingMode.PAPER: ModeCapability(
        can_emit_signal           = True,
        can_run_paper_orders      = True,
        can_log_shadow            = False,
        needs_manual_approval     = False,
        can_execute_live          = False,
        can_execute_live_ai_auto  = False,
        can_use_kimp_strategy     = True,
        can_use_futures           = False,
        requires_admin_token      = True,
    ),
    TradingMode.LIVE_SHADOW: ModeCapability(
        can_emit_signal           = True,
        can_run_paper_orders      = False,
        can_log_shadow            = True,
        needs_manual_approval     = False,
        can_execute_live          = False,
        can_execute_live_ai_auto  = False,
        can_use_kimp_strategy     = True,
        can_use_futures           = False,
        requires_admin_token      = True,
    ),
    TradingMode.LIVE_MANUAL_APPROVAL: ModeCapability(
        can_emit_signal           = True,
        can_run_paper_orders      = False,
        can_log_shadow            = True,
        needs_manual_approval     = True,
        can_execute_live          = True,
        can_execute_live_ai_auto  = False,
        can_use_kimp_strategy     = True,
        can_use_futures           = False,
        requires_admin_token      = True,
    ),
    TradingMode.LIVE_AI_ASSIST: ModeCapability(
        can_emit_signal           = True,
        can_run_paper_orders      = False,
        can_log_shadow            = True,
        needs_manual_approval     = True,
        can_execute_live          = True,
        can_execute_live_ai_auto  = False,
        can_use_kimp_strategy     = True,
        can_use_futures           = False,
        requires_admin_token      = True,
    ),
    TradingMode.LIVE_AI_EXECUTION: ModeCapability(
        can_emit_signal           = True,
        can_run_paper_orders      = False,
        can_log_shadow            = True,
        needs_manual_approval     = False,
        can_execute_live          = True,
        can_execute_live_ai_auto  = True,
        can_use_kimp_strategy     = True,
        can_use_futures           = False,
        requires_admin_token      = True,
    ),
}


def capability_for(mode: TradingMode) -> ModeCapability:
    """모드에 대응하는 ModeCapability 반환."""
    if mode not in _CAPABILITY_MATRIX:
        raise ValueError(f"Unknown TradingMode: {mode}")
    return _CAPABILITY_MATRIX[mode]


# ── 모드 전환 그래프 ──────────────────────────────────────────────
#
# 승격(promote): 한 단계만 위로. 건너뛰기 금지.
# 강등(downgrade): 어디서든 한 단계 아래로 가능 (사고 대응).
#                  PermissionGate / RiskManager 가 emergency 상황에서 호출.
# 비상정지: 모든 모드 → SIMULATION (KillSwitch가 별도 처리)

_PROMOTION_ORDER = [
    TradingMode.SIMULATION,
    TradingMode.PAPER,
    TradingMode.LIVE_SHADOW,
    TradingMode.LIVE_MANUAL_APPROVAL,
    TradingMode.LIVE_AI_ASSIST,
    TradingMode.LIVE_AI_EXECUTION,
]


def allowed_transitions(mode: TradingMode) -> dict[str, TradingMode | None]:
    """현재 모드에서 가능한 전이 후보.

    Returns
    -------
    dict with keys:
        ``promote``: 한 단계 위 또는 None (이미 최상위)
        ``downgrade``: 한 단계 아래 또는 None (이미 최하위)
        ``emergency``: 항상 SIMULATION
    """
    idx = _PROMOTION_ORDER.index(mode)
    return {
        "promote":   _PROMOTION_ORDER[idx + 1] if idx < len(_PROMOTION_ORDER) - 1 else None,
        "downgrade": _PROMOTION_ORDER[idx - 1] if idx > 0 else None,
        "emergency": TradingMode.SIMULATION,
    }


def promotion_distance(from_mode: TradingMode, to_mode: TradingMode) -> int:
    """승격 단계 수. 음수면 강등."""
    return _PROMOTION_ORDER.index(to_mode) - _PROMOTION_ORDER.index(from_mode)


def safe_default_mode() -> TradingMode:
    """운영 안전 기본값."""
    return TradingMode.PAPER
