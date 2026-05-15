from datetime import datetime, timezone, timedelta
from app.strategies.kimp_mean_reversion import KimpMeanReversionStrategy


def test_kimp_calculation_negative_when_domestic_cheaper():
    s = KimpMeanReversionStrategy()
    k = s.calculate_kimp(upbit_price_krw=980, okx_price_usdt=1, usdt_krw=1000)
    assert round(k, 2) == -2.0


def test_kimp_open_and_close_on_convergence():
    s = KimpMeanReversionStrategy(entry_threshold=-1.8, exit_threshold=-1.0)
    sig = s.generate_signal("BTC", 980, 1, 1000, now=datetime.now(timezone.utc))
    assert sig.action == "OPEN_REVERSE_KIMP"
    sig2 = s.generate_signal("BTC", 991, 1, 1000, now=datetime.now(timezone.utc) + timedelta(minutes=1))
    assert sig2.action == "CLOSE"


def test_kimp_blocks_when_cost_exceeds_edge():
    s = KimpMeanReversionStrategy(entry_threshold=-1.8, exit_threshold=-1.0)
    sig = s.generate_signal("BTC", 980, 1, 1000, upbit_spread_pct=0.02, okx_spread_pct=0.02)
    assert sig.action == "BLOCKED"
