# Market Data Collector — 체크리스트 #15

본 문서는 시장 데이터 수집 계층의 **역할 / 한계 / 데이터 흐름 / 안전 원칙** 을 정리한다.
구현은 `backend/app/market/collector.py`, 영속화는 `backend/app/market/market_persister.py`,
REST 는 `backend/app/api/market.py`.

---

## 1. 무엇인가 — 그리고 무엇이 *아닌가*

| Market Data Collector 는… | … 가 아니다 |
|---|---|
| **read-only** 시장 데이터(OHLCV/ticker/orderbook/funding/FX) 를 가져오는 계층 | 거래소에 주문을 넣는 계층이 아니다 |
| Watchlist (#14) universe 안에서만 수집한다 | 전체 시장 자동 스캔이 아니다 |
| Mock source 로 결정론적 테스트를 한다 | 본 단계에서 Upbit/OKX/Binance 실 source 를 만드는 작업이 아니다 (#21~#23) |
| ticker 캐시 + 13번 코인 테이블에 영속화한다 | private/계좌/체결/주문 endpoint 를 호출하지 않는다 |

> **여기서 모은 데이터가 곧 자동 주문으로 이어지지 않는다.**
> 데이터는 전략/리스크/백테스트의 *입력* 일 뿐이고,
> 주문 흐름은 CLAUDE.md §2.4 단일 경로의 모든 게이트를 따로 통과해야 한다.

---

## 2. 데이터 모델

`app.schemas.market` 에 정의된 frozen dataclass 5종:

| 모델 | 핵심 필드 |
|---|---|
| `Ticker` | symbol, price, bid, ask, spread_pct, volume_24h, ts |
| `OHLCV` | symbol, timeframe, ts, open, high, low, close, volume |
| `OrderBook` | symbol, bids[], asks[], ts (+ best_bid/ask/spread/bid_depth 메서드) |
| `FundingRate` | symbol, exchange, funding_rate, ts, next_funding_time? |
| `FxRate` | pair (예: `USDT-KRW`), rate, ts, source |

spot-only 거래소(예: upbit) 에서 funding 은 **None** (실패가 아닌 빈 결과).
FX 는 dedicated source 로 분리 — exchange source 의 capability 가 아님.

---

## 3. MarketDataSource Protocol

```python
@runtime_checkable
class MarketDataSource(Protocol):
    name: str
    def fetch_ticker(self, symbol: str) -> Ticker: ...
    def fetch_orderbook(self, symbol: str, depth: int = 5) -> OrderBook: ...
```

**Protocol 의 최소 표면** — 기존 Upbit/OKX/Binance read-only adapter 와의
호환을 유지하기 위함. OHLCV / funding / FX 는 **optional capability** 로,
collector 가 `hasattr` 로 발견해 사용한다 (`fetch_ohlcv` / `fetch_funding` /
`fetch_fx`).

**금지 메서드** — Protocol 에 추가되지 않는다:
`place_order`, `cancel_order`, `get_balance`, `get_account`,
`withdraw`, `transfer`, `create_order` 등. 회귀 테스트
`test_market_data_source_protocol_has_no_order_methods` 가 강제.

---

## 4. MockMarketDataSource

테스트/CI/오프라인 개발용 결정론적 source.

```python
mock_spot = MockMarketDataSource("upbit")                          # spot
mock_perp = MockMarketDataSource("okx_perp", supports_funding=True) # perpetual
fx_mock   = MockMarketDataSource("fx_mock",  supports_fx=True)      # FX dedicated
```

- 가격은 `md5(symbol)` 시드 → 같은 symbol 은 매번 같은 price.
- OHLCV 는 `(symbol, timeframe, bar_index)` 시드 → 결정론적 시퀀스.
- funding 은 `supports_funding=False` (기본) 일 때 None.
- FX 는 `supports_fx=False` (기본) 일 때 None.

지원 timeframe: `1m / 5m / 15m / 1h / 4h / 1d`.

---

## 5. Collector — Watchlist 기반 수집

### legacy API (변경 없음 — 회귀 호환)

| 메서드 | 동작 |
|---|---|
| `collect(pairs)` | ticker 1회 수집 → `CollectorReport(ok/stale/error)` |
| `collect_from_provider(fn)` | 위와 동일하지만 pairs 를 lazy 공급 |
| `get_ticker` / `cached_pairs` / `cache_size` / `clear_cache` | 캐시 헬퍼 |

### 신규 `collect_all(...)` (#15)

```python
report = collector.collect_all(
    pairs,                                # [(symbol, exchange), ...]
    includes={"ticker", "ohlcv", "orderbook", "funding"},
    timeframe="1m",
    ohlcv_limit=100,
    orderbook_depth=5,
    fx_pairs=["USDT-KRW"],                # FX 는 fx_source 가 별도로 필요
    max_symbols=100,                       # MARKET_COLLECTOR_MAX_SYMBOLS
    list_name="default",
)
```

**안전 규칙:**

1. **빈 pairs 입력 → `EmptyWatchlistError`** (전체 시장 fallback 금지).
2. **dedup** — 동일 `(symbol, exchange)` 는 1회만 처리.
3. **max_symbols** — 입력이 cap 을 넘으면 truncate (Watchlist cap 과 *별개의* 호출-단위 한도).
4. **부분 실패 격리** — 한 symbol/데이터타입 실패가 전체 보고를 깨지 않는다.
   실패는 `entry.failures: tuple[(type, reason), ...]` 로 기록.
5. **unknown include 키** → `ValueError` (예: `"balance"`).
6. **spot funding** → None (실패 아님).

### last_status()

`/api/market/collector/status` 의 원천. 마지막 수집의
`last_collected_at / last_symbol_count / last_success_count / last_failure_count /
last_includes / last_list_name`, 그리고 `sources / fx_source /
freshness_threshold_sec / cache_size / mode("read-only")` 반환.

---

## 6. DB 영속화 (`market_persister.persist_report`)

13번에서 만든 코인 스키마를 그대로 활용한다 — **신규 테이블 추가 없음**.

| 데이터 | 테이블 | 중복 정책 |
|---|---|---|
| OHLCV | `coin_candle` | `UNIQUE(exchange, symbol, interval, ts)` → skip |
| Ticker | `coin_tick` | 매번 append (timeseries) |
| Orderbook | `coin_orderbook_snapshot` | 매번 append |
| Funding | (저장 안 함) | 본 단계 범위 외 — 별도 PR |
| FX | (저장 안 함) | 본 단계 범위 외 — 별도 PR |

**부분 실패 격리**: 한 row 의 INSERT 실패는 그 row 만 skip. 다른 entry / 다른 데이터 타입은 계속 진행.

---

## 7. REST API

```
GET  /api/market/tickers                        # public — 캐시된 ticker
       ?list_name=...&exchange=...&enabled_only=true
POST /api/market/collect                        # admin — 1회 수집 트리거
GET  /api/market/collector/status               # public — 수집기 상태
GET  /api/freshness                             # public — feed freshness
```

`POST /api/market/collect` body:
```json
{
  "list_name": "default",
  "exchange":  "upbit",
  "include":   ["ticker", "ohlcv", "orderbook", "funding", "fx"],
  "timeframe": "1m",
  "limit":     100,
  "orderbook_depth": 5,
  "fx_pairs":  ["USDT-KRW"],
  "persist":   true
}
```

- body 미동봉 시 legacy 동작 (ticker only).
- `include` 가 `{"ticker"}` 이고 `persist=false` 면 legacy `collect()` 경로 (회귀 호환).
- 그 외 → `collect_all` 경로.

오류 매핑:
- 알 수 없는 include 키 → 400
- Watchlist 비어 있음 → 404
- admin token 없음 → 401

`GET /api/market/collector/status` 응답에 secret 키는 노출되지 않는다.

---

## 8. Watchlist (#14) 기반 universe 제한

본 작업은 #14 Watchlist 와 **반드시** 연동된다.

- `POST /api/market/collect` 는 `WatchlistService.list_entries(enabled_only=True, list_name=?, exchange=?)`
  로 수집 대상을 가져온다.
- Watchlist 가 비어 있으면 → **404** ("no enabled watchlist entries").
  전체 시장 fallback 으로 절대 빠지지 않는다.
- 호출-단위 추가 cap `MARKET_COLLECTOR_MAX_SYMBOLS` (기본 100) 가 Watchlist cap 위에 한 번 더 걸린다.

---

## 9. 환경변수

| 키 | 기본 | 의미 |
|---|---:|---|
| `MARKET_COLLECTOR_MAX_SYMBOLS` | 100 | `collect_all` 1회의 최대 symbol 수 |
| `FRESHNESS_THRESHOLD_SEC` | 5.0 | ticker.ts 가 이보다 오래되면 freshness.ok=False |

`field(default_factory=...)` 로 모든 호출에서 fresh 평가 — `monkeypatch.setenv` 후
`reset_settings_cache()` 만으로 즉시 반영 (회귀 테스트에 사용).

---

## 10. Freshness 연결 — 최소만

16번 Data Freshness 를 **본격 구현하지 않는다.** 본 단계에서는:

- `collect()` 와 `collect_all()` 모두 ticker 의 `ts` 로 `FreshnessStatus` 를 계산해 entry 에 포함.
- `/api/market/collector/status` 에 `freshness_threshold_sec` 만 노출.
- BUY 차단/stale 정책 강화 등 고급 정책은 16번에서 별도로 다룬다.

---

## 11. Frontend (#7 호환)

`frontend/src/pages/MarketPage.tsx` 가 `/market` 경로로 read-only 표시:
- 수집기 상태 카드 (mode, sources, last_*, cache_size 등)
- 캐시된 ticker 표

수동 collect 버튼은 만들지 않는다 — 본 페이지는 조회 전용. (admin token UX 는 다른 페이지에 이미 존재하지만, 본 페이지에서는 의도적으로 조회만 노출한다.)

`#7` 사양의 7-item 사이드바 메뉴는 **변경하지 않았다**. `/market` 라우트만 추가 (e.g., `/watchlist` 와 같은 처리).

---

## 12. 안전 원칙 — 변경되지 않은 것

본 작업은 다음을 변경/구현하지 않는다:

- 실제 거래소 LIVE 주문 / 잔고 / 체결 / private endpoint 호출
- `place_order`, `cancel_order`, `get_balance`, broker.* 코드 추가
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_CRYPTO_FUTURES_LIVE` 기본값
- frontend 에 API key / secret / token 저장
- 16번 Data Freshness 고급 정책
- 17번 Data Quality 작업
- 전체 시장 자동 스캔 기능

**15번 완료 = read-only 수집 계층 완료. LIVE 실거래 허가가 아니다.**

회귀 방지:
- `test_market_data_source_protocol_has_no_order_methods` — Protocol 에 주문 메서드 부재
- `test_no_forbidden_strings_in_market_production_files` — 신규 production 파일에 금지 문자열 부재
- `test_collector_does_not_import_brokers_or_execution` — collector 가 broker/execution/ccxt/pyupbit 를 import 하지 않음

---

## 13. 향후 확장 메모 (별도 체크리스트)

| 영역 | 메모 |
|---|---|
| Upbit / OKX / Binance public source | #21~#23 — read-only adapter 들과 `MarketDataSource` Protocol 정합성 통합 |
| Funding / FX 영속화 | 본 단계는 메모리/응답 한정. 표 또는 캐시 추가 필요 |
| Background collector loop | 본 단계는 동기 호출만 제공. 주기적 collect job 은 별도 PR |
| Freshness 고급 정책 | #16 — stale 시 BUY 차단/WATCH_ONLY 등 |
| Data Quality | #17 — spread/depth/volume/spike/fx_anomaly 평가 |
| Backfill | OHLCV 의 과거 봉 일괄 적재 (rate-limit aware) |
