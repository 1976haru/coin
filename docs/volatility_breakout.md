# VolatilityBreakoutContractStrategy (체크리스트 #31)

> Agent Trader Crypto OS v1 — ATR 기반 변동성 돌파 전략 (Signal only)

## 0. 한 줄 요약

`VolatilityBreakoutContractStrategy` 는 **ATR(14) vs ATR(42) 변동성 확장 +
직전 N봉 high/low 돌파 + 거래량 확장 필터(옵션)** 를 결합한 *변동성 돌파 후보
판단 전략*이다. `BUY`/`SELL` 은 *전략 판단 표현* — **실제 주문 명령이 아니다**.
모든 신호의 `is_order_intent` 는 영구 `False`. **본 단계 완료는 실거래 허가가
아니다** (CLAUDE.md §2.6).

기존 `VolatilityBreakoutStrategy` (Protocol 기반, `volatility_breakout.py`) 는
그대로 유지 — 본 신규 클래스는 #29 `StrategyContract` ABC 를 구현하는 parallel
layer.

## 1. 전략 목적

range 압축 후 변동성 확장(expansion) 시점의 돌파를 후보로 식별한다. 추세추종(#30)
이 이미 trending 시장의 초기 ~ 중기 구간을 노린다면, 본 전략은 **range/triangle
형성 후 break 시점**과 **추세 전환 직후 강한 expansion 구간** 을 모두 후보로
삼는다.

실제 주문 전환은 별도 단계의 책임 (Strategy → Agent → RiskManager → OrderGuard
→ PermissionGate → ApprovalQueue → OrderGateway).

설계 원칙 (CLAUDE.md §2.3 / §2.4):
- 전략은 broker / adapter / OrderGateway / MockBroker / PaperBroker 를 직접
  호출하지 않는다 (정적 회귀로 강제).
- 전략은 `.place_order(` / `.cancel_order(` / `.get_balance(` / `.submit_order(`
  를 호출하지 않는다.
- 신호의 `is_order_intent`, sizing 의 `is_final_order_size`, exit 의
  `is_order_intent` 모두 영구 False.

## 2. 사용 지표 (`_indicators.py` 재사용)

| 함수 | 시그니처 | 정의 |
|---|---|---|
| `atr(highs, lows, closes, period=14)` | float | Average True Range |
| `true_range(highs, lows, closes)` | list[float] | 봉별 TR (`len = n-1`) |
| `adx(highs, lows, closes, period=14)` | float | Wilder smoothing 기반 ADX (옵션 필터) |

ATR 확장 비율 = `ATR(atr_period) / ATR(atr_avg_period)`. 일반적으로 1.0 = 평균
변동, 1.5 = 평균보다 50% 확장, 3.0+ = 초고변동.

## 3. 파라미터

```python
@dataclass(frozen=True)
class VolatilityBreakoutParams:
    atr_period: int = 14
    atr_avg_period: int = 42                  # 확장 baseline
    range_lookback: int = 25                  # 직전 N봉 high/low (현재 봉 제외)
    breakout_buffer_pct: float = 0.002        # 0.2% 버퍼
    volume_surge_min: float = 1.2             # vol_ratio >= 1.2 (선택 필터)
    require_volume_filter: bool = False
    vol_expansion_min: float = 1.0            # ATR_now / ATR_avg >= 1.0 (필수)
    high_vol_mult: float = 3.0                # 초고변동 기준
    high_vol_size_shrink: float = 0.5         # 초고변동 시 size hint 50%
    adx_max_for_breakout: float = 0.0         # 0=비활성. 양수면 ADX 이 값 이하만
    min_candles_required: int = 0             # 0 = atr_avg_period + 5
    allow_short_candidates: bool = False      # SHORT 옵트인
    base_notional_usdt: float = 100.0
    max_leverage_hint: float = 1.0
```

## 4. generate_signal 조건표

| 조건 | 결과 |
|---|---|
| `data_quality_grade == "EXCLUDE"` | `BLOCKED` |
| `freshness_ok == False` | `BLOCKED` |
| `is_in_universe == False` | `BLOCKED` |
| `notice_context.high_risk_symbols` 매칭 | `BLOCKED` |
| 캔들 부족 (`< min_candles_required`) | `NO_ACTION` |
| `range_lookback + 1` 미만 | `NO_ACTION` |
| OHLC 길이 불일치 | `NO_ACTION` |
| `ATR(avg) <= 0` | `NO_ACTION` |
| `ATR_now / ATR_avg < vol_expansion_min` | `HOLD` |
| `adx_max_for_breakout > 0` 이고 `ADX > 상한` | `HOLD` (추세 이미 확장) |
| `require_volume_filter=True` 이고 `volume_ratio < volume_surge_min` | `HOLD` |
| `close > prev_high × (1 + buffer)` | **`BUY` candidate** |
| `allow_short_candidates=True` 이고 `close < prev_low × (1 - buffer)` | **`SELL` candidate** |
| 위 조건 미충족 | `HOLD` |

`prev_high` / `prev_low` 는 *현재 봉 제외* 직전 `range_lookback` 봉의 max/min
(lookahead 방지).

confidence 환산:
```
base  = 0.5 + min(0.3, (expansion_ratio - vol_expansion_min) × 0.2)
bonus = min(0.1, (volume_ratio - volume_surge_min) × 0.1)  if volume 충족
      = -0.1                                                if 미달
conf  = clip(base + bonus, 0.0, 0.9)
```

quality_score: `50 + (expansion-1) × 20 + (vol_ratio-1) × 10` → 0~100 (SignalQualityAgent 입력).

stop / take_profit:
- LONG: `stop_loss = max(0, close - 1.5×ATR)`, `take_profit = close + 3×ATR`
- SHORT: 대칭

**`BUY`/`SELL` 은 후보 표시. 실제 주문 명령 아님.**

## 5. calculate_size hint 정책

```python
hint = base_notional_usdt × confidence                 # BUY/SELL 시
     × high_vol_size_shrink                            # ATR_now > ATR_avg × high_vol_mult 시
     = 0                                               # HOLD/BLOCKED/NO_ACTION 시
```

- `is_final_order_size = False` 영구 — 최종 수량 아님.
- `used_for_order = False` 영구.
- 초고변동(ATR_now > ATR_avg × `high_vol_mult` (기본 3.0)) 시 자동 50% 축소 —
  변동성 급증 구간의 손실 폭주 방지.
- 최종 수량은 RiskManager + OrderGuard 가 결정.

## 6. exit_rule 정책

| 조건 | should_exit | fraction | urgency |
|---|---|---|---|
| `data_quality_grade == "EXCLUDE"` | True | 1.0 | critical |
| `freshness_ok == False` | True | 1.0 | high |
| `notice_context.high_risk_symbols` 매칭 | True | 1.0 | high |
| LONG 시 `(entry - close) >= ATR × 1.5` | True | 0.7 | normal |
| SHORT 시 `(close - entry) >= ATR × 1.5` | True | 0.7 | normal |
| `ATR_now / ATR_avg < 0.5` (변동성 축소) | True | 0.3 | normal |
| 데이터 부족 / 길이 불일치 | False | 0 | — |
| 정상 | False | 0 | — |

ATR×1.5 역행 = 평균 변동의 1.5배 손실 = 일반적인 "stop loss 한 단위" 의미 — 분할
청산 후보. 변동성 축소(contraction)는 momentum loss 신호 — 부분 청산 후보.

`is_order_intent = False` 영구 — 실제 청산 주문 아님. 후속 risk/order pipeline 이 검토.

## 7. explain_signal 출력

`SignalExplanation` 필드:
- **summary**:
  - `BUY` → "candidate_long: ATR volatility breakout (range expansion) — candidate only, not an order"
  - `SELL` → "candidate_short: ATR volatility breakdown — candidate only, not an order"
  - 그 외 → 상태별 메시지
- **reasons**: `signal.reason`
- **evidence**:
  - `ATR(14)=…, ATR(42)=…`
  - `volatility_expansion_ratio=…`
  - `recent_high(N)=…, recent_low=…, current=…`
  - `breakout_level=…, breakdown_level=…`
  - `volume_ratio=…`
  - `data_quality_grade=…, freshness_ok=…, is_in_universe=…, regime=…`
- **risks**: stale data / EXCLUDE / high-risk notice / theme review_required /
  초고변동 regime (sizing 축소 안내)
- **limitations**:
  - "candidate only — not an order"
  - "RiskManager / OrderGuard 가 최종 수량 결정"
  - "BUY/SELL action 은 전략 판단 표현이며 직접 주문 명령이 아님"
  - "ATR 변동성 돌파는 거짓 돌파 (false breakout) 위험 — confidence/sizing 보수적"

설명 어디에도 "지금 매수해라", "즉시 주문", "자동 매수" 같은 직접 주문 지시가
들어가지 않는다.

## 8. StrategyContract ABC 만족

```python
class VolatilityBreakoutContractStrategy(StrategyContract):
    capability = StrategyCapability(
        name="volatility_breakout_atr_v2",
        description=(
            "ATR-based volatility breakout. Uses ATR(14) vs ATR(42) expansion + "
            "recent N-bar range breakout + optional volume filter. Generates "
            "candidate signals only — never order intent."
        ),
        required_inputs=("closes", "highs", "lows"),
        signal_actions=("BUY", "SELL", "HOLD", "BLOCKED", "NO_ACTION", "WATCH_ONLY"),
    )
    enabled_by_default = False
    preferred_regimes = ("RANGE", "TREND_UP", "TREND_DOWN")

    def generate_signal(self, context): ...
    def calculate_size(self, context, signal): ...
    def exit_rule(self, context, signal): ...
    def explain_signal(self, context, signal): ...
```

`preferred_regimes` 에 `RANGE` 가 포함된 이유 — range/triangle 형성 후 break 가
본 전략의 주력 시나리오. trend regime 에서도 expansion 시점 capture 가능.

`evaluate(context)` 가 4단계를 한 번에 호출하고 contract 위반 시 raise (#29).

## 9. StrategyRegistry / StrategySelectionAgent 연동

```python
from app.strategies.contract_registry import build_empty_registry
from app.strategies.volatility_breakout_contract import VolatilityBreakoutContractStrategy
from app.agents.strategy_selection import (
    StrategyActivationContext, select_active_strategies,
)

reg = build_empty_registry()
reg.register_strategy(VolatilityBreakoutContractStrategy, enabled=False)
# enabled_by_default=False — 운영자가 명시적으로 enable

ctx = StrategyActivationContext(symbol="BTC-USDT", regime="RANGE")
decision = select_active_strategies(ctx, reg)
assert "volatility_breakout_atr_v2" in decision.activated
assert decision.direct_order_allowed is False    # 영구 False
```

## 10. 사용 예 (테스트 / 백테스트)

```python
from app.strategies.volatility_breakout_contract import (
    VolatilityBreakoutContractStrategy, VolatilityBreakoutParams,
)
from app.strategies.contract import StrategyContext

s = VolatilityBreakoutContractStrategy(
    VolatilityBreakoutParams(
        atr_period=14, atr_avg_period=42,
        range_lookback=25,
        breakout_buffer_pct=0.002,
        vol_expansion_min=1.2,
        require_volume_filter=True, volume_surge_min=1.5,
        high_vol_mult=3.0, high_vol_size_shrink=0.5,
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
    regime="RANGE",                    # #19
    extra={"volume_ratio": 1.7},       # 거래량 비율 명시 (선택)
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
- 초고변동 시 sizing 자동 축소 — 변동성 급증 손실 폭주 방지.

정적 회귀 (`tests/test_volatility_breakout_contract.py`):
- `volatility_breakout_contract.py` 에 `app.brokers` / `app.execution` import 부재.
- `requests` / `httpx` / `ccxt` / `pyupbit` / `binance` / `okx` SDK import 부재.
- `.place_order(` / `.cancel_order(` / `.get_balance(` / `.submit_order(` 호출 부재.
- `ENABLE_LIVE_TRADING = True` / `is_order_intent: bool = True` /
  `is_final_order_size: bool = True` / `used_for_order=True` literal 부재.

## 12. 회귀 테스트

`backend/tests/test_volatility_breakout_contract.py` — **39 케이스**. 분류:

1. **Signal generation — 시나리오** (5) — strong breakout BUY / strong breakdown
   SELL with short enabled / breakdown without short HOLD / no expansion HOLD /
   quiet range HOLD
2. **안전 가드** (4) — quality EXCLUDE / freshness stale / universe out /
   high-risk notice
3. **데이터 / 필터** (6) — 캔들 부족 / range_lookback 미달 / OHLC 길이 불일치 /
   volume filter required → HOLD / volume filter pass → BUY / ADX 상한 필터
4. **Sizing** (4) — zero for non-actionable / proportional to confidence /
   high-vol shrink / is_final_order_size=False
5. **Exit rule** (6) — quality EXCLUDE critical / freshness high / notice high /
   ATR×1.5 adverse LONG / 정상 / volatility contraction
6. **Explanation** (4) — candidate only / evidence with ATR/expansion/breakout_level /
   risks stale / limitations not-an-order
7. **evaluate()** (1) — is_order_intent=False / 전체 layer
8. **Static guards** (4) — broker import 부재 / SDK import 부재 / order method
   호출 부재 / forbidden literal 부재
9. **Registry / SelectionAgent integration** (5) — register / capability.name /
   preferred_regimes / RANGE 활성 / UNKNOWN 활성 (보수적 inclusion)

기존 `tests/test_volatility_breakout.py` (#31 1차, 13 케이스) 회귀 없음 — 기존
`VolatilityBreakoutStrategy` 그대로 유지.

```
cd backend
python -m pytest tests/test_volatility_breakout_contract.py -q
python -m pytest tests/test_volatility_breakout.py -q     # 기존 1차 회귀
```

## 13. 후속 단계

- 32번 Pair Trading (✅ 2026-05-18 [`docs/pair_trading.md`](pair_trading.md))
  / 33번 Kimp Mean Reversion / 34번 Funding Rate 도 동일 패턴 (`*_contract.py` +
  ABC 구현)으로 추가 가능.
- `StrategySelectionAgent` 본격 구현 — LLM 또는 정교한 휴리스틱으로 regime
  detection 강화.
- 전략 결과 → AgentOrchestrator → RiskManager → OrderGateway 통합은 별도 PR.

본 단계 완료는 실거래 허가가 아니다 (CLAUDE.md §2.6). 32번 이후 전략 구현은
본 작업 범위가 아니다.
