# TrendFollowingContractStrategy (체크리스트 #30)

> Agent Trader Crypto OS v1 — EMA/SMA/Donchian/ADX 기반 추세추종 전략 (Signal only)

## 0. 한 줄 요약

`TrendFollowingContractStrategy` 는 EMA fast/slow + SMA(200) trend filter + Donchian
breakout + ADX trend strength 를 결합한 **추세 후보 판단 전략**이다. `BUY`/`SELL`
은 *전략 판단 표현* — **실제 주문 명령이 아니다**. 모든 신호의 `is_order_intent`
는 영구 `False`. **본 단계 완료는 실거래 허가가 아니다** (CLAUDE.md §2.6).

기존 `TrendFollowingStrategy` (Protocol 기반, `trend_following.py`) 는 그대로 유지
— 본 신규 클래스는 #29 `StrategyContract` ABC 를 구현하는 parallel layer.

## 1. 전략 목적

코인 장기 추세 구간을 포착해 후보 진입/청산 판단을 *제안*한다. 실제 주문 전환은
별도 단계의 책임 (Strategy → Agent → RiskManager → OrderGuard → PermissionGate →
ApprovalQueue → OrderGateway).

설계 원칙 (CLAUDE.md §2.3 / §2.4):
- 전략은 broker / adapter / OrderGateway / MockBroker / PaperBroker 를 직접 호출
  하지 않는다 (정적 회귀로 강제).
- 전략은 `.place_order(` / `.cancel_order(` / `.get_balance(` / `.submit_order(`
  를 호출하지 않는다.
- 신호의 `is_order_intent`, sizing 의 `is_final_order_size`, exit 의
  `is_order_intent` 모두 영구 False.

## 2. 지표 카탈로그 (`_indicators.py`)

| 함수 | 시그니처 | 정의 |
|---|---|---|
| `ema(prices, period)` | float | 단순 EMA, 마지막 값 |
| `sma(prices, period)` | float | 표본 부족 시 가용 표본 평균 |
| `atr(highs, lows, closes, period=14)` | float | Average True Range |
| `true_range(highs, lows, closes)` | list[float] | 봉별 TR (`len = n-1`) |
| `donchian_channel(highs, lows, period=20, exclude_current=True)` | (high, low) | 직전 N봉 max/min |
| `adx(highs, lows, closes, period=14)` | float | Wilder 평활 기반 ADX |

ADX 알고리즘 (문서화 — 트레이딩뷰와 완전 동일하지 않으나 결정론적):
1. `TR_i = max(high_i-low_i, |high_i-close_{i-1}|, |low_i-close_{i-1}|)`
2. `+DM_i = max(0, high_i-high_{i-1})` 가 `max(0, low_{i-1}-low_i)` 보다 클 때만, 아니면 0. `-DM_i` 대칭.
3. Wilder smoothing — TR, +DM, -DM 각각 `period` 길이로.
4. `+DI = 100 × (+DM_smooth / TR_smooth)`, `-DI = 100 × (-DM_smooth / TR_smooth)`
5. `DX = 100 × |+DI - -DI| / (+DI + -DI)`
6. ADX = 마지막 DX 값 (Wilder 의 second pass 는 본 단계 단순화 — 추가 표본 필요 시 보강).

데이터 부족 (n < period+1) 또는 길이 불일치 → `0.0` (안전 기본값).

## 3. 파라미터

```python
@dataclass(frozen=True)
class TrendFollowingParams:
    ema_fast: int = 20
    ema_slow: int = 60
    trend_sma_period: int = 200
    donchian_period: int = 20
    adx_period: int = 14
    adx_min: float = 18.0                    # 횡보장 자동 비활성 임계
    breakout_buffer_pct: float = 0.0
    min_candles_required: int = 0            # 0 = ema_slow + 5
    allow_short_candidates: bool = False     # SHORT 옵트인
    base_notional_usdt: float = 100.0
    max_leverage_hint: float = 1.0
```

## 4. generate_signal 조건표

| 조건 | 결과 |
|---|---|
| `data_quality_grade == "EXCLUDE"` | `BLOCKED` |
| `freshness_ok == False` | `BLOCKED` |
| `is_in_universe == False` | `BLOCKED` |
| `notice_context.high_risk_symbols` 에 본 symbol/base 포함 | `BLOCKED` |
| 캔들 부족 (< `min_candles_required`) | `NO_ACTION` |
| OHLC 길이 불일치 | `NO_ACTION` |
| `ADX < adx_min` (약한 추세) | `HOLD` |
| `close > SMA(200)` + `EMA(fast) > EMA(slow)` + `close >= Donchian high × (1 + buffer)` + `ADX >= adx_min` | **`BUY` candidate** |
| 위 SHORT 미러 + `allow_short_candidates=True` | **`SELL` candidate** |
| 위 조건 미충족 | `HOLD` |

confidence 환산: `0.5 + min(0.4, (adx - adx_min) / 100)` → 0.5~0.9 범위.
quality_score: `min(100, max(0, adx × 2))` → SignalQualityAgent 입력.

stop/take_profit:
- LONG: `stop_loss = close - 1.5×ATR`, `take_profit = close + 3×ATR`
- SHORT: 대칭

**`BUY`/`SELL` 은 후보 표시. 실제 주문 명령 아님.**

## 5. calculate_size hint 정책

```python
suggested_notional_usdt = base_notional_usdt × confidence    # BUY/SELL 시
                        = 0                                  # HOLD/BLOCKED/NO_ACTION 시
```

- `is_final_order_size = False` 영구 — 최종 수량 아님.
- `used_for_order = False` 영구.
- 최종 수량은 RiskManager + OrderGuard 가 결정 (max_order_notional / 일일손실 한도 / 포지션 한도 등).

## 6. exit_rule 정책

| 조건 | should_exit | fraction | urgency |
|---|---|---|---|
| `data_quality_grade == "EXCLUDE"` | True | 1.0 | critical |
| `freshness_ok == False` | True | 1.0 | high |
| `notice_context.high_risk_symbols` 매칭 | True | 1.0 | high |
| `fast EMA < slow EMA` (cross-below) | True | 0.6 | normal |
| `close < Donchian low` (직전 N봉) | True | 1.0 | high |
| `ADX < adx_min × 0.7` (추세 약화) | True | 0.3 | normal |
| 데이터 부족 / OHLC 길이 불일치 | False | 0 | — |
| 정상 추세 유지 | False | 0 | — |

`is_order_intent = False` 영구 — 실제 청산 주문 아님. 후속 risk/order pipeline 이 검토.

## 7. explain_signal 출력

`SignalExplanation` 필드:
- **summary**: "candidate_long: EMA(20)>60, Donchian breakout, ADX>=adx_min — **candidate only, not an order**"
- **reasons**: 신호 생성 시 reason 문자열
- **evidence**:
  - `EMA(20)=…, EMA(60)=…`
  - `SMA(200)=…`
  - `ADX(14)=…`
  - `Donchian(20) high=…, low=…, current=…`
  - `data_quality_grade=GOOD/WARNING/EXCLUDE`
  - `freshness_ok=True/False`
  - `is_in_universe=True/False`
  - `regime=TREND_UP/…`
- **risks**: stale data / EXCLUDE / high-risk notice / theme review_required
- **limitations**:
  - "candidate only — not an order"
  - "RiskManager / OrderGuard 가 최종 수량 결정"
  - "BUY/SELL action 은 전략 판단 표현이며 직접 주문 명령이 아님"

설명 어디에도 "지금 매수해라", "즉시 주문", "자동 매수" 같은 직접 주문 지시가
들어가지 않는다.

## 8. StrategyContract ABC 만족

```python
class TrendFollowingContractStrategy(StrategyContract):
    capability = StrategyCapability(
        name="trend_following_v2",
        description="EMA fast/slow + SMA(200) + Donchian breakout + ADX",
        required_inputs=("closes", "highs", "lows"),
        signal_actions=("BUY", "SELL", "HOLD", "BLOCKED", "NO_ACTION", "WATCH_ONLY"),
    )
    enabled_by_default = False
    preferred_regimes = ("TREND_UP", "TREND_DOWN")

    def generate_signal(self, context): ...
    def calculate_size(self, context, signal): ...
    def exit_rule(self, context, signal): ...
    def explain_signal(self, context, signal): ...
```

`evaluate(context)` 가 4단계를 한 번에 호출하고 contract 위반 시 raise (#29).

## 9. StrategyRegistry / StrategySelectionAgent 연동

```python
from app.strategies.contract_registry import build_empty_registry
from app.strategies.trend_following_contract import TrendFollowingContractStrategy
from app.agents.strategy_selection import (
    StrategyActivationContext, select_active_strategies,
)

reg = build_empty_registry()
reg.register_strategy(TrendFollowingContractStrategy, enabled=False)
# enabled_by_default=False — 운영자가 명시적으로 enable

ctx = StrategyActivationContext(symbol="BTC-USDT", regime="TREND_UP")
decision = select_active_strategies(ctx, reg)
assert "trend_following_v2" in decision.activated
assert decision.direct_order_allowed is False    # 영구 False
```

`preferred_regimes = ("TREND_UP", "TREND_DOWN")` — `StrategySelectionAgent` 가 trending
regime 일 때 후보로 반환. `RANGE` regime 에서는 자동 skipped.

## 10. 사용 예 (테스트 / 백테스트)

```python
from app.strategies.trend_following_contract import (
    TrendFollowingContractStrategy, TrendFollowingParams,
)
from app.strategies.contract import StrategyContext

s = TrendFollowingContractStrategy(
    TrendFollowingParams(
        ema_fast=20, ema_slow=60,
        trend_sma_period=200, donchian_period=20,
        adx_period=14, adx_min=18.0,
        allow_short_candidates=False,
        base_notional_usdt=100.0,
    )
)

ctx = StrategyContext(
    symbol="BTC-USDT", timeframe="1h",
    closes=tuple(close_prices),
    highs=tuple(high_prices),
    lows=tuple(low_prices),
    freshness_ok=True,
    data_quality_grade="GOOD",
    is_in_universe=True,
    notice_context=notice_ctx_dict,    # #18
    theme_context=theme_ctx_dict,      # #19
    regime="TREND_UP",                 # #19
)

result = s.evaluate(ctx)
# result["signal"] = StrategySignal(...)
# result["sizing"] = PositionSizingHint(...)
# result["exit"]   = ExitRuleDecision(...)
# result["explanation"] = SignalExplanation(...)
# result["is_order_intent"] == False — 영구
# result["direct_order_allowed"] == False — 영구
```

## 11. 안전 가드 (재확인)

코드 레벨:
- `evaluate()` 가 신호 `is_order_intent=True` / 사이징 `is_final_order_size=True` /
  exit `is_order_intent=True` 반환 시 `StrategyContractError` raise.
- `StrategyContext` 생성 시 `extra` dict 에 secret 류 키 감지 시 raise.

정적 회귀 (`tests/test_trend_following_contract.py`):
- `trend_following_contract.py` 에 `app.brokers` / `app.execution` import 부재.
- `requests` / `httpx` / `ccxt` / `pyupbit` / `binance` / `okx` SDK import 부재.
- `.place_order(` / `.cancel_order(` / `.get_balance(` / `.submit_order(` 호출 부재.
- `ENABLE_LIVE_TRADING = True` / `is_order_intent: bool = True` /
  `is_final_order_size: bool = True` / `used_for_order=True` literal 부재.

## 12. 회귀 테스트

`backend/tests/test_trend_following_contract.py` — **45 케이스**. 분류:

1. **Indicators** (10) — SMA / EMA / ATR / true_range / Donchian / ADX
2. **Signal generation** (10) — strong uptrend BUY / weak trend HOLD / quality
   EXCLUDE / freshness stale / universe out / high-risk notice / insufficient
   candles / OHLC mismatch / short disabled / short enabled
3. **Sizing** (3) — zero for non-actionable / proportional / is_final_order_size=False
4. **Exit rule** (5) — quality EXCLUDE / freshness / notice / EMA cross / 정상
5. **Explanation** (4) — candidate only / evidence / risks / limitations
6. **evaluate()** (2) — is_order_intent=False / 전체 layer
7. **Static guards** (4) — broker import / SDK import / order method calls / forbidden literal
8. **Registry / SelectionAgent integration** (5)

기존 `tests/test_trend_following.py` (#30 1차, 14 케이스) 회귀 없음 — 기존
`TrendFollowingStrategy` 그대로 유지.

```
cd backend
python -m pytest tests/test_trend_following_contract.py -q
python -m pytest tests/test_trend_following.py -q     # 기존 1차 회귀
```

## 13. 후속 단계

- 31번 Volatility Breakout (✅ 2026-05-18 [`docs/volatility_breakout.md`](volatility_breakout.md))
  / 32번 Pair Trading / 33번 Kimp Mean Reversion 도 동일 패턴(`*_contract.py` +
  ABC 구현)으로 추가 가능.
- `StrategySelectionAgent` 본격 구현 — LLM 또는 정교한 휴리스틱.
- 전략 결과 → AgentOrchestrator → RiskManager → OrderGateway 통합은 별도 PR.

본 단계 완료는 실거래 허가가 아니다 (CLAUDE.md §2.6). 31번 이후 전략 구현은
본 작업 범위가 아니다.
