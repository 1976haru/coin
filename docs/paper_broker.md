# Paper Broker (체크리스트 #25)

> Agent Trader Crypto OS v1 — read-only 시세 + 가상 주문 체결 (실시간 환경 검증용)

## 0. 한 줄 요약

`PaperMarketBroker` (`backend/app/brokers/paper_market_broker.py`) + `PaperTrader`
(`backend/app/brokers/paper_trader.py`) 는 **read-only 시세 source** 와 **가상
주문 체결**을 연결한 paper-trading 컴포넌트다. 실제 거래소 주문 API 는 호출하지
않으며, 모든 결과에 `is_real_trade=False`, `mode="PAPER"`,
`warning="Paper execution only. Not real profit or real trade."`,
`fill_quality_warning="Paper fills may differ from live execution..."` 가 포함된다.

## 1. 목적과 범위

본 단계는 다음을 한다.

- `PaperMarketBroker` — read-only `fetch_ticker(symbol)` source 의 현재가를 사용해
  paper(가상) 주문을 체결.
- `PaperTrader` — paper mode 컨트롤러. source 선택 (mock / Upbit / OKX / Binance /
  KIS stub), start/stop/reset, OrderGateway 경유 주문 제출, 상태/로그 노출.
- REST API — 상태/로그 조회 + admin gated 컨트롤 (`/api/paper/*`).
- 외부 네트워크 호출 0 — paper 모듈 자체는 `requests` / `httpx` / `ccxt` /
  `pyupbit` / `binance` / `okx` SDK 어느 것도 import 하지 않는다 (정적 회귀).

본 단계는 다음을 **하지 않는다**.

- 실제 거래소 주문 API 호출 ❌
- 실제 Upbit/OKX/Binance/KIS 주문 호출 ❌
- 실제 잔고/계좌/포지션 저장 ❌
- LIVE mode 주문 처리 ❌ (요청 거부 + Trader 사전 차단)
- frontend 에 API key/secret/token 저장 ❌
- Strategy/Agent 가 paper 모듈을 직접 호출 ❌ (모듈 경계 + 정적 회귀)

## 2. `PaperBroker` / `MockBroker` / `PaperMarketBroker` 관계

| | `PaperBroker` (`paper_broker.py`) | `MockBroker` (`mock_simulation.py`) | `PaperMarketBroker` (`paper_market_broker.py`) |
|---|---|---|---|
| 가격 source | order dict 의 `price` | 결정론 hash + `set_market_price` | read-only adapter `fetch_ticker(symbol)` |
| Slippage | 랜덤 (`uniform`) | 결정론 (bp) | 결정론 (bp) |
| Fill chance | 랜덤 (`fill_chance`) | 항상 FILLED (MARKET) | 항상 FILLED (MARKET) |
| 다중 자산 | 단일 USDT | 다중 (free + locked) | 다중 (free + locked) |
| 포지션 | (없음) | qty + avg_entry + PnL | qty + avg_entry + PnL |
| LIMIT book | (없음) | open book + cancel | open book + cancel |
| universe / staleness | (없음) | (없음) | universe whitelist + max_ticker_age_sec |
| 용도 | OrderGateway 기본 paper executor | CI 결정론 단위 테스트 | 실시간 환경 검증 (paper mode) |
| 외부 호출 | 없음 (random 만) | 없음 | 없음 (source 가 read-only) |

셋 다 LIVE 거부 + secret 미보관 + `is_real_trade=False`.

## 3. PaperMarketBroker

### 3.1 `PaperMarketBrokerConfig` (frozen dataclass)

| 필드 | 기본 | 비고 |
|---|---|---|
| `base_currency` | `"USDT"` | quote 통화 |
| `fee_bps` | `5.0` | basis point (0.05%) |
| `slippage_bps` | `0.0` | bp, BUY 위로 / SELL 아래로 |
| `allow_short` | `False` | True 면 base 잔고 0 에서도 SELL 허용 |
| `allow_margin` | `False` | True 면 quote 잔고 초과 BUY 허용 |
| `max_order_notional` | `0.0` | 0 = 무제한 (브로커 단계) |
| `require_source` | `True` | True + source None → 모든 주문 REJECTED |
| `universe` | `None` | tuple — None 이면 전체 허용 |
| `max_ticker_age_sec` | `30.0` | stale ticker BUY 차단 (#16 정책) |
| `initial_balances` | `None` | `{"USDT": 10_000.0}` 형식 |

### 3.2 `PaperMarketSource` Protocol

```python
class PaperMarketSource(Protocol):
    name: str

    def fetch_ticker(self, symbol: str) -> Ticker | None: ...
```

`MockExchangeAdapter` / `UpbitAdapter` / `OkxAdapter` / `BinanceAdapter` 모두 본
Protocol 을 만족 — 그대로 주입 가능.

### 3.3 공개 메서드

| 메서드 | 결과 |
|---|---|
| `place_order(dict) → dict` | MARKET 즉시 FILLED, LIMIT crossable 즉시 / non-crossable open |
| `cancel_order(id_or_client_id) → dict` | open 만 취소 + locked balance 해제 |
| `get_balance(ccy)` / `get_position(symbol)` | 조회 read-only |
| `get_account_summary()` | balances + positions + open_orders + filled_count + source 정보 |
| `reset()` | 잔고/포지션/로그 초기화 |

### 3.4 안전 가드

- **universe whitelist (BUY 만 차단)** — universe 밖 symbol BUY → REJECTED
  (`reason="symbol not in paper universe — candidate_filter_review_required"`).
  EXIT(SELL) 은 위험 축소로 허용.
- **staleness (BUY 만 차단)** — ticker.ts 가 `max_ticker_age_sec` 보다 오래되면
  BUY REJECTED. EXIT 는 허용 (#16 freshness 정책 그대로).
- **LIVE 거부** — `mode="LIVE"` 또는 `trading_mode="LIVE"` 요청 → REJECTED
  (`route="live_not_wired"`).
- **잔고 가드** — `allow_short=False` 보유 초과 SELL / `allow_margin=False` quote
  초과 BUY → REJECTED.
- **idempotent** — `client_order_id` 또는 `idempotency_key` 중복 → 첫 결과 그대로.
- **audit secret sanitize** — `api_key`/`api_secret`/`passphrase`/`ok_access_sign`/
  `x_mbx_apikey` 등 자동 제거.

## 4. PaperTrader

### 4.1 source 카탈로그

```python
AVAILABLE_PAPER_SOURCES = (
    "mock",
    "upbit_readonly",
    "okx_readonly",
    "binance_readonly",
    "kis_readonly_stub",   # KIS adapter 미구현 — disabled stub
)
```

- `mock` 만 기본 factory 로 자동 빌드 (`MockExchangeAdapter`). 다른 거래소는 명시적
  주입(`source_factory=...`) 필요 — 임의로 네트워크 호출 가능한 source 를 생성하지
  않는다.
- `kis_readonly_stub` 선택 시 source=None + `PaperStatus.warnings` 에
  `"kis_readonly_stub: KIS adapter is not implemented in this phase"` 추가. 모든
  주문이 require_source=True 라 REJECTED.

### 4.2 공개 메서드

| 메서드 | 동작 |
|---|---|
| `select_paper_source(name)` | source 변경 (unknown → `PaperTraderError`) |
| `start_paper()` / `stop_paper()` / `reset_paper()` | PaperStatus 전이 + 로그 초기화 |
| `submit_paper_order_via_gateway(request, gateway)` | gateway.submit(request) 호출 + 로그 적재 + envelope 보강 |
| `get_paper_status() → dict` | 상태 + envelope + available_sources |
| `get_paper_logs(limit, client_order_id)` | 최근 N개 로그 (client_order_id 필터) |

### 4.3 OrderGateway 경유 강제

`submit_paper_order_via_gateway(request, gateway)` 는 broker 를 직접 호출하지
않는다 — `gateway.submit(request)` 위임만. gateway 는 Risk/Guard/Permission/
Approval/Freshness 를 통과시킨 후 paper executor → broker 로 전달한다.

```text
PaperTrader.submit_paper_order_via_gateway
   ↓
OrderGateway.submit  (risk → guard → gate → approval → freshness)
   ↓
PaperExecutor / PaperBroker  (또는 PaperMarketBroker)
```

- `running=False` 면 `PaperTraderError`.
- request 에 `mode="LIVE"` / `trading_mode="LIVE"` 가 있으면 gateway 도달 *전에*
  `PaperTraderError` — 정책 강화.
- gateway 가 `.submit()` 메서드를 노출하지 않으면 `PaperTraderError`.

### 4.4 `PaperStatus` 필드

```python
{
  "running": bool,
  "source_name": str,
  "started_at": iso | None,
  "stopped_at": iso | None,
  "last_order_at": iso | None,
  "last_market_at": iso | None,
  "orders_submitted": int,
  "orders_filled": int,
  "orders_rejected": int,
  "orders_canceled": int,
  "warnings": list[str],
  # envelope
  "mode": "PAPER",
  "is_real_trade": False,
  "execution_source": "paper_trader",
  "warning": "Paper execution only. Not real profit or real trade.",
  "fill_quality_warning": "Paper fills may differ from live execution...",
  "available_sources": [...]
}
```

## 5. REST API

| Method | Path | 권한 | 동작 |
|---|---|---|---|
| GET | `/api/paper/status` | public | PaperTrader 상태 + envelope |
| GET | `/api/paper/orders?limit=&client_order_id=` | public | paper order logs |
| GET | `/api/paper/sources` | public | 사용 가능한 source 목록 |
| POST | `/api/paper/start` | admin | paper mode 시작 |
| POST | `/api/paper/stop` | admin | paper mode 중지 |
| POST | `/api/paper/reset` | admin | paper state 초기화 |
| POST | `/api/paper/source` | admin | `{name: "mock"}` 으로 source 변경 |

**별도 paper submit endpoint 는 만들지 않는다.** paper 주문 송신은 기존
OrderGateway 단일 경로 (`/api/order/...`) 로만 — Strategy/Agent 우회 방지.

응답에는 항상 `is_real_trade=false`, `mode="PAPER"`, warning 두 개 포함.

## 6. 안전 정책 요약

- 본 단계 완료는 실거래 허가가 아니다 (CLAUDE.md §2.6).
- `place_order` 메서드명은 등장하지만 실제 거래소 주문 endpoint 는 호출하지 않는다.
- Paper 결과를 실제 수익으로 표시하지 않는다 — 모든 응답에 `is_real_trade=False`
  + `warning` 두 개 강제.
- Strategy/Agent 가 paper 모듈을 직접 호출/instantiate 하지 않는다 (compliance.py
  meta-checker 만 예외).
- paper 모듈 (`paper_market_broker.py` + `paper_trader.py`) 은 외부 네트워크
  라이브러리/SDK 를 import 하지 않는다.
- 출금/이체 메서드 부재 (영구).
- API key/secret/token 은 frontend 어디에도 노출되지 않는다.

## 7. 회귀 테스트

`backend/tests/test_paper_broker.py` — **46 케이스**. 분류:

1. **source 없음 거부** (1)
2. **MARKET BUY/SELL** + 잔고/포지션 (2)
3. **LIMIT 4종** — crossable / non-crossable / cancel (2)
4. **universe whitelist** — BUY 차단 / SELL 허용 (2)
5. **staleness** — BUY 차단 / SELL 허용 (2)
6. **fee / slippage** (2)
7. **allow_short / allow_margin** (2)
8. **duplicate client_order_id** (1)
9. **LIVE 거부** — mode / trading_mode (2)
10. **envelope 필드** — FILLED / REJECTED 둘 다 (2)
11. **audit secret sanitize** (1)
12. **account_summary** (1)
13. **PaperTrader source 선택** — unknown 거부 / KIS warning / 다른 source 로 전환 시 KIS warning 제거 / catalog (4)
14. **PaperTrader start/stop/reset** (1)
15. **submit_via_gateway** — 위임/로그/envelope/running=false/LIVE 사전 차단/no-submit gateway (4)
16. **logs 필터** (1)
17. **REST API** — status/sources/start admin gating/start with admin/source 변경/unknown source/orders empty/reset (7)
18. **정적 회귀** — paper 모듈 네트워크 import 부재 / 금지 문자열 부재 / strategies·agents 직접 import 부재 / 인스턴스화 부재 / brokers __all__ exports / MockExchangeAdapter 호환 (8)

```
cd backend
python -m pytest tests/test_paper_broker.py -q
```

## 8. 사용 예

```python
from app.brokers import (
    PaperMarketBroker, PaperMarketBrokerConfig,
    PaperTrader,
    make_paper_universe,
)
from app.brokers.mock_broker import MockExchangeAdapter

# 1) PaperMarketBroker 직접 사용 (테스트)
src = MockExchangeAdapter("paper_mock")
broker = PaperMarketBroker(
    source=src,
    config=PaperMarketBrokerConfig(
        fee_bps=5.0,
        universe=make_paper_universe(["BTC-USDT", "ETH-USDT"]),
        max_ticker_age_sec=30.0,
        initial_balances={"USDT": 10_000.0},
    ),
)
r = broker.place_order({
    "symbol": "BTC-USDT", "side": "BUY",
    "order_type": "MARKET", "notional_usdt": 100,
    "client_order_id": "test-1",
})
assert r["mode"] == "PAPER" and r["is_real_trade"] is False

# 2) PaperTrader + OrderGateway 경유 (운영)
trader = PaperTrader(
    default_source_name="mock",
    broker_config=PaperMarketBrokerConfig(
        fee_bps=5.0, initial_balances={"USDT": 10_000.0},
    ),
)
trader.start_paper()
result = trader.submit_paper_order_via_gateway(
    request={...},
    gateway=app_gateway,   # OrderGateway.submit(request) 노출
)
assert result["is_real_trade"] is False
```

## 9. 후속 단계와의 관계

- 26번 Rate Limit Guard — Paper broker 는 외부 호출이 없어 rate limit 미적용.
- LIVE 주문 adapter — 본 작업 범위 밖. 별도 LIVE class + OrderGateway 끝단 호출 +
  별도 환경변수 + 별도 승인 절차 통과 후.
- KIS adapter — 본 단계에서 `kis_readonly_stub` 으로 자리만 잡음. 실제 구현은
  별도 phase.
- 본 단계 완료는 실거래 허가가 아니다 (CLAUDE.md §2.6).
