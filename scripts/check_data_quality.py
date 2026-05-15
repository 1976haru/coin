"""scripts/check_data_quality.py — 체크리스트 #17 Data Quality CLI.

현재 watchlist 의 enabled 항목들을 1회 수집한 뒤 Data Quality 검사를 실행하고
결과를 stdout 으로 출력한다. CI 또는 운영 사전점검(#91 Pre-market Checklist)에서
호출할 수 있다.

사용:
    cd cointrade
    python scripts/check_data_quality.py
    python scripts/check_data_quality.py --list-name kimp_pairs
    python scripts/check_data_quality.py --json
    python scripts/check_data_quality.py --max-spread-pct 0.3 --min-volume 500000

종료 코드:
    0  모두 통과
    1  하나 이상 BLOCK
    2  watchlist 가 비어있음
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime, timezone

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
from app.db.session import get_session_factory, create_all_tables          # noqa: E402


def _build_collector() -> MarketDataCollector:
    """기본 Mock source 3개로 collector 생성. 실제 거래소 source는 #21·#22 이후."""
    return MarketDataCollector(sources={
        "upbit":   MockMarketDataSource("upbit"),
        "okx":     MockMarketDataSource("okx"),
        "binance": MockMarketDataSource("binance"),
    })


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Data quality checks against current watchlist")
    p.add_argument("--list-name", default=None,
                   help="특정 watchlist 그룹만 검사 (미지정 시 전체)")
    p.add_argument("--exchange", default=None,
                   help="특정 거래소만 검사")
    p.add_argument("--max-spread-pct", type=float, default=DEFAULT_MAX_SPREAD_PCT)
    p.add_argument("--min-volume", type=float, default=DEFAULT_MIN_VOLUME_24H_USDT)
    p.add_argument("--max-spike-pct", type=float, default=DEFAULT_MAX_PRICE_SPIKE_PCT)
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="JSON 형식으로 출력")
    args = p.parse_args(argv)

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
        if args.as_json:
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

    if args.as_json:
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


if __name__ == "__main__":
    sys.exit(main())
