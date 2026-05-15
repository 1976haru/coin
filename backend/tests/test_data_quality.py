"""체크리스트 #17 Data Quality — 회귀 테스트.

검증:
  1. 개별 체크: spread / volume / orderbook / fx / spike
  2. QualityReport 집계 — ok / has_blocking / blocks / warnings
  3. Strategy 호환 플래그: liquidity_ok / fx_anomaly_ok
  4. CLI scripts/check_data_quality.py — exit code 동작
"""
from __future__ import annotations
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.market.quality import (
    QualityCheck, QualityReport,
    assess_quote, assess_orderbook,
    check_quote_sanity, check_volume_floor, check_orderbook_depth,
    check_fx_rate_sanity, check_price_spike,
)
from app.schemas import Ticker, OrderBook


REPO_ROOT = Path(__file__).resolve().parents[2]


def make_ticker(price=100.0, bid=99.5, ask=100.5, volume=200_000.0) -> Ticker:
    return Ticker(
        symbol="BTC/USDT", price=price, bid=bid, ask=ask,
        spread_pct=(ask - bid) / bid if bid > 0 else 0.0,
        volume_24h=volume, ts=datetime.now(timezone.utc),
    )


# ── 1. 개별 체크 ──────────────────────────────────────────────────

def test_check_quote_sanity_passes_normal():
    checks = check_quote_sanity(make_ticker(), max_spread_pct=2.0)
    assert all(c.ok for c in checks)


def test_check_quote_sanity_blocks_negative_bid():
    t = make_ticker(bid=-1, ask=100.5)
    checks = check_quote_sanity(t)
    assert any(c.name == "quote" and not c.ok and c.severity == "block" for c in checks)


def test_check_quote_sanity_blocks_crossed_market():
    t = make_ticker(price=100.0, bid=101.0, ask=100.0)
    checks = check_quote_sanity(t)
    assert any("crossed market" in c.reason for c in checks)


def test_check_quote_sanity_warns_price_outside_range():
    t = make_ticker(price=200.0, bid=99.5, ask=100.5)
    checks = check_quote_sanity(t)
    warns = [c for c in checks if c.severity == "warn" and not c.ok]
    assert warns, "price 가 bid~ask 밖이면 warn"


def test_check_quote_sanity_blocks_wide_spread():
    t = make_ticker(bid=99.0, ask=110.0)  # spread ~11%
    checks = check_quote_sanity(t, max_spread_pct=0.5)
    spread_block = [c for c in checks if c.name == "spread" and not c.ok]
    assert spread_block
    assert spread_block[0].severity == "block"


def test_check_volume_floor_blocks_low_volume():
    t = make_ticker(volume=10.0)
    c = check_volume_floor(t, min_volume=100_000.0)
    assert not c.ok
    assert c.severity == "block"


def test_check_volume_floor_passes_high_volume():
    t = make_ticker(volume=500_000.0)
    c = check_volume_floor(t, min_volume=100_000.0)
    assert c.ok


def test_check_orderbook_depth_block_when_shallow():
    ob = OrderBook(symbol="BTC", bids=((100, 1.0), (99, 1.0)), asks=((101, 1.0),),
                   ts=datetime.now(timezone.utc))
    checks = check_orderbook_depth(ob, min_levels=5, min_top_size=1.0)
    blocks = [c for c in checks if not c.ok and c.severity == "block"]
    assert any(c.name == "ob_depth" for c in blocks)


def test_check_orderbook_depth_block_when_top_size_small():
    bids = tuple((100 - i, 0.1) for i in range(5))
    asks = tuple((101 + i, 0.1) for i in range(5))
    ob = OrderBook(symbol="BTC", bids=bids, asks=asks, ts=datetime.now(timezone.utc))
    checks = check_orderbook_depth(ob, min_levels=5, min_top_size=1.0)
    blocks = [c for c in checks if not c.ok and c.severity == "block"]
    assert any(c.name == "ob_top_size" for c in blocks)


def test_check_orderbook_depth_passes_when_deep_and_thick():
    bids = tuple((100 - i, 5.0) for i in range(10))
    asks = tuple((101 + i, 5.0) for i in range(10))
    ob = OrderBook(symbol="BTC", bids=bids, asks=asks, ts=datetime.now(timezone.utc))
    checks = check_orderbook_depth(ob, min_levels=5, min_top_size=1.0)
    assert all(c.ok for c in checks)


def test_check_fx_rate_sanity_passes_normal():
    c = check_fx_rate_sanity(rate=1380.0, fallback=1380.0, max_deviation_pct=5.0)
    assert c.ok


def test_check_fx_rate_sanity_blocks_extreme_deviation():
    c = check_fx_rate_sanity(rate=1500.0, fallback=1380.0, max_deviation_pct=5.0)
    assert not c.ok
    assert c.severity == "block"


def test_check_fx_rate_sanity_blocks_zero_or_negative():
    c = check_fx_rate_sanity(rate=0.0, fallback=1380.0)
    assert not c.ok
    assert c.severity == "block"


def test_check_fx_rate_sanity_warns_when_fallback_missing():
    c = check_fx_rate_sanity(rate=1380.0, fallback=0.0)
    assert c.severity == "warn"


def test_check_price_spike_blocks_extreme_jump():
    c = check_price_spike(current_price=110.0, prev_price=100.0, max_pct=8.0)
    assert not c.ok
    assert c.severity == "block"


def test_check_price_spike_passes_normal_move():
    c = check_price_spike(current_price=101.0, prev_price=100.0, max_pct=8.0)
    assert c.ok


def test_check_price_spike_warns_when_no_prev():
    c = check_price_spike(current_price=100.0, prev_price=0.0, max_pct=8.0)
    assert c.severity == "warn"


# ── 2. QualityReport 집계 ────────────────────────────────────────

def test_assess_quote_aggregates_all_checks():
    t = make_ticker()
    qr = assess_quote("BTC@upbit", t, max_spread_pct=2.0,
                      min_volume=100_000.0, prev_price=99.0, max_spike_pct=8.0)
    assert qr.label == "BTC@upbit"
    assert qr.ok
    assert len(qr.blocks) == 0


def test_assess_quote_block_when_low_volume():
    t = make_ticker(volume=10.0)
    qr = assess_quote("BTC@upbit", t, min_volume=100_000.0)
    assert not qr.ok
    assert any(c.name == "volume" for c in qr.blocks)


def test_assess_quote_blocks_propagate_to_has_blocking():
    t = make_ticker(volume=10.0)  # below floor
    qr = assess_quote("BTC@upbit", t, min_volume=100_000.0)
    assert qr.has_blocking is True


def test_quality_report_warnings_property():
    t = make_ticker(price=200.0, bid=99.5, ask=100.5)  # price out of range = warn
    qr = assess_quote("BTC@upbit", t, max_spread_pct=2.0, min_volume=100_000.0)
    assert len(qr.warnings) >= 1


# ── 3. Strategy 호환 플래그 ──────────────────────────────────────

def test_quality_report_liquidity_ok_when_clean():
    t = make_ticker()
    qr = assess_quote("BTC@upbit", t, max_spread_pct=2.0, min_volume=100_000.0)
    assert qr.liquidity_ok is True


def test_quality_report_liquidity_not_ok_on_spread_block():
    t = make_ticker(bid=99, ask=110)  # 11% spread
    qr = assess_quote("BTC@upbit", t, max_spread_pct=0.5, min_volume=100_000.0)
    assert qr.liquidity_ok is False


def test_quality_report_liquidity_not_ok_on_volume_block():
    t = make_ticker(volume=10.0)
    qr = assess_quote("BTC@upbit", t, min_volume=100_000.0)
    assert qr.liquidity_ok is False


def test_assess_orderbook_liquidity_propagates_to_report():
    ob = OrderBook(symbol="BTC", bids=((100, 0.1),), asks=((101, 0.1),),
                   ts=datetime.now(timezone.utc))
    qr = assess_orderbook("BTC@upbit", ob, min_levels=5, min_top_size=1.0)
    assert qr.liquidity_ok is False
    assert qr.has_blocking is True


def test_quality_report_fx_anomaly_ok_when_no_fx_check():
    """fx 체크가 없으면 fx_anomaly_ok=True (기본값)."""
    t = make_ticker()
    qr = assess_quote("BTC@upbit", t)
    assert qr.fx_anomaly_ok is True


def test_quality_report_fx_anomaly_not_ok_on_block():
    fx = check_fx_rate_sanity(rate=1500.0, fallback=1380.0, max_deviation_pct=5.0)
    qr = QualityReport(label="USDT/KRW", checks=(fx,))
    assert qr.fx_anomaly_ok is False
    assert qr.has_blocking is True


# ── 4. CLI scripts/check_data_quality.py ─────────────────────────

def test_script_exists_and_is_runnable():
    script = REPO_ROOT / "scripts" / "check_data_quality.py"
    assert script.is_file()


def test_script_help_runs():
    script = REPO_ROOT / "scripts" / "check_data_quality.py"
    r = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
        timeout=30,
    )
    assert r.returncode == 0
    assert "Data quality" in r.stdout or "--list-name" in r.stdout


def test_script_returns_2_when_watchlist_empty(tmp_path, monkeypatch):
    """DATABASE_URL 을 새 sqlite 로 가리켜 watchlist 비어있게 한 뒤 실행."""
    script = REPO_ROOT / "scripts" / "check_data_quality.py"
    db_path = tmp_path / "empty.db"
    env = {**__import__("os").environ, "DATABASE_URL": f"sqlite:///{db_path}"}
    r = subprocess.run(
        [sys.executable, str(script), "--json"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
        env=env, timeout=30,
    )
    assert r.returncode == 2
