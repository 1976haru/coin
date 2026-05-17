"""scripts/check_data_quality.py — 체크리스트 #17 Data Quality CLI.

두 가지 모드를 제공한다:

  1. **legacy live 모드** (기본): Watchlist 의 enabled 항목을 1회 수집하여
     ticker/orderbook quality (#17 의 quote sanity) 를 검사한다. 인자 미지정 시.
     - exit 0 : 모두 통과
     - exit 1 : 하나 이상 BLOCK
     - exit 2 : watchlist 비어 있음

  2. **historical candle 모드** (#17): `--symbol` 이 지정되면 활성화. 저장된
     coin_candle 데이터의 일별 품질을 검사하고 GOOD/WARNING/EXCLUDE 등급을 산출한다.
     - `--symbol BTC` --exchange mock --timeframe 1m --date YYYY-MM-DD
     - 또는 --from-date / --to-date 범위
     - `--output json` 또는 `--json`
     - `--fail-on-exclude` 옵션: EXCLUDE 가 있으면 exit code 2
     - 외부 거래소 호출 없음. 전체 시장 자동 스캔 없음 — --symbol 필수.

사용 예:
    python scripts/check_data_quality.py                                  # live
    python scripts/check_data_quality.py --symbol BTC --exchange mock \\
      --timeframe 1m --date 2026-05-17 --output json --fail-on-exclude
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import date as _date, datetime, timedelta, timezone

# backend/ 를 sys.path 에 추가해 app.* import 가능하게 함.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_BACKEND = os.path.join(_ROOT, "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.market.collector import MarketDataCollector, MockMarketDataSource  # noqa: E402
from app.market.quality import (                                           # noqa: E402
    assess_quote,
    DEFAULT_MAX_SPREAD_PCT,
    DEFAULT_MIN_VOLUME_24H_USDT,
    DEFAULT_MAX_PRICE_SPIKE_PCT,
)
from app.market.watchlist import WatchlistService                          # noqa: E402
from app.market.data_quality import (                                      # noqa: E402
    DataQualityConfig, DataQualityGrade,
    load_candles_for_day, run_day_check,
    BacktestPromotionGuard,
)
from app.db.session import get_session_factory, create_all_tables          # noqa: E402


# ── live 모드 (기존 동작 유지) ──────────────────────────────────

def _build_collector() -> MarketDataCollector:
    return MarketDataCollector(sources={
        "upbit":   MockMarketDataSource("upbit"),
        "okx":     MockMarketDataSource("okx"),
        "binance": MockMarketDataSource("binance"),
    })


def _run_live_mode(args, as_json: bool) -> int:
    create_all_tables()
    Sf = get_session_factory()
    with Sf() as s:
        svc = WatchlistService(s)
        entries = svc.list_entries(
            list_name=args.list_name,
            exchange=args.exchange,
            enabled_only=True,
        )

    if not entries:
        msg = "watchlist 에 enabled 항목이 없습니다. POST /api/watchlist 로 추가하세요."
        if as_json:
            print(json.dumps({"error": msg, "results": []}, ensure_ascii=False))
        else:
            print(msg, file=sys.stderr)
        return 2

    pairs = [(e["symbol"], e["exchange"]) for e in entries]
    collector = _build_collector()
    report = collector.collect(pairs)

    results: list[dict] = []
    blocked = 0
    for ce in report.entries:
        label = f"{ce.symbol}@{ce.exchange}"
        if ce.ticker is None:
            results.append({
                "label": label, "ok": False,
                "blocks": [ce.error or "ticker 없음"],
                "warnings": [], "freshness_ok": ce.freshness.ok,
            })
            blocked += 1
            continue

        qr = assess_quote(
            label, ce.ticker,
            max_spread_pct=args.max_spread_pct,
            min_volume=args.min_volume,
            max_spike_pct=args.max_spike_pct,
        )
        results.append({
            "label": qr.label,
            "ok": qr.ok and ce.freshness.ok,
            "freshness_ok": ce.freshness.ok,
            "freshness_reason": ce.freshness.reason,
            "blocks": [c.reason for c in qr.blocks],
            "warnings": [c.reason for c in qr.warnings],
        })
        if not qr.ok or not ce.freshness.ok:
            blocked += 1

    summary = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "blocked": blocked,
    }

    if as_json:
        print(json.dumps({"summary": summary, "results": results},
                         ensure_ascii=False, indent=2))
    else:
        print(f"=== Data Quality Report ({summary['ts']}) ===")
        print(f"total={summary['total']} blocked={summary['blocked']}")
        for r in results:
            mark = "FAIL" if not r["ok"] else "PASS"
            print(f"[{mark}] {r['label']}")
            if not r["freshness_ok"]:
                print(f"   FRESH: {r.get('freshness_reason', '')}")
            for b in r.get("blocks", []):
                print(f"   BLOCK: {b}")
            for w in r.get("warnings", []):
                print(f"   WARN:  {w}")

    return 1 if blocked else 0


# ── historical 모드 (#17) ──────────────────────────────────────

def _parse_date(s: str) -> _date:
    return _date.fromisoformat(s)


def _days_in_range(from_d: _date, to_d: _date) -> list[_date]:
    out: list[_date] = []
    d = from_d
    while d <= to_d:
        out.append(d)
        d += timedelta(days=1)
    return out


def _run_historical_mode(args, as_json: bool) -> int:
    # 대상 date(s)
    if args.from_date and args.to_date:
        from_d = _parse_date(args.from_date)
        to_d   = _parse_date(args.to_date)
        if from_d > to_d:
            print("from-date 가 to-date 보다 늦습니다.", file=sys.stderr)
            return 2
        days = _days_in_range(from_d, to_d)
    elif args.date:
        days = [_parse_date(args.date)]
    else:
        print(
            "historical 모드는 --date 또는 --from-date/--to-date 중 하나가 필요합니다.",
            file=sys.stderr,
        )
        return 2

    if not args.exchange or not args.timeframe:
        print(
            "historical 모드는 --exchange 와 --timeframe 이 필요합니다.",
            file=sys.stderr,
        )
        return 2

    cfg = DataQualityConfig()
    reports = []
    create_all_tables()
    Sf = get_session_factory()
    with Sf() as s:
        for d in days:
            candles = load_candles_for_day(
                s, symbol=args.symbol, exchange=args.exchange,
                timeframe=args.timeframe, day=d,
            )
            rep = run_day_check(
                candles,
                symbol=args.symbol, exchange=args.exchange,
                timeframe=args.timeframe, day=d, config=cfg,
            )
            reports.append(rep)

    guard = BacktestPromotionGuard()
    promo = guard.evaluate(reports)
    has_exclude = any(r.grade == DataQualityGrade.EXCLUDE for r in reports)

    payload = {
        "mode":     "historical",
        "symbol":   args.symbol,
        "exchange": args.exchange,
        "timeframe": args.timeframe,
        "days":     [r.as_dict() for r in reports],
        "promotion": promo.as_dict(),
    }

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"=== Data Quality (historical) {args.symbol}@{args.exchange} {args.timeframe} ===")
        for r in reports:
            print(f"  {r.date.isoformat()}  grade={r.grade.value:<7s}  "
                  f"missing={r.missing_count}/{r.expected_count} "
                  f"dup={r.duplicate_count}  invalid_ohlc={r.invalid_ohlc_count}  "
                  f"vol_anom={r.volume_anomaly_count}  outlier={r.price_outlier_count}  "
                  f"off={r.off_universe_count}  future={r.future_timestamp_count}")
            for reason in r.reasons:
                print(f"    - {reason}")
        print(f"promotion: allowed={promo.allowed} reason={promo.reason} "
              f"good={promo.good_ratio:.3f} warn={promo.warning_ratio:.3f} "
              f"excl={promo.exclude_ratio:.3f}")

    if args.fail_on_exclude and has_exclude:
        return 2
    return 0


# ── 진입점 ────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Data quality checks (live ticker quality + historical candle quality)")
    # legacy live 모드 — 유지
    p.add_argument("--list-name", default=None,
                   help="특정 watchlist 그룹만 검사 (live 모드)")
    p.add_argument("--max-spread-pct", type=float, default=DEFAULT_MAX_SPREAD_PCT)
    p.add_argument("--min-volume", type=float, default=DEFAULT_MIN_VOLUME_24H_USDT)
    p.add_argument("--max-spike-pct", type=float, default=DEFAULT_MAX_PRICE_SPIKE_PCT)
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="JSON 형식으로 출력 (--output json 과 동일)")

    # 양쪽 모드 공유
    p.add_argument("--exchange", default=None,
                   help="exchange filter (live) / target exchange (historical)")

    # historical 모드 (#17)
    p.add_argument("--symbol", default=None,
                   help="enable historical mode: symbol to check")
    p.add_argument("--timeframe", default=None,
                   help="historical 모드 timeframe (1m/5m/15m/1h/4h/1d)")
    p.add_argument("--date", default=None, help="단일 날짜 YYYY-MM-DD")
    p.add_argument("--from-date", default=None, dest="from_date",
                   help="시작 날짜 YYYY-MM-DD (--to-date 필요)")
    p.add_argument("--to-date", default=None, dest="to_date",
                   help="끝 날짜 YYYY-MM-DD (--from-date 필요)")
    p.add_argument("--output", choices=("text", "json"), default=None,
                   help="출력 형식 (--json 과 동등). 기본 text.")
    p.add_argument("--fail-on-exclude", action="store_true", dest="fail_on_exclude",
                   help="historical 모드에서 EXCLUDE 가 있으면 exit code 2")

    args = p.parse_args(argv)
    as_json = bool(args.as_json or args.output == "json")

    if args.symbol:
        return _run_historical_mode(args, as_json)
    return _run_live_mode(args, as_json)


if __name__ == "__main__":
    sys.exit(main())
