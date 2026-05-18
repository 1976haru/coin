# Binance Adapter — research & skeleton (체크리스트 #23)

> Agent Trader Crypto OS v1 — Binance 2차 후보 거래소 read-only 조사 + gated stubs

## 0. 한 줄 요약

본 단계(#23) 는 **research / read-only skeleton** 이다. 실거래·계정·선물·마진·
레버리지·출금 기능은 본 단계에서 **구현하지 않는다**. Binance live/trading 활성화는
별도 phase + **규제·지역 제한 확인** + 별도 LIVE adapter + OrderGateway 끝단 호출 +
별도 환경변수 + 별도 승인 절차 통과 후에만 가능 (CLAUDE.md §2.4 / §2.6).

## 1. 목적과 범위

Binance 는 해외 유동성 비교용 **2차 후보 거래소** 다 (1차: OKX). 본 단계는
read-only spot market data 조사·스켈레톤 + Mock/Fake 테스트만 작성한다.

본 단계는 다음만 한다.

- Binance Spot **public market data** endpoint 5종의 read-only 인터페이스 정의
- transport injection 으로 production transport 와 test FakeTransport 분리
- `X-MBX-USED-WEIGHT` 기반 rate limit 상태 추적
- private/account 영역의 *disabled stub* (본 단계 일체 차단)
- private/trade 영역의 *disabled stub* (본 단계 일체 차단)
- Mock/Fake transport 로 동작하는 회귀 테스트
- 해외 유동성 비교에 사용할 가격/호가/klines 구조 준비

본 단계는 다음을 **하지 않는다**.

- 실제 Binance 주문 / cancel / amend 네트워크 호출 ❌
- HMAC-SHA256 signing / `X-MBX-APIKEY` 헤더 구성 / timestamp/signature 생성 ❌
- 실제 private account / open-orders 네트워크 호출 ❌
- 선물(USDM/COINM) / 마진 / 레버리지 / 포지션 모드 설정 ❌
- 출금 / 이체 API ❌ (영구)
- frontend 가 Binance API 를 직접 호출 ❌
- `python-binance` / `binance-connector` SDK 추가 ❌
- 24번 이후 작업 ❌

## 2. 규제 / 지역 제한 (HARD GATE)

**Binance 서비스 가능 지역, 약관, KYC, IP/지역 제한, 한국 사용자의 이용 가능성은
변동 가능하다.** 본 모듈은 그 정책을 코드 레벨에서 강제한다.

- `BinanceAccountClient.fetch_*` 모두 `BinanceAccountPermissionError` 즉시 발생.
- `BinanceTradeClient.{place,cancel,get}_order` 모두 `ExchangeAdapterDisabledError`
  발생. 에러 메시지에 `binance_live_trading_disabled_until_regulatory_review` 가
  포함된다.
- 위 두 client 는 **credentials 가 들어와도 보관/사용하지 않는다.**
- `BinanceAdapter` 는 mode=`READ_ONLY` 영구 고정 + 생성자에 `api_key`/`api_secret`
  들어오면 `ValueError`.

**Binance.US 와 Binance Global 은 별개 거래소다.** 실제 live 활성화 단계에서는
운영자가 명시적으로 어느 거래소(US/Global) 인지 선언하고, 사용자의 IP/규제 영역에
맞춰 해당 host 와 API 만 사용해야 한다. 본 단계에서는 어느 쪽도 활성화하지 않는다.

## 3. 모듈 구조

| 파일 | 역할 |
|---|---|
| `app/brokers/binance_adapter.py` | `BinanceAdapter` (READ_ONLY ExchangeAdapter) + 심볼 헬퍼 |
| `app/brokers/binance_public.py` | `BinancePublicClient` (public market data, transport-주입) |
| `app/brokers/binance_rate_limit.py` | `X-MBX-USED-WEIGHT` 파싱 + `BinanceRateLimitState` |
| `app/brokers/binance_account.py` | `BinanceAccountClient` disabled stub (regulatory gate) |
| `app/brokers/binance_trade.py` | `BinanceTradeClient` disabled stub (regulatory gate) |

## 4. 심볼 정규화

Binance native 형식은 separator 없이 `BTCUSDT` 형태. 내부 표현은 `BTC-USDT`.

### 4.1 `normalize_binance_symbol(symbol) -> str`

| 입력 | 결과 |
|---|---|
| `"BTC"` | `"BTCUSDT"` (default quote USDT) |
| `"btc"` | `"BTCUSDT"` |
| `"BTC-USDT"` | `"BTCUSDT"` |
| `"BTC/USDT"` | `"BTCUSDT"` |
| `"BTCUSDT"` | `"BTCUSDT"` |
| `"btcusdt"` | `"BTCUSDT"` |
| `"BTCUSDT-PERP"` / `"BTCUSDT_PERP"` | `ValueError` (futures/perp 미지원) |
| `""` / `None` / `"   "` | `ValueError` |

### 4.2 `to_internal_symbol(binance_symbol) -> str`

알려진 quote 후미(`USDT`, `USDC`, `BUSD`, `TUSD`, `FDUSD`, `BTC`, `ETH`, `BNB`)
를 분리해 내부 `BASE-QUOTE` 표기로 변환.

### 4.3 `is_supported_binance_quote(symbol, allowed_quotes=None)`

기본 허용 quote: `USDT`, `USDC`, `BTC`, `ETH`. 호출자가 `allowed_quotes=["USDT"]`
같은 화이트리스트를 줄 수 있다.

## 5. BinancePublicClient

### 5.1 메서드

| 메서드 | endpoint | 비고 |
|---|---|---|
| `fetch_server_time()` | `GET /api/v3/time` | 서버 시각 (ms) |
| `fetch_exchange_info(symbol=None)` | `GET /api/v3/exchangeInfo` | 마켓 카탈로그 |
| `fetch_ticker(symbol)` | `GET /api/v3/ticker/24hr` | 24h 통계 + 현재가 |
| `fetch_orderbook(symbol, limit=100)` | `GET /api/v3/depth` | limit ∈ {5,10,20,50,100,500,1000,5000} |
| `fetch_klines(symbol, interval="1m", limit)` | `GET /api/v3/klines` | interval ∈ 16개, limit 1..1000 |

### 5.2 Path 화이트리스트

`_assert_public_path` 가 다음 8개 path 만 허용:

```
/api/v3/exchangeInfo, /api/v3/ticker/24hr, /api/v3/depth, /api/v3/klines,
/api/v3/time, /api/v3/avgPrice, /api/v3/ticker/price, /api/v3/ticker/bookTicker
```

외부 path (예: `/api/v3/order`, `/api/v3/account`, `/sapi/v1/...`, `/fapi/v1/...`)
는 즉시 `BinancePublicAPIError`. 본 client 는 어떤 경로로도 private/order/margin/
futures endpoint 를 호출하지 못한다.

### 5.3 Transport injection

silent 네트워크 호출 없음 — transport 미주입 → `RuntimeError`.

```python
transport(method: "GET", path: str, params: dict, headers: dict) -> BinanceTransportResponse
```

production transport (httpx/requests) 는 본 단계에서 추가하지 않는다. 후속 PR 에서
추가 시 `BINANCE_PUBLIC_DATA_HOST = "data-api.binance.vision"` 을 권장 host 로
사용하면 public market data 전용 endpoint 로 분리 가능 — 인증/주문 host 와 격리.

### 5.4 Symbol validation

`fetch_ticker` / `fetch_orderbook` / `fetch_klines` 는 native 형식(`BTCUSDT`) 만
허용. slash/dash 가 포함되면 `ValueError`. 변환은 `BinanceAdapter` 측에서
`normalize_binance_symbol` 로 수행 후 전달.

## 6. Rate limit

Binance 의 REST rate limit 은 **request weight** 기반이다 — endpoint 별 weight
가 다르고, 1분당 누적 weight 가 한도(기본 약 1200)를 넘으면 거부된다. 호출 후
사용된 weight 는 응답 헤더 `X-MBX-USED-WEIGHT` / `X-MBX-USED-WEIGHT-1M` 에 담긴다.

```python
from app.brokers import (
    parse_binance_used_weight, should_throttle_binance,
    BinanceRateLimitState, BINANCE_WEIGHT_SOFT_LIMIT,
)

parsed = parse_binance_used_weight({"X-MBX-USED-WEIGHT-1M": "23"})
# {"used_weight_1m": 23}

should_throttle_binance({"used_weight_1m": BINANCE_WEIGHT_SOFT_LIMIT})  # True
```

`BinanceRateLimitState(sleep_fn=time.sleep)` 가 누적 weight 와 throttle 결정을
관리. 본 모듈은 sleep 자체를 호출하지 않으며 caller 가 `maybe_throttle(sleep_seconds=...)`
호출 시 주입된 `sleep_fn` 으로만 backoff. 테스트는 가짜 sleep 함수로 빠르게 검증.

## 7. BinanceAdapter (ExchangeAdapter)

### 7.1 capability

```python
AdapterCapability(
    name="binance", mode="READ_ONLY",
    can_fetch_ticker=True, can_fetch_orderbook=True,
    can_fetch_balance=False, can_place_order=False, can_cancel_order=False,
    supports_futures=False, requires_secret=False,
)
```

### 7.2 두 경로

```python
# A) legacy — ccxt.binance 자동 (또는 fake ccxt 주입)
a = BinanceAdapter()
a = BinanceAdapter(client=FakeCcxtBinance())

# B) 신규 — BinancePublicClient + transport 주입
pc = BinancePublicClient(transport=my_transport)
a = BinanceAdapter(public_client=pc)
```

`api_key`/`api_secret` 전달 시 `ValueError`. mode=`READ_ONLY` 영구.

## 8. BinanceAccountClient (disabled stub)

본 단계는 read-only research/skeleton 이라 **credentials 가 들어와도 모든 메서드가
즉시 PermissionError**. credentials 는 보관하지 않는다 (`__init__` 끝에서 pop).

- `fetch_balances()` — disabled
- `fetch_account_info()` — disabled
- `fetch_open_orders(symbol)` — disabled
- 출금 메서드 부재 (영구)
- repr 에 secret 미노출

후속 단계에서 LIVE 활성화 시 별도 LIVE class 로 구현하며, 본 stub 은 그대로 보존.

## 9. BinanceTradeClient (disabled stub)

모든 메서드 호출 즉시 `ExchangeAdapterDisabledError`.

- `place_order` / `cancel_order` / `get_order` — disabled
- `DISABLED_REASON = "binance_live_trading_disabled_until_regulatory_review"` 상수
- `capability.to_dict()` 가 7개 동작(place/cancel/get/set_leverage/set_margin_type/
  trade_futures/trade_margin) 모두 False + "regulatory & regional review" 명시
- `__init__` 이 받은 credentials 즉시 폐기
- 실제 trade endpoint URL literal 부재 (정적 회귀로 강제)
- HMAC signing 부재

## 10. 단일 주문 경로 보존 (CLAUDE.md §2.4)

```text
Strategy → Agent → RiskManager → OrderGuard → PermissionGate
        → ApprovalQueue → OrderGateway → Executor/Adapter
```

### 10.1 강제 메커니즘

| 검증 | 회귀 테스트 |
|---|---|
| Strategy 가 binance_* 모듈 import 안 함 | `test_strategies_do_not_import_binance_module` |
| Agent 가 binance_* 모듈 import 안 함 (compliance.py 예외) | `test_agents_do_not_import_binance_module` |
| Strategy 가 Binance*Client 인스턴스화 안 함 | `test_strategies_no_binance_client_instantiation` |
| Agent 가 Binance*Client 인스턴스화 안 함 | `test_agents_no_binance_client_instantiation` |

## 11. MarketDataCollector 연결

`BinanceAdapter` 는 `MarketDataSource` Protocol 을 만족하므로
`MarketDataCollector(sources={"binance": adapter})` 로 주입 가능. 기본 source 는
mock 유지 — Binance source 는 명시적 선택 시만. 신규 회귀
`test_collector_with_binance_adapter_does_not_invoke_orders` 가 시세만 호출되고
`place_order`/`cancel_order` 가 호출되지 않음을 보장.

## 12. 해외 유동성 비교 준비 구조

본 단계에서 비교 로직을 완성하지 않는다. 다만 필요한 데이터 구조는 모두 준비.

```python
# OKX BTC-USDT spot + Binance BTCUSDT spot 비교 예시 (read-only)
okx_tk = okx_adapter.fetch_ticker("BTC-USDT")
bin_tk = binance_adapter.fetch_ticker("BTC-USDT")     # 내부 → BTCUSDT 자동 변환
```

해외 유동성 비교 / 김프-OKX-Binance 트라이앵글 분석 / spread 분석은 후속 단계에서
별도 모듈로 구현한다 — 본 adapter 는 데이터 소스만 제공.

## 13. 회귀 테스트

`backend/tests/test_binance_adapter.py` — **84 케이스**. 주요 분류:

1. **심볼 정규화 (기존)** — to_binance_symbol staticmethod, native quote 후미 분리
2. **모듈 helpers** (6) — normalize_binance_symbol, to_internal_symbol, is_supported_binance_quote, futures/perp 거부
3. **Capability + API key 거부 (기존)** — spot only, supports_futures=False
4. **fetch_ticker / fetch_orderbook (기존 + 신규)** — ccxt fake + BinancePublicClient transport
5. **Rate limit** (8) — used-weight 헤더 파싱, throttle, RateLimitState sleep injection
6. **BinancePublicClient** (12) — 5개 endpoint 응답 파싱, non-public path 차단, transport 미주입 → RuntimeError, invalid symbol/limit/interval 거부, 4xx → BinancePublicAPIError, rate_limit 자동 갱신, public data host 상수
7. **BinanceAccountClient** (4) — 모든 메서드 disabled (credentials 무관), repr 미노출, 출금 메서드 부재
8. **BinanceTradeClient** (5) — 모든 동작 disabled, regulatory reason, credentials 폐기, capability dict, 출금 메서드 부재
9. **단일 주문 경로** (4) — Strategy/Agent 가 binance 모듈 import·인스턴스화 부재
10. **production 정적 금지** (6) — ENABLE_LIVE_TRADING=True 부재, 실제 trade/account endpoint URL literal 부재, `X-MBX-APIKEY` literal 부재, JWT·HMAC import 부재, requests·httpx import 부재, binance_public.py 에 ccxt import 부재, python-binance/binance-connector import 부재, frontend BINANCE 키 부재
11. **brokers __all__ exports + collector 통합**

```
cd backend
python -m pytest tests/test_binance_adapter.py -q
```

## 14. 후속 단계

- **production transport** (httpx/requests) 추가 시 path 화이트리스트 유지 + 별도 PR.
  권장 host `data-api.binance.vision` (public market data 전용) 사용 — 인증/주문 host
  와 격리.
- **LIVE 주문 adapter** — 본 작업 범위 밖. 별도 LIVE class + OrderGateway 끝단 호출
  + **규제·지역 제한 확인** + 별도 환경변수 + 별도 문서 + 별도 테스트 + 별도 승인 후.
- **선물/마진** — 본 작업 범위 밖. 별도 phase (#67 Futures Scope 이후) 에서 검토.
- **해외 유동성 비교 / 김프-Binance 트라이앵글** — 별도 PR, signal-only 출력
  (BUY/SELL 직접 반환 없음, OrderGateway 경로 사용).

## 15. 안전 / 정책 요약

- 본 단계 완료는 실거래 허가가 아니다 (CLAUDE.md §2.6).
- `place_order` / `cancel_order` 메서드 명은 #20 인터페이스 때문에 등장하지만 실제
  네트워크 호출은 본 단계 어디에도 없다. account/trade stub 은 모두 disabled.
- BinanceAdapter / BinancePublicClient / BinanceAccountClient / BinanceTradeClient
  모두 출금/이체 메서드 부재 (영구).
- HMAC signing / `X-MBX-APIKEY` / signature 생성 코드 부재 (정적 회귀).
- API key/secret 은 frontend 어디에도 노출되지 않는다 (정적 회귀).
- AI/전략은 어떤 Binance client 도 직접 호출/instantiate 하지 않는다.
- python-binance / binance-connector SDK import 부재.
- 24번 이후 작업은 본 작업 범위가 아니다.
