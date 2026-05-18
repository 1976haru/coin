# Mock Broker (체크리스트 #24)

> Agent Trader Crypto OS v1 — 실제 거래소 없이 주문/체결/잔고/포지션을 재현하는
> 결정론적 시뮬레이션 브로커

## 0. 한 줄 요약

`MockBroker` (`backend/app/brokers/mock_simulation.py`) 는 **CI 와 안전한 테스트
전용** 시뮬레이션 브로커다. 외부 거래소와 연결하지 않으며, 모든 결과에
`is_real_trade=False`, `execution_source="mock_broker"`, `warning="Mock execution
only. Not real profit or real trade."` 가 포함된다.

`MockBroker` 결과를 실제 수익으로 오해해서는 안 된다.

## 1. 목적과 범위

- 실제 거래소 API 없이 주문 라이프사이클 (BUY/SELL, MARKET/LIMIT, FILLED/OPEN/
  CANCELED/REJECTED) 을 결정론적으로 재현.
- 다중 자산 잔고 (free + locked) + 포지션 (qty + avg_entry_price + realized/
  unrealized PnL) 회계.
- 수수료 (fee_bps) + 슬리피지 (slippage_bps) 반영.
- 외부 네트워크 호출 0 — `requests` / `httpx` / `ccxt` / `pyupbit` / `binance` /
  `okx` SDK 어느 것도 import 하지 않는다 (정적 회귀로 강제).

본 단계는 다음을 **하지 않는다**.

- 실제 거래소 API 호출 ❌
- 실제 Upbit/OKX/Binance 주문 호출 ❌
- 실제 잔고/계좌/포지션 조회 ❌
- LIVE mode 주문 처리 ❌ (생성 자체 차단 + 요청 거부)
- frontend 에 API key/secret/token 저장 ❌
- Strategy/Agent 가 본 브로커를 직접 호출 ❌ (모듈 경계 + 정적 회귀)

## 2. `MockExchangeAdapter` 와의 차이

| | `MockExchangeAdapter` (`mock_broker.py`) | `MockBroker` (`mock_simulation.py`) |
|---|---|---|
| 분류 | ExchangeAdapter 구현체 | 시뮬레이션 브로커 (facade) |
| 잔고 | 단일 USDT 잔고 | 다중 자산 (BASE+QUOTE) + locked |
| 포지션 | (없음) | qty + avg_entry_price + realized/unrealized PnL |
| LIMIT | (지원) — 별도 book 없음 | open book + cancel 시 locked 해제 |
| 단가 | symbol hash 결정론 | hash fallback + 외부 `set_market_price` |
| 용도 | collector source + ExchangeAdapter contract 검증 | OrderGateway drop-in + 주문 라이프사이클 시뮬 |
| 시그니처 | `place_order(OrderRequest \| dict) → OrderResult` | `place_order(dict) → dict` (PaperBroker 호환) |

둘 다 외부 네트워크 호출 없음 + LIVE 거부 + secret 미보관.

## 3. 데이터 구조

### 3.1 `MockBrokerConfig` (frozen dataclass)

| 필드 | 기본 | 비고 |
|---|---|---|
| `base_currency` | `"USDT"` | quote 통화 |
| `fee_bps` | `5.0` | basis point (0.05%) |
| `slippage_bps` | `0.0` | bp, BUY 위로 / SELL 아래로 |
| `allow_short` | `False` | True 면 base 잔고 0 에서도 SELL 허용 (숏 진입) |
| `allow_margin` | `False` | True 면 quote 잔고 초과 BUY 허용 |
| `partial_fill_enabled` | `False` | (현재 단계에서는 사용 안 함, 후속 확장) |
| `deterministic_seed` | `0` | random 미사용이므로 현재 단계에서는 정보용 |
| `max_order_notional` | `0.0` | 0 = 무제한 |
| `mode` | `"MOCK"` | `MOCK` / `PAPER` 만 허용. `LIVE` 면 `ValueError` |
| `initial_balances` | `{}` | `{"USDT": 10_000.0, "BTC": 0.0}` 형식 |

### 3.2 `MockAccountState`

- 자산별 `free` + `locked` 분리. JSON-safe dict snapshot.
- `lock(ccy, amount)` — open LIMIT 주문 진입 시 free→locked 이동.
- `unlock(ccy, amount)` — cancel 시 locked→free.
- `settle_buy(base, base_amt, quote, quote_used)` — 체결 정산.
- `settle_sell(base, base_amt, quote, quote_received, *, allow_negative_base)` —
  체결 정산. `allow_negative_base=True` 면 base 잔고가 음수로 가도록 허용 (숏
  진입 모델링).

### 3.3 `MockPositionBook`

- symbol → `_Position(qty, avg_entry_price, realized_pnl)`.
- `on_buy(symbol, qty, price)` — 가중평균 avg_entry_price 갱신, 숏 청산 시 PnL.
- `on_sell(symbol, qty, price)` — 롱 청산 PnL 누적, 잔여 qty 음수면 숏 진입.
- `unrealized_pnl(symbol, mark_price)` — 롱/숏 모두 안전.

### 3.4 `MockMarket`

- `set(symbol, price)` / `get(symbol)`. 가격이 설정되지 않으면 symbol hash 기반
  결정론 fallback (`1000 + (hash % 100_000)`).

### 3.5 `MockExecutionEngine`

- `calc_fill(side, ref_price) → (fill_price, fee_rate, slippage_pct)`.
- random 미사용 — 동일 입력 → 동일 출력.

## 4. 주문 → 체결 → 잔고 → 포지션 흐름

### 4.1 MARKET BUY

```
초기  USDT free=10_000 / BTC free=0 / position BTC-USDT qty=0
입력  BTC-USDT BUY MARKET notional=100 (price 50_000)
실행  qty = 100 / 50_000 = 0.002
      fee = 100 * 0.0005 = 0.05  (5 bps)
결과  status=FILLED, filled_price=50_000, fee_usdt=0.05
잔고  USDT free=10_000 - 100 - 0.05 = 9_899.95
      BTC  free=0 + 0.002 = 0.002
포지션  qty=0.002, avg_entry_price=50_000, realized_pnl=0
```

### 4.2 MARKET SELL (부분 청산)

```
초기  BTC free=0.002, position qty=0.002 @ 50_000, market 55_000
입력  BTC-USDT SELL MARKET qty=0.001
실행  fee = 55 * 0.0005 = 0.0275
결과  status=FILLED, filled_price=55_000
잔고  BTC free=0.001, USDT free += (55 - 0.0275)
포지션  qty=0.001 (남음), avg_entry_price=50_000 (유지), realized_pnl=+5.0
```

### 4.3 LIMIT BUY (체결 안 됨 → open + cancel)

```
입력  BTC-USDT BUY LIMIT notional=100 price=49_000 (market 50_000)
조건  market > limit → non-crossable → open
실행  fee_reserve = 100 * 0.0005 = 0.05
잔고  USDT locked += 100.05, USDT free -= 100.05
결과  status=ACCEPTED, route=paper, reason="mock open (LIMIT, not crossable)"

cancel(order_id) →
  status=ACCEPTED, USDT locked → free 복원
```

### 4.4 가격 자동 갱신

MARKET 체결 시 `MockMarket.set(symbol, fill_price)` 호출 — 다음 주문의 시장가
기준이 갱신된다. 외부에서 직접 호출하려면 `broker.set_market_price(symbol, price)`.

## 5. 수수료 / 슬리피지 / PnL

### 5.1 수수료

`fee_bps` (basis point). BUY 는 quote 차감에 추가, SELL 은 quote 수익에서 차감.

```
fee = notional * (fee_bps / 10_000)
```

### 5.2 슬리피지

`slippage_bps` (basis point). 결정론 — random 사용 없음.

```
direction = +1 if BUY else -1
fill_price = ref_price * (1 + slippage_bps/10_000 * direction)
```

### 5.3 realized PnL (롱 청산)

```
realized_delta = (exit_price - avg_entry_price) * sell_qty
```

### 5.4 realized PnL (숏 청산)

`on_buy` 가 short 청산을 자동 감지해 `(avg_entry - exit) * cover_qty` 를 PnL 누적.

### 5.5 unrealized PnL

```
long  : (mark_price - avg_entry) * qty
short : (avg_entry - mark_price) * |qty|
```

## 6. OrderGateway 연결

`MockBroker.place_order(dict) → dict` 시그니처가 기존 `PaperBroker.place_order`
와 호환. OrderGateway 의 paper 경로에서 `PaperBroker` 대신 drop-in 사용 가능.

응답 dict 에는 `PaperBroker` 가 반환하는 필드(`order_id`, `status`, `symbol`,
`side`, `notional_usdt`, `filled_price`, `fee_usdt`, `slippage_pct`) 모두 포함 +
`mode`, `is_real_trade`, `execution_source`, `warning`, `qty`, `audit` 추가.

본 단계에서는 OrderGateway 의 기본 paper broker 를 `PaperBroker` 그대로 유지 —
`MockBroker` 는 명시적 선택 시(테스트 / config) 사용. 단일 주문 경로(Strategy →
Agent → Risk → Guard → Gate → Queue → Gateway → Executor) 는 손대지 않는다.

### 6.1 단일 주문 경로 강제 메커니즘

| 검증 | 회귀 테스트 |
|---|---|
| Strategy 가 `mock_simulation` import 안 함 | `test_strategies_do_not_import_mock_broker` |
| Agent 가 `mock_simulation` import 안 함 (compliance.py 예외) | `test_agents_do_not_import_mock_broker` |
| Strategy 가 `MockBroker()` 인스턴스화 안 함 | `test_strategies_no_mock_broker_instantiation` |
| Agent 가 `MockBroker()` 인스턴스화 안 함 | `test_agents_no_mock_broker_instantiation` |

## 7. API / UI 표시 원칙

API 응답에 `MockBroker` 결과가 들어가면 다음 4개 필드가 항상 포함된다.

```json
{
  "mode": "MOCK",                                          // 또는 "PAPER"
  "is_real_trade": false,
  "execution_source": "mock_broker",
  "warning": "Mock execution only. Not real profit or real trade."
}
```

frontend 표시는 본 단계 범위가 아니지만, 향후 UI 에 표시 시:
- MOCK / PAPER 배지 노출
- "실제 수익 아님" 문구 노출
- 실거래 시작/중단 UI 와 시각적으로 분리

API/frontend 어디에도 API key/secret/token 을 저장하지 않는다 (정적 회귀로 강제).

## 8. 안전 정책

### 8.1 LIVE mode 거부

- `MockBrokerConfig(mode="LIVE")` 생성 → `ValueError`.
- `place_order(dict)` 의 `dict["mode"]` 또는 `dict["trading_mode"]` 가 `"LIVE"`
  면 `REJECTED` (`route="live_not_wired"`).

### 8.2 audit secret sanitize

`OrderResult.audit` blob 에서 다음 키 자동 제거:

`api_key`, `api_secret`, `secret`, `access_token`, `token`, `passphrase`,
`password`, `private_key`, `ok_access_key`, `ok_access_sign`,
`ok_access_passphrase`, `x_mbx_apikey`.

### 8.3 외부 네트워크 / SDK 부재

`mock_simulation.py` 는 다음을 import 하지 않는다 — 정적 회귀로 강제.

`requests`, `httpx`, `ccxt`, `pyupbit`, `binance`, `binance_connector`, `okx`.

### 8.4 입력 검증

- `side ∈ {BUY, SELL}` 외 → REJECTED
- `order_type ∈ {MARKET, LIMIT}` 외 → REJECTED
- LIMIT + price≤0 → REJECTED
- notional_usdt + qty 둘 다 0 → REJECTED
- symbol 분리 실패 (알려진 quote 후미 없음 + dash/slash 없음) → REJECTED
- `max_order_notional > 0` 초과 → REJECTED
- `allow_short=False` + base 잔고 미달 SELL → REJECTED (`insufficient_base_balance`)
- `allow_margin=False` + quote 잔고 미달 BUY → REJECTED (`insufficient_balance`)
- duplicate `client_order_id` / `idempotency_key` → 첫 결과 그대로 반환 (idempotent)

## 9. 회귀 테스트

`backend/tests/test_mock_broker.py` — **51 케이스**. 분류:

1. **초기 잔고 / get_balance** (2)
2. **MARKET BUY** — FILLED, 잔고 갱신, 포지션 가중평균 (3)
3. **MARKET SELL** — 전량 / 부분 청산 + realized PnL (2)
4. **LIMIT 주문** — crossable 즉시 / non-crossable open / no-price 거부 (5)
5. **cancel_order** — open / unknown / by client_id / FILLED 거부 (4)
6. **수수료 / 슬리피지** — fee_bps, BUY 위 / SELL 아래 (3)
7. **PnL** — unrealized long, flat (2)
8. **max_order_notional 한도** (1)
9. **allow_short / allow_margin** — 3가지 케이스 (3)
10. **duplicate client_order_id / idempotency_key** (2)
11. **LIVE mode 거부** — request mode/trading_mode, config (3)
12. **invalid input** — side, order_type, zero notional, symbol (4)
13. **응답 필드** — mode, is_real_trade, warning, paper mode (3)
14. **audit secret sanitize** (1)
15. **reset / account summary** (2)
16. **MockAccountState / MockPositionBook 단위** (3)
17. **정적 회귀** — 네트워크 import 부재, 금지 문자열 부재, strategies/agents
    직접 호출 부재, brokers __all__ exports, PaperBroker 시그니처 호환 (8)

```
cd backend
python -m pytest tests/test_mock_broker.py -q
```

## 10. 사용 예 (테스트 작성용)

```python
from app.brokers import MockBroker, MockBrokerConfig

cfg = MockBrokerConfig(
    base_currency="USDT",
    fee_bps=5.0,
    initial_balances={"USDT": 10_000.0},
    mode="MOCK",
)
b = MockBroker(cfg)
b.set_market_price("BTC-USDT", 50_000.0)

# MARKET BUY
r = b.place_order({
    "symbol": "BTC-USDT", "side": "BUY",
    "order_type": "MARKET", "notional_usdt": 100,
    "client_order_id": "test-1",
})
assert r["status"] == "FILLED"
assert r["is_real_trade"] is False
assert b.get_balance("BTC")["free"] == 0.002
assert b.get_position("BTC-USDT")["avg_entry_price"] == 50_000

# 가격 상승 → unrealized PnL
b.set_market_price("BTC-USDT", 60_000)
assert b.get_position("BTC-USDT")["unrealized_pnl"] == 20.0

# 전량 청산 → realized PnL
r2 = b.place_order({
    "symbol": "BTC-USDT", "side": "SELL",
    "order_type": "MARKET", "qty": 0.002,
})
assert b.get_position("BTC-USDT")["realized_pnl"] == 20.0
```

## 11. `MockBroker` vs `PaperMarketBroker` (#24 vs #25)

| | `MockBroker` (`mock_simulation.py`) | `PaperMarketBroker` (`paper_market_broker.py`) |
|---|---|---|
| 가격 source | 결정론 hash + `set_market_price` | read-only adapter `fetch_ticker(symbol)` |
| Slippage | 결정론 (bp) | 결정론 (bp) |
| universe / staleness | (없음) | universe whitelist + max_ticker_age_sec |
| LIMIT book | open book + cancel | open book + cancel |
| 외부 호출 | 없음 | 없음 (source 가 read-only) |
| 용도 | CI 단위 테스트 (결정론) | 실시간 환경 검증 (paper mode) |

`PaperTrader` (`paper_trader.py`) 는 `PaperMarketBroker` 의 상위 컨트롤러로,
source 선택과 OrderGateway 경유 강제 정책을 제공한다. 자세한 내용은
`docs/paper_broker.md`.

## 12. 후속 단계와의 관계

- 25번 Paper Broker — 별도 doc(`docs/paper_broker.md`). `MockBroker` 와는 가격
  source 출처가 다르고 universe/staleness 가드를 추가.
- 26번 Rate Limit Guard — Mock 브로커는 외부 호출이 없어 rate limit 미적용.
- LIVE 주문 adapter — 본 작업 범위 밖. 별도 LIVE class + OrderGateway 끝단 호출
  + 별도 환경변수 + 별도 승인 절차 통과 후.
- 본 단계 완료는 실거래 허가가 아니다 (CLAUDE.md §2.6).
