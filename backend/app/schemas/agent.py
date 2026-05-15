"""Agent 판단 스키마 — 체크리스트 #8 Shared Schemas.

AgentDecision은 app.agents.orchestrator에서 정의되며 여기서 재export한다.
CLAUDE.md §2.3: AgentDecision의 is_order_intent=False 기본값은 안전 원칙상 필수.
회귀는 tests/test_schemas.py가 보장.
"""
from app.agents.orchestrator import AgentDecision

__all__ = ["AgentDecision"]
