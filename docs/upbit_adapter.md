# Upbit Adapter (체크리스트 #21)

> Agent Trader Crypto OS v1 — 업비트 read-only adapter + 주문/잔고 gated stub

## 1. 목적과 범위

UpbitAdapter 는 국내 KRW 가격·호가 조회를 위한 **read-only adapter** 다.
시세/호가/캔들/체결 조회와 주문 API 구조를 명확히 분리한다.

본 단계(#21)는 다음만 한다.

- 업비트 **public quotation** 5개 endpoint 의 read-only 인터페이스 정의
- transport injection 으로 production transport 와 test FakeTransport 분리
- Remaining-Req 헤더 파싱 + 안전한 throttle 결정
- private/account 영역의 *gated stub* (credentials + transport 양쪽 필요)
- private/order 영역의 *disabled stub* (모든 호출 즉시 disabled)
- 김프 계산을 위한 KRW price 조회 구조 준비

본 단계는 다음을 **하지 않는다**.

- 실제 업비트 주문 API 네트워크 호출 ❌
- 실제 cancel order 네트워크 호출 ❌
- JWT signing / query_hash / HMAC signing 구현 ❌
- 실제 계좌 잔고 조회 (production transport) ❌
- 출금 / 이체 API ❌ (영구)
- 김프 기반 자동 주문 / BUY-SELL 신호 ❌
- 22번 OKX Adapter 작업 ❌

`fetch_price` 메서드 명은 #20 spec alias 로 base 에 존재하지만, 실제 LIVE 주문 송신은
별도 LIVE adapter + OrderGateway 승격 절차 후에만 가능 (CLAUDE.md §2.6).

## 2. 모듈 구조

| 파일 | 역할 |
|---|---|
| `app/brokers/upbit_adapter.py` | `UpbitAdapter` (READ_ONLY) + 심볼 헬퍼 |
| `app/brokers/upbit_public.py` | `UpbitPublicClient` (public quotation, transport-주입) |
| `app/brokers/upbit_rate_limit.py` | `parse_remaining_req` + `should_throttle` + `RateLimitState` |
| `app/brokers/upbit_account.py` | `UpbitAccountClient` (gated, credentials+transport 필요) |
| `app/brokers/upbit_order.py` | `UpbitOrderClient` (disabled stub) |

## 3. 심볼 정규화

### 3.1 `normalize_upbit_market(symbol) -> str`

내부 심볼을 업비트 형식 `QUOTE-BASE` 로 변환.

| 입력 | 결과 |
|---|---|
| `"BTC"` | `"KRW-BTC"` |
| `"btc"` | `"KRW-BTC"` |
| `"KRW-BTC"` | `"KRW-BTC"` |
| `"BTC-KRW"` | `"KRW-BTC"` |
| `"BTC/KRW"` | `"KRW-BTC"` |
| `"USDT-BTC"` | `"USDT-BTC"` |
| `"BTC-USDT"` | `"USDT-BTC"` |
| `"BTC-XRP"` | `"BTC-XRP"` (업비트 BTC 마켓) |
| `""` / `None` / `"   "` | `ValueError` |

알려진 quote 우선순위: **KRW → USDT → BTC** (KRW 가 양쪽 어느 위치에 있든 quote 로
간주, 나머지는 dash 위치로 판단).

### 3.2 `to_internal_symbol(upbit_market) -> str`

업비트 `QUOTE-BASE` → 프로젝트 내부 `BASE-QUOTE`.

| 입력 | 결과 |
|---|---|
| `"KRW-BTC"` | `"BTC-KRW"` |
| `"USDT-BTC"` | `"BTC-USDT"` |
| `"BTC-XRP"` | `"XRP-BTC"` |

### 3.3 `is_krw_market(market) -> bool`

`"KRW-..."` 로 시작하면 True (대소문자 무시).

## 4. UpbitPublicClient

### 4.1 메서드

| 메서드 | endpoint | 반환 |
|---|---|---|
| `fetch_markets()` | `GET /v1/market/all` | 마켓 카탈로그 |
| `fetch_ticker(markets)` | `GET /v1/ticker` | 다중 마켓 ticker |
| `fetch_orderbook(markets)` | `GET /v1/orderbook` | 다중 마켓 호가 |
| `fetch_candles_minutes(market, unit, count)` | `GET /v1/candles/minutes/{unit}` | 분봉 (unit ∈ {1,3,5,10,15,30,60,240}, count ≤ 200) |
| `fetch_trades_ticks(market, count)` | `GET /v1/trades/ticks` | 체결 틱 (count ≤ 500) |

### 4.2 Transport injection

본 client 는 silent 네트워크 호출을 **하지 않는다**. transport 가 없으면
`RuntimeError`. transport 시그니처:

```python
transport(method: "GET", path: str, params: dict, headers: dict) -> TransportResponse
```

`TransportResponse(status_code, body, headers)`.

production transport 는 본 단계에서 추가하지 않으며, 후속 PR 에서 httpx/requests
기반으로 별도 추가한다. **path 화이트리스트** (`_assert_public_path`) 가 모든 호출에
적용되어 private/account/order path 가 우회되지 않는다.

### 4.3 Response parsing

각 endpoint 마다 `_parse_markets` / `_parse_ticker` / `_parse_orderbook` /
`_parse_candles_minutes` / `_parse_trades_ticks` 함수로 분리 — 단위 테스트 용이.

### 4.4 Rate limit

`Remaining-Req` 응답 헤더가 있으면 `rate_limit.update(header)` 가 자동 호출.
`RateLimitState.maybe_throttle(sleep_seconds=...)` 로 caller 가 throttle 결정.
`sleep_fn` 은 주입 가능 — 테스트는 가짜 sleep 으로 빠르게 동작.

## 5. UpbitAdapter (ExchangeAdapter)

### 5.1 capability

```python
AdapterCapability(
    name="upbit", mode="READ_ONLY",
    can_fetch_ticker=True, can_fetch_orderbook=True,
    can_fetch_balance=False, can_place_order=False, can_cancel_order=False,
    supports_futures=False, requires_secret=False,
)
```

### 5.2 동작

- `fetch_ticker(symbol)` → Ticker
- `fetch_orderbook(symbol, depth=5)` → OrderBook
- `fetch_balance()` / `get_balance()` → `ExchangeAdapterDisabledError`
- `place_order(...)` → `ExchangeAdapterDisabledError`
- `cancel_order(...)` → `OrderResult(status="REJECTED", reason="upbit: cancel disabled")`

### 5.3 두 가지 경로

```python
# A) legacy — pyupbit 자동 import (또는 fake client 주입)
a = UpbitAdapter()                     # production
a = UpbitAdapter(client=FakeUpbit())   # tests

# B) 신규 — UpbitPublicClient + transport 주입
pc = UpbitPublicClient(transport=my_transport)
a = UpbitAdapter(public_client=pc)
```

생성자에 `api_key` / `api_secret` 이 들어오면 `ValueError` (READ_ONLY adapter 는 키를
받지 않는다).

## 6. UpbitAccountClient (gated)

### 6.1 동작

```python
c = UpbitAccountClient()
c.fetch_balances()                     # → UpbitAccountPermissionError (credentials 없음)

c = UpbitAccountClient(api_key="x", api_secret="y")
c.fetch_balances()                     # → UpbitAccountPermissionError (transport 없음)

c = UpbitAccountClient(api_key="x", api_secret="y", transport=fake)
c.fetch_balances()                     # → [{"currency": "BTC", "balance": "...", ...}]
```

### 6.2 안전 정책

- secret 값을 attribute 로 *저장하지 않는다* — repr 에 노출 부재.
- `/v1/accounts` 만 화이트리스트. 다른 path 는 `UpbitAccountPermissionError`.
- 출금/이체 메서드 부재 (영구) — `assert_no_withdrawal_methods` 통과.
- production transport 는 본 단계에서 추가하지 않음. LIVE 승격 절차 (CLAUDE.md
  §2.6) 후 별도 PR.

## 7. UpbitOrderClient (disabled stub)

모든 메서드 호출 즉시 `ExchangeAdapterDisabledError`.

- `place_order(...)` → disabled
- `cancel_order(...)` → disabled
- `get_order(...)` → disabled
- `__init__` 이 받은 credentials 는 즉시 폐기 (보관 안 함).
- JWT / HMAC / signing 코드 본 모듈에 없음.
- 출금 메서드 없음.
- `capability.to_dict()` 가 모든 가능성을 False 로 표기 + "gated on OrderGateway +
  LIVE permission" 명시.

실제 구현은 별도 LIVE 승격 절차 통과 후, 단일 주문 경로의 끝단(OrderGateway → Executor)
에서만 호출되도록 추가한다. Strategy/Agent 가 본 client 를 직접 호출하지 않는다.

## 8. 단일 주문 경로 보존 (CLAUDE.md §2.4)

```text
Strategy → Agent → RiskManager → OrderGuard → PermissionGate
        → ApprovalQueue → OrderGateway → Executor/Adapter
```

### 8.1 강제 메커니즘

| 검증 | 회귀 테스트 |
|---|---|
| Strategy 가 `upbit_*` 모듈 import 안 함 | `test_strategies_do_not_import_upbit_adapter` |
| Agent 가 `upbit_*` 모듈 import 안 함 (compliance.py 예외) | `test_agents_do_not_import_upbit_adapter` |
| Strategy 가 Upbit*Client 인스턴스화 안 함 | `test_strategies_no_upbit_adapter_call` |
| Agent 가 Upbit*Client 인스턴스화 안 함 | `test_agents_no_upbit_adapter_call` |

## 9. Rate limit / Remaining-Req

```python
from app.brokers import parse_remaining_req, should_throttle, RateLimitState

parsed = parse_remaining_req("group=market; min=599; sec=9")
# {"group": "market", "min": 599, "sec": 9}

should_throttle(parsed)                        # False
should_throttle({"group": "x", "min": 100, "sec": 0})  # True
should_throttle({"group": "x", "min": 0, "sec": 50})   # True (분 잔여 0)

state = RateLimitState(sleep_fn=time.sleep)
state.update(response_headers["Remaining-Req"])
state.maybe_throttle(sleep_seconds=0.2)        # 필요 시 sleep
```

테스트는 `sleep_fn=lambda s: recorded.append(s)` 같은 가짜 함수를 주입해 빠르게
동작 검증.

## 10. MarketDataCollector 연결

기존 #15 MarketDataCollector 는 `MarketDataSource` Protocol 만 만족하면 source 로
받는다. `UpbitAdapter` 는 본 Protocol 을 만족하므로 다음과 같이 주입 가능.

```python
from app.brokers import UpbitAdapter
from app.market.collector import MarketDataCollector

a = UpbitAdapter(client=FakeUpbit())            # 또는 public_client=...
c = MarketDataCollector(sources={"upbit": a})
report = c.collect([("BTC", "upbit"), ("ETH", "upbit")])
```

기본 source 는 mock 으로 유지하며 Upbit source 는 명시적으로 선택될 때만 사용.
collector 에서 사용되더라도 주문 메서드는 호출되지 않는다 (collector 는 시세만 읽음).

## 11. 김프 계산 준비

본 단계에서 김프 계산을 완성하지 않는다. 다만 다음 구조로 KRW price 를 얻을 수 있다.

```python
a = UpbitAdapter(client=FakeUpbit())
tk_krw = a.fetch_ticker("BTC-KRW")
# 또는
tk_krw = a.fetch_ticker("KRW-BTC")
print(tk_krw.price)   # 50_000_000.0
```

김프 계산은 후속 Kimp/FX 단계에서 다음을 조합:
- 업비트 KRW price (본 adapter)
- 해외 거래소 USDT price (OKX/Binance adapter, #22/#23)
- FX rate (별도 source)

본 단계에서 김프 기반 BUY/SELL 신호를 만들지 않는다.

## 12. 회귀 테스트

`backend/tests/test_upbit_adapter.py` — **73 케이스**. 주요 분류:

1. **심볼 정규화** — module-level helper 9개 (KRW/USDT/BTC 마켓, empty/None 거부, internal swap, is_krw_market)
2. **Capability + API key 거부** (기존)
3. **fetch_ticker / fetch_orderbook** — pyupbit fake + UpbitPublicClient transport
4. **Rate limit** — parse_remaining_req 정상/공백/깨진 헤더, should_throttle (sec/min/empty), RateLimitState sleep injection
5. **UpbitPublicClient** — markets/ticker/orderbook 응답 파싱, non-public path 차단, 4xx → UpbitPublicAPIError, transport 미주입 → RuntimeError, market arg validation, candles unit validation, rate_limit state 자동 갱신
6. **UpbitAdapter via UpbitPublicClient** — 정규화된 market 전달
7. **UpbitAccountClient** — credentials 없음 / transport 없음 / fake transport 정상, repr 에 secret 노출 부재, 출금 메서드 부재
8. **UpbitOrderClient** — 모든 동작 disabled, credentials 인자 폐기, capability dict, 출금 메서드 부재
9. **단일 주문 경로** — Strategy/Agent 가 upbit 모듈 import·인스턴스화 부재
10. **production 정적 금지** — ENABLE_LIVE_TRADING=True 부재, 출금 endpoint 부재, JWT/HMAC import 부재, requests/httpx import 부재, 주문 endpoint URL literal 부재, frontend 에 UPBIT 키 부재
11. **__all__ exports**

```
cd backend
python -m pytest tests/test_upbit_adapter.py -q
```

## 13. 안전 / 정책 요약

- 본 단계 완료는 실거래 허가가 아니다 (CLAUDE.md §2.6).
- `place_order` / `cancel_order` 메서드 명은 #20 인터페이스 때문에 등장하지만 실제
  네트워크 호출은 본 단계 어디에도 없다.
- UpbitAdapter / UpbitPublicClient / UpbitAccountClient / UpbitOrderClient 모두 출금/
  이체 메서드 부재 (영구) — `assert_no_withdrawal_methods` 가 회귀로 강제.
- API key / secret / token 은 frontend 어디에도 노출되지 않는다 (정적 검사).
- AI/전략은 어떤 Upbit client 도 직접 호출/instantiate 하지 않는다.
- 22번 OKX Adapter / 23번 Binance Adapter / LIVE 주문 adapter 는 본 작업 범위가 아니다.

## 14. 후속 단계

- #22 OKX Adapter — 동일한 패턴으로 ccxt 기반 public client 분리
- #23 Binance Adapter — 동일
- production transport (httpx/requests) 추가 — 별도 PR, path 화이트리스트 유지
- LIVE 주문 adapter — 별도 클래스, OrderGateway 의 끝단에서만 호출, 별도 환경변수 +
  별도 문서 + 별도 테스트 + 별도 승인 후
