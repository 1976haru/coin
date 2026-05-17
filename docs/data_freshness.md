# Data Freshness — 체크리스트 #16

지연된 시세로 인한 진입 사고를 막는 **안전장치**.
구현은 `backend/app/market/freshness.py` (FreshnessTracker / FreshnessPolicy),
REST 는 `backend/app/api/market.py`, 주문 path 는 `backend/app/api/orders.py`.

---

## 1. 무엇인가 — 그리고 무엇이 *아닌가*

| Data Freshness 는… | … 가 아니다 |
|---|---|
| 마지막 수신 시각으로부터 **N초 이상 지연되면 stale** 판정 | 시세 이상치 제거 / 스파이크 필터가 아니다 (#17) |
| WebSocket / stream **재연결 중에는 신규 진입 차단** | 실제 WebSocket 연결 구현이 아니다 (후속 단계) |
| BUY/OPEN/ENTER 계열만 차단 | SELL/EXIT/CLOSE 등 위험 축소는 막지 않는다 |
| 메모리 tracker (in-process) | DB 영속화 대상이 아니다 (본 단계) |

> 본 단계 완료는 **실거래 허가가 아니다.** CLAUDE.md §2.5 의 자동 진입 차단 조건 중
> "stale data" 와 "WebSocket reconnecting" 두 케이스에 대한 안전장치만 더한 것.

---

## 2. 데이터 모델

```python
@dataclass(frozen=True)
class FreshnessKey:
    symbol: str
    exchange: str
    data_type: str        # ticker / ohlcv / orderbook / funding / fx
    timeframe: str | None = None     # OHLCV 만 의미 있음

@dataclass
class FreshnessRecord:
    key: FreshnessKey
    last_seen_at: datetime | None = None
    update_count: int = 0

@dataclass(frozen=True)
class ReconnectScope:
    """필드가 None 이면 wildcard."""
    symbol:    str | None = None
    exchange:  str | None = None
    data_type: str | None = None
```

---

## 3. Policy — data_type 별 max_age

| data_type | 기본 max_age (초) | env |
|---|---:|---|
| ticker    | 30   | `MARKET_FRESHNESS_TICKER_MAX_AGE_SECONDS` |
| orderbook | 10   | `MARKET_FRESHNESS_ORDERBOOK_MAX_AGE_SECONDS` |
| ohlcv     | 300  | `MARKET_FRESHNESS_OHLCV_MAX_AGE_SECONDS` |
| funding   | 3600 | `MARKET_FRESHNESS_FUNDING_MAX_AGE_SECONDS` |
| fx        | 300  | `MARKET_FRESHNESS_FX_MAX_AGE_SECONDS` |

추가 토글:
- `MARKET_BLOCK_BUY_WHEN_STALE` (기본 true)
- `MARKET_BLOCK_BUY_WHEN_RECONNECTING` (기본 true)

모두 `Settings` 의 `field(default_factory=...)` 로 평가되어
`monkeypatch.setenv` + `reset_settings_cache()` 가 즉시 반영됨.

---

## 4. `is_stale` 정책 (pure function)

`is_stale(last_seen_at, max_age_seconds, now=None) -> bool`

- `last_seen_at is None` → **True** (missing 은 stale).
- `max_age_seconds <= 0` → **True** (안전 우선).
- `last_seen_at > now` (clock skew, 미래 ts) → **True**.
- 그 외: `(now - last_seen_at) > max_age_seconds` 일 때 True.
- naive datetime 입력은 UTC 로 간주하여 처리 (테스트로 회귀).

`compute_lag_seconds(last_seen_at, now=None) -> float | None`
- `last_seen_at is None` → `None`
- 미래 ts → `0.0`
- 그 외 → `now - last_seen_at` 초

---

## 5. FreshnessTracker — 기능 표

| 메서드 | 동작 |
|---|---|
| `mark_seen(symbol, exchange, data_type, timeframe=None, seen_at=None)` | 수집 성공 시 호출. 더 최신 ts 만 보존. |
| `mark_reconnecting(symbol=None, exchange=None, data_type=None, reason="")` | 범위에 reconnecting 표시. 빈 범위(모두 None) = 글로벌. |
| `clear_reconnecting(symbol=None, exchange=None, data_type=None)` | 해당 scope 제거. |
| `get_record(key)` | 단일 레코드 조회. |
| `is_reconnecting(symbol, exchange, data_type)` | wildcard 매치 결과. |
| `evaluate(symbol, exchange, data_type, ...)` | `FreshnessStatus` — reconnecting 우선, 그 다음 stale. |
| `can_open_new_position(symbol, exchange, required_data_types=("ticker",))` | 정책 토글 기반 entry 가능 여부 + reasons. |
| `can_generate_signal(symbol, exchange, side, ...)` | entry vs exit side 분류 후 결정. SELL/EXIT/CLOSE 는 항상 허용. |
| `evaluate_for_order(symbol, exchange, side, ...)` | `(block, statuses, reasons)` — OrderGateway 의 `freshness_statuses` 인자와 호환. |
| `get_summary(now=None)` | API 응답용 dict (counts/records/reconnecting/policy/blocks_new_entries). |
| `reset()` | 테스트 전용. |

스레드-안전 (`threading.RLock`). 메모리 기반 — DB 추가 없음.

---

## 6. side 분류

```python
ENTRY_SIDES = {"BUY", "ENTER", "OPEN", "OPEN_LONG", "OPEN_SHORT", "OPEN_REVERSE_KIMP"}
EXIT_SIDES  = {"SELL", "EXIT", "CLOSE", "CLOSE_LONG", "CLOSE_SHORT"}
```

- **entry 사이드** : stale 또는 reconnecting 이면 차단.
- **exit 사이드**  : freshness 로 막지 않는다 (위험 축소).
- **알 수 없는 side** : 보수적으로 entry 와 동일 처리.

회귀: `test_can_generate_signal_exit_always_allowed`,
`test_order_preview_sell_not_blocked_by_reconnecting`.

---

## 7. Collector(#15) 연동

`MarketDataCollector(sources, freshness_tracker=tracker)` 에 tracker 주입.

- 수집 **성공** 시 자동으로 `tracker.mark_seen(...)` 호출.
- 수집 **실패** 시 mark_seen 호출 안 함 → 해당 키는 fresh 로 갱신되지 않음.

| 수집 타입 | mark_seen 시점 | seen_at |
|---|---|---|
| ticker | `fetch_ticker` 성공 | `ticker.ts` |
| ohlcv  | `fetch_ohlcv` 성공 | (현재 시각 — limit 봉 단위로 시퀀스 기록) |
| orderbook | `fetch_orderbook` 성공 | `orderbook.ts` |
| funding | `fetch_funding` 이 non-None 반환 | `funding.ts` |
| fx (별도 source) | `fetch_fx` 이 non-None 반환 | `fx.ts` (symbol=pair, exchange="fx") |

회귀: `test_collector_marks_seen_on_ticker_success`,
`test_collector_does_not_mark_seen_when_ticker_fails`,
`test_collect_all_marks_seen_for_all_included_types`,
`test_collect_all_fx_recorded_under_fx_pseudoexchange`.

---

## 8. 주문 단일 경로(#2.4) 와의 연결

`POST /api/order/preview` 는 다음을 한다:

1. `tracker.evaluate_for_order(symbol, exchange, side)` 호출.
2. 반환된 `statuses` (ticker `FreshnessStatus` 1행) 를 `OrderGateway.submit(..., freshness_statuses=...)` 로 전달.
3. OrderGateway 의 기존 BUY-side freshness 가드(`should_block_new_buy`) 가 reasons 를
   RiskManager 로 넘기고, RiskManager 가 거부 → `REJECTED` 응답.

**기존 단일 주문 경로(#2.4) 를 깨지 않는다.** Risk → OrderGuard → PermissionGate →
ApprovalQueue → OrderGateway → Executor → AuditLog 모든 단계 그대로.

SELL/EXIT/CLOSE 의 경우 `evaluate_for_order` 가 빈 reasons + 채워진 statuses 를 반환 →
gateway 는 freshness 사유로 막지 않는다. (단, 다른 단계는 그대로 적용.)

---

## 9. REST API

```
GET    /api/freshness                       # public — 종합 status + summary
POST   /api/freshness/reconnecting          # admin — reconnecting scope 등록
DELETE /api/freshness/reconnecting          # admin — scope 해제
```

### GET 응답 예시
```json
{
  "ok": false,
  "reason": "재연결 중 — 신규 매수 금지",
  "summary": {
    "now": "2026-05-17T...",
    "records": [
      {"symbol": "BTC", "exchange": "upbit", "data_type": "ticker",
       "timeframe": null, "last_seen_at": "...", "age_seconds": 3.2,
       "max_age_seconds": 30.0, "stale": false}
    ],
    "counts": {"fresh": 3, "stale": 1, "missing": 0, "total": 4,
               "reconnecting_scopes": 1},
    "reconnecting": [{"symbol": null, "exchange": "upbit",
                      "data_type": null, "reason": "ws drop"}],
    "policy": {"ticker_max_age_sec": 30.0, "block_buy_when_stale": true, ...},
    "blocks_new_entries": true
  },
  "feed": { "ok": true, "age_seconds": 0.0, "reason": "mock_feed: 신선 0.00s" }
}
```

`ok` / `reason` 최상위 키는 legacy 테스트 호환을 위해 유지된다.

### POST `/api/freshness/reconnecting`
```json
{ "symbol": null, "exchange": "upbit", "data_type": null, "reason": "ws drop" }
```
- 필드 None = wildcard. 모두 None 이면 글로벌 reconnecting.
- 결과: `{"marked": true, "scope": {...}}`.

### DELETE `/api/freshness/reconnecting?exchange=upbit`
- `{"cleared": true|false}`.

---

## 10. Frontend

`/market` 페이지(`MarketPage.tsx`) 가 read-only 로 표시:
- Collector status (mode/sources/last_*).
- Freshness panel — counts/blocks_new_entries/now.
- Reconnecting 목록.
- Stale records 목록.
- Ticker 캐시 표.

수동 reconnecting 토글 버튼은 만들지 않았다 — admin 호출은 CLI/관리자 도구에서 한다.
secret/token 저장 없음.

---

## 11. 안전 원칙 — 변경되지 않은 것

본 작업은 다음을 변경/구현하지 않는다:

- 실제 거래소 LIVE 주문 / 잔고 / 체결 / private endpoint 호출
- `place_order`, `cancel_order`, `get_balance` 등 코드 추가
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_CRYPTO_FUTURES_LIVE` 기본값
- frontend secret/token 저장
- 17번 Data Quality (스파이크/이상치 필터)
- 18번 Exchange Notices
- 실제 WebSocket 연결 구현 (재연결 상태 전이만 제공)
- 전체 시장 자동 스캔

**16번 완료 = stale/reconnecting 안전장치 완료. LIVE 실거래 허가가 아니다.**

회귀 방지:
- `test_no_forbidden_strings_in_freshness_production` — `freshness.py`, `orders.py` 에 금지 문자열 부재
- `test_collector_does_not_import_brokers_or_execution` — collector 가 broker/execution 를 import 하지 않음 (#15 에서 유지)

---

## 12. 향후 확장 메모 (별도 체크리스트)

| 영역 | 메모 |
|---|---|
| 실제 WebSocket 연결 + 자동 reconnect signal | adapter (#21~#23) 와 연동. tracker 의 `mark_reconnecting/clear` 호출. |
| 다중 source 합의 (`A` 와 `B` 모두 stale 이어야 차단) | 별도 정책 PR |
| stale 시 자동 청산 정책 | 본 단계는 차단만, 자동 청산은 별도 |
| DB 영속화 | 메모리 tracker 만 — 운영 metric 수집/대시보드용으로는 별도 |
| `coin_risk_event` 통합 | stale 발생 시 `coin_risk_event` 로 자동 기록 — 별도 PR |
| #17 Data Quality 와의 통합 | Data Quality 의 `liquidity_ok`/`fx_anomaly_ok` 와 freshness 통합 결정 채널 |
