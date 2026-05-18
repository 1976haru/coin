# PairTradingContractStrategy (체크리스트 #32)

> Agent Trader Crypto OS v1 — BTC-ETH / BTC-SOL 등 페어 평균회귀 후보 (Signal only)

## 0. 한 줄 요약

`PairTradingContractStrategy` 는 **두 자산의 OLS hedge ratio + spread z-score**
를 사용해 평균회귀 *후보*를 식별하는 전략이다. `BUY` / `SELL` 은 *전략 판단
표현* 이며 **실제 주문 명령이 아니다**. LONG/SHORT leg 방향성은 `leg_bias` 라는
*설명 context* 로만 전달된다. 모든 신호의 `is_order_intent` 는 영구 `False`.
**본 단계 완료는 실거래 허가가 아니다** (CLAUDE.md §2.6).

기존 `PairTradingStrategy` (Protocol 기반, `pair_trading.py`) 는 그대로 유지 —
본 신규 클래스는 #29 `StrategyContract` ABC 를 구현하는 parallel layer.

## 1. 전략 목적

서로 강하게 상관된 두 자산(예: BTC와 ETH, 또는 BTC와 SOL) 간 가격 관계가 평균
대비 과도하게 벌어졌을 때를 *후보*로 식별한다. 방향성 리스크를 *일부* 완화할
수 있지만 **완전히 제거하지 못한다** — 페어가 추가로 발산할 수 있고, leg short
가능 여부 / 자금조달 비용 / leg 별 유동성 등은 후속 단계에서 검토된다.

실제 주문 전환은 별도 단계의 책임 (Strategy → Agent → RiskManager → OrderGuard
→ PermissionGate → ApprovalQueue → OrderGateway).

설계 원칙 (CLAUDE.md §2.3 / §2.4):
- 전략은 broker / adapter / OrderGateway / MockBroker / PaperBroker 를 직접
  호출하지 않는다 (정적 회귀로 강제).
- 전략은 `.place_order(` / `.cancel_order(` / `.get_balance(` / `.submit_order(`
  를 호출하지 않는다.
- 전략은 hedge leg 주문 객체를 생성하지 않는다 — `place_pair_order` 등 활성
  심볼 부재 (정적 회귀).
- 신호의 `is_order_intent`, sizing 의 `is_final_order_size`, exit 의
  `is_order_intent` 모두 영구 False.
- LONG/SHORT 은 *포지션 방향 설명*일 뿐 주문 명령이 아니다. action 으로는
  `BUY` / `SELL` 만 사용하며 (ALLOWED_SIGNAL_ACTIONS), 구체적 leg bias 는
  `evidence` / `reason` 에 *설명*으로만 들어간다.

## 2. BTC-ETH / BTC-SOL 페어 전략 개요

| 단계 | 처리 |
|---|---|
| 입력 | leg A 가격 `closes`, leg B 가격 `extra["closes_b"]`, pair label `symbol="A,B"` |
| hedge ratio | `h = cov(A, B) / var(B)` (OLS) — 직전 `window`(기본 60) 봉 기준 |
| spread | `s_i = A_i - h × B_i` |
| z-score | `z = (s_last - mean(s)) / std(s)` |
| correlation | `corr(A, B)` — `min_correlation`(0.6) 미달이면 BLOCKED |
| 진입 | `|z| ≥ entry_z` (기본 2.0) → BUY/SELL candidate |
| 회귀 | `|z| ≤ exit_z` (기본 0.5) → HOLD, exit_rule 이 full exit 후보 |
| 관찰 | `exit_z < |z| < entry_z` → WATCH_ONLY |

## 3. hedge ratio 계산 방식

OLS 단순 회귀:
```
mean_a = mean(A_window)
mean_b = mean(B_window)
cov_ab = mean((A - mean_a) × (B - mean_b))
var_b  = mean((B - mean_b)²)
hedge  = cov_ab / var_b
```

`var_b ≤ 0` 또는 `var_a ≤ 0` 이면 NO_ACTION (degenerate variance — pair 가설
불가능).

## 4. spread 계산 방식

```
spread_i = A_i - hedge × B_i   for i in window
```

직전 `hedge_stability_window`(기본 20) 봉의 hedge 변동성도 explain 에서 보고
가능 (현재 구현은 단일 window hedge — 후속 단계에서 rolling hedge 확장 가능).

## 5. z-score 진입 / 회귀 후보 판단 방식

```
mean_s = mean(spread)
std_s  = sqrt(mean((s - mean_s)²))
z      = (spread[-1] - mean_s) / std_s
```

| |z| 범위 | action | leg_bias (설명만) |
|---|---|---|
| `|z| < exit_z` | `HOLD` (회귀 달성) | — |
| `exit_z ≤ |z| < entry_z` | `WATCH_ONLY` | — |
| `z ≥ entry_z` | `SELL` candidate | `short_a_long_b` (A 상대적 비쌈) |
| `z ≤ -entry_z` | `BUY` candidate | `long_a_short_b` (A 상대적 쌈) |
| `|z| ≥ extreme_z` | 위 SELL/BUY + `[extreme]` 태그 + sizing 50% shrink | 동일 |

confidence 환산:
```
span = max(extreme_z - entry_z, ε)
base = 0.5 + min(0.3, (|z| - entry_z) / span × 0.3)
corr_bonus = min(0.1, max(0, (corr - 0.6) × 0.3))
conf = clip(base + corr_bonus, 0.0, 0.9)
```

quality_score (`SignalQualityAgent` 입력 0~100):
```
score = 50 + (|z| - 2.0) × 15 + (corr - 0.6) × 50
```

## 6. 방향성 리스크 완화 - 한계

- 페어 hedge 는 *완전한* directional neutrality 를 제공하지 않는다 — 합성 합의
  베타가 0 이 아닐 수 있고, 시장 전체 충격은 양 leg 모두에 영향을 준다.
- spread 가 평균으로 회귀하기 전에 *추가* 발산할 수 있다 (mean reversion
  실패).
- leg 한 쪽에 거래소 공지/유동성 이슈/페그 해제 발생 시 페어 가설 자체가
  붕괴.
- short leg 의 자금조달 비용 / 차입 가능성 / 마진 요건은 본 단계에서 *체크하지
  않는다* — RiskManager / OrderGuard / PermissionGate 가 검토.

`limitations` 필드에 본 한계가 항상 포함된다.

## 7. PAIR_DIVERGENCE_CANDIDATE 는 주문이 아니다

본 전략의 결과 (action `BUY`/`SELL`, summary `candidate_pair_*`) 는 *후보
표시*이며 즉시 매수/매도 / 자동 주문 / 직접 진입 지시가 *아니다*. 어떤
설명에도 다음 표현이 등장하지 않는다 (CLAUDE.md §2.3 / §3.1):

| 금지 표현 | 본 전략 동등 표현 |
|---|---|
| "A를 매수하고 B를 매도해라" | "long_a_short_b leg_bias (descriptive context)" |
| "즉시 롱/숏 진입" | "pair mean-reversion candidate" |
| "자동 주문" | "candidate only — not an order" |
| "place pair order" | (해당 함수 / 호출 자체가 부재) |

허용 표현:
- "A leg is relatively expensive"
- "B leg is relatively cheap"
- "review_required"
- "relative value candidate"

## 8. Signal.is_order_intent=false

- `StrategySignal.is_order_intent` — frozen dataclass default `False`. 본 전략
  어디에도 명시적 `True` 지정 없음 (정적 회귀).
- `PositionSizingHint.is_final_order_size` / `used_for_order` — `False` 영구.
- `ExitRuleDecision.is_order_intent` — `False` 영구.
- `evaluate()` 반환 dict — `is_order_intent` / `direct_order_allowed` /
  `used_for_order` 모두 영구 `False`.

## 9. 직접 주문 금지

정적 회귀 (`tests/test_pair_trading_contract.py`):
- `app.brokers` / `app.execution` import 부재.
- `requests` / `httpx` / `ccxt` / `pyupbit` / `binance` / `okx` SDK import
  부재.
- `.place_order(` / `.cancel_order(` / `.get_balance(` / `.submit_order(`
  호출 부재.
- `place_pair_order(` / `submit_leg_order(` / `submit_pair_order(` /
  `app.order_gateway` import / `OrderGateway(` / `BrokerAdapter(`
  인스턴스 생성 모두 부재.

## 10. leg_bias 는 설명 context 일 뿐 주문 지시 아님

`leg_bias` 는 다음 두 값 중 하나:
- `long_a_short_b` (z < 0 — A 가 상대적 쌈)
- `short_a_long_b` (z > 0 — A 가 상대적 비쌈)

이 값은 `evidence` 와 `reason` 에 *기술* 으로만 등장한다. 실제 leg 별 매수/매도
주문 객체 생성, leg notional split, 차입 가능성, 마진 요건, leverage 는 모두
RiskManager / OrderGuard / PermissionGate 단계의 책임.

## 11. freshness / data_quality / notices / theme context 반영 방식

| context 상태 | generate_signal | calculate_size | exit_rule |
|---|---|---|---|
| `data_quality_grade == "EXCLUDE"` | `BLOCKED` | 0 | full critical exit |
| `data_quality_grade == "WARNING"` | (통과) | × 0.7 | (통과) |
| `freshness_ok == False` | `BLOCKED` | 0 | full high exit |
| `is_in_universe == False` | `BLOCKED` | 0 | (통과) |
| `notice_context.high_risk_symbols` 매칭 (leg A 또는 B) | `BLOCKED` | 0 | full high exit |
| `notice_context.warning_symbols` 매칭 | (통과 + review_required note) | review_required note | (통과) |
| `theme_context.review_required_symbols` 매칭 | (통과 + review_required note) | review_required note | (통과) |

## 12. StrategySelectionAgent 활성화

`preferred_regimes = ("RANGE", "MEAN_REVERSION", "RELATIVE_VALUE")` — 추세장
(`TREND_UP` / `TREND_DOWN`) 에서는 자동 skipped. `UNKNOWN` regime 에서는 보수적
inclusion (활성).

기존 `select_active_strategies` hook 은 `capability.supports_pair=True` 인 전략을
*단일* symbol context 에서 제외하므로, 본 전략을 활성화하려면 `symbol="A,B"`
또는 `symbol="BTC-USDT,ETH-USDT"` 형식의 *pair label* 을 전달해야 한다.

```python
from app.agents.strategy_selection import (
    StrategyActivationContext, select_active_strategies,
)
from app.strategies.contract_registry import build_empty_registry
from app.strategies.pair_trading_contract import PairTradingContractStrategy

reg = build_empty_registry()
reg.register_strategy(PairTradingContractStrategy, enabled=True)

ctx = StrategyActivationContext(symbol="BTC-USDT,ETH-USDT", regime="RANGE")
decision = select_active_strategies(ctx, reg)
assert "pair_trading_meanrev_v2" in decision.activated
assert decision.direct_order_allowed is False
```

## 13. size hint - 최종 주문 수량 아님

```python
hint = base_pair_notional_usdt × confidence            # BUY/SELL 시
     × high_z_size_shrink                              # |z| ≥ extreme_z 시 (×0.5)
     × 0.7                                             # data_quality WARNING 시
     = 0                                               # HOLD/BLOCKED/NO_ACTION/WATCH_ONLY
```

- `suggested_notional_usdt` 는 *페어 양쪽 leg 합계의 hint*. leg 별 비율은
  RiskManager 가 결정.
- `is_final_order_size = False` 영구.
- `used_for_order = False` 영구.
- `leverage_hint = 1.0` 보수적.
- `reason` 에 "Final leg sizes (A vs B notional split, leverage, short-leg
  permissibility) are decided by RiskManager / OrderGuard / PermissionGate" 명시.

## 14. exit_rule - 실제 청산 주문 아님

| 조건 | should_exit | fraction | urgency | reason |
|---|---|---|---|---|
| `data_quality == EXCLUDE` | True | 1.0 | critical | 전량 청산 후보 |
| `freshness_ok == False` | True | 1.0 | high | freshness stale |
| `notice high-risk` | True | 1.0 | high | 공지 위험 |
| `|z| ≤ exit_z` | True | 1.0 | normal | spread 회귀 달성 |
| `corr < min_correlation` | True | 0.5 | high | 페어 가설 붕괴 |
| 진입 부호 반대 + `|z| ≥ extreme_z` | True | 0.7 | normal | 방향 무효 |
| 그 외 | False | 0 | — | 정상 |

`is_order_intent = False` 영구 — 실제 청산 주문 아님. 후속 risk/order pipeline 이 검토.

## 15. 32번 완료는 실거래 허가가 아니다

CLAUDE.md §2.6 — 체크리스트 PASS 는 *실거래 허가가 아니다*. LIVE 활성화는 별도
수동 승인, 별도 환경변수, 별도 문서, 별도 테스트 모두 통과한 후에만 가능.

## 16. 33번 이후 전략은 이번 범위가 아니다

본 PR 은 #32 만 처리한다. #33 Kimp Mean Reversion 등 후속 전략은 별도 단계.

## 17. StrategyContract ABC 만족

```python
class PairTradingContractStrategy(StrategyContract):
    capability = StrategyCapability(
        name="pair_trading_meanrev_v2",
        description="Pair mean reversion. OLS hedge ratio + spread z-score. ...",
        required_inputs=("closes", "extra.closes_b"),
        signal_actions=("BUY","SELL","HOLD","BLOCKED","NO_ACTION","WATCH_ONLY"),
        supports_pair=True,
    )
    enabled_by_default = False
    preferred_regimes = ("RANGE", "MEAN_REVERSION", "RELATIVE_VALUE")

    def generate_signal(self, context): ...
    def calculate_size(self, context, signal): ...
    def exit_rule(self, context, signal): ...
    def explain_signal(self, context, signal): ...
```

`evaluate(context)` 가 4단계를 한 번에 호출하고 contract 위반 시 raise.

## 18. 사용 예 (테스트 / 백테스트)

```python
from app.strategies.pair_trading_contract import (
    PairTradingContractStrategy, PairTradingParams,
)
from app.strategies.contract import StrategyContext

s = PairTradingContractStrategy(
    PairTradingParams(
        window=60, entry_z=2.0, exit_z=0.5, extreme_z=3.0,
        min_correlation=0.6,
        base_pair_notional_usdt=100.0,
        high_z_size_shrink=0.5,
    )
)

ctx = StrategyContext(
    symbol="BTC-USDT,ETH-USDT",
    closes=tuple(btc_prices),
    extra={
        "closes_b": tuple(eth_prices),
        "symbol_a": "BTC-USDT", "symbol_b": "ETH-USDT",
    },
    freshness_ok=True,
    data_quality_grade="GOOD",
    is_in_universe=True,
    notice_context=notice_ctx_dict,
    theme_context=theme_ctx_dict,
    regime="RANGE",
)

result = s.evaluate(ctx)
# result["signal"] = StrategySignal(action='BUY'/'SELL'/'HOLD'/..., is_order_intent=False)
# result["sizing"] = PositionSizingHint(is_final_order_size=False)
# result["exit"]   = ExitRuleDecision(is_order_intent=False)
# result["explanation"] = SignalExplanation(...)
# result["is_order_intent"] == False — 영구
# result["direct_order_allowed"] == False — 영구
```

## 19. 회귀 테스트

`backend/tests/test_pair_trading_contract.py` — **46 케이스**. 분류:

1. **Signal generation 시나리오** (5) — strong +z SELL / strong -z BUY / extreme tag /
   reverted HOLD / between WATCH_ONLY
2. **안전 가드** (5) — low correlation / quality EXCLUDE / freshness stale /
   universe out / high-risk notice on leg
3. **데이터 가드** (4) — missing closes_b / insufficient window / length mismatch /
   degenerate variance
4. **Sizing** (6) — zero for HOLD / zero for WATCH_ONLY / proportional / high_z_shrink /
   warning_shrink / reason mentions RiskManager
5. **Exit rule** (7) — quality EXCLUDE critical / freshness high / notice high /
   reverted full / corr drop partial / inverted extreme partial / normal no-exit
6. **Explanation** (6) — summary candidate / evidence pair_stats / limitations
   leg_bias descriptive / limitations neutrality caveat / risks corr drop / risks stale
7. **evaluate()** (1) — is_order_intent=False / 전체 layer
8. **Static guards** (5) — broker/execution import / SDK import / order method
   calls / forbidden literal / hedge leg order keywords active patterns
9. **Registry / SelectionAgent** (7) — register / capability.name / supports_pair=True /
   preferred_regimes / single-symbol skipped / pair-symbol activated /
   UNKNOWN regime activated

기존 `tests/test_pair_trading.py` (#32 1차, 12 케이스) 회귀 없음 — 기존
`PairTradingStrategy` 그대로 유지.

```
cd backend
python -m pytest tests/test_pair_trading_contract.py -q
python -m pytest tests/test_pair_trading.py -q     # 기존 1차 회귀
```

## 20. 후속 단계

- 33번 Kimp Mean Reversion / 34번 Kimp Formula / 35번 Kimp Guards 도 동일
  패턴(`*_contract.py` + ABC 구현)으로 추가 가능.
- 본 전략 확장: rolling hedge ratio / cointegration 검정 (Engle-Granger,
  Johansen) / 다중 leg pair (BTC-ETH-SOL triangular) — 후속 단계.
- 전략 결과 → AgentOrchestrator → RiskManager → OrderGateway 통합은 별도 PR.

본 단계 완료는 실거래 허가가 아니다 (CLAUDE.md §2.6). 33번 이후 전략 구현은
본 작업 범위가 아니다.
