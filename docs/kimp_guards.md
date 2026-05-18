# Kimp Risk Guards — 체크리스트 #35

김프/역김프 *진입 후보 차단 계층* 문서. 본 문서는 `backend/app/strategies/kimp_risk_guards.py`
(Decimal 기반, 구조적 ``KimpGuardDecision`` API) 의 사양을 설명한다. 기존
`backend/app/strategies/kimp_guards.py` (#35 1차 — float 7+1 단계 가드 + `evaluate_entry_guards`)
는 변경 없이 보존되며 KimpStrategy 회귀를 보장한다.

> **본 단계 완료는 실거래 허가가 아니다 (CLAUDE.md §2.6).** KimpRiskGuards 는
> *Signal 을 직접 주문으로 바꾸지 않는다*. broker / adapter / OrderGateway 를
> 호출하지 않으며, `direct_order_allowed=False` / `used_for_order=False` 영구.

---

## 1. KimpGuards 는 가드이지 전략이 아님

- Signal 을 *생성하지 않는다* (KimpStrategy 가 별도로 만든다).
- 주문 객체를 *생성하지 않는다*.
- broker / adapter / OrderGateway / execution / network SDK 를 import 하지 않는다.
- `place_order` / `cancel_order` / `get_balance` / `submit_order` / `withdraw` /
  `deposit` 호출 부재.
- BUY / SELL / ENTER / EXIT 문자열 리터럴을 반환값으로 사용하지 않는다.
- *주력 상시전략으로 등록하지 않는다* (event-only 보조 layer).

본 모듈이 반환하는 것은 `KimpGuardDecision` — KimpStrategy / KimpAgent /
RiskManager 가 *참조용* 으로만 사용한다.

---

## 2. 입력 — `KimpGuardInput`

```python
@dataclass(frozen=True)
class KimpGuardInput:
    symbol: str
    intended_kimp_state: str = "UNKNOWN"   # REVERSE_KIMP_CANDIDATE / KIMP_CANDIDATE / NEUTRAL_CANDIDATE / UNKNOWN
    domestic_exchange: str = "upbit"
    foreign_exchange: str = "okx"
    notices: tuple[Mapping[str, Any], ...] = ()
    notice_context_available: bool = True
    fx_rate_krw: Decimal | None = None
    fx_timestamp: datetime | None = None
    fx_reference: Decimal | None = None
    fx_source: str | None = None
    kimp_result: KimpResult | None = None   # #34
    # 호가창 (양 leg)
    domestic_bid / domestic_ask / domestic_bid_size / domestic_ask_size: ...
    foreign_bid  / foreign_ask  / foreign_bid_size  / foreign_ask_size:  ...
    orderbook_timestamp: datetime | None = None
    # Regime / 테마
    market_regime: str | None = None      # e.g. STRONG_BULL / BULL_TREND / RANGE
    theme_tags: tuple[str, ...] = ()      # e.g. ETF_INFLOW
    short_leg_implied: bool = False
    # 펀딩
    funding_rate_pct / funding_timestamp / funding_position_side
    # 가격 freshness
    domestic_price_timestamp / foreign_price_timestamp
    # 품질
    data_quality_grade: str | None = None   # GOOD / WARNING / EXCLUDE
    now: datetime | None = None
```

Notice 는 *듀크 타입 dict* 시퀀스 — 기존 `app.market.notice_context.NoticeContext`
와의 결합 없이 ``ExchangeNotice`` row / builder dict / 수동 mock 모두 호환.
기대 키: `notice_type / severity / symbols / exchange / title`.

---

## 3. 결정 — `KimpGuardDecision`

```python
@dataclass(frozen=True)
class KimpGuardDecision:
    input: KimpGuardInput
    allowed: bool                            # blocking 사유 없으면 True
    required_review: bool                    # WARNING/INFO 사유 있으면 True
    recommended_action: str                  # ALLOW_CANDIDATE / REVIEW_REQUIRED / BLOCK_CANDIDATE / CONTEXT_ONLY
    reasons: tuple[KimpGuardReason, ...]
    blocked_by: tuple[str, ...]              # HIGH/CRITICAL 사유 code 목록
    review_codes: tuple[str, ...]            # WARNING/INFO 사유 code 목록
    computed_at: datetime
    direct_order_allowed: bool = False       # 영구 False
    used_for_order: bool = False             # 영구 False
```

`KimpGuardReason` 의 필드: `code / severity / source / message / exchange /
symbol / evidence`.

`evidence` 는 Decimal/datetime 을 그대로 보존 — `build_kimp_guard_context` 에서
호출자가 직렬화한다.

---

## 4. 8개 가드 함수

각 가드는 `list[KimpGuardReason]` 을 반환. `evaluate_kimp_guards` 가 모두 합성.

### 4.1 `check_notice_risk` — 공지 가드 정책

| 조건 | code | 심각도 | 결과 |
|---|---|:---:|---|
| 입출금 중단 (`DEPOSIT_WITHDRAWAL_SUSPENSION`) | `deposit_withdrawal_suspended` | CRITICAL | BLOCK |
| 상장폐지 (`DELISTING`) | `delisting_notice` | CRITICAL | BLOCK |
| 유의종목 (`CAUTION`) | `caution_notice` | HIGH | BLOCK |
| 거래중단 (`TRADING_SUSPENSION`) | `trading_suspension` | CRITICAL | BLOCK |
| 미매핑 + severity HIGH/CRITICAL | `high_severity_notice` | (그대로) | BLOCK |
| `notice_context_available=False` + `require_notice_context=True` | `notice_context_missing` | HIGH | BLOCK |
| `notice_context_available=False` + `require_notice_context=False` | `notice_context_missing` | WARNING | REVIEW |

매칭 규칙: notice 의 `exchange` 가 domestic/foreign 거래소 중 하나에 일치 +
`symbols` 가 비어 있거나 (전역 공지) input.symbol 을 포함하면 적용.

### 4.2 `check_fx_risk` — FX 가드 정책

| 조건 | code | 심각도 | 결과 |
|---|---|:---:|---|
| `fx_rate_krw` ≤ 0 또는 None | `fx_invalid` | CRITICAL | BLOCK + 조기 반환 |
| `fx_source` 미설정 | `fx_source_missing` | WARNING | REVIEW |
| `fx_timestamp` None | `fx_stale` | HIGH | BLOCK |
| `now - fx_timestamp > max_fx_age_seconds` (60s) | `fx_stale` | HIGH | BLOCK |
| `KimpResult.fx_anomaly=True` | `fx_anomaly` | HIGH | BLOCK |

`fx_anomaly` 는 #34 KimpCalculator 의 sanity range 또는 reference deviation
검사 결과를 그대로 반영한다.

### 4.3 `check_liquidity_risk` — 호가 가드 정책

| 조건 | code | 심각도 | 결과 |
|---|---|:---:|---|
| 호가 모든 leg None + `require_orderbook_context=True` | `orderbook_missing` | HIGH | BLOCK |
| 호가 모든 leg None + `require_orderbook_context=False` | (없음) | — | PASS |
| `bid` ≤ 0 또는 `ask` ≤ 0 | `orderbook_invalid` | CRITICAL | BLOCK |
| `bid_size < min_bid_size` (cfg 양수일 때) | `liquidity_thin` | HIGH | BLOCK |
| `ask_size < min_ask_size` (cfg 양수일 때) | `liquidity_thin` | HIGH | BLOCK |
| `spread_bps > max_spread_bps` (50 bps) | `spread_wide` | HIGH | BLOCK |
| `now - orderbook_timestamp > max_orderbook_age_seconds` (10s) | `orderbook_stale` | HIGH | BLOCK |

양 leg 독립 검사. spread = `(ask - bid) / mid × 10_000` bps. mid 가 0/음수면
계산 생략.

### 4.4 `check_bull_market_short_risk` — 강세장 short 가드 정책

본 가드는 `block_reverse_kimp_short_in_bull_market=True` (기본) 이고 다음 4개 조건
모두 만족할 때만 차단한다:

1. `intended_kimp_state == REVERSE_KIMP_CANDIDATE`
2. `short_leg_implied == True`
3. (a) `market_regime` 이 `bull_market_regimes` (`STRONG_BULL` / `BULL_TREND`) 포함, **또는**
4. (b) `theme_tags` 중 하나가 `bull_market_themes` (`ETF_INFLOW` / `MARKET_WIDE_RALLY` / `RISK_ON_STRONG`) 포함

차단 사유: `bull_market_short_blocked` (HIGH).

- KIMP_CANDIDATE (정김프) 또는 short_leg=False 면 통과.
- `block_reverse_kimp_short_in_bull_market=False` 설정이면 항상 통과.
- 본 가드는 *주문을 만들지 않고 후보만 차단한다* (CLAUDE.md §2.3).

### 4.5 `check_funding_risk` — 펀딩 가드 정책

| 조건 | code | 심각도 | 결과 |
|---|---|:---:|---|
| `funding_rate_pct=None` + `require_funding_context=True` | `funding_context_missing` | HIGH | BLOCK |
| `funding_rate_pct=None` + `require_funding_context=False` | (없음) | — | PASS (optional) |
| `\|rate × 100\| > funding_risk_threshold_bps` (100 bps) | `funding_risk_high` | HIGH | BLOCK |
| `now - funding_timestamp > max_funding_age_seconds` (600s) | `funding_stale` | WARNING | REVIEW |
| `side=short` 이고 `rate < 0` (또는 `side=long` 이고 `rate > 0`) | `funding_direction_adverse` | WARNING | REVIEW |

펀딩비 데이터는 *optional* — 없어도 `require=False` 이면 가드 사유를 만들지 않는다.

### 4.6 `check_freshness_risk` — 가격 freshness 정책

| 조건 | code | 심각도 | 결과 |
|---|---|:---:|---|
| `now - domestic_price_timestamp > max_price_age_seconds` (30s) | `domestic_price_stale` | HIGH | BLOCK |
| 같은 조건 foreign | `foreign_price_stale` | HIGH | BLOCK |
| 둘 다 None | `price_timestamp_missing` | WARNING | REVIEW |

### 4.7 `check_data_quality_risk` — 데이터 품질 정책

| 조건 | code | 심각도 | 결과 |
|---|---|:---:|---|
| `grade=EXCLUDE` + `block_on_data_quality_exclude=True` (기본) | `data_quality_exclude` | CRITICAL | BLOCK |
| `grade=EXCLUDE` + 비활성 | 같은 code | WARNING | REVIEW |
| `grade=WARNING` + `block_on_data_quality_warning=False` (기본) | `data_quality_warning` | WARNING | REVIEW |
| `grade=WARNING` + 활성 | 같은 code | HIGH | BLOCK |
| `grade=GOOD` | (없음) | — | PASS |

#17 Data Quality (`GOOD`/`WARNING`/`EXCLUDE`) 등급을 그대로 받는다.

### 4.8 `check_missing_critical_context` — 보충 가드

`fx_rate_krw` / `kimp_result` 누락은 다른 가드도 잡지만 본 가드가 보충 사유로
명시한다. `intended_kimp_state=UNKNOWN` 이면 WARNING (가능한 다른 가드들 결과
참조).

| 누락 필드 | code | 심각도 |
|---|---|:---:|
| `fx_rate_krw` 또는 `kimp_result` | `missing_critical_context` | HIGH |
| `intended_kimp_state` 만 누락 | 같은 code | WARNING |

---

## 5. 합성 — `evaluate_kimp_guards`

```text
모든 가드 reason 수집 → blocking severity (HIGH/CRITICAL) 분리:
  blocked_by  = code(blocking reasons)
  review_codes = code(WARNING/INFO reasons)

결정:
  if blocked_by         → allowed=False, required_review=True,  BLOCK_CANDIDATE
  elif review_codes     → allowed=True,  required_review=True,  REVIEW_REQUIRED
  else                  → allowed=True,  required_review=False, ALLOW_CANDIDATE

direct_order_allowed = False  (영구)
used_for_order       = False  (영구)
```

---

## 6. KimpAgent / RiskManager hook — `build_kimp_guard_context`

```python
{
  "kind": "kimp_guard_context",
  "direct_order_allowed": False,
  "used_for_order": False,
  "symbol": "BTC",
  "intended_kimp_state": "REVERSE_KIMP_CANDIDATE",
  "domestic_exchange": "upbit",
  "foreign_exchange": "okx",
  "allowed": False,
  "required_review": True,
  "recommended_action": "BLOCK_CANDIDATE",
  "blocked_by": ["fx_anomaly", "domestic_price_stale"],
  "review_codes": ["fx_source_missing"],
  "reasons": [
    {"code": ..., "severity": ..., "source": ..., "message": ..., ...},
    ...
  ],
  "summary": "BLOCKED — 2 blocking reason(s)",
  "computed_at": "2026-05-18T...Z"
}
```

* JSON 직렬화 호환 — Decimal/datetime 은 str.
* `direct_order_allowed=False` 명시 — agent / risk manager 가 절대 주문 권한으로
  해석하지 않는다.
* `recommended_action` 은 라벨이지 *주문 명령이 아니다*.

---

## 7. KimpStrategy / KimpCalculator 와의 연동 (최소 wrapper)

본 단계 (#35 2차) 는 *KimpStrategy 코드를 직접 수정하지 않는다*. 기존 동작
(#33 + #35 1차) 은 그대로 유지되며 `test_existing_kimp_guards_still_works` 가
회귀를 보장한다.

연동 패턴 (호출자 측에서 권장):

```python
from app.market.kimp_calculator import KimpInputs, compute_kimp
from app.strategies.kimp_risk_guards import (
    KimpGuardInput, KimpCandidateState, evaluate_kimp_guards,
    build_kimp_guard_context,
)

kimp = compute_kimp(KimpInputs(...))
guard_input = KimpGuardInput(
    symbol="BTC",
    intended_kimp_state=KimpCandidateState.REVERSE_KIMP_CANDIDATE,
    fx_rate_krw=kimp.inputs.fx_rate_krw,
    fx_timestamp=now,
    fx_source="upbit_quote",
    kimp_result=kimp,
    notices=tuple_of_notice_dicts,
    notice_context_available=True,
    domestic_price_timestamp=...,
    foreign_price_timestamp=...,
    market_regime="RANGE",
    short_leg_implied=True,
    data_quality_grade="GOOD",
    now=now,
)
decision = evaluate_kimp_guards(guard_input)
# decision.allowed=False → KimpStrategy 가 Signal action=BLOCKED 로 생성
# decision.required_review=True → Signal 에 requires_review=True 표기
# decision.reasons → Signal.evidence 에 stash
context = build_kimp_guard_context(decision)
# KimpAgent / RiskManager 가 그대로 받음
```

---

## 8. 직접 주문 금지 검증

테스트가 자동 회귀:

- `test_module_no_broker_or_execution_imports`
- `test_module_no_order_gateway_or_adapter_imports`
- `test_module_no_network_sdk_imports`
- `test_module_no_order_method_calls` (`.place_order/.cancel_order/.get_balance/.submit_order/.withdraw/.deposit` 호출 부재)
- `test_module_no_forbidden_substrings`
  (`ENABLE_LIVE_TRADING=True`, `is_order_intent=True`, `used_for_order=True`,
  `direct_order_allowed=True` 등 모두 부재)
- `test_module_no_recommended_action_buy_sell_enter_exit`
  ("BUY"/"SELL"/"ENTER"/"EXIT" 따옴표 리터럴 부재 — docstring 설명용 단어 노출은
  허용)
- `test_direct_order_allowed_permanently_false_on_config_and_decision`
- `test_dataclasses_are_frozen`

---

## 9. 35번 완료는 실거래 허가가 아님 · 36번 이후 미작업

CLAUDE.md §2.6 — 체크리스트 PASS 는 실거래 허가가 아니다.

- ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / ENABLE_CRYPTO_FUTURES_LIVE 모두
  기본 false 유지.
- 본 작업은 #35 Kimp Risk Guards 확장만 수행. 36번 (Funding Cost Guard) 이후
  작업으로 넘어가지 않는다.
- KimpStrategy / KimpAgent 본격 통합은 후속 단계.

---

## 참조 모듈

- 생성: `backend/app/strategies/kimp_risk_guards.py` (#35 2차 확장)
- 회귀: `backend/tests/test_kimp_risk_guards.py` (52 케이스)
- 기존 (#35 1차, KimpStrategy 의존): `backend/app/strategies/kimp_guards.py`
- 계산 (#34): `backend/app/market/kimp_calculator.py` / `docs/kimp_formula.md`
- 전략 (#33): `backend/app/strategies/kimp_mean_reversion.py`
- 공지 (#18): `backend/app/market/notice_context.py`
- 데이터 freshness (#16): `backend/app/market/freshness.py`
- 데이터 품질 (#17): `backend/app/market/data_quality.py`
- 안전 원칙: `docs/safety_principles.md` / `CLAUDE.md`
