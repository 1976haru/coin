"""DB 패키지 — 체크리스트 #13 Database Schema, #14 Watchlist.

공개 API:
  - Base, AuditEvent, Order, AgentDecisionRecord, Position, WatchlistEntry  : ORM 모델
  - get_engine, get_session_factory, session_scope                          : 세션 헬퍼
  - reset_engine, create_all_tables                                          : 테스트/개발 유틸
  - get_database_url                                                         : 현재 DB URL

운영은 Alembic으로 마이그레이션:
    cd backend
    alembic upgrade head
"""
from .models import (
    Base, AuditEvent, Order, AgentDecisionRecord, Position, WatchlistEntry,
    CoinSymbol, CoinCandle, CoinTick, CoinOrderbookSnapshot,
    CoinSignal, CoinOrder, CoinTrade, CoinPosition, CoinRiskEvent,
    ExchangeNotice,
)
from .session import (
    get_engine, get_session_factory, session_scope,
    reset_engine, create_all_tables, get_database_url,
)

__all__ = [
    "Base", "AuditEvent", "Order", "AgentDecisionRecord", "Position", "WatchlistEntry",
    "CoinSymbol", "CoinCandle", "CoinTick", "CoinOrderbookSnapshot",
    "CoinSignal", "CoinOrder", "CoinTrade", "CoinPosition", "CoinRiskEvent",
    "ExchangeNotice",
    "get_engine", "get_session_factory", "session_scope",
    "reset_engine", "create_all_tables", "get_database_url",
]
