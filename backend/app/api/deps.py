"""API 의존성 — 싱글톤 객체와 인증 헬퍼.

main.py 에 흩어져 있던 싱글톤을 한 곳에 모은다.
"""
from typing import Iterator

from fastapi import Header, HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.execution.approval_queue import ApprovalQueue
from app.execution.order_gateway import OrderGateway
from app.brokers.paper_broker import PaperBroker
from app.audit.audit_log import AuditLog
from app.market.collector import MarketDataCollector, MockMarketDataSource
from app.market.freshness import FreshnessTracker, policy_from_settings
from app.market.notices import NoticeRegistry
from app.market.themes import ThemeRegistry, NewsRegistry
from app.strategies.kimp_mean_reversion import KimpMeanReversionStrategy
from app.strategies.strategies import (
    TrendFollowingStrategy, VolatilityBreakoutStrategy, PairTradingStrategy,
)
from app.agents.orchestrator import AgentOrchestrator


# ── 싱글톤 (프로세스 수명) ────────────────────────────────────────
settings        = get_settings()
approvals       = ApprovalQueue()
audit           = AuditLog()
paper_broker    = PaperBroker()
gateway         = OrderGateway(settings, approvals=approvals, audit=audit, paper_broker=paper_broker)
kimp_strategy   = KimpMeanReversionStrategy()
trend_strategy  = TrendFollowingStrategy()
vol_strategy    = VolatilityBreakoutStrategy()
pair_strategy   = PairTradingStrategy()
agent           = AgentOrchestrator()

# 체크리스트 #16: 시세 신선도 tracker (싱글톤)
freshness_tracker = FreshnessTracker(policy=policy_from_settings())

# 체크리스트 #15: 시세 수집기 (#21·#22 구현 전까지는 결정론적 Mock 사용)
# tracker 연결 — 수집 성공 시 mark_seen 자동 호출.
collector       = MarketDataCollector(
    sources={
        "upbit":   MockMarketDataSource("upbit"),
        "okx":     MockMarketDataSource("okx"),
        "binance": MockMarketDataSource("binance"),
    },
    freshness_threshold_sec=settings.freshness_threshold_sec,
    freshness_tracker=freshness_tracker,
)

# 체크리스트 #18: 거래소 공지 레지스트리 (메모리)
notices         = NoticeRegistry()

# 체크리스트 #19: 테마/뉴스 레지스트리 (메모리)
themes_registry = ThemeRegistry()
news_registry   = NewsRegistry()


def get_settings_dep():
    return settings


def get_gateway():
    return gateway


def get_approvals():
    return approvals


def get_audit():
    return audit


def get_kimp_strategy():
    return kimp_strategy


def get_trend_strategy():
    return trend_strategy


def get_agent():
    return agent


def get_collector() -> MarketDataCollector:
    return collector


def get_freshness_tracker() -> FreshnessTracker:
    return freshness_tracker


def get_notices() -> NoticeRegistry:
    return notices


def get_themes() -> ThemeRegistry:
    return themes_registry


def get_news() -> NewsRegistry:
    return news_registry


def verify_admin(x_admin_token: str = Header(default="")):
    """관리자 토큰 검증."""
    if x_admin_token != settings.admin_token:
        raise HTTPException(401, "Admin token required")


# ── DB 세션 (체크리스트 #13/#14) ─────────────────────────────────
_db_initialized = False


def get_db() -> Iterator[Session]:
    """FastAPI 의존성: SQLAlchemy 세션. 첫 호출 시 테이블 자동 생성.

    테스트는 ``app.dependency_overrides[get_db]`` 로 in-memory sqlite를 주입한다.
    """
    global _db_initialized
    from app.db.session import get_session_factory, create_all_tables
    if not _db_initialized:
        try:
            create_all_tables()
        except Exception:
            pass
        _db_initialized = True
    Sf = get_session_factory()
    s = Sf()
    try:
        yield s
    finally:
        s.close()
