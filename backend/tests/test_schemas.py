"""체크리스트 #8 Shared Schemas — 회귀 테스트.

목적:
  1. app.schemas 단일 진입점에서 모든 핵심 타입 import 가능
  2. is_order_intent 기본값 False (CLAUDE.md §2.3, §3.2)
  3. 기존 신호/판단 dataclass 호출 호환 (is_order_intent 추가가 깨지 않음)
  4. OrderRequest / AccountSnapshot 의 dict 호환 (OrderGateway/RiskManager 진입 형식)
"""
import pytest


# ── 1. 단일 진입점 import ─────────────────────────────────────────

def test_all_schemas_importable_from_single_entrypoint():
    from app.schemas import (
        Ticker, OHLCV, KimpSnapshot, OrderBook,
        SignalBase, Action, Side,
        StrategySignal, PairSignal, KimpSignal,
        OrderRequest, OrderResult, OrderType, OrderStatus, OrderRoute,
        Position, PositionSide, PositionStatus,
        RiskDecision, AccountSnapshot,
        AgentDecision,
    )
    # 클래스들이 None이 아닌지만 확인 (타입 별칭은 제외)
    for cls in (Ticker, OHLCV, KimpSnapshot, OrderBook,
                SignalBase, StrategySignal, PairSignal, KimpSignal,
                OrderRequest, OrderResult, Position,
                RiskDecision, AccountSnapshot, AgentDecision):
        assert cls is not None


def test_canonical_types_are_same_object_as_origin():
    """schemas/__init__의 재export가 정규 위치 클래스와 동일한 객체인지 (alias 아님)"""
    from app.schemas import AgentDecision, RiskDecision, StrategySignal, KimpSignal, PairSignal
    from app.agents.orchestrator import AgentDecision as OrigAgentDecision
    from app.risk.manager import RiskDecision as OrigRiskDecision
    from app.strategies.strategies import StrategySignal as OrigStrategySignal, PairSignal as OrigPairSignal
    from app.strategies.kimp_mean_reversion import KimpSignal as OrigKimpSignal

    assert AgentDecision is OrigAgentDecision
    assert RiskDecision is OrigRiskDecision
    assert StrategySignal is OrigStrategySignal
    assert KimpSignal is OrigKimpSignal
    assert PairSignal is OrigPairSignal


# ── 2. is_order_intent 기본 False (CLAUDE.md §2.3, §3.2) ──────────

def test_agent_decision_default_is_order_intent_false():
    from app.schemas import AgentDecision
    d = AgentDecision(action="HOLD", confidence=0.0, reason="test")
    assert d.is_order_intent is False, "AgentDecision은 is_order_intent=False 기본값 필수 (CLAUDE.md §2.3)"


def test_strategy_signal_default_is_order_intent_false():
    from app.schemas import StrategySignal
    s = StrategySignal(action="HOLD", confidence=0.0, reason="test")
    assert s.is_order_intent is False


def test_kimp_signal_default_is_order_intent_false():
    from app.schemas import KimpSignal
    s = KimpSignal(action="HOLD", symbol="BTC", kimp_pct=0.0,
                   confidence=0.0, reason="test")
    assert s.is_order_intent is False


def test_pair_signal_default_is_order_intent_false():
    from app.schemas import PairSignal
    s = PairSignal(action="HOLD", symbol_a="BTC", symbol_b="ETH",
                   z_score=0.0, hedge_ratio=1.0, confidence=0.0, reason="test")
    assert s.is_order_intent is False


def test_signal_base_required_fields_present():
    from app.schemas import SignalBase
    s = SignalBase(action="HOLD", confidence=0.5, reason="test")
    assert s.action == "HOLD"
    assert s.confidence == 0.5
    assert s.reason == "test"
    assert s.is_order_intent is False
    assert s.quality_score == 0.0


# ── 3. OrderRequest / OrderResult ────────────────────────────────

def test_order_request_generates_unique_idempotency_keys():
    from app.schemas import OrderRequest
    o1 = OrderRequest(symbol="BTC/USDT", side="BUY", notional_usdt=50)
    o2 = OrderRequest(symbol="BTC/USDT", side="BUY", notional_usdt=50)
    assert o1.idempotency_key != o2.idempotency_key


def test_order_request_is_order_intent_true_by_default():
    """OrderRequest는 명시적 주문 — 신호와 달리 is_order_intent 기본 True"""
    from app.schemas import OrderRequest
    o = OrderRequest(symbol="BTC/USDT", side="BUY", notional_usdt=50)
    assert o.is_order_intent is True


def test_order_request_to_dict_compatible_with_gateway_keys():
    """OrderGateway.submit()이 사용하는 키들이 모두 to_dict에 존재해야 함"""
    from app.schemas import OrderRequest
    o = OrderRequest(symbol="BTC/USDT", side="BUY", notional_usdt=50,
                     price=100000, leverage=1.0, confidence=0.8, reason="test")
    d = o.to_dict()
    for required_key in ("symbol", "side", "notional_usdt", "price",
                         "leverage", "idempotency_key"):
        assert required_key in d, f"OrderGateway가 사용하는 키 '{required_key}' 누락"


def test_order_result_defaults():
    from app.schemas import OrderResult
    r = OrderResult(status="ACCEPTED", route="paper")
    assert r.audit == {}
    assert r.reasons == ()


def test_order_result_from_dict_normalizes_paper_broker_output():
    """PaperBroker가 반환하는 dict 형식이 OrderResult.from_dict와 호환"""
    from app.schemas import OrderResult
    paper_dict = {
        "status": "FILLED",
        "symbol": "BTC/USDT",
        "side": "BUY",
        "order_id": "paper-abc123",
        "notional_usdt": 50.0,
        "filled_price": 100050.0,
        "fee_usdt": 0.025,
        "slippage_pct": 0.05,
        "created_at": "2026-05-10T00:00:00Z",
    }
    r = OrderResult.from_dict(paper_dict)
    assert r.status == "FILLED"
    assert r.symbol == "BTC/USDT"
    assert r.fee_usdt == 0.025


# ── 4. Position ──────────────────────────────────────────────────

def test_position_defaults():
    from app.schemas import Position
    p = Position(symbol="BTC/USDT", side="LONG", entry_price=100000,
                 qty=0.001, notional_usdt=100)
    assert p.status == "OPEN"
    assert p.leverage == 1.0
    assert p.entry_ts is not None


# ── 5. AccountSnapshot ↔ RiskManager 호환 ────────────────────────

def test_account_snapshot_to_dict_drives_risk_manager():
    """AccountSnapshot.to_dict()가 RiskManager.evaluate(account=...) 인자로 동작"""
    from app.schemas import AccountSnapshot
    from app.risk.manager import RiskManager

    rm = RiskManager(
        max_order_notional_usdt=100, max_open_positions=5,
        daily_loss_limit_pct=2.0, max_leverage=2.0,
        max_consecutive_losses=5, re_entry_cooldown_min=0,
    )
    acc = AccountSnapshot(open_positions=0, emergency_stop=False)
    decision = rm.evaluate(
        order={"side": "BUY", "symbol": "BTC", "notional_usdt": 50, "leverage": 1},
        account=acc.to_dict(),
    )
    assert decision.approved is True


def test_account_snapshot_emergency_stop_propagates():
    from app.schemas import AccountSnapshot
    from app.risk.manager import RiskManager

    rm = RiskManager(
        max_order_notional_usdt=100, max_open_positions=5,
        daily_loss_limit_pct=2.0, max_leverage=2.0,
        max_consecutive_losses=5, re_entry_cooldown_min=0,
    )
    acc = AccountSnapshot(open_positions=0, emergency_stop=True)
    decision = rm.evaluate(
        order={"side": "BUY", "symbol": "BTC", "notional_usdt": 50, "leverage": 1},
        account=acc.to_dict(),
    )
    assert decision.approved is False
    assert any("Emergency Stop" in r for r in decision.reasons)


# ── 6. 기존 호출 호환성 (회귀 보호) ───────────────────────────────

def test_existing_strategy_signal_positional_call_still_works():
    """is_order_intent 추가가 기존 위치 인자 호출을 깨지 않는지"""
    from app.strategies.strategies import StrategySignal
    s = StrategySignal("BUY", 0.8, "추세 정상")
    assert s.action == "BUY"
    assert s.is_order_intent is False
    s2 = StrategySignal("BUY", 0.8, "추세", 100.0, 95.0, 110.0, 80.0)
    assert s2.entry_price == 100.0
    assert s2.quality_score == 80.0
    assert s2.is_order_intent is False


def test_existing_agent_decision_positional_call_still_works():
    from app.agents.orchestrator import AgentDecision
    d = AgentDecision("HOLD", 0.0, "테스트")
    assert d.is_order_intent is False
    d2 = AgentDecision("BUY", 0.8, "추세", 80.0, False, "설명")
    assert d2.explain_text == "설명"
    assert d2.is_order_intent is False


def test_existing_kimp_signal_keyword_call_still_works():
    from app.strategies.kimp_mean_reversion import KimpSignal
    s = KimpSignal(action="BLOCKED", symbol="BTC", kimp_pct=-2.5,
                   confidence=0.0, reason="cost > edge",
                   expected_edge_pct=0.5, cost_pct=0.6)
    assert s.action == "BLOCKED"
    assert s.is_order_intent is False


# ─────────────────────────────────────────────────────────────────
# ── 7. 신규 Pydantic v2 모델 — 체크리스트 #8 스펙 검증 ───────────
# ─────────────────────────────────────────────────────────────────

from decimal import Decimal
from pydantic import ValidationError

# 스펙 이름 alias (Pydantic 버전을 본 단계 명세대로 사용)
from app.schemas.models import (
    TradingMode, MarketType, OrderSide, OrderType, OrderStatus,
    PositionSide as PSide, RiskLevel, AgentAction,
    TradingSignal, OrderRequest, PositionSnapshot, FillEvent,
    RiskCheckResult, AgentDecision,
)


def test_enums_have_expected_values():
    assert TradingMode.PAPER.value == "paper"
    assert TradingMode.MOCK.value == "mock"
    assert TradingMode.LIVE.value == "live"
    assert MarketType.CRYPTO.value == "crypto"
    assert OrderSide.BUY.value == "buy"
    assert OrderType.MARKET.value == "market"
    assert OrderType.LIMIT.value == "limit"


def test_trading_signal_creates_successfully():
    sig = TradingSignal(
        symbol="BTC/USDT",
        action=AgentAction.BUY,
        confidence=0.7,
        reason="momentum",
    )
    assert sig.symbol == "BTC/USDT"
    assert sig.action == AgentAction.BUY
    assert sig.trading_mode == TradingMode.PAPER  # 기본 paper
    assert sig.is_order_intent is False  # 신호는 주문 의도 없음


def test_trading_signal_rejects_confidence_above_one():
    with pytest.raises(ValidationError):
        TradingSignal(
            symbol="BTC/USDT",
            action=AgentAction.BUY,
            confidence=1.5,  # > 1 → invalid
            reason="bad",
        )


def test_trading_signal_rejects_confidence_below_zero():
    with pytest.raises(ValidationError):
        TradingSignal(
            symbol="BTC/USDT",
            action=AgentAction.BUY,
            confidence=-0.01,
            reason="bad",
        )


def test_order_request_rejects_non_positive_quantity():
    with pytest.raises(ValidationError):
        OrderRequest(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0"),  # 0 → invalid
        )
    with pytest.raises(ValidationError):
        OrderRequest(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("-1"),
        )


def test_order_request_limit_requires_limit_price():
    with pytest.raises(ValidationError):
        OrderRequest(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("1"),
            limit_price=None,  # limit 인데 가격 없음 → invalid
        )


def test_order_request_limit_with_price_ok():
    o = OrderRequest(
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        limit_price=Decimal("100000"),
    )
    assert o.limit_price == Decimal("100000")


def test_order_request_default_trading_mode_is_paper():
    o = OrderRequest(
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        quantity=Decimal("1"),
    )
    assert o.trading_mode == TradingMode.PAPER  # 안전 기본값


def test_order_request_live_unapproved_is_not_executable():
    """live + approved=False 는 실행 가능 상태로 보이지 않아야 한다 (안전 안내)."""
    o = OrderRequest(
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        trading_mode=TradingMode.LIVE,
        approved=False,
    )
    assert o.is_executable is False


def test_order_request_requires_approval_default_true():
    o = OrderRequest(
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        quantity=Decimal("1"),
    )
    assert o.requires_approval is True


def test_position_snapshot_flat_with_nonzero_quantity_rejected():
    with pytest.raises(ValidationError):
        PositionSnapshot(
            symbol="BTC/USDT",
            side=PSide.FLAT,
            quantity=Decimal("0.5"),  # flat 인데 수량 있음 → invalid
        )


def test_position_snapshot_flat_with_zero_quantity_ok():
    p = PositionSnapshot(symbol="BTC/USDT", side=PSide.FLAT)
    assert p.quantity == Decimal("0")


def test_position_snapshot_long_with_quantity_ok():
    p = PositionSnapshot(
        symbol="BTC/USDT",
        side=PSide.LONG,
        quantity=Decimal("0.5"),
        avg_entry_price=Decimal("100000"),
    )
    assert p.side == PSide.LONG
    assert p.quantity == Decimal("0.5")


def test_fill_event_rejects_non_positive_price():
    with pytest.raises(ValidationError):
        FillEvent(
            fill_id="f1", order_id="o1",
            symbol="BTC/USDT", side=OrderSide.BUY,
            quantity=Decimal("1"),
            price=Decimal("0"),  # price <= 0 → invalid
        )
    with pytest.raises(ValidationError):
        FillEvent(
            fill_id="f1", order_id="o1",
            symbol="BTC/USDT", side=OrderSide.BUY,
            quantity=Decimal("1"),
            price=Decimal("-1"),
        )


def test_fill_event_default_trading_mode_paper():
    f = FillEvent(
        fill_id="f1", order_id="o1",
        symbol="BTC/USDT", side=OrderSide.BUY,
        quantity=Decimal("1"),
        price=Decimal("100000"),
    )
    assert f.trading_mode == TradingMode.PAPER
    assert f.is_simulated is True


def test_risk_check_result_blocked_with_allowed_true_rejected():
    """RiskCheckResult: risk_level=blocked 이면 allowed 는 반드시 False."""
    with pytest.raises(ValidationError):
        RiskCheckResult(
            allowed=True,
            risk_level=RiskLevel.BLOCKED,
            reason="should not be allowed when blocked",
        )


def test_risk_check_result_blocked_with_allowed_false_ok():
    r = RiskCheckResult(
        allowed=False,
        risk_level=RiskLevel.BLOCKED,
        reason="daily loss limit",
    )
    assert r.allowed is False
    assert r.risk_level == RiskLevel.BLOCKED


def test_risk_check_result_ok_allowed_true():
    r = RiskCheckResult(allowed=True, risk_level=RiskLevel.OK)
    assert r.allowed is True


def test_agent_decision_rejects_confidence_above_one():
    with pytest.raises(ValidationError):
        AgentDecision(
            agent_name="signal_quality",
            action=AgentAction.HOLD,
            confidence=1.01,  # > 1 → invalid
            reason="bad",
        )


def test_agent_decision_rejects_confidence_below_zero():
    with pytest.raises(ValidationError):
        AgentDecision(
            agent_name="signal_quality",
            action=AgentAction.HOLD,
            confidence=-0.1,  # < 0 → invalid
            reason="bad",
        )


def test_agent_decision_default_is_order_intent_false_pydantic():
    """AgentDecision (Pydantic) 도 is_order_intent 기본 False — CLAUDE.md §2.3."""
    d = AgentDecision(
        agent_name="risk_officer",
        action=AgentAction.HOLD,
        confidence=0.5,
        reason="test",
    )
    assert d.is_order_intent is False


# ── 단일 진입점 / models 서브모듈 가용성 ──────────────────────────

def test_pydantic_models_importable_from_models_submodule():
    """spec 이 요구하는 이름들이 `app.schemas.models` 에서 모두 import 가능한지."""
    from app.schemas.models import (  # noqa: F401
        TradingMode, MarketType, OrderSide, OrderType, OrderStatus,
        PositionSide, RiskLevel, AgentAction,
        TradingSignal, OrderRequest, PositionSnapshot, FillEvent,
        RiskCheckResult, AgentDecision,
        ConfiguredBaseModel, Money, utc_now,
    )


def test_no_secret_fields_in_new_models():
    """신규 Pydantic 모델 필드명에 secret 류 키워드가 들어가 있지 않아야 한다."""
    forbidden = {"api_key", "secret", "passphrase", "token", "account_no",
                 "account_number", "private_key"}
    suspects = [OrderRequest, AgentDecision, TradingSignal, PositionSnapshot,
                FillEvent, RiskCheckResult]
    for model in suspects:
        names = set(model.model_fields.keys())
        leaked = names & forbidden
        assert not leaked, f"{model.__name__} leaks secret fields: {leaked}"
