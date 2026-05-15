"""승격/검증 게이트 패키지.

체크리스트 #64 Promotion Gate, #65 Paper Gate, #66 AI Assist Gate.
"""
from .promotion_gates import (
    GateResult,
    check_paper_gate,
    check_shadow_gate,
    check_reversion,
)

__all__ = ["GateResult", "check_paper_gate", "check_shadow_gate", "check_reversion"]
