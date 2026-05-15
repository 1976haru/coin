"""
PermissionGate — GPT의 route-based PermissionDecision 패턴 (최고 구현)
SIMULATION/PAPER → paper route
LIVE_SHADOW      → shadow (기록만)
LIVE_MANUAL      → approval_queue
LIVE_AI_EXECUTION → live (조건부)
"""
from dataclasses import dataclass
from app.core.modes import TradingMode


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    route: str      # "paper" | "shadow" | "approval_queue" | "live" | "blocked"
    reason: str


class PermissionGate:
    def __init__(
        self,
        mode: TradingMode,
        enable_live_trading: bool = False,
        enable_ai_execution: bool = False,
        enable_kimp_strategy: bool = False,
    ):
        self.mode = mode
        self.enable_live_trading  = enable_live_trading
        self.enable_ai_execution  = enable_ai_execution
        self.enable_kimp_strategy = enable_kimp_strategy

    def check(self, order: dict, source: str = "system") -> PermissionDecision:
        """
        GPT 패턴: route를 명시적으로 반환해 OrderGateway가 분기를 명확히 알 수 있게 함.
        """
        is_kimp = order.get("side") in {"OPEN_REVERSE_KIMP", "CLOSE_KIMP"}

        # 역김프 전략 플래그 체크
        if is_kimp and not self.enable_kimp_strategy:
            return PermissionDecision(False, "blocked", "ENABLE_KIMP_STRATEGY=false")

        # SIM/PAPER → 가상 주문만
        if self.mode.is_paper_or_sim:
            return PermissionDecision(True, "paper", f"{self.mode.value}: 가상 주문")

        # SHADOW → 기록만, 주문 금지
        if self.mode == TradingMode.LIVE_SHADOW:
            return PermissionDecision(False, "shadow", "LIVE_SHADOW: 신호 기록만, 주문 전송 금지")

        # 실거래 플래그 미활성
        if not self.enable_live_trading:
            return PermissionDecision(False, "blocked", "ENABLE_LIVE_TRADING=false")

        # LIVE_MANUAL / LIVE_AI_ASSIST → 승인 큐
        if self.mode.requires_manual_approval:
            return PermissionDecision(False, "approval_queue", f"{self.mode.value}: 수동 승인 필요")

        # LIVE_AI_EXECUTION → AI 자동 실행
        if self.mode == TradingMode.LIVE_AI_EXECUTION:
            if source == "ai" and not self.enable_ai_execution:
                return PermissionDecision(False, "blocked", "ENABLE_AI_EXECUTION=false")
            return PermissionDecision(True, "live", "실전 주문 허용")

        return PermissionDecision(False, "blocked", f"알 수 없는 모드: {self.mode}")
