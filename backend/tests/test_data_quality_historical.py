"""체크리스트 #17 Data Quality — 과거 candle 품질 검증 회귀 테스트.

기존 `test_data_quality.py` 는 live ticker quality(quality.py) 를 다룬다.
본 파일은 *historical candle* 계층(data_quality.py)을 검증한다 — 두 layer 는
서로 다른 모듈이며 회귀 테스트도 분리한다.

검증:
  1. 6개 검사 — missing / duplicate / OHLC / volume / outlier / off-universe
  2. day grade 산출 (GOOD/WARNING/EXCLUDE)
  3. BacktestPromotionGuard
  4. DB loader (load_candles_for_day) — coin_candle 와 정합
  5. CLI historical 모드 — exit code 0/2, --output json, --fail-on-exclude
  6. GET /api/market/data-quality/summary (public, secret 미노출)
  7. 정적 금지 문자열 부재
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from datetime import date as _date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, CoinCandle
from app.market.data_quality import (
    DataQualityGrade, DataQualityConfig,
    CandleRecord, DataQualityDayReport,
    check_missing, check_duplicates, check_ohlc_validity,
    check_volume_anomalies, check_price_outliers, check_off_universe,
    expected_candle_count, run_day_check,
    BacktestPromotionGuard, PromotionEvaluation,
    load_candles_for_day,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _ts(day: _date, minute: int) -> datetime:
    base = datetime.combine(day, time.min, tzinfo=timezone.utc)
    return base + timedelta(minutes=minute)


def _mk_candle(day: _date, minute: int, *, symbol="BTC", exchange="mock",
               timeframe="1m", price=100.0, volume=1.0) -> CandleRecord:
    return CandleRecord(
        exchange=exchange, symbol=symbol, timeframe=timeframe,
        ts=_ts(day, minute),
        open=price, high=price * 1.001, low=price * 0.999,
        close=price, volume=volume,
    )


# 안정된 회귀를 위해 "오늘"이 아니라 충분히 과거인 날짜를 사용한다 — 그렇지 않으면
# day=오늘 의 늦은 시각 candle 이 clock-now 보다 미래가 되어 future_ts → EXCLUDE.
DAY = _date(2026, 5, 1)


# ── 1. expected_candle_count ───────────────────────────────────

def test_expected_candle_count_per_timeframe():
    assert expected_candle_count("1m",  DAY) == 1440
    assert expected_candle_count("5m",  DAY) == 288
    assert expected_candle_count("15m", DAY) == 96
    assert expected_candle_count("1h",  DAY) == 24
    assert expected_candle_count("4h",  DAY) == 6
    assert expected_candle_count("1d",  DAY) == 1


# ── 2. 누락 ────────────────────────────────────────────────────

def test_check_missing_zero_when_complete_day():
    candles = [_mk_candle(DAY, m) for m in range(1440)]
    missing, rate = check_missing(candles, "1m", DAY)
    assert missing == 0
    assert rate == 0.0


def test_check_missing_counts_gap():
    # 100개만 있는 1m day
    candles = [_mk_candle(DAY, m) for m in range(100)]
    missing, rate = check_missing(candles, "1m", DAY)
    assert missing == 1440 - 100
    assert rate == pytest.approx((1440 - 100) / 1440)


def test_check_missing_ignores_duplicate_ts_in_count():
    # 동일 ts 2개 + 그 외 9개 = 10 unique ts → missing = 1440 - 10
    candles = [_mk_candle(DAY, m) for m in range(10)]
    candles.append(_mk_candle(DAY, 0))  # 중복
    missing, _ = check_missing(candles, "1m", DAY)
    assert missing == 1440 - 10


# ── 3. 중복 ────────────────────────────────────────────────────

def test_check_duplicates_none_when_unique():
    candles = [_mk_candle(DAY, m) for m in range(10)]
    assert check_duplicates(candles) == 0


def test_check_duplicates_counts_extras():
    candles = [_mk_candle(DAY, 0), _mk_candle(DAY, 0), _mk_candle(DAY, 0),
               _mk_candle(DAY, 1), _mk_candle(DAY, 1)]
    # ts=0 은 3개(중복 +2), ts=1 은 2개(중복 +1) → 총 3
    assert check_duplicates(candles) == 3


# ── 4. OHLC ────────────────────────────────────────────────────

def test_check_ohlc_validity_clean():
    candles = [_mk_candle(DAY, m) for m in range(5)]
    assert check_ohlc_validity(candles) == 0


def test_check_ohlc_validity_catches_high_below_low():
    bad = CandleRecord(exchange="mock", symbol="BTC", timeframe="1m",
                       ts=_ts(DAY, 0), open=100, high=99, low=101,
                       close=100, volume=1.0)
    assert check_ohlc_validity([bad]) == 1


def test_check_ohlc_validity_catches_zero_or_negative_price():
    z = CandleRecord(exchange="mock", symbol="BTC", timeframe="1m",
                     ts=_ts(DAY, 0), open=0, high=0, low=0,
                     close=0, volume=1.0)
    n = CandleRecord(exchange="mock", symbol="BTC", timeframe="1m",
                     ts=_ts(DAY, 1), open=-1, high=100, low=99,
                     close=100, volume=1.0)
    assert check_ohlc_validity([z, n]) == 2


def test_check_ohlc_validity_catches_open_above_high():
    bad = CandleRecord(exchange="mock", symbol="BTC", timeframe="1m",
                       ts=_ts(DAY, 0), open=200, high=100, low=99,
                       close=99, volume=1.0)
    assert check_ohlc_validity([bad]) == 1


# ── 5. volume ──────────────────────────────────────────────────

def test_check_volume_anomalies_clean():
    candles = [_mk_candle(DAY, m, volume=1.0) for m in range(10)]
    neg, zero, spike = check_volume_anomalies(candles)
    assert neg == 0 and zero == 0 and spike == 0


def test_check_volume_anomalies_counts_negative():
    candles = [
        CandleRecord(exchange="mock", symbol="BTC", timeframe="1m",
                     ts=_ts(DAY, 0), open=100, high=101, low=99,
                     close=100, volume=-1.0),
        _mk_candle(DAY, 1, volume=1.0),
    ]
    neg, _, _ = check_volume_anomalies(candles)
    assert neg == 1


def test_check_volume_anomalies_counts_zero():
    candles = [_mk_candle(DAY, m, volume=(0.0 if m < 3 else 1.0))
               for m in range(10)]
    _, zero, _ = check_volume_anomalies(candles)
    assert zero == 3


def test_check_volume_anomalies_detects_spike():
    # 20개의 1.0 + 1개의 5000.0 (rolling median 대비 5000배)
    cfg = DataQualityConfig(volume_spike_multiplier=100.0,
                            volume_rolling_window=20)
    candles = [_mk_candle(DAY, m, volume=1.0) for m in range(20)]
    candles.append(_mk_candle(DAY, 20, volume=5000.0))
    _, _, spike = check_volume_anomalies(candles, cfg)
    assert spike >= 1


# ── 6. 가격 outlier ────────────────────────────────────────────

def test_check_price_outliers_none_when_stable():
    candles = [_mk_candle(DAY, m, price=100.0 + 0.1 * m) for m in range(10)]
    w, e = check_price_outliers(candles)
    assert w == 0 and e == 0


def test_check_price_outliers_warning_at_60pct():
    candles = [
        _mk_candle(DAY, 0, price=100.0),
        _mk_candle(DAY, 1, price=160.0),  # +60%
    ]
    w, e = check_price_outliers(candles)
    assert w == 1
    assert e == 0


def test_check_price_outliers_exclude_at_120pct():
    candles = [
        _mk_candle(DAY, 0, price=100.0),
        _mk_candle(DAY, 1, price=220.0),  # +120%
    ]
    w, e = check_price_outliers(candles)
    assert e == 1


# ── 7. off-universe ────────────────────────────────────────────

def test_check_off_universe_unknown_exchange_counted():
    c = CandleRecord(exchange="ftx", symbol="BTC", timeframe="1m",
                     ts=_ts(DAY, 0), open=100, high=101, low=99,
                     close=100, volume=1.0)
    u, f, ow, g = check_off_universe([c], timeframe="1m", day=DAY,
                                     config=DataQualityConfig())
    assert u == 1


def test_check_off_universe_future_ts_counted():
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    c = CandleRecord(exchange="mock", symbol="BTC", timeframe="1m",
                     ts=future, open=100, high=101, low=99,
                     close=100, volume=1.0)
    u, f, ow, g = check_off_universe([c], timeframe="1m", day=DAY,
                                     config=DataQualityConfig())
    assert f == 1


def test_check_off_universe_off_watchlist_counted():
    c = _mk_candle(DAY, 0)  # BTC@mock
    _, _, ow, _ = check_off_universe(
        [c], timeframe="1m", day=DAY,
        watchlist_symbols=[("ETH", "mock")],
    )
    assert ow == 1


def test_check_off_universe_grid_mismatch_counted():
    # 1m grid 인데 30 sec 오프셋
    off_grid = _ts(DAY, 0) + timedelta(seconds=30)
    c = CandleRecord(exchange="mock", symbol="BTC", timeframe="1m",
                     ts=off_grid, open=100, high=101, low=99,
                     close=100, volume=1.0)
    _, _, _, g = check_off_universe([c], timeframe="1m", day=DAY)
    assert g == 1


# ── 8. run_day_check — Grade 산출 ──────────────────────────────

def test_run_day_check_good_when_complete_and_clean():
    candles = [_mk_candle(DAY, m, volume=1.0) for m in range(1440)]
    rep = run_day_check(candles, symbol="BTC", exchange="mock",
                        timeframe="1m", day=DAY)
    assert rep.grade == DataQualityGrade.GOOD


def test_run_day_check_warning_when_minor_missing():
    # 1430개 = 99.3% 만, 누락률 ~0.69% → WARNING
    candles = [_mk_candle(DAY, m) for m in range(1430)]
    rep = run_day_check(candles, symbol="BTC", exchange="mock",
                        timeframe="1m", day=DAY)
    assert rep.grade == DataQualityGrade.WARNING


def test_run_day_check_exclude_when_heavy_missing():
    # 1000개 = 30% 누락 > 1% → EXCLUDE
    candles = [_mk_candle(DAY, m) for m in range(1000)]
    rep = run_day_check(candles, symbol="BTC", exchange="mock",
                        timeframe="1m", day=DAY)
    assert rep.grade == DataQualityGrade.EXCLUDE


def test_run_day_check_exclude_when_negative_volume():
    candles = [_mk_candle(DAY, m, volume=1.0) for m in range(1440)]
    # 한 개를 음수로 교체
    bad = CandleRecord(exchange="mock", symbol="BTC", timeframe="1m",
                       ts=_ts(DAY, 0), open=100, high=101, low=99,
                       close=100, volume=-1.0)
    candles[0] = bad
    rep = run_day_check(candles, symbol="BTC", exchange="mock",
                        timeframe="1m", day=DAY)
    assert rep.grade == DataQualityGrade.EXCLUDE


def test_run_day_check_exclude_when_extreme_return():
    candles = [_mk_candle(DAY, m, price=100.0) for m in range(1440)]
    candles[1] = _mk_candle(DAY, 1, price=300.0)   # +200% (>90)
    rep = run_day_check(candles, symbol="BTC", exchange="mock",
                        timeframe="1m", day=DAY)
    assert rep.grade == DataQualityGrade.EXCLUDE


def test_run_day_check_reasons_include_clean_when_perfect():
    candles = [_mk_candle(DAY, m, volume=1.0) for m in range(1440)]
    rep = run_day_check(candles, symbol="BTC", exchange="mock",
                        timeframe="1m", day=DAY)
    assert "clean" in rep.reasons


def test_run_day_check_as_dict_keys():
    candles = [_mk_candle(DAY, m, volume=1.0) for m in range(1440)]
    rep = run_day_check(candles, symbol="BTC", exchange="mock",
                        timeframe="1m", day=DAY)
    d = rep.as_dict()
    expected = {
        "symbol", "exchange", "timeframe", "date",
        "expected_count", "actual_count",
        "missing_count", "missing_rate",
        "duplicate_count", "invalid_ohlc_count",
        "volume_anomaly_count", "price_outlier_count",
        "off_universe_count", "future_timestamp_count",
        "grade", "reasons",
    }
    assert expected.issubset(d.keys())


# ── 9. BacktestPromotionGuard ──────────────────────────────────

def _mk_report(grade: DataQualityGrade, day: _date) -> DataQualityDayReport:
    return DataQualityDayReport(
        symbol="BTC", exchange="mock", timeframe="1m", date=day,
        expected_count=1440, actual_count=1440,
        missing_count=0, missing_rate=0.0,
        duplicate_count=0, invalid_ohlc_count=0,
        volume_anomaly_count=0, price_outlier_count=0,
        off_universe_count=0, future_timestamp_count=0,
        grade=grade, reasons=("test",),
    )


def test_promotion_allowed_all_good():
    reports = [_mk_report(DataQualityGrade.GOOD, DAY + timedelta(days=i))
               for i in range(10)]
    e = BacktestPromotionGuard().evaluate(reports)
    assert e.allowed is True
    assert e.reason == "approved"


def test_promotion_blocked_when_any_exclude():
    reports = [_mk_report(DataQualityGrade.GOOD, DAY + timedelta(days=i))
               for i in range(9)]
    reports.append(_mk_report(DataQualityGrade.EXCLUDE, DAY + timedelta(days=9)))
    e = BacktestPromotionGuard().evaluate(reports)
    assert e.allowed is False
    assert e.reason == "blocked_by_excluded_data_quality_day"


def test_promotion_blocked_when_low_good_ratio():
    reports = [_mk_report(DataQualityGrade.GOOD, DAY + timedelta(days=i))
               for i in range(8)]
    reports.extend(_mk_report(DataQualityGrade.WARNING, DAY + timedelta(days=8 + i))
                   for i in range(2))
    # GOOD = 80%, default min = 90% → 차단
    e = BacktestPromotionGuard().evaluate(reports)
    assert e.allowed is False
    assert e.reason == "blocked_by_low_good_data_ratio"


def test_promotion_warning_within_limit_allowed():
    # GOOD 9, WARNING 1 → GOOD 90%, WARN 10%
    reports = [_mk_report(DataQualityGrade.GOOD, DAY + timedelta(days=i))
               for i in range(9)]
    reports.append(_mk_report(DataQualityGrade.WARNING, DAY + timedelta(days=9)))
    e = BacktestPromotionGuard().evaluate(reports)
    assert e.allowed is True
    assert e.reason == "warning_data_allowed_but_limited"


def test_promotion_empty_reports_blocked():
    e = BacktestPromotionGuard().evaluate([])
    assert e.allowed is False
    assert e.reason == "blocked_by_no_data_quality_reports"


# ── 10. DB loader 정합성 ────────────────────────────────────────

@pytest.fixture
def session():
    eng = create_engine(
        "sqlite:///:memory:", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    Sf = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    with Sf() as s:
        yield s
    eng.dispose()


def test_load_candles_for_day_reads_coin_candle(session):
    # 1m candle 3개 적재
    for m in range(3):
        session.add(CoinCandle(
            exchange="mock", symbol="BTC", interval="1m", ts=_ts(DAY, m),
            open=100, high=101, low=99, close=100, volume=1.0,
            source="test", meta={},
        ))
    session.commit()
    rows = load_candles_for_day(session, symbol="BTC", exchange="mock",
                                timeframe="1m", day=DAY)
    assert len(rows) == 3
    assert all(isinstance(r, CandleRecord) for r in rows)
    assert {r.symbol for r in rows} == {"BTC"}


def test_load_candles_for_day_ignores_other_day(session):
    other = DAY + timedelta(days=1)
    session.add(CoinCandle(
        exchange="mock", symbol="BTC", interval="1m",
        ts=_ts(other, 0), open=100, high=101, low=99, close=100,
        volume=1.0, source="test", meta={},
    ))
    session.commit()
    rows = load_candles_for_day(session, symbol="BTC", exchange="mock",
                                timeframe="1m", day=DAY)
    assert rows == []


# ── 11. CLI historical 모드 ────────────────────────────────────

CLI = REPO_ROOT / "scripts" / "check_data_quality.py"


def _run_cli(args, env_extra=None, timeout=30):
    env = {**os.environ}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
        env=env, timeout=timeout,
    )


def test_cli_help_still_lists_legacy_args():
    r = _run_cli(["--help"])
    assert r.returncode == 0
    assert "--list-name" in r.stdout
    # 신규 historical 옵션 노출
    assert "--symbol" in r.stdout
    assert "--fail-on-exclude" in r.stdout


def test_cli_historical_missing_timeframe_returns_2(tmp_path):
    db_path = tmp_path / "h1.db"
    env = {"DATABASE_URL": f"sqlite:///{db_path}"}
    r = _run_cli(
        ["--symbol", "BTC", "--exchange", "mock",
         "--date", DAY.isoformat()],
        env_extra=env,
    )
    assert r.returncode == 2


def test_cli_historical_empty_db_outputs_exclude(tmp_path):
    """coin_candle 가 비어 있으면 1440개 누락 → EXCLUDE → --fail-on-exclude 시 exit 2."""
    db_path = tmp_path / "h2.db"
    env = {"DATABASE_URL": f"sqlite:///{db_path}"}
    r = _run_cli(
        ["--symbol", "BTC", "--exchange", "mock",
         "--timeframe", "1m", "--date", DAY.isoformat(),
         "--output", "json", "--fail-on-exclude"],
        env_extra=env,
    )
    assert r.returncode == 2
    body = json.loads(r.stdout)
    assert body["mode"] == "historical"
    assert body["days"][0]["grade"] == "EXCLUDE"


# ── 12. REST API ────────────────────────────────────────────────

@pytest.fixture
def app_with_db():
    eng = create_engine(
        "sqlite:///:memory:", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    Sf = sessionmaker(bind=eng, expire_on_commit=False, future=True)

    from app.main import app
    from app.api.deps import get_db

    def _override_db():
        s = Sf()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_db
    yield app, Sf
    app.dependency_overrides.pop(get_db, None)
    eng.dispose()


def test_api_data_quality_summary_empty_db_returns_exclude(app_with_db):
    app, _ = app_with_db
    client = TestClient(app)
    r = client.get(
        "/api/market/data-quality/summary",
        params={"symbol": "BTC", "exchange": "mock",
                "timeframe": "1m", "date": DAY.isoformat()},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["report"]["grade"] == "EXCLUDE"
    assert body["promotion"]["allowed"] is False


def test_api_data_quality_summary_bad_date_returns_400(app_with_db):
    app, _ = app_with_db
    client = TestClient(app)
    r = client.get(
        "/api/market/data-quality/summary",
        params={"symbol": "BTC", "exchange": "mock",
                "timeframe": "1m", "date": "not-a-date"},
    )
    assert r.status_code == 400


def test_api_data_quality_summary_bad_timeframe_returns_400(app_with_db):
    app, _ = app_with_db
    client = TestClient(app)
    r = client.get(
        "/api/market/data-quality/summary",
        params={"symbol": "BTC", "exchange": "mock",
                "timeframe": "30m", "date": DAY.isoformat()},
    )
    assert r.status_code == 400


def test_api_data_quality_summary_with_good_day(app_with_db):
    app, Sf = app_with_db
    with Sf() as s:
        for m in range(1440):
            s.add(CoinCandle(
                exchange="mock", symbol="BTC", interval="1m", ts=_ts(DAY, m),
                open=100, high=101, low=99, close=100, volume=1.0,
                source="test", meta={},
            ))
        s.commit()

    client = TestClient(app)
    r = client.get(
        "/api/market/data-quality/summary",
        params={"symbol": "BTC", "exchange": "mock",
                "timeframe": "1m", "date": DAY.isoformat()},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["report"]["grade"] == "GOOD"
    assert body["report"]["actual_count"] == 1440
    assert body["promotion"]["allowed"] is True
    # secret 노출 없음
    text = json.dumps(body)
    for needle in ("api_key", "API_SECRET", "ACCESS_TOKEN", "passphrase"):
        assert needle not in text


# ── 13. PromotionEvaluation as_dict ────────────────────────────

def test_promotion_evaluation_as_dict_shape():
    ev = PromotionEvaluation(allowed=True, reason="approved",
                             good_ratio=1.0, warning_ratio=0.0,
                             exclude_ratio=0.0)
    d = ev.as_dict()
    assert set(d.keys()) == {"allowed", "reason", "good_ratio",
                             "warning_ratio", "exclude_ratio"}


# ── 14. 정적 금지 문자열 ───────────────────────────────────────

_FORBIDDEN = (
    "place_order(",
    "cancel_order(",
    "get_balance(",
    "broker.place_order",
    "OrderExecutor",
    "route_order",
    "KIS_APP_KEY",
    "KIS_APP_SECRET",
    "API_SECRET",
    "ACCESS_TOKEN",
    "ENABLE_LIVE_TRADING = True",
    "ENABLE_AI_EXECUTION = True",
    "ENABLE_CRYPTO_FUTURES_LIVE = True",
)


def test_no_forbidden_strings_in_data_quality_production():
    files = (
        "backend/app/market/data_quality.py",
        "scripts/check_data_quality.py",
    )
    for rel in files:
        p = REPO_ROOT / rel
        assert p.exists(), f"missing: {p}"
        text = p.read_text(encoding="utf-8")
        for needle in _FORBIDDEN:
            assert needle not in text, \
                f"{rel} contains forbidden string: {needle!r}"
