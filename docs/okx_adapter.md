# OKX Adapter (체크리스트 #22)

> Agent Trader Crypto OS v1 — OKX spot/swap read-only adapter + paper/mock 주문 + gated private

## 0.1 관련 정책 문서

- API key 권한 정책: [`docs/api_key_policy.md`](api_key_policy.md) — `api_key + secret + passphrase` 셋 모두 비밀, 출금 권한 영구 금지.
- Rate limit 정책: [`docs/api_limits.md`](api_limits.md) — OKX 코드 50011 처리.

## 1. 목적과 범위

OkxAdapter 는 OKX spot/swap 시세·호가·캔들·funding rate 조회를 위한 **read-only
adapter** 다. 주문 API 구조는 인지하되 본 단계에서는 실제 네트워크 호출을 구현하지
않는다 — Mock/Paper 또는 disabled/gated 처리만.

본 단계(#22)는 다음만 한다.

- OKX **public market data** 5개 endpoint 의 read-only 인터페이스 정의
- transport injection 으로 production transport 와 test FakeTransport 분리
- OKX 응답 code(50011 등) 기반 rate-limit 처리 + sleep 주입
- private/account 영역의 *gated stub* (credentials + transport 양쪽 필요)
- private/trade 영역의 *disabled stub* (모든 호출 즉시 disabled)
- spot/swap **PAPER 주문 엔진** (결정론, 외부 호출 없음, LIVE 거부)
- 역김프/헤지 계산을 위한 spot price + swap price + funding rate 구조 준비

본 단계는 다음을 **하지 않는다**.

- 실제 OKX 주문 / cancel / amend / leverage / margin-mode 네트워크 호출 ❌
- OK-ACCESS-KEY / OK-ACCESS-SIGN / OK-ACCESS-PASSPHRASE signing 구현 ❌
- 실제 계좌 잔고 / 포지션 조회 (production transport) ❌
- 출금 / 이체 API ❌ (영구)
- 역김프 / 헤지 자동 주문 ❌
- 23번 Binance Adapter 작업 ❌

`place_order` 메서드 명은 #20 인터페이스 때문에 등장하지만 실제 LIVE 주문 송신은
별도 LIVE adapter + OrderGateway 승격 절차 후에만 가능 (CLAUDE.md §2.6).

## 2. 모듈 구조

| 파일 | 역할 |
|---|---|
| `app/brokers/okx_adapter.py` | `OkxAdapter` (READ_ONLY ExchangeAdapter) + 심볼 헬퍼 |
| `app/brokers/okx_public.py` | `OkxPublicClient` (public market data, transport-주입) |
| `app/brokers/okx_rate_limit.py` | `parse_okx_api_error` + `should_throttle_okx` + `OkxRateLimitState` |
| `app/brokers/okx_account.py` | `OkxAccountClient` (gated, credentials+transport 필요) |
| `app/brokers/okx_trade.py` | `OkxTradeClient` (disabled stub) + `OkxPaperOrderClient` (PAPER 엔진) |

## 3. instrument / 심볼 정규화

### 3.1 `normalize_okx_inst_id(symbol, instrument_type=None)`

| 입력 | 결과 |
|---|---|
| `"BTC"` | `"BTC-USDT"` |
| `"BTC-USDT"` | `"BTC-USDT"` |
| `"btc-usdt"` | `"BTC-USDT"` |
| `"BTC/USDT"` | `"BTC-USDT"` |
| `"BTC-USDT-SWAP"` | `"BTC-USDT-SWAP"` |
| `"BTC"` (instrument_type="SWAP") | `"BTC-USDT-SWAP"` |
| `"BTC-USDT"` (instrument_type="SWAP") | `"BTC-USDT-SWAP"` |
| `"BTC-USD-260626-50000-C"` (OPTION) | `ValueError` |
| `""` / `None` | `ValueError` |

OPTION 과 복잡 FUTURES (만기/strike 포함) 은 본 단계 미지원.

### 3.2 `infer_okx_inst_type(inst_id) -> "SPOT" | "SWAP" | "FUTURES" | "UNKNOWN"`

`-SWAP`/`-FUTURES` 접미사로 판단. 2 토큰 (`BASE-QUOTE`) 은 SPOT.

### 3.3 `to_internal_symbol(okx_inst_id)`

OKX instId 와 내부 표기를 동일하게 유지 (`BTC-USDT`, `BTC-USDT-SWAP`).

## 4. OkxPublicClient

### 4.1 메서드

| 메서드 | endpoint | 반환 |
|---|---|---|
| `fetch_instruments(inst_type="SPOT")` | `GET /api/v5/public/instruments` | 카탈로그 |
| `fetch_ticker(inst_id)` | `GET /api/v5/market/ticker` | dict |
| `fetch_orderbook(inst_id, depth=20)` | `GET /api/v5/market/books` | dict |
| `fetch_candles(inst_id, bar="1m", limit=100)` | `GET /api/v5/market/candles` | list[dict] |
| `fetch_funding_rate(inst_id)` | `GET /api/v5/public/funding-rate` | dict (SWAP 전용) |

### 4.2 Transport injection

silent 네트워크 호출 없음 — transport 가 없으면 `RuntimeError`. 시그니처:

```python
transport(method: "GET", path: str, params: dict, headers: dict) -> OkxTransportResponse
```

production transport (httpx/requests) 는 본 단계에서 추가하지 않으며, 후속 PR + LIVE
승격 절차에서 추가. **path 화이트리스트** (`_assert_public_path`) 가 모든 호출에
적용 — private/account/trade path 는 본 client 로 우회 불가.

### 4.3 Rate limit / error 처리

OKX 응답 본문 `{"code": ..., "msg": ..., "data": ...}` 의 `code` 가 "0" 외이면
`OkxApiError`. `code="50011"` 은 rate-limit. `OkxRateLimitState` 가 누적 상태를
보관하고 `maybe_backoff(seconds=...)` 에서 caller 가 주입한 `sleep_fn` 으로 backoff.

```python
from app.brokers import parse_okx_api_error, OkxRateLimitState, should_throttle_okx

err = parse_okx_api_error({"code": "50011", "msg": "Requests too frequent"})
err.is_rate_limit  # True

state = OkxRateLimitState(sleep_fn=time.sleep)
state.update(body)
state.maybe_backoff(seconds=1.0)
```

## 5. OkxAdapter (ExchangeAdapter)

### 5.1 capability

```python
AdapterCapability(
    name="okx", mode="READ_ONLY",
    can_fetch_ticker=True, can_fetch_orderbook=True,
    can_fetch_balance=False, can_place_order=False, can_cancel_order=False,
    supports_futures=False, requires_secret=False,
)
```

### 5.2 두 경로

```python
# A) legacy ccxt
a = OkxAdapter()                      # production
a = OkxAdapter(client=FakeCcxtOkx())  # tests

# B) UpbitPublicClient 패턴 — transport 주입
pc = OkxPublicClient(transport=my_transport)
a = OkxAdapter(public_client=pc)
```

생성자에 `api_key`/`api_secret`/`api_password` 가 들어오면 `ValueError`.

## 6. OkxAccountClient (gated)

```python
c = OkxAccountClient()
c.fetch_balances()                                       # PermissionError

c = OkxAccountClient(api_key="x", api_secret="y")
c.fetch_balances()                                       # PermissionError (passphrase 부재)

c = OkxAccountClient(api_key="x", api_secret="y",
                     api_password="z", transport=fake)
c.fetch_balances()                                       # [{"ccy": "BTC", ...}]
```

### 6.1 안전 정책

- 세 credentials (key/secret/passphrase) **모두** 있어야 credentials_loaded=True.
- secret/passphrase 미보관 — `__init__` 끝에서 del. repr 미노출.
- `/api/v5/account/balance` + `/api/v5/account/positions` 만 화이트리스트.
- 출금/이체 메서드 부재 — `assert_no_withdrawal_methods` 통과.
- production transport 는 본 단계 미포함. LIVE 승격 절차 후 별도 PR.

## 7. OkxTradeClient (disabled stub)

모든 메서드 호출 즉시 `ExchangeAdapterDisabledError`.

- `place_order`, `cancel_order`, `amend_order`, `get_order` — disabled
- `__init__` 이 받은 key/secret/passphrase 는 즉시 폐기
- JWT / HMAC / OK-ACCESS-SIGN signing 코드 본 모듈에 없음
- 실제 trade endpoint URL literal 부재 (`/api/v5/trade/order` 등 어디에도 없음)
- 출금 메서드 부재
- `capability.to_dict()` 가 6개 동작 모두 False + "gated on OrderGateway + LIVE
  permission + separate phase" 명시

## 8. OkxPaperOrderClient (PAPER spot/swap 엔진)

외부 네트워크 호출 없는 결정론적 paper engine.

### 8.1 동작 표

| 입력 | 결과 |
|---|---|
| SPOT MARKET BUY + 잔고 충분 | `FILLED`, 잔고 차감, `order_id="okx-paper-<10hex>"` |
| SPOT LIMIT SELL + price>0 | `ACCEPTED` |
| SWAP MARKET BUY (`inst_id` 끝 `-SWAP`) | `FILLED`, leverage/margin 미적용 (입력은 받음) |
| `inst_type=SWAP` + `inst_id` 가 `-SWAP` 없음 | `REJECTED` |
| `inst_type=SPOT` + `inst_id` 가 `-SWAP` 끝남 | `REJECTED` |
| `inst_id` 가 단일 토큰 (예: `"BTC"`) | `REJECTED` (invalid inst_id) |
| `notional_usdt ≤ 0` and `sz ≤ 0` | `REJECTED` |
| SPOT BUY notional > 잔고 | `REJECTED` (`insufficient_balance`) |
| `client_order_id` 또는 `idempotency_key` 중복 | 첫 결과 그대로 반환 (idempotent) |
| `mode="LIVE"` 또는 `trading_mode="LIVE"` | `REJECTED` (`live_not_wired`) |
| cancel(알려진 order_id) | `ACCEPTED` (CANCELED 표기) |
| cancel(미존재 order_id) | `ACCEPTED` (grace, reason 에 unknown 명시) |

### 8.2 leverage / margin / reduce_only

입력 dict 의 `leverage`, `margin_mode`, `reduce_only` 필드는 **받기만 한다** —
실제 거래소 호출이나 포지션 회계에 적용되지 않는다. `OrderResult.audit` 에 기록되어
운영자가 의도를 확인할 수 있다.

### 8.3 audit secret sanitize

`OrderResult.audit` blob 에서 다음 키를 자동 제거.

`api_key`, `api_secret`, `secret`, `access_token`, `token`, `passphrase`, `password`,
`private_key`, `ok_access_key`, `ok_access_sign`, `ok_access_passphrase`,
`ok_access_timestamp`.

## 9. 단일 주문 경로 보존 (CLAUDE.md §2.4)

```text
Strategy → Agent → RiskManager → OrderGuard → PermissionGate
        → ApprovalQueue → OrderGateway → Executor/Adapter
```

| 검증 | 회귀 테스트 |
|---|---|
| Strategy 가 okx_* 모듈 import 안 함 | `test_strategies_do_not_import_okx_module` |
| Agent 가 okx_* 모듈 import 안 함 (compliance.py 예외) | `test_agents_do_not_import_okx_module` |
| Strategy 가 Okx*Client 인스턴스화 안 함 | `test_strategies_no_okx_client_instantiation` |
| Agent 가 Okx*Client 인스턴스화 안 함 | `test_agents_no_okx_client_instantiation` |

## 10. MarketDataCollector 연결

`OkxAdapter` 는 `MarketDataSource` Protocol 을 만족하므로
`MarketDataCollector(sources={"okx": adapter})` 로 주입 가능. 기존 회귀
(`test_collector_can_use_okx_adapter`) + 신규 회귀
(`test_collector_with_okx_adapter_does_not_invoke_orders`)가 시세만 호출되고 주문
메서드는 호출되지 않음을 보장. 기본 source 는 mock 유지 — OKX 는 명시적 선택 시만.

## 11. 역김프 / 헤지 준비 구조

본 단계에서 역김프/헤지 전략을 구현하지 않는다. 다만 필요한 데이터 구조는 모두 준비.

```python
a = OkxAdapter(public_client=pc)
spot_tk  = a.fetch_ticker("BTC-USDT")                   # spot
swap_tk  = a.fetch_ticker("BTC-USDT-SWAP")              # swap
funding  = pc.fetch_funding_rate("BTC-USDT-SWAP")       # funding
```

역김프/헤지 전략은 후속 단계에서 다음을 조합:
- Upbit KRW price (#21)
- OKX spot/swap price (본 단계)
- OKX funding rate (본 단계)
- FX rate (별도 source)
- Kimp / Risk guard

본 단계에서 BUY/SELL 직접 신호나 자동 숏/헤지 포지션을 만들지 않는다.

## 12. 회귀 테스트

`backend/tests/test_okx_adapter.py` — **88 케이스**. 주요 분류:

1. **심볼 정규화** (10) — module-level helpers, OPTION 거부, SPOT/SWAP 추론
2. **Capability + API key 거부** (기존)
3. **fetch_ticker / fetch_orderbook** — ccxt fake + OkxPublicClient transport
4. **OkxPublicClient** (10) — 5개 endpoint 응답 파싱, non-public path 차단,
   transport 미주입 → RuntimeError, invalid inst_id / bar / inst_type 거부, 50011
   응답 → OkxPublicAPIError + rate_limit state 갱신
5. **Rate limit** (7) — code 50011, OkxRateLimitState sleep injection, ok 응답이
   last_error clear
6. **OkxAccountClient** (5) — credentials 없음 / passphrase 누락 / fake transport,
   repr 에 secret 노출 부재, 출금 메서드 부재
7. **OkxTradeClient** (4) — 모든 동작 disabled, credentials 인자 폐기, capability
   dict, 출금 메서드 부재
8. **OkxPaperOrderClient** (12) — spot MARKET-FILLED / LIMIT-ACCEPTED, swap
   MARKET-FILLED, SWAP suffix 강제, invalid inst_id, LIVE 거부, 잔고 부족,
   idempotent, cancel known/unknown, audit secret 부재
9. **단일 주문 경로** (4) — Strategy/Agent 가 okx 모듈 import·인스턴스화 부재
10. **production 정적 금지** (5) — ENABLE_LIVE_TRADING=True 부재, 출금 endpoint
    부재, `OK-ACCESS-SIGN`/`OK_ACCESS_SIGN` 부재, JWT·HMAC import 부재,
    requests·httpx import 부재, okx_public.py 에 ccxt import 부재, 실제 trade
    endpoint URL literal 부재, frontend OKX 키 부재
11. **brokers __all__ exports + collector 통합**

```
cd backend
python -m pytest tests/test_okx_adapter.py -q
```

## 13. 안전 / 정책 요약

- 본 단계 완료는 실거래 허가가 아니다 (CLAUDE.md §2.6).
- `place_order` / `cancel_order` 메서드 명은 #20 인터페이스 때문에 등장하지만 실제
  네트워크 호출은 본 단계 어디에도 없다. `OkxPaperOrderClient` 만 결정론적 mock
  결과를 반환하며 `mode=LIVE` 는 거부.
- OkxAdapter / OkxPublicClient / OkxAccountClient / OkxTradeClient /
  OkxPaperOrderClient 모두 출금/이체 메서드 부재 (영구).
- OK-ACCESS-KEY / OK-ACCESS-SIGN / OK-ACCESS-PASSPHRASE signing 코드 부재 (정적 회귀).
- API key/secret/passphrase 는 frontend 어디에도 노출되지 않는다 (정적 회귀).
- AI/전략은 어떤 OKX client 도 직접 호출/instantiate 하지 않는다.
- 23번 Binance Adapter / 김프·헤지 전략 / LIVE 주문 adapter 는 본 작업 범위가
  아니다.

## 14. 후속 단계

- #23 Binance Adapter — Upbit/OKX 와 동일 패턴
- production transport (httpx/requests) 추가 — 별도 PR, path 화이트리스트 유지
- 김프 / 역김프 / 헤지 전략 — 별도 PR, signal-only 출력 (BUY/SELL 직접 반환 없음)
- LIVE 주문 adapter — 별도 클래스, OrderGateway 끝단에서만 호출, 별도 환경변수 +
  별도 문서 + 별도 테스트 + 별도 승인 후
