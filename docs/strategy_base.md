# StrategyBase / StrategyContract (체크리스트 #29)

> Agent Trader Crypto OS v1 — 전략 공통 인터페이스 (Signal 만 생성, 주문 안 함)

## 0. 한 줄 요약

전략은 **Signal 만 생성**한다. 주문/체결 호출은 절대 하지 않는다. 모든 신호의
`is_order_intent` 는 영구 `False` — 실제 주문 전환은 Strategy → Agent →
RiskManager → OrderGuard → PermissionGate → ApprovalQueue → OrderGateway 경로
에서만. **본 단계 완료는 실거래 허가가 아니다** (CLAUDE.md §2.6).

## 1. 두 계층 구조

본 저장소에는 **두 개의 strategy contract** 가 공존한다 — 점진적 마이그레이션.

| | `StrategyBase` (`base.py`) — 1차 | `StrategyContract` (`contract.py`) — 2차 신규 |
|---|---|---|
| 종류 | Protocol (duck typing) | ABC (abstract methods) |
| 강제 | `capability` 속성만 | `capability` + 4개 abstract method |
| 메서드 | 전략별 자유 | `generate_signal` / `calculate_size` / `exit_rule` / `explain_signal` |
| 기존 전략 | TrendFollowing/VolatilityBreakout/PairTrading/KimpMeanReversion 가 만족 | 신규 전략이 따른다 |
| Registry | `StrategyRegistry` (name 키) | `ContractRegistry` (regime/symbol/enabled 필터 지원) |

기존 `StrategyBase` Protocol 은 **그대로 유지** — 기존 4개 전략과 35개 회귀 테스트
가 그대로 동작한다. 신규 `StrategyContract` 는 더 풍부한 인터페이스를 강제하며
신규 전략은 본 ABC 를 따른다.

## 2. 전략 계층 역할

### 2.1 `StrategyContract` (ABC)

```python
class StrategyContract(ABC):
    capability: StrategyCapability        # 메타데이터 (name/description/required_inputs/...)
    enabled_by_default: bool = False      # 운영자가 명시 활성화
    preferred_regimes: tuple = ("UNKNOWN",)

    @abstractmethod
    def generate_signal(self, context: StrategyContext) -> StrategySignal: ...

    @abstractmethod
    def calculate_size(
        self, context: StrategyContext, signal: StrategySignal,
    ) -> PositionSizingHint: ...

    @abstractmethod
    def exit_rule(
        self, context: StrategyContext, signal: StrategySignal,
    ) -> ExitRuleDecision: ...

    @abstractmethod
    def explain_signal(
        self, context: StrategyContext, signal: StrategySignal,
    ) -> SignalExplanation: ...
```

`evaluate(context)` 편의 메서드 — 4단계를 한 번에 호출하고 결과 dict 반환.
호출 중 신호가 `is_order_intent=True` 또는 sizing 이 `is_final_order_size=True`
이면 즉시 `StrategyContractError` raise.

### 2.2 타입 요약

| 타입 | 핵심 필드 | 영구 False 플래그 |
|---|---|---|
| `StrategyContext` | symbol/timeframe/closes/freshness_ok/data_quality_grade/notice_context/theme_context/regime/positions_snapshot | (`extra` 에 secret 키 → raise) |
| `StrategySignal` | action / confidence / reason / entry_price / stop_loss / take_profit | `is_order_intent=False` |
| `PositionSizingHint` | suggested_qty / suggested_notional_usdt / leverage_hint / confidence / reason | `is_final_order_size=False`, `used_for_order=False` |
| `ExitRuleDecision` | should_exit / exit_qty_fraction(0..1) / urgency(normal/high/critical) / reason | `is_order_intent=False` |
| `SignalExplanation` | strategy_name / symbol / summary / reasons / evidence / risks / limitations / confidence / generated_at | (직접 주문 지시 없음) |

### 2.3 허용 action 카탈로그

`ALLOWED_SIGNAL_ACTIONS = ("BUY", "SELL", "HOLD", "BLOCKED", "NO_ACTION", "WATCH_ONLY")`

- `BUY` / `SELL` 은 **전략의 판단 표현** — *주문 명령이 아니다*. 후속 Agent /
  RiskManager / OrderGuard / PermissionGate / ApprovalQueue / OrderGateway 단계
  에서 실제 주문 의도로 변환된다.
- `BLOCKED` — data quality EXCLUDE 등 안전 사유로 비활성.
- `NO_ACTION` — 조건 미충족, 행동 없음.
- `WATCH_ONLY` — 낮은 confidence, 관찰만.

`is_safe_action(action)` 헬퍼가 카탈로그 멤버 여부를 검증.

## 3. 안전 가드

### 3.1 코드 레벨

`StrategyContract.evaluate()` 가 각 단계 결과를 검증:

| 검증 | 위반 시 |
|---|---|
| signal.is_order_intent == False | `StrategyContractError` |
| sizing.is_final_order_size == False | `StrategyContractError` |
| exit.is_order_intent == False | `StrategyContractError` |
| context.extra 에 secret 류 키 부재 | `StrategyContextError (생성 시점)` |

`assert_no_order_intent(signal)` — 외부 검증 헬퍼.

### 3.2 모듈 경계 (정적 회귀)

`backend/app/strategies/` 의 모든 파일이 다음을 만족 (정적 회귀로 강제):

- `app.brokers.*` import 부재
- `app.execution.order_gateway` / `app.execution.order_executor` / `app.execution` import 부재
- `.place_order(` / `.cancel_order(` / `.get_balance(` / `.submit_order(` 호출 부재
- `contract.py` / `contract_registry.py` 가 `requests` / `httpx` / `ccxt` / `pyupbit` / `binance` / `okx` SDK import 부재
- `ENABLE_LIVE_TRADING = True` / `is_order_intent: bool = True` literal 부재

`backend/app/agents/strategy_selection.py` 도 brokers 미참조.

## 4. ContractRegistry

신규 ABC 전략의 등록소. 1차 `StrategyRegistry` 와 분리 — 본 registry 는
`StrategyContract` 하위 클래스만 받는다.

```python
from app.strategies.contract_registry import build_empty_registry

reg = build_empty_registry()
reg.register_strategy(MyStrategy, enabled=False)
reg.list_strategies()                      # ["my_strategy"]
reg.set_enabled("my_strategy", True)
reg.filter_by_market_regime("TREND_UP")    # regime 매칭
reg.filter_by_symbol("BTC-USDT")           # pair 전략 제외
reg.filter_enabled()
reg.create_strategy("my_strategy", config={...})
reg.catalog()                              # UI/API 용 dict 목록
```

특징:
- 같은 name 중복 등록 → `ValueError`.
- `StrategyContract` 하위 아니면 `TypeError`.
- `enabled_by_default=False` 기본 — 운영자가 명시 활성화.
- `capability.supports_pair=True` 인 entry 는 `filter_by_symbol` 에서 제외 (단일
  symbol context 부적합).
- registry 자체는 broker/adapter/order_gateway 를 알지 못한다.

## 5. StrategySelectionAgent hook (interface only)

`backend/app/agents/strategy_selection.py` — **본격 구현은 후속 단계**. 본 단계
에서는 hook + 보수적 휴리스틱만.

```python
from app.agents.strategy_selection import (
    StrategyActivationContext, select_active_strategies,
)

ctx = StrategyActivationContext(
    symbol="BTC-USDT", regime="TREND_UP",
    notice_high_risk_count=0, theme_review_required=False,
)
decision = select_active_strategies(ctx, registry)

decision.activated              # ("trend_following", ...)
decision.skipped                # ("pair_dummy",)
decision.skipped_reasons        # {"pair_dummy": "pair_strategy_requires_two_symbols"}
decision.direct_order_allowed   # False — 영구
decision.used_for_order         # False — 영구
```

휴리스틱 (보수적):
1. `registry.filter_enabled()` → enabled 가 0개면 전체 후보.
2. `context.regime` 와 entry 의 `preferred_regimes` 매칭. `UNKNOWN` 은 통과 (보수적).
3. pair 전략은 단일 symbol context 에서 제외.
4. `notice_high_risk_count > 0` 또는 `theme_review_required=True` 면 후보 유지 +
   `notes` 추가 (Risk/OrderGuard 단계에서 추가 검증 권장).

**본 hook 은 차단하지 않는다** — 차단은 Risk/OrderGuard/PermissionGate 의 책임.

## 6. 신규 전략 추가 방법

```python
from app.strategies.contract import (
    StrategyContract, StrategyContext, PositionSizingHint,
    ExitRuleDecision, SignalExplanation,
)
from app.strategies.base import StrategyCapability
from app.strategies._signals import StrategySignal


class MyMeanReversionStrategy(StrategyContract):
    capability = StrategyCapability(
        name="my_mean_reversion",
        description="RSI < 30 → BUY candidate, RSI > 70 → SELL candidate",
        required_inputs=("closes", "rsi"),
        signal_actions=("BUY", "SELL", "HOLD", "BLOCKED"),
    )
    enabled_by_default = False
    preferred_regimes = ("RANGE",)

    def __init__(self, oversold: float = 30, overbought: float = 70):
        self.oversold = oversold
        self.overbought = overbought

    def generate_signal(self, ctx: StrategyContext) -> StrategySignal:
        # 안전 가드 — context 의 freshness/quality 확인
        if not ctx.freshness_ok:
            return StrategySignal(action="NO_ACTION", confidence=0,
                                  reason="stale data")
        if ctx.data_quality_grade == "EXCLUDE":
            return StrategySignal(action="BLOCKED", confidence=0,
                                  reason="data quality EXCLUDE")
        rsi = ctx.extra.get("rsi")
        if rsi is None:
            return StrategySignal(action="HOLD", confidence=0,
                                  reason="rsi unavailable")
        if rsi < self.oversold:
            return StrategySignal(action="BUY", confidence=0.6,
                                  reason=f"RSI={rsi:.1f} < {self.oversold}")
        if rsi > self.overbought:
            return StrategySignal(action="SELL", confidence=0.6,
                                  reason=f"RSI={rsi:.1f} > {self.overbought}")
        return StrategySignal(action="HOLD", confidence=0, reason="RSI mid")

    def calculate_size(self, ctx, signal) -> PositionSizingHint:
        # 최종 수량이 아님 — RiskManager 가 결정.
        return PositionSizingHint(
            symbol=ctx.symbol,
            suggested_notional_usdt=100.0,
            confidence=signal.confidence,
            reason="fixed 100 USDT hint",
        )

    def exit_rule(self, ctx, signal) -> ExitRuleDecision:
        # 실제 주문 명령이 아님.
        return ExitRuleDecision(symbol=ctx.symbol, should_exit=False)

    def explain_signal(self, ctx, signal) -> SignalExplanation:
        return SignalExplanation(
            strategy_name=self.capability.name,
            symbol=ctx.symbol,
            summary=f"candidate: {signal.action} (RSI mean-reversion)",
            reasons=(signal.reason,),
            limitations=("RSI 신호는 횡보장에 강하나 강한 추세에서 약함",),
            confidence=signal.confidence,
        )


# registry 에 등록
from app.strategies.contract_registry import build_empty_registry
reg = build_empty_registry()
reg.register_strategy(MyMeanReversionStrategy, enabled=False)
```

## 7. 절대 금지 (재확인)

- 전략 코드에서 `app.brokers` / `app.execution.order_gateway` import ❌
- 전략에서 `.place_order(` / `.cancel_order(` / `.get_balance(` / `.submit_order(` 호출 ❌
- 신호 객체의 `is_order_intent=True` 기본값 ❌
- `PositionSizingHint.is_final_order_size=True` 기본값 ❌
- `BUY`/`SELL` 을 실제 주문 명령으로 해석하는 코드 ❌
- 실제 거래소 주문 API 호출 추가 ❌
- frontend 에 secret 추가 ❌

위반 시 정적 회귀 테스트(36+)가 실패한다.

## 8. 회귀 테스트

`backend/tests/test_strategy_contract.py` — 42 케이스. 분류:

1. **ABC instantiation** (3) — 직접/incomplete/complete subclass
2. **evaluate** (2) — 결과 dict, is_order_intent=True → raise
3. **StrategyContext** (3) — defaults / list→tuple / secret key 거부
4. **StrategySignal** (3) — defaults / assert_no_order_intent
5. **PositionSizingHint** (2) — defaults / broker 필드 부재
6. **ExitRuleDecision** (3) — should_exit + is_order_intent / fraction 범위 / urgency
7. **SignalExplanation** (2)
8. **ContractRegistry** (8) — 하위 클래스 검증 / 중복 / get/list/catalog /
   create / regime 필터 / symbol 필터(pair 제외) / enabled / set_enabled
9. **StrategySelectionAgent hook** (6) — decision 반환 / regime 매칭 / pair 제외 /
   notice/theme notes / UNKNOWN
10. **Safety helpers** (2) — is_safe_action / ALLOWED_SIGNAL_ACTIONS
11. **Static regression** (6) — brokers/execution import 부재 / order method
    호출 부재 / SDK import 부재 / forbidden literal / agent import 부재
12. **Freshness/Quality 통합 sample** (2)
13. **Notice/Theme 통합 sample** (1)

기존 `tests/test_strategy_base.py` 35 케이스 회귀 없음.

```
cd backend
python -m pytest tests/test_strategy_contract.py tests/test_strategy_base.py -q
```

## 9. 후속 단계

- 30번 Trend Following 이후 — 본 ABC contract 를 기존 전략에 점진적 마이그레이션
  검토 (단, 기존 1차 회귀 테스트 보존).
- `StrategySelectionAgent` 본격 구현 — 후속 단계에서 LLM 기반 또는 정교한 휴리스틱.
- 전략 결과 → AgentOrchestrator → RiskManager → OrderGateway 연결은 별도 PR.
  전략 자체는 **이미 본 계층에서 끝나며** 그 다음은 risk/order pipeline 의 책임.

본 단계 완료는 실거래 허가가 아니다 (CLAUDE.md §2.6).
