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
from app.brokers.paper_trader import PaperTrader
from app.brokers.paper_market_broker import PaperMarketBrokerConfig
from app.brokers.rate_limit_guard import (
    ExchangeRateLimitRegistry, build_default_registry,
)
from app.audit.audit_log import AuditLog
from app.market.collector import MarketDataCollector, MockMarketDataSource
from app.market.freshness import FreshnessTracker, policy_from_settings
from app.market.notices import NoticeRegistry
from app.market.notice_collector import NoticeCollector, MockNoticeSource
from app.market.themes import ThemeRegistry, NewsRegistry
from app.market.theme_signals import ThemeSignalCollector, MockThemeProvider
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

# 체크리스트 #18: 거래소 공지 레지스트리 (메모리) + DB-backed collector.
# - notices (legacy 메모리 레지스트리) 는 KimpStrategy 호환용으로 유지.
# - notice_collector 는 영속 ExchangeNotice 테이블에 정규화 공지를 적재한다.
notices         = NoticeRegistry()
notice_collector = NoticeCollector(
    sources={"mock": MockNoticeSource("mock")},
)

# 체크리스트 #19: 테마/뉴스 레지스트리 (메모리) + DB-backed theme signal collector.
# - themes_registry / news_registry 는 기존 메모리 기반 — AgentOrchestrator 호환 유지.
# - theme_signal_collector 는 ``theme_signals`` 테이블에 비정형 데이터를 적재한다.
themes_registry = ThemeRegistry()
news_registry   = NewsRegistry()
theme_signal_collector = ThemeSignalCollector(
    providers={"mock": MockThemeProvider()},
)

# 체크리스트 #26: 거래소별 API rate-limit guard registry (싱글톤).
# 기본 정책 (upbit/okx/binance/mock/paper) 을 모두 사전 로드.
rate_limit_registry = build_default_registry(preload=True)


# 체크리스트 #25: PaperTrader (read-only 시세 source 기반 paper-trading 컨트롤러).
# 기본 source 는 mock. 운영자가 /api/paper/source 로 변경 가능.
paper_trader = PaperTrader(
    default_source_name="mock",
    broker_config=PaperMarketBrokerConfig(
        base_currency="USDT",
        fee_bps=5.0,
        slippage_bps=0.0,
        initial_balances={"USDT": 10_000.0},
    ),
)


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


def get_notice_collector() -> NoticeCollector:
    return notice_collector


def get_themes() -> ThemeRegistry:
    return themes_registry


def get_news() -> NewsRegistry:
    return news_registry


def get_theme_signal_collector() -> ThemeSignalCollector:
    return theme_signal_collector


def get_paper_trader() -> PaperTrader:
    return paper_trader


def get_rate_limit_registry() -> ExchangeRateLimitRegistry:
    return rate_limit_registry


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
