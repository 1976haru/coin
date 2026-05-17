"""Agent 판단 스키마 — 체크리스트 #8 Shared Schemas.

두 계층:
  1. (legacy) `AgentDecision` — app.agents.orchestrator 의 dataclass 재export.
     CLAUDE.md §2.3: is_order_intent=False 기본값. 1300+ 회귀 테스트가 의존.
  2. (new) `AgentDecisionModel` — Pydantic v2 BaseModel.
     스펙 (체크리스트 #8) 의 "AgentDecision" 이 가리키는 모델.
     `app.schemas.models` 를 통해 `AgentDecision` 이름으로도 import 가능.

AgentDecision 객체는 분석/추천만 한다. 직접 주문 실행으로 연결되지 않는다.
"""
from __future__ import annotations

from datetime import datetime
from typing import List

from pydantic import Field

from app.agents.orchestrator import AgentDecision

from .common import ConfiguredBaseModel, utc_now
from .enums import AgentAction

__all__ = ["AgentDecision", "AgentDecisionModel"]


class AgentDecisionModel(ConfiguredBaseModel):
    """AI Agent 판단 (Pydantic v2).

    안전 원칙:
      - `is_order_intent=False` 기본 (CLAUDE.md §2.3) — 판단은 주문이 아니다.
      - 본 객체로 OrderRequest 를 직접 만들지 않는다. 별도 경로(RiskManager →
        OrderGuard → PermissionGate → ApprovalQueue → OrderGateway) 를 거쳐야 한다.

    Validation:
      - `confidence ∈ [0, 1]`
    """

    agent_name:   str         = Field(..., min_length=1, description="발신 Agent 이름")
    action:       AgentAction = Field(..., description="추천 액션")
    confidence:   float       = Field(..., ge=0.0, le=1.0,
                                      description="신뢰도 [0,1]")
    reason:       str         = Field(default="", description="판단 근거 요약")
    explanations: List[str]   = Field(default_factory=list,
                                      description="세부 설명 라인 (감사로그용)")
    is_order_intent: bool = Field(
        default=False,
        description="판단이 곧 주문 의도인가 — 기본 False (안전 원칙).",
    )
    ts: datetime = Field(default_factory=utc_now)
