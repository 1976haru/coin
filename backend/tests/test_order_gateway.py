from datetime import datetime, timezone, timedelta
from app.core.config import Settings
from app.core.modes import TradingMode
from app.execution.order_gateway import OrderGateway
from app.market.freshness import check_timestamp_freshness


def settings(mode=TradingMode.PAPER):
    return Settings(trading_mode=mode, enable_live_trading=False, enable_ai_execution=False)


def test_gateway_rejects_stale_new_buy():
    gw = OrderGateway(settings())
    stale = check_timestamp_freshness(datetime.now(timezone.utc)-timedelta(seconds=99), 5, label="quote")
    res = gw.submit({"symbol":"BTC/USDT", "side":"BUY", "notional_usdt":10, "leverage":1}, {"open_positions":0}, [stale])
    assert res["status"] == "REJECTED"


def test_paper_order_accepted_when_safe():
    gw = OrderGateway(settings())
    fresh = check_timestamp_freshness(datetime.now(timezone.utc), 5, label="quote")
    res = gw.submit({"symbol":"BTC/USDT", "side":"BUY", "notional_usdt":10, "leverage":1}, {"open_positions":0}, [fresh])
    assert res["status"] == "ACCEPTED"
    assert res["route"] == "paper"


def test_live_manual_queues_approval_when_flag_enabled():
    st = Settings(trading_mode=TradingMode.LIVE_MANUAL_APPROVAL, enable_live_trading=True, enable_ai_execution=False)
    gw = OrderGateway(st)
    fresh = check_timestamp_freshness(datetime.now(timezone.utc), 5, label="quote")
    res = gw.submit({"symbol":"BTC/USDT", "side":"BUY", "notional_usdt":10, "leverage":1}, {"open_positions":0}, [fresh])
    assert res["status"] == "PENDING_APPROVAL"
