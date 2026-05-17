# Data Quality — 체크리스트 #17 (historical candle layer)

저장된 과거 candle 의 **신뢰도** 를 검증해, 잘못된 데이터로 백테스트 성과가
과대평가되는 것을 막는 계층.

- 구현: `backend/app/market/data_quality.py`
- CLI : `scripts/check_data_quality.py` (historical 모드)
- REST: `GET /api/market/data-quality/summary`
- 기존 *live ticker* 계층(`backend/app/market/quality.py`) 과는 **별도 모듈** — 섞지 않는다.

---

## 1. Data Quality vs Data Freshness — 역할 분리

| 구분 | Data Freshness (#16) | Data Quality (#17) |
|---|---|---|
| 질문 | "지금 데이터가 오래됐는가?" | "보관된 과거 데이터가 신뢰 가능한가?" |
| 대상 | live tracker (`last_seen_at`) | 저장된 candle (coin_candle) |
| 시점 | 실시간 | 사후 검증 (백테스트 / 분석) |
| 결과 | block/허용 + reason | GOOD / WARNING / EXCLUDE |
| 차단 대상 | 신규 BUY/OPEN 진입 | 백테스트 성과의 승격 근거 |

두 계층은 *동일 결정을 두 번 하지 않는다*. 신규 진입은 Freshness 가, 승격 판단은
Data Quality 가 맡는다.

---

## 2. 검사 6종 (per `(symbol, exchange, timeframe, day)`)

| 검사 | 함수 | 정책 |
|---|---|---|
| 누락 candle | `check_missing` | 기대치 대비 unique-ts 부족분. GOOD ≤ 0.1% / WARNING ≤ 1% / 그 외 EXCLUDE. |
| 중복 candle | `check_duplicates` | 동일 ts 중복. 1+ → WARNING, 비율 1% 초과 → EXCLUDE. |
| OHLC 논리 | `check_ohlc_validity` | `high<low / open>high / open<low / close>high / close<low / ≤0 / NaN`. 1+ → WARNING, 5+ → EXCLUDE. |
| volume 이상 | `check_volume_anomalies` | 음수=EXCLUDE / zero 30%↑=WARNING / rolling median 대비 100배 spike=WARNING. |
| 가격 outlier | `check_price_outliers` | 직전 close 대비 abs return: >50% WARNING / >90% EXCLUDE. |
| 장외 데이터 | `check_off_universe` | unknown exchange=WARNING / 미래 ts=EXCLUDE / Watchlist 밖=WARNING / grid 불일치=WARNING. |

기본 임계값은 `DataQualityConfig` 에 정리되어 있으며 운영 시 인자로 덮어쓴다.

---

## 3. Grade 산출

`run_day_check(candles, symbol, exchange, timeframe, day, config?, watchlist_symbols?, now?)`
→ `DataQualityDayReport` (frozen dataclass).

```
DataQualityDayReport(
    symbol, exchange, timeframe, date,
    expected_count, actual_count,
    missing_count, missing_rate,
    duplicate_count, invalid_ohlc_count,
    volume_anomaly_count, price_outlier_count,
    off_universe_count, future_timestamp_count,
    grade ∈ {GOOD, WARNING, EXCLUDE},
    reasons: tuple[str, ...]
)
```

`grade` 는 각 검사의 가장 무거운 단계로 결정 (EXCLUDE > WARNING > GOOD).
`reasons` 는 사람이 읽을 수 있는 사유 목록 (clean / missing_rate 0.012 > 0.01 등).

`.as_dict()` → API/JSON 출력 형식.

---

## 4. BacktestPromotionGuard

```python
guard = BacktestPromotionGuard(
    min_good_ratio = 0.9,   # 90% 이상 GOOD
    max_warning_ratio = 0.1,
    max_exclude_ratio = 0.0,
)
ev = guard.evaluate(list_of_day_reports)
ev.allowed   # True/False
ev.reason    # "approved" / "warning_data_allowed_but_limited"
             # "blocked_by_no_data_quality_reports"
             # "blocked_by_excluded_data_quality_day"
             # "blocked_by_low_good_data_ratio"
             # "blocked_by_high_warning_ratio"
```

**guard 는 판단만 한다.** 실제 승격은 별도 promotion gate (#64, #66) 가 수행하며,
본 guard 는 그 입력 중 *데이터 품질* 영역을 책임진다.

| 시나리오 | 결과 |
|---|---|
| 빈 reports | `blocked_by_no_data_quality_reports` |
| 어느 하루라도 EXCLUDE | `blocked_by_excluded_data_quality_day` |
| GOOD 비율 < 90% | `blocked_by_low_good_data_ratio` |
| WARNING 비율 > 10% | `blocked_by_high_warning_ratio` |
| GOOD 100% | `approved` |
| GOOD 90%↑ + WARNING ≤10% | `warning_data_allowed_but_limited` (allowed=True) |

---

## 5. CLI 사용 예시

```
# 1) 단일 day 점검 (text)
python scripts/check_data_quality.py \
    --symbol BTC --exchange mock --timeframe 1m --date 2026-05-17

# 2) 범위 + JSON + EXCLUDE 시 exit 2
python scripts/check_data_quality.py \
    --symbol BTC --exchange mock --timeframe 1m \
    --from-date 2026-05-01 --to-date 2026-05-17 \
    --output json --fail-on-exclude

# 3) (legacy) live ticker quality — --symbol 미지정 시 기존 동작 유지
python scripts/check_data_quality.py --list-name kimp_pairs --json
```

종료 코드 (historical 모드):
- 0 : 모든 days 통과 (또는 --fail-on-exclude 미사용)
- 2 : `--fail-on-exclude` + EXCLUDE 발생 시 / 인자 누락

종료 코드 (legacy live 모드):
- 0 : 통과
- 1 : 하나 이상 BLOCK
- 2 : watchlist 비어 있음

---

## 6. REST API

```
GET /api/market/data-quality/summary
    ?symbol=...&exchange=...&timeframe=...&date=YYYY-MM-DD
```

public — secret 노출 없음. coin_candle 만 읽으며 외부 호출 없음.

응답 예시:
```json
{
  "report": {
    "symbol": "BTC", "exchange": "mock", "timeframe": "1m",
    "date": "2026-05-17",
    "expected_count": 1440, "actual_count": 1440,
    "missing_count": 0, "missing_rate": 0.0,
    "duplicate_count": 0, "invalid_ohlc_count": 0,
    "volume_anomaly_count": 0, "price_outlier_count": 0,
    "off_universe_count": 0, "future_timestamp_count": 0,
    "grade": "GOOD",
    "reasons": ["clean"]
  },
  "promotion": {
    "allowed": true, "reason": "approved",
    "good_ratio": 1.0, "warning_ratio": 0.0, "exclude_ratio": 0.0
  }
}
```

오류:
- date 파싱 실패 → 400
- timeframe 미지원 → 400

---

## 7. 안전 원칙 — 변경되지 않은 것

본 작업은 다음을 변경/구현하지 않는다:

- 실제 거래소 LIVE 주문 / 잔고 / 체결 / private endpoint
- `place_order`, `cancel_order`, `get_balance`, broker.* 코드
- `ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / ENABLE_CRYPTO_FUTURES_LIVE` 기본값
- frontend secret/token 저장
- 18번 Exchange Notices
- 19번 Trend/News/Theme Signals
- 전체 시장 자동 스캔 (`--symbol` 필수, 빈 watchlist 시 fallback 없음)

**17번 완료 = 데이터 신뢰도 검증 + 승격 guard 완료. LIVE 실거래 허가가 아니다.**

회귀 방지:
- `test_no_forbidden_strings_in_data_quality_production` — `data_quality.py`, `check_data_quality.py` 에 금지 문자열 부재.
- `test_market_data_source_protocol_has_no_order_methods` (#15) — Protocol 에 주문 메서드 부재.
- `test_collector_does_not_import_brokers_or_execution` (#15) — collector 가 broker/execution 를 import 하지 않음.

---

## 8. 향후 확장 메모 (별도 체크리스트)

| 영역 | 메모 |
|---|---|
| Hourly/weekly grade 집계 | 단일-day 위에 주/월 단위 종합 grade — #64 promotion gate 통합 |
| 자동 backfill | 누락 candle 자동 재수집 — collector(#15) 의 historical fetch 와 연동 |
| `coin_risk_event` 통합 | EXCLUDE 발생 시 13번 `coin_risk_event` 로 자동 기록 |
| 다중 source 합의 | A/B 거래소 candle 비교로 outlier 정확도 개선 |
| frontend Quality 대시보드 | day grade timeline UI — `/market` 탭 보강 |
