"""체크리스트 #8 Shared Schemas — 공통 Enum 정의.

모듈 경계(market, strategies, risk, execution, brokers, agents, governance) 사이의
데이터 형식 불일치를 막기 위해 모든 분류 코드를 본 모듈에 집중한다.

원칙:
- str Enum 으로 정의 → JSON 직렬화 / FastAPI 응답 호환.
- 값은 소문자 snake/short 코드 (UI 라벨용 lowercase 기본).
- live 는 enum 에만 존재. 실거래 실행 기능은 본 단계에서 구현하지 않는다 (CLAUDE.md §2.2).

기존 dataclass 기반 스키마(order.py 의 `OrderType` Literal 등)와 이름이 겹치는
항목은 모두 본 파일 import 시 `from app.schemas.enums import ...` 형태로
명시적으로 가져와 충돌을 피한다.
"""
from __future__ import annotations
from enum import Enum


class TradingMode(str, Enum):
    """거래 운용 모드.

    paper/mock 은 항상 실거래 미연결. live 는 본 단계 비활성 — 값만 존재.
    백엔드 6단계 `app.core.modes.TradingMode` 와 별도로, frontend/공유 스키마용
    3단계 단순 모드를 정의한다.
    """

    PAPER = "paper"
    MOCK  = "mock"
    LIVE  = "live"


class MarketType(str, Enum):
    """시장 분류."""

    STOCK   = "stock"
    CRYPTO  = "crypto"
    FUTURES = "futures"
    ETF     = "etf"
    UNKNOWN = "unknown"


class OrderSide(str, Enum):
    BUY  = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    """주문 종류. 본 단계는 market/limit 만 사용. stop 계열은 자리만 둔다."""

    MARKET     = "market"
    LIMIT      = "limit"
    STOP       = "stop"
    STOP_LIMIT = "stop_limit"


class OrderStatus(str, Enum):
    """주문 라이프사이클 상태."""

    PENDING          = "pending"
    PENDING_APPROVAL = "pending_approval"
    APPROVED         = "approved"
    REJECTED         = "rejected"
    BLOCKED          = "blocked"
    SUBMITTED        = "submitted"
    PARTIAL          = "partial"
    FILLED           = "filled"
    CANCELLED        = "cancelled"
    EXPIRED          = "expired"


class PositionSide(str, Enum):
    LONG  = "long"
    SHORT = "short"
    FLAT  = "flat"


class RiskLevel(str, Enum):
    """리스크 게이트 판단 등급."""

    OK       = "ok"
    WARNING  = "warning"
    BLOCKED  = "blocked"


class AgentAction(str, Enum):
    """Agent 가 내릴 수 있는 추천. 실제 주문 실행으로 직결되지 않는다.

    낮은 confidence 는 WATCH_ONLY 로 매핑하라 (CLAUDE.md §2.3).
    """

    HOLD       = "hold"
    BUY        = "buy"
    SELL       = "sell"
    WATCH_ONLY = "watch_only"
    BLOCK      = "block"
