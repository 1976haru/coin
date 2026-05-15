from datetime import datetime, timezone, timedelta
from app.market.freshness import check_timestamp_freshness, DataFeedState, check_feed_freshness, should_block_new_buy


def test_freshness_stale_blocks_buy():
    now = datetime.now(timezone.utc)
    stale = check_timestamp_freshness(now - timedelta(seconds=10), 5, now, "quote")
    block, reasons = should_block_new_buy(stale)
    assert block is True
    # 메시지는 한국어 ("quote: 지연 10.00s > 5.00s")
    assert "지연" in reasons[0]


def test_reconnecting_feed_is_not_fresh():
    now = datetime.now(timezone.utc)
    status = check_feed_freshness(DataFeedState(True, True, now, "upbit"), 5, now)
    assert status.ok is False
    # 메시지: "upbit: 재연결 중 — 신규 매수 금지"
    assert "재연결" in status.reason


def test_fresh_quote_allows_buy():
    """방금 들어온 시세는 BUY 차단 안 함."""
    now = datetime.now(timezone.utc)
    fresh = check_timestamp_freshness(now, 5, now, "quote")
    block, reasons = should_block_new_buy(fresh)
    assert block is False
    assert reasons == []
