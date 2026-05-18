# Kimp Formula — 체크리스트 #34

표준 김프/역김프 계산 모듈 문서. 본 문서는 `backend/app/market/kimp_calculator.py`
(Decimal 기반) 의 사양을 설명하며, 기존 `backend/app/market/kimp.py` (float 기반,
KimpStrategy 의존성) 와의 관계를 정리한다.

> **본 단계는 실거래 허가가 아니다.** KimpCalculator 는 *계산 모듈* 이며 Signal
> 을 생성하지 않고 주문하지 않는다. 33번 이후 전략/agent 가 본 모듈을 참조한다.

---

## 1. 전략 목적

업비트 (국내 KRW 가격) 과 해외 거래소 (OKX / Binance 등 USDT 가격) 의 가격 차이를
USDT/KRW 환율로 환산해 *김프/역김프* 를 표준화한 단일 진리 소스. 동일 입력에 대해
KimpStrategy / KimpAgent / RiskManager 가 모두 같은 값을 사용하도록 보장한다.

본 모듈은:

- 전략이 *아니다* (Signal 생성 안 함).
- 주문 모듈이 *아니다* (broker / adapter / OrderGateway 호출 안 함).
- 주력 상시전략으로 등록하지 *않는다*.
- 계산 결과는 event / risk / context input — 직접 BUY / SELL / ENTER / EXIT 로
  이어지면 안 된다.

`direct_order_allowed = False` 영구.

---

## 2. 입력 — 업비트 KRW · 해외 USDT · USDT/KRW

`KimpInputs`:

| 필드 | 타입 | 설명 |
|---|---|---|
| `domestic_price_krw` | Decimal | 국내 (Upbit) KRW 가격 |
| `foreign_price_quote` | Decimal | 해외 (OKX / Binance / …) USDT 또는 USD 가격 |
| `fx_rate_krw` | Decimal | USDT/KRW 또는 USD/KRW 환율 |
| `symbol` | str \| None | 심볼 (예: `"BTC"`) |
| `domestic_exchange` | str | 기본 `"upbit"` |
| `foreign_exchange` | str | 기본 `"okx"` |
| `quote_currency` | str | 기본 `"USDT"` |
| `timestamp` | datetime \| None | 관측 시각 |
| `previous_premium_bps` | Decimal \| None | 직전 관측치 (수렴/확대 분류용) |
| `reference_fx_rate_krw` | Decimal \| None | 표준 환율 (anomaly deviation 계산) |

숫자형은 모두 Decimal 로 처리한다 — 부동소수점 누적 오차 회피. float / int / str
입력은 내부 `_to_decimal` 로 안전 변환 (float 은 `str()` 경유로 바이너리 드리프트
회피).

---

## 3. foreign_price_krw 공식

```text
foreign_price_krw = foreign_price_quote × fx_rate_krw
```

해외 USDT 가격을 KRW 단위로 환산한다.

---

## 4. premium_ratio 공식

```text
premium_ratio = (domestic_price_krw - foreign_price_krw) / foreign_price_krw
```

양수 → 한국이 비싸다 (정김프) / 음수 → 한국이 싸다 (역김프).

---

## 5. premium_percent 공식

```text
premium_percent = premium_ratio × 100
```

소수 비율을 % 로 표현. UI / 리포트 표시에 사용.

---

## 6. premium_bps 공식

```text
premium_bps = premium_ratio × 10_000
```

bps 단위로 표현 — 임계값 / 비용 / 가드 계산에 일관된 단위로 사용된다.
`premium_percent × 100 == premium_bps` 항등성을 회귀 테스트로 보장.

---

## 7. 수렴/확대 판단 기준 (ConvergenceState)

`previous_premium_bps` 가 주어지면 |current| 와 |previous| 의 차이로 분류한다.

```text
delta_bps = |premium_bps| - |previous_premium_bps|

delta_bps > +convergence_threshold_bps  → EXPANDING
delta_bps < -convergence_threshold_bps  → CONVERGING
otherwise                                → NEUTRAL
previous_premium_bps is None            → UNKNOWN
```

기본 `convergence_threshold_bps = 10`. EXPANDING 시 `risk_flags` 에 `"expanding"`
추가.

---

## 8. 환율 이상 판단 기준 (FX anomaly)

두 단계:

1. **Sanity range** — `fx_rate_min ≤ fx ≤ fx_rate_max` (기본 500 ~ 3000) 외부면
   `fx_anomaly=True`, `fx_anomaly_reason` 에 사유 기록.
2. **Reference deviation** — `reference_fx_rate_krw` 가 주어지면:

   ```text
   deviation_bps = |fx - reference| / reference × 10_000
   deviation_bps > fx_anomaly_deviation_bps (기본 500)  → fx_anomaly=True
   ```

`fx_anomaly=True` 일 때 `risk_flags` 에 `"fx_anomaly"` 추가. KimpStrategy /
KimpAgent / RiskManager 가 review_required 또는 BLOCKED 처리 가능.

> FX anomaly 가 있어도 `compute_kimp` 자체는 raise 하지 않는다 — 계산값 (참고용)
> 은 제공하되 사용 정책은 호출자가 결정한다 (`is_valid` 와 `fx_anomaly` 가 별개).

---

## 9. raw premium 과 fee/funding/transfer adjusted premium 구분

`calculate_fee_adjusted_premium_bps` 는 *참고용* 보정값을 반환한다.

```text
total_cost_bps = domestic_fee_bps + foreign_fee_bps + fx_fee_bps
                 + transfer_cost_bps + |funding_bps|

adjusted_premium_bps = sign(raw) × max(0, |raw| - total_cost_bps)
```

- 부호는 보존된다 (raw 가 음수면 adjusted 도 음수).
- 비용이 |raw| 를 초과하면 0 으로 clamp.
- `funding_bps` 는 부호 무관 비용으로 취급 (`abs`).

> **경고**: 본 보정값은 *실제 거래 가능성을 보장하지 않는다*. 입출금 중단, 슬리피지,
> 매칭 실패, 규제, 세금, 전송 지연 등 외부 리스크는 반영되지 않는다. raw / adjusted
> 어느 값도 그것만 보고 진입해서는 안 된다.

---

## 10. KimpCalculator 는 전략이 아니라 계산 모듈

- Signal 객체를 *반환하지 않는다*. 대신 `KimpResult` 를 반환한다.
- `action: BUY/SELL/...` 같은 액션 필드가 *없다*. 상태 라벨은 `Direction`,
  `ConvergenceState`, `DislocationKind` 뿐이며 모두 *서술적 라벨* 이다.
- KimpStrategy 와 KimpAgent 가 동일 계산 결과를 *참조* 하도록 단일 진리 소스를
  제공한다.

```text
KimpInputs → compute_kimp() → KimpResult → KimpStrategy / KimpAgent 가 참조
```

---

## 11. KimpResult 는 Signal 이 아님

`KimpResult` 는 frozen dataclass — 변경 불가. 필드:

| 필드 | 타입 | 비고 |
|---|---|---|
| `inputs` | KimpInputs | 원본 입력 보존 |
| `foreign_price_krw` | Decimal | 해외 가격 KRW 환산 |
| `premium_ratio` | Decimal | 소수 비율 |
| `premium_percent` | Decimal | 비율 × 100 |
| `premium_bps` | Decimal | 비율 × 10_000 |
| `direction` | str | KIMP / REVERSE_KIMP / NEUTRAL |
| `convergence_state` | str | EXPANDING / CONVERGING / NEUTRAL / UNKNOWN |
| `delta_bps` | Decimal \| None | |current| - |previous| |
| `fx_anomaly` | bool | sanity 또는 reference deviation 위반 |
| `fx_anomaly_reason` | str \| None | |
| `fx_deviation_bps` | Decimal \| None | reference 대비 deviation |
| `is_valid` | bool | 입력 검증 통과 여부 |
| `invalid_reason` | str \| None | |
| `risk_flags` | tuple[str, ...] | `invalid_input` / `fx_anomaly` / `large_premium` / `expanding` |
| `computed_at` | datetime | |
| `direct_order_allowed` | bool | 영구 `False` |

---

## 12. direct_order_allowed = False (영구)

`KimpCalculatorConfig.direct_order_allowed` 와 `KimpResult.direct_order_allowed`
모두 영구 `False`. 정적 회귀 테스트 `test_direct_order_allowed_permanently_false_on_config_and_result`
가 매 실행마다 검증한다.

```text
StrategySignal / KimpResult
  → AgentReview
  → RiskManager
  → OrderGuard
  → PermissionGate
  → ApprovalQueue
  → OrderGateway
```

본 모듈은 위 경로의 *최좌측 입력 보조* 로만 사용된다. 우회 경로 금지.

---

## 13. 주력 상시전략 금지

김프/역김프는 입출금 중단, FX 이상, 수수료, 전송 지연, 규제, 세금, funding 리스크가
크다. 본 계산 결과를 주력 상시전략으로 등록하지 않는다. 이벤트 기반 보조 시그널/
risk context 로만 사용된다 (StrategySelectionAgent 가 별도 정책 적용).

---

## 14. 김프/역김프 구조적 리스크

KimpAgent hook `classify_structural_vs_temporary_dislocation` 이 다중 관측치
시계열을 분류한다.

| 분류 | 조건 |
|---|---|
| **STRUCTURAL** | 부호 일관 + 평균 \|premium_bps\| ≥ `structural_min_abs_bps` |
| **TEMPORARY** | 부호 혼재 (확률적 변동) |
| **MIXED** | 부호 일관 + 평균 미달 |
| **UNKNOWN** | is_valid 결과 < `structural_min_count` |

모든 분류 결과에 `direct_order_allowed=False` 명시.

리스크 카탈로그 (계산 모듈 범위 밖, 정책 결정용):

- 입출금 중단 / 상장폐지 / 유의종목
- FX 이상 (USDT 의 KRW 페그 이탈 / 환전 라인 끊김)
- 거래소 수수료 / 슬리피지
- 전송 지연 / 입금 confirm 시간
- 규제 / 세금 / KYC
- Funding rate 비용 (영구선물 short 시)

---

## 15. KimpStrategy / KimpAgent 와의 관계

본 1차 확장은 *KimpStrategy 코드를 직접 수정하지 않는다*. 기존 동작 (33번 베이스라인)
은 그대로 유지되며, `test_kimp_strategy_signal_unchanged_after_calculator_added`
가 회귀를 보장한다.

연동 후속:

- KimpStrategy 가 KimpCalculator 결과를 `KimpSignal.evidence` 에 stash 하는
  최소 wrapper 는 후속 단계에서 추가 가능 (요구 시).
- KimpAgent 가 본격적으로 추가될 때 `build_kimp_context` 를 그대로 사용.
- `classify_structural_vs_temporary_dislocation` 가 다중 시점 관측을 받아
  agent 의 구조적/일시적 리스크 판단 context 를 생성.

---

## 16. 본 단계 완료는 실거래 허가가 아님 · 35번 이후 미작업

CLAUDE.md §2.6 — 체크리스트 PASS 는 실거래 허가가 아니다.

- ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / ENABLE_CRYPTO_FUTURES_LIVE 모두
  기본 false 유지.
- 본 작업은 #34 Kimp Formula 표준화만 수행. 35번 (Kimp Guards 확장) 이후 작업으로
  넘어가지 않는다.
- 35번 이후 의존 작업은 별도 체크리스트 항목으로 별도 PR 에서 진행.

---

## 참조 모듈

- 생성: `backend/app/market/kimp_calculator.py`
- 회귀: `backend/tests/test_kimp_calculator.py` (43 케이스)
- 기존 float 단일 진리 소스 (33번 의존): `backend/app/market/kimp.py`
- KimpStrategy (33번): `backend/app/strategies/kimp_mean_reversion.py`
- 가드 (35번): `backend/app/strategies/kimp_guards.py`
- Funding 가드 (36번): `backend/app/market/funding.py`
- 전략 contract (29번): `backend/app/strategies/contract.py`
- 안전 원칙: `docs/safety_principles.md` / `CLAUDE.md`
