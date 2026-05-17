# Exchange Adapter Interface (체크리스트 #20)

> Agent Trader Crypto OS v1 — 거래소 교체 가능 공통 인터페이스

## 1. 목적과 범위

거래소별 시세/잔고/주문 송신을 표준화한 공통 인터페이스를 정의한다. 실제 거래소
구현(#21 Upbit, #22 OKX, #23 Binance) 은 본 인터페이스를 따른다.

본 단계(#20)는 다음만 한다.

- `ExchangeAdapter` ABC + `AdapterCapability` 정의
- `MockExchangeAdapter` (네트워크 없는 결정론적 paper-only adapter)
- Upbit/OKX/Binance adapter 의 READ_ONLY stub 보존
- contract tests — 인터페이스 계약 + 안전 가드 회귀

본 단계는 다음을 **하지 않는다**.

- 실제 거래소 LIVE 주문 호출 ❌
- 실제 private endpoint / 인증 / HMAC signing 호출 ❌
- AI/전략이 adapter 직접 호출 ❌
- 21번 Upbit Adapter, 22번 OKX Adapter 실주문 구현 ❌
- 전체 시장 자동 스캔 ❌

## 2. 공통 메서드 카탈로그

| 메서드 | 반환 | 비고 |
|---|---|---|
| `fetch_ticker(symbol)` | `Ticker` | 전체 Ticker (price/bid/ask/spread/volume/ts) |
| `fetch_price(symbol)` | `float` | fetch_ticker.price 의 spec alias |
| `fetch_orderbook(symbol, depth=5)` | `OrderBook` | best_bid < best_ask 강제 |
| `fetch_balance()` | `dict` | capability.can_fetch_balance 필요 |
| `get_balance()` | `dict` | fetch_balance 의 spec alias |
| `place_order(order)` | `OrderResult` | capability.can_place_order 필요 |
| `cancel_order(order_id)` | `OrderResult` | capability false 면 REJECTED |

`Ticker`, `OrderBook`, `OrderRequest`, `OrderResult` 는 `app.schemas` 의 공유 스키마.

## 3. AdapterCapability / AdapterMode

```python
@dataclass(frozen=True)
class AdapterCapability:
    name: str
    mode: AdapterMode  # "READ_ONLY" | "PAPER" | "SANDBOX" | "LIVE"
    can_fetch_ticker:    bool = True
    can_fetch_orderbook: bool = True
    can_fetch_balance:   bool = False
    can_place_order:     bool = False
    can_cancel_order:    bool = False
    supports_futures:    bool = False
    requires_secret:     bool = False
```

`capability` 가 false 인 동작 호출 시 `ExchangeAdapterDisabledError` 또는
`OrderResult(status="REJECTED", ...)` 가 반환된다 (조용한 NotImplemented 없음).

## 4. LIVE 모드 정책 (CLAUDE.md §2.2)

base `place_order` 는 두 가지 LIVE 차단을 강제한다.

1. `capability.mode == "LIVE"` 인데 `settings.enable_live_trading == False` 이면 사전
   거부. 응답: `status="REJECTED"`, `route="live_not_wired"`, reason 에
   `ENABLE_LIVE_TRADING` 명시.
2. order dict 가 `mode="LIVE"` / `trading_mode="LIVE"` 를 명시해도 PAPER-only adapter
   (Mock) 는 거부. 응답: `status="REJECTED"`, `route="live_not_wired"`.

따라서 어떤 경로로도 LIVE 키 / LIVE flag 없이 실주문이 송신되지 않는다.

## 5. MockExchangeAdapter

결정론적 paper-only adapter. 외부 네트워크 호출 없음, API 키 없음.

### 5.1 capability

```python
AdapterCapability(
    name="mock", mode="PAPER",
    can_fetch_ticker=True, can_fetch_orderbook=True,
    can_fetch_balance=True, can_place_order=True, can_cancel_order=True,
    supports_futures=False, requires_secret=False,
)
```

### 5.2 동작 규칙

| 입력 | 결과 |
|---|---|
| MARKET BUY + 잔고 충분 | `FILLED`, 잔고 차감, `order_id="mock-<10hex>"` |
| LIMIT SELL + price>0 | `ACCEPTED` (체결은 별도) |
| LIMIT BUY + price 누락/0 | `REJECTED` (`LIMIT order requires price>0`) |
| notional_usdt ≤ 0 | `REJECTED` |
| BUY notional > 잔고 | `REJECTED` (`insufficient_balance`) |
| client_order_id / idempotency_key 중복 | 첫 결과 그대로 반환 (idempotent — 잔고 이중 차감 없음) |
| `mode="LIVE"` 또는 `trading_mode="LIVE"` | `REJECTED` (PAPER-only) |
| cancel(알려진 order_id) | `ACCEPTED` (mock cancel) |
| cancel(미존재 order_id) | `ACCEPTED` (grace, reason에 unknown 명시) |

### 5.3 audit secret sanitize

응답 `OrderResult.audit` blob 은 다음 키를 자동 제거한다 — 사용자가 실수로 secret 류
값을 order dict 에 넣어도 응답에 새지 않는다.

`api_key`, `api_secret`, `secret`, `access_token`, `token`, `passphrase`, `password`,
`private_key`.

## 6. Upbit / OKX / Binance adapter (이번 단계: stub)

세 adapter 모두 `mode="READ_ONLY"` 영구 고정 — `place_order` / `fetch_balance` /
`cancel_order` capability false. 호출 시 `ExchangeAdapterDisabledError`.

- 생성자에 `api_key` / `api_secret` / `api_password` 가 들어오면 `ValueError`.
- pyupbit / ccxt 는 **lazy import** — 클래스 정의 시점에 의존성 부재라도 안전.
- 실제 주문 코드는 #21/#22/#23 후속 단계에서 별도 LIVE adapter 로 추가하며, 본
  READ_ONLY adapter 는 그대로 보존한다.

### 6.1 Upbit 확장 (#21, 2026-05-18)

`UpbitAdapter` 외에 4개 보조 모듈 추가:

- `UpbitPublicClient` (`upbit_public.py`) — transport-주입 public quotation client
  (markets / ticker / orderbook / candles_minutes / trades_ticks). path 화이트리스트
  강제. 자세한 사용은 `docs/upbit_adapter.md`.
- `parse_remaining_req` / `should_throttle` / `RateLimitState` (`upbit_rate_limit.py`)
  — Remaining-Req 헤더 파싱 + 안전한 throttle 결정 + sleep_fn 주입.
- `UpbitAccountClient` (`upbit_account.py`) — gated stub. credentials/transport
  둘 다 없으면 `UpbitAccountPermissionError`. secret 미보관/미노출. /v1/accounts 만
  화이트리스트.
- `UpbitOrderClient` (`upbit_order.py`) — disabled stub. 모든 메서드 즉시
  `ExchangeAdapterDisabledError`. JWT/HMAC signing 코드 부재.

`UpbitAdapter` 가 `public_client` 인자로 `UpbitPublicClient` 를 주입받을 수 있으며,
주입되면 transport 기반 경로를 사용하고 그렇지 않으면 legacy pyupbit 경로를 사용한다.

## 7. 단일 주문 경로 (CLAUDE.md §2.4)

```text
Strategy → Agent → RiskManager → OrderGuard → PermissionGate
        → ApprovalQueue → OrderGateway → Executor/Adapter
```

### 7.1 강제 메커니즘

| 검증 | 메커니즘 |
|---|---|
| Strategy 가 broker import 안 함 | `test_strategies_do_not_import_brokers` (regex grep) |
| Agent 가 broker import 안 함 | `test_agents_do_not_import_brokers` (compliance.py 만 예외) |
| Strategy 가 `.place_order(` / `.cancel_order(` 호출 안 함 | `test_strategies_do_not_call_place_order` |
| Agent 가 `.place_order(` / `.cancel_order(` 호출 안 함 | `test_agents_do_not_call_place_order` |
| Market 모듈도 adapter 호출 안 함 | `test_market_modules_do_not_call_place_order` |

`compliance.py` 는 *meta-checker* 로 brokers 를 import 하지만 trading 의도가 아니다
— `assert_no_withdrawal_methods` 같은 안전 검증에만 사용.

## 8. OrderGateway 연결

기존 `OrderGateway` 는 PaperBroker 와 ApprovalQueue 로 본 단계 이전부터 연결되어
있다. 본 단계에서는 단일 주문 경로를 깨지 않기 위해 OrderGateway 의 adapter injection
구조를 손대지 않는다.

향후 LIVE adapter 연결은 별도 승격 절차 (CLAUDE.md §2.6) 와 함께 #21/#22 에서
다룬다. 본 단계는 인터페이스 + Mock + contract tests 까지로 한정한다.

## 9. 회귀 테스트

`backend/tests/test_exchange_adapter.py` — **53 케이스**. 주요 분류:

1. **AdapterCapability** — 기본값, to_dict 직렬화, secret-like 값 부재
2. **추상 인터페이스 강제** — `ExchangeAdapter()` 직접 인스턴스화 실패
3. **disabled 동작** — fetch_balance/place_order 호출 시 raise / cancel 시 REJECTED
4. **MockExchangeAdapter 결정론** — 동일 symbol 동일 가격
5. **spec 메서드 alias** — fetch_price / get_balance
6. **orderbook depth + best_bid<best_ask**
7. **MARKET FILLED / LIMIT ACCEPTED / LIMIT no-price REJECTED**
8. **cancel ACCEPTED (known + unknown)**
9. **client_order_id idempotent + idempotency_key fallback**
10. **insufficient_balance REJECTED + 잔고 보존**
11. **LIVE mode REJECTED (mock + base + LIVE-flag false)**
12. **audit secret sanitize**
13. **Upbit/OKX/Binance stub disabled + api_key 거부**
14. **MarketDataSource Protocol 호환 + collector 직접 주입**
15. **출금 메서드 부재 (base / mock / PaperBroker)**
16. **단일 주문 경로 — Strategy/Agent/Market 정적 검증**
17. **brokers 모듈 내 ENABLE_LIVE_TRADING=True / secret literal / requests·httpx·hmac import 부재**

```
cd backend
python -m pytest tests/test_exchange_adapter.py -q
```

## 10. 안전 / 정책 요약

- 본 단계 완료는 실거래 허가가 아니다 (CLAUDE.md §2.6).
- `place_order` 메서드 명은 인터페이스에만 존재하며, capability.mode + ENABLE_LIVE_TRADING
  이중 게이트를 통과한 LIVE adapter 만 실제 송신 가능. 현재 시점에서는 LIVE adapter 가
  존재하지 않는다.
- `MockExchangeAdapter` 는 PAPER 영구 고정 — LIVE 키를 받지 않고, dict 의 `mode="LIVE"`
  요청도 명시적으로 거부한다.
- 출금 / 이체 메서드는 base / mock / paper / 실제 stub 어디에도 정의되어 있지 않다 — 
  `assert_no_withdrawal_methods` 가 회귀로 강제.
- AI/전략은 adapter 를 직접 호출할 수 없다 (모듈 경계 + 호출 정적 검증).
- 21번/22번 Upbit/OKX 실주문 adapter 구현은 본 작업 범위가 아니다.

## 11. 후속 단계

- #21 Upbit Adapter — LIVE adapter 신설 (별도 클래스), 본 READ_ONLY adapter 는 보존
- #22 OKX Adapter — 동일
- #23 Binance Adapter — 동일
- 모든 LIVE adapter 활성화는 별도 환경변수 + 별도 문서 + 별도 테스트 + 별도 승인 후
