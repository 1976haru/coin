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
