# Strategy Portfolio — Agent Trader Crypto OS v1

> 체크리스트 **#2** — 4대 전략 확정 산출물.
> 원칙: 모든 전략은 **signal-only**. 직접 주문 금지. `StrategySignal.is_order_intent=false` 기본.

---

## 1. 4대 전략 카드 (한눈에)

```
┌──────────────────────┬──────────────────────┐
│ ① Trend Following    │ ② Volatility Breakout│
│   추세추종           │   변동성 돌파        │
│   장세: TREND_*      │   장세: BREAKOUT     │
│   상태: 상시 후보    │   상태: 상시 후보    │
├──────────────────────┼──────────────────────┤
│ ③ Pair Trading       │ ④ Kimp Mean Reversion│
│   페어 평균회귀      │   역김프 평균회귀    │
│   장세: RANGE        │   장세: KIMP_GAP     │
│   상태: 상시 후보    │   상태: 특수 전용    │
└──────────────────────┴──────────────────────┘
```

UI(#73 Dashboard)는 이 4장을 카테고리 카드로 보여준다 (MOCA 패턴).

---

## 2. 전략별 상세

### 2.1 Trend Following (추세추종) — `app/strategies/strategies.py::TrendFollowingStrategy`

| 항목 | 값 |
|---|---|
| 카테고리 | 추세추종 / 모멘텀 |
| 목적 | 코인 장기 추세 구간 포착 |
| 적합 장세 | TREND_UP / TREND_DOWN (ADX ≥ 18) |
| **role** | regime-following primary |
| **regime** | trending markets |
| **risk** | 횡보장 손실 누적, late-entry |
| **status** | active (PAPER signal-only) |

진입/청산:
- **BUY**: `EMA20 > EMA60` AND `현재가 > SMA200` AND `volume_ratio ≥ 1.2`, 신뢰도 ≤ 0.88
- **SELL**: `EMA20 < EMA60` AND `현재가 < SMA200` AND `volume_ratio ≥ 1.2`, 신뢰도 ≤ 0.80
- 손절: ATR × 1.5
- 익절: ATR × 3.0

차단 조건:
- ADX < 18 (횡보장 자동 비활성화) → `HOLD`
- 데이터 부족 (ema_slow + 5 미만) → `HOLD`

### 2.2 Volatility Breakout (변동성 돌파) — `app/strategies/strategies.py::VolatilityBreakoutStrategy`

| 항목 | 값 |
|---|---|
| 카테고리 | 변동성 돌파 / ATR |
| 목적 | 장세 전환과 급등락 구간 포착 |
| 적합 장세 | 변동성 확장 구간 (BREAKOUT) |
| **role** | event-driven |
| **regime** | volatility expansion |
| **risk** | 가짜 돌파(whipsaw), 슬리피지 |
| **status** | active (PAPER signal-only) |

진입/청산:
- 기준선:
  - `breakout_level = 전 26봉 최고가 × (1 + 0.002)`
  - `breakdown_level = 전 26봉 최저가 × (1 − 0.002)`
- **BUY**: `현재가 > breakout_level` AND `volume_ratio ≥ 1.2`
- **SELL**: `현재가 < breakdown_level`
- 초고변동(ATR > avg×3) 자동 사이즈 50% 축소

차단 조건:
- `volume_ratio < 1.2` → `HOLD`
- 데이터 < 20봉 → `HOLD`
- 룩어헤드 방지: `[-26:-1]` 슬라이스로 전봉 기준만 사용

### 2.3 Pair Trading (페어 평균회귀) — `app/strategies/strategies.py::PairTradingStrategy`

| 항목 | 값 |
|---|---|
| 카테고리 | 평균회귀 / 통계적 차익 |
| 목적 | 방향성 리스크 일부 완화 (BTC-ETH 등) |
| 적합 장세 | RANGE-BOUND, 페어 상관 안정 |
| **role** | market-neutral hedge |
| **regime** | mean-reverting |
| **risk** | 상관관계 깨짐(co-integration breakdown), 펀딩비 누적 |
| **status** | active (PAPER signal-only) |

진입/청산:
- OLS hedge ratio: `cov(A,B) / var(B)`
- 스프레드 z-score (60봉)
- **OPEN_SHORT_A_LONG_B**: `z > +2.0`
- **OPEN_LONG_A_SHORT_B**: `z < -2.0`
- **CLOSE**: `|z| < 0.5`

차단 조건:
- 데이터 < 20봉 → `HOLD`
- 페어 분산 0 → hedge ratio 1.0 fallback

### 2.4 Kimp Mean Reversion (역김프 평균회귀) — **특수전략** — `app/strategies/kimp_mean_reversion.py`

| 항목 | 값 |
|---|---|
| 카테고리 | 평균회귀 (특수) |
| 목적 | 국내/해외 가격 괴리 활용 |
| 적합 장세 | KIMP_GAP (역김프 ≤ -1.8%) |
| **role** | event-only special strategy |
| **regime** | KR vs global price dislocation |
| **risk** | 구조적 (입출금 차단, 강제 손절, 환율 이상, BTC 강세장) |
| **status** | **이벤트 전용** — 상시 주력전략 금지 |

진입/청산 (현재 파라미터):
- **OPEN_REVERSE_KIMP**: kimp ≤ -1.8%
- **CLOSE**: kimp ≥ -1.0% (수렴)
- **STOP_LOSS**: kimp ≤ -3.0% (확대 손절)
- **TIME_STOP**: 15분 경과

차단 조건 (`kimp_guards`, #35) — **하나라도 위험하면 BLOCKED**:
1. 입출금 중단 / 상폐 / 유의종목
2. USDT/KRW 환율 이상치
3. 호가 유동성 / 거래량 부족
4. BTC 급등장 (숏 청산 위험)
5. 비용(수수료+슬리피지+펀딩) ≥ 기대 수익

표준 계산 모듈 (#34 Kimp Formula): [`docs/kimp_formula.md`](kimp_formula.md) — `backend/app/market/kimp_calculator.py` Decimal 기반 단일 진리 소스 (KimpInputs/KimpResult/Direction/ConvergenceState/DislocationKind/compute_kimp/calculate_fee_adjusted_premium_bps/build_kimp_context/classify_structural_vs_temporary_dislocation). KimpStrategy 와 후속 KimpAgent 가 동일 계산 결과를 참조.

구조적 가드 모듈 (#35 Kimp Risk Guards 확장): [`docs/kimp_guards.md`](kimp_guards.md) — `backend/app/strategies/kimp_risk_guards.py` 8 가드 합성 `KimpGuardDecision` API (KimpGuardInput/Reason/Decision/Config + check_notice/fx/liquidity/bull_market_short/funding/freshness/data_quality/missing_critical_context + evaluate_kimp_guards + build_kimp_guard_context). 가드 사유 severity (INFO/WARNING/HIGH/CRITICAL) 에 따라 ALLOW_CANDIDATE/REVIEW_REQUIRED/BLOCK_CANDIDATE 라벨 반환. `direct_order_allowed=False` 영구.

펀딩 비용 가드 모듈 (#36 Funding Cost Guard 확장): [`docs/funding_cost_guard.md`](funding_cost_guard.md) — `backend/app/risk/funding.py` 구조적 `FundingGuardDecision` API (`FundingCostGuard` 클래스 + FundingCostInput/Estimate/Snapshot/Reason/Decision/Config + evaluate_funding_entry/hold + compute_funding_estimate + build_funding_guard_context). 신규 진입 평가 (ALLOW_NEW_CANDIDATE/BLOCK_NEW_CANDIDATE/REVIEW_REQUIRED) 와 보유 평가 (HOLD_CANDIDATE/REDUCE_CANDIDATE/REVIEW_REQUIRED) 분리. `cost_to_edge_ratio` 정책 (block 0.8 / review 0.4) + 누적 비용 정책 (reduce 2.0% / warning 1.0%) + extreme threshold 100 bps. `direct_order_allowed=False` / `used_for_order=False` 영구.

---

## 3. 장세 × 전략 활성 매트릭스

`StrategySelectionAgent` (#41) 가 시장 장세를 분류하고 각 전략의 활성/비활성을 결정한다.

| 장세 | Trend | Volatility | Pair | Kimp |
|---|:---:|:---:|:---:|:---:|
| TREND_UP   | ✅ active | ◔ caution | ⛔ block | ⛔ event-only |
| TREND_DOWN | ✅ active | ◔ caution | ⛔ block | ⛔ event-only |
| RANGE      | ⛔ block  | ◔ caution | ✅ active | ⛔ event-only |
| BREAKOUT   | ◔ caution | ✅ active | ⛔ block | ⛔ event-only |
| HIGH_VOL   | ⛔ block  | ◔ size50 | ⛔ block | ⛔ event-only |
| KIMP_GAP   | (해당없음) | (해당없음) | (해당없음) | ✅ active (가드 통과 시) |
| UNCERTAIN  | ⛔ block  | ⛔ block  | ⛔ block | ⛔ block |

범례: ✅ 활성, ◔ 조건부, ⛔ 비활성

---

## 4. 역김프 특수 정책

이노그릿 실패의 핵심 교훈: **역김프를 상시 주력전략으로 운영하면 망한다**.

본 시스템에서의 강제 정책:
- `ENABLE_KIMP_STRATEGY` 가 true 여도 **상시 활성**이 아니라 **이벤트 트리거** (kimp ≤ entry_threshold) 시에만 신호 생성
- `PermissionGate` 가 `OPEN_REVERSE_KIMP / CLOSE_KIMP` 에 대해 별도 플래그 + 모드 검증
- LIVE 모드에서도 5개 가드 모두 통과 + 사용자 승인 필요
- 노출 한도(#48)는 다른 전략보다 엄격 (예: 동시 김프 포지션 ≤ 1)
- `RiskOfficerAgent` (#40) 가 김프 진입 후보에 대해 별도 거부권

회귀 테스트:
- `tests/test_kimp_strategy.py::test_kimp_blocks_when_cost_exceeds_edge` — 비용 ≥ 기대 시 BLOCKED
- `tests/test_permission_gate.py::test_kimp_blocked_when_flag_off` — 플래그 off 시 차단

---

## 5. 새 전략 추가 절차

1. `app/strategies/base.py` 의 `StrategyBase` 상속 (#29)
2. `generate_signal()` 가 `StrategySignal` 만 반환 — `is_order_intent=false`
3. `app/brokers/*` 직접 import 금지 (회귀 테스트가 차단)
4. 단위 테스트 + 백테스트 결과 동봉
5. 본 문서에 카드 추가 + 장세 매트릭스 갱신
6. `StrategySelectionAgent` 의 활성/비활성 룰 갱신
7. `Promotion Gate` (#64) 통과 후 PAPER 합류

---

## 6. 모듈 경계

- 전략 → BrokerAdapter / Executor 직접 호출 **금지** (회귀 테스트 `test_strategies_do_not_import_brokers`)
- 전략 → AgentOrchestrator 직접 호출 **금지** (역방향)
- 전략은 시그널만 생성, 그 외 모든 결정은 상위 레이어 책임

---

## 7. 변경 이력

| 일자 | 변경 |
|---|---|
| 2026-05-10 | 체크리스트 #2 산출물로 본 문서 작성. 4대 전략 코드 기반 파라미터·가드·매트릭스 정리 |
