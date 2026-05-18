# Funding Cost Guard — 체크리스트 #36

펀딩비 *리스크/비용 판단 계층* 문서. 본 문서는 `backend/app/risk/funding.py`
(Decimal 기반 구조적 ``FundingGuardDecision`` API) 의 사양을 설명한다. 기존
`backend/app/market/funding.py` (#36 1차 — float 순수 수식) 는 변경 없이 보존
되며 `tests/test_funding.py` 가 회귀를 보장한다.

> **본 단계 완료는 실거래 허가가 아니다 (CLAUDE.md §2.6).** FundingCostGuard 는
> *Signal 을 직접 주문으로 바꾸지 않으며* broker / adapter / OrderGateway 를
> 호출하지 않는다. `direct_order_allowed=False` / `used_for_order=False` 영구.

---

## 1. FundingCostGuard 목적

Perpetual futures (영구선물/스왑) 의 funding rate 가 포지션 방향과 보유 기간을
감안해 *실제 수익을 훼손할 수 있다*. 본 가드는:

- 현재 funding rate 를 읽어 방향별 비용/수익을 계산한다.
- 예상 보유 기간 동안 누적 funding cost 를 추정한다.
- 비용이 기대 edge 보다 크면 신규 진입 후보를 *차단* 한다.
- 보유 포지션의 누적 funding 비용이 과도하면 *reduce* 후보로 표시한다.
- funding data 가 stale/missing 이면 신규 진입을 차단하거나 review_required.
- KimpGuards / PairTrading / KimpStrategy / RiskManager 가 참조용으로 사용한다.

본 모듈은:

- **Strategy 가 아니다** (Signal 생성 안 함).
- **주문 모듈이 아니다** (broker / adapter / OrderGateway 호출 안 함).
- **주력 상시전략이 아니다** (참조 layer).
- 거래소 funding/perpetual/leverage/margin API 를 추가하지 않는다.

---

## 2. funding rate 의 기본 개념

Perpetual futures 는 만기가 없어 spot 가격과의 수렴을 유지하기 위해 정기적으로
*funding* 을 정산한다 (보통 8시간 주기, 거래소별로 다를 수 있음).

- ``rate_pct`` : 단일 주기당 funding 비율. % 단위 (예: 0.01 = 0.01%).
- 양수 funding (mark > index): **long → short** 으로 지급. long 이 비용, short 가 수익.
- 음수 funding (mark < index): **short → long** 으로 지급. short 가 비용, long 이 수익.

---

## 3. long / short 별 funding 비용 방향

`_signed_per_event_cost(rate, side)` 가 부호 있는 비용을 산출한다.

| side | rate 부호 | 결과 | is_unfavorable |
|---|:---:|---|:---:|
| short | + | -rate (수취) | False |
| short | - | -rate = +abs (지불) | True |
| long  | + | +rate (지불) | True |
| long  | - | +rate = -abs (수취) | False |
| unknown | any | `abs(rate)` (보수적 비용) | True |

`is_unfavorable=True` 면 WARNING (`FUNDING_DIRECTION_ADVERSE`).

---

## 4. 예상 보유 시간 기준 funding cost 계산 방식

```text
num_funding_events  = intended_hours_held / interval_hours  (분수 허용)
signed_cost_pct     = signed_per_event(rate, side) × num_funding_events
abs_cost_pct        = |signed_cost_pct|
cost_bps            = abs_cost_pct × 100
```

예: short + rate=0.01% / 8h, 24시간 보유 → events=3, signed_cost = -0.03%,
abs_cost = 0.03%, cost_bps = 3.

---

## 5. annualized funding 계산 방식

```text
periods_per_year = (24 / interval_hours) × 365
annualized_pct   = rate_pct × periods_per_year
```

예: 0.01% / 8h → 0.01 × (24/8 × 365) = 0.01 × 1095 = **10.95% APR**.

거래소별 funding interval 이 다를 수 있다 (Bybit/Binance/OKX 일반 8h, FTX 과거
1h). `FundingRateSnapshot.interval_hours` 로 노출된다.

---

## 6. cost_to_edge_ratio 개념

```text
cost_to_edge_ratio = abs_cost_pct / |expected_edge_pct|       (edge 양수일 때)
                   = None                                       (edge 미지정)
```

`is_unfavorable=False` (수익 방향) 이면 본 ratio 정책은 적용하지 않는다.
`is_unfavorable=True` 일 때만 다음 임계와 비교:

| ratio | code | severity | 결과 |
|---|---|:---:|---|
| ≥ `block_ratio` (기본 0.8) | `funding_cost_exceeds_edge` | HIGH | BLOCK |
| ≥ `review_ratio` (기본 0.4) and < block_ratio | `funding_cost_near_edge` | WARNING | REVIEW |
| < `review_ratio` | (없음) | — | PASS |

---

## 7. funding 이 불리하면 진입/보유 제한 (정책 표)

### 7.1 entry 평가 (`evaluate_funding_entry`)

| 조건 | code | severity | recommended_action |
|---|---|:---:|---|
| snapshot 없음 + `require_funding_context=True` | `funding_data_missing` | HIGH | BLOCK_NEW_CANDIDATE |
| snapshot 없음 + `require_funding_context=False` | 같은 code | WARNING | REVIEW_REQUIRED |
| interval_hours ≤ 0 | `funding_invalid_interval` | HIGH | BLOCK_NEW_CANDIDATE |
| timestamp 없음 | `funding_data_stale` | HIGH | BLOCK_NEW_CANDIDATE |
| age > `max_funding_age_seconds` (600s) | `funding_data_stale` | HIGH | BLOCK_NEW_CANDIDATE |
| `\|rate × 100\|` > `extreme_threshold_bps` (100 bps) | `funding_extreme` | HIGH | BLOCK_NEW_CANDIDATE |
| `is_unfavorable=True` | `funding_direction_adverse` | WARNING | REVIEW_REQUIRED |
| ratio ≥ block_ratio | `funding_cost_exceeds_edge` | HIGH | BLOCK_NEW_CANDIDATE |
| review_ratio ≤ ratio < block_ratio | `funding_cost_near_edge` | WARNING | REVIEW_REQUIRED |
| side / symbol 누락 | `missing_critical_context` | HIGH | BLOCK_NEW_CANDIDATE |

### 7.2 hold 평가 (`evaluate_funding_hold`)

entry 와 동일한 데이터/방향 가드를 적용하고 추가로 *누적 비용* 검사:

| 조건 | code | severity | recommended_action |
|---|---|:---:|---|
| `accumulated_funding_cost_pct ≥ accumulated_cost_reduce_pct` (2.0%) | `funding_accumulated_reduce` | HIGH | REDUCE_CANDIDATE |
| `accumulated_cost_warning_pct (1.0%) ≤ accumulated < reduce_pct` | `funding_accumulated_high` | WARNING | REVIEW_REQUIRED |
| `accumulated < warning_pct` 또는 None | (없음) | — | HOLD_CANDIDATE |

> **REDUCE_CANDIDATE 는 "보유 축소 권고" 라벨이며 *주문 명령이 아니다*.** 실제
> 청산은 Strategy → Agent → RiskManager → OrderGuard → PermissionGate →
> ApprovalQueue → OrderGateway 경로에서만.

---

## 8. missing/stale funding data 차단 정책

- 기본 `require_funding_context=True` — funding 없으면 신규 진입 *차단* (HIGH).
- 호출자가 funding 을 옵션으로 다루고 싶으면 `FundingGuardConfig(require_funding_context=False)`
  설정 — 그 경우 WARNING REVIEW 로 떨어진다.
- timestamp None 도 stale 로 간주 (HIGH).
- age > 600s 이면 stale (HIGH). 거래소 polling 주기에 맞게 호출자가 조정 가능.

---

## 9. funding 은 실제 수익을 훼손할 수 있음

본 가드의 모든 추정값 (`cost_pct` / `cost_bps` / `annualized_pct` / `cost_to_edge_ratio`)
은 **예상 비용** 일 뿐 실제 수익을 보장하지 않는다. 다음 리스크는 별도:

- 슬리피지 / 체결 실패
- 거래소 funding 산식 변경
- 마진 콜 / 강제 청산
- 레버리지 변동
- 거래 중단 / 입출금 차단 (KimpGuards 가 별도 처리)

---

## 10. FundingCostGuard 는 주문하지 않음

- broker / adapter / OrderGateway / execution / network SDK 를 import 하지 않는다.
- `.place_order` / `.cancel_order` / `.get_balance` / `.submit_order` / `.withdraw`
  / `.deposit` / `.set_leverage` / `.set_margin` 호출 부재 (정적 회귀로 강제).
- "BUY" / "SELL" / "ENTER" / "EXIT" 따옴표 리터럴 부재 (정적 회귀로 강제).
- 반환값에 BUY / SELL / ENTER / EXIT 토큰 등장 0.

`FundingGuardDecision.recommended_action` 은 다음 라벨만 사용한다:

```text
ALLOW_NEW_CANDIDATE   BLOCK_NEW_CANDIDATE   REVIEW_REQUIRED
HOLD_CANDIDATE        REDUCE_CANDIDATE
```

---

## 11. direct_order_allowed = False (영구)

`FundingGuardConfig.direct_order_allowed` / `FundingGuardConfig.used_for_order`
/ `FundingGuardDecision.direct_order_allowed` / `FundingGuardDecision.used_for_order`
모두 영구 `False`. 정적 회귀 테스트 `test_direct_order_allowed_permanently_false`
가 매 실행마다 검증한다.

---

## 12. 거래소별 funding 산식과 정산 시각 차이

- 8h 가 가장 일반적 (Binance USDT-M perpetual / OKX swap / Bybit / Deribit).
- 일부 거래소는 funding interval 이 다르거나 *동적* 으로 조정한다.
- 본 가드는 `FundingRateSnapshot.interval_hours` 에서 거래소별 값을 받아 그대로
  계산에 반영한다. 호출자가 거래소 어댑터로부터 정확한 interval 을 채워서 넘겨야
  한다.
- annualized 환산 공식: `(24 / interval_hours) × 365`.

---

## 13. KimpGuards / KimpStrategy 와의 연동 (최소)

본 단계 (#36 2차) 는 *KimpStrategy / kimp_risk_guards 코드를 직접 수정하지 않는다*.
호출자 측 연동 패턴:

```python
from app.risk.funding import (
    FundingCostGuard, FundingCostInput, FundingRateSnapshot,
    FundingGuardConfig,
)

guard = FundingCostGuard(FundingGuardConfig())
snap = FundingRateSnapshot(
    rate_pct=Decimal("0.01"),
    timestamp=now,
    interval_hours=Decimal("8"),
    exchange="okx",
    symbol="BTC",
)
inp = FundingCostInput(
    symbol="BTC",
    side="short",
    snapshot=snap,
    intended_hours_held=Decimal("24"),
    expected_edge_pct=Decimal("0.5"),
    now=now,
)
decision = guard.evaluate_entry(inp)
# decision.allowed=False → KimpStrategy 가 Signal 을 BLOCKED 로 만든다
# decision.required_review=True → Signal 에 requires_review=True 표기
# decision.estimate → Signal.meta["funding_cost_estimate"] 에 stash
ctx = build_funding_guard_context(decision)
# KimpAgent / RiskManager 가 그대로 받음
```

후속 단계 (RiskManager 본격 통합) 에서는:

- KimpRiskGuards (#35) 의 `check_funding_risk` 가 단순 rate threshold 만 사용하던
  것을 본 모듈 결정으로 대체할 수 있다.
- PairTrading / StrategyContext.meta 에 `funding_cost_context` 를 넣는 helper
  추가 (`calculate_size` 에서 size hint 축소 옵션) — *현재 단계 범위 밖*.

---

## 14. 36번 완료는 실거래 허가가 아님 · 37번 이후 미작업

CLAUDE.md §2.6 — 체크리스트 PASS 는 실거래 허가가 아니다.

- ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / ENABLE_CRYPTO_FUTURES_LIVE 모두
  기본 false 유지.
- 본 작업은 #36 Funding Cost Guard 확장만 수행. 37번 이후 (Agent Architecture
  등) 작업으로 넘어가지 않는다.

---

## 참조 모듈

- 생성: `backend/app/risk/funding.py` (#36 2차 확장)
- 회귀: `backend/tests/test_funding_guard.py` (45 케이스)
- 기존 (#36 1차, KimpStrategy 의존): `backend/app/market/funding.py`
- 기존 회귀: `backend/tests/test_funding.py` (#35 1차 + #36 1차 통합 40 케이스)
- 가드 (#35): `backend/app/strategies/kimp_risk_guards.py` / `docs/kimp_guards.md`
- 계산 (#34): `backend/app/market/kimp_calculator.py` / `docs/kimp_formula.md`
- 전략 (#33): `backend/app/strategies/kimp_mean_reversion.py`
- 안전 원칙: `docs/safety_principles.md` / `CLAUDE.md`
